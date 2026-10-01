"""
reality_check.py -- what was actually there, against what we actually took.

For every stock that reached the Board, Super Stocks or the Super Lab today, this
puts two things side by side:

  PERFECT      the best round trips the tape allowed, found with full hindsight.
               Multiple entries and exits per stock are allowed, because a stock
               that runs, pulls back and runs again offers two trades. This is
               NOT a target -- nobody can trade it. It is the ceiling, and the
               only honest way to size the gap.

  LIVE         what live_paper actually did, read from the ENTRY/EXIT lines the
               engine wrote to the app log as it ran.

The point is not to feel bad about the gap. It is to see WHERE the gap is:
  - stocks that ran and were never carded at all      -> a detection problem
  - stocks carded but never bought                    -> a capacity problem
  - stocks bought late or sold early                  -> a rules problem
Each has a different fix, and lumping them together is how the last two days
went in circles.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"
POSITION_RS = 1_66_667.0
MIN_MOVE = 0.8          # a round trip smaller than this is noise, not a trade
WIN_FROM, WIN_TO = "09:15:00", "10:30:00"


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def hm(s):
    return f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"


def load(day, until=None):
    until = until or WIN_TO
    bars = defaultdict(list)
    with (LOGS / f"bars30_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("sym") == "AAA":
                continue
            if WIN_FROM <= d["hhmm"] <= until:
                bars[d["sym"]].append((sec(d["hhmm"]), d["o"], d["h"], d["l"], d["c"]))
    for s in bars:
        bars[s].sort()

    sup = {}
    p = LOGS / f"super_{day}.jsonl"
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not (WIN_FROM <= d["ts"] <= until):
                    continue
                for r in d.get("rows") or []:
                    s = r.get("sym")
                    if s and s != "AAA" and s not in sup:
                        sup[s] = (sec(d["ts"]), r.get("urgency"), r.get("from_open"))

    board = {}
    p = LOGS / f"board_{day}.jsonl"
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                hms = str(d.get("ts"))[11:19]
                if not hms or hms < WIN_FROM:
                    continue
                if hms > until:
                    break
                for _pn, rows in (d.get("panels") or {}).items():
                    for r in (rows or []):
                        s = r.get("sym")
                        if s and s not in board:
                            board[s] = sec(hms)
    return dict(bars), sup, board


def live_trades(day, until=None):
    """Pair ENTRY with the next EXIT for the same symbol, from the app log."""
    until = until or WIN_TO
    ev = []
    with (HERE / "logs" / "movers_app.log").open(encoding="utf-8") as f:
        for line in f:
            m = re.search(r"\[(\d\d:\d\d:\d\d)\] live paper: (ENTRY|EXIT)\s+(\S+) @([\d.]+)", line)
            if not m:
                continue
            t = m.group(1)
            if not (WIN_FROM <= t <= until):
                continue
            ev.append((t, m.group(2), m.group(3), float(m.group(4))))
    open_pos, out = {}, defaultdict(list)
    for t, kind, sym, px in ev:
        if kind == "ENTRY":
            open_pos[sym] = (t, px)
        elif sym in open_pos:
            t0, p0 = open_pos.pop(sym)
            out[sym].append({"in": t0, "in_px": p0, "out": t, "out_px": px,
                             "pct": (px / p0 - 1) * 100})
    for sym, (t0, p0) in open_pos.items():          # still open
        out[sym].append({"in": t0, "in_px": p0, "out": None, "out_px": None, "pct": None})
    return dict(out)


def perfect(b, min_move=MIN_MOVE, max_trades=3):
    """Best non-overlapping round trips on the recorded tape, greedy on size.

    Uses the bar LOW to buy and a LATER bar HIGH to sell, which is the true
    ceiling and deliberately unreachable -- it assumes you bought the exact low.
    """
    trips = []
    n = len(b)
    for i in range(n):
        lo = b[i][3]
        if lo <= 0:
            continue
        best = None
        for j in range(i + 1, n):
            g = (b[j][2] / lo - 1) * 100
            if best is None or g > best[0]:
                best = (g, j)
        if best and best[0] >= min_move:
            trips.append({"gain": best[0], "i": i, "j": best[1],
                          "in_t": b[i][0], "in_px": lo,
                          "out_t": b[best[1]][0], "out_px": b[best[1]][2]})
    trips.sort(key=lambda x: -x["gain"])
    picked, used = [], []
    for t in trips:
        if any(not (t["j"] < a or t["i"] > bb) for a, bb in used):
            continue
        picked.append(t)
        used.append((t["i"], t["j"]))
        if len(picked) >= max_trades:
            break
    picked.sort(key=lambda x: x["in_t"])
    return picked
