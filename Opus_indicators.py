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


# ================= V3.5 (Opus) add-on indicators =================
def _tr_series(h, l, c):
    n = len(c); tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = (h[i] - l[i]) if (h[i] is not None and l[i] is not None) else 0.0
        else:
            hi, li, pc = h[i], l[i], c[i - 1]
            tr[i] = max(hi - li, abs(hi - pc), abs(li - pc))
    return tr


def _atr_series(h, l, c, n):
    """Wilder RMA ATR as a per-bar series (None until enough bars)."""
    m = len(c); out = [None] * m
    if m < n or n < 1:
        return out
    tr = _tr_series(h, l, c)
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, m):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def ut_bot(h, l, c, key=1.0, atr_period=2):
    """UT Bot (QuantNomad / HPotter): ATR trailing stop + Buy/Sell flips.
    Returns {stop[], buy[], sell[], in_buy(bool)}. We use only the labels."""
    n = len(c)
    atrs = _atr_series(h, l, c, atr_period)
    stop = [None] * n
    for i in range(n):
        a = atrs[i]
        if a is None:
            continue
        nloss = key * a
        if i == 0 or stop[i - 1] is None:
            stop[i] = c[i] - nloss
            continue
        ps, pc, cc = stop[i - 1], c[i - 1], c[i]
        if cc > ps and pc > ps:
            stop[i] = max(ps, cc - nloss)
        elif cc < ps and pc < ps:
            stop[i] = min(ps, cc + nloss)
        elif cc > ps:
            stop[i] = cc - nloss
        else:
            stop[i] = cc + nloss
    buy = [False] * n; sell = [False] * n
    for i in range(1, n):
        if stop[i] is None or stop[i - 1] is None:
            continue
        cross_up = c[i - 1] <= stop[i - 1] and c[i] > stop[i]
        cross_dn = c[i - 1] >= stop[i - 1] and c[i] < stop[i]
        if c[i] > stop[i] and cross_up:
            buy[i] = True
        if c[i] < stop[i] and cross_dn:
            sell[i] = True
    in_buy = bool(stop[-1] is not None and c[-1] > stop[-1])
    return {"stop": stop, "buy": buy, "sell": sell, "in_buy": in_buy}


def consolidation_zone(h, l, c, loopback=20, max_range_pct=1.5):
    """Display-only coil detector (LonesomeTheBlue-style, simplified): if the last
    `loopback` bars sit inside a tight band (<= max_range_pct of price), report the box."""
    n = len(c)
    if n < max(4, loopback):
        return None
    sh = [x for x in h[-loopback:] if x is not None]
    sl = [x for x in l[-loopback:] if x is not None]
    if not sh or not sl:
        return None
    top = max(sh); bot = min(sl); px = c[-1]
    if not px:
        return None
    rng = (top - bot) / px * 100.0
    if rng <= max_range_pct:
        return {"top": round(top, 3), "bottom": round(bot, 3),
                "start": n - loopback, "range_pct": round(rng, 2)}
    return None


def consolidation_zones(h, l, c, prd=10, conslen=5):
    """Faithful port of LonesomeTheBlue \"Consolidation Zones - Live\" (Pine v4).
    Zigzag pivots (prd) drive a running pivot pp; while pp keeps oscillating inside
    [condlow, condhigh] for >= conslen bars a box is drawn and extended; a pivot that
    breaks the box resets it. Returns [{s,e,top,bottom}] absolute bar indices."""
    n = len(c)
    if n < prd + 2 or conslen < 2:
        return []

    def is_highest(i):
        w = h[max(0, i - prd + 1):i + 1]
        return bool(w) and h[i] is not None and h[i] >= max(x for x in w if x is not None)

    def is_lowest(i):
        w = l[max(0, i - prd + 1):i + 1]
        return bool(w) and l[i] is not None and l[i] <= min(x for x in w if x is not None)

    def highest(i, ln):
        w = [x for x in h[max(0, i - ln + 1):i + 1] if x is not None]
        return max(w) if w else h[i]

    def lowest(i, ln):
        w = [x for x in l[max(0, i - ln + 1):i + 1] if x is not None]
        return min(w) if w else l[i]

    dir_ = 0
    pp = None
    pp_prev = None
    conscnt = 0
    condhigh = None
    condlow = None
    zones = []
    cur_box = None

    for i in range(n):
        hb = h[i] if is_highest(i) else None
        lb = l[i] if is_lowest(i) else None
        newdir = dir_
        if hb is not None and lb is None:
            newdir = 1
        elif lb is not None and hb is None:
            newdir = -1
        # zigzag value for this bar
        if hb is not None and lb is not None:
            zz = hb if newdir == 1 else lb
        elif hb is not None:
            zz = hb
        elif lb is not None:
            zz = lb
        else:
            zz = None
        # running pivot = extreme of the current directional run (reset when dir flips)
        if newdir != dir_:
            pp = None
        dir_ = newdir
        if zz is not None:
            if pp is None:
                pp = zz
            elif dir_ == 1 and zz > pp:
                pp = zz
            elif dir_ == -1 and zz < pp:
                pp = zz

        changed = (pp is not None and pp != pp_prev)
        if changed:
            if conscnt > conslen and condhigh is not None:
                pass  # (breakout up/down would fire here)
            if conscnt > 0 and condhigh is not None and condlow is not None \
                    and condlow <= pp <= condhigh:
                conscnt += 1
            else:
                conscnt = 0
        else:
            conscnt += 1

        if conscnt >= conslen:
            if conscnt == conslen:
                condhigh = highest(i, conslen)
                condlow = lowest(i, conslen)
            else:
                condhigh = max(condhigh, h[i] if h[i] is not None else condhigh)
                condlow = min(condlow, l[i] if l[i] is not None else condlow)
            cur_box = {"s": max(0, i - conscnt), "e": i,
                       "top": round(condhigh, 3), "bottom": round(condlow, 3)}
        else:
            if cur_box is not None:
                zones.append(cur_box)
                cur_box = None
            condhigh = condlow = None

        pp_prev = pp

    if cur_box is not None:
        zones.append(cur_box)
    return zones


def ualgo_trend(o, h, l, c, mult=2.0, atr_len=14, method="Method 1"):
    """Port of UAlgo 'Trend Signals with TP & SL' trend engine (replaces UT-Bot).
    ATR band on src=hl2: up=src-mult*ATR, dn=src+mult*ATR; trend flips 1/-1;
    buy=flip to +1, sell=flip to -1. Returns trend[], up[], dn[], buy[], sell[], in_buy."""
    n = len(c)
    if n == 0:
        return {"trend": [], "up": [], "dn": [], "buy": [], "sell": [], "in_buy": False}
    src = [((h[i] + l[i]) / 2.0) if (h[i] is not None and l[i] is not None) else c[i] for i in range(n)]
    if method == "Method 2":
        tr = _tr_series(h, l, c)
        atr = [None] * n
        for i in range(n):
            if i + 1 >= atr_len:
                atr[i] = sum(tr[i - atr_len + 1:i + 1]) / atr_len
    else:
        atr = _atr_series(h, l, c, atr_len)
    up = [None] * n; dn = [None] * n; trend = [1] * n
    buy = [False] * n; sell = [False] * n
    for i in range(n):
        a = atr[i]
        if a is None:
            up[i] = dn[i] = None
            trend[i] = trend[i - 1] if i > 0 else 1
            continue
        raw_up = src[i] - mult * a
        raw_dn = src[i] + mult * a
        up1 = up[i - 1] if (i > 0 and up[i - 1] is not None) else raw_up
        dn1 = dn[i - 1] if (i > 0 and dn[i - 1] is not None) else raw_dn
        up[i] = max(raw_up, up1) if (i > 0 and c[i - 1] > up1) else raw_up
        dn[i] = min(raw_dn, dn1) if (i > 0 and c[i - 1] < dn1) else raw_dn
        pt = trend[i - 1] if i > 0 else 1
        t = pt
        if pt == -1 and c[i] > dn1:
            t = 1
        elif pt == 1 and c[i] < up1:
            t = -1
        trend[i] = t
        if i > 0:
            if t == 1 and trend[i - 1] == -1:
                buy[i] = True
            if t == -1 and trend[i - 1] == 1:
                sell[i] = True
    return {"trend": trend, "up": up, "dn": dn, "buy": buy, "sell": sell,
            "in_buy": bool(trend[-1] == 1)}
