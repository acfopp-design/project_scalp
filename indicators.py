"""
indicators.py - pure-python indicator math (no network), with full per-bar
SERIES so the UI can draw them. Ported/extended from the proven dhan_watch.py.

Everything works on plain python lists of floats.
"""
import math


def sma(a, n):
    return sum(a[-n:]) / n if len(a) >= n else None


def sma_series(a, n):
    out = [None] * len(a)
    for i in range(len(a)):
        if i + 1 >= n:
            out[i] = sum(a[i + 1 - n:i + 1]) / n
    return out


def ema_series(a, n):
    if len(a) < n:
        return [None] * len(a)
    k = 2 / (n + 1)
    out = [None] * len(a)
    e = sum(a[:n]) / n
    out[n - 1] = e
    for i in range(n, len(a)):
        e = a[i] * k + e * (1 - k)
        out[i] = e
    return out


def ema(a, n):
    s = ema_series(a, n)
    return s[-1] if s else None


def wma(a, n):
    if len(a) < n:
        return None
    w = list(range(1, n + 1))
    s = a[-n:]
    return sum(x * wt for x, wt in zip(s, w)) / sum(w)


def hma_series(a, n):
    """Hull MA(n) per bar (the Pine 'Hull Suite', HMA 55)."""
    out = [None] * len(a)
    half_n, sqrt_n = n // 2, int(math.sqrt(n))
    if len(a) < n + sqrt_n:
        return out
    half = [wma(a[:i + 1], half_n) for i in range(len(a))]
    full = [wma(a[:i + 1], n) for i in range(len(a))]
    raw = [(2 * h - f) if (h is not None and f is not None) else None
           for h, f in zip(half, full)]
    for i in range(len(a)):
        seg = [x for x in raw[max(0, i - sqrt_n + 1):i + 1] if x is not None]
        if len(seg) >= sqrt_n:
            w = list(range(1, sqrt_n + 1))
            out[i] = sum(x * wt for x, wt in zip(seg[-sqrt_n:], w)) / sum(w)
    return out


def rsi(a, n=14):
    if len(a) < n + 1:
        return None
    g = l = 0.0
    for i in range(-n, 0):
        d = a[i] - a[i - 1]
        g += max(d, 0); l += max(-d, 0)
    if l == 0:
        return 100.0
    rs = (g / n) / (l / n)
    return 100 - 100 / (1 + rs)


def macd_series(a, f=12, s=26, sig=9):
    """Returns dict of per-bar lists: line[], signal[], hist[] (None-padded)."""
    ef, es = ema_series(a, f), ema_series(a, s)
    n = len(a)
    line = [None] * n
    for i in range(n):
        if ef[i] is not None and es[i] is not None:
            line[i] = ef[i] - es[i]
    # signal = EMA(sig) of the non-None portion of line
    idx = [i for i in range(n) if line[i] is not None]
    sig_line = [None] * n
    hist = [None] * n
    if idx:
        vals = [line[i] for i in idx]
        sg = ema_series(vals, sig)
        for j, i in enumerate(idx):
            if sg[j] is not None:
                sig_line[i] = sg[j]
                hist[i] = line[i] - sg[j]
    return {"line": line, "signal": sig_line, "hist": hist}


def atr(h, l, c, n=14):
    if len(c) < n + 1:
        return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
          for i in range(1, len(c))]
    return sum(tr[-n:]) / n


def psar_series(h, l, step=0.02, inc=0.02, mx=0.2):
    """Parabolic SAR per bar + 'is SAR below price' flag per bar."""
    n = len(h)
    out = [None] * n
    if n < 3:
        return out
    up = True; af = step; ep = h[0]; sar = l[0]
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            if l[i] < sar:
                up = False; sar = ep; ep = l[i]; af = step
            elif h[i] > ep:
                ep = h[i]; af = min(af + inc, mx)
        else:
            if h[i] > sar:
                up = True; sar = ep; ep = h[i]; af = step
            elif l[i] < ep:
                ep = l[i]; af = min(af + inc, mx)
        out[i] = sar
    return out


def ichimoku_series(h, l, tenkan=9, kijun=26, senkouB=52, disp=26):
    """Per-bar cloud top/bottom (Senkou A/B shifted `disp` bars forward)."""
    n = len(h)
    spanA = [None] * n
    spanB = [None] * n

    def donch(arr_h, arr_l, period, i):
        if i + 1 < period:
            return None
        seg_h = arr_h[i + 1 - period:i + 1]
        seg_l = arr_l[i + 1 - period:i + 1]
        return (max(seg_h) + min(seg_l)) / 2

    for i in range(n):
        t = donch(h, l, tenkan, i)
        k = donch(h, l, kijun, i)
        b = donch(h, l, senkouB, i)
        if t is not None and k is not None:
            spanA[i] = (t + k) / 2
        if b is not None:
            spanB[i] = b
    # shift forward by disp -> value plotted at bar i comes from i-disp
    top = [None] * n
    bot = [None] * n
    for i in range(n):
        j = i - disp
        if j >= 0 and spanA[j] is not None and spanB[j] is not None:
            top[i] = max(spanA[j], spanB[j])
            bot[i] = min(spanA[j], spanB[j])
    return top, bot


def angle_deg(series, atr_val, lookback=3):
    """EMA/line angle in degrees: atan of ATR-normalised slope over `lookback`
    bars. ATR-normalisation makes the angle comparable across price levels
    (the log-scale-equivalent the user asked about)."""
    vals = [x for x in series if x is not None]
    if len(vals) < lookback + 1 or not atr_val:
        return None
    slope = (vals[-1] - vals[-1 - lookback]) / lookback / atr_val
    return math.degrees(math.atan(slope))
