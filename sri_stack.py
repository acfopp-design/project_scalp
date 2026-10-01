"""
sri_stack.py -- Sri's own chart, computed from OHLC. Box 7 / E1 of PLAN.md.

Every parameter here is read off his two screenshots (MAN INDUSTRIES 30s,
GUJARAT MINERAL 1m), not guessed:

    MA/EMA Cross            MA 12 (SMA)  /  EMA 8
    CM MACD Custom          12, 26, close, 9, EMA
    RSI                     14, with an SMA 14 signal line
    Parabolic SAR           0.02, 0.02, 0.2
    Heiken Ashi SuperTrend  10, 3          (chart reads "10 3"; handoff said 10,2
                                            -- both are computed, see st10_2)
    Ichimoku                9, 26, 52, 26, 26
    VWAP                    session, not on his chart but free from the same bars

The trading engine has never read ANY of these. superstocks.py computes EMA 9
and HOTT/OTT -- a different period and a different trend indicator from what he
actually watches.

WHAT HE SAID EACH ONE MEANS (04-Sep), encoded as fields rather than prose:
  * EMA above MA is the first signal; the WIDTH of the gap is the strength
  * MACD crossover usually coincides; a cross ABOVE the zero line is strongest
  * RSI is a strength reading, not a hard gate  (his words: "majorly I use rsi
    to see strength. You can decide on that")
  * SAR below the candle is bullish; DISTANCE is strength; SAR approaching the
    candle is the early warning of a dip
  * Ichimoku: the colour of the cloud AHEAD of price is the trend forecast;
    a THICK cloud means a crossover/reversal is more likely; the distance
    between candle and cloud is trend strength

NOTHING HERE IS VALIDATED YET. This module only computes; it decides nothing.
The calibration gate (Sri checks these numbers against his own Dhan chart) comes
before any of it is measured, and measurement comes before anything ships. That
order is the whole lesson of card_lab.py.
"""
import sys
import indicators as I
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent / "Archive" / "code"))
try:
    import hott_lott as HL          # Sri's HOTT/LOTT, already ported in Archive
except Exception:
    HL = None
try:
    import Opus_indicators as OI    # consolidation zone lives here
except Exception:
    OI = None


def heiken_ashi(o, h, l, c):
    """Standard HA. First bar seeds from the real bar, as TradingView does."""
    ho, hh, hl, hc = [], [], [], []
    for i in range(len(c)):
        close = (o[i] + h[i] + l[i] + c[i]) / 4.0
        op = (o[i] + c[i]) / 2.0 if i == 0 else (ho[i - 1] + hc[i - 1]) / 2.0
        ho.append(op); hc.append(close)
        hh.append(max(h[i], op, close)); hl.append(min(l[i], op, close))
    return ho, hh, hl, hc


def supertrend(h, l, c, period=10, mult=3.0):
    """Classic SuperTrend on the series given. Returns (line[], dir[]) where
    dir is +1 up-trend (line below price) / -1 down-trend."""
    n = len(c)
    line = [None] * n
    dirn = [None] * n
    if n < period + 1:
        return line, dirn
    # Wilder ATR
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [None] * n
    seed = sum(tr[1:period + 1]) / period
    atr[period] = seed
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    fu = fl = None
    prev_dir = 1
    for i in range(period, n):
        if atr[i] is None:
            continue
        mid = (h[i] + l[i]) / 2.0
        ub, lb = mid + mult * atr[i], mid - mult * atr[i]
        fu = ub if (fu is None or ub < fu or c[i - 1] > fu) else fu
        fl = lb if (fl is None or lb > fl or c[i - 1] < fl) else fl
        if prev_dir == 1:
            d = -1 if c[i] < fl else 1
        else:
            d = 1 if c[i] > fu else -1
        dirn[i] = d
        line[i] = fl if d == 1 else fu
        prev_dir = d
    return line, dirn


def vwap_series(h, l, c, v, start=0):
    """Session VWAP. `start` is where TODAY begins -- when prior-session bars are
    prepended to warm the indicators, VWAP must still reset at the open."""
    out, pv, vv = [], 0.0, 0.0
    for i in range(len(c)):
        if i == start:
            pv = vv = 0.0
        tp = (h[i] + l[i] + c[i]) / 3.0
        pv += tp * (v[i] or 0)
        vv += (v[i] or 0)
        out.append(pv / vv if vv else None)
    return out


def compute(bars, vwap_from=0):
    """bars: list of dicts with o,h,l,c,v in time order. Returns per-bar dicts
    carrying every field, so any bar can be inspected the way his chart is."""
    o = [b["o"] for b in bars]; h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]; c = [b["c"] for b in bars]
    v = [b.get("v") or 0 for b in bars]
    n = len(c)

    ema8  = I.ema_series(c, 8)
    ma12  = I.sma_series(c, 12)
    macd  = I.macd_series(c, 12, 26, 9)
    rsi14 = [None] * n
    for i in range(n):
        if i >= 14:
            rsi14[i] = I.rsi(c[:i + 1], 14)
    rsi_sig = I.sma_series([x if x is not None else 0 for x in rsi14], 14)
    sar = I.psar_series(h, l, 0.02, 0.02, 0.2)
    cloud_top, cloud_bot = I.ichimoku_series(h, l, 9, 26, 52, 26)
    vwap = vwap_series(h, l, c, v, vwap_from)

    hao, hah, hal, hac = heiken_ashi(o, h, l, c)
    st_line, st_dir = supertrend(hah, hal, hac, 10, 3.0)          # chart: 10 3
    st2_line, st2_dir = supertrend(hah, hal, hac, 10, 2.0)        # handoff: 10 2

    # HOTT / LOTT -- Sri point 6: "if the candle crosses the upper band and turns
    # cyan, high probability of going upper and upper."
    hott = lott = [None] * n
    if HL is not None:
        try:
            hott, lott = HL.hott_lott(c)
        except Exception:
            hott = lott = [None] * n

    # Consolidation zone -- Sri point 7: price coils, then breaks out; a break
    # near the TOP of the zone is the bullish one.
    zone = [None] * n
    if OI is not None:
        for i in range(20, n):
            try:
                zone[i] = OI.consolidation_zone(h[:i+1], l[:i+1], c[:i+1])
            except Exception:
                zone[i] = None

    out = []
    for i in range(n):
        e, m = ema8[i], ma12[i]
        gap = ((e - m) / m * 100.0) if (e is not None and m) else None
        prev_e, prev_m = (ema8[i - 1], ma12[i - 1]) if i else (None, None)
        cross_up = (e is not None and m is not None and prev_e is not None
                    and prev_m is not None and prev_e <= prev_m and e > m)
        ml, ms = macd["line"][i], macd["signal"][i]
        pl, ps = (macd["line"][i - 1], macd["signal"][i - 1]) if i else (None, None)
        macd_cross_up = (None not in (ml, ms, pl, ps) and pl <= ps and ml > ms)
        ct, cb = cloud_top[i], cloud_bot[i]
        row = {
            "i": i, "hhmm": bars[i].get("hhmm"), "c": c[i],
            "ema8": e, "ma12": m, "ema_gap_pct": gap,
            "ema_cross_up": cross_up, "ema_above": (None if (e is None or m is None) else e > m),
            "macd": ml, "macd_sig": ms, "macd_hist": macd["hist"][i],
            "macd_cross_up": macd_cross_up,
            "macd_above_zero": (None if ml is None else ml > 0),
            "rsi": rsi14[i], "rsi_sig": rsi_sig[i] if i >= 27 else None,
            "sar": sar[i],
            "sar_below": (None if sar[i] is None else sar[i] < l[i]),
            "sar_dist_pct": (((c[i] - sar[i]) / c[i] * 100.0)
                             if sar[i] and c[i] else None),
            "st_dir": st_dir[i], "st_line": st_line[i],
            "st10_2_dir": st2_dir[i],
            "cloud_top": ct, "cloud_bottom": cb,
            "cloud_thick_pct": (((ct - cb) / c[i] * 100.0)
                                if (ct is not None and cb is not None and c[i]) else None),
            "above_cloud": (None if ct is None else c[i] > ct),
            "cloud_dist_pct": (((c[i] - ct) / c[i] * 100.0)
                               if (ct is not None and c[i]) else None),
            "vwap": vwap[i],
            "above_vwap": (None if vwap[i] is None else c[i] > vwap[i]),
            "hott": hott[i], "lott": lott[i],
            "hott_state": (None if (hott[i] is None or lott[i] is None)
                           else ("UP" if c[i] > hott[i]
                                 else ("DOWN" if c[i] < lott[i] else "FLAT"))),
            "hott_cross_up": (bool(i and hott[i] is not None and hott[i-1] is not None
                                   and c[i-1] <= hott[i-1] and c[i] > hott[i])),
            "in_zone": zone[i] is not None,
            "zone_top": (zone[i] or {}).get("top"),
            "zone_bottom": (zone[i] or {}).get("bottom"),
            # broke out of a coil, and did it near the TOP of that coil
            "zone_break_up": (bool(i and zone[i-1] and not zone[i]
                                   and zone[i-1].get("top") and c[i] >= zone[i-1]["top"])),
        }
        out.append(row)
    return out
