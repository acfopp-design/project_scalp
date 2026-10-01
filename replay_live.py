"""
replay_live.py -- run the LIVE engine over a recorded session, with no future.

WHAT MAKES THIS DIFFERENT FROM paper_engine.py
    paper_engine replays the session as a back-test: it sweeps six slot counts
    and keeps the best, which is a decision taken with the whole day visible.
    This does not simulate the strategy -- it runs live_paper.py itself, the
    same code that trades forward, and feeds it the session one recorded
    snapshot at a time in chronological order.

    At every step the engine sees exactly what it would have seen live: the
    Super Stocks rows written at that second, and prices as at that second. The
    clock is virtual, so the engine's own cooldowns, dwell floors and time caps
    all behave as they did today. Nothing downstream of the current tick is
    reachable. If this makes money, it is not because it peeked.

WHY IT MATTERS
    01-Sep's live run lost Rs 1,372 in five minutes at 15:07. That was one
    five-minute sample at the worst hour of the day. This runs the same engine
    over the whole session from 09:15, which is the only way to know whether the
    engine is broken or the hour was.
"""
import json
import sys
import time as _realtime
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"
sys.path.insert(0, str(HERE))

import live_paper as LP           # noqa: E402

# This is an offline back-test. Never write to the live session's state file --
# see the OFFLINE note in live_paper._statefile().
LP.OFFLINE = True

# ---------------------------------------------------------------------------
# APPLY THE SHIPPED CONFIG, exactly as Movers_app does at boot.
#
# 04-Sep: found that replay_live and regress.py were running on live_paper's
# MODULE DEFAULTS, not on live_config.json -- the file the board hot-applies and
# actually trades. Three constants differed:
#       MIN_POSITION_RS   live 60000   module 40000
#       MIN_PRICE         live   100   module    20
#       PHASE_ENABLED     live False   module  True   <-- the damaging one
#
# PHASE_ENABLED=True is the time-banded phase table, which was measured at
# Rs 31,178 against a Rs 44,870 baseline -- a Rs 13,692 REGRESSION -- and turned
# off in live config on 03-Sep. Every replay and every regress run since has
# therefore been scoring a system the board does not run, including one feature
# that had already been rejected.
#
# This is the fifth time replay and live have differed in a way that changed a
# conclusion (13s vs 3s tick, close-to-close ATR, a shared state file, rules
# fitted on data live never produced, and now the config itself). Standing rule:
# before trusting any replay result, ask what the replay does differently.
#
# --nocfg reproduces the old behaviour for comparison.
def _apply_live_config(log=lambda m: None):
    if "--nocfg" in sys.argv:
        return []
    try:
        cfg = json.loads((HERE / "live_config.json").read_text(encoding="utf-8"))
    except Exception as e:
        log(f"replay: no live_config.json ({type(e).__name__}) -- module defaults")
        return []
    changed = []
    for k, v in (cfg.get("live_paper") or {}).items():
        if hasattr(LP, k) and getattr(LP, k) != v:
            changed.append((k, getattr(LP, k), v))
            setattr(LP, k, v)
    return changed


APPLIED = _apply_live_config()

import paper_engine as PE         # noqa: E402


class _Clock:
    """Stands in for the `time` module inside live_paper, so its cooldowns,
    dwell floors and time caps all run on the session's clock, not the wall."""
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, _n):
        pass


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def load_board(day, fresh_cross_min=3):
    """Signals from the BOARD, not from Super Stocks.

    WHY THIS EXISTS -- Sri's question, and he was right.
        The engine was fed only from the Super Stocks tab, which cards 19-48
        stocks a session and was EMPTY on 27-Aug. The Board carries every ScanX
        mover -- 124-155 stocks a session -- and its log goes back to 17-Aug.
        Counted in the 09:15-10:30 window:

            session   Super Stocks    Board
            21-Aug     no log         229 snapshots / 144 stocks
            24-Aug     no log         290 / 155
            25-Aug     no log         324 / 126
            26-Aug     no log         215 / 139
            27-Aug     21 / 19        314 / 146
            28-Aug    251 / 38        296 / 130
            01-Sep    305 / 124       305 / 124

        So feeding from the Board takes the testable sample from two sessions to
        seven, and rescues 27-Aug, which I had written off as unusable when in
        fact only one tab was empty that day.

    THE SIGNAL is the board's own BUY TRIGGERED condition: a FRESH MA/EMA buy
    cross on 30-second bars (crossBuy with crossAge under a few minutes). That
    is the panel he actually reads, so the engine is now trading what the board
    tells him rather than a second opinion.
    """
    snaps = []
    with (LOGS / f"board_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            hms = str(d.get("ts"))[11:19]
            if not hms or hms < "09:15:00":
                continue
            if hms > "15:30:00":
                break
            t = sec(hms)
            rows, seen = [], set()
            for _p, rr in (d.get("panels") or {}).items():
                for r in (rr or []):
                    sym = r.get("sym")
                    if not sym or sym in seen:
                        continue
                    if not r.get("crossBuy"):
                        continue
                    ca = r.get("crossAge")
                    if ca is None or ca > fresh_cross_min:
                        continue
                    seen.add(sym)
                    rows.append({"sym": sym, "sid": sym,
                                 "price": r.get("price"),
                                 # the board's own score stands in for urgency
                                 "urgency": float(r.get("score") or 0),
                                 "from_open": r.get("streakPct"),
                                 "preopen": bool(r.get("pinned"))})
            if rows:
                snaps.append((t, rows))
    snaps.sort(key=lambda x: x[0])
    return snaps


def load(day):
    snaps = []
    sup = LOGS / f"super_{day}.jsonl"
    # 21-26 Aug have a full board log but no Super Stocks log at all. Requiring
    # one meant board-fed replays of those four sessions failed silently with a
    # FileNotFoundError -- which is exactly the sample this was built to unlock.
    if sup.exists():
      with sup.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = sec(d["ts"])
            if sec("09:15:00") <= t <= sec("15:30:00"):
                snaps.append((t, [r for r in (d.get("rows") or [])
                                  if r.get("sym") != "AAA"]))
    snaps.sort(key=lambda x: x[0])

    bars = {}
    with (LOGS / f"bars30_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("sym") == "AAA":
                continue
            t = sec(d["hhmm"])
            if sec("09:15:00") <= t <= sec("15:30:00"):
                bars.setdefault(str(d.get("sid") or d["sym"]), []).append((t, d["c"]))
    # index by BOTH sid and symbol: the super rows carry sid, the bar file
    # carries whichever it had at the time.
    by_sym = {}
    with (LOGS / f"bars30_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("sym") == "AAA":
                continue
            t = sec(d["hhmm"])
            if sec("09:15:00") <= t <= sec("15:30:00"):
                by_sym.setdefault(d["sym"], []).append(
                    (t, d["c"], d["h"], d["l"], d.get("v") or 0))
    # ---- PREFER THE FETCHED TAPE over bars30 ------------------------------
    # bars30 only covers symbols Movers_ticks tracked -- on 04-Sep that was 81
    # of the 169 symbols carded, so the replay could not price 52% of what it
    # could trade. Those positions froze at their entry price and exited flat on
    # the time cap, which is why the replay reported +Rs 31,562 on a day the
    # live engine made -Rs 747. build_tape.py fetches true 30-second bars for
    # every carded symbol; use them wherever they exist.
    tape_dir = HERE / "logs" / "tape" / day
    filled = 0
    if tape_dir.is_dir():
        lo_t, hi_t = sec("09:15:00"), sec("15:30:00")
        for f in tape_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            sym = f.stem
            ts = d.get("t") or []
            c, h, l = d.get("c") or [], d.get("h") or [], d.get("l") or []
            v = d.get("v") or []
            if not ts or len(c) != len(ts):
                continue
            ser = []
            for i, tv in enumerate(ts):
                try:
                    dt = datetime.fromtimestamp(tv, LP.IST)
                except Exception:
                    continue
                # MUST be this session. The 30-second feed only carries about
                # three sessions, so a cache folder written for an older day can
                # hold nothing but RECENT bars. Matching on time-of-day alone
                # would have priced a 27-Aug replay with 04-Sep prices -- caught
                # before it produced a single number, but only just.
                if dt.strftime("%Y%m%d") != day:
                    continue
                tt = sec(dt.strftime("%H:%M:%S"))
                if lo_t <= tt <= hi_t:
                    ser.append((tt, c[i], h[i] if i < len(h) else c[i],
                                l[i] if i < len(l) else c[i],
                                (v[i] if i < len(v) else 0) or 0))
            if ser:
                by_sym[sym] = ser
                bars[sym] = [(r[0], r[1]) for r in ser]
                filled += 1
    if filled:
        print(f"replay {day}: tape cache supplied {filled} symbols")

    for m in (bars, by_sym):
        for k in m:
            m[k].sort()
    return snaps, bars, by_sym




_VW = {}


def _vwap_at(rows, t):
    """Session VWAP as at t, from the same bars the replay prices with.

    Cached per (symbol series id, t-bucket) because it is recomputed on every
    tick for every row otherwise. Typical-price weighted by volume where volume
    exists; falls back to a simple mean of typical prices when the feed gives
    no volume, which is honest but weaker -- flagged rather than silently
    pretending it is a real VWAP.
    """
    if not rows:
        return None
    key = (id(rows), t // 30)
    hit = _VW.get(key)
    if hit is not None:
        return hit
    pv = vv = 0.0
    tp_sum = n = 0
    for i, r in enumerate(rows):
        if r[0] + BAR_SEC > t:          # in-progress bar is not knowable yet
            break
        tp = (r[2] + r[3] + r[1]) / 3.0 if len(r) >= 4 else r[1]
        v = (r[4] if len(r) >= 5 else 0) or 0
        pv += tp * v
        vv += v
        tp_sum += tp
        n += 1
    out = (pv / vv) if vv else ((tp_sum / n) if n else None)
    _VW[key] = out
    return out


BAR_SEC = 30


def _bar_at(rows, t):
    """(low, high) of the most recent COMPLETED bar at or before t.

    NO LOOK-AHEAD. Bars are stamped with their START time, so the bar whose
    start is <= t is usually still IN PROGRESS -- at 09:15:10 that bar spans
    09:15:00-09:15:30 and its high/low contain twenty seconds that have not
    happened yet. Judging a stop against it would let the engine see the future
    and would quietly inflate every result, which is the entire family of bug
    this file has already produced three times.

    So require the bar to have CLOSED: start + BAR_SEC <= t. The cost is that a
    stop can trigger up to one bar late, which is the pessimistic direction and
    the right one.
    """
    lo, hi = 0.0, 0.0
    a, b = 0, len(rows) - 1
    best = None
    while a <= b:
        m = (a + b) // 2
        if rows[m][0] + BAR_SEC <= t:
            best = rows[m]; a = m + 1
        else:
            b = m - 1
    if best and len(best) >= 4:
        hi, lo = best[2], best[3]
    return lo, hi


def _atr_at(rows, t, n=14):
    """ATR(14) as a % of price using only bars at or before t. by_sym here holds
    (t, close) pairs, so true range degrades to |close-to-close| -- a slight
    UNDERSTATEMENT of ATR, which makes the stop narrower, not wider. Erring
    against the change being tested is the right direction."""
    pre = [x for x in rows if x[0] <= t and x[1]]
    if len(pre) < 5:
        return None
    trs = []
    for i in range(max(1, len(pre) - n), len(pre)):
        c0, _c, h, l = pre[i - 1][1], pre[i][1], pre[i][2], pre[i][3]
        trs.append(max(h - l, abs(h - c0), abs(l - c0)))
    if not trs or not pre[-1][1]:
        return None
    return sum(trs) / len(trs) / pre[-1][1] * 100.0


class Alarm:
    """Prices as at the current virtual second -- last print at or before it.
    Never the next one; that would be the future.

    KEYED BY SYMBOL, not security-id: the Super Stocks log writes `sid: null`
    on every row, so a sid lookup matched nothing and the first run of this
    replay reported zero trades on a session with 1,201 snapshots. The rows fed
    to the engine get sid = symbol to match."""
    def __init__(self, by_sym):
        self.by_sym = by_sym
        self.t = 0

    def snapshot(self):
        out = {}
        for sid, rows in self.by_sym.items():
            lo, hi = 0, len(rows) - 1
            best = None
            while lo <= hi:
                mid = (lo + hi) // 2
                # The bar's CLOSE is only knowable once the bar has closed.
                # Using the bar whose START is <= t means reading a price up to
                # 30 seconds ahead -- which set entry fills as well as exits.
                if rows[mid][0] + BAR_SEC <= self.t:
                    best = rows[mid][1]
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best:
                out[str(sid)] = (best, 0, 0, 0, 0, 0, 0, 0)
        return self.t, out


def run(day=None, capital=1_00_000, leverage=5, target=10, log=lambda m: None,
        until="15:20:00", source="super", fresh_cross_min=3):
    day = day or datetime.now().strftime("%Y%m%d")
    snaps, bars, by_sym = load(day)
    if source == "board":
        snaps = load_board(day, fresh_cross_min)
    if not snaps:
        return None, f"no super log for {day}"

    clock = _Clock()
    base = datetime.strptime(day, "%Y%m%d").replace(tzinfo=LP.IST)
    real_time = LP.time
    LP.time = clock                      # the engine's clock is now the session's
    try:
        LP.reset()
        LP.start(capital, leverage, target, log=log)
        alarm = Alarm(by_sym)
        for t, rows in snaps:
            if t > sec(until):
                break
            clock.now = (base + timedelta(seconds=t)).timestamp()
            alarm.t = t
            # inject ATR(14) as at this second, so the replay's stop matches
            # what the live engine would compute from the card's own chart
            # (low, high) of the bar containing this second, per symbol, so
            # the engine can judge the stop on the LOW and the target on the
            # HIGH instead of on the 30-second close -- see live_paper.tick's
            # `extremes` note. --closes reproduces the old, inflated behaviour.
            ext = None
            if "--closes" not in sys.argv:
                ext = {}
                # Every symbol on the tab RIGHT NOW, plus every symbol we are
                # still holding. The second half is the one that matters: a
                # position whose stock has dropped off the tab is exactly the
                # one that stops out unseen, and it is absent from `rows`.
                syms = {r.get("sym") for r in rows if r.get("sym")}
                try:
                    syms |= {str(pp.get("sid")) for pp in LP._S["open"].values()}
                except Exception:
                    pass
                for sym in syms:
                    lo, hi = _bar_at(by_sym.get(sym) or [], t)
                    if lo or hi:
                        ext[str(sym)] = (lo, hi)
            LP.tick(alarm, [dict(r, sid=r.get("sym"),
                                 atr_pct=_atr_at(by_sym.get(r.get("sym")) or [], t),
                                 vwap=_vwap_at(by_sym.get(r.get("sym")) or [], t))
                            for r in rows], log=log, extremes=ext)
        # square off whatever is still open, at the last recorded price
        clock.now = (base + timedelta(seconds=sec(until))).timestamp()
        alarm.t = sec(until)
        _ts, snap = alarm.snapshot()
        LP._close_all({k: v[0] for k, v in snap.items()}, "end of replay", log)
        out = LP.snapshot()
    finally:
        LP.time = real_time
    return out, None


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    cap = float(sys.argv[2]) if len(sys.argv) > 2 else 1_00_000
    d, err = run(day, cap)
    if err:
        print("ERR:", err)
        raise SystemExit(1)
    s, tr = d["summary"], d["trades"]
    print(f"\nREPLAY {day} -- live engine, no future knowledge, "
          f"Rs {cap:,.0f} at {s['leverage']}x in {s['slots']} positions")
    print(f"{'Stock':<13}{'In':<10}{'Buy':>9} {'Out':<10}{'Sell':>9}{'Qty':>6}"
          f"{'Net':>10}  Why")
    for t in tr:
        print(f"{t['sym']:<13}{t.get('in_hms',''):<10}{t['in']:>9.2f} "
              f"{t.get('out_hms',''):<10}{t['out']:>9.2f}{t['qty']:>6}"
              f"{t['net']:>10,.0f}  {t['why']}")
    print(f"\ntrades {s['trades']}  wins {s['wins']}  gross {s['gross']:,.0f}  "
          f"charges {s['charges']:,.0f}  NET Rs {s['net']:,.0f} ({s['net_pct']}%)")
    print(f"skipped {s['skipped']}  displaced {s['displaced']}")
