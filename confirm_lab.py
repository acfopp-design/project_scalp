"""
confirm_lab.py -- Box 6.1: how much confirmation does an entry need?

Sri's sheet: "if bullish candle with volumes and price up - take trade".
Sri, 04-Sep: "I feel 2, but would you think we lose a lot in 30 seconds 2
candles, almost a minute - what if the stock only jumps in 30 seconds candle
then starts dipping drastically in next candle. I have seen incidents during
9:16 or 9:17 timeframes where stocks get peaked and then suddenly dip like hell."

He is describing a real trade-off: waiting for a second candle costs entry price,
not waiting costs accuracy. Box 5 showed why it matters -- cards first seen in
09:15-09:16 stop out at 52% against 27% for the rest of the day, but that same
minute held 28-Aug's two biggest winners. The first minute is high VARIANCE, not
simply bad, so the answer is confirmation rather than a time block.

WHAT IS COMPARED, from each card's first sighting at price P:
  none      enter at P immediately                        (what the engine does)
  c1close   wait for the current 30s candle to CLOSE up, enter at its close
  c2close   wait for a second consecutive up candle       (Sri's "I feel 2")
  part2     enter partway into candle 2 -- at the first bar whose price is
            still above candle 1's HIGH. Confirmation without paying a full
            extra candle. This is the middle path proposed on 04-Sep.
  vol       candle 1 up AND its volume above the symbol's own recent average

Outcome for each: walk FORWARD from the actual entry price, stop at -1% counts
before any later high, target +2%.

READ-ONLY.
"""
import json, glob, sys
from collections import defaultdict

TARGET, STOP = 2.0, -1.0
MINPX = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--minpx=")), 100.0))


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
                if s and s != "AAA" and s not in seen:
                    seen[s] = dict(r, seen_at=r.get("first_seen") or snap.get("ts"))
    return seen


def forward(bs, i0, entry):
    """From bar index i0 onward: best rise, and whether -1% came first."""
    best = 0.0
    for b in bs[i0:]:
        lo, hi = b.get("l") or 0, b.get("h") or 0
        if lo and (lo - entry) / entry * 100 <= STOP:
            return best, True
        if hi:
            best = max(best, (hi - entry) / entry * 100)
    return best, False


def variants(bs, idx, card_px):
    """Return {name: (entry_price, first_bar_index)} or None where no entry."""
    out = {"none": (card_px, idx)}
    n = len(bs)
    c1 = bs[idx] if idx < n else None
    if not c1:
        return out
    up1 = (c1.get("c") or 0) > (c1.get("o") or 0)
    if up1 and idx + 1 < n:
        out["c1close"] = (c1["c"], idx + 1)
        c2 = bs[idx + 1]
        if (c2.get("c") or 0) > (c2.get("o") or 0) and idx + 2 < n:
            out["c2close"] = (c2["c"], idx + 2)
        # partway into candle 2: first later bar still trading above candle 1's high
        h1 = c1.get("h") or 0
        for j in range(idx + 1, min(idx + 3, n)):
            if (bs[j].get("h") or 0) > h1:
                out["part2"] = (h1, j)
                break
        # volume confirmation on candle 1
        prior = [b.get("v") or 0 for b in bs[max(0, idx - 20):idx]]
        avg = (sum(prior) / len(prior)) if prior else 0
        if avg and (c1.get("v") or 0) > avg * 1.5 and idx + 1 < n:
            out["vol"] = (c1["c"], idx + 1)
    return out


def main():
    days = sorted(f.split("_")[-1][:8] for f in glob.glob("logs/movers_board/super_*.jsonl")
                  if "monitor" not in f)
    agg = defaultdict(lambda: {"n": 0, "hit": 0, "stop": 0, "up": 0.0, "slip": 0.0})
    early = defaultdict(lambda: {"n": 0, "hit": 0, "stop": 0})
    for day in days:
        bs_all = bars_for(day)
        for sym, c in cards(day).items():
            t, px = c.get("seen_at") or "", c.get("price") or 0
            if not t or px < MINPX or sym not in bs_all:
                continue
            bs = bs_all[sym]
            idx = next((i for i, b in enumerate(bs) if (b.get("hhmm") or "") >= t), None)
            if idx is None:
                continue
            for name, (entry, j) in variants(bs, idx, px).items():
                up, stopped = forward(bs, j, entry)
                a = agg[name]
                a["n"] += 1; a["up"] += up; a["slip"] += (entry - px) / px * 100
                a["hit"] += (up >= TARGET and not stopped); a["stop"] += stopped
                if t < "09:17:00":
                    e = early[name]
                    e["n"] += 1; e["hit"] += (up >= TARGET and not stopped); e["stop"] += stopped

    print(f"{len(days)} sessions, price >= Rs{MINPX:.0f}, forward-only from each entry\n")
    print(f"{'variant':<10}{'taken':>7}{'reached +2%':>13}{'stopped':>9}{'avg best':>10}{'entry slip':>12}")
    for k in ["none", "c1close", "part2", "c2close", "vol"]:
        a = agg.get(k)
        if not a or not a["n"]:
            continue
        print(f"{k:<10}{a['n']:>7}{100*a['hit']/a['n']:>12.0f}%{100*a['stop']/a['n']:>8.0f}%"
              f"{a['up']/a['n']:>9.2f}%{a['slip']/a['n']:>11.2f}%")
    print(f"\nSAME, but only cards first seen before 09:17 (Sri's danger window):")
    print(f"{'variant':<10}{'taken':>7}{'reached +2%':>13}{'stopped':>9}")
    for k in ["none", "c1close", "part2", "c2close", "vol"]:
        e = early.get(k)
        if not e or not e["n"]:
            continue
        print(f"{k:<10}{e['n']:>7}{100*e['hit']/e['n']:>12.0f}%{100*e['stop']/e['n']:>8.0f}%")


if __name__ == "__main__":
    main()
