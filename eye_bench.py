"""eye_bench.py -- the human-eye benchmark, computed mechanically.

Sri, 07-Sep: "sanity check every 10min from 9:15AM onwards, and auto refixing
human eye vs your code logic."

WHAT IT DOES. Over a window of the session it finds, with full hindsight, the
up-legs a person reading the chart would have taken, and allocates them to the
same book the code has: three slots, one position each. That is exactly the
by-hand exercise done on 04-Sep (LALITHAA 09:16, JINDWORLD 09:22, XTRANET 10:04
...) turned into code, so the comparison can run every ten minutes instead of
once a week.

IT IS A CEILING, NOT A TARGET. Every leg is chosen knowing how it ended. Nothing
live can reach it. Its value is the GAP: which legs the code missed, and whether
it missed them because it never signalled, because it had no slot, or because it
sold too early.
"""
import paper_engine as PE

# Sri, 07-Sep: "a human won't take a trade exactly at 9:15:00. He watches from
# 9:15:30 to see whether it is going up or down. Decision with action starts
# exactly from 9:16 onwards. And per human eye you should not consider the
# gap-up stocks."
#
# Both were inflating this benchmark. It was crediting the eye with legs that
# began on the opening tick -- UDAYJEW 09:15:00->09:16:00 +8.7%, NIACL
# 09:15:00->09:16:00 +3.7%, XTRANET from 09:15:00 -- prices nobody could have
# acted on, and it was scoring stocks that had already gapped, where the "move"
# is yesterday's news and the session is just chop.
EYE_FROM = "09:16:00"    # nothing before this counts, for anybody
GAP_MAX  = 5.0           # skip a stock that opened more than this above its
                         # previous close -- the eye does not chase a gap
MIN_LEG_PCT = 1.0        # ignore anything a person would not bother with
SLOTS = 3
CAPITAL, LEVERAGE = 100_000.0, 5.0


def _legs(bars, i0, i1, min_pct=MIN_LEG_PCT):
    """Non-overlapping up-legs inside [i0, i1): buy a low, sell a later high."""
    out, i = [], i0
    while i < i1:
        lo = bars[i]["l"] or bars[i]["c"]
        if not lo:
            i += 1; continue
        best = None
        for j in range(i + 1, i1):
            hi = bars[j]["h"] or bars[j]["c"]
            if not hi:
                continue
            g = (hi / lo - 1) * 100.0
            if best is None or g > best[0]:
                best = (g, j, hi)
            # stop extending once price has fallen well under the entry
            if (bars[j]["l"] or hi) < lo * 0.985 and best and best[0] >= min_pct:
                break
        if best and best[0] >= min_pct:
            out.append({"i_in": i, "i_out": best[1], "in": lo, "out": best[2],
                        "pct": best[0]})
            i = best[1] + 1
        else:
            i += 1
    return out


def bench(tape, upto="15:30:00", slots=SLOTS, min_pct=MIN_LEG_PCT,
          eye_from=None, gap_max=None):
    """tape: {sym: (bars, day_start)}. Returns (trades, net)."""
    eye_from = eye_from or EYE_FROM
    gap_max = GAP_MAX if gap_max is None else gap_max
    cand, skipped = [], []
    for sym, (bars, d0) in tape.items():
        # GAPPED? Needs the previous session, which the warm-up bars carry.
        if gap_max and d0:
            prev_close = bars[d0 - 1]["c"]
            op = bars[d0]["o"]
            if prev_close and op and (op / prev_close - 1) * 100.0 > gap_max:
                skipped.append(sym)
                continue
        idx = [k for k in range(d0, len(bars))
               if eye_from <= bars[k]["hhmm"] <= upto]
        if len(idx) < 4:
            continue
        for L in _legs(bars, idx[0], idx[-1] + 1, min_pct):
            cand.append({"sym": sym, "t_in": bars[L["i_in"]]["hhmm"],
                         "t_out": bars[L["i_out"]]["hhmm"], **L})
    cand.sort(key=lambda x: -x["pct"])
    slot_free = ["00:00:00"] * slots
    taken = []
    per = CAPITAL * LEVERAGE / slots
    for c in cand:
        for s in range(slots):
            if slot_free[s] <= c["t_in"]:
                q = int(per / c["in"])
                if q <= 0:
                    break
                bv, sv = q * c["in"], q * c["out"]
                ch = PE.charges(bv, sv)["total"]
                taken.append({**c, "slot": s, "qty": q, "net": sv - bv - ch})
                slot_free[s] = c["t_out"]
                break
    taken.sort(key=lambda x: x["t_in"])
    bench.skipped = skipped
    return taken, sum(t["net"] for t in taken)
