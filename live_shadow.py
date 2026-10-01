"""live_shadow.py -- the NEW logic (signal_sim) trading LIVE, paper only.

Sri, 07-Sep 07:40: "wire it before 9:15AM."

WHAT THIS IS. A second paper engine that runs beside the Board. It does not
touch live_paper.py, the Live Trading button, or any order path -- rewriting the
Board's engine ninety minutes before the open is how a working system gets
broken on the day it matters. This runs the exact rules we built and measured
on 04-Sep, on today's live data, from 09:15.

WHERE THE DATA COMES FROM
  today   logs/movers_board/bars30_<today>.jsonl -- completed 30-second OHLCV
          bars the Board already writes as it watches. Real volume, live.
  warm-up logs/tape/<prev>/<SYM>.json -- the previous session, so the volume
          baseline and the 26-candle indicators are alive at 09:15:00 instead
          of 09:28.
  universe a name is tradeable only from the moment the Board carded it, read
          from board_<today>.jsonl timestamps. No look-ahead.
  day %   the Board's own movers panels, again timestamped.

HOW IT WORKS. Every REFRESH seconds it rebuilds the day so far and re-runs the
whole session forward-only from 09:15. The engine is deterministic, so replaying
the morning each cycle gives exactly the same decisions plus whatever the newest
bar adds -- much safer than carrying mutable state across a live loop.

OUTPUT  logs/SHADOW_LIVE_<today>.json   (book + closed trades, rewritten each cycle)
        logs/SHADOW_LIVE_<today>.log    (one line per new trade)
"""
import json, os, sys, time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import signal_sim as G
import sri_stack as S
import eye_bench as EB

REFRESH = 20
IST_OPEN, IST_CLOSE = "09:15:00", "15:30:00"
# 07-Sep measured the real rate: 119 symbols in 34s = 0.29s each, four times
# faster than the 0.7s build_tape suggested. At 120 names UDAYJEW and AARADHYA
# -- both top-five movers that morning -- had only SIX candles by 09:45 because
# the fetch had not reached them. 240 still sweeps in about 70 seconds.
MAX_FETCH = 500        # was 240 -- half the board's own names had no bars
FETCH_EVERY = 90        # seconds between sweeps


# ---- LIVE 30-SECOND BARS, STRAIGHT FROM DHAN -------------------------------
# Sri, 07-Sep: "you can access dhan bars from 9:15AM onwards, what's your issue"
# -- he is right. The Board only streams 30-second bars for names it has already
# carded, which on 04-Sep meant RML's first bar arrived at 10:45 for a trade at
# 10:29. /getDataS serves any security id from the open, so the shadow fetches
# its own bars and is not held to what the Board happens to be watching.
_FEED = {"cf": None, "sid": {}, "last": 0.0}


def _feed():
    if _FEED["cf"] is None:
        import Movers_chartfeed as cf
        _FEED["cf"] = cf
    return _FEED["cf"]


def _sids(syms):
    want = {s for s in syms if s not in _FEED["sid"]}
    if not want:
        return _FEED["sid"]
    import csv
    f = HERE / "security_id_list.csv"
    if f.exists():
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                if row.get("SEM_EXM_EXCH_ID") != "NSE":
                    continue
                if row.get("SEM_SERIES") not in ("EQ", "BE", "SM", "D1"):
                    continue
                sy = row.get("SEM_TRADING_SYMBOL")
                if sy in want and sy not in _FEED["sid"]:
                    _FEED["sid"][sy] = row["SEM_SMST_SECURITY_ID"]
    for s in want:
        _FEED["sid"].setdefault(s, None)
    return _FEED["sid"]


def pick_universe(day, log):
    """What a person would have on screen right now: Dhan's own movers panels,
    most recent poll first, then the 09:08 list. Live, timestamped, no peeking."""
    seen, order = {}, []
    f = HERE / "logs" / "movers_board" / f"board_{day}.jsonl"
    if f.exists():
        rows = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in reversed(rows[-40:]):
            try:
                d = json.loads(line)
            except Exception:
                continue
            for panel in (d.get("panels") or {}).values():
                for r in panel or []:
                    sy, p = r.get("sym"), r.get("day_pct")
                    if sy and sy not in seen:
                        seen[sy] = p if p is not None else 0.0
    order = [s for s, _ in sorted(seen.items(), key=lambda kv: -(kv[1] or 0))]
    # EVERY name the board surfaced today, not just what is on the panels right
    # now. 08-Sep 10:05: the board had surfaced 251 names and only 121 of them
    # had bars on disk, because this read the last 40 lines of the panel log and
    # never looked at the Super Stocks cards at all. Sri: "You are not properly
    # using the board." Freshness order is unchanged -- what is on screen now is
    # still fetched first; these are appended behind it.
    try:
        import funnel as _FN
        for sy in _FN.superstocks(day):
            if sy not in seen:
                order.append(sy); seen[sy] = 0.0
        for sy in _FN.shockers(day):
            if sy not in seen:
                order.append(sy); seen[sy] = 0.0
    except Exception as e:
        log(f"universe: could not read the board's full day -- {type(e).__name__}: {e}")

    b = HERE / "logs" / f"bullish_0909_{day}.json"
    if b.exists():
        try:
            for r in json.loads(b.read_text(encoding="utf-8")).get("rows", []):
                if r.get("sym") and r["sym"] not in seen:
                    order.append(r["sym"]); seen[r["sym"]] = 0.0
        except Exception:
            pass
    return order[:MAX_FETCH]


WORKERS = 5             # parallel fetches. See fetch_live() for why 5.


def fetch_interval(hms, last_ok=1):
    """Seconds to wait before the next sweep.

    30-Sep. The opening minute was being thrown away:

        09:15:04  feed: 0 symbols fetched, 12 failed, 4s
        09:16:59  feed: 21 symbols fetched, 5 failed, 10s

    The 09:15:04 sweep ran 26 seconds before the first 30-second bar had even
    closed, so of course it found nothing -- and then slept the full 90s. The
    engine had no data at all until 09:16:59 and no funnel until 09:18:06, so
    the open simply did not exist for it. Sri, 30-Sep: "at 9:15 am to 9:16AM,
    engine should be in a position to take decisions which stock to enter."

    So: sweep hard and often early, and whenever a sweep comes back empty.
    Settle to the normal cadence once data is flowing.
    """
    if hms < "09:25:00":
        return 15
    if not last_ok:
        return 20
    return FETCH_EVERY


def fetch_live(day, syms, log):
    """Pull today's 30-second bars for `syms` into logs/tape_live/<day>/.

    PARALLEL since 30-Sep. It used to fetch one symbol at a time: 141 names
    at roughly 0.4s each is a ~60s sweep, on a 90s cadence, so a name fetched
    at the end of a sweep was already a minute stale and the worst case ran to
    ~180s. The engine re-decides every 20s, so it was re-deciding four or five
    times on identical, ageing data -- which also feeds the "displaced by"
    churn.

    Five workers, not more: Dhan answered 31 requests with HTTP 429 today even
    at one-at-a-time, so the ceiling here is the rate limit, not our CPU.
    getDataS returns the whole day in one call, so a name fetched at 09:16
    still gets its 09:15 bars -- the data was never late, the queue was.
    """
    out = HERE / "logs" / "tape_live" / day
    out.mkdir(parents=True, exist_ok=True)
    cf = _feed()
    sid = _sids(syms)
    import build_tape as BT
    from concurrent.futures import ThreadPoolExecutor
    import threading
    t0 = time.time()
    lock = threading.Lock()
    tally = {"ok": 0, "fail": 0}

    def _one(sym):
        if time.time() - t0 > 150:
            return
        s = sid.get(sym)
        if not s:
            return
        try:
            d, err = cf.get_seconds(str(s), interval="30S", days=2)
            if err or not d:
                with lock:
                    tally["fail"] += 1
                return
            d = BT._only_day(d, day)
            if not d:
                with lock:
                    tally["fail"] += 1
                return
            (out / f"{sym}.json").write_text(
                json.dumps(d, separators=(",", ":")), encoding="utf-8")
            with lock:
                tally["ok"] += 1
        except Exception:
            with lock:
                tally["fail"] += 1

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(_one, list(syms)))
    ok, fail = tally["ok"], tally["fail"]
    el = time.time() - t0
    if el > 600:
        # 23-Sep: a cycle logged 15,284s. Every socket here already carries a
        # 20-25s timeout, so nothing hung -- the laptop slept and the wall clock
        # jumped on wake. Worth saying out loud, because a silent four-hour gap
        # in the log reads exactly like a hung endpoint and cost an evening's
        # misdiagnosis. If a position is open when this fires it was UNMANAGED
        # for that whole stretch, which is the part that actually matters.
        log(f"feed: WALL CLOCK JUMPED {el/60:.0f} min -- the machine was most "
            f"likely asleep. Any open position went unmanaged for that time.")
    log(f"feed: {ok} symbols fetched, {fail} failed, {el:.0f}s")
    return ok


def load_live(day):
    """{sym: [bars]} from the shadow's own fetch."""
    from datetime import timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    d = HERE / "logs" / "tape_live" / day
    out = {}
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts, c = raw.get("t") or [], raw.get("c") or []
        if not ts or len(c) != len(ts):
            continue
        bars = []
        for i, tv in enumerate(ts):
            hm = datetime.fromtimestamp(tv, IST).strftime("%H:%M:%S")
            if not (IST_OPEN <= hm <= IST_CLOSE):
                continue
            bars.append({"o": (raw.get("o") or c)[i], "h": (raw.get("h") or c)[i],
                         "l": (raw.get("l") or c)[i], "c": c[i],
                         "v": (raw.get("v") or [0] * len(c))[i], "hhmm": hm})
        if len(bars) >= 6:
            out[f.stem] = sorted(bars, key=lambda x: x["hhmm"])

    # FAST FEED OVERLAY, 30-Sep. quote_feed builds 30s bars from one bulk
    # quote per second, so it has the newest half-minutes long before the
    # per-symbol candle sweep gets round to that name again. Dhan's own
    # candles stay authoritative wherever both exist -- they are the
    # exchange's, ours are reconstructed -- so the fast bars only FILL IN
    # timestamps the sweep has not delivered yet. That is the whole point:
    # the engine stops reasoning about minute-old prices without giving up
    # the accuracy of the official series for everything older.
    try:
        import quote_feed as QF
        fast = QF.load(day)
    except Exception:
        fast = {}
    for sym, raw in (fast or {}).items():
        ts, c = raw.get("t") or [], raw.get("c") or []
        if not ts or len(c) != len(ts):
            continue
        have = {b["hhmm"] for b in out.get(sym, [])}
        add = []
        for i, tv in enumerate(ts):
            hm = datetime.fromtimestamp(tv, IST).strftime("%H:%M:%S")
            if hm in have or not (IST_OPEN <= hm <= IST_CLOSE):
                continue
            add.append({"o": (raw.get("o") or c)[i], "h": (raw.get("h") or c)[i],
                        "l": (raw.get("l") or c)[i], "c": c[i],
                        "v": (raw.get("v") or [0] * len(c))[i], "hhmm": hm})
        if add:
            out[sym] = sorted(out.get(sym, []) + add, key=lambda x: x["hhmm"])
    return out


def _prev_session_dir(day):
    """Most recent tape folder before `day` that actually holds bars.

    logs/tape is written by build_tape.py and is the canonical source, but it
    is only there when that script has been run.  logs/tape_live is captured
    by the board every session and carries the same {o,h,l,c,v,t} schema, so
    fall back to it -- without the previous session's bars every indicator is
    blind until roughly 09:40 and the eye logic loses its warm-up prefix.
    """
    best = None
    for name in ("tape", "tape_live"):
        base = HERE / "logs" / name
        if not base.exists():
            continue
        days = sorted(p.name for p in base.iterdir()
                      if p.is_dir() and p.name.isdigit() and p.name < day
                      and any(p.glob("*.json")))
        if not days:
            continue
        cand = base / days[-1]
        if best is None or cand.name > best.name:
            best = cand
    return best


def load_warm(prev_dir, want_day=None):
    """{sym: [bars]} for the previous session, from the tape files."""
    out = {}
    if not prev_dir or not prev_dir.exists():
        return out
    from datetime import timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    for f in prev_dir.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts, c = raw.get("t") or [], raw.get("c") or []
        if not ts or len(c) != len(ts):
            continue
        by = {}
        for i, tv in enumerate(ts):
            dt = datetime.fromtimestamp(tv, IST)
            hm = dt.strftime("%H:%M:%S")
            if not (IST_OPEN <= hm <= IST_CLOSE):
                continue
            by.setdefault(dt.strftime("%Y%m%d"), []).append(
                {"o": (raw.get("o") or c)[i], "h": (raw.get("h") or c)[i],
                 "l": (raw.get("l") or c)[i], "c": c[i],
                 "v": (raw.get("v") or [0] * len(c))[i], "hhmm": hm})
        if not by:
            continue
        k = want_day if (want_day and want_day in by) else sorted(by)[-1]
        if len(by[k]) >= 200:
            out[f.stem] = by[k]
    return out


def load_today(day):
    """{sym: [bars]} from the Board's live 30-second bars."""
    f = HERE / "logs" / "movers_board" / f"bars30_{day}.jsonl"
    out = {}
    if not f.exists():
        return out
    seen = set()
    for line in f.open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        hm, sym = r.get("hhmm"), r.get("sym")
        if not sym or not hm or not (IST_OPEN <= hm <= IST_CLOSE):
            continue
        if (sym, hm) in seen:
            continue
        seen.add((sym, hm))
        out.setdefault(sym, []).append(
            {"o": r.get("o"), "h": r.get("h"), "l": r.get("l"),
             "c": r.get("c"), "v": r.get("v") or 0, "hhmm": hm})
    for k in out:
        out[k].sort(key=lambda x: x["hhmm"])
    return out


def load_board(day):
    """(available_from, day_pct) -- both timestamped, never read ahead."""
    av, dp = {}, {}
    # EVERY name the board surfaced today, not just what is on the panels right
    # now. 08-Sep 10:05: the board had surfaced 251 names and only 121 of them
    # had bars on disk, because this read the last 40 lines of the panel log and
    # never looked at the Super Stocks cards at all. Sri: "You are not properly
    # using the board." Freshness order is unchanged -- what is on screen now is
    # still fetched first; these are appended behind it.
    try:
        import funnel as _FN
        for sy in _FN.superstocks(day):
            if sy not in seen:
                order.append(sy); seen[sy] = 0.0
        for sy in _FN.shockers(day):
            if sy not in seen:
                order.append(sy); seen[sy] = 0.0
    except Exception as e:
        log(f"universe: could not read the board's full day -- {type(e).__name__}: {e}")

    b = HERE / "logs" / f"bullish_0909_{day}.json"
    if b.exists():
        try:
            for r in json.loads(b.read_text(encoding="utf-8")).get("rows", []):
                av[r["sym"]] = IST_OPEN
        except Exception:
            pass
    f = HERE / "logs" / "movers_board" / f"board_{day}.jsonl"
    if f.exists():
        for line in f.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            hm = (d.get("ts") or "")[11:19]
            if len(hm) != 8:
                continue
            hm = max(hm, IST_OPEN)
            for rows in (d.get("panels") or {}).values():
                for r in rows or []:
                    sy, p = r.get("sym"), r.get("day_pct")
                    if not sy:
                        continue
                    if hm < av.get(sy, "99:99:99"):
                        av[sy] = hm
                    if p is not None:
                        dp.setdefault(sy, []).append((hm, float(p)))
    for k in dp:
        dp[k].sort()
    return av, dp


def build(day, warm):
    """{sym: (warm+today bars, day_start_index)} for every symbol with enough.
    The shadow's own Dhan fetch first; the Board's bars only fill the gaps."""
    today = load_today(day)
    today.update(load_live(day))
    tape = {}
    for sym, bars in today.items():
        if len(bars) < 6:
            continue
        w = warm.get(sym) or []
        tape[sym] = (w + bars, len(w))
    return tape, today


def checkpoint(day, tape, closed, upto, log):
    """Every ten minutes: what the eye could have had, what the code got, and
    for each leg the code did not get, WHY.

    Nothing here changes a trading rule. A rule changed at 10:25 on the strength
    of what happened at 10:15 is fitted to the morning it was written in; the
    diagnosis is the deliverable, and the change is made after the close with
    the whole day and the other sessions to check it against.
    """
    eye, eye_net = EB.bench(tape, upto)
    code_net = sum(c["net"] for c in closed if c["out_t"] <= upto)
    log("=" * 64)
    log(f"CHECKPOINT {upto}   eye Rs {eye_net:,.0f}   code Rs {code_net:,.0f}   "
        f"({(code_net / eye_net * 100) if eye_net else 0:.0f}% of the ceiling)")
    took = {}
    for c in closed:
        took.setdefault(c["sym"], []).append(c)
    for x in sorted(eye, key=lambda z: -z["net"])[:6]:
        mine = [c for c in took.get(x["sym"], [])
                if c["in_t"] <= x["t_out"] and c["out_t"] >= x["t_in"]]
        if mine:
            m = mine[0]
            got = (m["out"] / m["in"] - 1) * 100
            tag = (f"took it {m['in_t']}->{m['out_t']} {got:+.2f}% vs {x['pct']:+.2f}%"
                   f"  ({'sold early' if m['out_t'] < x['t_out'] else 'in late'})")
        else:
            av = G.AVAILABLE_FROM.get(x["sym"])
            denied = [d for d in G.DENIED if d[1] == x["sym"] and d[0] <= x["t_out"]]
            if av is None:
                tag = "NEVER SEEN -- the Board did not card it"
            elif av > x["t_in"]:
                tag = f"BOARD CARDED IT LATE -- only visible from {av}"
            elif denied:
                tag = f"SIGNALLED but NO SLOT ({len(denied)} times)"
            else:
                tag = "no signal -- the rules never fired on it"
        log(f"  {x['sym']:12s} {x['t_in']}->{x['t_out']} {x['pct']:+6.2f}% "
            f"Rs {x['net']:>8,.0f}   {tag}")
    log("=" * 64)
    (HERE / "logs" / f"CHECKPOINT_{day}.log").open("a", encoding="utf-8").write(
        f"{upto}  eye {eye_net:.0f}  code {code_net:.0f}\n")


def cycle(day, warm, log):
    tape, today = build(day, warm)
    if not tape:
        log("no bars yet")
        return None
    cycle.tape = tape
    av, dp = load_board(day)
    G.VOL_MODE = True
    G.DAY_PCT = dp
    G.AVAILABLE_FROM = {s: av[s] for s in av if s in tape}
    G.universe = lambda d: (tape, set(tape), set())
    closed, net = G.run(day, log=None)
    warmed = sum(1 for s in tape if tape[s][1] > 0)
    return {"tape": len(tape), "warmed": warmed, "closed": closed, "net": net}


def dashboard(day, hms, r):
    """A page Sri can leave open. Refreshes itself every 15 seconds."""
    op = G.OPEN_NOW
    rows = []
    for p in sorted(op, key=lambda x: -x.get("live_pct", 0)):
        rows.append(f"<tr class=o><td>{p['sym']}</td><td>{p['in_t']}</td>"
                    f"<td>holding</td><td>{p['in']:.2f}</td><td>{p['last']:.2f}</td>"
                    f"<td class='{'g' if p['live_pct']>=0 else 'r'}'>{p['live_pct']:+.2f}%</td>"
                    f"<td>{p['qty']*p['in']:,.0f}</td><td>open</td>"
                    f"<td class='{'g' if p['live_pct']>=0 else 'r'}'>{p['live_rs']:+,.0f}</td></tr>")
    for c in sorted(r["closed"], key=lambda x: x["in_t"], reverse=True):
        pct = (c["out"] / c["in"] - 1) * 100
        rows.append(f"<tr><td>{c['sym']}</td><td>{c['in_t']}</td><td>{c['out_t']}</td>"
                    f"<td>{c['in']:.2f}</td><td>{c['out']:.2f}</td>"
                    f"<td class='{'g' if pct>=0 else 'r'}'>{pct:+.2f}%</td>"
                    f"<td>{c['qty']*c['in']:,.0f}</td><td>{c['why']}</td>"
                    f"<td class='{'g' if c['net']>=0 else 'r'}'>{c['net']:+,.0f}</td></tr>")
    cp = ""
    f = HERE / "logs" / f"CHECKPOINT_{day}.log"
    if f.exists():
        cp = "<pre>" + f.read_text(encoding="utf-8")[-1200:] + "</pre>"
    net = r["net"] + sum(p["live_rs"] for p in op)
    html = f"""<!doctype html><meta charset=utf-8>
<meta http-equiv=refresh content=15><title>Live Shadow {day}</title>
<style>body{{font:13px system-ui;margin:18px;background:#0f1115;color:#e6e6e6}}
h1{{font-size:17px;margin:0 0 2px}}.sub{{color:#8a8f98;margin-bottom:14px}}
table{{border-collapse:collapse;width:100%;max-width:1000px}}
td,th{{padding:5px 9px;border-bottom:1px solid #23262d;text-align:right}}
td:first-child,th:first-child,td:nth-child(8){{text-align:left}}
th{{color:#8a8f98;font-weight:500}} .g{{color:#3ddc84}} .r{{color:#ff6b6b}}
tr.o{{background:#16351f}} pre{{background:#161922;padding:10px;color:#b9bec7;
white-space:pre-wrap;max-width:1000px}} .big{{font-size:24px}}</style>
<h1>Live Shadow &mdash; new logic, paper only</h1>
<div class=sub>{hms} &middot; {r['tape']} symbols watched ({r['warmed']} with
warm-up) &middot; {len(op)} open &middot; {len(r['closed'])} closed &middot;
<span class="big {'g' if net>=0 else 'r'}">Rs {net:+,.0f}</span>
on Rs 1,00,000 &middot; page refreshes itself</div>
<table><tr><th>stock<th>in<th>out<th>buy<th>price<th>move<th>size<th>why<th>Rs</tr>
{''.join(rows) or '<tr><td colspan=9>no trades yet</td></tr>'}</table>
{cp}"""
    (HERE / "logs" / f"SHADOW_LIVE_{day}.html").write_text(html, encoding="utf-8")


def board_snapshot(day=None):
    """The shadow's book in the shape the Board's Live Trading tab already
    draws, so Sri watches ONE screen instead of two. Returns None if there is
    no fresh shadow file, and the Board then falls back to its own engine."""
    day = day or datetime.now().strftime("%Y%m%d")
    f = HERE / "logs" / f"SHADOW_LIVE_{day}.json"
    if not f.exists() or time.time() - f.stat().st_mtime > 180:
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    import paper_engine as pe
    rows = []
    for p in d.get("open", []):
        bv, sv = p["qty"] * p["in"], p["qty"] * p["last"]
        ch = pe.charges(bv, sv)
        rows.append({"sym": p["sym"], "sid": p.get("sid", ""), "in": p["in"],
                     "in_hms": p["in_t"], "in_t": p["in_t"], "qty": p["qty"],
                     "out": round(p["last"], 2), "out_hms": "\u2014", "why": "open",
                     "gross": round(sv - bv, 2), "charges": ch, "chg": ch["total"],
                     "net": round(sv - bv - ch["total"], 2), "held": 0,
                     "status": "In-Progress", "value": round(bv, 2)})
    for c in d.get("trades", []):
        bv, sv = c["qty"] * c["in"], c["qty"] * c["out"]
        rows.append({"sym": c["sym"], "sid": c.get("sid", ""), "in": c["in"],
                     "in_hms": c["in_t"], "in_t": c["in_t"], "qty": c["qty"],
                     "out": c["out"], "out_hms": c["out_t"], "why": c["why"],
                     "gross": round(sv - bv, 2),
                     "charges": {"total": c["chg"]}, "chg": c["chg"],
                     "net": round(c["net"], 2), "held": 0,
                     "status": "Closed", "value": round(bv, 2)})
    rows.sort(key=lambda x: x["in_t"])
    cap = G.CAPITAL
    done = [r for r in rows if r["status"] == "Closed"]
    wins = [r for r in done if r["net"] > 0]
    realised = round(sum(r["net"] for r in done), 2)
    unreal = round(sum(r["net"] for r in rows if r["status"] == "In-Progress"), 2)
    net = round(realised + unreal, 2)
    cp = HERE / "logs" / f"CHECKPOINT_{day}.log"
    last_cp = ""
    if cp.exists():
        tail = [l for l in cp.read_text(encoding="utf-8").splitlines() if l.strip()]
        last_cp = tail[-1] if tail else ""
    s = {"active": True, "started": "09:15:00", "stopped": None, "capital": cap,
         "leverage": G.LEVERAGE, "slots": 3,
         "per_slot": round(cap * G.LEVERAGE / 3, 0),
         "exposure": round(cap * G.LEVERAGE, 0),
         "window": f"09:15 - {d.get('as_of', '')}", "trades": len(rows),
         "open_n": len(d.get("open", [])), "done_n": len(done),
         "wins": len(wins), "losses": len(done) - len(wins),
         "win_pct": round(len(wins) * 100.0 / len(done)) if done else 0,
         "gross": round(sum(r["gross"] for r in rows), 2),
         "charges": round(sum(r["chg"] for r in rows), 2),
         "net": net, "realised": realised, "unrealised": unreal,
         "net_pct": round(net / cap * 100, 2) if cap else 0,
         "target_pct": 30.0, "target_rs": round(cap * 0.30, 2),
         "target_hit": net >= cap * 0.30, "target_gap": round(net - cap * 0.30, 2),
         "best": ({"sym": max(rows, key=lambda t: t["net"])["sym"],
                   "net": round(max(r["net"] for r in rows), 2)} if rows else None),
         "worst": ({"sym": min(rows, key=lambda t: t["net"])["sym"],
                    "net": round(min(r["net"] for r in rows), 2)} if rows else None),
         "ex_best": 0.0, "ex_best_pct": 0.0, "displaced": 0, "skipped": {},
         "ticks": 0, "last_tick": d.get("as_of", ""), "err": None,
         "window_closed": False, "entry_window": "09:15-14:30",
         "max_from_open": 0, "live": True,
         "rule": ("SHADOW ENGINE (paper). Volume expansion out of a quiet base OR a "
                  "breakout from a tight band to a new day high, above VWAP, "
                  "SuperTrend up, SAR below. Only names already +2% on the day. "
                  "Exit: -1% stop, ride and give back 1.2% from the peak, cut when "
                  "volume dies, hand the slot back after 10 idle minutes, out 1% "
                  "before an upper circuit. Own 30-second feed from Dhan, warm-up "
                  "from the previous session. "
                  + (f"Last check \u2014 {last_cp}" if last_cp else "")),
         }
    return {"ok": True, "summary": s, "trades": rows, "shadow": True}


def main():
    day = datetime.now().strftime("%Y%m%d")
    once = "--once" in sys.argv
    for a in sys.argv[1:]:
        if a.isdigit() and len(a) == 8:
            day = a
    prev = _prev_session_dir(day)
    warm = load_warm(prev)
    outj = HERE / "logs" / f"SHADOW_LIVE_{day}.json"
    outl = HERE / "logs" / f"SHADOW_LIVE_{day}.log"

    def log(m):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
        print(line, flush=True)
        with outl.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"live_shadow {day}: warm-up from {prev.name if prev else 'NONE'} "
        f"({len(warm)} symbols). NEW logic, paper only, no orders.")
    if not warm:
        log("WARNING: no previous session -- indicators blind until ~09:28.")

    seen, last_cp, last_fetch = set(), None, 0.0
    while True:
        hms = datetime.now().strftime("%H:%M:%S")
        if IST_OPEN <= hms <= "15:31:00" and time.time() - last_fetch >= FETCH_EVERY:
            last_fetch = time.time()
            try:
                fetch_live(day, pick_universe(day, log), log)
            except Exception as e:
                log(f"feed error: {type(e).__name__}: {e}")
        try:
            r = cycle(day, warm, log)
        except Exception as e:
            log(f"cycle error: {type(e).__name__}: {e}")
            r = None
        if r:
            for c in r["closed"]:
                k = (c["sym"], c["in_t"], c["out_t"])
                if k in seen:
                    continue
                seen.add(k)
                log(f"  {c['sym']:12s} {c['in_t']}->{c['out_t']} "
                    f"{c['in']:.2f}->{c['out']:.2f} "
                    f"{(c['out']/c['in']-1)*100:+.2f}%  {c['why']:14s} "
                    f"Rs {c['net']:,.0f}")
            outj.write_text(json.dumps(
                {"day": day, "as_of": hms, "symbols": r["tape"],
                 "warmed": r["warmed"], "net": r["net"],
                 "trades": r["closed"], "open": list(G.OPEN_NOW)},
                indent=1, default=str), encoding="utf-8")
            log(f"{r['tape']} symbols ({r['warmed']} warmed) | "
                f"{len(r['closed'])} trades | NET Rs {r['net']:,.0f}")
            try:
                dashboard(day, hms, r)
            except Exception as e:
                log(f"dashboard error: {type(e).__name__}: {e}")
            # every ten minutes from 09:15
            mins = int(hms[:2]) * 60 + int(hms[3:5])
            slot = mins - (mins % 10)
            if hms >= "09:25:00" and slot != last_cp:
                last_cp = slot
                upto = f"{slot // 60:02d}:{slot % 60:02d}:00"
                try:
                    checkpoint(day, getattr(cycle, "tape", {}), r["closed"], upto, log)
                except Exception as e:
                    log(f"checkpoint error: {type(e).__name__}: {e}")
        # RELOAD CHANNEL. Dropping logs/shadow_reload.flag makes this
        # process exit; the supervisor restarts it within five seconds with the
        # current code. Added 07-Sep so a rule fix never again needs Sri to
        # close and reopen the .bat mid-session.
        # NOT in logs/control/ -- the supervisor owns that directory and eats
        # every *.cmd it does not recognise, which silently swallowed the first
        # reload request on 07-Sep. Its own folder, its own flag.
        rl = HERE / "logs" / "shadow_reload.flag"
        if rl.exists():
            try:
                rl.unlink()
            except Exception:
                pass
            log("reload requested -- exiting so the supervisor restarts me")
            break
        if once or hms >= "15:31:00":
            break
        time.sleep(REFRESH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
