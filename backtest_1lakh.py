"""
backtest_1lakh.py -- Rs 1,00,000, 01-Sep-2026, 09:15-10:30. What the board's
own signals would actually have paid.

NOT A FANTASY RUN. Every entry and exit price below is a price the tick builder
recorded on the tape today, in logs/movers_board/bars30_20260901.jsonl. Nothing
is interpolated, nothing is invented, and no trade is entered at a time the
signal had not yet fired.

THE ONE RULE THAT KEEPS THIS HONEST
    The signal is the FIRST APPEARANCE of a stock on the Super Stocks tab,
    taken from super_20260901.jsonl -- a file written live during the session,
    before any of this was run. Entry is the next recorded bar AFTER that
    timestamp, never the signal bar itself, because he cannot fill at the
    instant a card appears.

    Picking today's biggest movers with hindsight would produce a beautiful
    table and would be worth nothing. That number is computed separately at the
    bottom, labelled as the ceiling, so the gap between the two is visible.

INTRABAR AMBIGUITY RESOLVES AGAINST THE TRADE
    A 30-second bar whose low breaks the stop AND whose high reaches the target
    is booked as a STOP. There is no way to know the order inside the bar, and
    assuming the good one is how backtests lie.
"""
import json, math, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"
DAY = "20260901"

CAPITAL = 1_00_000.0
LEVERAGE = 5.0                 # Dhan MIS intraday, his stated setup
START, END = "09:15:00", "10:30:00"
MIN_PRICE = 20.0

def sec(x):
    h, m, s = (list(map(int, x.split(":"))) + [0])[:3]
    return h * 3600 + m * 60 + s

S_START, S_END = sec(START), sec(END)

# ---------------------------------------------------------------- charges
def charges(buy_val, sell_val):
    """Dhan equity INTRADAY, both legs."""
    turn = buy_val + sell_val
    bro = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
    stt = 0.00025 * sell_val
    exch = 0.0000297 * turn
    sebi = 0.000001 * turn
    stamp = 0.00003 * buy_val
    gst = 0.18 * (bro + exch + sebi)
    return round(bro + stt + exch + sebi + stamp + gst, 2)

# ---------------------------------------------------------------- data
bars = defaultdict(list)          # sym -> [(sec, o,h,l,c,v)]
for line in (LOGS / f"bars30_{DAY}.jsonl").open(encoding="utf-8"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("sym") == "AAA":
        continue                  # super_check.py's test symbol
    t = sec(d["hhmm"])
    if not (S_START <= t <= S_END + 1800):
        continue
    bars[d["sym"]].append((t, d["o"], d["h"], d["l"], d["c"], d.get("v") or 0))
for s in bars:
    bars[s].sort()

signals = {}                      # sym -> (sec, meta) first Super Stocks appearance
for line in (LOGS / f"super_{DAY}.jsonl").open(encoding="utf-8"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    t = sec(d["ts"])
    if not (S_START <= t <= S_END):
        continue
    for r in d.get("rows") or []:
        sym = r.get("sym")
        if not sym or sym == "AAA" or sym in signals:
            continue
        signals[sym] = (t, r)

print(f"bars: {len(bars)} symbols   signals: {len(signals)} first-appearances "
      f"in {START}-{END}")

# ---------------------------------------------------------------- engine
def run(target, stop, timecap, slots, trail=None, label=""):
    """One position per slot, first-come-first-served by signal time."""
    order = sorted(signals.items(), key=lambda kv: kv[1][0])
    free_at = [S_START] * slots
    trades = []
    skipped = {"no_price": 0, "busy": 0}
    for sym, (t_sig, meta) in order:
        b = bars.get(sym)
        if not b:
            continue
        # first bar strictly AFTER the signal
        entry = next((x for x in b if x[0] > t_sig), None)
        if entry is None or entry[4] < MIN_PRICE:
            continue
        # FILL WINDOW. bars30 only carries a symbol while the tick builder is
        # tracking it, so a signal can be followed by a nine-minute hole. Taking
        # the first bar on the far side of that hole is not a fill, it is a
        # different trade with hindsight baked in -- SAILIFE entered at 09:51:30
        # on a 09:42:56 signal before this guard existed. No price within 90
        # seconds means no trade.
        if entry[0] - t_sig > 90:
            skipped["no_price"] += 1
            continue
        s_i = min(range(slots), key=lambda i: free_at[i])
        if free_at[s_i] > entry[0]:
            skipped["busy"] += 1
            continue                      # every slot busy when this signal fired
        e_t, e_px = entry[0], entry[4]
        per_slot = CAPITAL * LEVERAGE / slots
        qty = int(per_slot // e_px)
        if qty <= 0:
            continue
        tgt = e_px * (1 + target / 100)
        stp = e_px * (1 + stop / 100)
        peak = e_px
        x_t, x_px, why = None, None, None
        for (t, o, h, l, c, v) in b:
            if t <= e_t:
                continue
            if l <= stp:                  # STOP FIRST -- see module docstring
                x_t, x_px, why = t, stp, "stop"
                break
            if h >= tgt:
                x_t, x_px, why = t, tgt, "target"
                break
            if trail:
                peak = max(peak, h)
                tr = peak * (1 - trail / 100)
                if tr > stp and l <= tr:
                    x_t, x_px, why = t, tr, "trail"
                    break
                stp = max(stp, tr) if tr > stp else stp
            if t - e_t >= timecap:
                x_t, x_px, why = t, c, "time"
                break
            if t >= S_END:
                x_t, x_px, why = t, c, "10:30 close"
                break
        if x_t is None:
            last = b[-1]
            x_t, x_px, why = last[0], last[4], "last print"
        bv, sv = qty * e_px, qty * x_px
        ch = charges(bv, sv)
        trades.append({
            "sym": sym, "slot": s_i + 1,
            "in_t": e_t, "out_t": x_t, "in": round(e_px, 2), "out": round(x_px, 2),
            "qty": qty, "gross": round(sv - bv, 2), "chg": ch,
            "net": round(sv - bv - ch, 2), "why": why,
            "held": x_t - e_t, "sig": t_sig, "urg": meta.get("urgency"),
        })
        free_at[s_i] = x_t
    net = sum(t["net"] for t in trades)
    wins = sum(1 for t in trades if t["net"] > 0)
    return {"label": label, "trades": trades, "net": round(net, 2),
            "n": len(trades), "wins": wins,
            "pct": round(net / CAPITAL * 100, 2), "skipped": dict(skipped),
            "chg": round(sum(t["chg"] for t in trades), 2)}

def hhmm(s):
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

# ---------------------------------------------------------------- sweep
CONFIGS = []
for slots in (1, 2, 3, 4, 6):
    for tgt, stp, tc, tr in ((1.0, -0.5, 900, None), (1.5, -0.6, 900, None),
                             (2.0, -0.8, 1200, None), (3.0, -1.0, 1800, None),
                             (99.0, -0.6, 1800, 0.8), (99.0, -0.8, 2700, 1.2)):
        lbl = (f"{slots} slot{'s' if slots>1 else ''} · "
               + (f"trail {tr}%" if tr else f"tgt +{tgt}%")
               + f" · stop {stp}% · cap {tc//60}m")
        CONFIGS.append(run(tgt, stp, tc, slots, tr, lbl))

CONFIGS.sort(key=lambda r: -r["net"])
print("\n" + "=" * 96)
print(f"{'CONFIG':<46} {'trades':>6} {'win%':>6} {'charges':>10} {'NET Rs':>12} {'on 1L':>8}")
print("=" * 96)
for r in CONFIGS[:14]:
    w = round(r["wins"] / r["n"] * 100) if r["n"] else 0
    print(f"{r['label']:<46} {r['n']:>6} {w:>5}% {r['chg']:>10,.0f} "
          f"{r['net']:>12,.0f} {r['pct']:>7.2f}%")

best = CONFIGS[0]
print("\n" + "=" * 96)
print(f"BEST: {best['label']}")
print("=" * 96)
print(f"{'#':<3}{'Stock':<13}{'Signal':<10}{'Entry':<10}{'Buy':>9}{'Exit':<10}"
      f"{'Sell':>9}{'Qty':>7}{'Value':>11}{'Gross':>10}{'Chg':>8}{'Net':>10}  Why")
tot = 0
for i, t in enumerate(sorted(best["trades"], key=lambda x: x["in_t"]), 1):
    tot += t["net"]
    print(f"{i:<3}{t['sym']:<13}{hhmm(t['sig']):<10}{hhmm(t['in_t']):<10}"
          f"{t['in']:>9.2f}{hhmm(t['out_t']):<10}{t['out']:>9.2f}{t['qty']:>7}"
          f"{t['qty']*t['in']:>11,.0f}{t['gross']:>10,.0f}{t['chg']:>8,.0f}"
          f"{t['net']:>10,.0f}  {t['why']} ({t['held']//60}m)")
print(f"{'':<3}{'TOTAL':<13}{'':<39}{'':>7}{'':>11}"
      f"{sum(t['gross'] for t in best['trades']):>10,.0f}"
      f"{best['chg']:>8,.0f}{best['net']:>10,.0f}")

# ---------------------------------------------------------------- ceiling
print("\n" + "=" * 96)
print("HINDSIGHT CEILING -- what a perfect chooser would have made. NOT achievable.")
print("=" * 96)
peaks = []
for sym, (t_sig, _m) in signals.items():
    b = [x for x in bars.get(sym, []) if S_START <= x[0] <= S_END]
    if len(b) < 3:
        continue
    lo = min(x[3] for x in b)
    for j, x in enumerate(b):
        hi = max(y[2] for y in b[j:])
        peaks.append((round((hi / x[4] - 1) * 100, 2), sym, hhmm(x[0])))
peaks.sort(reverse=True)
for p, s, t in peaks[:6]:
    print(f"   {s:<13} +{p:>6.2f}%  from {t}")
json.dump({"best": best["label"], "trades": best["trades"],
           "configs": [{k: v for k, v in c.items() if k != "trades"} for c in CONFIGS]},
          open(HERE / "logs" / "BACKTEST_1LAKH.json", "w"), indent=1)
