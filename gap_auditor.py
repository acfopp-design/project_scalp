"""
gap_auditor.py -- measure the gap between the badge and the real move, every
15 minutes, and tune towards zero.

THE OBJECTIVE
    "Aim for 0 gaps between board stocks vs Dhan real trading platform."

    Two gaps, measured per stock:
        START GAP  minutes between the move actually beginning and the badge
                   appearing.  Positive = the badge was late.
        END GAP    minutes between the move actually ending and the badge
                   coming off.  Positive = the badge outstayed the move.

    On 24-Aug the median start gap was +7.2 minutes and ten badges outstayed
    their move by more than ten minutes. Zero is the target. Zero is also not
    reachable -- a badge cannot appear before the move it describes, and no
    rule recognises a top in real time. Anything inside +/- 1 minute is
    indistinguishable from the sampling rate and is treated as zero here.

HOW THE AUTO-TUNING IS CONSTRAINED, AND WHY
    You asked it to correct itself and aim for perfection. It does, but under
    rules that exist because of what happened before: a badge rule was retuned
    four times in one morning on impressions, each change made it worse, and by
    the end it fired zero times. Chasing perfection every 15 minutes is exactly
    the process that produced that.

    So a parameter may move only when ALL of these hold:

      1. At least MIN_CASES stocks in the audit -- one stock is an anecdote.
      2. The SAME error, in the SAME direction, in TWO CONSECUTIVE audits.
         A single 15-minute window is mostly noise.
      3. One parameter per audit. Move two and you cannot tell which helped.
      4. Steps are small and bounded; every setting has a floor and a ceiling
         it cannot pass whatever the evidence says.
      5. A savepoint is taken before every change.
      6. If the next audit is WORSE, the change is reverted automatically.

    Rule 6 is what makes this safe to leave running. It cannot wander far,
    because it checks its own work and walks back.

WHAT IT WRITES
    logs/movers_board/gap_audit_YYYYMMDD.jsonl   every audit, machine-readable
    logs/movers_board/gap_report_YYYYMMDD.txt    the same thing, readable
    tuning.json                                  the live parameter overrides
    logs/movers_board/tuning_history.jsonl       every change and its evidence
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGDIR = ROOT / "logs" / "movers_board"
LOGDIR.mkdir(parents=True, exist_ok=True)
TUNING = ROOT / "tuning.json"
HISTORY = LOGDIR / "tuning_history.jsonl"

WINDOW_START, WINDOW_END = "09:15:00", "10:30:00"
EVERY_MIN = 15
MIN_CASES = 6              # fewer than this and nothing moves
ZERO_BAND = 1.0            # minutes; inside this counts as no gap
CONSECUTIVE = 2            # same error twice in a row before acting

# Every tunable, with the floor and ceiling it may never pass. The bounds are
# not decoration: they are what stops a bad morning of data turning the badge
# into something that never fires, or fires on everything.
BOUNDS = {
    "DIP_GRACE":        (0, 3),
    "DIP_PCT":          (0.004, 0.012),
    "PIN_MIN_BARS":     (3, 12),
    "IGNITE_PCT":       (0.004, 0.012),
    "IGNITE_WINDOW":    (60, 180),
    "STALE_NO_HIGH_SEC": (120, 600),
}
STEP = {
    "DIP_GRACE": 1, "DIP_PCT": 0.001, "PIN_MIN_BARS": 1,
    "IGNITE_PCT": 0.001, "IGNITE_WINDOW": 15, "STALE_NO_HIGH_SEC": 60,
}

_state = {"last_audit": None, "streak": {}, "pending": None, "audits": 0}


def _sec(t):
    a, b, c = t.split(":")
    return int(a) * 3600 + int(b) * 60 + int(c)


def _hm(t):
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"


def tuning():
    """Live overrides. Empty means the board's own defaults are in force."""
    try:
        return json.loads(TUNING.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ------------------------------------------------------------------ measure
def measure(day=None, upto=None, log=print):
    """Gaps for every badged stock so far today, from the board's own log."""
    import collections
    day = day or datetime.now().strftime("%Y%m%d")
    f = LOGDIR / f"board_{day}.jsonl"
    if not f.exists():
        return None
    S = _sec(WINDOW_START)
    E = _sec(upto) if upto else _sec(WINDOW_END)
    px = collections.defaultdict(dict)
    pin = collections.defaultdict(list)
    extra = collections.defaultdict(dict)
    with f.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            i = ln.find('"ts": "')
            if i < 0:
                continue
            ts = ln[i + 18:i + 26]
            if len(ts) != 8 or ts[2] != ":":
                continue
            t = _sec(ts)
            if t < S or t > E:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            for pan in (r.get("panels") or {}).values():
                for c in pan:
                    s = str(c.get("sym", "")).upper()
                    p = c.get("price")
                    if not (s and p):
                        continue
                    px[s].setdefault(t, float(p))
                    if c.get("pinned"):
                        if not pin[s] or pin[s][-1] != t:
                            pin[s].append(t)
                    if t not in extra[s]:
                        extra[s][t] = {"nodip": c.get("nodip"),
                                       "dipsUsed": c.get("dipsUsed"),
                                       "sinceHigh": c.get("sinceHigh"),
                                       "prov": c.get("pinProvisional"),
                                       "stale": c.get("stalePin")}
    cases = []
    for s, pins in pin.items():
        if not pins:
            continue
        v = sorted(px[s].items())
        if len(v) < 5:
            continue
        peak_t, peak_p = max(v, key=lambda x: x[1])
        before = [(t, p) for t, p in v if t <= peak_t]
        if not before:
            continue
        lo_t, lo_p = min(before, key=lambda x: x[1])
        run = (peak_p / lo_p - 1) * 100
        if run < 0.8:
            continue
        end_t = next((t for t, p in v if t > peak_t
                      and (peak_p - p) / peak_p * 100 >= 1.0), v[-1][0])
        e = extra[s].get(pins[0], {})
        cases.append({
            "sym": s, "board_start": pins[0], "board_end": pins[-1],
            "real_start": lo_t, "real_end": end_t, "run_pct": round(run, 2),
            "gap_start": round((pins[0] - lo_t) / 60, 1),
            "gap_end": round((pins[-1] - end_t) / 60, 1),
            "provisional": bool(e.get("prov")), "dips_used": e.get("dipsUsed"),
        })
    return cases


def summarise(cases):
    if not cases:
        return None
    gs = [c["gap_start"] for c in cases]
    ge = [c["gap_end"] for c in cases]
    return {
        "n": len(cases),
        "median_start": round(statistics.median(gs), 1),
        "median_end": round(statistics.median(ge), 1),
        "late_starts": sum(1 for x in gs if x > ZERO_BAND),
        "early_starts": sum(1 for x in gs if x < -ZERO_BAND),
        "overstays": sum(1 for x in ge if x > ZERO_BAND),
        "early_drops": sum(1 for x in ge if x < -ZERO_BAND),
        "on_time_start": sum(1 for x in gs if abs(x) <= ZERO_BAND),
        "on_time_end": sum(1 for x in ge if abs(x) <= ZERO_BAND),
        "provisional_used": sum(1 for c in cases if c["provisional"]),
        "perfect": sum(1 for c in cases
                       if abs(c["gap_start"]) <= ZERO_BAND and abs(c["gap_end"]) <= ZERO_BAND),
    }


# ------------------------------------------------------------------- decide
def decide(summary):
    """One proposed change, or None. Evidence-driven, bounded, single-step."""
    if not summary or summary["n"] < MIN_CASES:
        return None
    t = tuning()

    def cur(name, default):
        return t.get(name, default)

    # WORST FIRST. Late badges cost trades; stale badges cost trust. Both matter,
    # but a badge that arrives after the move is the failure being solved.
    if summary["median_start"] > ZERO_BAND:
        # Still late. Loosen the slow route before touching the fast one --
        # PIN_MIN_BARS is the documented floor on how early a badge can exist.
        if cur("PIN_MIN_BARS", 8) > BOUNDS["PIN_MIN_BARS"][0]:
            return ("PIN_MIN_BARS", cur("PIN_MIN_BARS", 8) - STEP["PIN_MIN_BARS"],
                    f"median start gap {summary['median_start']:+.1f} min -- badges still late")
        if cur("IGNITE_PCT", 0.006) > BOUNDS["IGNITE_PCT"][0]:
            return ("IGNITE_PCT", round(cur("IGNITE_PCT", 0.006) - STEP["IGNITE_PCT"], 4),
                    f"median start gap {summary['median_start']:+.1f} min -- ignition too strict")
    if summary["early_drops"] > summary["n"] * 0.25:
        if cur("DIP_GRACE", 1) < BOUNDS["DIP_GRACE"][1]:
            return ("DIP_GRACE", cur("DIP_GRACE", 1) + 1,
                    f"{summary['early_drops']} of {summary['n']} badges dropped while still running")
    if summary["overstays"] > summary["n"] * 0.25:
        if cur("STALE_NO_HIGH_SEC", 300) > BOUNDS["STALE_NO_HIGH_SEC"][0]:
            return ("STALE_NO_HIGH_SEC",
                    cur("STALE_NO_HIGH_SEC", 300) - STEP["STALE_NO_HIGH_SEC"],
                    f"{summary['overstays']} of {summary['n']} badges outstayed the move")
    if summary["early_starts"] > summary["n"] * 0.35:
        # Firing BEFORE the move is a false alarm, not earliness.
        if cur("IGNITE_PCT", 0.006) < BOUNDS["IGNITE_PCT"][1]:
            return ("IGNITE_PCT", round(cur("IGNITE_PCT", 0.006) + STEP["IGNITE_PCT"], 4),
                    f"{summary['early_starts']} of {summary['n']} badges fired before any real move")
    return None


def _score(s):
    """Lower is better. One number so 'did that change help?' has an answer."""
    if not s:
        return 9e9
    return abs(s["median_start"]) + abs(s["median_end"]) \
        + (s["overstays"] + s["early_drops"]) / max(s["n"], 1) * 5


def apply_change(name, value, why, log=print):
    lo, hi = BOUNDS[name]
    value = max(lo, min(hi, value))
    t = tuning()
    old = t.get(name)
    if old == value:
        return False
    try:
        import savepoint
        savepoint.create(f"auto-tune {name}: {old} -> {value} ({why})", log=lambda m: None)
    except Exception as e:
        log(f"gap_auditor: savepoint failed, NOT changing anything ({type(e).__name__})")
        return False          # no savepoint, no change. Non-negotiable.
    t[name] = value
    TUNING.write_text(json.dumps(t, indent=1), encoding="utf-8")
    rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "param": name,
           "from": old, "to": value, "why": why}
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    log(f"gap_auditor: TUNED {name} {old} -> {value}  ({why})")
    return True


def revert_last(log=print):
    try:
        lines = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return False
    if not lines:
        return False
    last = lines[-1]
    t = tuning()
    if last["from"] is None:
        t.pop(last["param"], None)
    else:
        t[last["param"]] = last["from"]
    TUNING.write_text(json.dumps(t, indent=1), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "param": last["param"], "from": last["to"],
                            "to": last["from"],
                            "why": "REVERTED -- the next audit was worse"},
                           separators=(",", ":")) + "\n")
    log(f"gap_auditor: REVERTED {last['param']} back to {last['from']} -- it made things worse")
    return True


# -------------------------------------------------------------------- audit
def audit(day=None, upto=None, log=print, autotune=True):
    day = day or datetime.now().strftime("%Y%m%d")
    cases = measure(day, upto, log)
    if cases is None:
        log("gap_auditor: no board log yet")
        return None
    s = summarise(cases)
    _state["audits"] += 1
    stamp = upto or datetime.now().strftime("%H:%M:%S")
    rec = {"ts": stamp, "day": day, "summary": s, "cases": cases,
           "tuning": tuning()}
    with (LOGDIR / f"gap_audit_{day}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    _report(rec, day)
    if not s:
        log(f"gap_auditor {stamp}: nothing badged yet")
        return rec

    log(f"gap_auditor {stamp}: {s['n']} badged | start {s['median_start']:+.1f}m "
        f"end {s['median_end']:+.1f}m | perfect {s['perfect']}/{s['n']} "
        f"| late {s['late_starts']} overstay {s['overstays']} earlydrop {s['early_drops']}")

    if not autotune:
        return rec

    # RULE 6 FIRST: did the previous change help? If not, walk it back before
    # doing anything else.
    prev = _state.get("last_summary")
    if _state.get("pending") and prev is not None:
        if _score(s) > _score(prev) + 0.5:
            revert_last(log)
            _state["pending"] = None
            _state["streak"] = {}
            _state["last_summary"] = s
            return rec
        _state["pending"] = None

    d = decide(s)
    if d:
        name, value, why = d
        # RULE 2: the same error twice in a row.
        k = f"{name}:{'+' if (value or 0) > (tuning().get(name) or 0) else '-'}"
        _state["streak"][k] = _state["streak"].get(k, 0) + 1
        for other in list(_state["streak"]):
            if other != k:
                _state["streak"][other] = 0
        if _state["streak"][k] >= CONSECUTIVE:
            if apply_change(name, value, why, log):
                _state["pending"] = name
                _state["streak"][k] = 0
        else:
            log(f"gap_auditor: would change {name} -> {value} ({why}); "
                f"waiting for a second consecutive audit before acting")
    else:
        log("gap_auditor: no change proposed -- gaps within tolerance or too few cases")
    _state["last_summary"] = s
    return rec


def _report(rec, day):
    s = rec["summary"]
    p = LOGDIR / f"gap_report_{day}.txt"
    with p.open("a", encoding="utf-8") as f:
        f.write("\n" + "=" * 78 + "\n")
        f.write(f"  GAP AUDIT  {day}  as at {rec['ts']}\n")
        f.write("=" * 78 + "\n")
        if not s:
            f.write("  nothing badged yet\n")
            return
        f.write(f"  badged stocks with a real up-leg : {s['n']}\n")
        f.write(f"  median START gap                 : {s['median_start']:+.1f} min\n")
        f.write(f"  median END gap                   : {s['median_end']:+.1f} min\n")
        f.write(f"  PERFECT (both within {ZERO_BAND:.0f} min)      : {s['perfect']} of {s['n']}\n")
        f.write(f"  late starts / overstays          : {s['late_starts']} / {s['overstays']}\n")
        f.write(f"  early drops / early starts       : {s['early_drops']} / {s['early_starts']}\n")
        f.write(f"  badges via fast ignition         : {s['provisional_used']}\n")
        f.write(f"  live tuning                      : {rec['tuning'] or '(board defaults)'}\n\n")
        f.write(f"  {'STOCK':<13}{'BOARD':<10}{'REAL':<10}{'gapS':>7}{'gapE':>7}{'run':>7}  prov\n")
        f.write("  " + "-" * 62 + "\n")
        for c in sorted(rec["cases"], key=lambda x: -abs(x["gap_start"]))[:25]:
            f.write(f"  {c['sym']:<13}{_hm(c['board_start']):<10}{_hm(c['real_start']):<10}"
                    f"{c['gap_start']:>+7.1f}{c['gap_end']:>+7.1f}{c['run_pct']:>6.1f}%"
                    f"   {'Y' if c['provisional'] else '-'}\n")


def loop(log=print):
    """Every 15 minutes from 09:15 to 10:30. Runs inside the board."""
    done = set()
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            key = (now.strftime("%Y%m%d"), hhmm)
            if (now.weekday() < 5 and WINDOW_START[:5] <= hhmm <= "10:45"
                    and now.minute % EVERY_MIN == 0 and key not in done):
                done.add(key)
                audit(log=log)
            time.sleep(20)
        except Exception as e:
            log(f"gap_auditor: {type(e).__name__} {str(e)[:110]}")
            time.sleep(60)


if __name__ == "__main__":
    import sys
    day = next((a for a in sys.argv[1:] if a.isdigit() and len(a) == 8), None)
    r = audit(day=day, autotune=False)
    if r and r["summary"]:
        s = r["summary"]
        print(f"\n  perfect {s['perfect']}/{s['n']}   "
              f"median start {s['median_start']:+.1f}m   median end {s['median_end']:+.1f}m")
        print(f"  full report -> logs/movers_board/gap_report_{r['day']}.txt")
