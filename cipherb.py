"""cipherb.py -- VuManChu Cipher B (Market Cipher B), WaveTrend core.

NOT VERIFIED AGAINST THE SOURCE. TradingView would not render the Source code
panel in the browser pane and WebFetch is blocked for that page, so unlike
AlphaTrend -- where I read the real Pine and found my version wrong -- this is
the widely-documented definition, not one I have checked line by line. Sri
should compare the signal times below against his own chart before trusting it.

What is coded, per the public description of Cipher B:

    wtChannelLen 9, wtAverageLen 12, source hlc3, wt2 = sma(wt1, 3)
      esa  = ema(hlc3, 9)
      de   = ema(abs(hlc3 - esa), 9)
      ci   = (hlc3 - esa) / (0.015 * de)
      wt1  = ema(ci, 12)
      wt2  = sma(wt1, 3)
    small green dot : wt1 crosses UP through wt2                (any level)
    big  green dot  : that cross, AND wt2 <= osLevel (-53)      (oversold)
    red dot         : wt1 crosses DOWN through wt2
Note these are Cipher B's own parameters (9/12/3), NOT LazyBear's WaveTrend
10/21 that I tested earlier -- a different indicator with a similar name.
"""

def _ema(v, n):
    out, k, a = [], 2.0 / (n + 1), None
    for x in v:
        a = x if a is None else x * k + a * (1 - k)
        out.append(a)
    return out


def _sma(v, n):
    return [sum(v[max(0, i - n + 1):i + 1]) / len(v[max(0, i - n + 1):i + 1])
            for i in range(len(v))]


def wt(bars, chan=9, avg=12, malen=3):
    src = [(x["h"] + x["l"] + x["c"]) / 3.0 for x in bars]
    esa = _ema(src, chan)
    de = _ema([abs(src[i] - esa[i]) for i in range(len(src))], chan)
    ci = [(src[i] - esa[i]) / (0.015 * de[i]) if de[i] else 0.0
          for i in range(len(src))]
    wt1 = _ema(ci, avg)
    wt2 = _sma(wt1, malen)
    return wt1, wt2


def direction(bars, chan=9, avg=12, malen=3, oversold=None):
    """+1 from a green cross until the next red cross. `oversold` None = the
    small dot (any level); a number = the big dot (wt2 must be below it)."""
    wt1, wt2 = wt(bars, chan, avg, malen)
    d = [-1] * len(bars)
    for i in range(1, len(bars)):
        up = wt1[i] > wt2[i] and wt1[i - 1] <= wt2[i - 1]
        dn = wt1[i] < wt2[i] and wt1[i - 1] >= wt2[i - 1]
        if up and (oversold is None or wt2[i] <= oversold):
            d[i] = 1
        elif dn:
            d[i] = -1
        else:
            d[i] = d[i - 1]
    return d
