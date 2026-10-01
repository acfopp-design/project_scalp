"""
firstmin_lab.py -- Box 5: is Sri's "no trades 09:15-09:16" rule right?

His sheet: "Exactly at 9:15AM till 9:16AM - don't take any trades till you are
100% confident."

Two independent reasons to suspect he is right, both already on the record:
  * 03-Sep, live: 29 trades in the first 13 minutes, SEVEN of them at the
    identical second 09:16:11. The book filled completely before any position
    had proved anything. Exposure was capped correctly; there was no control
    on RATE.
  * 09:15-09:30 has the highest stop rate of any window measured, 35%.

But "the first minute is bad" has never actually been measured against the rest
of the session. This does that, per CARD, using the card's own first_seen time
and what the tape then offered.

For each card, from the moment it was FIRST SEEN:
    up   = best rise before it ever falls 1% below the entry price
    down = did it drop 1% first
That ordering matters: a card that offers +3% only after first dropping 1.2%
would have been stopped out, and counting its high as a win is the mistake
paper_engine's hindsight column makes.

READ-ONLY.
"""
import json, glob, sys
from collections import defaultdict

TARGET = 2.0
STOP   = -1.0
MINPX  = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--minpx=")), 100.0))


def bars_for(day):
    out = defaultdict(list)
    try:
        fh = open(f"logs/movers_board/bars30_{day}.jsonl", encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return {}
    with fh:
        for line in fh:
            try:
                b = json.loads(line)
            except Exception:
                continue
            if b.get("sym"):
                out[b["sym"]].append(b)
    for s in out:
        out[s].sort(key=lambda x: x.get("t") or 0)
    return out


def cards(day):
    """first sighting of each symbol, with the card as it then looked."""
    seen = {}
    try:
        fh = open(f"logs/movers_board/super_{day}.jsonl", encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return {}
    with fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            for r in snap.get("rows") or []:
                s = r.get("sym")
                if s and s != "AAA" and s not in seen:      # AAA is super_check's test row
                    seen[s] = dict(r, seen_at=r.get("first_seen") or snap.get("ts"))
    return seen


def outcome(bs, from_hms, entry):
    """Walk forward from the card. Returns (best_up_pct, stopped_first)."""
    best = 0.0
    for b in bs:
        if (b.get("hhmm") or "") < from_hms:
            continue
        lo, hi = b.get("l") or 0, b.get("h") or 0
        if lo and (lo - entry) / entry * 100 <= STOP:
            return best, True
        if hi:
            best = max(best, (hi - entry) / entry * 100)
    return best, False


def main():
    days = sorted(f.split("_")[-1][:8] for f in glob.glob("logs/movers_board/super_*.jsonl")
                  if "monitor" not in f)
    buckets = defaultdict(lambda: {"n": 0, "hit": 0, "stop": 0, "up": 0.0})
    for day in days:
        bs = bars_for(day)
        for sym, c in cards(day).items():
            t = c.get("seen_at") or ""
            px = c.get("price") or 0
            if not t or px < MINPX or sym not in bs:
                continue
            up, stopped = outcome(bs[sym], t, px)
            if t < "09:16:00":      k = "09:15-09:16"
            elif t < "09:17:00":    k = "09:16-09:17"
            elif t < "09:20:00":    k = "09:17-09:20"
            elif t < "09:30:00":    k = "09:20-09:30"
            elif t < "10:00:00":    k = "09:30-10:00"
            elif t < "10:30:00":    k = "10:00-10:30"
            else:                   k = "after 10:30"
            b = buckets[k]
            b["n"] += 1; b["up"] += up
            b["hit"] += (up >= TARGET and not stopped)
            b["stop"] += stopped
    order = ["09:15-09:16", "09:16-09:17", "09:17-09:20", "09:20-09:30",
             "09:30-10:00", "10:00-10:30", "after 10:30"]
    print(f"{len(days)} sessions, price >= Rs{MINPX:.0f}, from each card's first sighting")
    print(f"outcome walks FORWARD: stop at {STOP}% counts before any later high\n")
    print(f"{'window':<15}{'cards':>7}{'reached +2%':>13}{'stopped -1%':>13}{'avg best':>10}")
    tot = defaultdict(float)
    for k in order:
        b = buckets.get(k)
        if not b or not b["n"]:
            continue
        print(f"{k:<15}{b['n']:>7}{100*b['hit']/b['n']:>12.0f}%{100*b['stop']/b['n']:>12.0f}%"
              f"{b['up']/b['n']:>9.2f}%")
        if k != "09:15-09:16":
            tot["n"] += b["n"]; tot["hit"] += b["hit"]; tot["stop"] += b["stop"]; tot["up"] += b["up"]
    if tot["n"]:
        print(f"{'-- rest of day':<15}{int(tot['n']):>7}{100*tot['hit']/tot['n']:>12.0f}%"
              f"{100*tot['stop']/tot['n']:>12.0f}%{tot['up']/tot['n']:>9.2f}%")


if __name__ == "__main__":
    main()
