"""
super_monitor.py -- watches the SUPER STOCKS tab and says, out loud, whether it
                    is actually working.

WHY THIS EXISTS
    Super Stocks was broken from the day it was built and nobody noticed for two
    sessions. One line -- the sweep keys quotes by str(sid), the universe keys
    names by int(sid) -- meant every stock was skipped on every pass. The tab sat
    empty while VOLTAMP ran +6% and VINCOFE +4.5%, and it looked exactly like a
    quiet market.

    Three things had to be true at once for that to survive:
        the backtest shared the bug's assumption, so it passed
        an empty tab and a broken tab printed the same sentence
        nothing ever read the tab's own output log

    This file is the answer to the second and third.

THE ONE RULE THAT MAKES IT WORTH RUNNING
    A monitor that asks the module "are you all right?" is worthless, because a
    broken module answers yes. `scanned = 0` IS the module's own report, and it
    read as a quiet market for two days.

    So every check here is anchored to an INDEPENDENT source: the alarm's own
    early_movers(), which sweeps the same 2,455 stocks through a different code
    path and got the key types right. When the alarm can see fifteen stocks up
    2% from their open and Super Stocks reports it scanned nothing, that is a
    contradiction no amount of internal state can explain away.

COST
    Zero Dhan requests. It reads what is already in memory, once a minute.

OUTPUT
    logs/movers_board/super_monitor_YYYYMMDD.txt   plain English, newest last
    logs/movers_board/super_monitor_YYYYMMDD.jsonl one record per pass
    STATE["super_health"]                          one line on the tab itself
"""
from __future__ import annotations

import json
import math
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGDIR = HERE / "logs" / "movers_board"
LOGDIR.mkdir(parents=True, exist_ok=True)

CHECK_SEC = 60                  # how often to look
STALE_SCAN_SEC = 90             # scan() runs every 10s; 90s without one is dead
CONTRADICTION_MIN = 5           # alarm sees movers, tab scanned nothing, for N min
DRY_SPELL_MIN = 20              # stocks climbing but none ever bursting, for N min
OUTCOME_HORIZON = 600           # judge a pick 10 minutes later, as the backtest did

# What the backtest said to expect. If live drifts far outside this, either the
# market changed or the code did, and either way it needs saying.
EXPECT_HIT = 67.0
EXPECT_LO, EXPECT_HI = 58.0, 74.0
EXPECT_PER_DAY = 8.1

_lock = threading.Lock()
_state = {
    "verdict": "not started", "level": "info", "findings": [],
    "checked": None, "picks": 0, "judged": 0, "worked": 0,
}
_seen = {}                  # sym -> {t, price, peak}
_contradiction_since = [None]
_dry_since = [None]
_last_scan_ts = [None, None]     # (value, when we first saw that value)
_history = deque(maxlen=240)


def _hms(now=None):
    return (now or datetime.now()).strftime("%H:%M:%S")


def _sec(t):
    try:
        return int(t[0:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])
    except (TypeError, ValueError, IndexError):
        return None


def health():
    with _lock:
        return dict(_state)


def _wilson_lo(k, n):
    if not n:
        return 0.0
    p, z = k / n, 1.96
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    d = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - d) / den * 100


# ========================================================== the checks
def check(superstocks, alarm, now_hms=None, log=lambda m: None):
    """One pass. Returns (level, verdict, findings)."""
    now_hms = now_hms or _hms()
    t = _sec(now_hms) or 0
    find = []
    level = "ok"

    def add(lv, msg):
        nonlocal level
        find.append((lv, msg))
        order = {"ok": 0, "info": 1, "warn": 2, "critical": 3}
        if order[lv] > order[level]:
            level = lv

    # ---- what the module says about itself -----------------------------
    try:
        s = superstocks.summary()
    except Exception as e:
        add("critical", f"summary() raised {type(e).__name__}: {str(e)[:70]}")
        return _finish(level, find, now_hms, log)
    f = s.get("funnel") or {}
    scanned = s.get("scanned") or 0
    rows_n = s.get("rows") or 0

    # ---- what an INDEPENDENT sweep says --------------------------------
    # early_movers() walks the same 2,455 stocks through different code that
    # gets the key types right. It is the control.
    movers = None
    try:
        movers = len(alarm.early_movers())
    except Exception as e:
        add("warn", f"could not cross-check against the alarm: "
                    f"{type(e).__name__} {str(e)[:60]}")
    quotes = 0
    try:
        _ts, snap = alarm.snapshot()
        quotes = len(snap or {})
    except Exception as e:
        add("warn", f"could not read the sweep: {type(e).__name__} {str(e)[:60]}")

    # ---- 1. is the scan running at all? --------------------------------
    ts = s.get("ts")
    if _last_scan_ts[0] == ts:
        age = t - (_last_scan_ts[1] or t)
        if age > STALE_SCAN_SEC:
            add("critical", f"scan() has not run for {age}s -- last pass {ts}. "
                            f"The Super Stocks thread is dead or stuck.")
    else:
        _last_scan_ts[0], _last_scan_ts[1] = ts, t

    # ---- 2. THE BUG THAT HID FOR TWO DAYS ------------------------------
    if quotes > 0 and scanned == 0:
        if _contradiction_since[0] is None:
            _contradiction_since[0] = t
        mins = (t - _contradiction_since[0]) / 60.0
        msg = (f"the sweep is returning {quotes} quotes but Super Stocks scanned "
               f"ZERO of them")
        if s.get("unmapped"):
            msg += f" ({s['unmapped']} could not be matched to a stock name)"
        if mins >= CONTRADICTION_MIN:
            add("critical", msg + f" -- {mins:.0f} minutes now. This is the "
                                  f"str/int key fault, or another like it. "
                                  f"NOT a quiet market.")
        else:
            add("warn", msg + " -- watching")
    else:
        _contradiction_since[0] = None

    # ---- 3. the alarm can see movers, the tab cannot --------------------
    if movers is not None and movers >= 3 and (f.get("up2") or 0) == 0 and scanned > 0:
        add("critical",
            f"the alarm can see {movers} stocks up 2%+ from their open; Super "
            f"Stocks scanned {scanned} and found none. Two sweeps of the same "
            f"market disagree -- one of them is wrong.")

    # ---- 4. climbing but never bursting --------------------------------
    if (f.get("climbing") or 0) > 0 and (f.get("bursting") or 0) == 0:
        if _dry_since[0] is None:
            _dry_since[0] = t
        mins = (t - _dry_since[0]) / 60.0
        if mins >= DRY_SPELL_MIN:
            add("warn", f"{f.get('climbing')} stocks climbing but not one has "
                        f"cleared today's speed bar (+{s.get('bar', 0):.2f}% in 90s) "
                        f"for {mins:.0f} minutes. Either the market has gone "
                        f"still, or the 90-second window is not being measured.")

    # ---- 4b. is the bar itself sane? ------------------------------------
    # The gate shipped at a fixed 1.2% and fired on 0.3% of real windows. It is
    # now a percentile, so the failure mode to watch for is the opposite: a bar
    # so low that drift gets carded, or one stuck at the floor all session.
    bar = s.get("bar")
    if bar is not None and (s.get("bar_samples") or 0) >= 300:
        if bar <= superstocks.MIN_RISE_FLOOR and (f.get("bursting") or 0) > 12:
            add("warn", f"the speed bar is stuck at its floor "
                        f"(+{bar:.2f}%) and {f['bursting']} stocks are clearing "
                        f"it. The tab is about to show drift, not bursts.")
    else:
        _dry_since[0] = None

    # ---- 5. is anything synthetic in there? ----------------------------
    try:
        uni = alarm.universe()
        names = {str(v).upper() for v in uni.values()}
        bad = [r["sym"] for r in superstocks.rows()
               if str(r.get("sym", "")).upper() not in names]
        if bad:
            add("critical", f"symbols on the tab that are not real NSE stocks: "
                            f"{', '.join(bad[:5])}. Test data has reached live output.")
    except Exception:
        pass

    # ---- 6. do the picks behave the way the backtest said? -------------
    _track(superstocks, alarm, t)
    judged, worked = _score(t)
    if judged >= 10:
        rate = worked / judged * 100
        lo = _wilson_lo(worked, judged)
        if lo > EXPECT_HI:
            add("info", f"picks are doing BETTER than the backtest: {rate:.0f}% "
                        f"of {judged} reached +0.5% in 10 min (expected "
                        f"{EXPECT_LO:.0f}-{EXPECT_HI:.0f}%)")
        elif rate < EXPECT_LO - 15:
            add("warn", f"picks are underperforming the backtest: {rate:.0f}% of "
                        f"{judged} reached +0.5% in 10 min, expected around "
                        f"{EXPECT_HIT:.0f}%. Worth a look, not yet an alarm.")

    # ---- 7. volume of picks --------------------------------------------
    mins_open = max(1.0, (t - 33300) / 60.0)          # since 09:15
    if mins_open >= 45:
        picks = len(_seen)
        if picks == 0 and movers and movers >= 5:
            add("warn", f"not one Super Stock in {mins_open:.0f} minutes, while "
                        f"the alarm has {movers} movers on screen. The gate may "
                        f"be too tight, or something upstream is empty.")

    if level == "ok":
        find.append(("ok", f"{scanned} scanned -> {f.get('up2',0)} up 2% -> "
                           f"{f.get('climbing',0)} climbing -> {f.get('bursting',0)} "
                           f"bursting -> {rows_n} on screen"))
    return _finish(level, find, now_hms, log, scanned, f, rows_n, movers)


# ---------------------------------------------------- outcome tracking
def _track(superstocks, alarm, t):
    """Remember every pick, and keep its running peak, so it can be judged."""
    try:
        rows = superstocks.rows()
    except Exception:
        return
    for r in rows:
        sym = r.get("sym")
        px = r.get("price")
        if not sym or not px:
            continue
        if sym not in _seen:
            _seen[sym] = {"t": t, "entry": float(px), "peak": float(px)}
    # refresh peaks from the live sweep -- free, it is already in memory
    try:
        _ts, snap = alarm.snapshot()
        uni = alarm.universe()
        by_sym = {}
        for sid, q in (snap or {}).items():
            try:
                nm = uni.get(int(sid))
            except (TypeError, ValueError):
                nm = None
            if nm:
                by_sym[nm] = float(q[0] or 0)
        for sym, d in _seen.items():
            p = by_sym.get(sym)
            if p and p > d["peak"]:
                d["peak"] = p
    except Exception:
        pass


def _score(t):
    judged = worked = 0
    for sym, d in _seen.items():
        if t - d["t"] < OUTCOME_HORIZON:
            continue
        judged += 1
        if d["peak"] >= d["entry"] * 1.005:
            worked += 1
    return judged, worked


# ------------------------------------------------------------- output
def _finish(level, find, now_hms, log, scanned=0, funnel=None, rows_n=0, movers=None):
    judged, worked = _score(_sec(now_hms) or 0)
    verdict = {
        "ok": "Super Stocks is behaving as expected",
        "info": "Super Stocks is working; something worth knowing",
        "warn": "Super Stocks needs a look",
        "critical": "SUPER STOCKS IS NOT WORKING",
    }[level]
    with _lock:
        _state.update({"verdict": verdict, "level": level,
                       "findings": [m for _lv, m in find], "checked": now_hms,
                       "picks": len(_seen), "judged": judged, "worked": worked})
    rec = {"ts": now_hms, "level": level, "scanned": scanned,
           "funnel": funnel or {}, "rows": rows_n, "alarm_movers": movers,
           "picks": len(_seen), "judged": judged, "worked": worked,
           "findings": [f"{lv}: {m}" for lv, m in find]}
    _history.append(rec)
    day = datetime.now().strftime("%Y%m%d")
    try:
        with (LOGDIR / f"super_monitor_{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception as e:
        log(f"super_monitor: could not write jsonl -- {type(e).__name__} {e}")
    # The plain-English file is rewritten each pass so it is always readable
    # top to bottom without hunting for the latest block.
    try:
        _write_report(day)
    except Exception as e:
        log(f"super_monitor: could not write report -- {type(e).__name__} {e}")
    if level in ("warn", "critical"):
        for lv, m in find:
            if lv in ("warn", "critical"):
                log(f"super_monitor [{lv.upper()}] {m}")
    return level, verdict, [m for _lv, m in find]


def _write_report(day):
    p = LOGDIR / f"super_monitor_{day}.txt"
    h = list(_history)
    worst = max((r["level"] for r in h),
                key=lambda l: {"ok": 0, "info": 1, "warn": 2, "critical": 3}[l],
                default="ok")
    lines = []
    lines.append("=" * 74)
    lines.append(f"  SUPER STOCKS -- health, {day}")
    lines.append("=" * 74)
    lines.append("")
    st = health()
    lines.append(f"  RIGHT NOW ({st['checked']}): {st['verdict']}")
    lines.append(f"  worst so far today: {worst.upper()}")
    lines.append("")
    lines.append(f"  picks made today      : {st['picks']}   "
                 f"(the backtest expects about {EXPECT_PER_DAY:.0f})")
    if st["judged"]:
        lines.append(f"  judged after 10 min   : {st['judged']}, of which "
                     f"{st['worked']} reached +0.5%  "
                     f"({st['worked']/st['judged']*100:.0f}%, "
                     f"backtest said {EXPECT_LO:.0f}-{EXPECT_HI:.0f}%)")
    else:
        lines.append("  judged after 10 min   : none old enough yet")
    lines.append("")
    lines.append("  Anything that was not routine:")
    seen_msgs = set()
    any_bad = False
    for r in h:
        for m in r["findings"]:
            if m.startswith(("warn:", "critical:")) and m not in seen_msgs:
                seen_msgs.add(m)
                any_bad = True
                lines.append(f"    {r['ts']}  {m}")
    if not any_bad:
        lines.append("    nothing. Every pass was clean.")
    lines.append("")
    lines.append("  The funnel, every 10 minutes:")
    lines.append(f"    {'time':<10}{'scanned':>9}{'up 2%':>8}{'climbing':>10}"
                 f"{'bursting':>10}{'on screen':>11}{'alarm sees':>12}")
    last = None
    for r in h:
        mm = r["ts"][:5]
        if last is not None and (_sec(r["ts"]) - last) < 600:
            continue
        last = _sec(r["ts"])
        fn = r.get("funnel") or {}
        lines.append(f"    {mm:<10}{r['scanned']:>9}{fn.get('up2',0):>8}"
                     f"{fn.get('climbing',0):>10}{fn.get('bursting',0):>10}"
                     f"{r['rows']:>11}{(r.get('alarm_movers') if r.get('alarm_movers') is not None else '-'):>12}")
    lines.append("")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def loop(superstocks, alarm, log=lambda m: None, sleep=None):
    """Run inside the board. Never raises out."""
    import time
    sleep = sleep or time.sleep
    while True:
        try:
            now = datetime.now()
            if now.weekday() >= 5 or not ("09:14:00" <= _hms(now) <= "15:30:00"):
                sleep(120)
                continue
            check(superstocks, alarm, _hms(now), log)
        except Exception as e:
            log(f"super_monitor: EXCEPTION {type(e).__name__}: {str(e)[:120]}")
        sleep(CHECK_SEC)


def reset_for_day():
    with _lock:
        _seen.clear()
        _history.clear()
        _contradiction_since[0] = None
        _dry_since[0] = None
        _last_scan_ts[0] = _last_scan_ts[1] = None
        _state.update({"verdict": "not started", "level": "info", "findings": [],
                       "checked": None, "picks": 0, "judged": 0, "worked": 0})
