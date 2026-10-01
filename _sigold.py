"""
signal_sim.py -- a SIGNAL-driven backtest, not a card-driven one.

WHY THIS EXISTS (04-Sep, found from Sri's own charts)
  The live engine only ever enters at the instant a stock is FIRST CARDED. If
  the carding gates do not happen to fire at the second the chart says go, the
  trade is never considered again.

    WOCKPHARMA (Wockhardt) -- Sri's setups at 09:18, 09:37 and 11:31. The board
      did not card it until 13:24:56. Never traded.
    TEJASNET -- carded once at 09:17, entered, stopped -Rs 1,840 at 09:19. His
      09:55 setup (+2.18%) was never taken: the card was no longer "new" and the
      re-entry cooldown was running.

  Both stocks are in his watchlist, and both DID produce his signal on the tape:
  TEJASNET fired MACD cross-up + RSI>50 + above VWAP at 09:53:30, two minutes
  before his 09:55 entry; WOCKPHARMA at 11:44:30.

  So the miss is structural. This engine fixes the structure: it watches a fixed
  universe every 30 seconds and enters on the SIGNAL, whenever it appears.

RULES
  universe   MyWatchlist + every symbol carded that day (whatever has a tape)
  entry      MACD line crosses above its signal, AND RSI > RSI_MIN, AND price
             above session VWAP, AND SuperTrend up  (each switchable)
  exit       stop -1%; past +2% ride while making new highs and exit on a 1%
             giveback from the peak -- the rule shipped 04-Sep
  sizing     risk-based, same as live: risk 1.5% of capital per trade against
             the stop distance, capped by MIN/MAX position and total exposure
  charges    paper_engine.charges -- Dhan intraday, both legs, every component

NO LOOK-AHEAD
  Indicators at bar i are computed from bars[0..i] only. A decision taken on
  bar i is FILLED AT THE OPEN OF BAR i+1, because bar i's close is not known
  until it closes. Stops and targets are then checked against bar highs/lows
  from i+1 onward, stop first.
"""
import json, sys
from collections import defaultdict
from pathlib import Path

import entry_lab as E
import sri_stack as S
import paper_engine as PE

HERE = Path(__file__).resolve().parent

CAPITAL      = 100_000.0
LEVERAGE     = 5.0
RISK_PCT     = 1.5
MAX_RISK_PCT = 8.0
MIN_POS      = 60_000.0
MAX_POS_PCT  = 40.0
STOP_PCT     = -1.0
TARGET_PCT   = 2.0
GIVE_PCT     = 1.0
TIME_CAP     = 1800
MIN_PRICE    = 100.0
RSI_MIN      = 50.0
# --- exhaustion filter (Sri, 04-Sep, on the MTAR 11:12 entry) --------------
# "The stock price you entered is at peak... by that time volume buying
# ignition engine would have exhausted."  MACD crossing up says a move HAPPENED;
# it says nothing about whether it is spent. On MTAR the three entries he
# called good or lucky had RSI 51/57/50 and sat 0.08-0.54% above VWAP. The one
# he called a blunder had RSI 93 and was stretched 1.60% above VWAP.
RSI_MAX      = 75.0     # above this the ignition is already spent
VWAP_MAX_PCT = 99.0     # OFF. Fitted to MTAR at 1.0% and it was wrong: TEJASNET
                        # gapped and traded 1-2% above VWAP all day, so a 1.0%
                        # cap rejected all 33 of its signals including Sri's own
                        # 09:55 entry. Absolute distance from VWAP is not
                        # comparable across stocks or days; RSI is normalised
                        # and does the same job without the overfit.
# --- dead-position exit ----------------------------------------------------
# "The stock nearly dead at 10:47, wondering what made you hold."  There was no
# rule to exit a position that had simply stopped working -- only -1%, +2%, or
# the 30-minute clock. So it sat there.
# --- giveback protection BELOW the target ---------------------------------
# Sri, on TEJASNET 10:53: "why is exit till 11:17:30, you should have exited 5
# min back."  The position peaked +1.39% at 11:10:30 and exited -0.33% at
# 11:17:30. Nothing acted in between: the +2% target was never reached so the
# ride-and-trail never armed, -1% was never hit, and the dead-exit test required
# price to be back at entry. So every move that peaks under +2% and rolls over
# was given back in FULL. This is his Box 9 complaint -- "profits are achieved
# and it still holds" -- and it had no rule against it.
# --- MACD cross must be MEANINGFUL, not just a cross -----------------------
# TEJASNET 04-Sep: all six losing trades entered on a histogram of 0.02-0.10 --
# noise-level crossings -- while the two winners came from real crosses early in
# a move. An absolute threshold cannot work across a Rs 600 and a Rs 7,000
# stock, so measure the histogram against the stock's OWN recent histogram size.
# FIRST ATTEMPT WAS INVERTED: at a crossover the histogram is ~0 by definition,
# so testing it against the stock's AVERAGE histogram rejected every good cross
# in a trending stock (avg 0.999, threshold 0.350, actual 0.057) and accepted
# noise crosses in a dead one (avg 0.067, threshold 0.024, actual 0.026).
# Exactly backwards. What separates a real cross from a fake one causally is
# whether the histogram is EXPANDING and MACD is RISING into it.
# --- is the stock ALIVE right now? -----------------------------------------
# Sri: "if you could identify the stock at that timing is dead, why take the
# risk of entering."  On TEJASNET 04-Sep the three dead losses all had a prior
# 30-minute ATR of 0.12-0.13% of price, against 0.20-0.34% for every winner.
#
# The threshold is anchored to COST, not fitted: a round trip costs about
# 0.067% of position value in charges. If the stock's typical 30-second bar
# spans less than a few times that, it cannot pay for a scalp no matter how
# good the signal looks -- the move has to clear the toll before it clears
# anything else.
COST_PCT     = 0.067    # measured round-trip charges as % of position value
MIN_ATR_X    = 2.5      # recent ATR must be at least this many round trips
ATR_LOOKBACK = 60       # bars (30 minutes) used to measure it
HIST_RISE_N  = 3        # bars over which the histogram must be growing
MACD_RISE    = True     # and MACD itself must be higher than it was N bars ago
# --- stretch, measured against the stock's own range ------------------------
# The 11:08 entry was +2.27% above VWAP, the most stretched point of the day,
# straight after a spike. An absolute cap was tried and was wrong (it rejected
# every TEJASNET signal while passing MTAR's). Relative to the stock's own
# day range so far, it travels.
MAX_STRETCH_X = 0.55    # distance above VWAP, as a fraction of the day's range
ARM_PCT      = 0.35     # once a position has been this far ahead...
GIVE_BACK    = 0.35     # ...exit if it gives back this much from its peak
STALE_SEC    = 420.0    # no new high for this long...
STALE_GIVE   = 0.0      # ...and not above entry -> get out
ENTRY_FROM   = "09:16:00"
ENTRY_TO     = "14:30:00"
SQUARE_OFF   = "15:15:00"
REENTRY_SEC  = 120   # a fresh expansion is a fresh signal; 5 min locked
                     # us out of NIACL's move one minute after exiting it
USE_ST       = True
USE_VWAP     = True
USE_MACD     = True

# ============================ SRI'S OWN STRATEGY ============================
# Entry, exactly as he wrote it:
#   1. EMA 8 crosses above MA 12          -- his first signal
#   2. MACD crossover, usually at the same time; above the zero line = strongest
#   3. RSI between 50 and 65
#   4. SAR dot below the candle; a bigger gap means stronger
#   5. HOTT/LOTT: price above the upper band (the cyan state)
#   6. Consolidation zone: a break out of the coil near its top
# Exit, exactly as he wrote it:
#   a candle CLOSES below the EMA -> get out
#   (a 1% stop is kept underneath purely as a safety net, since his written
#    rule has no loss limit and one bad gap would otherwise be unbounded)
SRI_MODE      = False

# ===================== TREND-STATE ENTRY (the third way) ====================
# Sri, 05-Sep: "you are hardcoding rules rather than learning... don't write
# rules on rocks just because I gave them."
#
# The lesson from the IFCI 11:38 miss is structural, not a parameter. Both of
# our rule sets treated the crossover as an EVENT that expires. He actually
# buys a trend that is ALREADY established and still intact -- the cross is how
# it started, not the only second you may act. So: enter whenever the stock is
# in an up-trend and still moving, regardless of when the cross happened.
TREND_MODE   = False

# ================= VOLUME EXPANSION (what the eye actually saw) =============
# Reading five stocks on 04-Sep by hand, every trade worth taking had the same
# signature and it was NOT an indicator crossing:
#     MARSONS  10:16   35K -> 907K   (25x)   ran +6.1%
#     NIACL    11:38  717K -> 7.3M   (10x)   ran +8.1%
#     LALITHAA 13:21   80K -> 3.1M   (40x)   ran +2.2%
#     HEG      13:37    6K -> 127K   (20x)   ran +2.7%
#     IFCI     11:37  2.2M -> 15.3M   (7x)   ran +2.6%
# A quiet stretch, then volume multiplying several-fold within a few candles,
# with price making new highs at the same time.
#
# The trap that must be excluded: MARSONS 09:50 printed 822K then 905K -- its
# biggest volume of the morning -- and price went 127.47 -> 130.35 -> 128. That
# is people getting OUT. So volume alone is not the signal; volume WITH price
# making new highs is.
VOL_MODE     = False
# Measured over a WINDOW, not one candle. The 25x expansions seen by eye were
# on 5-minute blocks; at 30-second resolution the same move reads 4.0, then 1.3,
# then 1.0 -- the signal is real but invisible bar by bar.
VOL_WIN      = 6        # sum the last 6 candles (3 minutes)
# MARSONS 04-Sep, the five entries side by side:
#     10:16  WIN +27,124   volume 32.7x   0.88% above VWAP
#     12:47  dud  -3,910   volume  8.9x   3.26% above VWAP
#     09:50  dud  -5,208   volume  5.9x   1.20% above VWAP
#     11:05  dud  -5,185   volume  3.7x   3.34% above VWAP
#     12:06  dud  -2,194   volume  2.7x   3.00% above VWAP
# The expansion that mattered was an ORDER OF MAGNITUDE, not a mild one, and it
# happened while price was still close to VWAP. The three afternoon duds were
# all 10%+ from the open with the day's range already 10% -- buying the tail.
# REVERTED from 8.0 / 1.5. Those were read off MARSONS alone and killed every
# other stock: across the seven profitable entries in the five stocks read by
# hand, volume expansion ranged 3.0x to 32.7x and distance above VWAP 0.56% to
# 3.25%. MARSONS' 32.7x was exceptional, not the standard.
VOL_X        = 3.0
VOL_MAX_VWAP = 3.5
# What DOES separate them, across all five stocks: how much of its move the
# stock has already made. Every winner entered with the day's range under 8.2%;
# MARSONS' three afternoon duds all entered with it above 10.4%. This is Sri's
# "entering when the ignition is exhausted", measured -- and it agrees with the
# independent finding that entries 7%+ from the open win only 12% of the time.
MAX_DAY_RANGE = 9.0
VOL_BASE_N   = 20
VOL_NEWHIGH_N = 10      # price must be making a new high of the last N candles
# TWO DIFFERENT EXITS, because the eye used two.
# A trade that has not worked is cut quickly on fading volume. A trade that IS
# working is held with a wide trail and volume is ignored entirely -- NIACL on
# 04-Sep exited at 11:50:30 on "volume faded" at +0.58% and the run to +8.13%
# began sixty seconds later, then the re-entry cooldown locked it out.
VOL_FADE_X   = 0.8      # dud: volume back under this...
VOL_QUIET_N  = 12       # ...and no new high for this many candles -> cut it
VOL_WORKING  = 0.5      # once a trade is this far ahead it is "working"...
VOL_TRAIL    = 1.2      # ...and is then held until it gives back this much
TREND_RSI_LO = 50.0
TREND_RSI_HI = 80.0
SRI_CROSS_N   = 6       # EMA/MA cross counts as fresh for this many candles
SRI_RSI_LO    = 50.0
SRI_RSI_HI    = 65.0
SRI_NEED_HOTT = True    # require price above the HOTT band
SRI_NEED_ZONE = False   # also require a consolidation break-out (rarely fires)
SRI_MACD_ZERO = False   # require MACD above zero (his "strongest" case)
# --- "is this stock even alive today" ---------------------------------------
# 04-Sep: 115 trades, 77 of them exiting dead. The universe carries all 414
# MyWatchlist names and most are doing nothing on any given day. The board's
# carding gates were doing this job and I removed them when I went
# signal-driven. A MACD cross on a stock that has not moved all day is noise.
MIN_FROM_OPEN = 1.0     # must be at least this far above its own open
MIN_DAY_RANGE = 1.0     # and its high-low so far must span at least this much



def _sri_gates(v, i, require_cross=True):
    """His rules, unmixed with mine."""
    st, bars = v["st"], v["bars"]
    if i < 40 or i >= len(st):
        return False
    r = st[i]
    c = bars[i]["c"]
    # 1. EMA 8 above MA 12, from a cross in the last few candles
    if r["ema_above"] is not True:
        return False
    if require_cross and not any(st[j]["ema_cross_up"]
                                 for j in range(max(0, i - SRI_CROSS_N), i + 1)):
        return False
    # 2. MACD above its signal, and optionally above zero
    if r["macd"] is None or r["macd_sig"] is None or r["macd"] <= r["macd_sig"]:
        return False
    if SRI_MACD_ZERO and not r["macd_above_zero"]:
        return False
    # 3. RSI in his band
    rsi = r["rsi"] or 0
    if rsi < SRI_RSI_LO or rsi > SRI_RSI_HI:
        return False
    # 4. SAR below the candle
    if r.get("sar_below") is not True:
        return False
    # 5. HOTT -- price above the upper band
    if SRI_NEED_HOTT and r.get("hott_state") != "UP":
        return False
    # 6. consolidation break-out near the top of the coil
    if SRI_NEED_ZONE and not r.get("zone_break_up"):
        return False
    return True


def _trend_gates(v, i, require_cross=True):
    """In an up-trend, still moving, not exhausted. No cross timing at all."""
    st, bars = v["st"], v["bars"]
    if i < 40 or i >= len(st):
        return False
    r = st[i]
    c = bars[i]["c"]
    if r["ema_above"] is not True:
        return False
    if r["macd"] is None or r["macd_sig"] is None or r["macd"] <= r["macd_sig"]:
        return False
    if r["st_dir"] != 1 or r.get("sar_below") is not True:
        return False
    vw = r.get("vwap")
    if not vw or c < vw:
        return False
    rsi = r["rsi"] or 0
    if rsi < TREND_RSI_LO or rsi > TREND_RSI_HI:
        return False
    prior = bars[max(0, i - ATR_LOOKBACK):i]
    if prior and c:
        atr_pct = sum(x["h"] - x["l"] for x in prior) / len(prior) / c * 100.0
        if atr_pct < MIN_ATR_X * COST_PCT:
            return False
    return True


def _vol_stats(v, i):
    """Volume of the last VOL_WIN candles against the quiet stretch before it."""
    bars = v["bars"]
    a = bars[max(0, i - VOL_WIN + 1):i + 1]
    b = bars[max(0, i - VOL_WIN - VOL_BASE_N + 1):max(0, i - VOL_WIN + 1)]
    if not a or not b:
        return 0.0
    cur = sum((x["v"] or 0) for x in a) / len(a)
    base = sum((x["v"] or 0) for x in b) / len(b)
    return (cur / base) if base else 0.0


def _vol_gates(v, i, require_cross=True):
    """Quiet, then volume multiplies, and price is making new highs with it."""
    st, bars = v["st"], v["bars"]
    if i < max(40, VOL_BASE_N + 5) or i >= len(st):
        return False
    r = st[i]
    c = bars[i]["c"]
    if _vol_stats(v, i) < VOL_X:
        return False
    # Price must be confirming -- the MARSONS 09:50 trap filter. Judged over the
    # same window as the volume: has the block made a new high, and is it up?
    win = bars[max(0, i - VOL_WIN + 1):i + 1]
    look = bars[max(0, i - VOL_WIN - VOL_NEWHIGH_N + 1):max(0, i - VOL_WIN + 1)]
    if look and max(x["h"] for x in win) <= max(x["h"] for x in look):
        return False
    if c <= win[0]["o"]:
        return False                       # the block itself must be up
    vw = r.get("vwap")
    if not vw or c < vw:
        return False                       # buyers in control today
    if (c - vw) / vw * 100.0 > VOL_MAX_VWAP:
        return False                       # already stretched -- the tail
    hi = max(x["h"] for x in bars[:i + 1])
    lo = min(x["l"] for x in bars[:i + 1])
    if c and (hi - lo) / c * 100.0 > MAX_DAY_RANGE:
        return False                       # the day's move is already made
    # LIVENESS -- but it must not veto a stock that is waking up RIGHT NOW.
    # HEG on 04-Sep sat in a 5-point band for four hours and then ran +6.6%;
    # the backward-looking test refused the breakout precisely because the past
    # was quiet. So the current block's own range is what counts, not history.
    if c:
        rng = (max(x["h"] for x in win) - min(x["l"] for x in win)) / c * 100.0
        if rng < MIN_ATR_X * COST_PCT:
            return False
    return True


def _gates(v, i, require_cross=True):
    if VOL_MODE:
        return _vol_gates(v, i, require_cross)
    if TREND_MODE:
        return _trend_gates(v, i, require_cross)
    if SRI_MODE:
        return _sri_gates(v, i, require_cross)
    """Every entry condition, in one place, so the decision bar and the fill bar
    are judged identically."""
    st, bars = v["st"], v["bars"]
    if i < 40 or i >= len(st):
        return False
    r = st[i]
    c = bars[i]["c"]
    if USE_MACD:
        if require_cross and not r["macd_cross_up"]:
            return False
        m, sg = r["macd"], r["macd_sig"]
        if m is None or sg is None or m <= sg:
            return False
        k = i - HIST_RISE_N
        if k < 0 or st[k]["macd"] is None or st[k]["macd_sig"] is None:
            return False
        if (m - sg) <= (st[k]["macd"] - st[k]["macd_sig"]):
            return False                      # histogram not expanding
        if MACD_RISE and m <= st[k]["macd"]:
            return False                      # MACD itself not rising
    # DEAD-STOCK GATE: it must be moving enough to clear its own charges.
    prior = bars[max(0, i - ATR_LOOKBACK):i]
    if prior and c:
        atr_pct = sum(x["h"] - x["l"] for x in prior) / len(prior) / c * 100.0
        if atr_pct < MIN_ATR_X * COST_PCT:
            return False
    rsi = r["rsi"] or 0
    if rsi < RSI_MIN or rsi > RSI_MAX:
        return False
    if USE_VWAP:
        vw = r.get("vwap")
        if not vw or c < vw:
            return False
        hi = max(x["h"] for x in bars[:i + 1])
        lo = min(x["l"] for x in bars[:i + 1])
        rng = (hi - lo) / c * 100.0 if c else 0
        stretch = (c - vw) / vw * 100.0
        if rng and stretch > MAX_STRETCH_X * rng:
            return False                      # too far above VWAP for this stock
    if USE_ST and r["st_dir"] != 1:
        return False
    if r.get("sar_below") is False:
        return False                          # SAR above the candle -- not long
    return True


def universe(day):
    tape = E.load_tape(day)
    syms = set(tape)
    try:
        wl = json.loads((HERE / "logs" / "mywatchlist.json").read_text(encoding="utf-8"))
        watch = {r["sym"] for l in wl["lists"].values() for r in l}
    except Exception:
        watch = set()
    return tape, syms, watch


def run(day, log=print, verbose=False):
    tape, syms, watch = universe(day)
    series = {}
    for s in syms:
        bars = [b for b in tape[s] if "09:15:00" <= b["hhmm"] <= "15:30:00"]
        if len(bars) < 40:
            continue
        px = sum(b["c"] for b in bars[:20]) / 20
        if px < MIN_PRICE:
            continue
        series[s] = {"bars": bars, "st": S.compute(bars)}
        series[s]["idx"] = {b["hhmm"]: k for k, b in enumerate(bars)}

    times = sorted({b["hhmm"] for v in series.values() for b in v["bars"]})
    idx = {s: {b["hhmm"]: i for i, b in enumerate(v["bars"])} for s, v in series.items()}

    cash_risk = CAPITAL * MAX_RISK_PCT / 100.0
    exposure_cap = CAPITAL * LEVERAGE
    open_pos, closed, last_exit = {}, [], {}
    pending = []                       # decided on bar i, filled at open of i+1

    for t in times:
        # ---- fills first: anything decided on the previous bar
        for _st, sym, stop_px in sorted(pending, key=lambda x: -x[0]):
            if sym in open_pos or len(open_pos) >= 12:
                continue
            i = idx[sym].get(t)
            if i is None:
                continue
            fill = series[sym]["bars"][i]["o"]
            if not fill:
                continue
            # RE-CHECK AT THE FILL. TEJASNET trade 7 on 04-Sep was decided with
            # RSI 50 and filled a bar later with RSI 45, MACD already negative
            # and SAR above the candle -- a trade the rules would never have
            # allowed at the moment money actually moved. Gates are tested again
            # here, without requiring a fresh cross.
            if not _gates(series[sym], i, require_cross=False):
                continue
            risk_ps = fill - stop_px
            if risk_ps <= 0:
                continue
            used_risk = sum(p["risk"] for p in open_pos.values())
            budget = min(CAPITAL * RISK_PCT / 100.0, cash_risk - used_risk)
            if budget <= 0:
                continue
            qty = int(budget / risk_ps)
            val = qty * fill
            cap_val = min(exposure_cap - sum(p["qty"] * p["in"] for p in open_pos.values()),
                          exposure_cap * MAX_POS_PCT / 100.0)
            if val > cap_val:
                qty = int(cap_val / fill); val = qty * fill
            if qty <= 0 or val < MIN_POS:
                continue
            open_pos[sym] = {"sym": sym, "in": fill, "qty": qty, "in_t": t,
                             "stp": stop_px, "tgt": fill * (1 + TARGET_PCT / 100),
                             "peak": fill, "peak_t": t, "riding": False, "armed": False,
                             "risk": qty * risk_ps}
        pending = []

        # ---- mark and exit
        for sym in list(open_pos):
            p = open_pos[sym]
            i = idx[sym].get(t)
            if i is None:
                continue
            b = series[sym]["bars"][i]
            lo, hi, c = b["l"], b["h"], b["c"]
            why = None; px = c
            if lo and lo <= p["stp"]:
                why, px = "stop", p["stp"]
            elif SRI_MODE:
                # HIS EXIT: a candle closes below the EMA.
                e = series[sym]["st"][i]["ema8"]
                if e and c < e:
                    why, px = "closed below EMA", c
            elif VOL_MODE:
                if hi > p["peak"]:
                    p["peak"], p["peak_t"] = hi, t
                gain_peak = (p["peak"] - p["in"]) / p["in"] * 100.0
                give = (p["peak"] - c) / p["peak"] * 100.0 if p["peak"] else 0.0
                if gain_peak >= VOL_WORKING:
                    # WORKING -- ride it. Volume is deliberately ignored here;
                    # every big move in the five stocks read by hand went quiet
                    # in the middle and then went further.
                    if give >= VOL_TRAIL:
                        why, px = "trail", c
                else:
                    # NOT WORKING -- cut it as soon as the volume that brought
                    # us in has gone and it is making no new highs.
                    j = series[sym]["idx"].get(t)
                    if j is not None:
                        look = series[sym]["bars"][max(0, j - VOL_QUIET_N):j + 1]
                        hh = max(x["h"] for x in look) if look else 0
                        if (hh <= p["peak"] - 1e-9
                                and _vol_stats(series[sym], j) < VOL_FADE_X):
                            why, px = "volume faded", c
            elif SRI_MODE:
                pass
            elif hi:
                if hi > p["peak"]:
                    p["peak"], p["peak_t"] = hi, t
                if hi >= p["tgt"]:
                    p["riding"] = True
                if (p["peak"] - p["in"]) / p["in"] * 100 >= ARM_PCT:
                    p["armed"] = True
                give = (p["peak"] - c) / p["peak"] * 100 if p["peak"] else 0
                if p["riding"]:
                    if give >= GIVE_PCT:
                        why, px = "rode+faded", c
                elif p.get("armed") and give >= GIVE_BACK:
                    why, px = "gave back", c
            secs = lambda s_: int(s_[:2]) * 3600 + int(s_[3:5]) * 60 + int(s_[6:8])
            if not why and (SRI_MODE or VOL_MODE):
                if t >= SQUARE_OFF:
                    why, px = "square-off", c
            elif not why:
                # DEAD POSITION: no new high for STALE_SEC and not in profit.
                # Take the small loss instead of paying the clock out.
                if (secs(t) - secs(p.get("peak_t", p["in_t"])) >= STALE_SEC
                        and (c - p["in"]) / p["in"] * 100 <= STALE_GIVE):
                    why, px = "dead", c
                elif (secs(t) - secs(p["in_t"])) >= TIME_CAP:
                    why, px = "time", c
                elif t >= SQUARE_OFF:
                    why, px = "square-off", c
            if why:
                bv, sv = p["qty"] * p["in"], p["qty"] * px
                ch = PE.charges(bv, sv)["total"]
                closed.append({**p, "out_t": t, "out": px, "why": why,
                               "gross": sv - bv, "chg": ch, "net": sv - bv - ch})
                last_exit[sym] = t
                del open_pos[sym]

        # ---- decide (fills next bar)
        if not (ENTRY_FROM <= t <= ENTRY_TO):
            continue
        for sym, v in series.items():
            if sym in open_pos:
                continue
            le = last_exit.get(sym)
            if le and (int(t[:2]) * 60 + int(t[3:5])) - (int(le[:2]) * 60 + int(le[3:5])) < REENTRY_SEC / 60:
                continue
            i = idx[sym].get(t)
            if i is None or i < 40:
                continue
            bar = v["bars"][i]
            op = v["bars"][0]["o"] or bar["c"]
            if op and (bar["c"] - op) / op * 100.0 < MIN_FROM_OPEN:
                continue
            hi_so_far = max(x["h"] for x in v["bars"][:i + 1])
            lo_so_far = min(x["l"] for x in v["bars"][:i + 1])
            if lo_so_far and (hi_so_far - lo_so_far) / lo_so_far * 100.0 < MIN_DAY_RANGE:
                continue
            if not _gates(v, i, require_cross=True):
                continue
            c = v["bars"][i]["c"]
            # STRENGTH, so that when more signals fire than there is room for,
            # the book takes the best rather than whichever symbol the
            # filesystem happened to list first. Without this the same config
            # returned -162, +3,189 and +11,618 on the same day across three
            # processes -- capacity binds in the morning and arbitrary order
            # then decides the whole session.
            _r = v["st"][i]
            vw = _r.get("vwap") or c
            strength = ((c - vw) / vw * 100.0) + (_r["rsi"] or 0) / 100.0
            pending.append((strength, sym, c * (1 + STOP_PCT / 100)))

    net = sum(c["net"] for c in closed)
    wins = [c for c in closed if c["net"] > 0]
    if log:
        log(f"{day}: universe {len(series)} symbols  |  {len(closed)} trades  "
            f"net Rs {net:,.0f} ({net/CAPITAL*100:.2f}%)  "
            f"wins {len(wins)}/{len(closed)}  charges {sum(c['chg'] for c in closed):,.0f}")
    return closed, net


if __name__ == "__main__":
    days = sys.argv[1:] or ["20260902", "20260903", "20260904"]
    tot = 0
    for d in days:
        _, n = run(d)
        tot += n
    print(f"TOTAL Rs {tot:,.0f}")
