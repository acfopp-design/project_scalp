"""
dip_lab.py -- Box 6.2 / 6.3, measured from the TAPE not from cards.

The first attempt bucketed carded rows and found nothing: 204 of 222 cards sat
in a single off_peak bucket and 205 in a single since_high bucket, because the
freshness gates shipped 03-Sep (ENTRY_MAX_OFF_PEAK 0.40, ENTRY_MAX_SINCE_HIGH
90) already remove everything else. Measuring a gate's value on data the gate
has already filtered is the card_lab mistake -- the answer is guaranteed before
you start.

So this recomputes the same quantities from bars30 directly, at moments the
board WOULD consider, and keeps the full range.

CANDIDATE MOMENT: a 30-second bar where the stock is >= MIN_FROM_OPEN above its
own open and priced >= MIN_PRICE -- the board's own admission shape. To avoid
counting the same run hundreds of times, at most one moment per symbol per
5-minute block.

AT EACH MOMENT, from the tape alone:
  off_peak     % below the session high so far        (Sri: "profit booking from
                                                       highs" vs a real reversal)
  since_high   seconds since that high was printed
  dips         times it has given back >0.5% from a running high and resumed
                                                      (Sri's "chop")
  vol_x        this bar's volume / its own trailing 20-bar average
                                                      (Sri 04-Sep Q5: unusual
                                                       against the stock's OWN
                                                       recent average)
  rise_90s     the 3-bar move into this moment

OUTCOME: walk forward -- best rise before -1% is touched. Uses bar LOWS for the
stop and HIGHS for the rise, which is exactly what replay_live fails to do.

READ-ONLY.
"""
import json, glob, sys
from collections import defaultdict

TARGET, STOP = 2.0, -1.0
MIN_FROM_OPEN = 2.0
MIN_PRICE = 100.0
BLOCK = 300          # one candidate moment per symbol per 5 minutes


def sessions():
    return sorted(f.split("_")[-1][:8] for f in glob.glob("logs/movers_board/bars30_*.jsonl"))


def load(day):
    out = defaultdict(list)
    with open(f"logs/movers_board/bars30_{day}.jsonl", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                b = json.loads(line)
            except Exception:
                continue
            if b.get("sym") and b.get("sym") != "AAA" and (b.get("hhmm") or "") >= "09:15:00":
                out[b["sym"]].append(b)
    for s in out:
        out[s].sort(key=lambda x: x.get("t") or 0)
    return out


def forward(bs, i0, entry):
    best = 0.0
    for b in bs[i0:]:
        lo, hi = b.get("l") or 0, b.get("h") or 0
        if lo and (lo - entry) / entry * 100 <= STOP:
            return best, True
        if hi:
            best = max(best, (hi - entry) / entry * 100)
    return best, False


def moments(bs):
    """Yield dicts describing each candidate moment, computed causally."""
    if len(bs) < 25:
        return
    op = bs[0].get("o") or bs[0].get("c")
    if not op:
        return
    hi_so_far, hi_t = op, bs[0].get("t") or 0
    dips, in_dip = 0, False
    last_block = None
    for i, b in enumerate(bs):
        c = b.get("c") or 0
        h = b.get("h") or 0
        t = b.get("t") or 0
        if h > hi_so_far:
            hi_so_far, hi_t = h, t
            if in_dip:
                dips += 1
                in_dip = False
        if hi_so_far and (hi_so_far - c) / hi_so_far * 100 > 0.5:
            in_dip = True
        if i < 20:
            continue
        if c < MIN_PRICE:
            continue
        if (c - op) / op * 100 < MIN_FROM_OPEN:
            continue
        blk = t // BLOCK
        if blk == last_block:
            continue
        last_block = blk
        prior = [x.get("v") or 0 for x in bs[i - 20:i]]
        avg = (sum(prior) / len(prior)) if prior else 0
        c3 = bs[i - 3].get("c") or 0
        yield {
            "i": i, "px": c,
            "off_peak": (hi_so_far - c) / hi_so_far * 100 if hi_so_far else None,
            "since_high": t - hi_t,
            "dips": dips,
            "vol_x": ((b.get("v") or 0) / avg) if avg else None,
            "rise_90s": ((c - c3) / c3 * 100) if c3 else None,
        }


def bucket(rows, field, edges, label):
    agg = defaultdict(lambda: {"n": 0, "win": 0, "stop": 0, "up": 0.0})
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        for lo, hi, name in edges:
            if lo <= v < hi:
                a = agg[name]
                a["n"] += 1; a["win"] += r["win"]; a["stop"] += r["stopped"]; a["up"] += r["up"]
                break
    print(f"\n  {label}")
    print(f"    {'bucket':<18}{'n':>6}{'reached +2%':>13}{'stopped':>9}{'avg best':>10}")
    for _, _, name in edges:
        a = agg.get(name)
        if not a or a["n"] < 10:
            continue
        print(f"    {name:<18}{a['n']:>6}{100*a['win']/a['n']:>12.0f}%"
              f"{100*a['stop']/a['n']:>8.0f}%{a['up']/a['n']:>9.2f}%")


def main():
    days = sessions()
    rows = []
    for day in days:
        for sym, bs in load(day).items():
            for m in moments(bs):
                up, st = forward(bs, m["i"], m["px"])
                rows.append({**m, "up": up, "stopped": st, "win": (up >= TARGET and not st)})
    print(f"{len(days)} sessions, {len(rows)} candidate moments "
          f"(>= +{MIN_FROM_OPEN}% from open, >= Rs{MIN_PRICE:.0f}, one per symbol per 5 min)")
    base_win = 100 * sum(r["win"] for r in rows) / max(len(rows), 1)
    base_stop = 100 * sum(r["stopped"] for r in rows) / max(len(rows), 1)
    print(f"BASE: reached +2% {base_win:.0f}%   stopped {base_stop:.0f}%")

    bucket(rows, "off_peak", [(0, 0.2, "0-0.2 at high"), (0.2, 0.4, "0.2-0.4"),
                              (0.4, 0.8, "0.4-0.8"), (0.8, 1.5, "0.8-1.5"),
                              (1.5, 3.0, "1.5-3.0"), (3.0, 99, "3%+ off high")],
           "OFF_PEAK -- how far below its own session high  (shipped gate: <= 0.40)")
    bucket(rows, "since_high", [(0, 30, "0-30s"), (30, 90, "30-90s"), (90, 180, "90-180s"),
                                (180, 600, "3-10 min"), (600, 99999, "10 min+")],
           "SINCE_HIGH -- seconds since the last new high  (shipped gate: <= 90)")
    bucket(rows, "dips", [(0, 1, "0"), (1, 2, "1"), (2, 3, "2"), (3, 5, "3-4"), (5, 99, "5+")],
           "DIPS -- times it gave back >0.5% and resumed  (shipped gate: <= 2)")
    bucket(rows, "vol_x", [(0, 0.5, "<0.5x"), (0.5, 1.0, "0.5-1x"), (1.0, 1.5, "1-1.5x"),
                           (1.5, 3.0, "1.5-3x"), (3.0, 6.0, "3-6x"), (6.0, 999, "6x+")],
           "VOL_X -- this bar vs the stock's OWN 20-bar average  (Sri 04-Sep Q5)")
    bucket(rows, "rise_90s", [(-99, 0, "falling"), (0, 1, "0-1%"), (1, 2, "1-2%"),
                              (2, 4, "2-4%"), (4, 99, "4%+")],
           "RISE_90s -- the move into this moment")


if __name__ == "__main__":
    main()
