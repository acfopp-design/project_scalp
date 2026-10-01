"""
master_scalper.py - Python port of the "Ultimate Master Scalper" BUY logic.

Computes, on a 1-minute OHLCV DataFrame:
  - UAlgo trend (ATR trailing-stop / SuperTrend style) + its 'Buy' flip
  - Master Long trigger (EMA9 bull + volume spike + ROC accel + above Ichimoku
    cloud + above HL-OTT + UAlgo uptrend)   [consolidation is NOT used to gate]
BUY = BOTH the UAlgo Buy flip AND the Master Long trigger on the same bar.

Only standard libs + numpy/pandas. No network, no Flask - so it is unit-testable
in isolation. The dashboard imports compute_signals() from here.

Defaults mirror the Pine inputs:
  emaLength=9, volMultiplier=1.5, rocLength=14
  Ichimoku 9/26/52, displacement=26
  HL-OTT: ott_length=2, ott_percent=0.6, ott_hlLength=10, MA='VAR'
  UAlgo: src=hl2, mult=2.0, atrLen=14, Method 1 (ta.atr)
"""
import numpy as np
import pandas as pd

# ----- basic indicators -----
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n, min_periods=1).mean()
def roc(s, n): return (s - s.shift(n)) / s.shift(n) * 100.0

def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]; pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()

def donchian(high, low, n):
    return (high.rolling(n, min_periods=1).max() + low.rolling(n, min_periods=1).min()) / 2.0

# ----- VAR (CMO-weighted variable MA), Pine Var_Func -----
def var_ma(src, length):
    s = np.asarray(src, dtype=float); n = len(s)
    valpha = 2.0 / (length + 1)
    prev = np.concatenate(([s[0]], s[:-1]))           # src[1]
    vud1 = np.where(s > prev, s - prev, 0.0); vud1[0] = 0.0
    vdd1 = np.where(s < prev, prev - s, 0.0); vdd1[0] = 0.0
    vUD = pd.Series(vud1).rolling(9, min_periods=1).sum().values
    vDD = pd.Series(vdd1).rolling(9, min_periods=1).sum().values
    denom = vUD + vDD
    vCMO = np.zeros(n)
    nzm = denom != 0
    vCMO[nzm] = (vUD[nzm] - vDD[nzm]) / denom[nzm]
    out = np.zeros(n); p = 0.0                          # nz(VAR[1]) = 0 on first bar
    for i in range(n):
        a = valpha * abs(vCMO[i])
        out[i] = a * s[i] + (1 - a) * p
        p = out[i]
    return out

# ----- one HL-OTT line (used for HOTT on highest-high, LOTT on lowest-low) -----
def ott_line(ma_input, length, percent, ma_type="VAR"):
    MA = var_ma(ma_input, length) if ma_type == "VAR" else ema(pd.Series(ma_input), length).values
    n = len(MA); fark = MA * percent * 0.01
    OTT = np.zeros(n)
    ls_p = np.nan; ss_p = np.nan; d_p = 1
    for i in range(n):
        m = MA[i]; f = fark[i]
        ls_ref = ls_p if not np.isnan(ls_p) else (m - f)
        ls = max(m - f, ls_ref) if m > ls_ref else (m - f)
        ss_ref = ss_p if not np.isnan(ss_p) else (m + f)
        ss = min(m + f, ss_ref) if m < ss_ref else (m + f)
        ss_dir = ss_p if not np.isnan(ss_p) else ss
        ls_dir = ls_p if not np.isnan(ls_p) else ls
        if d_p == -1 and m > ss_dir:   d = 1
        elif d_p == 1 and m < ls_dir:  d = -1
        else:                          d = d_p
        mt = ls if d == 1 else ss
        OTT[i] = mt * (200 + percent) / 200.0 if m > mt else mt * (200 - percent) / 200.0
        ls_p, ss_p, d_p = ls, ss, d
    return OTT

# ----- UAlgo ATR trailing-stop trend (+1 up / -1 down) -----
def ualgo_trend(df, mult=2.0, atr_len=14):
    hl2 = ((df["high"] + df["low"]) / 2.0).values
    a = atr(df, atr_len).values
    close = df["close"].values; n = len(close)
    up = hl2 - mult * a; dn = hl2 + mult * a
    trend = np.ones(n, dtype=int)
    upt_p = np.nan; dnt_p = np.nan; tr_p = 1
    for i in range(n):
        c1 = close[i - 1] if i > 0 else close[i]       # close[1]
        upt_ref = upt_p if not np.isnan(upt_p) else up[i]
        upt = max(up[i], upt_ref) if c1 > upt_ref else up[i]
        dnt_ref = dnt_p if not np.isnan(dnt_p) else dn[i]
        dnt = min(dn[i], dnt_ref) if c1 < dnt_ref else dn[i]
        if tr_p == -1 and close[i] > (dnt_p if not np.isnan(dnt_p) else dnt):   tr = 1
        elif tr_p == 1 and close[i] < (upt_p if not np.isnan(upt_p) else upt):  tr = -1
        else:                                                                  tr = tr_p
        trend[i] = tr
        upt_p, dnt_p, tr_p = upt, dnt, tr
    return trend

# ----- consolidation zones (Pine section 3); DISPLAY ONLY, never gates anything -----
def consolidation(df, prd=10, conslen=5):
    high = df["high"].values; low = df["low"].values; n = len(df)
    H_prd = df["high"].rolling(prd, min_periods=1).max().values
    L_prd = df["low"].rolling(prd, min_periods=1).min().values
    hb = high >= H_prd; lb = low <= L_prd
    H_cl = df["high"].rolling(conslen, min_periods=1).max().values
    L_cl = df["low"].rolling(conslen, min_periods=1).min().values
    dir_c = 0; pp = np.nan; pp_prev = np.nan; conscnt = 0; condhigh = np.nan; condlow = np.nan
    in_zone = np.zeros(n, bool); zhigh = np.full(n, np.nan); zlow = np.full(n, np.nan)
    for i in range(n):
        h0, l0 = hb[i], lb[i]
        if h0 and not l0: dir_c = 1
        elif l0 and not h0: dir_c = -1
        if h0 and l0: zz = high[i] if dir_c == 1 else low[i]
        elif h0: zz = high[i]
        elif l0: zz = low[i]
        else: zz = np.nan
        if not np.isnan(zz):
            if np.isnan(pp): pp = zz
            else:
                if dir_c == 1 and zz > pp: pp = zz
                if dir_c == -1 and zz < pp: pp = zz
        changed = (not np.isnan(pp)) and (np.isnan(pp_prev) or pp != pp_prev)
        if changed:
            conscnt = conscnt + 1 if (conscnt > 0 and not np.isnan(condhigh) and condlow <= pp <= condhigh) else 0
        else:
            conscnt += 1
        if conscnt >= conslen:
            if conscnt == conslen: condhigh = H_cl[i]; condlow = L_cl[i]
            else: condhigh = max(condhigh, high[i]); condlow = min(condlow, low[i])
            in_zone[i] = True; zhigh[i] = condhigh; zlow[i] = condlow
        pp_prev = pp
    return in_zone, zhigh, zlow

# ----- full signal computation -----
def compute_signals(df, *, ema_len=9, vol_mult=1.5, roc_len=14,
                    tenkan_len=9, kijun_len=26, senkouB_len=52, displacement=26,
                    ott_length=2, ott_percent=0.6, ott_hl=10,
                    u_mult=2.0, u_atr=14, window=30, drop_forming=True):
    """Returns a dict of signal series/flags. Evaluates on the last CLOSED bar
    (drops the still-forming last bar when drop_forming=True)."""
    df = df.reset_index(drop=True).copy()
    if drop_forming and len(df) > 1:
        df = df.iloc[:-1].reset_index(drop=True)       # confirm on candle close
    need = max(senkouB_len + displacement, ott_hl + 5, kijun_len + 5, u_atr + 5) + 5
    n = len(df)
    if n < need:
        return {"ok": False, "reason": f"need {need} bars, have {n}",
                "buy_now": False, "buy_active": False}

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

    # momentum group
    isBullish = (close > ema(close, ema_len)).values
    isVolSpike = (vol > sma(vol, 20) * vol_mult).values
    r = roc(close, roc_len)
    isAccUp = ((r > r.shift(1)) & (r > 0)).fillna(False).values

    # ichimoku cloud (cloud at current bar = senkou values 'displacement' bars ago)
    tenkan = donchian(high, low, tenkan_len)
    kijun = donchian(high, low, kijun_len)
    senkouA = (tenkan + kijun) / 2.0
    senkouB = donchian(high, low, senkouB_len)
    cloudTop = pd.concat([senkouA.shift(displacement), senkouB.shift(displacement)], axis=1).max(axis=1)
    cloudBot = pd.concat([senkouA.shift(displacement), senkouB.shift(displacement)], axis=1).min(axis=1)
    aboveCloud = (close > cloudTop).fillna(False).values

    # HL-OTT (HOTT uses highest-high; we compare close > HOTT[2])
    src_h = high.rolling(ott_hl, min_periods=1).max().values
    HOTT = ott_line(src_h, ott_length, ott_percent, "VAR")
    HOTT2 = pd.Series(HOTT).shift(2).values
    src_l = low.rolling(ott_hl, min_periods=1).min().values
    LOTT = ott_line(src_l, ott_length, ott_percent, "VAR")
    aboveHOTT = np.where(np.isnan(HOTT2), False, close.values > HOTT2)

    # UAlgo trend + buy flip
    trend = ualgo_trend(df, u_mult, u_atr)
    trend_prev = np.concatenate(([trend[0]], trend[:-1]))
    ualgo_buy = (trend == 1) & (trend_prev == -1)

    # Master Long trigger (NO consolidation gate, per requirement)
    long_trigger = isBullish & isVolSpike & isAccUp & aboveCloud & aboveHOTT & (trend == 1)

    # BUY = BOTH the UAlgo buy flip AND the Master Long trigger on the same bar
    buy_event = ualgo_buy & long_trigger

    in_zone, zone_high, zone_low = consolidation(df)            # display-only (never gates)

    last = n - 1
    win = slice(max(0, n - window), n)
    recent_ualgo = bool(ualgo_buy[win].any())                   # UAlgo 'Buy' flip within window
    recent_long = bool(long_trigger[win].any())                 # Master Long fired within window
    combo = ualgo_buy | long_trigger
    cwin = combo[win]; locs = np.where(cwin)[0]
    buy_age = int(len(cwin) - 1 - locs[-1]) if len(locs) else None   # bars since last signal print
    return {
        "ok": True, "bars": n,
        # BUY = BOTH signals present within the 30-bar window (pins to top while valid).
        "buy_active": recent_ualgo and recent_long,
        "buy_now": bool(buy_event[last]),                       # both on the exact last closed bar (strict)
        "ualgo_buy_recent": recent_ualgo, "long_recent": recent_long, "buy_age": buy_age,
        "long_now": bool(long_trigger[last]),
        "ualgo_buy_now": bool(ualgo_buy[last]),
        "trend_now": int(trend[last]),
        # series (numpy bool/float arrays) for Phase-4 painting:
        "buy_event": buy_event, "ualgo_buy": ualgo_buy, "long_trigger": long_trigger,
        "in_zone": in_zone, "zone_high": zone_high, "zone_low": zone_low,
        "trend": trend, "HOTT": HOTT, "LOTT": LOTT, "cloudTop": cloudTop.values, "cloudBot": cloudBot.values,
    }


if __name__ == "__main__":
    # smoke test with synthetic data
    import numpy as np
    rng = np.random.default_rng(0)
    n = 120
    base = 100 + np.cumsum(rng.normal(0.05, 0.4, n))
    df = pd.DataFrame({"open": base - 0.05, "high": base + 0.2, "low": base - 0.2,
                       "close": base, "volume": rng.integers(1e5, 5e5, n)})
    out = compute_signals(df)
    print({k: out[k] for k in ("ok", "bars", "buy_now", "buy_active", "long_now", "trend_now")})
