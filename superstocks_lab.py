"""
superstocks_lab.py -- a SEPARATE COPY of superstocks.py, for tuning trading rules.

WHY THIS FILE EXISTS
    Sri trades off the SUPER STOCKS tab. On 01-Sep I changed that module three
    times in one afternoon while working on the paper-trading engines -- the
    liquidity gate, the ordering, and worst of all I wired its position-size
    input to the capital box on the Paper Trading tab, so typing a number into a
    BACK-TEST could silently change which stocks appeared on the tab he actually
    trades from. He had assumed I was leaving it alone. He was right to.

    So the board he trades is frozen, and this is where rules get changed. It is
    a byte-for-byte copy at the moment of the split, with its own module state,
    its own constants and its own tab. Nothing here can move the SUPER STOCKS
    tab, and the paper-trading engines read THIS one.

HOW TO USE IT
    Change a constant here, watch the LAB tab beside the real one, and compare.
    When a change has earned its place over several sessions, port it across
    deliberately -- not as a side effect of something else.

SPLIT FROM superstocks.py ON 01-Sep-2026. Everything below this header is that
file unchanged; diff the two to see what has drifted since.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGDIR = HERE / "logs" / "movers_board"
LOGDIR.mkdir(parents=True, exist_ok=True)

# ---- what qualifies -------------------------------------------------------
MIN_FROM_OPEN = 2.0        # % above TODAY'S OPEN -- all six were 2-4% when first seen

# THE BOARD IS THE CANDIDATE SOURCE, THIS FILE IS THE FILTER.  (Sri, 02-Sep)
#     Measured: the Board sees a real run at a median of 0.4 minutes; Super
#     Stocks confirms at 1.2 minutes but MISSES 32% of runs entirely. Trading
#     the Board raw loses money (-Rs 19,336 over 7 sessions) because it carries
#     every mover. So the Board supplies the names and these gates decide.
#
#     Concretely: a stock already on the Board has passed its price, turnover
#     and liquidity filters, so it does not need to prove +2% from open before
#     this file will look at it. COALINDIA ran +1.91% on 02-Sep and was never
#     examined for want of 0.09%. Everything else -- burst, participation,
#     stall, fade -- still applies unchanged.
BOARD_MIN_FROM_OPEN = 1.0

# THE FROM-OPEN BLIND SPOT.  02-Sep, measured over three sessions:
#   16 stocks whose tape offered 3%+ were never carded, worth Rs 1,24,623 of
#   ceiling, purely because the admission test measures from the OPEN:
#       TEMPSENS   tape offered +8.0%  never exceeded  0.00% from open
#       DCI        tape offered +8.7%  never exceeded +0.13% from open
#       PINELABS   tape offered +4.5%  never exceeded -0.38% from open
#   These opened, sold off, and then rallied hard off an intraday low. The
#   entire rally is invisible to a +2%-above-the-open rule.
#
# SECOND ADMISSION PATH: a stock that has run MIN_FROM_LOW off its own low of
# the day qualifies even if it is still under its open. Every other gate --
# burst, liquidity participation, stall, fade, circuit -- applies unchanged, so
# this widens WHAT is looked at without loosening what is accepted.
#
# The risk is real and named: this is where falling knives live. The stall/fade
# test is what keeps a stock that is merely bouncing from staying on the tab.
# THE CONJUNCTION PROBLEM.  02-Sep, replaying the gates bar by bar:
#     stock         bars   ever +2% open   ever burst   BOTH at once
#     BODALCHEM       86         73            19            17
#     SKYWAYS         60         51            16            15
#     COALINDIA      123          1             2             0
#     SAGILITY       121          0             0             0
#
# Every gate is evaluated on the SAME 10-second scan pass, so a stock has to be
# above its open AND bursting AND not stalled AND liquid at one instant. Many
# runners satisfy each of those at different moments and never simultaneously.
#
# BURST MEMORY fixes the half of it that is clearly wrong: a burst is an EVENT,
# not a state. Once a stock has printed a qualifying 90-second move, that fact
# is remembered for BURST_MEMORY_SEC, so the other gates can catch up with it
# instead of having to coincide with it to the second. The stall and fade tests
# are untouched and still remove anything that has actually stopped -- so this
# widens the window in which a real burst can be acted on, without admitting a
# stock that is no longer running.
BURST_MEMORY = True
BURST_MEMORY_SEC = 150.0

FROM_LOW_PATH = True
MIN_FROM_LOW = 3.0         # % off the day's low
MIN_FROM_LOW_ABS = -6.0    # ...but never a stock still this far under its open
MIN_PRICE = 20.0           # below this the spread eats the arithmetic
MAX_DAY_PCT = 19.0         # 20% is the circuit; at 19% it is about to be unbuyable
UC_NEAR = 0.995            # within 0.5% of the upper circuit -> cannot be bought

# THE RULE THAT ACTUALLY DOES THE WORK -- see super_backtest.py.
#
# The first version of this file shipped without it, and the backtest found
# that the whole tab was then worth +2.3 points over the trivial filter
# "the stock is up 2% from its open". Everything else -- relative volume,
# urgency, the fade guard -- was decoration.
#
# Measured over 16 recorded sessions, at the moment a stock first appears:
#
#       rise in the last 90 seconds      n     gained 0.5% in 10 min
#       under 0.15%                     22            45%
#       0.15 - 0.60%                    30            40%
#       0.60 - 1.00%                    43            47%
#       1.00% and over                 112            71%
#
# Fitted on the first 11 sessions and then checked on the 5 it had never seen:
# 51% -> 62%, median gain 0.66% -> 0.80%. Smaller than the fit suggested, which
# is what an honest holdout looks like, and in the same direction.
# ...AND THE THRESHOLD THAT TURNED OUT TO BE CALIBRATED ON THE WRONG DATA.
#
# 1.2% was read off the backtest and shipped. Live, on 27-Aug, it fired on
# 0.3% of all 90-second windows. RATNAVEER climbed +2.18% to +4.41% from its
# open on Rs 120 lakh a minute, was promoted by the alarm 101 times, and never
# once reached the tab -- while VINDHYATEL sat on screen at 90s of -0.09%.
#
# The distributions are simply different:
#
#     90-second rise, measured on the alarm's own sweep, 27-Aug
#         median            +0.06%
#         75th percentile   +0.16%
#         90th              +0.34%
#         95th              +0.50%
#         99th              +0.90%
#         at or above 1.2%   0.3% of windows
#
# The backtest ran on board_*.jsonl, which samples a stock irregularly and
# sparsely, so `min(price in the last 90s)` picked up gaps that are not real
# ticks and inflated every reading. The RANKING the backtest found -- faster is
# better -- still holds. The NUMBER does not transfer, and a number carried
# between two differently-sampled datasets is exactly the kind of thing that
# should have been checked before shipping.
#
# So the gate is no longer a number. It is a PERCENTILE of what this market is
# doing right now, recomputed continuously from the stocks that pass every
# earlier rule. A fast tape raises the bar; a slow one lowers it; neither can
# leave the tab empty for an hour or flood it with drift.
RISE_PCTILE = 95.0         # be in the fastest 5% of 90-second moves today
# THE FLOOR DOES MORE WORK THAN THE PERCENTILE, and 0.25% was far too low.
#
# A percentile of a CALM distribution is meaningless -- the top 5% of a market
# where nothing is happening is still 5% of the candidates. With the bar window
# shortened to three minutes, the floor became the binding rule for most of the
# session, and at 0.25% it admitted drift: 60 cards a day at 60%.
#
# Measured, 11 sessions, real Dhan bars, with the 3-minute window:
#
#     floor    cards/day   worked
#     0.25%        60        60%
#     0.50%        53        62%
#     0.80%        45        65%
#     1.00%        41        65%      <- 09:15-09:30 alone: 242 at 73%
#
# 1.0% is the point where more tightening stops buying anything. The percentile
# now only bites when the whole market is faster than that, which is what it
# was for.
MIN_RISE_FLOOR = 1.00      # a real burst, not drift
_R90_MIN_SAMPLES = 150     # until this many, use the floor alone

# HOW FAR BACK THE BAR LOOKS. This is the whole rule, and getting it wrong
# killed the tab for a full session.
#
# 28-Aug, live, from the monitor's own report:
#
#     time     scanned   up 2%   climbing   BURSTING   on screen
#     09:15      1,770       7          7          1           1
#     09:25      2,150      69         38          0           5
#     09:35      2,213     124         65          0           0
#     09:45      2,240     145         44          0           0
#     09:55      2,258     150         43          0           0
#
# From 09:32 the tab was empty for the rest of his window while 150 stocks were
# up 2% and 43 were still climbing. The bar had risen to +2.08% and stayed
# there: the sample was a plain 6,000-value deque, roughly twenty minutes of
# history, and once the opening minutes were in it nothing in a calmer market
# could ever clear it. A percentile of a non-stationary distribution, measured
# over the past and applied to the present.
#
# Three minutes. The bar now tracks what stocks are doing RIGHT NOW: it rises
# on its own when the whole market is flying, which is correct, and falls back
# within minutes when the market settles, which is what it failed to do.
BAR_WINDOW_SEC = 180

# THE STEADY-CLIMBER PATH -- and the trade-off it forces.
#
# Measured 27-Aug on real Dhan bars: of the tradeable stocks that climbed 2%+
# continuously through his window, the burst gate alone caught fewer than one
# in three. It missed BOMDYEING (+8.97%), HCC (+5.90%), NSLNISP (+5.73%),
# GENESYS (+4.99%) -- stocks that walked up without ever sprinting. A
# percentile-of-speed gate is blind to them by construction.
#
# Opening a second door costs precision, and the cost is measurable
# (11 sessions, 91 real tradeable climbers across 26 and 27-Aug):
#
#     second door         cards/day   worked   caught
#     none (burst only)        23       73%     26/91
#     climber, 4%+ open        38       67%     44/91
#     climber, 3%+ open        49       66%     52/91
#     climber, 2%+ open        75       59%     67/91
#
# 4% is set here as the balance: it nearly doubles what the tab catches for
# six points of hit rate, and 38 names across 75 minutes is still readable.
# 2% catches three in four but puts 75 names on the screen, which is not a
# handful and is not something he can act on in 30 seconds each.
#
# THIS IS A JUDGEMENT ABOUT HIS TOLERANCE FOR MISSES VERSUS NOISE, NOT A FACT.
# One number changes it.
CLIMB_MAX_SINCE_HIGH = 30   # made a new session high within the last 30s
CLIMB_MAX_FADE = 0.20       # and is within 0.2% of it -- no giveback at all
CLIMB_MIN_FROM_OPEN = 4.0   # and is already this far above the open

# NOTE ON RELATIVE VOLUME, which this file used to advertise as its core idea:
#   a MIN_REL_VOL constant sat here, was quoted in the docstring, and was NEVER
#   APPLIED anywhere in scan(). A dead constant that reads like a live rule is
#   the same failure as the ignition badge the auditor could not see
#   (HANDOVER mistake #3), so it has been deleted rather than quietly enforced.
#   The backtest is why it was not simply switched on: where relative volume
#   could be computed at all it did not separate winners from losers
#   (56% / 59% / 53% across 3x, 5x and 10x). It is still shown on the card as
#   information; it is not a gate.
MIN_ABS_CR = 0.25          # a token floor: Rs 25 lakh on the day, to exclude the dead

# ---- CAN HE ACTUALLY TRADE IT? -------------------------------------------
# His instruction, 27-Aug, after FINKURVE, NATCAPSUQ and VINDHYATEL appeared:
#     "look at its volumes. Absolutely no liquidity. Not at all tradeable.
#      So, dont want any stocks which are not tradeable."
#
# Measured live on the tab's own output that morning:
#
#     stock         price   Rs traded per minute   ticks with a trade
#     RAMRAT        564     Rs 141.8 lakh          6/6   = 100%
#     NELCO         968     Rs  66.0 lakh          5/6   =  83%
#     VINDHYATEL   2515     Rs  18.0 lakh          11/15 =  73%
#     FINKURVE        -     Rs   0.0 lakh          0%
#     NATCAPSUQ       -     Rs   0.0 lakh          0%
#
# FINKURVE and NATCAPSUQ traded NOTHING for minutes at a time and were still
# on screen. That is not a thin stock, that is a stock with no other side.
#
# The right anchor is HIS POSITION, not an abstract floor. A rupee floor on the
# DAY is what wrongly excluded VINCOFE, so this measures the LAST MINUTE --
# what he would actually have to get out through.
#
#     Rs 50,000 against Rs 25 lakh a minute = 2% of one minute's flow.
#
# HONESTLY LABELLED: this pair of rules is NOT backtested. The board log's
# volume field does not refresh fast enough to reconstruct per-minute turnover
# -- 43 of 130 past signals compute to exactly zero, which is a recording
# artefact, not a market. Rather than dress a guess up as a measurement, these
# are set from his position size and are being measured FORWARD by
# super_monitor.py.
MIN_RS_PER_MIN = 25_00_000     # Rs 25 lakh traded in the last 60 seconds
MIN_LIVE_TICKS = 0.60          # and it must actually print in 60% of them

# ...AND THE SAME AGAIN, SUSTAINED. This is the rule that matters for EXIT.
#
# The last-60-seconds test alone is a test of whether he can get IN. It passed
# stocks he then flagged by name -- TRAVELFOOD, RUBICON, KITEX -- because a
# stock that trades Rs 4 lakh a minute all day and spikes to Rs 30 lakh for the
# one minute it is bursting will clear a 60-second gate every time. He buys
# into the spike and then owns it through the silence that follows.
#
# Measured on the tab's own output, 27-Aug, average rupees a minute since the
# open. He flagged everything at the top of this list and nothing at the bottom:
#
#     flagged as untradeable        not flagged
#       BIRLAPREC   Rs   0 lakh       MANINDS     Rs  50 lakh
#       FINKURVE    Rs   1 lakh       DELHIVERY   Rs  59 lakh
#       NATCAPSUQ   Rs   1 lakh       RAMRAT      Rs  63 lakh
#       VINCOFE     Rs   2 lakh       BOMDYEING   Rs  83 lakh
#       REDTAPE     Rs   2 lakh       RATNAVEER   Rs  94 lakh
#       KITEX       Rs   9 lakh       NSLNISP     Rs 117 lakh
#       VINDHYATEL  Rs  12 lakh       WEL         Rs 238 lakh
#       RUBICON     Rs  31 lakh
#       TRAVELFOOD  Rs  47 lakh
#
# CORRECTION, same evening. The Rs 50 lakh figure above was derived partly from
# rows in super_*.jsonl that were MY OWN SYNTHETIC TEST DATA, not the market.
# Real Dhan 1-minute bars for 27-Aug say VINCOFE traded Rs 53 lakh a minute,
# not Rs 2 lakh. The whole "this excludes the founding six" conclusion drawn
# from it was wrong. Cross-checked against the board's own record for BBTC, the
# fetched bars agree to within 1%, so the fetch is the trustworthy source.
#
# AND RUPEES TURN OUT TO BE THE WRONG UNIT ENTIRELY.
#
# Rs 50,000 as a fraction of one minute's turnover is ~1% for almost every
# stock he flagged AND almost every stock he did not. It does not separate them
# at all. SHARE COUNT does, cleanly, on real 27-Aug data:
#
#     he called these untradeable          he did not
#       BIRLAPREC       179 shares/min       RAMRAT        6,676
#       VINDHYATEL      291                  MANINDS       7,840
#       NATCAPSUQ       491                  DELHIVERY    11,291
#       KITEX           534                  SHYAMMETL    20,441
#       FINKURVE      1,245                  RATNAVEER    32,678
#       REDTAPE       1,524                  BOMDYEING    81,788
#       RUBICON       1,564                  ADANIPOWER  424,213
#       TRAVELFOOD    4,902
#
# Every flagged stock is at or under 4,902 shares a minute. Nothing above that
# was flagged. TRAVELFOOD is the proof: Rs 67 lakh a minute, which sails over
# any rupee floor, on 4,902 shares of a Rs 1,366 stock. Slippage is set by how
# many SHARES sit on the book at each tick, and a high price hides a thin book
# from any rupee measure.
#
# AND IT KEEPS THE TAB'S PURPOSE INTACT. On real data all six founding names
# clear this easily -- VINCOFE 31,028, WELCORP 30,141, BBTC 74,866,
# MOSCHIP 107,677, BOMDYEING 81,788, IMAGICAA 230,230. The conflict between
# "catch the thin movers" and "only show tradeable stocks" was never real; it
# was an artefact of measuring the wrong quantity.
MIN_SHARES_PER_MIN = 5_000          # sustained, since the open -- the real rule

# A rupee floor is still needed underneath it, or a Rs 21 stock doing 5,000
# shares a minute (Rs 1 lakh) would qualify on share count alone. Set below the
# thinnest of the six (VINCOFE, Rs 53 lakh) so it never becomes the binding
# constraint on the names this tab exists for.
MIN_SUSTAINED_RS_PER_MIN = 25_00_000

# ==========================================================================
#  POSITION-AWARE LIQUIDITY  (01-Sep -- changed on Sri's instruction)
# ==========================================================================
# WHAT WAS WRONG WITH THE TWO FLOORS ABOVE
#     They are absolutes, calibrated when he traded Rs 50,000. He now sizes at
#     Rs 1.67 lakh to Rs 5 lakh per position, so the same floors no longer mean
#     what they meant. Worse, MIN_SHARES_PER_MIN is a SHARE COUNT, which makes
#     the identical rule 26x harsher on an expensive stock:
#
#         Rs 40 stock   5,000 shares/min  =  Rs 2.0 lakh/min demanded
#         Rs 300 stock  5,000 shares/min  =  Rs 15.0 lakh/min demanded
#         Rs 1,050 stock 5,000 shares/min =  Rs 52.5 lakh/min demanded
#
#     KALYANIFRG did Rs 31.9 lakh/min while running +7.22% (09:47-09:57) and was
#     refused for doing 3,025 shares a minute. And the floor is simultaneously
#     TOO LOOSE the other way: at Rs 40, a Rs 1.67 lakh position is 4,175 shares
#     against 5,000/min -- 83% of a whole minute's flow, which is unfillable.
#
# WHAT REPLACES THEM: PARTICIPATION
#     The question is not "how much does it trade" but "how big am I inside it".
#         participation = position value / rupees traded per minute
#     Note this is unit-invariant: shares_needed / shares_per_min is the SAME
#     number, because both divide by price. So one test in rupees covers both,
#     and it scales with his capital automatically -- a bigger position faces a
#     stricter test, which is the correct direction and the opposite of what the
#     old fixed floors did.
#
# WHAT IS KEPT, AND WHY
#     MIN_SHARES_ABS survives as a BOOK-DEPTH floor, because the original
#     evidence for the share count is real and participation cannot see it:
#     TRAVELFOOD did Rs 67 lakh/min -- sailing over any rupee test -- on 4,902
#     shares of a Rs 1,366 stock, and he judged it untradeable by eye. A high
#     price hides a thin book from any rupee measure. It is lowered, not
#     removed, and it is no longer the binding constraint.
#
# THIS OVERRIDES A DELIBERATE DECISION RECORDED BELOW.
#     The comment block in scan() argues, with measurements, that the tab was
#     RIGHT to drop KALYANIFRG. That argument is sound at Rs 50,000 sizing on a
#     15%-of-flow participation. It is wrong at Rs 1.67 lakh, which is 5.2%.
#     The disagreement was always about position size, and position size is now
#     an input rather than an assumption. Changed on 01-Sep at his instruction;
#     the old constants are left in place above so this can be reverted.
MAX_PARTICIPATION = 0.08   # a position may be at most 8% of one minute's flow
MIN_SHARES_ABS = 1_500     # book-depth floor: below this the book is thin in
                           # share terms whatever the rupee value says
MIN_RS_FLOOR = 5_00_000    # Rs 5 lakh/min -- an absolute basement under both

# POSITION SIZE. Set by the app from the Paper Trading capital/leverage/slots so
# the gate above is measured against what he would actually buy. Until it is
# set, the historical Rs 50,000 is assumed so behaviour is unchanged.
POSITION_RS = 50_000.0


def set_position_size(rs):
    """Tell the scan how big a position it is screening for."""
    global POSITION_RS
    try:
        POSITION_RS = max(10_000.0, float(rs))
    except (TypeError, ValueError):
        pass


# ROLLING WINDOW, NOT THE SESSION AVERAGE  (fix 3)
#     SAKAR ran +5.99% from 12:02 to 12:42 doing 9,523 shares/min, and was never
#     carded: at 12:10 it had traded 96,875 shares in 175 minutes of session =
#     553 shares/min, nine times under the floor. A session average carries a
#     dead morning as a permanent verdict, so a stock that wakes up at noon can
#     never qualify however hard it runs. This is the same class of bug as
#     first2_val, which was fixed for exactly this reason and not fixed here.
LIQ_WINDOW_SEC = 300       # judge liquidity on the last five minutes
LIQ_WINDOW_MIN_SEC = 90    # below this the window is too short to rate

# ---- still moving? --------------------------------------------------------
STALL_SEC = 240            # no new high for four minutes -> it has stopped
FADE_PCT = 1.5             # given back this much from its peak -> it is over

# HOW LONG A QUALIFIED STOCK STAYS ON SCREEN.
#
# The backtest caught this and it would have been an ugly live surprise: with
# the 90-second burst applied on EVERY pass, the tab was EMPTY 87% of the
# session. A stock qualified, the burst finished ten seconds later, and the card
# vanished before he could look at it. He reads this board at 09:08 and glances
# at it while trading -- a card that exists for one refresh does not exist.
#
# So the burst is an ENTRY condition, not a display condition. Once a stock has
# earned its place it keeps it until it stalls, fades, or the clock below runs
# out -- and a fresh burst restarts that clock.
#
# 600s is not a round number picked for looking tidy: measured over 16 sessions,
# three quarters of these stocks reach their peak within 7.5 minutes of first
# appearing. Ten minutes covers the tradeable life of the move and stops.
#
# The clock runs from the LAST qualifying pass, not the first, so a card
# actually lives KEEP_SEC + however long the burst kept re-arming it -- about
# 11.5 minutes for a single 90-second burst. That is intended: a stock that is
# still bursting has not finished.
#
# The fade and stall guards below still override this. Being held is not being
# protected: give back FADE_PCT from the peak, or stop making highs for
# STALL_SEC, and the card goes regardless of the clock.
# Before this much of the session has elapsed there is no session average to
# take, so the last-60-seconds rate is used for liquidity instead.
SUSTAIN_AFTER_MIN = 2.0

HOLD_MAX_SINCE_HIGH = 60   # still printing new highs -> the clock does not run
KEEP_SEC = 600

# ---- WATCH MODE (01-Sep-2026) -------------------------------------------
# A card used to vanish the moment it stopped qualifying. SSWL flickered TWELVE
# times in 35 minutes while rising 6.7%, then disappeared for good at 10:48 on a
# 3.4% dip -- and was back at 333 by 10:51 with the tab still blank, because
# re-entry needs a fresh burst or a new session high and a stock recovering off
# a low is neither.
#
# So: is a disappearance actually a warning? Measured over all 293 of them
# across four sessions, using the board's own price log for the next 10 minutes:
#
#     recovered >= +0.5%   58%          (the tab's ENTRY hit rate is 65%)
#     fell      <= -1.0%   26%
#     median best case   +0.70%
#
# A dropped card is very nearly as good as a fresh pick. The disappearance
# carries almost no information -- so hiding it destroys more than it protects.
#
# Five features were tested for whether they separate recovery from breakdown.
# Momentum does NOT: rise_90s, urgency and off_peak all land between 54% and
# 65%, inside the noise. Only two things separate at all:
#
#     Rs/min at the drop   < Rs 1 Cr -> 30% recover     > Rs 3 Cr -> 64%
#     % above open         3-5%      -> 64% recover     > 8% -> 50%, and 41% break
#
# Thin stocks do not come back, and stocks already up 8%+ break down twice as
# often. Those two -- and only those two -- keep a card out of WATCH.
WATCH_SEC = 600            # how long a dropped card stays watchable
WATCH_MAX_GIVEBACK = 1.0   # % below the drop price -> it really has broken down
WATCH_MIN_RS_MIN = 1_00_00_000   # Rs 1 Cr/min at the drop; below this only 30% recover
WATCH_MAX_FROM_OPEN = 8.0  # already up more than this -> 41% break down, let it go
WATCH_N = 6                # watch cards shown beneath the active ones
MIN_DWELL_SEC = 60         # once shown, a card cannot blink out inside a minute

TOP_N = 8                  # he asks for a handful, repeatedly. This is the handful.
_HIST_SEC = 900            # fifteen minutes of per-stock memory

_lock = threading.Lock()
_hist = {}                 # sym -> deque[(t, price, dayvol)]
_peak = {}                 # sym -> (peak_price, t_of_peak)
_first = {}                # sym -> first time it qualified
_watch = {}                # sym -> {"since","px","reason"} while it is watchable
_shown = {}                # sym -> epoch it was last displayed (dwell guard)
_qual = {}                 # sym -> t of its most recent 90-second burst
_liq_seen = {}            # sym -> (rs/min, shares/min, participation %)
_burst_at = {}            # sym -> last time it printed a qualifying burst
_r90 = deque()             # (t, rise90) over the last BAR_WINDOW_SEC -- see below
_state = {"rows": [], "ts": None, "scanned": 0, "qualified": 0,
          "why_empty": None, "err": None, "unmapped": 0,
          "funnel": {"up2": 0, "buyable": 0, "climbing": 0,
                     "tradeable": 0, "bursting": 0}}


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _sec(t):
    a, b, c = t.split(":")
    return int(a) * 3600 + int(b) * 60 + int(c)


def _liquidity(alarm, sid, h, t, ltp):
    """(rupees traded in the last ~60s, fraction of recent ticks that printed).

    Either may be None when it genuinely cannot be known yet -- and None means
    "unknown", never "fine". A rule that reads False when the data is absent is
    worse than no rule; that is what made the auditor report zero ignition
    badges for a week (HANDOVER mistake #3), so the caller only rejects on a
    real number.
    """
    rs_min = None
    delta = getattr(alarm, "delta", None)
    if delta is not None:
        try:
            # same clamp as rise90: a 60-second window at 09:15:20 would span
            # the pre-open, where volume is zero and the rate is meaningless.
            _w = max(15, min(60, int(t - 33300)))
            _p, rup, win = delta(sid, seconds=_w)
            if rup is not None and win:
                rs_min = float(rup) * 60.0 / max(1.0, float(win))
        except Exception:
            rs_min = None
    live_pct = None
    if len(h) >= 6:
        seg = [h[i][2] - h[i - 1][2] for i in range(1, len(h))
               if h[i][0] > t - 120]
        if len(seg) >= 5:
            live_pct = sum(1 for x in seg if x > 0) / len(seg)
            if rs_min is None:                     # fall back to our own trail
                span = max(1.0, h[-1][0] - h[0][0])
                rs_min = (h[-1][2] - h[0][2]) * ltp / span * 60.0
    return rs_min, live_pct


def summary():
    with _lock:
        s = {k: v for k, v in _state.items() if k != "rows"}
    s["rows"] = len(_state["rows"])
    s["position_rs"] = POSITION_RS
    s["max_participation"] = MAX_PARTICIPATION
    s["liq_window_sec"] = LIQ_WINDOW_SEC
    return s


def rows():
    with _lock:
        return list(_state["rows"])


def reset_for_day():
    with _lock:
        _hist.clear()
        _peak.clear()
        _first.clear()
        _qual.clear()
        _r90.clear()
        _liq_seen.clear()
        _burst_at.clear()
        _watch.clear()
        _shown.clear()
        _state.update({"rows": [], "scanned": 0, "qualified": 0,
                       "why_empty": None, "err": None, "unmapped": 0,
                       "funnel": {"up2": 0, "buyable": 0, "climbing": 0,
                                  "tradeable": 0, "bursting": 0}})
        _watch.clear()
        _shown.clear()


# ====================================================================== scan
def scan(alarm, now_hms=None, log=lambda m: None, board_sids=None,
         preopen_syms=None):
    """One pass over the ENTIRE universe. Returns the qualifying rows.

    Cheap by construction: the alarm already holds the sweep, so this is
    arithmetic over a dict already in memory.
    """
    now_hms = now_hms or _now()
    t = _sec(now_hms)
    try:
        ts, snap = alarm.snapshot()
    except Exception as e:
        with _lock:
            _state["err"] = f"{type(e).__name__}: {str(e)[:80]}"
        return []
    if not snap:
        with _lock:
            _state["why_empty"] = "the market sweep has no data yet"
            _state["rows"] = []
        return []
    uni = alarm.universe()
    # THE BUG THAT MADE THIS TAB SHOW NOTHING FOR TWO DAYS.
    #
    #   Movers_alarm.snapshot() keys its dict by str(sid).
    #   Movers_alarm.universe() keys its dict by int(sid).
    #
    # So `uni.get(sid)` matched NOTHING, every stock was skipped, and the tab
    # sat empty through a session in which VOLTAMP ran +6% and VINCOFE +4.5%.
    # `early_movers()` in the same alarm module gets this right -- it writes
    # `uni.get(int(sid))` -- which is exactly why it kept promoting stocks all
    # morning while Super Stocks showed nothing.
    #
    # The backtest did not catch it because the replay handed scan() INT keys.
    # A test that shares an assumption with the code it tests cannot fail; that
    # is mistake #1 in HANDOVER and I made it again. super_check.py now feeds
    # STRING keys, exactly as the live alarm does.
    #
    # Both are accepted here so this cannot break again from either side.
    uni = {**uni, **{str(k): v for k, v in uni.items()}}

    cand = []
    scanned = 0
    unmapped = 0
    # THE FUNNEL. An empty tab has to say WHICH rule emptied it. Without this,
    # "no qualifying stocks" reads identically whether the market is quiet or
    # the module is broken -- and for two days it was broken and looked quiet.
    f = {"up2": 0, "buyable": 0, "climbing": 0, "tradeable": 0, "bursting": 0}
    # TODAY'S BAR, recomputed every pass from the stocks that reached the last
    # gate. Not a constant read off a backtest of differently-sampled data.
    while _r90 and _r90[0][0] < t - BAR_WINDOW_SEC:
        _r90.popleft()
    bar = MIN_RISE_FLOOR
    if len(_r90) >= _R90_MIN_SAMPLES:
        srt = sorted(v for _tt, v in _r90)
        bar = max(MIN_RISE_FLOOR, srt[min(len(srt) - 1,
                                          int(RISE_PCTILE / 100.0 * len(srt)))])
    for sid, q in snap.items():
        sym = uni.get(sid)
        if not sym:
            unmapped += 1
            continue
        try:
            ltp = float(q[0] or 0)
            dayvol = float(q[1] or 0)
            op = float(q[2] or 0)
            lo_day = float(q[4] or 0) if len(q) > 4 else 0.0
            ucct = float(q[5] or 0) if len(q) > 5 else 0.0
            prev = float(q[7] or 0) if len(q) > 7 else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        if ltp <= 0 or op <= 0 or dayvol <= 0:
            continue
        scanned += 1

        h = _hist.setdefault(sym, deque())
        h.append((t, ltp, dayvol))
        while h and h[0][0] < t - _HIST_SEC:
            h.popleft()

        # ---- the move so far, from TODAY'S OPEN -------------------------
        from_open = (ltp / op - 1) * 100
        day_pct = ((ltp / prev - 1) * 100) if prev else None
        _floor = (BOARD_MIN_FROM_OPEN if str(sid) in (board_sids or ())
                  else MIN_FROM_OPEN)
        from_low = ((ltp / lo_day - 1) * 100) if lo_day > 0 else 0.0
        _by_open = from_open >= _floor
        _by_low = (FROM_LOW_PATH and from_low >= MIN_FROM_LOW
                   and from_open >= MIN_FROM_LOW_ABS)
        if not (_by_open or _by_low) or ltp < MIN_PRICE:
            continue
        f["up2"] += 1
        if day_pct is not None and day_pct >= MAX_DAY_PCT:
            continue
        if ucct and ltp >= ucct * UC_NEAR:
            continue                       # locked or about to lock -- unbuyable
        f["buyable"] += 1

        # ---- peak and whether it is still going --------------------------
        pk, pk_t = _peak.get(sym, (ltp, t))
        if ltp > pk:
            pk, pk_t = ltp, t
        _peak[sym] = (pk, pk_t)
        since_high = t - pk_t
        fade = (pk - ltp) / pk * 100 if pk else 0.0
        stopped = (since_high >= STALL_SEC or fade >= FADE_PCT)
        if not stopped:
            f["climbing"] += 1

        # ---- RELATIVE volume: this stock against ITS OWN normal ----------
        # Rupee floors were what excluded the six. Judging a stock against its
        # own average minute treats a Rs 3 Cr small-cap doing 8x its usual
        # business as what it is -- unusual -- instead of as small.
        rel = None
        cr_day = ltp * dayvol / 1e7
        if len(h) >= 4:
            recent = h[-1][2] - h[-2][2]                     # volume this tick
            span = max(1.0, h[-1][0] - h[0][0])
            per_tick = (h[-1][2] - h[0][2]) / max(1, len(h) - 1)
            if per_tick > 0:
                rel = recent / per_tick
        if cr_day < MIN_ABS_CR:
            continue
        if rel is not None and rel < 1.0 and since_high > 60:
            continue                       # volume gone AND not making highs

        # ---- CAN HE GET OUT? --------------------------------------------
        # Rupees traded in the LAST MINUTE, and whether it printed at all.
        # The alarm already computes the first for the ignition badge, over the
        # same shared sweep, so this costs nothing.
        rs_min, live_pct = _liquidity(alarm, sid, h, t, ltp)
        if rs_min is not None and rs_min < MIN_RS_PER_MIN:
            continue                       # Rs 50,000 would move this price
        if live_pct is not None and live_pct < MIN_LIVE_TICKS:
            continue                       # dead air -- no other side to sell to
        # SUSTAINED, not just right now. This is the exit test -- see the
        # comment on MIN_SUSTAINED_RS_PER_MIN. A one-minute spike is not
        # liquidity, it is a trap with good timing.
        # WARM-UP. The session-average rules cannot be applied at 09:15:20 --
        # there is no session yet to average. This divided the day's volume by a
        # hard floor of three minutes when twenty-two seconds had elapsed, an
        # eight-fold understatement, and the funnel showed the damage on
        # 28-Aug: 7 stocks climbing, exactly 1 judged tradeable.
        #
        #     09:15:22   climbing  7  ->  tradeable  1
        #     09:16:22   climbing 30  ->  tradeable 10
        #     09:22:22   climbing 52  ->  tradeable 17
        #
        # So for the first SUSTAIN_AFTER_MIN the LAST-60-SECONDS rate is used
        # instead, which is time-correct from the first trade. Nothing is left
        # ungated -- a different, better-founded gate is used.
        elapsed = (t - 33300) / 60.0                      # 33300s = 09:15:00
        mins_open = max(0.5, elapsed)
        rs_sustained = cr_day * 1e7 / mins_open
        shares_min = dayvol / mins_open
        # ---- WHY THE SESSION-AVERAGE GATE STAYS AS AN EXIT TEST -------------
        # 01-Sep: Super Stocks dropped KALYANIFRG at 09:20:31 and 1045 while the
        # stock ran on to 1092 at 09:55. That looks exactly like the tab quitting
        # 35 minutes early, and it was nearly "fixed" by exempting held cards
        # from these two averages.
        #
        # The tape says otherwise. From its own 30-second bars, 09:20:31->09:55:
        #     58,454 shares over 34 minutes = 1,694 shares/min = Rs 17.8 lakh/min
        #     09:31  437 shares/min   Rs  4.5 lakh/min
        #     09:41  335 shares/min   Rs  3.4 lakh/min
        #     09:47  476 shares/min   Rs  4.9 lakh/min
        # It was under BOTH floors for essentially the whole climb. Rs 50,000 is
        # 15% of one minute's entire flow at those rates -- the definition of the
        # slippage he said to avoid: "Absolutely no liquidity. Not at all
        # tradeable. So, dont want any stocks which are not tradeable."
        #
        # The stock went up 12% on air. The tab was RIGHT to let it go, and
        # holding it would have put an unfillable name at the top of his screen
        # for 35 minutes. Left exactly as it was, deliberately, with the numbers
        # written down so nobody "fixes" it again.
        # ---- LIQUIDITY, on a rolling window, against HIS position size ----
        # rs_roll / sh_roll are the last LIQ_WINDOW_SEC of flow taken from this
        # module's own history deque, falling back to the session average only
        # while the window is too short to rate. Session average is no longer a
        # gate in its own right -- see LIQ_WINDOW_SEC above.
        rs_roll, sh_roll, roll_span = None, None, 0.0
        if len(h) >= 2:
            cut = t - LIQ_WINDOW_SEC
            base = None
            for _e in h:
                if _e[0] >= cut:
                    base = _e
                    break
            if base is not None and h[-1][0] - base[0] >= LIQ_WINDOW_MIN_SEC:
                roll_span = (h[-1][0] - base[0]) / 60.0
                sh_roll = (h[-1][2] - base[2]) / max(roll_span, 1e-9)
                rs_roll = sh_roll * ltp
        if rs_roll is None:
            # not enough window yet -- use the last 60 seconds if the alarm has
            # it, and only then fall back to the session rate.
            rs_roll = rs_min if rs_min is not None else (
                rs_sustained if elapsed >= SUSTAIN_AFTER_MIN else None)
            sh_roll = (rs_roll / max(ltp, 1e-9)) if rs_roll is not None else None

        if rs_roll is not None:
            # PARTICIPATION -- how much of one minute's flow his order would be.
            if POSITION_RS > rs_roll * MAX_PARTICIPATION:
                continue
            if rs_roll < MIN_RS_FLOOR:
                continue
            # BOOK DEPTH -- the TRAVELFOOD case, which rupees cannot see.
            if sh_roll is not None and sh_roll < MIN_SHARES_ABS:
                continue
        f["tradeable"] += 1
        _liq_seen[sym] = (round(rs_roll or 0), round(sh_roll or 0),
                          round((POSITION_RS / rs_roll * 100) if rs_roll else 0, 1))

        # ---- speed, over the last 90 seconds -----------------------------
        # COLD START. This module builds its own price history, so for the first
        # 90 seconds after ANY start it has no window to measure and every stock
        # scores 0. That would have blinded the tab through 09:15:00-09:16:30 --
        # the single most valuable minute and a half of his day -- and again for
        # 90 seconds after every restart.
        #
        # The alarm has been sweeping since 09:00 and already holds that window,
        # so it is asked instead until this module's own history is deep enough.
        covered = (h[-1][0] - h[0][0]) if len(h) >= 2 else 0
        base = [p for tt, p, _ in h if t - 90 <= tt < t]
        if covered >= 85 and base:
            rise90 = (ltp / min(base) - 1) * 100
        else:
            rise90 = None
            _delta = getattr(alarm, "delta", None)
            if _delta is not None:
                try:
                    # NEVER LOOK BACK PAST THE OPENING BELL. The alarm has been
                    # sweeping since 09:00, so a plain 90-second window at
                    # 09:15:30 reaches into the pre-open and reports the OPENING
                    # GAP as if it were a 90-second surge. A stock that gapped
                    # 5% would read as a 5% burst before it had traded for a
                    # minute -- and would drag the whole speed bar up with it,
                    # which is how the bar ran away to +2.08% this morning.
                    _p, _r, _w = _delta(sid, seconds=max(15, min(90, int(t - 33300))))
                    if _p is not None:
                        rise90 = float(_p)
                except Exception as e:                      # never silent
                    with _lock:
                        _state["err"] = f"delta: {type(e).__name__} {str(e)[:50]}"
            if rise90 is None:
                rise90 = (ltp / min(base) - 1) * 100 if base else 0.0

        # Every stock that got this far is part of today's speed distribution,
        # whether or not it qualifies. This is the sample the bar is set from.
        _r90.append((t, rise90))

        # THE GATE. A stock that is up 2% but has been flat for the last minute
        # and a half is a stock whose move already happened. He needs the ones
        # moving while he is looking at them.
        #
        # Applied to ENTRY only -- see KEEP_SEC. A fresh burst re-arms the clock,
        # so a stock that keeps going keeps its place instead of flickering.
        # SECOND WAY IN -- the steady climber.
        #
        # Measured 27-Aug against real Dhan bars: of 42 tradeable stocks that
        # climbed 2%+ continuously that morning, the burst gate alone caught
        # 10. It missed BOMDYEING (+8.97%), HCC (+5.90%), NSLNISP (+5.73%),
        # GENESYS (+4.99%), JTLIND (+4.86%), VINCOFE and RATNAVEER -- every one
        # of them a stock that walked up without ever sprinting.
        #
        # A percentile-of-speed gate is by construction blind to the move that
        # goes furthest by going steadily. So a stock also qualifies if it is
        # AT a new session high right now, has never given anything back, and
        # is already well above its open. That is a different shape of the same
        # thing he is looking for.
        climbing_hard = (since_high <= CLIMB_MAX_SINCE_HIGH
                         and fade <= CLIMB_MAX_FADE
                         and from_open >= CLIMB_MIN_FROM_OPEN)
        state = "active"
        if stopped:
            # ---- IT STOPPED OR TURNED. Watch it, do not erase it. ---------
            # 58% of these make +0.5% inside ten minutes. Two exceptions, both
            # measured: a thin stock (30% recover) and one already up 8%+
            # (41% break down). Those two are genuinely finished.
            if sym not in _qual:
                continue
            w = _watch.get(sym)
            if w is None:
                if (rs_min is not None and rs_min < WATCH_MIN_RS_MIN) or \
                        from_open > WATCH_MAX_FROM_OPEN:
                    continue                   # the two cases that do not come back
                w = _watch[sym] = {"since": t, "px": ltp,
                                   "reason": ("faded %.1f%% from its high" % fade)
                                   if fade >= FADE_PCT
                                   else "no new high for %ds" % int(since_high)}
            if t - w["since"] > WATCH_SEC:
                _watch.pop(sym, None); _qual.pop(sym, None)
                continue                       # ten minutes is long enough
            if ltp <= w["px"] * (1 - WATCH_MAX_GIVEBACK / 100.0):
                _watch.pop(sym, None); _qual.pop(sym, None)
                continue                       # it really has broken down
            if rs_min is not None and rs_min < WATCH_MIN_RS_MIN:
                _watch.pop(sym, None); _qual.pop(sym, None)
                continue                       # it has dried up
            state = "watch"
        elif rise90 >= bar or climbing_hard:
            _qual[sym] = t
            _burst_at[sym] = t                 # remember the event
            _watch.pop(sym, None)              # it is going again
            f["bursting"] += 1
        elif (BURST_MEMORY and _burst_at.get(sym)
              and t - _burst_at[sym] <= BURST_MEMORY_SEC):
            # it burst moments ago and has not stalled or faded since -- the
            # other gates simply were not all true at that exact instant
            _qual[sym] = t
            _watch.pop(sym, None)
            f["bursting"] += 1
            f["by_memory"] = f.get("by_memory", 0) + 1
        elif sym not in _qual:
            continue                       # never earned a place on the tab
        elif since_high > HOLD_MAX_SINCE_HIGH and t - _qual[sym] > KEEP_SEC:
            # A CARD STAYS WHILE THE STOCK IS STILL MAKING NEW HIGHS.
            #
            # His instruction, 27-Aug. Measured that morning, the ten-minute cap
            # dropped BBTC at 09:28 while its climb ran to 09:37 -- the tab was
            # taking the card away nine minutes before the move was over.
            #
            # So the clock now only runs once the stock has STOPPED printing new
            # highs. The stall and fade guards above still apply and are what
            # actually end a card: no new high for STALL_SEC, or FADE_PCT given
            # back from the peak. Nothing here can keep a dying stock on screen.
            continue
        held_s = t - _qual[sym]

        first = _first.setdefault(sym, now_hms)
        # URGENCY -- what should be at the top of his screen right now.
        #   still-rising beats already-risen; a fresh find beats an old one.
        age_min = (t - _sec(first)) / 60.0
        urgency = (rise90 * 3.0
                   + max(0.0, 4.0 - since_high / 60.0) * 2.0
                   + min(from_open, 8.0) * 0.5
                   + (min(rel, 10.0) if rel else 0) * 0.4
                   - min(age_min, 30.0) * 0.15)

        cand.append({
            "sym": sym, "sid": str(sid), "price": round(ltp, 2),
            "from_open": round(from_open, 2),
            "day_pct": (round(day_pct, 2) if day_pct is not None else None),
            "rise_90s": round(rise90, 2),
            "rel_vol": (round(rel, 1) if rel else None),
            "tover_cr": round(cr_day, 2),
            "rs_min": (int(rs_min) if rs_min is not None else None),
            "rs_sust": int(rs_sustained),
            "sh_min": int(shares_min),
            "live_pct": (round(live_pct * 100) if live_pct is not None else None),
            "peak": round(pk, 2), "off_peak": round(fade, 2),
            "since_high_s": int(since_high),
            "first_seen": first, "ts": now_hms,
            "held_s": int(held_s),          # 0 = bursting right now
            "state": state,
            "watch_since": (int(t - _watch[sym]["since"]) if sym in _watch else None),
            "watch_px": (_watch[sym]["px"] if sym in _watch else None),
            "watch_why": (_watch[sym]["reason"] if sym in _watch else None),
            "bar": round(bar, 2),
            "hot": bool(rise90 >= bar and since_high <= 60),
            "urgency": round(urgency, 1),
        })

    # ---- BOARD FIRST, THEN THE UNIVERSE (01-Sep-2026) --------------------
    # His instruction: "let that super stocks tab get feed from board first
    # followed by universe." He cannot watch a board of 40+ cards by scrolling,
    # so this tab is what he actually trades from -- and the stocks already on
    # the Board are the ones that passed its liquidity and price filters and
    # that he has been watching. They lead. The whole-universe sweep then adds
    # what the ScanX lists never carried -- which on 01-Sep was YATRA, a stock
    # that never appeared on the Board at all and ran from 09:16.
    #
    # Within each group the order is unchanged: urgency, still-rising first.
    # The two groups are not interleaved, so a board name is never pushed off
    # the tab by a faster stranger.
    # PRE-OPEN CONFIRMATION LEADS EVERYTHING.
    #
    # 01-Sep, measured: the pre-open tab called nine names STRONG GAP-UP at
    # 09:07:56 -- one minute before Sri reads the board. Three of those nine
    # were later confirmed here by Super Stocks: KALYANIFRG, TBZ, GODREJAGRO.
    # Those three finished the session's #1, #2 and #3 pre-open runners
    # (+12.03%, +16.19%, +3.70% from the open). The other six were never
    # confirmed and went nowhere.
    #
    # So the pre-open call ALONE is a poor signal -- six of nine were duds --
    # but a pre-open call that this tab then confirms was the single best
    # filter the board produced that morning. TBZ was carded here at 09:16:32
    # and ran +13.3% from that price; it sat below three weaker names in the
    # ordering because nothing told the tab it had been flagged 8 minutes
    # earlier. This is what tells it.
    _po = {str(x).upper() for x in (preopen_syms or set())}
    _bs = {str(x) for x in (board_sids or set())}
    for r in cand:
        r["src"] = "board" if str(r.get("sid")) in _bs else "universe"
        r["preopen"] = str(r.get("sym", "")).upper() in _po
        # The numbers the liquidity gate actually used, carried onto the card so
        # a pick can be questioned instead of taken on faith.
        _lq = _liq_seen.get(r.get("sym"))
        if _lq:
            r["liq_rs_min"], r["liq_sh_min"], r["participation"] = _lq
    cand.sort(key=lambda r: (0 if r["preopen"] else 1,
                             0 if r["src"] == "board" else 1, -r["urgency"]))
    act = [r for r in cand if r.get("state") != "watch"]
    wat = [r for r in cand if r.get("state") == "watch"]
    # A watch card can never push an active one off the screen.
    out = act[:TOP_N] + wat[:WATCH_N]
    _state["n_active"] = len(act[:TOP_N])
    _state["n_watch"] = len(wat[:WATCH_N])
    _state["from_board"] = sum(1 for r in out if r["src"] == "board")
    _state["from_preopen"] = sum(1 for r in out if r.get("preopen"))
    _state["from_universe"] = sum(1 for r in out if r["src"] == "universe")
    with _lock:
        _state["rows"] = out
        _state["ts"] = now_hms
        _state["scanned"] = scanned
        _state["qualified"] = len(cand)
        _state["funnel"] = dict(f)
        _state["bar"] = round(bar, 2)
        _state["bar_samples"] = len(_r90)
        _state["unmapped"] = unmapped
        if out:
            _state["why_empty"] = None
        elif scanned == 0:
            # This is a WIRING failure, not a quiet market, and it must never
            # again be mistaken for one.
            _state["why_empty"] = (
                f"BROKEN: the sweep returned {len(snap)} quotes but none could be "
                f"matched to a stock name ({unmapped} unmatched). "
                f"Nothing is being scanned. This is a fault, not a quiet market.")
        elif f["up2"] == 0:
            _state["why_empty"] = (f"{scanned} stocks scanned - not one is up "
                                   f"{MIN_FROM_OPEN}% from today's open yet")
        elif f["tradeable"] == 0 and f["climbing"] > 0:
            _state["why_empty"] = (
                f"{f['climbing']} stocks are up and still climbing, but not one "
                f"trades enough to get Rs {POSITION_RS:,.0f} in AND OUT. A "
                f"position may be at most {MAX_PARTICIPATION*100:.0f}% of one "
                f"minute's flow, measured over the last "
                f"{LIQ_WINDOW_SEC//60} minutes, so this needs "
                f"Rs {POSITION_RS/MAX_PARTICIPATION/1e5:.0f} lakh a minute "
                f"(floor Rs {MIN_RS_FLOOR/1e5:.0f} lakh, and "
                f"{MIN_SHARES_ABS:,}+ shares a minute for book depth)")
        elif f["climbing"] == 0:
            _state["why_empty"] = (f"{f['up2']} stocks are up {MIN_FROM_OPEN}%+ from "
                                   f"open, but every one has stalled or faded")
        else:
            _state["why_empty"] = (
                f"{f['climbing']} stocks are up and still climbing, but none is "
                f"moving fast enough right now "
                f"(today's bar is +{bar:.2f}% in 90 seconds). They have already run.")
        _state["err"] = None
    if out:
        _record(out)
    return out


#: The exact fields written to disk. THIS LIST IS NOT COSMETIC.
#
# `rs_min` and `live_pct` -- the two numbers the liquidity gate decides on --
# were added to the card and NOT added here. So when he asked "does TRAVELFOOD
# really qualify for liquidity?", the honest answer was: I cannot tell you,
# because the number the code used to let it through was never written down.
#
# That is the same failure, for the third time in this project:
#   * the UC field vanished for a session behind `except: pass`
#   * the auditor reported "0 ignition badges" because the field was on the
#     card but not in the log projection
#   * and now a liquidity gate that cannot be audited
#
# RULE: if a value can reject a stock, it goes in this tuple the same minute it
# is written. A gate nobody can see is a gate nobody can trust.
_LOGGED = ("sym", "price", "from_open", "day_pct", "rise_90s", "rel_vol",
           "tover_cr", "urgency", "first_seen",
           "rs_min", "rs_sust", "sh_min", "live_pct", "held_s", "bar", "src",
           "state", "watch_since", "watch_px", "watch_why",
           "since_high_s", "off_peak",
           # 04-Sep: "preopen" was computed by scan(), carried on the live card
           # and read by live_paper (PREOPEN_BOOST), but was NEVER in this
           # projection -- so every log ever written showed no pre-open cards at
           # all, and the handoff concluded the boost "has never fired" from
           # evidence that could not have shown it either way. The wiring was
           # intact; the instrument was blind. Exactly what the RULE above
           # warns about.
           "preopen")


def _record(rows_):
    try:
        p = LOGDIR / f"super_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(),
                                "rows": [{k: r.get(k) for k in _LOGGED}
                                         for r in rows_]},
                               separators=(",", ":")) + "\n")
    except Exception as e:
        # Never silent. A logging failure that hides itself is how the two
        # instrumentation gaps above survived as long as they did.
        with _lock:
            _state["err"] = f"log write failed: {type(e).__name__} {str(e)[:50]}"


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE))
    import Movers_alarm as alarm
    print("universe :", len(alarm.universe()))
    r = scan(alarm)
    print("scanned  :", summary()["scanned"], " qualified:", summary()["qualified"])
    for x in r[:12]:
        print(f"  {x['sym']:<13}{x['price']:>9}  open+{x['from_open']:>5}%  "
              f"90s+{x['rise_90s']:>5}%  rel{x['rel_vol']}  urg {x['urgency']}")
