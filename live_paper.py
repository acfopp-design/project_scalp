"""### LEGACY / IDLE ### live_paper.py -- the OLD engine. It only runs when
EYE_LOGIC = False. The module that actually trades today is paper_live.py.
Editing this file does NOT change live trading behaviour.
"""
"""
live_paper.py -- forward paper trading, in real time, on live prices.

HOW THIS DIFFERS FROM paper_engine.py
    paper_engine REPLAYS a session that has already happened: it can see the
    whole tape, so it knows a bar's high touched the target before its low broke
    the stop. This one trades FORWARD. It sees only what has printed so far, it
    cannot look ahead, and it can only act on prices it actually sampled.

    That difference is the entire point. A back-test that reads its own future
    flatters itself in ways nobody notices; this one cannot, so when the two
    disagree the gap is the honest cost of not knowing what happens next.

WHERE THE PRICES COME FROM -- AND WHY THIS COSTS NOTHING
    Every price is read out of the alarm's existing full-market sweep, which is
    already running for the Board. Not one extra request goes to Dhan, so this
    tab can be left running all session without touching the rate budget. Open
    positions are priced by security-id, so a stock is still marked to market
    after its card has left the Super Stocks tab.

WHAT IT CANNOT DO, STATED PLAINLY
    It samples the sweep, roughly every few seconds. It does NOT see every tick.
    A stock that spikes through the target and falls back between two samples is
    booked at the price actually seen, not at the target -- which is the
    conservative direction, and the same direction a real order would suffer
    without a resting limit. Fills are assumed at the sampled price with no
    slippage and no partial fills; the participation gate on the Super Stocks
    tab is what keeps that assumption defensible, not this module.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paper_engine as pe

# ===========================================================================
#  EYE_LOGIC -- 22-Sep. The decisions come from the human-eye rules
#  (tune_v6 + eye_cfg, matched to Sri's own 22 trades on 21-Sep) instead of the
#  urgency/freshness gates below. Everything else on this tab is unchanged:
#  sizing, slots, Dhan charges via pe.charges, persistence, the snapshot the
#  tab draws. Set EYE_LOGIC = False and hit /admin/reload to go straight back
#  to the old engine -- no code change needed.
#
#  Measured on 21-Sep's real feed, pre-open universe, 2 slots, after charges:
#      old-style, no selection gate ........ +3.68%
#      EYE_LOGIC with SELECT_MIN = 1.5 ..... +26.22%
#  One day, in-sample on the selection threshold. Treat it as a forward test.
# ===========================================================================
EYE_LOGIC = True
try:
    import eye_signals as _eye
except Exception as _e:          # never let this stop the old engine loading
    _eye = None
    print("eye_signals unavailable:", _e)

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"
LOGS.mkdir(parents=True, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))

# Same rules as the back-test, deliberately. If these two ever disagree it must
# be because the future was unknown, not because the rules were different.
TARGET_PCT = pe.TARGET_PCT
STOP_PCT = pe.STOP_PCT
TIME_CAP = pe.TIME_CAP
MIN_PRICE = pe.MIN_PRICE
DISPLACE_MARGIN = pe.DISPLACE_MARGIN
PREOPEN_BOOST = pe.PREOPEN_BOOST
# 02-Sep: the cooldown clock now runs from the EXIT (see the block in tick()).
# 900s was measured against a clock that never started -- for a stock that
# stayed carded the cooldown was infinite -- so it was never really tested.
REENTRY_COOLDOWN = 180.0   # from the exit, not from when the card appeared
NEW_CARD_SEC = 30.0        # a card seen this recently is the same signal
MAX_ENTRIES_PER_SYM = pe.MAX_ENTRIES_PER_SYM

# ==========================================================================
#  ENTRY RULES -- DERIVED, NOT GUESSED  (rules_lab.py, 4 sessions, 74 trades)
# ==========================================================================
# The first version of this block was fitted to ONE afternoon and two of its
# three rules did not survive contact with the other three sessions. What
# follows is what is left after measuring 27-Aug, 28-Aug, 31-Aug and 01-Sep,
# scored in net rupees per trade after Dhan's charges at Rs 1.67 lakh a position.
#
# WITHDRAWN -- "decline anything already up 6%+ from the open"
#     Fitted to 01-Sep, where 0 of 8 such cards reached target. Across four
#     sessions that bucket MADE money: 6-8% Rs 1,261/trade (n=6), 8-12% Rs 301
#     (n=6). Only 12%+ lost, on two trades, which is an anecdote. The ceiling is
#     raised to a level that only excludes the genuinely exhausted.
#
# WITHDRAWN -- "entries only 09:15-10:30"
#     Also fitted to 01-Sep. Four sessions: 09:15-09:45 Rs 482/trade (n=44),
#     09:45-10:30 Rs 723 (n=9), 10:30-13:00 Rs 565 (n=13). Only 13:00-15:30 lost
#     (Rs -409, n=8). His own trading stops at 10:30 either way, so the engine
#     simply stops before the hour that actually loses.
#
# KEPT -- an urgency floor, which the one-day version never had
#     urgency <12   14 trades   Rs  -335 per trade
#     urgency >=12  60 trades   Rs   608 per trade
#     Negative on 3 of the 4 sessions individually. n=14 is thin and 28-Aug ran
#     the other way (+Rs 164 on 3 trades), so this is PROVISIONAL -- but it is
#     the cut the data supports best, and 01-Sep's live run opened SSWL,
#     SYMBIOTEC and JINDWORLD at 11.9-12.0, right on that boundary.
#
# NOT CHANGED -- the +3% / -1% pair, and here is why that matters most
#     A 25-cell target/stop grid says +5%/-1.5% pays Rs 656 a trade against the
#     current Rs 430. Held out one session at a time and re-fitted on the other
#     three, that collapses:
#         average IN-sample   Rs 737 per trade
#         average OUT-of-sample Rs  86 per trade
#     and on 3 of the 4 held-out days the fitted pair did WORSE than plain
#     +3%/-1%. The grid is fitting noise. The exits stay where they are until
#     something out-of-sample says otherwise.
#
# AND THE UNCOMFORTABLE ONE: 31-Aug lost money on every configuration tried
# (Rs -493/trade at baseline). One session in four where nothing worked is not
# a bug to be tuned away -- it is what this strategy looks like on a bad day.
ENTRY_NEEDS_VWAP = False   # refuse entries below the session VWAP
# 04-Sep, 177 cards over the three sessions that have a real 30-second tape:
#     above VWAP   n=75   reached +2% 65%   stopped  5%   avg best 4.25%
#     below VWAP   n=102  reached +2% 11%   stopped 80%   avg best 0.59%
# and it held every session: 76/65/54% above, stops 4/8/4%. Nothing else in
# Sri's stack comes close -- SuperTrend 10,3 gives 48%/34%, the EMA8/MA12 cross
# he treats as his FIRST signal gives 32%/50%, i.e. no discrimination at all.
# Adding SuperTrend on top of VWAP moves 65%->68% and costs a third of the
# trades; adding cloud/MACD/SAR as well moves nothing. So: VWAP alone.
# VWAP was not on his chart. It was added for free from the same bars.
ENTRY_MAX_FROM_OPEN = 12.0  # only the genuinely exhausted; see WITHDRAWN above
ENTRY_MIN_URGENCY = 12.0    # provisional, n=14 against
# ---- FRESHNESS: only buy something that is still making highs --------------
# 03-Sep. The single most repeated criticism from Sri, and the one measurable
# thing the engine was ignoring. See the long note at the entry gate.
ENTRY_NEEDS_FRESH_HIGH = True
if EYE_LOGIC:
    # v6 has already judged the bar; these would only re-reject its picks.
    ENTRY_NEEDS_FRESH_HIGH = False
    ENTRY_NEEDS_VWAP = False
    NEW_CARD_SEC = 0.0           # a v6 row is re-offered every cycle by design
ENTRY_MAX_OFF_PEAK = 0.40   # % below its own session high -- beyond this the
                            # move has already turned and we are buying the fade
ENTRY_MAX_SINCE_HIGH = 90   # seconds since it last printed a new high

# ---- TREND AND CHOP, 03-Sep, straight from Sri --------------------------
# "If EMA or MA is flat, why are you considering. And if EMA is falling down,
#  I still see you considering. Also if the stock's candles are up then down,
#  again up then down, that stock is not good for trading."
#
# All three are already on the card. ema_angle is the slope of the EMA the
# board plots; dipsUsed counts how many times the move has given back and
# resumed. The engine computed both, drew both, logged both, and gated on
# neither -- it decided on urgency and from_open alone. A flat or falling EMA
# is not a trend, and a stock that saws up and down is paying us in charges
# while it makes up its mind.
ENTRY_MIN_EMA_ANGLE = 0.05  # above flat. 0 is flat, negative is falling.
ENTRY_MAX_DIPS = 2          # more give-backs than this and it is chop, not a run

# ---- THE SESSION IS NOT ONE MARKET ---------------------------------------
# Sri, 03-Sep: "Maximum profits can be achieved from 9:15 till 10:30. Most
# stocks get into bull side. After that only a few stocks surge."
#
# Measured across 6 sessions, every carded stock at +2%/-1%:
#     09:15-09:45   148 cards   47% win   31% reached +2%
#     09:45-10:30    31 cards   70% win   19%
#     10:30-11:30    20 cards   45% win   15%
#     11:30-13:00     3 cards
#     13:00-15:30     9 cards
# 85% of every opportunity the scanner has ever found arrives before 10:30.
# The first half hour is where the VOLUME is; 09:45-10:30 has the best hit
# rate; after 10:30 the market thins out and gets choppier.
#
# One static setting cannot serve both. Worse, it silently drifts: urgency
# subtracts trade age and leans on the 90-second rise, so the same score means
# something different at 09:20 and at 11:20. At 10:20 today a floor of 16 --
# reasonable at the open -- was rejecting 100% of candidates, because by then
# nothing scored above 13. The floor had not changed; the market had.
#
# So the gates now move with the clock: wide open while the whole market is
# running, progressively stricter as real opportunity dries up.
PHASES = [
    # Measured at Sri's 15-minute granularity, 6 sessions, every carded stock:
    #   band          n   /day   avg%   win%   +2%   STOP%   med urg
    #   09:15-09:30  123    25   0.38%   46%   31%    35%      15.6
    #   09:30-09:45   25     5   0.31%   52%   28%    28%      15.0
    #   09:45-10:00   14     3   0.62%   78%   28%     7%      13.5   thin
    #   10:00-10:15   11     4   0.49%   63%    9%     0%      13.5   thin
    #   10:15-10:30    6     3   0.39%   83%   16%    16%      15.9   thin
    #   10:30-11:30   20     5   0.12%   45%   15%    15%      14.9
    #
    # This does NOT say the open is easy money, which is what I assumed an hour
    # ago and what "most stocks go bull side" sounds like. The open is where the
    # VOLUME is -- 25 cards a day against 3 later -- but it is also the most
    # violent: 31% reach +2% and 35% stop out. It is a high-variance hour, not a
    # generous one.
    #
    # 09:45-10:15 is the opposite shape: barely any candidates, but they hardly
    # ever stop (7% and 0%). Fewer chances, far safer ones.
    #
    # So the gates do NOT simply loosen at the open and tighten later. At the
    # open, candidates are plentiful, so we can afford to be STRICT on freshness
    # and still fill the book -- and being strict is exactly what stops us
    # buying the 35% that roll over. Mid-morning, candidates are scarce and
    # well-behaved, so we loosen to catch the few that appear.
    #
    # HONESTY: the three middle bands are 6-14 samples each. Individually each
    # is an anecdote. What earns the change is that all three point the same
    # way on the stop rate (7%, 0%, 16% against 35% and 28% at the open), which
    # is a consistent shape rather than one lucky cell. If tomorrow disagrees,
    # this table is wrong and moves back.
    #
    # until     urgency  off_peak  since_high  ema_angle  dips
    ("09:30",   10.0,    0.35,      60,        0.02,      2),  # plentiful + violent -> strict freshness
    ("09:45",   10.0,    0.40,      75,        0.02,      2),
    ("10:00",    9.0,    0.55,     120,        0.00,      3),  # 78% win, 7% stop -> let more through
    ("10:15",    9.0,    0.55,     120,        0.00,      3),  # 0% stop in sample
    ("10:30",   10.0,    0.50,     110,        0.02,      3),
    ("11:30",   14.0,    0.30,      75,        0.06,      2),  # thinning out
    ("13:00",   16.0,    0.25,      60,        0.08,      1),  # few real movers
    ("15:30",   15.0,    0.30,      75,        0.06,      2),
]
PHASE_ENABLED = True


def _phase(now_hms=None):
    """Effective entry gates for this moment of the session."""
    if not PHASE_ENABLED:
        return (ENTRY_MIN_URGENCY, ENTRY_MAX_OFF_PEAK, ENTRY_MAX_SINCE_HIGH,
                ENTRY_MIN_EMA_ANGLE, ENTRY_MAX_DIPS, "static")
    # Read the clock through the `time` module, not datetime.now(). replay_live
    # monkeypatches live_paper.time with a virtual clock; datetime.now() would
    # ignore it and hand every replayed bar the CURRENT wall-clock phase --
    # so a backtest of the morning would silently be scored with the 10:45
    # settings. Identical behaviour live, correct behaviour in replay.
    t = now_hms or datetime.fromtimestamp(time.time(), IST).strftime("%H:%M:%S")
    for until, urg, opk, sh, ang, dips in PHASES:
        if t < until:
            return (urg, opk, sh, ang, dips, f"<{until}")
    last = PHASES[-1]
    return (last[1], last[2], last[3], last[4], last[5], "late")
ENTRY_FROM = "09:15:00"
ENTRY_TO = "13:00:00"       # 13:00-15:30 is the only window that loses
if EYE_LOGIC:
    # v6 has its own last-entry clock (eye_cfg LASTT) and its own dip exit, and
    # it was measured across the whole session, not just the morning. This must
    # sit AFTER the assignment above or the module-level default wins.
    ENTRY_TO = "15:05:00"
    # CAPITAL MODEL. v6 was measured with the whole Rs 5,00,000 of 5x buying
    # power in at most TWO names. Spreading the same signals thinner costs a
    # lot, measured on 21-Sep's real feed after charges:
    #     2 slots x Rs 2,50,000 .... +43.09%   (87 trades, 44 wins)
    #     3 slots x Rs 1,66,667 .... +38.01%
    #     4 slots x Rs 1,25,000 .... +29.81%
    #     6 units x Rs   83,333 .... +18.41%   <- the tab's old default
    # Concentration IS the edge here: it lives in the few names whose bar is
    # exceptional for that stock, and six ways dilutes exactly that.
    EYE_SLOTS = 2

# LET A WINNER RUN.
#     01-Sep: TBZ was bought at 338.85 and sold at 349.10 for the +3% target.
#     It went on to 366.80 -- +8.25% from entry -- eleven minutes later, riding
#     above its HOTT band the whole way. SSWL did the same from 318.95 to
#     328.45. A fixed target caps every winner at +3% while every loser is
#     allowed its full -1%, and this strategy already lives on four runners out
#     of twenty-seven; clipping them is the one thing it cannot afford.
#
#     TRAIL_ARM_PCT   once a trade is up this much, the fixed target is dropped
#     TRAIL_GIVE_PCT  and it is held until it gives back this much from its peak
#     Set TRAIL_ARM_PCT to None to go back to the fixed target.
# EXIT ON THE STOCK GOING QUIET, NOT ON A NUMBER I CHOSE.
#     Sri's point, and it is the right one: the decision should follow how
#     ACTIVE the stock still is. A fixed +3% sold TBZ at 349.10 while it was
#     still making a new high every thirty seconds on rising volume; it reached
#     366.80 eleven minutes later.
#
#     Super Stocks already owns a definition of "it has stopped" and uses it to
#     DROP a card: no new high for STALL_SEC, or FADE_PCT given back from the
#     peak. Reusing that here means one definition of activity for both ends of
#     the trade instead of two sets of tuned numbers -- the stock is bought
#     while it is running and sold when the same test says it has stopped
#     running. The -1% stop stays underneath as the disaster brake.
# "target". Every test behind today's shipped config -- pyramiding, units,
# ATR stops, the +2% target -- was run with EXIT_MODE="target". The module
# default was still "hybrid" from yesterday's experiments, so the live engine
# would have run a configuration nothing had measured. Caught before restart;
# this is the same class as leaving the displacement A/B on a different clock.
EXIT_MODE = "target"       # "hybrid" = bank at +2% UNLESS still making highs
                           # "active" = hold while it is still running
                           # "target" = the old fixed +3% / -1%
                           # "target" = the old fixed +3% / -1%
STALL_SEC = 150.0          # no new high for this long -> it has stopped
FADE_PCT = 1.0             # or given back this much from its own peak
MAX_HOLD_PCT = 25.0        # sanity cap, never expected to bind
HYBRID_ALIVE_SEC = 60.0    # made a new high within this long = still running
HYBRID_GIVE_PCT = 1.2      # once riding, exit on this much off the peak

TRAIL_ARM_PCT = None
TRAIL_GIVE_PCT = 1.2

SQUARE_OFF = "15:15:00"     # everything is closed here, come what may
TICK_SEC = 3.0              # how often the book is marked to market

# A SLOT IS DEFENDED BY WHAT THE STOCK IS WORTH NOW, NOT AT ENTRY.
#     01-Sep, 15:00:55: the book filled with SSWL, SYMBIOTEC and JINDWORLD at
#     urgency 12.0, 12.0 and 11.9. Twenty minutes later SSWL had decayed to 6.6
#     and the other two had fallen off the Super Stocks tab entirely -- yet all
#     three still defended their slots at their ENTRY rank, because rank was
#     written once and never updated. WSTCSTPAPR was on the tab at urgency 9.5,
#     up 5.79% from the open, and could not get in: it needed to beat a frozen
#     12.0 by the 3-point margin. Eleven signals were turned away that way.
#
#     Now each open position's rank is refreshed from its live card every tick.
#     A stock that has left the tab is not a Super Stock any more, so after a
#     short grace its rank collapses and anything the tab currently rates can
#     take the capital. The grace exists because cards blink -- SSWL going in
#     and out was the whole reason WATCH mode was built -- and evicting on a
#     one-tick gap would churn the book for nothing.
OFFTAB_GRACE = 45.0        # seconds a held stock may be off the tab unpunished
OFFTAB_RANK = 0.0          # what its rank collapses to once the grace expires

# THE MARGIN HAS TO SCALE, BECAUSE THE URGENCY SCALE DOES.
#     DISPLACE_MARGIN = 3.0 was calibrated at 09:15, where urgency ran 15-30.
#     By mid-session it compresses: at 15:07 every one of the eight cards on the
#     tab sat between 10.0 and 12.7 -- a total spread of 2.7 points -- so a
#     3-point margin was mathematically impossible to clear and NOTHING could
#     ever be displaced. Measured that minute: MARINE 12.6, SAKAR 12.4 and
#     DYCL 12.3 were all locked out by a JINDWORLD holding at 11.0 and losing
#     Rs 1,127, and 10 signals were turned away.
#
#     A proportional edge tracks the scale wherever it happens to be.
#     MEASURED LIVE, 15:07-15:12, AFTER the proportional margin went in: the
#     book turned over FOUR times in five minutes -- JINDWORLD and SYMBIOTEC out
#     at 2 minutes, JAYKAY at 2, GAJA at 3 -- and the seven trades booked Rs 741
#     of charges against Rs 631 of market loss. The churn cost more than the
#     market did. 3.0 flat was too wide to ever fire; 8% was narrow enough to
#     fire constantly. Widened, and given a floor in TIME as well as in score.
DISPLACE_MIN_EDGE = 1.5    # absolute floor, so tiny numbers cannot churn
DISPLACE_REL_EDGE = 0.15   # ...or 15% better than the holder, whichever is more

# A POSITION CANNOT BE DISPLACED IN ITS FIRST FEW MINUTES.
#     Every displacement pays two sets of charges (~Rs 106 a round trip here) to
#     chase a fraction of an urgency point. A trade needs time to be right or
#     wrong before it is judged; the stop is what handles one that is simply
#     bad. Displacement is for "something much better turned up", not for
#     routine rotation.
# DISPLACEMENT IS OFF IN LIVE.  02-Sep 09:48, measured on the tape as it ran:
#
#     09:43:54  ARIES, PAR, CENTENKA entered
#     09:46:55  ARIES + PAR displaced -- 181 seconds, i.e. the instant the
#               dwell floor expired
#     09:47:13  CENTENKA displaced
#     09:46:55  BODALCHEM in -> stopped 27 s later, -Rs 1,943
#     09:47:22  PNBGILTS  in -> stopped 42 s later, -Rs 2,083
#
#   The whole book rotated the moment it was allowed to, into names that
#   immediately stopped out. Every realised loss in that run was a displacement
#   or a post-displacement stop; the three positions it was still HOLDING were
#   all green.
#
# WHY I GOT THIS WRONG
#   The A/B that justified displacement (Rs 36,833 with, Rs 3,147 without, over
#   4 sessions) was run on replay_live, which steps once per recorded board
#   snapshot -- about every 13 seconds. The live engine ticks every 3. Same
#   rule, four times the firing rate: in the replay it rotates into strength,
#   live it thrashes. I validated on a slower simulation and shipped to a faster
#   engine, which is the third time today I have been caught by the difference
#   between the two.
#
# Set DISPLACE_ENABLED = True to restore it -- but only after replay_live is
# made to tick at the live rate, so the test matches the thing being tested.
# ATR-SCALED STOPS.  A flat -1% is not the same risk on every stock.
#     Measured, 22 cards with enough pre-card history over 3 sessions:
#         ATR(14) on 30s bars      a -1% stop equals     hit -1% first
#           0.15-0.30%                   5.3 ATR                0%
#           0.30-0.50%                   3.0 ATR               66%
#           over 0.50%                   1.4 ATR               63%
#     On a volatile name -1% sits inside a single bar's normal range, so it is
#     hit by noise rather than by the trade being wrong. TBZ on 01-Sep: ATR
#     1.34%, dipped -1.37%, was stopped -- then ran +10.28%.
#
#     ONE-SIDED ON PURPOSE: the stop is never TIGHTER than the flat -1%, only
#     wider on stocks that actually move that much. A pure 1.5xATR rule would
#     have tightened the calm names to -0.5% and stopped out 100% of the
#     0.30-0.50% band, which is the opposite of the intent.
# ==========================================================================
#  CONVICTION SIZING AND PYRAMIDING   (Sri, 02-Sep: "TBZ has very very good
#  momentum, you can bet more on that compared to BODALCHEM")
# ==========================================================================
# He is right that flat sizing is wrong. Measured over 84 cards / 3 sessions:
#     urgency 12-13  avg best +1.11%   reached +3%   0%
#     urgency 13-16  avg best +1.90%                19%
#     urgency 16-20  avg best +1.47%                12%
#     urgency 20+    avg best +2.66%                26%
# A 20+ card is worth 2.4x a 12-13 card, so they should not get the same money.
#
# BUT the same data killed the simple version of his idea:
#     TBZ        urgency 15.1 at the card  ->  went +10.26%
#     BODALCHEM  urgency 15.5 at the card  ->  went  +2.86%
# At the moment of the card BODALCHEM scored HIGHER. Nothing observable then
# separated them. What separated them is that TBZ kept running AFTER entry.
#
# So the money is made in two steps, not one:
#   1. size the ENTRY by conviction, which the urgency table does support
#   2. ADD to whatever is actually working, which is decided by the tape and
#      needs no prediction at all
#
# Capital is now counted in UNITS rather than slots, because "3 positions" was
# an arbitrary number that also capped how large a single winner could get.
# DYNAMIC SIZING -- risk, not a hardcoded unit count.  (Sri, 02-Sep: "why is
# that fixed to 6? why can't that be dynamic?")
#
# He is right, and "6" was indefensible: it won a three-way test against 3 and
# 8 and I froze the winner. It made position size independent of how risky the
# position actually was, and independent of how many good stocks were on the
# tab. Two stocks with the same rupee value but stops of -1% and -2.5% were
# risking 2.5x different amounts and being treated as identical.
#
# RISK PARITY instead. Every position risks the same rupee amount:
#     position value = risk budget / (its own stop distance)
# so a stock whose ATR forces a -2.5% stop gets a SMALLER position than one
# stopping at -1%, and both lose the same if they are wrong. The number of
# concurrent positions is then whatever the total risk budget allows -- wide on
# a morning full of qualifying names, concentrated when little qualifies. It is
# never a number I chose.
#
# UNITS survives only as the pyramiding ladder and the hard exposure cap.
MIN_POSITION_RS = 40_000.0  # below this, do not open at all -- see _size_for
RISK_SIZING = True
# TESTED, 28-Aug / 01-Sep / 02-Sep, 09:15-10:30:
#     fixed 6 units (previous)      Rs 35,814   worst day +6,827
#     risk 0.75%/trade, 4% book     Rs 19,562   worst day     +17
#     risk 1.0%/trade,  5% book     Rs 25,305   worst day  +2,271
#     risk 1.5%/trade,  8% book     Rs 31,119   worst day  +8,528   <- shipped
#
# Fixed-6 tests HIGHER on total, by Rs 4,695 -- and all of that edge sits in a
# single session (02-Sep, 20,242 vs 10,879). Risk parity has the better worst
# day on all three, sizes each position by what it actually risks, and lets the
# number of positions follow the opportunity instead of a constant I picked.
# Shipping the more defensible system and recording that it costs Rs 4,695 of
# back-tested profit, because three sessions cannot tell those apart.
RISK_PER_TRADE_PCT = 1.5   # of CAPITAL, not of the leveraged exposure
MAX_TOTAL_RISK_PCT = 8.0   # the whole open book may risk this much at once
MAX_POS_PCT_OF_BOOK = 40.0 # and no single name may exceed this share of it

UNITS = 6                  # exposure cap only: capital x leverage / UNITS
if EYE_LOGIC:
    UNITS = EYE_SLOTS
UNITS_BASE = 1             # a normal card takes one unit
# CONVICTION SIZING IS OFF -- 1, not 2. Tested over three sessions:
#     6 units, flat entry, no pyramid          Rs 30,151   worst day +7,199
#     6 units, CONVICTION entry, no pyramid    Rs 13,779   worst day -4,440
#     6 units, flat entry, PYRAMID             Rs 36,367   worst day +6,827
#     6 units, conviction + pyramid            Rs 25,107   worst day +3,881
#   Sizing up on a high urgency card made things WORSE on every comparison,
#   which is exactly what the TBZ/BODALCHEM pair predicted: at the moment of
#   the card BODALCHEM scored HIGHER (15.5 vs 15.1) and went nowhere. Urgency
#   ranks a POPULATION well and cannot separate two individual stocks.
#   Pyramiding does the job instead, using the tape rather than a guess.
UNITS_STRONG = 1           # flat at entry; size comes from adding to winners
STRONG_URGENCY = 18.0
UNITS_MAX_PER_SYM = 3      # and no name may ever hold more than half the book

PYRAMID = True
PYRAMID_AT = 1.2           # add when a position is up this much...
PYRAMID_NEEDS_HIGH = 45.0  # ...and made a new high within this many seconds

ATR_STOPS = True
# 1.5 and not 2.0. An earlier run showed 2.0xATR paying Rs 39,527 against
# Rs 27,315 flat -- that was MY measurement bug: replay_live was computing ATR
# close-to-close (no high/low), which understates true range, so "2.0" there
# meant something much narrower than 2.0 live. With true ATR on both sides the
# same test gives 2.0x = Rs 11,651 and one session at -Rs 10,704. Corrected.
#
# What survives is small and honest: 1.5xATR = Rs 28,738 vs Rs 27,315 flat over
# three sessions, and the gain sits entirely in one of them. Kept because it can
# never be TIGHTER than the flat stop, so the downside is bounded -- not because
# three sessions proved anything.
ATR_MULT = 1.5
ATR_MAX_PCT = 2.5          # never risk more than this on one trade

DISPLACE_ENABLED = False
MIN_HOLD_BEFORE_DISPLACE = 180.0

# A WINNER IS NOT DISPLACED WHILE IT IS STILL ON THE TAB.
#     Rotating out of a position that is working, to chase one that merely
#     scores higher, is how a book pays two sets of charges to go sideways. A
#     stock that has LEFT the tab has no protection: it is not a Super Stock any
#     more, whatever it is doing.
PROTECT_WINNER_PCT = 0.5   # holding above +0.5% keeps its slot

# A POSITION THAT HAS NOT MOVED YET TELLS YOU NOTHING.
#     01-Sep replay, 09:15-10:30: NINE of 27 trades exited at exactly -Rs 106 --
#     entered and displaced at an identical price, zero market movement, pure
#     brokerage. KALYANIFRG was one of them, rotated out three minutes after
#     entry before it could do anything. Charges took 22% of gross profit,
#     almost all of it on those. A flat position is not evidence of failure;
#     only a losing one, or one that has left the tab, is.
# TESTED AND TURNED OFF. Across every session with usable data it changed
# nothing at a 7-minute dwell and made 01-Sep WORSE at 3 minutes
# (Rs 10,413 -> Rs 4,511). The nine flat exits it was built to stop cost about
# Rs 950; changing the dwell floor alone swings a session by Rs 15,000. The
# churn was never the driver, so this guard is not shipped -- kept as a switch
# because the reasoning is sound and a bigger sample may yet justify it.
DISPLACE_NEEDS_EVIDENCE = False
FLAT_BAND_PCT = 0.15       # inside +/-0.15% the trade has not said anything yet


def _displace_bar(holder):
    """What a challenger must beat to take this slot."""
    r = max(0.0, float(holder.get("rank") or 0))
    return r + max(DISPLACE_MIN_EDGE, r * DISPLACE_REL_EDGE)

_lock = threading.Lock()
_S = {
    "active": False, "started": None, "stopped": None,
    "capital": pe.DEFAULT_CAPITAL, "leverage": pe.DEFAULT_LEVERAGE,
    "target_pct": 10.0, "slots": pe.DEFAULT_SLOTS,
    "open": {},          # slot -> position
    "closed": [],        # finished trades
    "seen": {},          # sym -> last time its card was on screen
    "taken": {},         # sym -> [exit epochs]
    "first_rank": {},    # sym -> rank of its first card (re-entry must match it)
    "err": None, "ticks": 0, "last_tick": None, "displaced": 0,
    "skipped": {"too_cheap": 0, "crowded_out": 0, "cooldown": 0, "no_price": 0},
}


def atr_pct_from(row):
    """ATR(14) as a % of price, from whatever bar history the card carries.

    Live cards are built by build_card() and carry chart.h / chart.l / chart.c.
    The replay injects `atr_pct` directly from bars30. Returns None when there
    is not enough history -- and None means "unknown", never "fine": the caller
    falls back to the flat stop rather than guessing a wide one.
    """
    v = row.get("atr_pct")
    if v:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    ch = row.get("chart") or {}
    h, l, c = ch.get("h") or [], ch.get("l") or [], ch.get("c") or []
    n = min(len(h), len(l), len(c))
    if n < 5:
        return None
    trs = []
    for i in range(max(1, n - 14), n):
        try:
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        except (TypeError, IndexError):
            continue
    if not trs or not c[n - 1]:
        return None
    return (sum(trs) / len(trs)) / c[n - 1] * 100.0


def _units_used():
    return sum(q.get("units", 1) for q in _S["open"].values())


def _open_risk_rs():
    """What the book stands to lose if every open position hits its stop."""
    tot = 0.0
    for q in _S["open"].values():
        stp = q.get("stop_pct") or STOP_PCT
        tot += q["qty"] * q["in"] * abs(stp) / 100.0
    return tot


def _size_for(px, stop_pct):
    """Rupee value of a position that risks exactly RISK_PER_TRADE_PCT.

    Bounded three ways: the per-trade risk, the share of the book one name may
    take, and whatever exposure is left. Returns 0 when the risk budget is
    already spent -- which is what makes the number of positions dynamic.
    """
    cap, lev = _S["capital"], _S["leverage"]
    exposure_cap = cap * lev
    risk_left = cap * MAX_TOTAL_RISK_PCT / 100.0 - _open_risk_rs()
    if risk_left <= 0:
        return 0.0
    want_risk = min(cap * RISK_PER_TRADE_PCT / 100.0, risk_left)
    val = want_risk / max(abs(stop_pct), 0.05) * 100.0
    deployed = sum(q["qty"] * q["in"] for q in _S["open"].values())
    val = max(0.0, min(val,
                       exposure_cap * MAX_POS_PCT_OF_BOOK / 100.0,
                       exposure_cap - deployed))
    # A POSITION TOO SMALL TO PAY ITS OWN CHARGES IS NOT A POSITION.
    #
    # As the risk budget and exposure run out this used to trend to zero
    # smoothly, and the caller turned whatever was left into int(val // px) --
    # so the last trades of a full book came out at ONE SHARE. Today's replay:
    # 22 of 42 trades were under Rs 40,000 and together made Rs 8. SYNCOMF was
    # qty 1, a position worth Rs 22. JINDWORLD Rs 41. MAHABANK Rs 86.
    #
    # They are not harmlessly small. Each one takes a slot, pays brokerage on
    # both legs, and starts a re-entry cooldown on that symbol -- so a token
    # trade can lock us out of the same stock when it is worth buying properly.
    # Refusing is strictly better than a gesture.
    if val < MIN_POSITION_RS:
        return 0.0
    return val


def _unit_rs():
    return _S["capital"] * _S["leverage"] / max(1, UNITS)


def _stop_pct_for(row):
    """The stop for THIS stock: the flat floor, widened by its own volatility."""
    if not ATR_STOPS:
        return STOP_PCT
    a = atr_pct_from(row)
    if not a:
        return STOP_PCT                       # unknown -> unchanged behaviour
    return -min(ATR_MAX_PCT, max(abs(STOP_PCT), ATR_MULT * a))


_SIDMAP = {}
def pe_sid(sym):
    """security id from the master list, so a name v6 likes can be traded even
    if the Board never carded it."""
    global _SIDMAP
    if not _SIDMAP:
        import csv
        f = Path(__file__).parent / "security_id_list.csv"
        if f.exists():
            with f.open(encoding="utf-8", errors="ignore") as fh:
                for row in csv.DictReader(fh):
                    if row.get("SEM_EXM_EXCH_ID") != "NSE": continue
                    if row.get("SEM_SERIES") not in ("EQ", "BE", "SM", "D1"): continue
                    sy = row.get("SEM_TRADING_SYMBOL")
                    if sy: _SIDMAP.setdefault(sy, row.get("SEM_SMST_SECURITY_ID"))
    return _SIDMAP.get(sym)


def _hms(ts=None):
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M:%S") if ts \
        else datetime.now(IST).strftime("%H:%M:%S")


def _sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


# OFFLINE RUNS MUST NOT TOUCH THE LIVE BOOK.
#     02-Sep 10:15: replay_live imports this module and calls reset()/start()/
#     tick(), every one of which persists -- to the SAME file the live engine
#     writes. So every back-test I ran this morning overwrote the running
#     session's saved state. The live engine's in-memory book was never at risk
#     (separate process), but a board restart would have restored MY replay's
#     positions as if they were real trades.
#
#     Any process that is not the live engine sets OFFLINE = True and writes to
#     its own file. replay_live does this at import.
OFFLINE = False


def _statefile():
    day = datetime.now(IST).strftime("%Y%m%d")
    return LOGS / (f"livepaper_replay_{day}.json" if OFFLINE
                   else f"livepaper_{day}.json")


def _persist():
    """Survive a board restart. A forward test that forgets its open positions
    when the process bounces is not a forward test."""
    try:
        with _lock:
            d = {k: _S[k] for k in ("active", "started", "stopped", "capital",
                                    "leverage", "target_pct", "slots", "open",
                                    "closed", "taken", "first_rank", "displaced",
                                    "skipped")}
        _statefile().write_text(json.dumps(d, default=str), encoding="utf-8")
    except Exception:
        pass


def _restore():
    f = _statefile()
    if not f.exists():
        return
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    with _lock:
        for k, v in d.items():
            if k in _S:
                _S[k] = v
        _S["open"] = {int(k): v for k, v in (_S.get("open") or {}).items()}


def start(capital, leverage, target_pct, slots=None, log=lambda m: None):
    with _lock:
        if _S["active"]:
            return False, "already running"
        _S.update({
            "active": True, "started": _hms(), "stopped": None,
            "capital": max(1000.0, float(capital or pe.DEFAULT_CAPITAL)),
            "leverage": max(1.0, min(10.0, float(leverage or pe.DEFAULT_LEVERAGE))),
            "target_pct": float(target_pct or 0),
            "slots": int(slots or (EYE_SLOTS if EYE_LOGIC else pe.DEFAULT_SLOTS)),
            "open": {}, "closed": [], "seen": {}, "taken": {}, "first_rank": {},
            "err": None, "ticks": 0, "displaced": 0, "pyramided": 0,
            "skipped": {"too_cheap": 0, "crowded_out": 0, "cooldown": 0,
                        "no_price": 0, "already_run": 0},
            "window_closed": False,
        })
    log(f"live paper: STARTED  Rs {capital:,.0f} x{leverage} in {_S['slots']} positions")
    _persist()
    return True, None


def stop(log=lambda m: None, mark=None):
    """Stop taking new trades. Open positions are closed at the last price."""
    with _lock:
        if not _S["active"]:
            return False, "not running"
        _S["active"] = False
        _S["stopped"] = _hms()
    if mark:
        _close_all(mark, "stopped by user", log)
    log("live paper: STOPPED")
    _persist()
    return True, None


def reset(log=lambda m: None):
    with _lock:
        _S.update({"active": False, "started": None, "stopped": None,
                   "open": {}, "closed": [], "seen": {}, "taken": {},
                   "first_rank": {}, "err": None, "ticks": 0, "displaced": 0,
                   "pyramided": 0,
                   "skipped": {"too_cheap": 0, "crowded_out": 0,
                               "cooldown": 0, "no_price": 0}})
    _persist()
    log("live paper: cleared")
    return True, None


# ---------------------------------------------------------------- book
def _close(slot, px, why, now, log):
    p = _S["open"].pop(slot, None)
    if not p:
        return
    bv, sv = p["qty"] * p["in"], p["qty"] * px
    ch = pe.charges(bv, sv)
    t = dict(p)
    t.update({"out": round(px, 2), "out_t": now, "out_hms": _hms(now), "why": why,
              "gross": round(sv - bv, 2), "charges": ch, "chg": ch["total"],
              "net": round(sv - bv - ch["total"], 2),
              "held": int(now - p["in_t"]), "status": "Completed"})
    _S["closed"].append(t)
    _S["taken"].setdefault(p["sym"], []).append(now)
    log(f"live paper: EXIT  {p['sym']} @{px:.2f} ({why}) net Rs {t['net']:,.0f}")


def _close_all(prices, why, log):
    now = time.time()
    for slot in list(_S["open"]):
        p = _S["open"][slot]
        px = prices.get(str(p["sid"]))
        _close(slot, px if px else p["last"], why, now, log)


def _eye_enforce(log=lambda m: None):
    """Keep the EYE_LOGIC settings true no matter what live_config.json says.

    22-Sep, 08:43, live: the board booted, _apply_config pushed live_config.json
    onto this module, and every EYE_LOGIC override was silently reverted --
    ENTRY_TO back to 13:00, ENTRY_NEEDS_FRESH_HIGH back to True. The tab would
    have stopped entering at 1pm and re-rejected v6's picks with the gates
    EYE_LOGIC exists to bypass, while the log showed a perfectly healthy board.
    The config file is now correct too, but a JSON edit is not a guarantee, so
    the invariant is asserted here on every tick as well.
    """
    global ENTRY_TO, ENTRY_NEEDS_FRESH_HIGH, ENTRY_NEEDS_VWAP, NEW_CARD_SEC, UNITS
    fixed = []
    if ENTRY_TO != "15:05:00":       ENTRY_TO = "15:05:00";        fixed.append("ENTRY_TO")
    if ENTRY_NEEDS_FRESH_HIGH:       ENTRY_NEEDS_FRESH_HIGH = False; fixed.append("FRESH_HIGH")
    if ENTRY_NEEDS_VWAP:             ENTRY_NEEDS_VWAP = False;      fixed.append("VWAP")
    if NEW_CARD_SEC:                 NEW_CARD_SEC = 0.0;            fixed.append("NEW_CARD_SEC")
    if UNITS != EYE_SLOTS:           UNITS = EYE_SLOTS;             fixed.append("UNITS")
    if fixed:
        log("eye: re-asserted " + ", ".join(fixed) + " (live_config had reverted them)")


def tick(alarm, super_rows, log=lambda m: None, extremes=None):
    """One pass: mark the book to market, then consider new signals.

    `extremes` (OFFLINE REPLAY ONLY) maps sid -> (low, high) for the bar that
    contains this tick. When given, the STOP is tested against the LOW and the
    target against the HIGH, with the stop taking precedence if both fall inside
    the same bar. Entries are unaffected and still use the last price.

    Why it exists: replay_live marked every position to the 30-SECOND CLOSE, so
    a stock that traded to -1.4% inside a bar and closed at -0.6% never stopped.
    On 04-Sep the replay took 27 trades with TWO stops and reported +Rs 31,562
    while the live engine took 38 with roughly half stopping and made -Rs 747.
    Every replay figure this project has ever produced was inflated by that.
    Live passes nothing here, so live behaviour is byte-for-byte unchanged.

    Costs nothing -- alarm.snapshot() is the sweep the Board already runs.
    """
    if not _S["active"]:
        return
    if EYE_LOGIC:
        _eye_enforce(log)
    try:
        _, snap = alarm.snapshot()
    except Exception as e:
        _S["err"] = f"{type(e).__name__}: {str(e)[:80]}"
        return
    if not snap:
        return
    now = time.time()
    hms = _hms(now)
    prices = {}
    for sid, q in snap.items():
        try:
            lp = float(q[0] or 0)
            if lp > 0:
                prices[str(sid)] = lp
        except (TypeError, ValueError, IndexError):
            continue

    with _lock:
        _S["ticks"] += 1
        _S["last_tick"] = hms

        # ---- 1. mark the book, and exit anything that has hit a rule --------
        for slot in list(_S["open"]):
            p = _S["open"][slot]
            px = prices.get(str(p["sid"]))
            if px:
                p["last"] = px
                p["live_pct"] = round((px / p["in"] - 1) * 100, 2)
                p["peak"] = max(p.get("peak", p["in"]), px)
            px = p["last"]
            gain = (px / p["in"] - 1) * 100
            if px >= p.get("peak", px):
                p["peak"], p["peak_t"] = px, now      # a new high resets the clock

            # THE EYE EXIT RUNS BEFORE ANY EXIT_MODE BRANCH.
            # It was first written inside the "hybrid" and "active" branches --
            # and this board runs EXIT_MODE = "target", so neither executes.
            # The tab opened positions and never closed one, which is precisely
            # the silent failure the notes above keep warning about. Caught by
            # driving tick() with a fake feed before the open, not by reading.
            if EYE_LOGIC and _eye is not None and _eye.wants_close(p["sym"], p["in_hms"]):
                _close(slot, px, "eye exit", now, log)
                continue

            if EXIT_MODE == "hybrid":
                # THE TARGET IS A DECISION POINT, NOT A REFLEX.
                # At +3% ask one question: is it still making new highs right
                # now? If yes, hold and switch to a giveback trail. If it has
                # gone quiet, bank it. This applies "how active is the stock"
                # at the only moment it matters, instead of replacing the
                # target everywhere -- which measured WORSE on both sessions.
                quiet = now - p.get("peak_t", p["in_t"])
                fade = (p["peak"] - px) / p["peak"] * 100 if p.get("peak") else 0
                if px <= p["stp"]:
                    _close(slot, px, "stop", now, log)
                elif px >= p["tgt"]:
                    if quiet <= HYBRID_ALIVE_SEC:
                        p["riding"] = True            # still running -- let it go
                    if not p.get("riding"):
                        _close(slot, px, "target", now, log)
                    elif fade >= HYBRID_GIVE_PCT:
                        _close(slot, px, "rode + faded", now, log)
                elif p.get("riding") and fade >= HYBRID_GIVE_PCT:
                    _close(slot, px, "rode + faded", now, log)
                elif now - p["in_t"] >= TIME_CAP:
                    _close(slot, px, "time", now, log)
                elif hms >= SQUARE_OFF:
                    _close(slot, px, "square-off", now, log)
                continue
            if EXIT_MODE == "active":
                quiet = now - p.get("peak_t", p["in_t"])
                fade = (p["peak"] - px) / p["peak"] * 100 if p.get("peak") else 0
                if px <= p["stp"]:
                    _close(slot, px, "stop", now, log)
                elif fade >= FADE_PCT:
                    _close(slot, px, "faded", now, log)
                elif quiet >= STALL_SEC:
                    _close(slot, px, "stalled", now, log)
                elif gain >= MAX_HOLD_PCT:
                    _close(slot, px, "target", now, log)
                elif now - p["in_t"] >= TIME_CAP:
                    _close(slot, px, "time", now, log)
                elif hms >= SQUARE_OFF:
                    _close(slot, px, "square-off", now, log)
                continue
            if TRAIL_ARM_PCT is not None and gain >= TRAIL_ARM_PCT:
                p["trailing"] = True
            # In replay, judge the stop on the bar's LOW and the target on its
            # HIGH -- the pessimistic convention, stop first. Live passes no
            # extremes and falls back to the single last price, unchanged.
            ex = (extremes or {}).get(str(p["sid"]))
            lo_px = ex[0] if ex and ex[0] else px
            hi_px = ex[1] if ex and ex[1] else px
            if lo_px <= p["stp"]:
                _close(slot, p["stp"] if ex else px, "stop", now, log)
            elif p.get("trailing"):
                # armed: no target any more, only a giveback from the high
                give = (p["peak"] - px) / p["peak"] * 100 if p["peak"] else 0
                if give >= TRAIL_GIVE_PCT:
                    _close(slot, px, "trail", now, log)
            elif hi_px >= p["tgt"]:
                _close(slot, p["tgt"] if ex else px, "target", now, log)
            elif now - p["in_t"] >= TIME_CAP:
                _close(slot, px, "time", now, log)
            elif hms >= SQUARE_OFF:
                _close(slot, px, "square-off", now, log)
        if hms >= SQUARE_OFF:
            _S["active"] = False
            _S["stopped"] = hms

        # ---- 2. re-rate the open book against the LIVE tab ------------------
        live_rank = {}
        for r in (super_rows or []):
            if r.get("sym"):
                live_rank[r["sym"]] = (float(r.get("urgency") or 0)
                                       + (PREOPEN_BOOST if r.get("preopen") else 0))
        for p in _S["open"].values():
            if p["sym"] in live_rank:
                p["rank"] = live_rank[p["sym"]]
                p["off_tab_since"] = None
                p["defending"] = round(p["rank"], 1)
            else:
                if not p.get("off_tab_since"):
                    p["off_tab_since"] = now
                if now - p["off_tab_since"] >= OFFTAB_GRACE:
                    p["rank"] = OFFTAB_RANK
                    p["defending"] = 0.0
                    p["off_tab"] = int(now - p["off_tab_since"])

        # ---- 2b. PYRAMID -- add to what the tape says is working -------------
        # No prediction involved: a position only gets more money once it is
        # already up PYRAMID_AT and has made a new high in the last few seconds.
        # This is how the book ends up large in TBZ and small in BODALCHEM
        # without having had to tell them apart at 09:15.
        if PYRAMID and _S["active"]:
            for slot_, q in list(_S["open"].items()):
                if q.get("units", 1) >= UNITS_MAX_PER_SYM:
                    continue
                if UNITS - _units_used() < 1:
                    break
                if (q.get("live_pct") or 0) < PYRAMID_AT:
                    continue
                if now - q.get("peak_t", q["in_t"]) > PYRAMID_NEEDS_HIGH:
                    continue                      # it has gone quiet -- no more
                if RISK_SIZING:
                    add_val = _size_for(q["last"], q.get("stop_pct") or STOP_PCT)
                    if add_val <= 0:
                        continue                  # risk budget spent
                    # an add may not make one name more than its share of the book
                    cap_val = (_S["capital"] * _S["leverage"]
                               * MAX_POS_PCT_OF_BOOK / 100.0)
                    add_val = min(add_val, max(0.0, cap_val - q["qty"] * q["in"]))
                    add_qty = int(add_val // q["last"])
                else:
                    add_qty = int(_unit_rs() // q["last"])
                if add_qty <= 0:
                    continue
                # average the entry, keep the stop measured from the NEW average
                tot = q["qty"] + add_qty
                q["in"] = round((q["in"] * q["qty"] + q["last"] * add_qty) / tot, 2)
                q["qty"] = tot
                q["units"] = q.get("units", 1) + 1
                q["adds"] = q.get("adds", 0) + 1
                q["value"] = round(q["in"] * tot, 0)
                q["tgt"] = q["in"] * (1 + TARGET_PCT / 100)
                q["stp"] = q["in"] * (1 + (q.get("stop_pct") or STOP_PCT) / 100)
                q["live_pct"] = round((q["last"] / q["in"] - 1) * 100, 2)
                _S["pyramided"] = _S.get("pyramided", 0) + 1
                log(f"live paper: ADD   {q['sym']} @{q['last']:.2f} x{add_qty} "
                    f"(unit {q['units']}/{UNITS_MAX_PER_SYM}, avg {q['in']:.2f})")

        # ---- 3. new signals -- a card that was NOT on screen last pass ------
        # Outside the entry window nothing NEW is opened. Open positions carry
        # on and exit on their own rules; this only stops fresh entries.
        # NOTE: _persist() takes _lock, which is NOT reentrant -- calling it
        # from inside this block deadlocks the tick thread and the tab freezes.
        # Fall through to the single _persist() after the block instead.
        _S["window_closed"] = not (ENTRY_FROM <= hms <= ENTRY_TO)
        # Three separate reasons to stop opening anything new: the run was
        # stopped or squared off, the clock is outside his entry window, or the
        # session is over. Open positions still exit on their own rules above.
        _new_signals = _S["active"] and not _S["window_closed"]
        held = {p["sym"] for p in _S["open"].values()}

        # THE CANDIDATE LIST. Under EYE_LOGIC the board's urgency ranking is
        # replaced by what the human-eye rules want open right now, already
        # filtered by the selection score and one leg per name. The rows are
        # shaped like the board's so every gate, sizing and slot rule below is
        # reached unchanged -- only WHICH names arrive here is different.
        _rows = super_rows or []
        if EYE_LOGIC and _eye is not None:
            _eye.ensure(log)
            _sid = {}
            for _r in (super_rows or []):
                if _r.get("sym"): _sid[_r["sym"]] = _r.get("sid")
            _rows = []
            for w in _eye.want_open():
                if w["side"] != 1:
                    continue            # tab is long-only; v6 shorts are logged, not taken
                _s = _sid.get(w["sym"]) or pe_sid(w["sym"])
                if not _s:
                    continue
                _rows.append({"sym": w["sym"], "sid": _s, "price": w["px"],
                              "urgency": 99.0, "from_open": 0.0, "off_peak": 0.0,
                              "since_high_s": 0, "ema_angle": w["ang"],
                              "dipsUsed": 0, "dips_used": 0, "vwap": None, "preopen": False,
                              "eye_score": w["score"], "eye_since": w["ti"]})

        for r in _rows if _new_signals else []:
            sym = r.get("sym")
            sid = str(r.get("sid") or "")
            if not sym or not sid:
                continue
            was = _S["seen"].get(sym)
            _S["seen"][sym] = now
            rank = float(r.get("urgency") or 0) + (PREOPEN_BOOST if r.get("preopen") else 0)
            if sym not in _S["first_rank"]:
                _S["first_rank"][sym] = rank
            # 02-Sep, LIVE, Sri caught this: TBZ was bought at 383.75, sold at
            # 391.20 seven minutes later, and then ran to 416.00 -- +8.4% from
            # our own entry -- while sitting on the tab CONTINUOUSLY, 32 cards,
            # urgency climbing 15.1 -> 22.7. It was never re-entered.
            #
            # WHY: `seen[sym]` was refreshed on every tick a card was on screen,
            # so for a stock that stays carded `now - was` is always ~3 seconds
            # and the 15-minute clock NEVER STARTS. The cooldown was infinite,
            # not 900s. The state file proved it: skipped.cooldown was 0, so the
            # cooldown test was never even reached.
            #
            # The clock now runs from the EXIT time, which is what a cooldown
            # actually means. `seen` goes back to doing one job: telling a card
            # that is merely still on screen apart from a card that has just
            # arrived, for stocks never traded.
            if sym in held:
                continue                       # already holding it
            prior_exits = _S["taken"].get(sym) or []
            if prior_exits:
                if len(prior_exits) >= MAX_ENTRIES_PER_SYM:
                    _S["skipped"]["cooldown"] += 1
                    continue
                if now - max(prior_exits) < REENTRY_COOLDOWN:
                    _S["skipped"]["cooldown"] += 1
                    continue
            elif was is not None and now - was < NEW_CARD_SEC:
                # never traded, and the card was already on screen last pass --
                # it is the same signal, not a new one. It stays eligible.
                pass
            # THE MOVE HAS TO STILL BE AHEAD OF US.
            fo = r.get("from_open")
            if ENTRY_NEEDS_VWAP:
                vw = r.get("vwap")
                cpx = r.get("price")
                if vw and cpx and float(cpx) < float(vw):
                    _S["skipped"]["below_vwap"] = _S["skipped"].get("below_vwap", 0) + 1
                    continue
            if fo is not None and float(fo) > ENTRY_MAX_FROM_OPEN:
                _S["skipped"]["already_run"] = _S["skipped"].get("already_run", 0) + 1
                continue
            _pu, _pop, _psh, _pang, _pdip, _pname = _phase()
            if rank < _pu:
                _S["skipped"]["weak"] = _S["skipped"].get("weak", 0) + 1
                continue
            # IS IT STILL GOING UP, RIGHT NOW?
            #
            # Sri's complaint every single session, in his words: "entry taken
            # when it started dipping... exactly the reverse". BALUFORGE was
            # bought 0.05% off its high and fell at once. KIRI leg 1 ran +5%;
            # leg 2, bought after the top, lost. Same stock, same morning --
            # the rule was never wrong about the NAME, only the MOMENT.
            #
            # Urgency cannot express that. It is a blend of speed, size and
            # age, so a stock that surged hard and has just rolled over still
            # scores well for a while. Today proves it: the 14-16 band lost
            # Rs 9,910 over 12 trades while the 0-14 band MADE money. A higher
            # floor therefore throttles good trades without fixing the actual
            # error -- it blocked JINDWORLD, HIKAL and RAYMOND, all of which
            # reached +2%.
            #
            # off_peak and since_high_s say it directly: how far below its own
            # session high it is trading, and how long since it last made a new
            # one. Both have been computed and logged all along, and the engine
            # has never once looked at them.
            if ENTRY_NEEDS_FRESH_HIGH:
                op_ = r.get("off_peak")
                # the scanner writes since_high_s; build_card writes sinceHigh.
                # Read both -- a gate that silently reads None is not a gate,
                # and this project has already shipped one of those.
                sh_ = r.get("since_high_s")
                if sh_ is None:
                    sh_ = r.get("sinceHigh")
                if op_ is not None and float(op_) > _pop:
                    _S["skipped"]["rolled_over"] = _S["skipped"].get("rolled_over", 0) + 1
                    continue
                if sh_ is not None and float(sh_) > _psh:
                    _S["skipped"]["stale_high"] = _S["skipped"].get("stale_high", 0) + 1
                    continue
                # flat or falling EMA is not a trend
                ang = r.get("ema_angle")
                if ang is not None and float(ang) < _pang:
                    _S["skipped"]["ema_flat"] = _S["skipped"].get("ema_flat", 0) + 1
                    continue
                # up, down, up, down -- chop pays only the broker
                dips = r.get("dipsUsed")
                if dips is not None and int(dips) > _pdip:
                    _S["skipped"]["choppy"] = _S["skipped"].get("choppy", 0) + 1
                    continue
            if prior_exits and pe.REENTRY_NEEDS_STRONGER and rank < _S["first_rank"][sym]:
                _S["skipped"]["cooldown"] += 1
                continue
            px = prices.get(sid) or float(r.get("price") or 0)
            if px < MIN_PRICE:
                _S["skipped"]["too_cheap"] += 1
                continue
            if not px:
                _S["skipped"]["no_price"] += 1
                continue

            want = UNITS_STRONG if rank >= STRONG_URGENCY else UNITS_BASE
            _sp = _stop_pct_for(r)
            if RISK_SIZING:
                _val = _size_for(px, _sp)
                if _val <= 0:
                    _S["skipped"]["crowded_out"] += 1
                    continue
                slot = next((i for i in range(64) if i not in _S["open"]), None)
            else:
                free = UNITS - _units_used()
                _val = _unit_rs() * min(want, free) if free >= 1 else 0
                slot = (next((i for i in range(UNITS) if i not in _S["open"]), None)
                        if free >= 1 else None)
            if slot is None:
                # Only positions that may actually be replaced are candidates:
                # a winner still on the tab is not one of them.
                # A winner is protected whether or not its card is still on the
                # tab. GAJA was displaced at +0.99% (Rs 1,536 in profit) purely
                # because it had dropped off the list -- the tab losing interest
                # is not a reason to sell something that is working. The stop,
                # the target and the time cap are what close a position.
                def _movable(q):
                    if now - q["in_t"] < MIN_HOLD_BEFORE_DISPLACE:
                        return False
                    if (q.get("live_pct") or 0) >= PROTECT_WINNER_PCT:
                        return False
                    if DISPLACE_NEEDS_EVIDENCE:
                        # off the tab is evidence; so is being down. Flat is not.
                        off = q["sym"] not in live_rank
                        return off or (q.get("live_pct") or 0) <= -FLAT_BAND_PCT
                    return True
                movable = ([k for k, q in _S["open"].items() if _movable(q)]
                           if DISPLACE_ENABLED else [])
                weakest = min(movable, key=lambda k: _S["open"][k]["rank"]) if movable else None
                if weakest is not None and rank > _displace_bar(_S["open"][weakest]):
                    wp = _S["open"][weakest]
                    _close(weakest, prices.get(str(wp["sid"]), wp["last"]),
                           "displaced", now, log)
                    _S["displaced"] += 1
                    slot = weakest
                else:
                    _S["skipped"]["crowded_out"] += 1
                    continue

            qty = int(_val // px)
            if qty <= 0:
                _S["skipped"]["too_cheap"] += 1
                continue
            _S["open"][slot] = {
                "sym": sym, "sid": sid, "slot": slot + 1, "leg": len(prior_exits) + 1,
                "sig_t": now, "sig_hms": hms, "in_t": now, "in_hms": hms,
                "in": round(px, 2), "last": px, "peak": px, "qty": qty,
                "units": want, "adds": 0,
                "value": round(qty * px, 0), "rank": rank, "entry_rank": rank,
                "defending": round(rank, 1), "off_tab_since": None,
                "urgency": r.get("urgency"),
                "preopen": r.get("preopen_at") or (r.get("preopen") and "pre-open"),
                "tgt": px * (1 + TARGET_PCT / 100),
                "stp": px * (1 + _stop_pct_for(r) / 100),
                "stop_pct": round(_stop_pct_for(r), 2),
                "atr_pct": (round(atr_pct_from(r), 3) if atr_pct_from(r) else None),
                "peak_t": now,
                "live_pct": 0.0, "status": "In-Progress",
            }
            held.add(sym)
            log(f"live paper: ENTRY {sym} @{px:.2f} x{qty} "
                f"(slot {slot+1}, urgency {r.get('urgency')})")
    _persist()


# ---------------------------------------------------------------- read
def snapshot():
    """Everything the tab draws. Open positions carry their UNREALISED P&L so
    the running total is the truth at this instant, not only what is booked."""
    with _lock:
        cap = _S["capital"]
        rows = []
        for p in sorted(_S["open"].values(), key=lambda x: x["in_t"]):
            bv, sv = p["qty"] * p["in"], p["qty"] * p["last"]
            ch = pe.charges(bv, sv)          # what it would cost to close now
            rows.append({**p, "out": round(p["last"], 2), "out_hms": "—",
                         "why": "open", "gross": round(sv - bv, 2),
                         "charges": ch, "chg": ch["total"],
                         "net": round(sv - bv - ch["total"], 2),
                         "held": int(time.time() - p["in_t"]),
                         "status": "In-Progress"})
        rows += sorted(_S["closed"], key=lambda x: x["in_t"])
        rows.sort(key=lambda x: x["in_t"])

        realised = round(sum(t["net"] for t in _S["closed"]), 2)
        unreal = round(sum(r["net"] for r in rows if r["status"] == "In-Progress"), 2)
        net = round(realised + unreal, 2)
        wins = [t for t in _S["closed"] if t["net"] > 0]
        nets = sorted((t["net"] for t in _S["closed"]), reverse=True)
        tgt_rs = round(cap * _S["target_pct"] / 100, 2)
        n_done = len(_S["closed"])
        s = {
            "active": _S["active"], "started": _S["started"], "stopped": _S["stopped"],
            "capital": cap, "leverage": _S["leverage"], "slots": _S["slots"],
            "per_slot": round(cap * _S["leverage"] / max(1, _S["slots"]), 0),
            "exposure": round(cap * _S["leverage"], 0),
            "window": f"{_S['started'] or '—'} - {_S['stopped'] or _hms()}",
            "trades": len(rows), "open_n": len(_S["open"]), "done_n": n_done,
            "wins": len(wins), "losses": n_done - len(wins),
            "win_pct": round(len(wins) * 100.0 / n_done) if n_done else 0,
            "gross": round(sum(r["gross"] for r in rows), 2),
            "charges": round(sum(r["chg"] for r in rows), 2),
            "net": net, "realised": realised, "unrealised": unreal,
            "net_pct": round(net / cap * 100, 2) if cap else 0,
            "target_pct": _S["target_pct"], "target_rs": tgt_rs,
            "target_hit": bool(net >= tgt_rs), "target_gap": round(net - tgt_rs, 2),
            "best": ({"sym": max(rows, key=lambda t: t["net"])["sym"],
                      "net": round(max(r["net"] for r in rows), 2)} if rows else None),
            "worst": ({"sym": min(rows, key=lambda t: t["net"])["sym"],
                       "net": round(min(r["net"] for r in rows), 2)} if rows else None),
            "ex_best": round(sum(nets[1:]), 2) if len(nets) > 1 else 0.0,
            "ex_best_pct": round(sum(nets[1:]) / cap * 100, 2) if len(nets) > 1 and cap else 0.0,
            "displaced": _S["displaced"], "skipped": dict(_S["skipped"]),
            "ticks": _S["ticks"], "last_tick": _S["last_tick"], "err": _S["err"],
            "window_closed": _S.get("window_closed", False),
            "entry_window": f"{ENTRY_FROM[:5]}-{ENTRY_TO[:5]}",
            "max_from_open": ENTRY_MAX_FROM_OPEN,
            "live": True,
            "rule": (f"LIVE. Entry on a new Super Stocks card at the price then showing · "
                     f"target +{TARGET_PCT}% · stop {STOP_PCT}% · {TIME_CAP//60}-min cap · "
                     f"entries only {ENTRY_FROM[:5]}-{ENTRY_TO[:5]} and only while a stock is "
                     f"under +{ENTRY_MAX_FROM_OPEN}% from its open · "
                     f"square-off {SQUARE_OFF[:5]} · a signal {DISPLACE_MARGIN}+ points "
                     f"stronger displaces the weakest position · prices read from the "
                     f"sweep the Board already runs, so this makes NO request to Dhan"),
        }
        return {"ok": True, "summary": s, "trades": rows}


def loop(alarm, super_rows_fn, log=lambda m: None, stop_fn=None):
    _restore()
    while not (stop_fn and stop_fn()):
        try:
            if _S["active"]:
                tick(alarm, super_rows_fn() or [], log)
        except Exception as e:
            _S["err"] = f"{type(e).__name__}: {str(e)[:90]}"
            log(f"live paper loop: {type(e).__name__} {str(e)[:110]}")
        time.sleep(TICK_SEC)

# reload marker 22-Sep 10:12 -- eye_signals hold-time fix
