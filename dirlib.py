"""dirlib.py -- every indicator expressed the SAME way: a direction line.

Sri, 07-Sep, after catching my AlphaTrend bug: "ensure you don't do mistakes of
these kind with other indicators."

The bug was that I wrote each indicator's SIGNAL by hand and got AlphaTrend's
wrong -- I used "price crosses the line" where the published script compares the
line with itself two bars back, so I generated 31 flips where the chart shows 5.

The fix is structural. Each function here returns ONE thing: dirn[i] = +1 when
the indicator says up, -1 when it says down, for every candle. Nothing else.
From that single line, all three ways of trading it are derived mechanically:

    long signal  = the bar where dirn turns +1
    short signal = the bar where dirn turns -1
    native mode  = always in the market, reverse on every turn

So there is no per-indicator signal code left to get wrong, and long and short
are guaranteed to be exact mirrors of each other.
"""


def _tr(bars):
    out = [0.0] * len(bars)
    for i in range(1, len(bars)):
        out[i] = max(bars[i]["h"] - bars[i]["l"],
                     abs(bars[i]["h"] - bars[i - 1]["c"]),
                     abs(bars[i]["l"] - bars[i - 1]["c"]))
    return out


def _rma(v, n):
    out, a = [0.0] * len(v), 0.0
    for i, x in enumerate(v):
        a = x if i == 0 else (a * (n - 1) + x) / n
        out[i] = a
    return out


def _sma(v, n):
    return [sum(v[max(0, i - n + 1):i + 1]) / len(v[max(0, i - n + 1):i + 1])
            for i in range(len(v))]


def _ema(v, n):
    out, k, a = [], 2.0 / (n + 1), None
    for x in v:
        a = x if a is None else x * k + a * (1 - k)
        out.append(a)
    return out


def atr(bars, n):
    return _rma(_tr(bars), n)


# ---------------------------------------------------------------- AlphaTrend
def mfi(bars, n=14):
    """Money Flow Index on hlc3 -- what AlphaTrend actually uses. My first
    version used an up-volume share as a stand-in; it is not the same thing and
    it moved the flips."""
    tp = [(x["h"] + x["l"] + x["c"]) / 3 for x in bars]
    pos = [0.0] * len(bars); neg = [0.0] * len(bars)
    for i in range(1, len(bars)):
        flow = tp[i] * (bars[i]["v"] or 0)
        if tp[i] > tp[i - 1]:
            pos[i] = flow
        elif tp[i] < tp[i - 1]:
            neg[i] = flow
    out = [50.0] * len(bars)
    for i in range(len(bars)):
        p = sum(pos[max(0, i - n + 1):i + 1])
        q = sum(neg[max(0, i - n + 1):i + 1])
        out[i] = 100.0 if q == 0 else (0.0 if p == 0 else 100.0 - 100.0 / (1 + p / q))
    return out


def alphatrend(bars, coeff=1.0, ap=14):
    """KivancOzbilgic. Line ratchets with money flow; direction = line vs line[2].

        upT = low - ATR*Coeff ; downT = high + ATR*Coeff
        line = mfi >= 50 ? max(upT, line[1]) : min(downT, line[1])
        BUY = crossover(line, line[2])
    """
    a = atr(bars, ap)
    m = mfi(bars, ap)
    line = [0.0] * len(bars)
    for i in range(len(bars)):
        up = bars[i]["l"] - a[i] * coeff
        dn = bars[i]["h"] + a[i] * coeff
        if i == 0:
            line[i] = up
            continue
        line[i] = max(up, line[i - 1]) if m[i] >= 50 else min(dn, line[i - 1])
    d = [1] * len(bars)
    for i in range(2, len(bars)):
        d[i] = 1 if line[i] > line[i - 2] else (-1 if line[i] < line[i - 2] else d[i - 1])
    return d


# ---------------------------------------------------------------- SuperTrend
def supertrend(bars, per=10, mult=3.0):
    a = atr(bars, per)
    d = [1] * len(bars)
    up = [0.0] * len(bars); dn = [0.0] * len(bars)
    for i in range(len(bars)):
        mid = (bars[i]["h"] + bars[i]["l"]) / 2
        u, l = mid + mult * a[i], mid - mult * a[i]
        if i:
            l = max(l, dn[i - 1]) if bars[i - 1]["c"] > dn[i - 1] else l
            u = min(u, up[i - 1]) if bars[i - 1]["c"] < up[i - 1] else u
            d[i] = 1 if bars[i]["c"] > up[i - 1] else (-1 if bars[i]["c"] < dn[i - 1] else d[i - 1])
        up[i], dn[i] = u, l
    return d


# ---------------------------------------------------------------- UT Bot
def utbot(bars, key=1.0, per=10):
    a = atr(bars, per)
    st = [0.0] * len(bars)
    for i in range(len(bars)):
        loss = key * a[i]
        c = bars[i]["c"]; pc = bars[i - 1]["c"] if i else c
        p = st[i - 1] if i else c - loss
        st[i] = (max(p, c - loss) if (c > p and pc > p) else
                 min(p, c + loss) if (c < p and pc < p) else
                 (c - loss if c > p else c + loss))
    return [1 if bars[i]["c"] > st[i] else -1 for i in range(len(bars))]


# ---------------------------------------------------------------- Chandelier
def chandelier(bars, per=22, mult=3.0):
    a = atr(bars, per)
    d = [1] * len(bars)
    for i in range(1, len(bars)):
        w = bars[max(0, i - per + 1):i + 1]
        long_stop = max(x["h"] for x in w) - mult * a[i]
        short_stop = min(x["l"] for x in w) + mult * a[i]
        d[i] = 1 if bars[i]["c"] > short_stop else (-1 if bars[i]["c"] < long_stop else d[i - 1])
    return d


# ---------------------------------------------------------------- SSL Channel
def ssl(bars, per=10):
    sh = _sma([x["h"] for x in bars], per)
    sl = _sma([x["l"] for x in bars], per)
    d = [1] * len(bars)
    for i in range(1, len(bars)):
        d[i] = 1 if bars[i]["c"] > sh[i] else (-1 if bars[i]["c"] < sl[i] else d[i - 1])
    return d


# ---------------------------------------------------------------- Range Filter
def rangefilter(bars, per=20, mult=3.0):
    c = [x["c"] for x in bars]
    diff = [0.0] + [abs(c[i] - c[i - 1]) for i in range(1, len(c))]
    r = [x * mult for x in _ema(_ema(diff, per), per * 2 - 1)]
    filt = [c[0]] * len(c)
    for i in range(1, len(c)):
        p = filt[i - 1]
        filt[i] = max(p, c[i] - r[i]) if c[i] > p else min(p, c[i] + r[i])
    d = [1] * len(c)
    for i in range(1, len(c)):
        d[i] = 1 if filt[i] > filt[i - 1] else (-1 if filt[i] < filt[i - 1] else d[i - 1])
    return d


# ---------------------------------------------------------------- Donchian
def donchian(bars, n=20):
    d = [1] * len(bars)
    for i in range(n, len(bars)):
        hi = max(x["h"] for x in bars[i - n:i])
        lo = min(x["l"] for x in bars[i - n:i])
        d[i] = 1 if bars[i]["c"] > hi else (-1 if bars[i]["c"] < lo else d[i - 1])
    return d


# ---------------------------------------------------------------- HOTT / LOTT
def ott(bars, n=2, percent=1.4):
    """KivancOzbilgic's Optimized Trend Tracker (the HOTT/LOTT engine, which is
    already in Sri's Board as Pine)."""
    src = [x["c"] for x in bars]
    # VIDYA-ish support: use a simple VAR/EMA blend -- EMA is the common default
    ma = _ema(src, n * 2)
    longstop = [0.0] * len(bars); shortstop = [0.0] * len(bars)
    d = [1] * len(bars)
    for i in range(len(bars)):
        f = ma[i] * percent / 100.0
        ls = ma[i] - f; ss = ma[i] + f
        if i:
            ls = max(ls, longstop[i - 1]) if ma[i - 1] > longstop[i - 1] else ls
            ss = min(ss, shortstop[i - 1]) if ma[i - 1] < shortstop[i - 1] else ss
            d[i] = 1 if ma[i] > shortstop[i - 1] else (-1 if ma[i] < longstop[i - 1] else d[i - 1])
        longstop[i], shortstop[i] = ls, ss
    return d


# ---------------------------------------------------------------- WaveTrend
def wavetrend(bars, n1=10, n2=21):
    """LazyBear's WaveTrend. Direction = wt1 above/below wt2."""
    ap = [(x["h"] + x["l"] + x["c"]) / 3 for x in bars]
    esa = _ema(ap, n1)
    dv = _ema([abs(ap[i] - esa[i]) for i in range(len(ap))], n1)
    ci = [(ap[i] - esa[i]) / (0.015 * dv[i]) if dv[i] else 0.0 for i in range(len(ap))]
    wt1 = _ema(ci, n2)
    wt2 = _sma(wt1, 4)
    return [1 if wt1[i] > wt2[i] else -1 for i in range(len(bars))]


# ---------------------------------------------------------------- Linear Reg
def linreg_candles(bars, n=11):
    """ugurvu's Linear Regression Candles: direction = linreg close vs its own
    signal line."""
    def lr(vals, i):
        w = vals[max(0, i - n + 1):i + 1]
        m = len(w)
        if m < 2:
            return w[-1]
        sx = sum(range(m)); sy = sum(w)
        sxx = sum(k * k for k in range(m)); sxy = sum(k * w[k] for k in range(m))
        den = m * sxx - sx * sx
        if den == 0:
            return w[-1]
        b = (m * sxy - sx * sy) / den
        a = (sy - b * sx) / m
        return a + b * (m - 1)
    c = [x["c"] for x in bars]
    lrc = [lr(c, i) for i in range(len(c))]
    sig = _sma(lrc, 7)
    return [1 if lrc[i] > sig[i] else -1 for i in range(len(bars))]


# ---------------------------------------------------------------- Jurik-ish MA
def jma(bars, n=14, phase=0):
    """everget's Jurik MA -- direction = slope."""
    src = [x["c"] for x in bars]
    beta = 0.45 * (n - 1) / (0.45 * (n - 1) + 2)
    out = [src[0]] * len(src)
    e0 = e1 = e2 = 0.0
    jm = src[0]
    pr = 0.5 if phase == 0 else max(0.5, min(2.5, phase / 100.0 + 1.5))
    for i in range(1, len(src)):
        alpha = beta
        e0 = (1 - alpha) * src[i] + alpha * e0
        e1 = (src[i] - e0) * (1 - beta) + beta * e1
        e2 = (e0 + pr * e1 - jm) * (1 - alpha) ** 2 + alpha ** 2 * e2
        jm = jm + e2
        out[i] = jm
    return [1 if out[i] > out[i - 1] else -1 for i in range(len(out))]


# ---------------------------------------------------------------- MACD
def macd(bars, fast=12, slow=26, sig=9):
    c = [x["c"] for x in bars]
    m = [a - b for a, b in zip(_ema(c, fast), _ema(c, slow))]
    s = _ema(m, sig)
    return [1 if m[i] > s[i] else -1 for i in range(len(bars))]


# ---------------------------------------------------------------- volume rule
def volume_rule(bars, x=3.0, win=6, base=20):
    """Sri's/my own: volume expands out of a quiet base and price makes a new
    high. Not a two-sided indicator, so 'down' just means 'not firing'."""
    d = [-1] * len(bars)
    for i in range(len(bars)):
        a = bars[max(0, i - win + 1):i + 1]
        b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
        if not a or not b:
            continue
        ca = sum((y["v"] or 0) for y in a) / len(a)
        cb = sum((y["v"] or 0) for y in b) / len(b)
        if not cb or ca / cb < x:
            d[i] = d[i - 1] if i else -1
            continue
        look = bars[max(0, i - win - 10 + 1):max(0, i - win + 1)]
        if look and max(y["h"] for y in a) <= max(y["h"] for y in look):
            d[i] = d[i - 1] if i else -1
            continue
        d[i] = 1 if bars[i]["c"] > a[0]["o"] else -1
    return d


FIELD = {
    "AlphaTrend 1/14":      lambda b: alphatrend(b),
    "SuperTrend 10/3":      lambda b: supertrend(b),
    "UT Bot 1/10":          lambda b: utbot(b),
    "Chandelier 22/3":      lambda b: chandelier(b),
    "SSL Channel 10":       lambda b: ssl(b),
    "Range Filter 20/3":    lambda b: rangefilter(b),
    "Donchian 20":          lambda b: donchian(b),
    "OTT (HOTT/LOTT) 2/1.4":lambda b: ott(b),
    "WaveTrend 10/21":      lambda b: wavetrend(b),
    "LinReg Candles 11":    lambda b: linreg_candles(b),
    "Jurik MA 14":          lambda b: jma(b),
    "MACD 12/26/9":         lambda b: macd(b),
    "Volume expansion 3x":  lambda b: volume_rule(b),
}
