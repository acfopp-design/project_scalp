"""Walk-forward MIN_EXP through the real 2-slot engine, day by day.

Single-day stories have been wrong 5 times out of 5 on this project, so the
only result that counts is one that holds across independent days.
"""
import json, os, sys
sys.path.insert(0, ".")
import leverage as LV, live_shadow as LS, paper_live as PL, funnel as FN, eye_strategy as ES

DAYS = sys.argv[1].split(",") if len(sys.argv) > 1 else [
    "20260915", "20260916", "20260917", "20260918",
    "20260921", "20260922", "20260923", "20260924", "20260925"]
VALS = [float(v) for v in (sys.argv[2].split(",") if len(sys.argv) > 2
                           else ["0", "2", "3", "4", "6", "8"])]

_cache = {}


def build(day):
    if day in _cache:
        return _cache[day]
    warm = LS.load_warm(LS._prev_session_dir(day))
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
        if len(tb) < 3:
            continue
        tape[s], d0[s] = bb + tb, len(bb)
    if not tape:
        _cache[day] = None
        return None
    avail, _, _ = FN.build(day, tape)
    pins = PL.nodip_watchlist(day)
    if pins:
        avail = {s: t for s, t in avail.items() if s in pins}
    fun = {s: tape[s] for s in avail if s in tape}
    if not fun:
        _cache[day] = None
        return None
    _cache[day] = (fun, {s: d0[s] for s in fun}, avail)
    return _cache[day]


def run(day, val):
    got = build(day)
    if not got:
        return None
    fun, fd0, avail = got
    ES.MIN_EXP = val
    ES._cache.clear()      # (sym, nbars) keyed - stale across knob changes
    closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
    t = closed + live
    return {"net": sum(x.get("net", 0) or 0 for x in t), "n": len(t),
            "wins": sum(1 for x in t if (x.get("net") or 0) > 0)}


rows, tot = {}, {v: 0.0 for v in VALS}
print(f"{'DAY':10}" + "".join(f"{('MIN_EXP ' + str(v)):>16}" for v in VALS))
for day in DAYS:
    LV._load(day)
    line, ok = f"{day:10}", False
    for v in VALS:
        r = run(day, v)
        if r is None:
            line += f"{'no data':>16}"
            continue
        ok = True
        tot[v] += r["net"]
        line += f"{r['net']:>10,.0f} ({r['n']:>2}){'':1}"
    rows[day] = ok
    print(line)
ES.MIN_EXP = 0.0
print("-" * (10 + 16 * len(VALS)))
print(f"{'TOTAL':10}" + "".join(f"{tot[v]:>16,.0f}" for v in VALS))
base = tot[VALS[0]]
print(f"{'vs OFF':10}" + "".join(f"{(tot[v]-base):>+16,.0f}" for v in VALS))
