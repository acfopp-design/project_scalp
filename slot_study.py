"""slot_study.py -- does taking ONE position at a time actually beat splitting?

Sri, 08-Sep 09:39, watching the live engine sit in BEL while the movers list ran
away: "Seriously your 1 trade at a time - does it work? I see 100's of good
stocks picked momentum. You got stuck with BEL."

The old answer -- 1 position Rs 29,698 vs 5 positions Rs 9,565 -- was measured
with an entry rule that had no day-move floor and no volume-expansion floor, so
it was comparing concentration on a signal that fired on sluggish large caps.
That proved nothing about concentration. This re-runs 1 / 3 / 5 with both
filters on, across three sessions, so the comparison is between slot counts and
nothing else.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import entry_lab as E
import funnel as FN
import paper_engine as PE
import combos2 as C2

CAPITAL, LEVERAGE = 100_000.0, 5.0
BOOK = CAPITAL * LEVERAGE
STOP_PCT, TRAIL_PCT = -1.0, 1.2
FROM, UPTO = "09:16:00", "15:15:00"
WARM_MIN = 55
MIN_DAY_PCT = 0.0
MIN_VOLX = 0.0


def strategy(bars):
    return C2.combined(bars, C2.pullback_alt)


def volx(bars, i, win=6, base=20):
    a = bars[max(0, i - win + 1):i + 1]
    b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
    if not a or not b:
        return 0.0
    ca = sum((x["v"] or 0) for x in a) / len(a)
    cb = sum((x["v"] or 0) for x in b) / len(b)
    return ca / cb if cb else 0.0


def day_pct(bars, i, d0):
    ref = bars[d0 - 1]["c"] if d0 > 0 and bars[d0 - 1].get("c") else None
    if not ref and d0 < len(bars):
        ref = bars[d0]["o"] or bars[d0]["c"]
    if not ref:
        return 0.0
    return (bars[i]["c"] / ref - 1) * 100


def replay(tape, d0, avail, slots, upto=UPTO, filters=True):
    dirs = {s: strategy(b) for s, b in tape.items()}
    idx = {s: {b["hhmm"]: i for i, b in enumerate(bars) if i >= d0.get(s, 0)}
           for s, bars in tape.items()}
    clock = sorted({bars[i]["hhmm"] for s, bars in tape.items()
                    for i in range(d0.get(s, 0), len(bars))
                    if FROM <= bars[i]["hhmm"] <= upto})
    per = BOOK / slots
    live, done = [], []
    for t in clock:
        still = []
        for p in live:
            bars = tape[p["sym"]]
            i = idx[p["sym"]].get(t)
            if i is None:
                still.append(p)
                continue
            lo, hi, c = bars[i]["l"], bars[i]["h"], bars[i]["c"]
            why = px = None
            if lo and lo <= p["in"] * (1 + STOP_PCT / 100):
                px, why = p["in"] * (1 + STOP_PCT / 100), "stop"
            elif t >= upto:
                px, why = c, "square-off"
            else:
                p["peak"] = max(p["peak"], hi or c)
                if (p["peak"] - c) / p["peak"] * 100 >= TRAIL_PCT and c > p["in"]:
                    px, why = c, "trail"
            if why:
                bv, sv = p["qty"] * p["in"], p["qty"] * px
                ch = PE.charges(bv, sv)["total"]
                done.append({**p, "out": px, "out_t": t, "why": why,
                             "net": sv - bv - ch})
            else:
                still.append(p)
        live = still
        if t >= upto or len(live) >= slots:
            continue
        held = {p["sym"] for p in live}
        cands = []
        for s, bars in tape.items():
            if s in held or t < avail.get(s, "99:99:99"):
                continue
            i = idx[s].get(t)
            if i is None or i < WARM_MIN or i + 1 >= len(bars):
                continue
            d = dirs[s]
            if not (d[i] == 1 and d[i - 1] != 1):
                continue
            v = volx(bars, i)
            if filters and (v < MIN_VOLX
                            or day_pct(bars, i, d0.get(s, 0)) < MIN_DAY_PCT):
                continue
            cands.append((v, s, i))
        cands.sort(key=lambda x: -x[0])
        for v, s, i in cands:
            if len(live) >= slots:
                break
            bars = tape[s]
            entry = bars[i + 1]["o"] or bars[i + 1]["c"]
            if not entry:
                continue
            qty = int(per / entry)
            if qty > 0:
                live.append({"sym": s, "in": entry, "in_t": bars[i + 1]["hhmm"],
                             "qty": qty, "peak": entry, "str": round(v, 1)})
    for p in live:
        bars = tape[p["sym"]]
        c = bars[-1]["c"]
        bv, sv = p["qty"] * p["in"], p["qty"] * c
        ch = PE.charges(bv, sv)["total"]
        done.append({**p, "out": c, "out_t": "open", "why": "still open",
                     "net": sv - bv - ch})
    return done


def build(day):
    tape_raw = E.load_tape_warm(day)
    tape, d0 = {}, {}
    for s, v in tape_raw.items():
        bars, n = v if isinstance(v, tuple) else (v, 0)
        if len(bars) - n < 10:
            continue
        tape[s], d0[s] = bars, n
    avail, sh, wl = FN.build(day, tape)
    return {s: tape[s] for s in avail}, {s: d0[s] for s in avail}, avail


def main():
    days = [a for a in sys.argv[1:] if a.isdigit()] or ["20260903", "20260904", "20260907"]
    upto = "10:30:00"
    print(f"Funnel universe | HOTT/LOTT + Pullback ALT | {FROM}-{upto} | "
          f"Rs {BOOK:,.0f} of buying power split N ways")
    print(f"Filters: day move >= {MIN_DAY_PCT}%, volume expansion >= {MIN_VOLX}x")
    print()
    for label, filt in (("PURE INDICATOR, super stocks + board + watchlist", False),):
        print("--- " + label + " " + "-" * max(2, 50 - len(label)))
        print(f"{'day':<12}{'universe':>10}{'1 slot':>13}{'3 slots':>13}{'5 slots':>13}   trades 1/3/5")
        tot = {1: 0.0, 3: 0.0, 5: 0.0}
        for day in days:
            try:
                tape, d0, avail = build(day)
            except Exception as e:
                print(f"{day:<12}  build failed: {type(e).__name__}: {e}")
                continue
            row, cnt = {}, {}
            for n in (1, 3, 5):
                done = replay(tape, d0, avail, n, upto, filt)
                row[n] = sum(t["net"] for t in done)
                cnt[n] = len(done)
                tot[n] += row[n]
            print(f"{day:<12}{len(avail):>10}{row[1]:>13,.0f}{row[3]:>13,.0f}"
                  f"{row[5]:>13,.0f}   {cnt[1]}/{cnt[3]}/{cnt[5]}")
        print(f"{'TOTAL':<12}{'':>10}{tot[1]:>13,.0f}{tot[3]:>13,.0f}{tot[5]:>13,.0f}")
        print()


if __name__ == "__main__":
    main()
