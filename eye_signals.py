"""eye_signals.py -- the human-eye logic (tune_v6 + eye_cfg) as a signal source
for live_paper.py's Live Paper Trading tab.

WHY A SEPARATE MODULE. live_paper.tick() is handed last prices only -- no bar
series -- and it runs every few seconds. The v6 rules need the complete 30-second
series per name and a full replay takes 10-20s for ~90 symbols. So the replay
runs HERE, on its own thread, and publishes a decision that tick() just reads.
Deterministic and forward-only, so each cycle reproduces the whole morning plus
whatever the newest bar adds.

WHAT IT DECIDES
  want_open()   symbols v6 wants a position in right now, best first
  wants_close() True when v6's adaptive dip exit has fired on a held position

Charges are NOT priced here. live_paper already prices every leg with
paper_engine.charges (Dhan intraday, both legs) and that is left alone.
"""
import json, threading, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
HERE = Path(__file__).parent
IST = timezone(timedelta(hours=5, minutes=30))

REFRESH   = 25          # seconds between full replays
MAX_NAMES = 220         # how many pre-open names to keep tape for
OPEN_T, CLOSE_T = "0915", "1530"
WARM = 60               # previous-session bars, so indicators are alive at 09:15

# SELECTION. The v6 rules are a good per-stock entry/exit and a poor stock
# picker: turned loose on the whole pre-open list they churn. Sri's own 22
# entries sat at the 76th percentile of volume and the 92nd of bar range inside
# each stock's session, so a name only qualifies when its bar is exceptional BY
# ITS OWN STANDARDS. Score = volume pctile + range pctile + keep-ratio.
# On 21-Sep's real feed: no gate +3.68%, score>=1.2 +6.92%, >=1.5 +26.22%,
# >=1.8 +17.44%, >=2.0 +17.36%. Every threshold beats no gate, so this is a
# slope and not one lucky value -- but it is still ONE DAY.
SELECT_MIN  = 1.5       # 0 disables the gate
BEST_PER_BAR = 1        # at most this many names promoted from any one bar

_lock  = threading.Lock()
_state = {"clock": "", "cycles": 0, "err": "", "open": [], "closing": {},
          "tape": 0, "holding": []}
_thread = None


# ---------------------------------------------------------------- tape
def _day():  return datetime.now(IST).strftime("%Y%m%d")

def _prev_tape_day(day):
    b = HERE / "logs" / "tape_live"
    if not b.exists(): return None
    d = sorted(p.name for p in b.iterdir() if p.is_dir() and p.name < day)
    return d[-1] if d else None

def _series(day, sym):
    f = HERE / "logs" / "tape_live" / day / f"{sym}.json"
    if not f.exists(): return []
    try: d = json.loads(f.read_text(encoding="utf-8"))
    except Exception: return []
    ts, c = d.get("t") or [], d.get("c") or []
    if not ts or len(c) != len(ts): return []
    o = d.get("o") or c; h = d.get("h") or c; l = d.get("l") or c
    v = d.get("v") or [0] * len(c)
    return [(datetime.fromtimestamp(t, IST).strftime("%H%M"),
             o[i], h[i], l[i], c[i], v[i] or 0) for i, t in enumerate(ts)]

def _universe(day):
    """Frozen before the open, so using it from 09:15 is not look-ahead."""
    syms = []
    f = HERE / "logs" / "movers_board" / f"premarket_calls_{day}.json"
    if f.exists():
        try:
            for r in (json.loads(f.read_text(encoding="utf-8")).get("calls") or []):
                s = r.get("sym") if isinstance(r, dict) else r
                if s: syms.append(s)
        except Exception: pass
    b = HERE / "logs" / f"bullish_0909_{day}.json"
    if b.exists():
        try:
            for r in json.loads(b.read_text(encoding="utf-8")).get("rows", []):
                if r.get("sym"): syms.append(r["sym"])
        except Exception: pass
    if not syms:
        pv = _prev_tape_day(day)
        if pv:
            d = HERE / "logs" / "tape_live" / pv
            if d.exists(): syms = [p.stem for p in d.glob("*.json")]
    return list(dict.fromkeys(syms))[:MAX_NAMES]

def _fetch(day, syms, log):
    try:
        import live_shadow as LS
        return LS.fetch_live(day, syms, log)
    except Exception as e:
        with _lock: _state["err"] = f"fetch: {type(e).__name__} {str(e)[:70]}"
        return 0


# ---------------------------------------------------------------- replay
def _replay(day, prev, log):
    import tune_v6 as V6
    from eye_cfg import CFG
    scratch = HERE / "logs" / "_eye_scratch"; scratch.mkdir(parents=True, exist_ok=True)
    uni = set(_universe(day))
    now_hm = datetime.now(IST).strftime("%H%M")
    live, closing, tape = [], {}, 0
    tdir = HERE / "logs" / "tape_live" / day
    if not tdir.exists(): return live, closing, 0
    for f in sorted(tdir.glob("*.json")):
        sym = f.stem
        if uni and sym not in uni: continue
        today_rows = [r for r in _series(day, sym) if OPEN_T <= r[0] <= CLOSE_T]
        if len(today_rows) < 3: continue
        tape += 1
        rows = (_series(prev, sym)[-WARM:] if prev else []) + today_rows
        try:
            (scratch / f"{sym}.csv").write_text(
                ";".join(f"{t},{o:g},{h:g},{l:g},{c:g},{int(v)}" for t, o, h, l, c, v in rows))
            d = V6.prep(str(scratch / f"{sym}.csv"))
            trades = V6.sim(d, dict(CFG, SLOTS=99, MAXTR=999))
        except Exception:
            continue
        idx = {d["T"][i]: i for i in range(d["n"] - 1, d["st"] - 1, -1)}
        for t in trades:
            i = idx.get(t["ti"])
            if i is None: continue
            score = d["volq"][i] + d["rngq"][i] + min(d["keep"][i], 1.0)
            if SELECT_MIN and score < SELECT_MIN:
                continue                      # not exceptional for this name
            # a leg whose exit bar has not printed yet is STILL OPEN right now
            if t["ti"] <= now_hm <= t["to"]:
                live.append({"sym": sym, "side": t["side"], "ti": t["ti"],
                             "score": round(score, 3),
                             "px": float(d["C"][-1]), "ang": round(float(d["ANG"][i]), 1)})
            if t["to"] < now_hm:
                closing[sym] = max(closing.get(sym, ""), t["to"])
        # (holding is resolved after the loop; a name with ANY open leg wins)
    # HOLDING is the truth about what is still open, taken BEFORE any dedup.
    # 22-Sep: exits used to read the DEDUPED list, so a leg the dedup dropped
    # looked closed and fired an exit while v6's dip rule had not triggered --
    # every position was cut at ~96 seconds regardless of the stock. The dedup
    # exists to throttle NEW entries; it must never reach the exit path.
    holding = {r["sym"] for r in live}

    # one leg per name, best-scoring bar first within each 30s stamp, then
    # earliest stamp first -- the only tie-break that beat plain first-come.
    live.sort(key=lambda r: (r["ti"], -r["score"]))
    out, seen_sym, per_bar = [], set(), {}
    for r in live:
        if r["sym"] in seen_sym: continue
        if per_bar.get(r["ti"], 0) >= BEST_PER_BAR: continue
        per_bar[r["ti"]] = per_bar.get(r["ti"], 0) + 1
        seen_sym.add(r["sym"]); out.append(r)
    return out, closing, tape, holding


def _worker(log):
    day = _day(); prev = _prev_tape_day(day)
    while True:
        hm = datetime.now(IST).strftime("%H%M")
        if hm > CLOSE_T:
            with _lock: _state["clock"] = hm + " closed"
            time.sleep(60); continue
        if hm < OPEN_T:
            with _lock: _state["clock"] = hm + " pre-open"
            time.sleep(20); continue
        try:
            _fetch(day, _universe(day), log)
            live, closing, tape, holding = _replay(day, prev, log)
            with _lock:
                _state.update(clock=hm, cycles=_state["cycles"] + 1, err="",
                              open=live, closing=closing, tape=tape,
                              holding=sorted(holding))
            (HERE / "logs" / f"EYE_SIGNALS_{day}.json").write_text(
                json.dumps(_state, indent=1), encoding="utf-8")
            log(f"eye: {tape} names on tape, {len(live)} wanted open")
        except Exception as e:
            with _lock: _state["err"] = f"{type(e).__name__}: {str(e)[:90]}"
            log(f"eye ERROR {type(e).__name__}: {str(e)[:110]}")
        time.sleep(REFRESH)


def ensure(log=lambda m: None):
    global _thread
    if _thread and _thread.is_alive(): return
    _thread = threading.Thread(target=_worker, args=(log,), daemon=True)
    _thread.start()
    log("eye: signal thread started")


# ---------------------------------------------------------------- public
def want_open():
    with _lock: return list(_state["open"])

def wants_close(sym, entry_hms):
    """True only once v6's own adaptive dip exit has fired for this name.

    Reads `holding` -- every leg open right now, before the entry dedup -- so a
    position is never closed merely because its candidate row was deduped away.
    """
    with _lock:
        holding = set(_state.get("holding") or ())
        done = _state["closing"].get(sym)
    if sym in holding:
        return False              # v6 still wants to be in this name
    return bool(done)

def status():
    with _lock: return dict(_state, open=len(_state["open"]))
