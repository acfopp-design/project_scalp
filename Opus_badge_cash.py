"""
Opus_badge_cash.py -- CASH pipeline scorer + badge logic (V3.5 / Opus).

NOTE: This is an INDEPENDENT COPY of the badge logic (badge_fno.py is identical
by design) so the Cash and F&O agents hold separate state and never interfere.
"""
from datetime import datetime
import Opus_indicators as I
import Opus_engine as engine

IST = engine.IST
MIN_DAY_MOVE = 0.5         # catch ignition early (was 1.5)
MAX_DAY_MOVE = 17.0
FREEZE_MIN_MOVE = 2.0
FREEZE_MIN_CYCLES = 2
VOL_IG = 1.8
VOL_FRESH = 1.2
WIN_BARS = 15
MIN_WIN_TURNOVER = 1_500_000
MIN_WIN_RANGE = 0.3
WINDOW = 18                  # show last 18 one-min candles (bigger candles)
# ---- Layer-1 turnover filters (price-neutral, rupees) ----
DAY_TURNOVER_MIN = 10_000_000      # #2: drop day turnover < Rs 1 Cr
FIRST2_TURNOVER_MIN = 2_500_000    # #3: from 9:17, drop first-2-min turnover < Rs 25L


BULL_SCORE_MIN = 8.0               # V3.5: A/B/C composite that counts as bullish
BULL_M_MIN = 1.5                   # V3.5: Fable-style M-score that counts as bullish
# ---- V3.5 combined ENTRY badge tunables (edit these to loosen/tighten) ----
ENTRY_WIN_BARS = 3        # cross & igniting must occur within this many 1-min bars
ENTRY_ANG_MIN = 10        # fresh-quality: min EMA-9 angle (deg)
ENTRY_ANG_MAX = 55        # fresh-quality: max EMA-9 angle (deg)
ENTRY_VOL_X = 1.2         # fresh-quality: min volume surge multiple
ENTRY_BLOCK_OVERBOUGHT = True   # fresh-quality: block when RSI>75
FREEZE_HOLD_CYCLES = 4          # (legacy) below-VWAP grace; superseded by directional release below
FREEZE_GIVEBACK_PCT = 1.5       # release FROZEN if price gives back this many day% points from its high
FREEZE_DROP_ANG = -5.0          # release FROZEN when EMA-9 angle falls to/below this (clear downturn)
# ---- V3.5 enhancement: UT-Bot, consolidation, freshness tunables ----
TREND_SENS = 2.0         # UAlgo trend sensitivity (Multiplier 0.5-5)
TREND_ATR = 14           # UAlgo ATR length
CLOUD_LEN = 10           # UAlgo cloud MA length (EMA of high/low)
TREND_SL = 2.0           # UAlgo stop-loss percent (TP/SL config; 0 = off)
CONS_LOOKBACK = 10       # consolidation-zone loopback (prd), TV default 10
CONS_MAXRANGE = 1.5      # consolidation band max range (% of price)
FRESH_WIN = 3            # a cross/flip counts as "fresh" within this many bars
FRESH_ANG_MIN = 20       # fresh MA/EMA cross needs EMA-9 angle >= this (deg)
CONS_MINLEN = 5          # consolidation zone: min bars in a coil box
# ---- V3.5 FIRST-SURGE (opening-window) detector ----
# Catches opening momentum where the EMA9xSMA12 cross ALREADY happened in a
# prior session (no fresh cross today). Fires FRESH/ENTRY off an established
# uptrend + real surge instead of requiring the crossover bar.
FIRST_SURGE_ON = True     # master switch
FIRST_SURGE_BARS = 5      # only active for the first N one-min bars of the session (~first 5 min)
FIRST_SURGE_MIN_PCT = 1.0 # min day% move to count as a surge
FIRST_SURGE_VOL_X = 1.2   # min volume surge multiple


# ---- live config loader: reads Opus_config.json every cycle (mtime-cached), no restart ----
import os as _os, json as _json
_CFG_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "Opus_config.json")
_cfg_cache = {"mtime": None, "data": {}}
def _cfg():
    try:
        m = _os.path.getmtime(_CFG_PATH)
        if m != _cfg_cache["mtime"]:
            with open(_CFG_PATH, encoding="utf-8") as _f:
                _cfg_cache["data"] = _json.load(_f)
            _cfg_cache["mtime"] = m
    except Exception:
        pass
    return _cfg_cache["data"]


# ================= V3.5 (Opus) helpers =================
def _session_vwap(h, l, c, v, tb):
    """Intraday VWAP over today's bars (typical price x volume)."""
    th, tl, tc, tv = h[-tb:], l[-tb:], c[-tb:], v[-tb:]
    num = den = 0.0
    for hi, li, ci, vi in zip(th, tl, tc, tv):
        if vi:
            num += ((hi + li + ci) / 3.0) * vi
            den += vi
    return (num / den) if den else None


def _m_score(c, vsurge, atr):
    """Fable-style momentum: 3-min move in volatility units x RVOL x (0.5+efficiency)."""
    if len(c) < 4 or not atr or atr <= 0:
        return 0.0
    vbm = min(abs(c[-1] - c[-4]) / atr, 3.0)
    rv = min((vsurge or 0) / 1.5, 2.5)
    if (vsurge or 0) < 0.8:
        rv *= 0.3
    seg = c[-11:] if len(c) >= 11 else c
    signal = abs(seg[-1] - seg[0])
    noise = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    er = (signal / noise) if noise else 0.0
    return round(vbm * rv * (0.5 + er), 2)


def _dip_state(o, h, l, c, v, ema9_last, vwap):
    """REVERSAL = below EMA-9 & VWAP with lower-lows + expanding down-vol; DIP-OK = pullback still holding."""
    if len(c) < 4:
        return None
    pull = c[-1] < max(c[-3:-1])
    holds = (ema9_last is not None and c[-1] >= ema9_last) or (vwap is not None and c[-1] >= vwap)
    below = (ema9_last is not None and c[-1] < ema9_last) and (vwap is None or c[-1] < vwap)
    lower_low = l[-1] < l[-2] < l[-3]
    down_vol_exp = (c[-1] < o[-1]) and (v[-1] or 0) >= (v[-2] or 0)
    if pull and below and lower_low and down_vol_exp:
        return "REVERSAL"
    if pull and holds:
        return "DIP-OK"
    return None


class CashScorer:
    PERSIST_CYCLES = 4         # keep IGNITING/FRESH badge lit ~4 cycles (~2 min) after it fires

    def __init__(self):
        self.prev_ign = {}     # symbol -> previous raw igniting (debounce)
        self.freeze = {}       # symbol -> {frozen, cycles, signal}
        self.persist = {}      # symbol -> {ign:int, fresh:int} (badge persistence)
        self.bull = {}         # V3.5: symbol -> first-bullish memory
        self.crosst = {}       # V3.5: symbol -> HH:MM:SS of first EMA/MACD cross-up
        self.entry_bars = {}   # V3.5: symbol -> {cross,ign,fresh} last-fire bar times

    # ---------- per-stock scoring on already-fetched candles ----------
    def compute_row(self, name, sid, bundle):
        cd = bundle["candles"]
        o, h, l, c, v = cd["open"], cd["high"], cd["low"], cd["close"], cd["volume"]
        ts = cd["timestamp"]
        if len(c) < 20:
            return {"name": name, "sid": sid, "error": "few_bars"}
        px = c[-1]
        a = I.atr(h, l, c) or 1e-9
        ema9 = I.ema_series(c, 9)
        sma12 = I.sma_series(c, 12)
        mac = I.macd_series(c)
        rsi = I.rsi(c)
        vsma = I.sma(v, 12)
        vsurge = (v[-1] / vsma) if vsma else None
        ang = I.angle_deg(ema9, a, 3)

        prev_close = bundle.get("prev_close")
        day_pct = round((px / prev_close - 1) * 100, 2) if prev_close else None

        ema9_gt = bool(ema9[-1] and sma12[-1] and ema9[-1] > sma12[-1])
        ema9_cross = bool(ema9[-2] and sma12[-2] and ema9[-2] <= sma12[-2] and ema9_gt)
        macd_cross = bool(mac["line"][-1] is not None and mac["signal"][-1] is not None
                          and mac["line"][-2] is not None and mac["signal"][-2] is not None
                          and mac["line"][-2] <= mac["signal"][-2] and mac["line"][-1] > mac["signal"][-1])
        macd_pos = bool(mac["line"][-1] is not None and mac["line"][-1] > 0)

        ang_hist = [I.angle_deg(ema9[:k + 1], a, 3) for k in range(len(ema9) - 5, len(ema9))]
        _va = [x for x in ang_hist if x is not None]
        ang_rising = bool(len(_va) >= 2 and _va[-1] > _va[0])
        _a3 = [x for x in ang_hist[-3:] if x is not None]
        ang_sust30 = bool(len(_a3) >= 3 and all(x > 30 for x in _a3))
        ang_nondecl = bool(len(_a3) >= 2 and _a3[-1] >= _a3[0])

        _rv = v[-WIN_BARS:] if len(v) >= WIN_BARS else v
        _rh = h[-WIN_BARS:] if len(h) >= WIN_BARS else h
        _rl = l[-WIN_BARS:] if len(l) >= WIN_BARS else l
        active = (sum(1 for x in _rv if x and x > 0) / len(_rv)) if _rv else 0.0
        win_turn = sum(x for x in _rv if x) * px
        win_range = ((max(_rh) - min(_rl)) / px * 100) if (px and _rh and _rl) else 0.0
        liquid_now = bool(active >= 0.6 and win_turn >= MIN_WIN_TURNOVER)
        # recent (last 3 min) turnover -> drops stocks dead RIGHT NOW (e.g. RACLGEAR)
        recent3_turn = sum(vi * ci for vi, ci in zip(v[-3:], c[-3:]) if vi and ci)
        # dead/illiquid (dash-candle) stocks -> dropped from the board
        stagnant = bool(active < 0.6 or win_turn < MIN_WIN_TURNOVER
                        or win_range < MIN_WIN_RANGE or recent3_turn < 2_500_000)
        # ---- Layer-1 TURNOVER filters (today's bars only) ----
        tb = bundle.get("today_bars") or len(c)
        tv, tc = v[-tb:], c[-tb:]
        day_turnover = sum(vi * ci for vi, ci in zip(tv, tc) if vi and ci)
        first2_turn = sum(vi * ci for vi, ci in zip(tv[:2], tc[:2]) if vi and ci)
        day_vol = sum(x for x in tv if x)        # today's cumulative share volume
        # #2 day-turnover gate only. (Filter #3 first-2-min removed: it dropped
        # stocks quiet at open but igniting later — the recent-window 'stagnant'
        # check already guarantees the stock is liquid NOW, so late igniters show.)
        liq_drop = bool(day_turnover < DAY_TURNOVER_MIN)

        _N = 6
        base_hi = max(h[-_N - 1:-1]) if len(h) > _N else None
        breakout = bool(base_hi is not None and px > base_hi)

        eligible = bool(day_pct is not None and MIN_DAY_MOVE <= day_pct < MAX_DAY_MOVE)
        up_bar = bool(px > o[-1])
        vol_ig = bool(vsurge is not None and vsurge >= VOL_IG)
        vol_fr = bool(vsurge is not None and vsurge >= VOL_FRESH)

        raw_ign = bool(eligible and liquid_now and vol_ig and breakout and up_bar)
        edge = bool(raw_ign and not self.prev_ign.get(name, False))
        self.prev_ign[name] = raw_ign

        overbought = bool(rsi is not None and rsi > 75)
        fresh_now = bool(eligible and (ema9_cross or macd_cross) and vol_fr and up_bar
                         and ang is not None and 10 <= ang <= 50 and not overbought)
        # ---- FIRST-SURGE (opening-window) detector: no crossover bar required ----
        # If the EMA9xSMA12 cross already happened in a PRIOR session, EMA9 is
        # already > SMA12 at the open and 'fresh_now' can never fire. During the
        # first N bars, fire off an established uptrend (ema9_gt) + real surge
        # (day% move, volume, above-VWAP, positive angle) instead.
        _fs = _cfg()
        _fs_on   = bool(_fs.get("FIRST_SURGE_ON", FIRST_SURGE_ON))
        _fs_bars = int(_fs.get("FIRST_SURGE_BARS", FIRST_SURGE_BARS))
        _fs_pct  = _fs.get("FIRST_SURGE_MIN_PCT", FIRST_SURGE_MIN_PCT)
        _fs_vol  = _fs.get("FIRST_SURGE_VOL_X", FIRST_SURGE_VOL_X)
        _fs_bloc = _fs.get("ENTRY_BLOCK_OVERBOUGHT", ENTRY_BLOCK_OVERBOUGHT)
        _fs_vwap = _session_vwap(h, l, c, v, tb)
        surge_now = bool(_fs_on and eligible and tb <= _fs_bars
                         and ema9_gt and up_bar
                         and (vsurge or 0) >= _fs_vol
                         and (_fs_vwap is None or px >= _fs_vwap)
                         and ang is not None and ang > 0
                         and (day_pct or 0) >= _fs_pct
                         and not (_fs_bloc and overbought))
        # ---- badge persistence: keep lit a few cycles after firing ----
        pst = self.persist.setdefault(name, {"ign": 0, "fresh": 0})
        if raw_ign:   pst["ign"] = self.PERSIST_CYCLES
        if fresh_now or surge_now: pst["fresh"] = self.PERSIST_CYCLES
        edge = pst["ign"] > 0
        fresh = pst["fresh"] > 0
        pst["ign"] = max(0, pst["ign"] - 1)
        pst["fresh"] = max(0, pst["fresh"] - 1)
        watch = bool((ang is not None and ang >= 50 and not ang_rising)
                     or (rsi is not None and rsi > 78))

        if not eligible:        stage = "ineligible"
        elif raw_ign and not watch: stage = "igniting"
        elif fresh:             stage = "fresh"
        elif watch or (ang is not None and ang > 65): stage = "extended"
        else:                   stage = "running"

        # A/B/C (gated)
        if not eligible:
            sa = sb = sc = 0.0
        else:
            sa = 0.0
            if ang is not None:
                if 30 <= ang <= 45: sa += 6
                elif 20 <= ang < 30: sa += 4
                elif 45 < ang <= 60: sa += 4
                elif 10 <= ang < 20: sa += 2
            if ang_sust30: sa += 4
            sa = min(10.0, sa)
            sb = 0.0
            if raw_ign: sb += 5
            if fresh: sb += 5
            if ema9_cross: sb += 2
            if macd_cross: sb += 2
            if ang_rising: sb += 1
            sb = min(10.0, sb)
            sc = 0.0
            if macd_cross and macd_pos: sc += 4
            elif macd_pos: sc += 2
            if ema9_gt: sc += 2
            vs = vsurge or 0
            if liquid_now:
                if vs >= 1.5: sc += 3
                elif vs >= 1.0: sc += 1
            if (mac["hist"][-1] or 0) > 0: sc += 1
            sc = min(10.0, sc)

        # ---------- V3.5 (Opus): M-score, VWAP, DIP/REVERSAL, timed cross, combined bullish ----------
        vwap = _session_vwap(h, l, c, v, tb)
        m_score = _m_score(c, vsurge, a)
        dip_state = _dip_state(o, h, l, c, v, ema9[-1], vwap)
        now_hms = datetime.now(IST).strftime("%H:%M:%S")
        cross_now = bool((ema9_cross or macd_cross) and up_bar and eligible)
        if cross_now and name not in self.crosst:
            self.crosst[name] = now_hms
        cross_time = self.crosst.get(name)
        # ---- V3.5 COMBINED "ENTRY" badge ----
        # Fires when a CROSS event and an IGNITING breakout occur within a 3-bar window
        # AND the FRESH-quality gate holds right now (angle 10-55, vol>=1.2x, not overbought,
        # up-bar, not a reversal). The fresh 'conditions' are used live rather than requiring
        # the fresh latch to fire on the shallow-angle cross bar (which almost never coincides).
        latest_bar = int(ts[-1]) if ts and ts[-1] else None
        eb = self.entry_bars.setdefault(name, {"cross": None, "ign": None})
        if latest_bar:
            # latch cross on the raw crossover event (the crossover bar is often red,
            # so gating it by up_bar would miss it); quality is enforced at fire time.
            if ema9_cross or macd_cross: eb["cross"] = latest_bar
            if raw_ign:                  eb["ign"]   = latest_bar
        _c = _cfg()
        _win_bars = _c.get("ENTRY_WIN_BARS", ENTRY_WIN_BARS)
        _ang_min  = _c.get("ENTRY_ANG_MIN", ENTRY_ANG_MIN)
        _ang_max  = _c.get("ENTRY_ANG_MAX", ENTRY_ANG_MAX)
        _vol_x    = _c.get("ENTRY_VOL_X", ENTRY_VOL_X)
        _block_ob = _c.get("ENTRY_BLOCK_OVERBOUGHT", ENTRY_BLOCK_OVERBOUGHT)
        ENTRY_WIN = _win_bars * 60   # window in seconds
        fresh_quality = bool(eligible and up_bar and ang is not None
                             and _ang_min <= ang <= _ang_max
                             and (vsurge or 0) >= _vol_x
                             and not (_block_ob and overbought)
                             and dip_state != "REVERSAL")
        entry_ready = False
        if latest_bar and eb["cross"] and eb["ign"]:
            span = abs(eb["cross"] - eb["ign"])              # cross & ignition clustered
            age = latest_bar - max(eb["cross"], eb["ign"])   # cluster is recent
            entry_ready = bool(span <= ENTRY_WIN and age <= ENTRY_WIN and fresh_quality)
        if surge_now:                     # opening-window surge = an ENTRY, sans crossover bar
            entry_ready = True
        pst.setdefault("entry", 0)
        if entry_ready: pst["entry"] = self.PERSIST_CYCLES
        entry = pst["entry"] > 0
        pst["entry"] = max(0, pst["entry"] - 1)
        score_val = round(sa + sb + sc, 1)
        bull_abc = bool(eligible and score_val >= BULL_SCORE_MIN)
        bull_m = bool(eligible and m_score >= BULL_M_MIN)
        bullish = bool(eligible and (surge_now or cross_now or raw_ign or fresh or bull_abc or bull_m))
        cur_reason = ("surge" if surge_now else "cross" if cross_now else "ignite" if raw_ign
                      else "fresh" if fresh else "score" if bull_abc else "mscore" if bull_m else None)
        if bullish and name not in self.bull:
            self.bull[name] = {"t": now_hms, "reason": cur_reason, "pct0": day_pct, "vol0": round(day_vol)}
        bt = self.bull.get(name)

        # ---------- V3.5 ENHANCEMENT: UT-Bot, consolidation zone, freshness ----------
        _cc = _cfg()
        trend_sens = _cc.get("TREND_SENS", TREND_SENS)
        trend_atr = int(_cc.get("TREND_ATR", TREND_ATR))
        cloud_len = int(_cc.get("CLOUD_LEN", CLOUD_LEN))
        cons_lb = int(_cc.get("CONS_LOOKBACK", CONS_LOOKBACK))
        cons_mr = _cc.get("CONS_MAXRANGE", CONS_MAXRANGE)
        cons_ml = int(_cc.get("CONS_MINLEN", CONS_MINLEN))
        fresh_win = int(_cc.get("FRESH_WIN", FRESH_WIN))
        fresh_ang = _cc.get("FRESH_ANG_MIN", FRESH_ANG_MIN)
        ut = I.ualgo_trend(o, h, l, c, trend_sens, trend_atr)  # UAlgo trend (replaces UT-Bot)
        ut_in_buy = ut["in_buy"]
        ema_hi = I.ema_series(h, cloud_len)
        ema_lo = I.ema_series(l, cloud_len)
        cz_list = I.consolidation_zones(h, l, c, cons_lb, cons_ml)  # prd, conslen

        def _recent_true(arr, k):
            for j in range(len(arr) - 1, max(-1, len(arr) - 1 - k), -1):
                if arr[j]:
                    return len(arr) - 1 - j
            return None

        def _recent_cross(aa, bb, k):
            for j in range(len(aa) - 1, max(0, len(aa) - 1 - k), -1):
                if (aa[j - 1] is not None and bb[j - 1] is not None
                        and aa[j] is not None and bb[j] is not None
                        and aa[j - 1] <= bb[j - 1] and aa[j] > bb[j]):
                    return len(aa) - 1 - j
            return None

        ut_buy_ago = _recent_true(ut["buy"], fresh_win)
        ma_ago = _recent_cross(ema9, sma12, fresh_win)
        macd_ago = _recent_cross(mac["line"], mac["signal"], fresh_win)
        fresh_ma = bool(ma_ago is not None and ang is not None and ang >= fresh_ang)
        fresh_macd = bool(macd_ago is not None)
        fresh_macd_above0 = bool(fresh_macd and macd_pos)
        fresh_ut = bool(ut_buy_ago is not None)
        fresh_any = bool(fresh_ma or fresh_macd or fresh_ut)
        _bagos = [x for x in (ma_ago, macd_ago, ut_buy_ago) if x is not None]
        fresh_bars_ago = min(_bagos) if _bagos else None

        _s = max(0, len(c) - WINDOW)
        ut_buy_idx = [i - _s for i in range(_s, len(c)) if ut["buy"][i]]
        ut_sell_idx = [i - _s for i in range(_s, len(c)) if ut["sell"][i]]
        # map absolute zone bar-indices into the shown WINDOW; keep zones overlapping it
        czones = []
        for z in cz_list:
            if z["e"] < _s:
                continue
            czones.append({"s": max(0, z["s"] - _s), "e": z["e"] - _s,
                           "top": z["top"], "bottom": z["bottom"]})
        cons_now = bool(any(z["s"] <= len(c) - 1 <= z["e"] for z in cz_list))
        chart_payload = self._chart(ts, o, h, l, c, ema9, sma12, mac)
        chart_payload["czones"] = czones
        chart_payload["utBuy"] = ut_buy_idx
        chart_payload["utSell"] = ut_sell_idx
        chart_payload["cloudHi"] = [round(ema_hi[i], 3) if ema_hi[i] is not None else None for i in range(_s, len(c))]
        chart_payload["cloudLo"] = [round(ema_lo[i], 3) if ema_lo[i] is not None else None for i in range(_s, len(c))]
        def _cstate(i):
            mi = mac["line"][i]; mp = mac["line"][i - 1] if i > 0 else None
            if mi is None or mp is None:
                return None
            rising = mi > mp
            if mi > 0:
                return 0 if rising else 1     # 0=Positive Uptrend, 1=Positive Downtrend
            return 3 if rising else 2         # 2=Negative Uptrend, 3=Negative Downtrend
        chart_payload["cloudState"] = [_cstate(i) for i in range(_s, len(c))]

        return {
            "name": name, "sym": name, "sid": sid, "error": None,
            "price": round(px, 2), "day_pct": day_pct,
            "ema_angle": round(ang, 1) if ang is not None else None,
            "vol_surge_x": round(vsurge, 2) if vsurge else None,
            "eligible": eligible, "igniting": edge, "igniting_raw": raw_ign,
            "ema9_cross_up": ema9_cross, "macd_cross_up": macd_cross, "macd_line_pos": macd_pos,
            "fresh": fresh, "frozen": False, "stage": stage, "stagnant": stagnant,
            "liquid_now": liquid_now, "liq_drop": liq_drop,
            "day_turnover": round(day_turnover), "first2_turn": round(first2_turn),
            "day_vol": round(day_vol),
            "ang_nondecl": ang_nondecl,
            "sa": sa, "sb": sb, "sc": sc, "score": score_val,
            "m_score": m_score, "vwap": round(vwap, 2) if vwap else None,
            "dip_state": dip_state, "cross_now": cross_now, "cross_time": cross_time,
            "entry": entry, "surge": surge_now,
            "bullish": bullish, "bull_reason": cur_reason,
            "bull_time": bt["t"] if bt else None,
            "bull_first_reason": bt["reason"] if bt else None,
            "ut_in_buy": ut_in_buy, "cons": cons_now,
            "fresh_ma": fresh_ma, "fresh_macd": fresh_macd, "fresh_macd_above0": fresh_macd_above0,
            "fresh_ut": fresh_ut, "fresh_any": fresh_any, "fresh_bars_ago": fresh_bars_ago,
            "chart": chart_payload,
        }

    def _chart(self, ts, o, h, l, c, ema9, sma12, mac):
        n = len(c); s = max(0, n - WINDOW)          # last 30 bars
        def w(arr): return [None if (i >= len(arr) or arr[i] is None) else round(arr[i], 3) for i in range(s, n)]
        # unix seconds + IST offset so lightweight-charts' time axis shows IST HH:MM
        t = [int(ts[i]) + 19800 if i < len(ts) and ts[i] else None for i in range(s, n)]
        out = {"t": t, "o": w(o), "h": w(h), "l": w(l), "c": w(c),
               "ema9": w(ema9), "sma12": w(sma12),
               "macdLine": w(mac["line"]), "macdSignal": w(mac["signal"]), "macdHist": w(mac["hist"])}
        return out

    # ---------- ranking + freeze (per-cycle) ----------
    def rank(self, rows):
        # V3.5 FIX #3: shield a currently-FROZEN (surging) stock from the stagnant/liquidity
        # drop gates so it never disappears mid-surge; normal names still obey the gates.
        live = [r for r in rows if not r.get("error") and r.get("eligible")
                and r.get("ema_angle") is not None
                and ((not r.get("stagnant") and not r.get("liq_drop"))
                     or self.freeze.get(r.get("name"), {}).get("frozen"))]
        _c = _cfg()
        hold_cycles = _c.get("FREEZE_HOLD_CYCLES", FREEZE_HOLD_CYCLES)
        giveback_pct = _c.get("FREEZE_GIVEBACK_PCT", FREEZE_GIVEBACK_PCT)
        drop_ang = _c.get("FREEZE_DROP_ANG", FREEZE_DROP_ANG)
        now = set()
        for r in live:
            nm = r["name"]; now.add(nm)
            st = self.freeze.setdefault(nm, {"frozen": False, "cycles": 0, "peak": 0.0, "weak": 0})
            pct = r.get("day_pct") or 0
            ang = r.get("ema_angle")
            price = r.get("price"); vwap = r.get("vwap")
            # arm FROZEN: sustained strong move (day% >= 2, EMA-9 up & not declining)
            sustaining = bool(pct >= FREEZE_MIN_MOVE and ang is not None
                              and ang > 0 and r.get("ang_nondecl"))
            if not st["frozen"]:
                if sustaining:
                    st["cycles"] += 1
                    if st["cycles"] >= FREEZE_MIN_CYCLES:
                        st["frozen"] = True; st["peak"] = pct; st["weak"] = 0
                else:
                    st["cycles"] = 0
            else:
                # FROZEN = still going UP, or just COILING / accumulating above support.
                # Release the moment it TRULY turns DOWN; keep holding a flat coil.
                st["peak"] = max(st.get("peak", 0.0), pct)
                giveback = st["peak"] - pct                       # day% points given back from high
                below_vwap = bool(vwap and price and price < vwap)
                # genuine downturn (NOT a coil): EMA-9 clearly sloping down, OR sloping down
                # while it has lost VWAP, OR an active REVERSAL (lower-lows below support).
                rolling_over = bool(
                    r.get("dip_state") == "REVERSAL"
                    or (ang is not None and ang <= drop_ang)
                    or (ang is not None and ang < 0 and below_vwap))
                # A flat coil (angle ~0, holding) matches none of these -> stays frozen.
                if pct < FREEZE_MIN_MOVE or giveback >= giveback_pct or rolling_over:
                    st["frozen"] = False; st["cycles"] = 0; st["peak"] = 0.0; st["weak"] = 0
            r["frozen"] = st["frozen"]
            if r["frozen"] and not r.get("bullish"):
                r["bullish"] = True
                b = self.bull.get(nm)
                if not b:
                    b = {"t": datetime.now(IST).strftime("%H:%M:%S"), "reason": "frozen",
                         "pct0": r.get("day_pct"), "vol0": r.get("day_vol")}
                    self.bull[nm] = b
                r["bull_time"] = b["t"]; r["bull_first_reason"] = b["reason"]
        for k in [k for k in self.freeze if k not in now]:
            self.freeze.pop(k, None)
        # shuffle every cycle: frozen first, EMA-9>0 above EMA-9<=0, then day% desc, score desc
        live.sort(key=lambda r: (not r["frozen"], (r.get("ema_angle") or 0) <= 0,
                                 -(r.get("day_pct") or 0), -(r.get("score") or 0)))
        return live
