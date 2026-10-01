"""
imb_backtest.py -- does Dhan's "Intraday Momentum Blast" screener actually blast?

THE SCREENER, read off ScanX on 28-Aug (scanx.trade/.../intraday-momentum-blast-imb-442186)

    NSE
    Price               above Open Price
    Price % Change      >= 1.00
    RSI (14)            >= 60
    MACD Histogram      >= 0
    Volume              1 Day Unusual Volume
    Price               above Supertrend
    Market Cap          >= Rs 50,000 Cr
    (the -411742 variant adds: F&O stocks only)

WHAT CAN AND CANNOT BE REPRODUCED HERE -- read this before believing any number

    REPRODUCED EXACTLY, from real Dhan 1-minute bars:
        price above the day's open
        day % change >= 1.00 (against the previous session's close)

    REPRODUCED AS A STATED APPROXIMATION:
        "1 Day Unusual Volume" -- the screener does not say what unusual means.
        Here it is: volume so far today, at the same point in the session, at
        least UNUSUAL_X times this stock's own average for the same window
        across the other sessions in the cache.

    SUBSTITUTED, AND NOT THE SAME THING:
        RSI(14), MACD histogram and Supertrend are computed on 1-MINUTE bars.
        On ScanX they are DAILY values -- HINDZINC showed a Supertrend of 557
        against a price of 622, a 10% gap that only a daily chart produces.
        So this tests the SPIRIT of the confirmation stack, not its letter, and
        the two are reported separately for exactly that reason.

    NOT REPRODUCED AT ALL:
        MARKET CAP >= Rs 50,000 Cr. It is in no file this project holds, and it
        is the screener's DECISIVE filter -- it is what reduces the whole market
        to nine names. A NIFTY-500 membership test plus a turnover floor is used
        as a stand-in. That is a weaker filter than the real one, so this
        backtest sees MORE stocks than the screener would, not fewer.

WHAT "BLASTING" IS TAKEN TO MEAN
    Judged the way every other signal in this project has been, so the answer is
    comparable rather than freestanding:
        did it gain 0.5% within 10 minutes of the screener first flagging it
        did it beat 0.107%, the measured round-trip cost on Rs 50,000
    plus the best it ever offered before 10:30, and how far it went against him
    first.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import super_fetch                                          # noqa: E402

WIN_START, WIN_END = 9 * 3600 + 15 * 60, 10 * 3600 + 30 * 60
DAY_PCT_MIN = 1.0
RSI_MIN = 60.0
UNUSUAL_X = 1.5
TURNOVER_FLOOR_CR = 50.0        # stand-in for "a big company"
BREAKEVEN, TARGET = 0.107, 0.5


def _rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _ema(v, n):
    k, out, e = 2 / (n + 1), [None] * len(v), None
    for i, x in enumerate(v):
        e = x if e is None else x * k + e * (1 - k)
        out[i] = e
    return out


def _macd_hist(closes, f=12, s=26, sig=9):
    ef, es = _ema(closes, f), _ema(closes, s)
    line = [a - b for a, b in zip(ef, es)]
    sg = _ema(line, sig)
    return [a - b for a, b in zip(line, sg)]


def _supertrend(highs, lows, closes, n=10, mult=3.0):
    """Standard Supertrend. Returns the band, and whether price is above it."""
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    atr, a = [None] * len(tr), None
    for i, x in enumerate(tr):
        a = x if a is None else (a * (n - 1) + x) / n
        atr[i] = a
    st, up = [None] * len(closes), [False] * len(closes)
    dirn, prev = 1, None
    for i in range(len(closes)):
        if atr[i] is None:
            continue
        mid = (highs[i] + lows[i]) / 2
        ub, lb = mid + mult * atr[i], mid - mult * atr[i]
        if prev is not None:
            ub = min(ub, prev[0]) if closes[i - 1] <= prev[0] else ub
            lb = max(lb, prev[1]) if closes[i - 1] >= prev[1] else lb
        if dirn == 1 and closes[i] < lb:
            dirn = -1
        elif dirn == -1 and closes[i] > ub:
            dirn = 1
        st[i] = lb if dirn == 1 else ub
        up[i] = closes[i] > st[i]
        prev = (ub, lb)
    return st, up


def load(days=5):
    cache = super_fetch.load()
    allday = sorted({d for r in cache.values() for d in r["sessions"]})
    return cache, allday[-days:], allday


def universe_big():
    """Stand-in for market cap: NIFTY-500 membership."""
    import csv
    p = HERE / "nifty500_cache.csv"
    out = set()
    if p.exists():
        with p.open(encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                s = (r.get("Symbol") or "").strip().upper()
                if s:
                    out.add(s)
    return out


def run(days=5, with_confirmations=True, log=print):
    cache, sessions, allday = load(days)
    big = universe_big()
    picks = {d: [] for d in sessions}
    for sid, rec in cache.items():
        sym = rec["sym"]
        if big and sym.upper() not in big:
            continue
        # this stock's own normal volume-by-this-point, from the OTHER sessions
        base = []
        for d, s in rec["sessions"].items():
            if d not in sessions:
                base.append(sum(b[5] for b in s["bars"]))
        norm = statistics.median(base) if base else None
        for day in sessions:
            s = rec["sessions"].get(day)
            if not s or len(s["bars"]) < 30:
                continue
            bars = s["bars"]
            op, prev = s["open"], s.get("prev_close") or 0
            if op <= 0 or prev <= 0:
                continue
            c = [b[4] for b in bars]
            h = [b[2] for b in bars]
            lo = [b[3] for b in bars]
            rsi = _rsi(c)
            hist = _macd_hist(c)
            _st, above = _supertrend(h, lo, c)
            cum = 0.0
            hit = None
            for i, b in enumerate(bars):
                cum += b[5]
                if c[i] <= op:
                    continue                       # price must be above open
                if (c[i] / prev - 1) * 100 < DAY_PCT_MIN:
                    continue                       # day change >= 1%
                if norm:
                    expected = norm * (i + 1) / max(len(bars), 1)
                    if expected > 0 and cum < UNUSUAL_X * expected:
                        continue                   # "1 day unusual volume"
                if cum * c[i] / 1e7 < TURNOVER_FLOOR_CR:
                    continue                       # stand-in for a big company
                if with_confirmations:
                    if rsi[i] is None or rsi[i] < RSI_MIN:
                        continue
                    if hist[i] is None or hist[i] < 0:
                        continue
                    if not above[i]:
                        continue
                hit = i
                break
            if hit is None:
                continue
            i = hit
            entry = c[i]
            fwd10 = [j for j in range(i + 1, len(bars)) if bars[j][0] <= bars[i][0] + 600]
            rest = range(i + 1, len(bars))
            best10 = max((h[j] for j in fwd10), default=entry)
            bestall = max((h[j] for j in rest), default=entry)
            worst = min((lo[j] for j in rest), default=entry)
            tpk = next((bars[j][0] - bars[i][0] for j in rest if h[j] >= bestall), 0)
            picks[day].append({
                "sym": sym, "t": bars[i][0], "px": entry,
                "day_pct": (entry / prev - 1) * 100,
                "from_open": (entry / op - 1) * 100,
                "g10": (best10 / entry - 1) * 100,
                "gmax": (bestall / entry - 1) * 100,
                "mae": (worst / entry - 1) * 100,
                "tpk": tpk / 60.0,
                "cr": cum * entry / 1e7,
            })
    return picks, sessions


def _hm(t):
    return f"{t//3600:02d}:{t%3600//60:02d}"


def report(log=print):
    for conf in (True, False):
        picks, sessions = run(with_confirmations=conf, log=log)
        lab = ("FULL screener (incl. RSI/MACD/Supertrend, on 1-min bars)"
               if conf else "WITHOUT the RSI/MACD/Supertrend confirmations")
        log("")
        log("=" * 78)
        log(f"  INTRADAY MOMENTUM BLAST -- {lab}")
        log("=" * 78)
        allp = []
        for day in sessions:
            rows = sorted(picks[day], key=lambda r: r["t"])
            allp += rows
            log("")
            log(f"  {day[6:8]}-{day[4:6]}   {len(rows)} stocks flagged")
            if not rows:
                log("     none")
                continue
            log(f"    {'time':<7}{'stock':<12}{'price':>9}{'day%':>7}{'+10min':>8}"
                f"{'best':>8}{'worst':>8}{'to peak':>9}")
            for r in rows[:12]:
                log(f"    {_hm(r['t']):<7}{r['sym']:<12}{r['px']:>9.1f}{r['day_pct']:>6.1f}%"
                    f"{r['g10']:>7.2f}%{r['gmax']:>7.2f}%{r['mae']:>7.2f}%{r['tpk']:>8.1f}m")
            if len(rows) > 12:
                log(f"    ... {len(rows)-12} more")
        if not allp:
            log("\n  nothing flagged in the whole week")
            continue
        n = len(allp)
        w = sum(1 for r in allp if r["g10"] >= TARGET)
        b = sum(1 for r in allp if r["g10"] >= BREAKEVEN)
        log("")
        log(f"  {'':<34}{'value'}")
        log("  " + "-" * 52)
        log(f"  {'stocks flagged in the week':<34}{n}  ({n/len(sessions):.1f} a day)")
        log(f"  {'gained 0.5% within 10 min':<34}{w/n*100:.0f}%")
        log(f"  {'beat the 0.107% round trip':<34}{b/n*100:.0f}%")
        log(f"  {'median gain in 10 min':<34}{statistics.median(r['g10'] for r in allp):+.2f}%")
        log(f"  {'median BEST offered before 10:30':<34}"
            f"{statistics.median(r['gmax'] for r in allp):+.2f}%")
        log(f"  {'median WORST before 10:30':<34}"
            f"{statistics.median(r['mae'] for r in allp):+.2f}%")
        log(f"  {'median time to that best':<34}"
            f"{statistics.median(r['tpk'] for r in allp):.1f} min")
        log(f"  {'median flag time':<34}"
            f"{_hm(int(statistics.median(r['t'] for r in allp)))}")


if __name__ == "__main__":
    report()
