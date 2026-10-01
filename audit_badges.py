"""
audit_badges.py -- Board badge vs ACTUAL surge, for a session.

WHY THIS EXISTS
    Every badge rule so far looked right in isolation and was wrong on the tape:
    IFCI flagged for 3.5 minutes while flat, RUNNING flickered 63 times in 30
    minutes against surges of 5-26 minutes, EMSLIMITED inched to new highs while
    gaining 0.08% in five minutes. None of that was visible from the rule; all of
    it was obvious from the log.

    So the comparison is no longer done by hand. This regenerates the table from
    the board's own log and writes it next to the log, every run.

USAGE
    python audit_badges.py [YYYYMMDD]        (defaults to today)
"""
import json
import sys
import collections
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "movers_board"


def _s(t):
    a, b, c = t.split(":")
    return int(a) * 3600 + int(b) * 60 + int(c)


def _h(x):
    return f"{x // 3600:02d}:{(x % 3600) // 60:02d}:{x % 60:02d}"


def load(day):
    p = LOG / f"board_{day}.jsonl"
    path = collections.defaultdict(list)
    if not p.exists():
        return path
    with p.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts = (r.get("ts") or "")[11:19]
            if not ts or ts < "09:15:00":
                continue
            for pan in (r.get("panels") or {}).values():
                for c in pan:
                    path[str(c.get("sym", "")).upper()].append(
                        (_s(ts), c.get("price"), c.get("day_pct"),
                         bool(c.get("runner")), bool(c.get("pinned"))))
    for s in path:
        seen = set()
        path[s] = [x for x in path[s] if not (x[0] in seen or seen.add(x[0]))]
        path[s].sort()
    return path


def windows(v, idx):
    out, cur = [], None
    for x in v:
        if x[idx]:
            cur = [x[0], x[0]] if cur is None else [cur[0], x[0]]
        elif cur:
            out.append(tuple(cur))
            cur = None
    if cur:
        out.append(tuple(cur))
    return out


def surge(v, minmove=1.5):
    """Biggest continuous advance in the session: trough -> later peak."""
    best, lo = None, None
    for x in v:
        if x[1] is None:
            continue
        if lo is None or x[1] < lo[1]:
            lo = x
        if lo and lo[1] and (x[1] / lo[1] - 1) * 100 >= minmove:
            g = (x[1] / lo[1] - 1) * 100
            if best is None or g > best[2]:
                best = (lo[0], x[0], g)
    return best


def main(day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    path = load(day)
    if not path:
        print(f"no board log for {day}")
        return
    lines, stats = [], collections.Counter()
    # RUNNING was deleted on 19-Aug -- see the note in Movers_app.
    for label, idx in (("NO-DIP", 4),):
        for s, v in path.items():
            sg = surge(v)
            for a, b in windows(v, idx):
                stats[label + "_windows"] += 1
                stats[label + "_secs"] += (b - a)
                lines.append({
                    "badge": label, "sym": s,
                    "badge_start": _h(a), "badge_end": _h(b),
                    "badge_min": round((b - a) / 60, 1),
                    "surge_start": _h(sg[0]) if sg else None,
                    "surge_end": _h(sg[1]) if sg else None,
                    "surge_min": round((sg[1] - sg[0]) / 60, 1) if sg else None,
                    "surge_gain_pct": round(sg[2], 2) if sg else None,
                    "late_min": round((a - sg[0]) / 60, 1) if sg else None,
                    "stopped_early_min": round((sg[1] - b) / 60, 1) if sg else None,
                })
    # stocks that moved but never badged -- the misses
    misses = []
    for s, v in path.items():
        pxs = [x[1] for x in v if x[1]]
        if len(pxs) < 4:
            continue
        gain = (max(pxs) / min(pxs) - 1) * 100
        if gain >= 2.0 and not any(x[4] for x in v):
            misses.append({"sym": s, "intraday_pct": round(gain, 2),
                           "peak_day_pct": round(max((x[2] or 0) for x in v), 2),
                           "first_seen": _h(v[0][0]), "snapshots": len(v)})
    misses.sort(key=lambda r: -r["intraday_pct"])
    out = {"day": day, "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "stocks_on_board": len(path),
           "summary": {k: (round(v / 60) if k.endswith("_secs") else v)
                       for k, v in stats.items()},
           "windows": sorted(lines, key=lambda r: r["badge_start"]),
           "moved_but_never_badged": misses}
    p = LOG / f"badge_audit_{day}.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"{day}: {len(path)} stocks, {len(lines)} badge windows, "
          f"{len(misses)} moved>=2% with no badge -> {p.name}")
    for lab in ("NO-DIP",):
        print(f"  {lab:<9}{stats[lab+'_windows']:>4} windows, "
              f"{round(stats[lab+'_secs']/60)} badge-minutes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
