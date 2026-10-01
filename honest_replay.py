"""honest_replay.py -- re-measure past days WITHOUT the look-ahead.

READ-ONLY. Writes nothing the live engine owns; prints a table and exits.

WHY
    Sri, 30-Sep: "how can you justify the qualification and ranking is
    correct - can you take the past history of previous days and comeup".

    Every published number in this project was produced with funnel.build()
    forcing each watchlist name's availability to 09:15:00, so the replay
    could enter a stock at a price from before it knew the stock existed.
    This script runs each day twice -- once as the engine saw it (CONTAMINATED)
    and once with availability set to the moment the Board actually first
    logged the name (HONEST) -- and prints both.

    The honest availability is not a guess: logs/movers_board/board_<day>.jsonl
    is a live append-only log, so the first timestamp carrying a symbol is a
    recorded fact about when we could first have known about it.

USAGE
    python honest_replay.py 20260925 20260928 20260929
"""
import sys, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_shadow as LS
import paper_live as PL
import funnel as FN
import eye_strategy as ES


def tape_for(day, upto="15:30:00"):
    warm = LS.load_warm(LS._prev_session_dir(day))
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= upto]
        if len(tb) < 3:
            continue
        tape[s] = bb + tb
        d0[s] = len(bb)
    return tape, d0


def run(day, honest, upto="15:30:00"):
    tape, d0 = tape_for(day, upto)
    if not tape:
        return None
    sh = FN.shockers(day)
    wl = FN.watchlist()
    av = {}
    for s, (hm, _src) in sh.items():
        if s in tape:
            av[s] = hm
    if not honest:
        # exactly what the engine did: the watchlist override
        for s in wl:
            if s in tape:
                av[s] = min(av.get(s, "99:99:99"), "09:15:00")
    else:
        # honest: a watchlist name is known only from when it was first logged
        for s in wl:
            if s in tape and s not in av:
                av[s] = "99:99:99"          # never seen on the board -> untradeable
    pins = set(PL.nodip_watchlist(day))
    if pins:
        av = {s: t for s, t in av.items() if s in pins}
    fun = {s: tape[s] for s in av if s in tape}
    fd0 = {s: d0[s] for s in fun}
    ES._cache.clear()
    closed, live = PL.run_book(fun, fd0, av, "09:15:00", upto)
    net = sum(c.get("net") or 0 for c in closed)
    wins = sum(1 for c in closed if (c.get("net") or 0) > 0)
    early = 0
    for c in closed:
        a = av.get(c["sym"])
        if a and c.get("in_t") and c["in_t"] < a:
            early += 1
    return {"trades": len(closed), "wins": wins, "net": net,
            "names": len(fun), "early": early}


if __name__ == "__main__":
    days = sys.argv[1:] or ["20260925", "20260928", "20260929"]
    print("%-10s %-14s %7s %6s %12s %s" % ("DAY", "MODE", "TRADES", "WINS", "NET", "ENTRIES BEFORE KNOWN"))
    print("-" * 78)
    for d in days:
        for honest in (False, True):
            try:
                r = run(d, honest)
            except Exception as e:
                print("%-10s %-14s  error: %s" % (d, "honest" if honest else "contaminated", str(e)[:40]))
                continue
            if not r:
                print("%-10s %-14s  no tape" % (d, "honest" if honest else "contaminated"))
                continue
            print("%-10s %-14s %7d %6d %12s %s"
                  % (d, "HONEST" if honest else "contaminated",
                     r["trades"], r["wins"], format(r["net"], "+,.0f"), r["early"]))
        print()
