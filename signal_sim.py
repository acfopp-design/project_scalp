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
RISK_PCT     = 2.0      # 04-Sep one book: 1.5%=12,083  2.0%=16,191  2.5%=15,999
                        # 4.0%=7,385 -- past ~2.5% the losers scale faster.
MAX_RISK_PCT = 8.0
MIN_POS      = 100_000.0   # fewer, bigger positions beat many small ones
MAX_POS_PCT  = 40.0
STOP_PCT     = -1.0
TARGET_PCT   = 2.0
GIVE_PCT     = 1.0
TIME_CAP     = 1800
MIN_PRICE    = 20.0     # Sri: a penny stock is under Rs 20. Was 100, which
                        # silently deleted IFCI (opens at 98) from every run.
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
VOL_STALE_SEC  = 600.0  # VOL_MODE: no new high for this long...
QUICK_SEC    = 0.0      # MEASURED HARMFUL: cutting early at 90-240s lowers the
                        # average loss from -0.76% to -0.53% and the book from
                        # 28,967 to 1,827-15,017 -- it cuts the winners too.
                        # underwater this long...
QUICK_LOSS   = -0.15    # ...by at least this much...
QUICK_UP     = 0.30     # ...and never got further ahead than this -> cut it
STALE_MIN_OPEN = 2      # only hand a slot back when the book is competing
VOL_STALE_GIVE = 0.5    # ...and less than this % ahead -> hand the slot back
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
STRENGTH_MODE = "vol"   # vwap | push | vol | both -- how the book picks when
                        # more signals fire than there are slots
# ---- ROTATION -------------------------------------------------------------
# With Rs 5,00,000 of buying power and ~Rs 1,50,000 a position the book holds
# about three names, and on 04-Sep it was full from 09:16 to the close: 3,915
# signals were refused for lack of room, among them JINDWORLD 09:50 and NIACL
# 09:54, both of which went on to run. A human does not sit in a position that
# has stopped working while a better one is in front of him -- he swaps.
ROTATE       = False    # RETESTED 07-Sep with the current config (vol ranking,
                        # going-nowhere exit, gainer filter): 28,448 without it;
                        # with it 27,657 / 21,639 / 16,412 / 29,075 / 26,513 /
                        # 24,762 across six settings. One setting beats it by
                        # 2%, the rest lose up to 42% -- that is noise, not an
                        # edge. STILL OFF.
                        # MEASURED HARMFUL on 04-Sep: every setting tried lost
                        # money (-14,356 at 2x, -7,858 at 2x/300s, +7,785 best
                        # vs +12,083 without). Churn costs more than the slot
                        # is worth. Kept for the record, left OFF.
ROTATE_X     = 2.0      # the new signal must be this many times stronger
ROTATE_MAX   = 0.5      # ...and the position it replaces must be under this %
ROTATE_MIN_SEC = 120    # ...and have been given at least this long to work

# ---- WHAT THE HUMAN IS ACTUALLY LOOKING AT ---------------------------------
# Sri reads the day's gainer list and watches the names at the top of it. That
# is a LIVE feed, not hindsight: the Board polls Dhan's movers panels all day
# and each row carries the stock's day %. On 04-Sep the code's three slots held
# BIRLAPREC, KOPRAN, IKIO, ROSSTECH and SKYWAYS -- names sitting +0.3% to +1%
# on the day -- while the human held XTRANET, RML, MARSONS and JINDWORLD.
# DAY_PCT[sym] = [(hhmm, pct), ...] ascending; only readings at or before the
# decision bar are ever consulted.
DAY_PCT = {}
MOM_N = 20              # candles of momentum used by STRENGTH_MODE "mom"
MIN_DAY_PCT_OWN = 3.5   # bar for a stock the Board has not yet ranked
MIN_DAY_PCT = 2.0       # only trade a name the Board already shows this far up
                        # on the day -- what "it is on my screen" actually means.
                        # Ignored when DAY_PCT is empty (single-stock studies).

def day_pct_at(sym, t):
    """The most recent day % the Board had published for this stock by time t."""
    rows = DAY_PCT.get(sym)
    if not rows:
        return None
    lo, hi, out = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= t:
            out = rows[mid][1]; lo = mid + 1
        else:
            hi = mid - 1
    return out


OPEN_NOW = []           # positions still open at the end of the last cycle
DENIED = []             # (time, sym, reason) -- signals the book could not take
AVAILABLE_FROM = {}     # {sym: "HH:MM:SS"} -- when the name became knowable
RANGE_MODE   = "span"   # "span" = high-low, "fromlow" = run up from the day's low
MAX_UP_FROM_LOW = 9.0

# ---- SECOND WAY IN: the quiet-band breakout --------------------------------
# The volume rule only sees a stock WAKE UP. JINDWORLD never slept: 2-5 million
# shares every candle from the open, so its 09:22 turn read 1.2x, its 09:49
# breakout 1.8x and its 11:10 pop 0.6x, and a +14% day was refused outright.
# What those three moments DID have in common is shape, which is Sri's own
# consolidation-zone idea: the stock goes sideways in a tight band, then closes
# out of the top of it. No volume multiple required -- just not drying up.
USE_BREAKOUT = True
BRK_N        = 20      # candles of sideways to call it a band (10 minutes)
BRK_TIGHT    = 2.5     # the band must be no wider than this %
BRK_VOL      = 0.8     # and volume must be at least this x its own recent norm
BRK_NEW_DAY_HIGH = True
BRK_MAX_VWAP = 5.0     # a tight band held far above VWAP is a trend holding its
                       # gains; a VOLUME SPIKE that far above VWAP is a blow-off.
                       # Different animals, so the two paths get different caps.
VOL_BASE_N   = 20
SAME_TIME_BASE = False  # RIGHT IN PRINCIPLE, OFF IN PRACTICE. On the 13 single-
                        # stock studies it is worth +31,897 (317,670 -> 349,567:
                        # JINDWORLD +11k, RADHIKAJWE +29k) because it finally
                        # sees the opening drives. In the 3-slot book it HALVES
                        # the result (28,967 -> 14,311) -- the extra morning
                        # signals fill the slots before the real movers signal.
                        # Turn this ON when capital allows more than 3 positions.
                        # first ~13 minutes: compare against the same minutes of
                        # the previous session, not against the opening surge
VOL_NEWHIGH_N = 10      # price must be making a new high of the last N candles
# TWO DIFFERENT EXITS, because the eye used two.
# A trade that has not worked is cut quickly on fading volume. A trade that IS
# working is held with a wide trail and volume is ignored entirely -- NIACL on
# 04-Sep exited at 11:50:30 on "volume faded" at +0.58% and the run to +8.13%
# began sixty seconds later, then the re-entry cooldown locked it out.
VOL_FADE_X   = 0.8      # dud: volume back under this...
VOL_QUIET_N  = 12       # ...and no new high for this many candles -> cut it
STALL_N      = 0        # MEASURED HARMFUL: N=4/6/8/12 gives 194k/217k/263k/
                        # 277k against 318k with it off. Runners go quiet in the
                        # middle and then go further -- same reason the quick
                        # cut failed. Left OFF.
                        # candles without a new high -> sell into the stall
STALL_MIN    = 1.0      # ...only once the trade is this far ahead
VOL_WORKING  = 0.5      # once a trade is this far ahead it is "working"...
VOL_TRAIL    = 1.2
TRAIL_SPEED   = 0.0     # MEASURED ZERO-SUM on 04-Sep: x5 holds XTRANET to
                        # +12.65% (was +4.67%) and the book still ends at 28,976
                        # vs 28,967 -- the extra 9k is lost elsewhere. Left OFF.
                        # giveback allowed = this many x the stock's own average
TRAIL_SPEED_N = 20      # candle range over the last N candles
TRAIL_FRAC   = 0.0          # give a BIG winner more rope than a small one: the
                            # allowed giveback is the larger of VOL_TRAIL and
                            # this fraction of the gain already made.
# ---- CIRCUIT GUARD -------------------------------------------------------
# Sri, 05-Sep: "if it is reaching upper circuit, you should exit 1% [below the]
# upper circuit." TBZ on 04-Sep jumped 473.90 -> 480.85 in one 30-second candle
# and locked there for the rest of the day; the engine bought the locked price
# and sat in it until square-off, which cannot happen in real life -- at a
# locked upper circuit there are no sellers to buy from and no buyers to sell to.
CIRCUIT_NEAR  = 1.0     # exit (and never enter) within this % of the band
CIRCUIT_FROZEN_N = 4    # identical prints at the day's high = locked (2 minutes)
CIRCUIT_BANDS = {}      # {sym: band_pct}, filled from logs/circuit_bands.json
try:
    CIRCUIT_BANDS = json.loads(
        (HERE / "logs" / "circuit_bands.json").read_text(encoding="utf-8"))
except Exception:
    pass

TRAIL_ABOVE_ENTRY = True    # never let the trail exit AT or BELOW break-even --
                            # that is the stop's job. LALITHAA 09:18 entered at
                            # 288.30, peaked 292, trailed out at 288.45 flat, and
                            # then ran to 296.      # ...and is then held until it gives back this much
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
MIN_FROM_OPEN = 0.0     # must merely be at/above its own open. Was 1.0, which
                        # forced the engine to wait until the move had already
                        # happened: HEG ran 705->742 and every signal from 09:54
                        # to 13:41 was refused for being "only" +0.8% from open,
                        # so it finally bought the top at 740 and stopped out.
MIN_DAY_RANGE = 1.0     # and its high-low so far must span at least this much



def _sri_gates(v, i, require_cross=True):
    """His rules, unmixed with mine."""
    st, bars = v["st"], v["bars"]
    if i >= len(st):
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
    if i >= len(st):
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


def _upper_circuit(v, sym):
    """The upper band price for today, or None if the band is unknown.
    Needs yesterday's close, which the warm-up tape already carries."""
    d0 = v.get("d0", 0)
    if not d0:
        return None
    rec = CIRCUIT_BANDS.get(sym)
    if isinstance(rec, dict):
        return rec.get("upper")            # exact price from Dhan -- preferred
    if not rec:
        return None
    # a bare number means a band PERCENT; the tape's last bar of the previous
    # session is only an approximation of the official close, so this path is
    # the fallback, not the main one.
    prev_close = v["bars"][d0 - 1]["c"]
    return prev_close * (1 + float(rec) / 100.0) if prev_close else None


def _frozen(v, i):
    """Locked: the last CIRCUIT_FROZEN_N candles printed one identical price and
    that price is the high of the day. Needs no band table at all."""
    d0 = v.get("d0", 0)
    if i - d0 < CIRCUIT_FROZEN_N:
        return False
    win = v["bars"][i - CIRCUIT_FROZEN_N + 1:i + 1]
    px = win[-1]["c"]
    if any(x["h"] != px or x["l"] != px or x["c"] != px for x in win):
        return False
    return px >= max(x["h"] for x in v["bars"][d0:i + 1]) - 1e-9


def _at_circuit(v, sym, i, c):
    """True when price is inside the 1% no-go zone under the band, or locked."""
    up = _upper_circuit(v, sym)
    if up and c >= up * (1 - CIRCUIT_NEAR / 100.0):
        return "near upper circuit"
    if _frozen(v, i):
        return "circuit locked"
    return None


def _vol_stats(v, i):
    """Volume of the last VOL_WIN candles against the quiet stretch before it.

    EARLY IN THE SESSION that comparison is broken: the "quiet stretch before"
    IS the opening auction, the heaviest volume of the day. On 04-Sep it made
    MARSONS 09:16 read 0.7x, JINDWORLD 09:22 read 1.2x and LALITHAA 09:28 read
    0.2x -- three trades the eye took and the code refused -- while all three
    stocks were running. Before enough of today has passed, the honest
    comparison is against THE SAME MINUTES OF THE PREVIOUS SESSION, which is
    what a trader means by "unusual volume this morning".
    """
    bars = v["bars"]
    d0 = v.get("d0", 0)
    off = i - d0
    if d0 and SAME_TIME_BASE and off < VOL_WIN + VOL_BASE_N and off >= VOL_WIN - 1:
        a = bars[i - VOL_WIN + 1:i + 1]
        lo = max(0, off - VOL_WIN + 1)
        b = bars[lo:off + 1]                  # yesterday, same clock times
        if a and b:
            cur = sum((x["v"] or 0) for x in a) / len(a)
            base = sum((x["v"] or 0) for x in b) / len(b)
            return (cur / base) if base else 0.0
    a = bars[max(0, i - VOL_WIN + 1):i + 1]
    b = bars[max(0, i - VOL_WIN - VOL_BASE_N + 1):max(0, i - VOL_WIN + 1)]
    if not a or not b:
        return 0.0
    cur = sum((x["v"] or 0) for x in a) / len(a)
    base = sum((x["v"] or 0) for x in b) / len(b)
    return (cur / base) if base else 0.0


def _breakout(v, i, require_cross=True):
    """Sideways band, then a close above it. Returns True on the break.

    At the FILL bar (require_cross False) the band is measured from one candle
    further back. The breakout candle itself widens the band by definition, so
    re-testing "was it tight?" on the fill bar rejects every breakout ever made
    -- JINDWORLD's 09:49 break qualified and was then thrown away one candle
    later for the crime of having broken out.
    """
    st, bars = v["st"], v["bars"]
    d0 = v.get("d0", 0)
    if i - d0 < BRK_N + 3 or i >= len(st):
        return False
    off = 0 if require_cross else 1
    base = bars[i - BRK_N - off:i - off]
    hi = max(x["h"] for x in base)
    lo = min(x["l"] for x in base)
    c = bars[i]["c"]
    if not lo or (hi - lo) / lo * 100.0 > BRK_TIGHT:
        return False                       # not a band, just a move
    if c <= hi:
        return False                       # has not broken out yet
    if BRK_NEW_DAY_HIGH and c <= max(x["h"] for x in bars[d0:i - off]):
        return False                       # a break of a LOCAL band that is
                                           # still under the day's high is a
                                           # lower high -- MARSONS tried three
                                           # times at 139 on 04-Sep and each one
                                           # cost a full stop.
    if _vol_stats(v, i) < BRK_VOL:
        return False                       # breaking out on nothing
    r = st[i]
    vw = r.get("vwap")
    if not vw or c < vw:
        return False                       # buyers must own the day
    if (c - vw) / vw * 100.0 > BRK_MAX_VWAP:
        return False
    if r["st_dir"] != 1 or r.get("sar_below") is False:
        return False
    return True


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
    d0 = v.get("d0", 0)
    hi = max(x["h"] for x in bars[d0:i + 1])
    lo = min(x["l"] for x in bars[d0:i + 1])
    # HOW FAR HAS IT ALREADY RUN. Measuring this as high-minus-low punishes a
    # stock that was shaken out at the open and then trended: JINDWORLD spiked
    # 50.63->52.53, was dumped to 49.06 by 09:19, and that alone spent the whole
    # 9% budget before the real move began -- the engine was locked out of a
    # +14% day. Distance from the day's LOW measures the run itself.
    if RANGE_MODE == "fromlow":
        if lo and (c - lo) / lo * 100.0 > MAX_UP_FROM_LOW:
            return False
    elif c and (hi - lo) / c * 100.0 > MAX_DAY_RANGE:
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
        if _vol_gates(v, i, require_cross):
            return True
        return bool(USE_BREAKOUT and _breakout(v, i, require_cross))
    if TREND_MODE:
        return _trend_gates(v, i, require_cross)
    if SRI_MODE:
        return _sri_gates(v, i, require_cross)
    """Every entry condition, in one place, so the decision bar and the fill bar
    are judged identically."""
    st, bars = v["st"], v["bars"]
    if i >= len(st):
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
        d0 = v.get("d0", 0)
        hi = max(x["h"] for x in bars[d0:i + 1])
        lo = min(x["l"] for x in bars[d0:i + 1])
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
    # WARM TAPE: today's bars with the previous session in front of them, so the
    # volume baseline and the 26-candle indicators are already alive at 09:15:00
    # instead of about 09:28. Sri, 05-Sep: "from 9:15 till 9:30 maximum trading
    # happens -- if you say you don't have something, that's a concern."
    tape = E.load_tape_warm(day)
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
        bars, d0 = tape[s]
        if len(bars) - d0 < 40:
            continue
        px = sum(b["c"] for b in bars[d0:d0 + 20]) / 20
        if px < MIN_PRICE:
            continue
        # vwap_from=d0 so VWAP still resets at today's open despite the warm-up
        series[s] = {"bars": bars, "d0": d0, "st": S.compute(bars, vwap_from=d0)}
        series[s]["idx"] = {b["hhmm"]: k for k, b in enumerate(bars) if k >= d0}

    # clock and lookups are TODAY only -- the warm-up bars are history the
    # indicators may read, never bars the book is allowed to trade.
    times = sorted({b["hhmm"] for v in series.values() for b in v["bars"][v["d0"]:]})
    idx = {s: dict(v["idx"]) for s, v in series.items()}

    DENIED.clear()
    cash_risk = CAPITAL * MAX_RISK_PCT / 100.0
    exposure_cap = CAPITAL * LEVERAGE
    open_pos, closed, last_exit = {}, [], {}
    pending = []                       # decided on bar i, filled at open of i+1

    for t in times:
        # ---- rotation: make room for a much stronger signal
        if ROTATE and pending and open_pos:
            secs_ = lambda s_: int(s_[:2]) * 3600 + int(s_[3:5]) * 60 + int(s_[6:8])
            best = max(x[0] for x in pending)
            free = [s_ for s_ in open_pos if s_ not in {x[1] for x in pending}]
            for sym_ in sorted(free, key=lambda s_: open_pos[s_].get("str", 0)):
                if len(pending) <= 0:
                    break
                p_ = open_pos[sym_]
                i_ = idx[sym_].get(t)
                if i_ is None:
                    continue
                c_ = series[sym_]["bars"][i_]["c"]
                gain_ = (c_ - p_["in"]) / p_["in"] * 100.0
                if gain_ > ROTATE_MAX:
                    continue                       # it is working -- leave it
                if secs_(t) - secs_(p_["in_t"]) < ROTATE_MIN_SEC:
                    continue                       # give it a fair chance first
                if best < p_.get("str", 0) * ROTATE_X:
                    continue                       # not clearly better
                bv_, sv_ = p_["qty"] * p_["in"], p_["qty"] * c_
                ch_ = PE.charges(bv_, sv_)["total"]
                closed.append({**p_, "out_t": t, "out": c_, "why": "rotated out",
                               "gross": sv_ - bv_, "chg": ch_,
                               "net": sv_ - bv_ - ch_})
                last_exit[sym_] = t
                del open_pos[sym_]

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
            if _at_circuit(series[sym], sym, i, fill):
                continue
            if not _gates(series[sym], i, require_cross=False):
                continue
            risk_ps = fill - stop_px
            if risk_ps <= 0:
                continue
            used_risk = sum(p["risk"] for p in open_pos.values())
            budget = min(CAPITAL * RISK_PCT / 100.0, cash_risk - used_risk)
            if budget <= 0:
                DENIED.append((t, sym, "no risk budget left")); continue
            qty = int(budget / risk_ps)
            val = qty * fill
            cap_val = min(exposure_cap - sum(p["qty"] * p["in"] for p in open_pos.values()),
                          exposure_cap * MAX_POS_PCT / 100.0)
            if val > cap_val:
                qty = int(cap_val / fill); val = qty * fill
            if qty <= 0 or val < MIN_POS:
                DENIED.append((t, sym, "no room / below minimum size")); continue
            open_pos[sym] = {"sym": sym, "in": fill, "qty": qty, "in_t": t,
                             "stp": stop_px, "tgt": fill * (1 + TARGET_PCT / 100),
                             "peak": fill, "peak_t": t, "riding": False, "armed": False,
                             "risk": qty * risk_ps, "str": _st}
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
            cz = _at_circuit(series[sym], sym, i, c)
            if cz:
                # Get out while there is still a bid. Once it locks, nothing
                # sells at any price for the rest of the session.
                why, px = cz, c
            elif lo and lo <= p["stp"]:
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
                # SELL INTO THE STALL. Trade-by-trade against the human on
                # 04-Sep the entries matched within 0-3 minutes, but the exits
                # were late EVERY time: LALITHAA +1.18 vs +2.11, XTRANET +1.08
                # vs +2.75, NIACL +1.18 vs +2.99, IFCI +2.62 vs +3.59. A % trail
                # cannot do better -- it must hand back its own width before it
                # fires. The eye does not wait for a giveback; it sells when the
                # push stops making new highs.
                if (STALL_N and gain_peak >= STALL_MIN
                        and i - STALL_N >= 0
                        and max(x["h"] for x in series[sym]["bars"][i - STALL_N + 1:i + 1])
                            <= p["peak"] - 1e-9):
                    why, px = "push stalled", c
                elif gain_peak >= VOL_WORKING:
                    # WORKING -- ride it. Volume is deliberately ignored here;
                    # every big move in the five stocks read by hand went quiet
                    # in the middle and then went further.
                    # GIVE A FAST STOCK MORE ROOM. A flat 1.2% giveback is far
                    # too tight for a name covering 2% every three minutes:
                    # XTRANET on 04-Sep pulled back 218.76->215.00 (1.7%) in the
                    # middle of a run from 207 to 234 and was shaken out at
                    # +4.67% where the eye held +12.56%. The room a trade needs
                    # is set by how fast IT is moving, not by one number for
                    # every stock on the board.
                    room = max(VOL_TRAIL, TRAIL_FRAC * gain_peak)
                    if TRAIL_SPEED:
                        _b = series[sym]["bars"]
                        _w = _b[max(0, i - TRAIL_SPEED_N + 1):i + 1]
                        if _w and c:
                            _rng = sum(x["h"] - x["l"] for x in _w) / len(_w) / c * 100.0
                            room = max(room, TRAIL_SPEED * _rng)
                    if give >= room and (not TRAIL_ABOVE_ENTRY
                                             or c > p["in"] * (1 + COST_PCT / 100)):
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
                # A SLOT IS THE SCARCEST THING THE BOOK OWNS. With Rs 5,00,000
                # and ~Rs 2,00,000 a position only two or three fit, and on
                # 04-Sep the book filled at 09:16:30 with the first four signals
                # of the day and then refused 65 later signals on XTRANET,
                # MARSONS, JINDWORLD, NIACL, IFCI, LALITHAA and RML -- every one
                # of the names the human actually traded. Meanwhile CYIENTDLM
                # sat in a slot for 39 minutes to make +0.71%.
                # So: a position that is not going anywhere gives its slot back.
                # ...but ONLY when slots are actually being competed for. In a
                # single-stock study nothing is waiting for the slot, and
                # cutting a slow starter there costs real money: IFCI -13,736,
                # LALITHAA -14,331, NIACL -7,049 when this fired unconditionally.
                # QUICK CUT. The 12 losers in the 04-Sep morning book all look
                # the same: entered, never made a new high worth anything (best
                # gain after entry 0.03%-1.28%), rolled over and paid the full
                # -1% stop. A trade that is going to work usually works at once
                # -- every big winner was ahead within a minute or two. So a
                # trade that is UNDERWATER and has made no new high in QUICK_SEC
                # is cut at a fraction of the stop instead of the whole of it.
                if (not why and VOL_MODE and QUICK_SEC
                        and secs(t) - secs(p["in_t"]) >= QUICK_SEC
                        and c <= p["in"] * (1 + QUICK_LOSS / 100.0)
                        and p["peak"] <= p["in"] * (1 + QUICK_UP / 100.0)):
                    why, px = "not working", c
                if (not why and VOL_MODE and VOL_STALE_SEC
                        and len(open_pos) >= STALE_MIN_OPEN
                        and secs(t) - secs(p.get("peak_t", p["in_t"])) >= VOL_STALE_SEC
                        and (c - p["in"]) / p["in"] * 100.0 <= VOL_STALE_GIVE):
                    why, px = "going nowhere", c
                elif t >= SQUARE_OFF:
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
            # A stock the Board had not yet carded cannot be traded: at 09:20 we
            # do not know what will appear on the movers list at 10:30.
            if AVAILABLE_FROM and t < AVAILABLE_FROM.get(sym, "99:99:99"):
                continue
            if sym in open_pos:
                continue
            le = last_exit.get(sym)
            if le and (int(t[:2]) * 60 + int(t[3:5])) - (int(le[:2]) * 60 + int(le[3:5])) < REENTRY_SEC / 60:
                continue
            i = idx[sym].get(t)
            if i is None:
                continue
            bar = v["bars"][i]
            op = v["bars"][v.get("d0", 0)]["o"] or bar["c"]
            if op and (bar["c"] - op) / op * 100.0 < MIN_FROM_OPEN:
                continue
            d0v = v.get("d0", 0)
            hi_so_far = max(x["h"] for x in v["bars"][d0v:i + 1])
            lo_so_far = min(x["l"] for x in v["bars"][d0v:i + 1])
            if lo_so_far and (hi_so_far - lo_so_far) / lo_so_far * 100.0 < MIN_DAY_RANGE:
                continue
            if _at_circuit(v, sym, i, v["bars"][i]["c"]):
                continue                       # no seller to buy from up here
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
            if MIN_DAY_PCT is not None and DAY_PCT:
                dp = day_pct_at(sym, t)
                if dp is None:
                    # THE BOARD HAS NOT PUBLISHED A DAY % FOR IT YET. On 07-Sep
                    # ZYDUSWELL ran 541->565 (+3.9%) between 09:15 and 09:20 on
                    # 4x-15x volume and every candle was refused for "not on the
                    # gainer list", because the list did not exist yet. The
                    # stock's own move from its open is in the bars we already
                    # hold, needs nobody's permission, and is the same thing the
                    # eye reads off the screen.
                    _d0 = v.get("d0", 0)
                    _op = v["bars"][_d0]["o"]
                    _own = ((c - _op) / _op * 100.0) if _op else None
                    # ...but held to a HIGHER bar than a Board-confirmed mover.
                    # A stock the Board has not noticed yet is less proven, and
                    # letting it in at the same 2% cost 04-Sep 28,967 -> 11,581.
                    if _own is None or _own < MIN_DAY_PCT_OWN:
                        continue
                    dp = _own
                if dp is None or dp < MIN_DAY_PCT:
                    continue                    # not moving -- not on the
                                                # human's screen either
            _d0 = v.get("d0", 0)
            if STRENGTH_MODE in ("mom", "momvol"):
                _k = max(_d0, i - MOM_N)
                _ref = v["bars"][_k]["c"] or c
                _mom = (c - _ref) / _ref * 100.0
                strength = _mom if STRENGTH_MODE == "mom" else _mom * max(_vol_stats(v, i), 0.1)
            elif STRENGTH_MODE == "range":
                _hi = max(x["h"] for x in v["bars"][_d0:i + 1])
                _lo = min(x["l"] for x in v["bars"][_d0:i + 1])
                strength = (_hi - _lo) / _lo * 100.0 if _lo else 0.0
            elif STRENGTH_MODE == "mover":
                strength = (day_pct_at(sym, t) or 0.0) * max(_vol_stats(v, i), 0.1)
            elif STRENGTH_MODE == "vwap":
                strength = ((c - vw) / vw * 100.0) + (_r["rsi"] or 0) / 100.0
            else:
                # HOW HARD IS THIS ONE ACTUALLY MOVING. With only ~3 slots the
                # book must hold the strongest signals of the morning, not the
                # first three to fire. Volume multiple x the push in the signal
                # block itself.
                _blk = v["bars"][max(0, i - VOL_WIN + 1):i + 1]
                _push = (c - _blk[0]["o"]) / _blk[0]["o"] * 100.0 if _blk[0]["o"] else 0.0
                _vx = _vol_stats(v, i)
                if STRENGTH_MODE == "push":
                    strength = _push
                elif STRENGTH_MODE == "vol":
                    strength = _vx
                else:                                   # "both"
                    strength = _push * max(_vx, 0.1)
            pending.append((strength, sym, c * (1 + STOP_PCT / 100)))

    # POSITIONS STILL OPEN when the tape ran out. Live, this is the book right
    # now and it is what Sri needs to see on screen; in a completed backtest it
    # is empty because square-off closes everything.
    OPEN_NOW.clear()
    for sym, p in open_pos.items():
        i = idx[sym].get(times[-1]) if times else None
        last = series[sym]["bars"][i]["c"] if i is not None else p["in"]
        OPEN_NOW.append({**p, "last": last,
                         "live_pct": round((last / p["in"] - 1) * 100, 2),
                         "live_rs": p["qty"] * (last - p["in"])
                                    - PE.charges(p["qty"] * p["in"],
                                                 p["qty"] * last)["total"]})

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
