"""
latency_lab.py -- how late is the board?

THE QUESTION
    SSWL began moving at 09:57 and Super Stocks did not card it until 10:13:38
    -- sixteen minutes, and the whole 310 -> 328 run happened inside that gap.
    That is not a market limit, it is detection lag, and nobody had measured it.

WHAT IT MEASURES
    For every stock that actually ran in the morning window, this finds the
    moment the run STARTED on the tape and compares it to the moment the board
    first carded it. Run start is defined without hindsight bias in the usual
    trap-ish way: it is the last price low from which the stock went on to gain
    RUN_PCT within RUN_WINDOW minutes without first giving back GIVEBACK_PCT.
    That is a hindsight definition -- deliberately. The point is not to trade it
    but to measure how much of each real move the board slept through.

WHY THE LAG IS NOT FREE TO REMOVE
    Carding earlier means carding on less evidence, which means more false
    starts. So this also reports what the stock did AFTER the card, split by how
    late the card was -- if late cards are the profitable ones, the lag is doing
    useful work and should be left alone. If early cards are better, the lag is
    pure cost.
"""
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"

RUN_PCT = 3.0          # what counts as "it ran"
RUN_WINDOW = 20 * 60   # within this long
GIVEBACK_PCT = 1.0     # and without dipping this much first
WIN_FROM, WIN_TO = "09:15:00", "10:30:00"


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def hhmm(s):
    return f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"


def load(day):
    bars = defaultdict(list)
    with (LOGS / f"bars30_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("sym") == "AAA":
                continue
            t = sec(d["hhmm"])
            if sec(WIN_FROM) <= t <= sec(WIN_TO) + RUN_WINDOW:
                bars[d["sym"]].append((t, d["h"], d["l"], d["c"]))
    for s in bars:
        bars[s].sort()

    first_super = {}
    p = LOGS / f"super_{day}.jsonl"
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = sec(d["ts"])
                for r in d.get("rows") or []:
                    s = r.get("sym")
                    if s and s != "AAA" and s not in first_super:
                        first_super[s] = (t, r.get("urgency"), r.get("from_open"))

    first_board = {}
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
                if hms > WIN_TO:
                    break
                for _pn, rows in (d.get("panels") or {}).items():
                    for r in (rows or []):
                        s = r.get("sym")
                        if s and s not in first_board:
                            first_board[s] = sec(hms)
    return dict(bars), first_super, first_board


def run_start(b):
    """(t, price, peak_gain) of the first real run, or None."""
    for i, (t, h, l, c) in enumerate(b):
        if t > sec(WIN_TO):
            break
        base = l
        peak = base
        for (t2, h2, l2, c2) in b[i:]:
            if t2 - t > RUN_WINDOW:
                break
            peak = max(peak, h2)
            if l2 < base * (1 - GIVEBACK_PCT / 100) and peak < base * (1 + RUN_PCT / 100):
                break                       # it dipped before it ran
            if peak >= base * (1 + RUN_PCT / 100):
                return t, base, (peak / base - 1) * 100
    return None


def report(days, out=print):
    rows = []
    for day in days:
        bars, fs, fb = load(day)
        for sym, b in bars.items():
            if len(b) < 6:
                continue
            r = run_start(b)
            if not r:
                continue
            t0, px0, gain = r
            s_t = fs.get(sym, (None,))[0]
            b_t = fb.get(sym)
            # what was still left AFTER the card
            left = None
            if s_t:
                after = [x for x in b if x[0] >= s_t and x[0] - s_t <= RUN_WINDOW]
                if after:
                    entry = after[0][3]
                    left = (max(x[1] for x in after) / entry - 1) * 100
            rows.append({"day": day, "sym": sym, "t0": t0, "gain": gain,
                         "super": s_t, "board": b_t, "left": left,
                         "lag_s": (s_t - t0) if s_t else None,
                         "lag_b": (b_t - t0) if b_t else None})
    return rows
