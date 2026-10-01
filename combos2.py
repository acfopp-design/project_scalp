"""combos2.py -- HOTT/LOTT paired with JustUncleL's two tools.

HOTT/LOTT (KivancOzbilgic / Anil Ozeksi) is ported from Archive/code/hott_lott.py,
which was written from the published algorithm and uses the script's own default
VAR/VIDYA moving average and the OTT ratchet. Here it is fed TRUE bar highs and
lows rather than the board's last-traded-price stream, which is closer to the
TradingView plot than the archived version could get.

    close > HOTT  -> UPTREND
    close < LOTT  -> DOWNTREND
    in between    -> FLAT ZONE, the author says do nothing. That is the point of
                     the pairing: HOTT/LOTT decides WHEN THE MARKET IS WORTH
                     TRADING, and the second tool decides the entry.

PULLBACK TRADING TOOL ALT R1.0 (JustUncleL)
    uptrend  = EMA8 > EMA21 > EMA50
    long arrow = in an uptrend, price dips below the EMA8/EMA21 and then closes
                 back above it -- a pullback that resumes.

PRICE ACTION TRADING SYSTEM v0.3 (JustUncleL)
    trend filter = EMA17 rising (his "MACD slow EMA" blue line)
    RSI(7) filter = RSI above 50 for longs
    entry = a one-or-two bar pullback inside the trend, then a close back above
            the prior bar's high. "Works best on the first alert after the MACD
            crossover; don't trade when the MAs are flat or crossing quickly."

BOTH descriptions come from the authors' own pages. Neither Pine source could be
read (TradingView will not render the code panel in the browser pane), so these
are implementations of the DESCRIBED rules, not verified line-by-line ports.
"""
import math


def _ema(v, n):
    out, k, a = [], 2.0 / (n + 1), None
    for x in v:
        a = x if a is None else x * k + a * (1 - k)
        out.append(a)
    return out


def _vidya(src, length):
    out = [src[0] if src else 0.0] * len(src)
    ups = [0.0] * len(src); dns = [0.0] * len(src)
    for i in range(1, len(src)):
        d = src[i] - src[i - 1]
        ups[i] = max(d, 0.0); dns[i] = max(-d, 0.0)
    k = 2.0 / (length + 1)
    val = src[0] if src else 0.0
    for i in range(len(src)):
        lo = max(0, i - 8)
        su, sd = sum(ups[lo:i + 1]), sum(dns[lo:i + 1])
        cmo = abs((su - sd) / (su + sd)) if (su + sd) else 0.0
        val = src[i] * k * cmo + val * (1 - k * cmo)
        out[i] = val
    return out


def _ott(src, length, percent):
    m = _vidya(src, length)
    n = len(src)
    out = [None] * n
    lp = sp = None
    d = 1
    for i in range(n):
        mv = m[i]
        fark = mv * percent * 0.01
        ls = mv - fark
        if lp is not None and mv > lp:
            ls = max(ls, lp)
        ss = mv + fark
        if sp is not None and mv < sp:
            ss = min(ss, sp)
        if d == -1 and sp is not None and mv > sp:
            d = 1
        elif d == 1 and lp is not None and mv < lp:
            d = -1
        mt = ls if d == 1 else ss
        out[i] = mt * (200 + percent) / 200 if mv > mt else mt * (200 - percent) / 200
        lp, sp = ls, ss
    return out


def hott_lott(bars, hl=8, length=2, percent=1.4):
    n = len(bars)
    hi = [max(x["h"] for x in bars[max(0, i - hl + 1):i + 1]) for i in range(n)]
    lo = [min(x["l"] for x in bars[max(0, i - hl + 1):i + 1]) for i in range(n)]
    H, L = _ott(hi, length, percent), _ott(lo, length, percent)
    lag = 2                                    # the script plots nz(HOTT[2])
    H = [None] * lag + H[:-lag] if n > lag else H
    L = [None] * lag + L[:-lag] if n > lag else L
    out = []
    for i in range(n):
        c = bars[i]["c"]
        if H[i] is None or L[i] is None:
            out.append(0)
        elif c > H[i]:
            out.append(1)
        elif c < L[i]:
            out.append(-1)
        else:
            out.append(0)                      # FLAT ZONE
    return out


def pullback_alt(bars, fast=8, sig=21, med=50):
    """+1 on the bar where a pullback inside an uptrend resumes."""
    c = [x["c"] for x in bars]
    e8, e21, e50 = _ema(c, fast), _ema(c, sig), _ema(c, med)
    out = [-1] * len(bars)
    pulled = False
    for i in range(1, len(bars)):
        up = e8[i] > e21[i] > e50[i]
        if not up:
            pulled = False
            out[i] = -1
            continue
        if c[i] < e8[i]:                       # dipped into the pullback
            pulled = True
            out[i] = out[i - 1]
            continue
        if pulled and c[i] > e8[i] and c[i] > c[i - 1]:
            out[i] = 1                         # resumed
            pulled = False
        else:
            out[i] = out[i - 1] if out[i - 1] == 1 else -1
    return out


def price_action_v03(bars, ema=17, rsi_len=7):
    """+1 when a short pullback resumes inside an EMA17 uptrend with RSI>50."""
    c = [x["c"] for x in bars]
    e = _ema(c, ema)
    g = [0.0] * len(c); l = [0.0] * len(c)
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        g[i] = max(d, 0.0); l[i] = max(-d, 0.0)
    ag = _ema(g, rsi_len); al = _ema(l, rsi_len)
    rsi = [100 - 100 / (1 + (ag[i] / al[i])) if al[i] else 100.0 for i in range(len(c))]
    out = [-1] * len(bars)
    for i in range(2, len(bars)):
        trend = e[i] > e[i - 1] and c[i] > e[i]
        if not trend or rsi[i] <= 50:
            out[i] = -1
            continue
        dipped = bars[i - 1]["c"] < bars[i - 2]["c"] or bars[i - 1]["l"] < bars[i - 2]["l"]
        broke = c[i] > bars[i - 1]["h"]
        out[i] = 1 if (dipped and broke) else (out[i - 1] if out[i - 1] == 1 else -1)
    return out


def combined(bars, entry_fn, hl=8, length=2, percent=1.4, **kw):
    """HOTT/LOTT must say UPTREND (not flat, not down) AND the entry tool must
    fire. Exit when HOTT/LOTT leaves the uptrend."""
    z = hott_lott(bars, hl, length, percent)
    e = entry_fn(bars, **kw)
    out = [-1] * len(bars)
    for i in range(len(bars)):
        if z[i] == 1 and e[i] == 1:
            out[i] = 1
        elif z[i] == 1 and out[i - 1] == 1:
            out[i] = 1                          # stay while the trend holds
        else:
            out[i] = -1
    return out
