"""indi_lab.py -- test ONE indicator, on its own, against the human eye.

Sri, 07-Sep: "keep your entire logic aside." So nothing here imports the entry
rules from signal_sim. Each strategy below is the published indicator and
nothing else. The book, the charges and the clock are identical for all of
them, so the only thing that differs is the signal.

RULES OF THE TEST
  capital Rs 1,00,000 at 5x = Rs 5,00,000, three slots, Rs 1,66,666 each
  entries from 09:16:00 only -- nobody acts on the opening tick
  a stock that gapped more than GAP_MAX above its previous close is excluded
  exits are the SAME for every strategy: -1% stop, or 1.2% back off the peak
  everything is squared off at the cut-off
  charges are Dhan's real intraday round trip on both legs
"""
import sys
from datetime import datetime
import paper_engine as PE
import live_shadow as L

CAPITAL, LEVERAGE, SLOTS = 100_000.0, 5.0, 3
PER_SLOT = CAPITAL * LEVERAGE / SLOTS
FROM, GAP_MAX = "09:16:00", 5.0
STOP_PCT, TRAIL_PCT = -1.0, 1.2


def load(day, upto):
    warm = L.load_warm(L._prev_session_dir(day))
    today = L.load_today(day)
    today.update(L.load_live(day))
    out = {}
    for s, bars in today.items():
        b = [x for x in bars if x["hhmm"] <= upto and x["c"]]
        if len(b) < 20:
            continue
        w = warm.get(s) or []
        gap = None
        if w:
            pc = w[-1]["c"]
            gap = (b[0]["o"] / pc - 1) * 100.0 if pc and b[0]["o"] else None
        if gap is not None and gap > GAP_MAX:
            continue                       # gapped -- the eye does not chase it
        out[s] = b
    return out


# ---------------- the strategies ----------------
def sig_orb(bars, open_min=15):
    """OPENING RANGE BREAKOUT. Mark the high of the first `open_min` minutes,
    then buy the first close above it. One signal a day, the classic rule."""
    n = open_min * 2                       # 30-second candles
    if len(bars) <= n:
        return []
    hi = max(x["h"] for x in bars[:n])
    for i in range(n, len(bars)):
        if bars[i]["c"] > hi and bars[i]["hhmm"] >= FROM:
            return [i]
    return []


def sig_donchian(bars, n=20):
    """DONCHIAN CHANNEL. Buy the close above the highest high of the last n
    candles. Re-arms after the channel is lost."""
    out, armed = [], True
    for i in range(n, len(bars)):
        if bars[i]["hhmm"] < FROM:
            continue
        ch = max(x["h"] for x in bars[i - n:i])
        lo = min(x["l"] for x in bars[i - n:i])
        if armed and bars[i]["c"] > ch:
            out.append(i); armed = False
        elif bars[i]["c"] < lo:
            armed = True
    return out


def sig_alphatrend(bars, coeff=1.0, ap=14):
    """ALPHATREND (KivancOzbilgic), AT 1 14. A trailing line that flips with
    money flow; the signal is the flip up."""
    n = len(bars)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(bars[i]["h"] - bars[i]["l"],
                    abs(bars[i]["h"] - bars[i - 1]["c"]),
                    abs(bars[i]["l"] - bars[i - 1]["c"]))
    atr = [0.0] * n
    for i in range(n):
        w = tr[max(0, i - ap + 1):i + 1]
        atr[i] = sum(w) / len(w) if w else 0.0
    up = [0.0] * n; dn = [0.0] * n; at = [0.0] * n
    for i in range(n):
        up[i] = bars[i]["l"] - atr[i] * coeff
        dn[i] = bars[i]["h"] + atr[i] * coeff
        if i == 0:
            at[i] = up[i]; continue
        # money-flow proxy: up-volume share over ap candles
        w = bars[max(0, i - ap + 1):i + 1]
        pos = sum((x["v"] or 0) for x in w if x["c"] >= x["o"])
        tot = sum((x["v"] or 0) for x in w) or 1
        mfi = 100.0 * pos / tot
        if mfi >= 50:
            at[i] = up[i] if up[i] >= at[i - 1] else at[i - 1]
        else:
            at[i] = dn[i] if dn[i] <= at[i - 1] else at[i - 1]
    return [i for i, sd in at_flips(bars, coeff, ap)
            if sd > 0 and bars[i]["hhmm"] >= FROM]


STRATS = {"ORB (15-min opening range)": sig_orb,
          "Donchian 20-bar high": sig_donchian,
          "AlphaTrend 1/14": sig_alphatrend}


def run(tape, signals, upto):
    """One book, three slots, identical exits."""
    events = []
    for sym, bars in tape.items():
        for i in signals(bars):
            if i + 1 < len(bars):
                events.append((bars[i + 1]["hhmm"], sym, i + 1))   # fill next bar
    events.sort()
    free_at = ["00:00:00"] * SLOTS
    trades = []
    for t, sym, i in events:
        slot = next((s for s in range(SLOTS) if free_at[s] <= t), None)
        if slot is None:
            continue
        bars = tape[sym]
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            continue
        qty = int(PER_SLOT / entry)
        if qty <= 0:
            continue
        peak, out_px, out_t, why = entry, None, None, None
        for j in range(i, len(bars)):
            lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
            if lo and lo <= entry * (1 + STOP_PCT / 100):
                out_px, out_t, why = entry * (1 + STOP_PCT / 100), bars[j]["hhmm"], "stop"
                break
            peak = max(peak, hi or c)
            if peak and (peak - c) / peak * 100 >= TRAIL_PCT and c > entry:
                out_px, out_t, why = c, bars[j]["hhmm"], "trail"
                break
        if out_px is None:
            out_px, out_t, why = bars[-1]["c"], upto, "square-off"
        bv, sv = qty * entry, qty * out_px
        ch = PE.charges(bv, sv)["total"]
        trades.append({"sym": sym, "in_t": t, "out_t": out_t, "in": entry,
                       "out": out_px, "qty": qty, "why": why,
                       "net": sv - bv - ch})
        free_at[slot] = out_t
    return trades, sum(x["net"] for x in trades)


# ---------------- filters and combinations ----------------
def _vwap(bars):
    out, pv, vv = [], 0.0, 0.0
    for x in bars:
        tp = (x["h"] + x["l"] + x["c"]) / 3.0
        v = x["v"] or 0
        pv += tp * v; vv += v
        out.append(pv / vv if vv else x["c"])
    return out


def _volx(bars, i, win=6, base=20):
    a = bars[max(0, i - win + 1):i + 1]
    b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
    if not a or not b:
        return 0.0
    ca = sum((x["v"] or 0) for x in a) / len(a)
    cb = sum((x["v"] or 0) for x in b) / len(b)
    return ca / cb if cb else 0.0


def above_vwap(bars):
    vw = _vwap(bars)
    return lambda i: bars[i]["c"] >= vw[i]


def vol_expanding(bars, x=3.0):
    return lambda i: _volx(bars, i) >= x


def sig_volume(bars, x=3.0):
    """My own main rule on its own: volume multiplies out of a quiet base and
    the block makes a new high."""
    out = []
    for i in range(26, len(bars)):
        if bars[i]["hhmm"] < FROM:
            continue
        if _volx(bars, i) < x:
            continue
        win = bars[max(0, i - 5):i + 1]
        look = bars[max(0, i - 16):max(0, i - 5)]
        if look and max(y["h"] for y in win) <= max(y["h"] for y in look):
            continue
        if bars[i]["c"] <= win[0]["o"]:
            continue
        out.append(i)
    return out


def combine(*sigfns, mode="and", filters=()):
    """AND = every signal must fire on the same candle. OR = any of them."""
    def fn(bars):
        sets = [set(f(bars)) for f in sigfns]
        idx = set.intersection(*sets) if mode == "and" else set.union(*sets)
        for mk in filters:
            ok = mk(bars)
            idx = {i for i in idx if ok(i)}
        return sorted(idx)
    return fn


def near(sigfns, within=6, filters=()):
    """AND, but tolerant: the other signals only need to have fired within
    `within` candles, because two indicators rarely turn on the same tick."""
    def fn(bars):
        sets = [sorted(f(bars)) for f in sigfns]
        base = sets[0]
        out = []
        for i in base:
            if all(any(abs(i - j) <= within for j in s) for s in sets[1:]):
                out.append(i)
        for mk in filters:
            ok = mk(bars)
            out = [i for i in out if ok(i)]
        return out
    return fn


# ---------------- the rest of the field ----------------
def _atr(bars, n):
    tr = [0.0] * len(bars)
    for i in range(1, len(bars)):
        tr[i] = max(bars[i]["h"] - bars[i]["l"],
                    abs(bars[i]["h"] - bars[i - 1]["c"]),
                    abs(bars[i]["l"] - bars[i - 1]["c"]))
    out, a = [0.0] * len(bars), 0.0
    for i in range(len(bars)):
        a = tr[i] if i == 0 else (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def sig_utbot(bars, key=1.0, per=10):
    """UT BOT ALERTS. ATR trailing stop; buy when close crosses above it."""
    atr = _atr(bars, per)
    st = [0.0] * len(bars)
    for i in range(len(bars)):
        loss = key * atr[i]
        c, pc = bars[i]["c"], bars[i - 1]["c"] if i else bars[i]["c"]
        p = st[i - 1] if i else c - loss
        if c > p and pc > p:
            st[i] = max(p, c - loss)
        elif c < p and pc < p:
            st[i] = min(p, c + loss)
        elif c > p:
            st[i] = c - loss
        else:
            st[i] = c + loss
    return [i for i in range(1, len(bars))
            if bars[i]["hhmm"] >= FROM
            and bars[i]["c"] > st[i] and bars[i - 1]["c"] <= st[i - 1]]


def sig_supertrend(bars, per=10, mult=3.0):
    """SUPERTREND 10/3 -- what Sri already has on his chart."""
    atr = _atr(bars, per)
    dirn, up, dn = [1] * len(bars), [0.0] * len(bars), [0.0] * len(bars)
    for i in range(len(bars)):
        mid = (bars[i]["h"] + bars[i]["l"]) / 2
        u, d = mid - mult * atr[i], mid + mult * atr[i]
        if i:
            u = max(u, up[i - 1]) if bars[i - 1]["c"] > up[i - 1] else u
            d = min(d, dn[i - 1]) if bars[i - 1]["c"] < dn[i - 1] else d
            dirn[i] = 1 if bars[i]["c"] > dn[i - 1] else (-1 if bars[i]["c"] < up[i - 1] else dirn[i - 1])
        up[i], dn[i] = u, d
    return [i for i in range(1, len(bars))
            if bars[i]["hhmm"] >= FROM and dirn[i] == 1 and dirn[i - 1] == -1]


def sig_chandelier(bars, per=22, mult=3.0):
    """CHANDELIER EXIT. Stop hangs ATR below the highest high."""
    atr = _atr(bars, per)
    dirn = [1] * len(bars)
    for i in range(1, len(bars)):
        w = bars[max(0, i - per + 1):i + 1]
        long_stop = max(x["h"] for x in w) - mult * atr[i]
        short_stop = min(x["l"] for x in w) + mult * atr[i]
        dirn[i] = 1 if bars[i]["c"] > short_stop else (-1 if bars[i]["c"] < long_stop else dirn[i - 1])
    return [i for i in range(1, len(bars))
            if bars[i]["hhmm"] >= FROM and dirn[i] == 1 and dirn[i - 1] == -1]


def sig_ssl(bars, per=10):
    """SSL CHANNEL. SMA of highs and SMA of lows; buy the flip up."""
    hlv = [0] * len(bars)
    for i in range(len(bars)):
        w = bars[max(0, i - per + 1):i + 1]
        sh = sum(x["h"] for x in w) / len(w)
        sl = sum(x["l"] for x in w) / len(w)
        hlv[i] = 1 if bars[i]["c"] > sh else (-1 if bars[i]["c"] < sl else (hlv[i - 1] if i else 0))
    return [i for i in range(1, len(bars))
            if bars[i]["hhmm"] >= FROM and hlv[i] == 1 and hlv[i - 1] != 1]


def _ema(vals, n):
    out, k = [], 2.0 / (n + 1)
    a = None
    for v in vals:
        a = v if a is None else v * k + a * (1 - k)
        out.append(a)
    return out


def sig_rangefilter(bars, per=20, mult=3.0):
    """RANGE FILTER (DonovanWall). A smoothed band that only moves when price
    travels far enough; buy when it turns up and price is above it."""
    c = [x["c"] for x in bars]
    d = [0.0] + [abs(c[i] - c[i - 1]) for i in range(1, len(c))]
    avrng = _ema(d, per)
    smooth = _ema(avrng, per * 2 - 1)
    r = [x * mult for x in smooth]
    filt = [c[0]] * len(c)
    for i in range(1, len(c)):
        p = filt[i - 1]
        filt[i] = max(p, c[i] - r[i]) if c[i] > p else min(p, c[i] + r[i])
    upw = [0] * len(c)
    for i in range(1, len(c)):
        upw[i] = upw[i - 1] + 1 if filt[i] > filt[i - 1] else (0 if filt[i] < filt[i - 1] else upw[i - 1])
    return [i for i in range(1, len(bars))
            if bars[i]["hhmm"] >= FROM and upw[i] > 0 and upw[i - 1] == 0 and c[i] > filt[i]]


def sig_halftrend(bars, amp=2):
    """HALFTREND (Everget), simplified to its trend flip."""
    n = len(bars)
    trend = [0] * n
    maxlow, minhigh = bars[0]["l"], bars[0]["h"]
    for i in range(1, n):
        w = bars[max(0, i - amp + 1):i + 1]
        hh = max(x["h"] for x in w); ll = min(x["l"] for x in w)
        ma_h = sum(x["h"] for x in w) / len(w)
        ma_l = sum(x["l"] for x in w) / len(w)
        t = trend[i - 1]
        if t == 0:
            maxlow = max(ll, maxlow)
            if ma_h < maxlow and bars[i]["c"] < bars[i - 1]["l"]:
                t = 1; minhigh = hh
        else:
            minhigh = min(hh, minhigh)
            if ma_l > minhigh and bars[i]["c"] > bars[i - 1]["h"]:
                t = 0; maxlow = ll
        trend[i] = t
    return [i for i in range(1, n)
            if bars[i]["hhmm"] >= FROM and trend[i] == 0 and trend[i - 1] == 1]


FIELD = {"AlphaTrend 1/14": sig_alphatrend,
         "UT Bot 1/10": sig_utbot,
         "SuperTrend 10/3": sig_supertrend,
         "Chandelier Exit 22/3": sig_chandelier,
         "SSL Channel 10": sig_ssl,
         "Range Filter 20/3": sig_rangefilter,
         "HalfTrend 2": sig_halftrend,
         "Donchian 20": sig_donchian,
         "ORB 15-min": sig_orb,
         "Volume expansion 3x": sig_volume}


# ---------------- SHORT SIDE ----------------
def _alphatrend_line(bars, coeff=1.0, ap=14):
    n = len(bars)
    atr = _atr(bars, ap)
    at = [0.0] * n
    for i in range(n):
        up = bars[i]["l"] - atr[i] * coeff
        dn = bars[i]["h"] + atr[i] * coeff
        if i == 0:
            at[i] = up; continue
        w = bars[max(0, i - ap + 1):i + 1]
        pos = sum((x["v"] or 0) for x in w if x["c"] >= x["o"])
        tot = sum((x["v"] or 0) for x in w) or 1
        at[i] = (up if up >= at[i - 1] else at[i - 1]) if (100.0 * pos / tot) >= 50 \
            else (dn if dn <= at[i - 1] else at[i - 1])
    return at


def at_flips(bars, coeff=1.0, ap=14):
    """THE REAL ALPHATREND SIGNAL, corrected 07-Sep.

    Sri: "as per the screenshot only 5 trades for PWL, then why 31?"

    Because I had it wrong. The published indicator does NOT signal on price
    crossing the line. It compares the AlphaTrend line WITH ITSELF TWO BARS AGO:
        k1 = AlphaTrend, k2 = AlphaTrend[2]
        BUY = crossover(k1, k2), SELL = crossunder(k1, k2)
    The line is a ratchet -- it only moves in the trend's direction and sits
    flat otherwise -- so k1 vs k2 turns over rarely and cleanly. Price crossing
    the line, which is what I coded, happens every time a candle wobbles, and
    gave 31 flips on PWL where the chart plainly shows 5.

    Returns [(index, +1 buy / -1 sell)].
    """
    at = _alphatrend_line(bars, coeff, ap)
    out = []
    for i in range(3, len(bars)):
        k1, k1p, k2, k2p = at[i], at[i - 1], at[i - 2], at[i - 3]
        if k1 > k2 and k1p <= k2p:
            out.append((i, 1))
        elif k1 < k2 and k1p >= k2p:
            out.append((i, -1))
    return out


def sig_alphatrend_short(bars, coeff=1.0, ap=14):
    """SELL side of the corrected signal."""
    return [i for i, sd in at_flips(bars, coeff, ap)
            if sd < 0 and bars[i]["hhmm"] >= FROM]


def run_sided(tape, long_fn, short_fn, upto):
    """Same book, but a signal can be a BUY or a SELL. Shorts are the exact
    mirror: stop 1% ABOVE entry, trail 1.2% up off the trough."""
    events = []
    if long_fn:
        for sym, bars in tape.items():
            for i in long_fn(bars):
                if i + 1 < len(bars):
                    events.append((bars[i + 1]["hhmm"], sym, i + 1, +1))
    if short_fn:
        for sym, bars in tape.items():
            for i in short_fn(bars):
                if i + 1 < len(bars):
                    events.append((bars[i + 1]["hhmm"], sym, i + 1, -1))
    events.sort()
    free_at = ["00:00:00"] * SLOTS
    trades = []
    for t, sym, i, side in events:
        slot = next((s for s in range(SLOTS) if free_at[s] <= t), None)
        if slot is None:
            continue
        bars = tape[sym]
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            continue
        qty = int(PER_SLOT / entry)
        if qty <= 0:
            continue
        out_px = out_t = why = None
        if side > 0:
            peak = entry
            for j in range(i, len(bars)):
                lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
                if lo and lo <= entry * (1 + STOP_PCT / 100):
                    out_px, out_t, why = entry * (1 + STOP_PCT / 100), bars[j]["hhmm"], "stop"; break
                peak = max(peak, hi or c)
                if peak and (peak - c) / peak * 100 >= TRAIL_PCT and c > entry:
                    out_px, out_t, why = c, bars[j]["hhmm"], "trail"; break
        else:
            trough = entry
            for j in range(i, len(bars)):
                lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
                if hi and hi >= entry * (1 - STOP_PCT / 100):
                    out_px, out_t, why = entry * (1 - STOP_PCT / 100), bars[j]["hhmm"], "stop"; break
                trough = min(trough, lo or c)
                if trough and (c - trough) / trough * 100 >= TRAIL_PCT and c < entry:
                    out_px, out_t, why = c, bars[j]["hhmm"], "trail"; break
        if out_px is None:
            out_px, out_t, why = bars[-1]["c"], upto, "square-off"
        bv, sv = qty * entry, qty * out_px
        ch = PE.charges(bv, sv)["total"]
        net = (sv - bv - ch) if side > 0 else (bv - sv - ch)
        trades.append({"sym": sym, "side": "LONG" if side > 0 else "SHORT",
                       "in_t": t, "out_t": out_t, "in": entry, "out": out_px,
                       "qty": qty, "why": why, "net": net})
        free_at[slot] = out_t
    return trades, sum(x["net"] for x in trades)
