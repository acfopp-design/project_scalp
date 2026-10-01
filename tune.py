"""
tune.py -- learn from the last ten minutes and apply the lesson, now.

Sri: "not just report -- tune so you learn mistakes and reapply immediately,
every 10 minutes."

WHY THIS IS DANGEROUS AND HOW IT IS CONTAINED
    An auto-tuner reacting to ten minutes of trades is the single easiest way
    to destroy this system. It already happened by hand: on 01-Sep the
    displacement margin was changed twice in one afternoon in OPPOSITE
    directions, each time off one observation. Automating that judgement makes
    it faster, not better.

    So every guard below exists because of a specific failure:

    1. ONE CHANGE PER CYCLE. Two changes at once cannot be told apart
       afterwards, and then neither can be trusted or undone.
    2. MINIMUM SAMPLE. A bucket under MIN_N is an anecdote. Three times last
       week a convincing per-trade number failed full-engine replay.
    3. BOUNDED RANGES AND SMALL STEPS. Every knob has a floor and a ceiling it
       cannot leave, and moves one step at a time. Runaway tuning is what turns
       a bad morning into an unrecoverable one.
    4. IT MEASURES ITS OWN LAST MOVE AND UNDOES IT. learn.py cuts P&L at each
       tune, so the segment since the last change is that change's own report
       card. If it did worse than the segment before it, it is REVERTED before
       anything else is considered. This is the part that makes it learning
       rather than drifting.
    5. COOL-OFF. After a revert it changes nothing next cycle. Otherwise it
       oscillates between two settings for the rest of the session.

WHY IT CAN APPLY WITHOUT A RESTART
    This runs on Windows beside the board, so it can call /admin/reload on
    localhost -- which Claude's own shell cannot reach. Constants are hot-applied
    to the running process and OPEN POSITIONS ARE NOT DISTURBED.
"""
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
BOARD = LOGS / "movers_board"
CFG = HERE / "live_config.json"
TUNES = LOGS / "TUNES.jsonl"
IST = timezone(timedelta(hours=5, minutes=30))

MIN_N = 8                 # trades in a segment before it may be judged
MIN_N_REVERT = 6          # ...slightly lower to undo a bad change sooner
RELOAD = "http://127.0.0.1:5005/admin/reload"

# knob -> (floor, ceiling, step). Nothing may leave these.
BOUNDS = {
    "ENTRY_MIN_URGENCY": (12.0, 26.0, 1.5),
    "REENTRY_COOLDOWN": (120.0, 900.0, 120.0),
    "MAX_TOTAL_RISK_PCT": (3.0, 8.0, 1.0),
}


def now():
    return datetime.now(IST)


def hms():
    return now().strftime("%H:%M:%S")


def load_tunes(day):
    if not TUNES.exists():
        return []
    out = []
    for line in TUNES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t.get("day") == day:
            out.append(t)
    return out


def segments(closed, tunes):
    """[(from, to, label, trades)] cut at each tune."""
    marks = [("09:15:00", "session open")] + [(t["at"], t["change"]) for t in tunes]
    out = []
    for i, (t0, label) in enumerate(marks):
        t1 = marks[i + 1][0] if i + 1 < len(marks) else "23:59:59"
        seg = [x for x in closed if t0 <= (x.get("in_hms") or "") < t1]
        out.append((t0, t1, label, seg))
    return out


def per_trade(seg):
    return (sum((x.get("net") or 0) for x in seg) / len(seg)) if seg else None


DRY = False            # set by --dry; testing must never mutate live config


def apply_change(key, value, why, day, extra=None):
    if DRY:
        return True, f"DRY: would set {key} = {value}"
    try:
        cfg = json.loads(CFG.read_text(encoding="utf-8"))
    except Exception:
        return False, "cannot read live_config.json"
    lp = cfg.setdefault("live_paper", {})
    before = lp.get(key)
    lp[key] = value
    try:
        CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        return False, f"cannot write config: {e}"

    ok = "written (reload not confirmed)"
    try:
        with urllib.request.urlopen(RELOAD, timeout=10) as r:
            ok = "hot-applied" if r.status == 200 else f"reload HTTP {r.status}"
    except Exception as e:
        ok = f"written, reload failed: {type(e).__name__}"

    rec = {"at": hms(), "day": day, "change": f"{key} {before} -> {value}",
           "why": why, "applied": ok, "auto": True}
    if extra:
        rec.update(extra)
    try:
        with TUNES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return True, f"{key} {before} -> {value} ({ok})"


def run(state, log=print):
    """One tuning decision. Returns a one-line verdict."""
    day = now().strftime("%Y%m%d")
    t = now().strftime("%H:%M:%S")
    if not ("09:25:00" <= t <= "15:05:00"):
        return "tune: outside the tuning window"

    closed = list(state.get("closed") or [])
    if len(closed) < MIN_N:
        return f"tune: only {len(closed)} closed trades -- too few to judge"

    tunes = load_tunes(day)
    segs = segments(closed, tunes)

    # ---- 1. did my LAST change make things worse? undo it first -----------
    auto = [x for x in tunes if x.get("auto")]
    if auto and len(segs) >= 2:
        cur, prev = segs[-1], segs[-2]
        if len(cur[3]) >= MIN_N_REVERT and len(prev[3]) >= MIN_N_REVERT:
            a, b = per_trade(cur[3]), per_trade(prev[3])
            last = auto[-1]
            if a is not None and b is not None and a < b and not last.get("reverted"):
                key = last["change"].split()[0]
                old = last["change"].split("->")[0].split()[-1]
                if key in BOUNDS and old not in ("None", ""):
                    try:
                        val = float(old)
                    except ValueError:
                        val = None
                    if val is not None:
                        okc, msg = apply_change(
                            key, val,
                            f"REVERT: since that change Rs {a:,.0f}/trade vs "
                            f"Rs {b:,.0f}/trade before it",
                            day, {"revert_of": last["change"], "reverted": True})
                        return f"tune: REVERTED -- {msg}"

    # a revert last cycle means sit still this cycle
    if auto and auto[-1].get("reverted"):
        recent = auto[-1]["at"]
        if (datetime.strptime(t, "%H:%M:%S") -
                datetime.strptime(recent, "%H:%M:%S")).total_seconds() < 900:
            return "tune: cooling off after a revert -- no change"

    # ---- 2. otherwise, act on the clearest live signal --------------------
    try:
        cfg = json.loads(CFG.read_text(encoding="utf-8"))["live_paper"]
    except Exception:
        return "tune: cannot read config"

    # (a) URGENCY. Split this segment's trades at the current floor + 2 and
    #     see whether the weaker half is paying for itself.
    # A change must be judged ONLY on trades taken under it. Falling back to
    # the whole day here caused a ratchet: with no trades yet in the new
    # segment it re-read the same old losing trades every cycle and raised the
    # floor 18 -> 19.5 -> 21 -> 22.5 in three passes, each time "confirming"
    # itself on evidence that predated the previous raise. A tuner that learns
    # from trades its own change already excluded is not learning, it is
    # counting the same mistake repeatedly.
    seg = segs[-1][3]
    if len(seg) < MIN_N:
        return (f"tune: only {len(seg)} closed trades since the last change "
                f"-- waiting for evidence under the CURRENT setting")
    cur_u = float(cfg.get("ENTRY_MIN_URGENCY", 12.0))
    lo = [x for x in seg if (x.get("urgency") or 0) < cur_u + 2]
    if len(lo) >= MIN_N:
        avg = per_trade(lo)
        if avg is not None and avg < 0:
            lo_b, hi_b, step = BOUNDS["ENTRY_MIN_URGENCY"]
            new = min(hi_b, round(cur_u + step, 1))
            if new > cur_u:
                okc, msg = apply_change(
                    "ENTRY_MIN_URGENCY", new,
                    f"urgency under {cur_u+2:.1f} lost Rs {avg:,.0f}/trade "
                    f"over {len(lo)} trades this segment", day)
                return f"tune: {msg}"

    # (b) REPEAT ENTRIES. Same symbol bought again and losing -> wait longer.
    seen, repeats = set(), []
    for x in sorted(seg, key=lambda r: r.get("in_hms") or ""):
        s = x.get("sym")
        if s in seen:
            repeats.append(x)
        seen.add(s)
    if len(repeats) >= MIN_N:
        avg = per_trade(repeats)
        if avg is not None and avg < 0:
            cur_c = float(cfg.get("REENTRY_COOLDOWN", 180.0))
            lo_b, hi_b, step = BOUNDS["REENTRY_COOLDOWN"]
            new = min(hi_b, cur_c + step)
            if new > cur_c:
                okc, msg = apply_change(
                    "REENTRY_COOLDOWN", new,
                    f"second entries lost Rs {avg:,.0f}/trade over "
                    f"{len(repeats)} of them", day)
                return f"tune: {msg}"

    # (c) DRAWDOWN BRAKE. Deep in the red -> risk less per trade, do not
    #     "trade out of it". That instinct is what turns a bad day into a
    #     terrible one, and it is the one a machine should be best at resisting.
    cap = float(state.get("capital") or 1)
    net = sum((x.get("net") or 0) for x in closed)
    if net / cap * 100 <= -8.0:
        cur_r = float(cfg.get("MAX_TOTAL_RISK_PCT", 8.0))
        lo_b, hi_b, step = BOUNDS["MAX_TOTAL_RISK_PCT"]
        new = max(lo_b, cur_r - step)
        if new < cur_r:
            okc, msg = apply_change(
                "MAX_TOTAL_RISK_PCT", new,
                f"down {net/cap*100:.1f}% of capital -- reducing size, "
                f"not chasing it back", day)
            return f"tune: {msg}"

    return "tune: nothing clear enough to change"
