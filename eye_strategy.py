"""eye_strategy.py -- the human-eye rules (tune_v6 + eye_cfg) shaped for
paper_live.py's run_book().

paper_live owns the Live Paper Trading tab. Its replay loop is already the right
shape -- previous-session warm-up, deterministic forward-only replay -- so this
swaps the BRAIN and leaves the plumbing, the control file, the snapshot and the
tab rendering exactly as they are.

Gives run_book three things per symbol:
    entry[i]  +1 where v6 wants to open a long on bar i, -1 for a short, 0 none
    exit_i[i] the bar index v6's own adaptive dip exit closes that leg on
    score[i]  selection score = volume pctile + range pctile + keep-ratio,
              all inside that stock's own session. Sri's 22 labelled entries sat
              at the 76th percentile for volume and the 92nd for range, so a
              name only qualifies when its bar is exceptional BY ITS OWN
              STANDARDS. Measured on 21-Sep's real feed, after charges:
                  no gate +3.68%   >=1.2 +6.92%   >=1.5 +43.09%   >=1.8 +17.44%
"""
import os
from pathlib import Path
import tune_v6 as V6
from eye_cfg import CFG

HERE = Path(__file__).parent
SCRATCH = HERE / "logs" / "_eye_scratch"
SELECT_MIN = 1.5
EXITMODE   = 'rhythm'   # 'dip' | 'trend' | 'rhythm'
# Measured 22-Sep through paper_live's own 2-slot run_book on 154 badged names,
# session so far, after charges:
#     dip (fixed 8-bar tolerance) .... +5.80%   29 trades
#     rhythm (scaled to the stock) ... +15.45%  30 trades   <- chosen
#     trend (hold while above EMA8) .. -0.58%   44 trades
# 'trend' looked 5x better on TRANSRAILL alone and won 83% of names in
# isolation, but through two SHARED slots it churns and loses. Stocks in
# isolation and stocks competing for capital are different questions.

# THE STOCK MUST BE GOING SOMEWHERE. Sri, 22-Sep: "if those stocks are not
# worth, then you should take stocks which are good and bumping high rather
# than flat stocks." The three losers that prompted it were dead flat --
# SHIPROCKET 128.49->128.22, SUDEEPPHRM 1182.80->1179.60, JINDWORLD 52.20->52.10.
# Measured against his own 22 labelled entries on 21-Sep:
#     up on the day   : 100% were up >= +0.5%, minimum +0.52%, median +6.50%
#     off its own high: median -0.86%, worst -3.12%
# He never bought a flat stock and never bought one far off its high.
# TESTED 23-Sep OVER 6 DAYS (16/17/18/21/22/23) AND REJECTED. Net Rs:
#     baseline (all off) .. 223,063   <- best
#     MAX_UP=6 ............ 220,358      MAX_UP=8 ..... 217,787
#     MAX_UP=10 ........... 219,641
#     OPEN_FREE=15 ........ 215,147      OPEN_FREE=30 . 213,423
#     COOLDOWN=20 ......... 217,059      COOLDOWN=45 .. 219,471
# Every one of the three had a persuasive story from a single day's losses, and
# every one lost money over six. MAX_UP does fix FRONTSP and EKC but costs
# Rs 21,000 on 17-Sep by refusing winners that were already extended.
# OPEN_FREE helps 16 and 21-Sep and costs Rs 16,000 on 22-Sep. COOLDOWN saves
# the ARCIL pair and gives back Rs 10,600 on 16-Sep. Left OFF. They stay here,
# wired and switchable, so the next idea gets measured instead of argued.
VOLX_MIN = 0.0          # 0 = off. Minimum ratio of the SIGNAL BAR's volume to
# the average of the 4 bars before it. 24-Sep, human-eye read on OFSS (-Rs 704):
# the move was already 4 bars old and volume was FADING into the signal --
# 1,243 against a 1,683 average, 74%. The engine bought the fourth bar of a
# stall. HSCL by contrast signalled on 19,362 against ~10,700, 181%, and the
# entry was fine -- that one gave its gain back on the EXIT, a different fault.
MAX_UP   = 0            # 0 = off. Ceiling on how extended a name may be at
# entry. 23-Sep: losers averaged cum 4.42 vs winners 3.06. FRONTSP made
# +Rs 12,571 entered at +3.3% on the day and lost Rs 711 entered at +13.0%;
# EKC lost Rs 2,634 at +8.2%. "Enter a mover, do not chase a blown-off one."
OPEN_FREE = 0           # minutes from 09:15 during which MIN_UP does not apply.
# 23-Sep: Sri bought KANOHAR at 09:16 with the stock DOWN 0.62% on the day and
# rode it +4%. MIN_UP=0.5 forbids that outright. At the open "up on the day"
# means almost nothing -- the day is four bars old.
MIN_UP   = 0.5          # % above today's open
MAX_OFF  = -3.5         # % below its own running high (his worst was -3.12)

# LAST ENTRY 11:30. Sri's call, 22-Sep, on the evidence of the day:
#     10:00-10:59   15 trades  60% wins   +Rs   223
#     11:00-11:59   24 trades  50% wins   +Rs 5,994
#     12:00-12:59   21 trades  24% wins   -Rs 6,152   <- gave back the morning
#     13:00-13:59   15 trades  47% wins   +Rs 3,007
# The 21-Sep study said the same thing: "13:00-15:30 is the only window that
# loses" was already written into live_paper's own ENTRY_TO. Open positions are
# NOT force-closed here -- they conclude on their own exit rule.
# DEAD ZONE -- TESTED AND REJECTED, 24-Sep. Sri, approving the 14:00 cutoff:
# "be careful while taking trades as the market become dull from 11AM onwards."
# Blocking the lull was tested over 7 days and costs money every way it is cut:
#     1400, no dead zone .......... Rs 347,804   <- best
#     1400, dead 11:30-12:30 ...... Rs 324,513
#     1400, dead 11:00-12:00 ...... Rs 324,019
#     1400, dead 11:00-12:30 ...... Rs 293,900
#     1400, dead 11:00-13:00 ...... Rs 269,401
#     1130 (the old rule) ......... Rs 275,440
# The lull is real -- a dead zone lifts the win rate every time (24-Sep: 74% to
# 82%) -- but it LOWERS net, because it idles both slots through the middle of
# the day and because trades that begin in the lull often run into the afternoon.
# The earlier "12:00 is worse than 11:30" reading was a slot-occupancy effect,
# not evidence of a bad window: stopping at 12:00 filled the slots with trades
# that were still holding when the good afternoon signals arrived.
# Left wired and OFF so the next idea about the midday can be measured, not argued.
DEAD_FROM = ""
DEAD_TO   = ""

# LAST ENTRY 11:30 -> 14:00, 24-Sep, on Sri's approval. Seven days, current
# config: 11:30 Rs 275,440 | 13:00 Rs 327,319 | 14:00 Rs 347,804 | 15:05
# Rs 349,063. 14:00 beat 11:30 on every day that produced afternoon signals
# (18/21/22/23/24-Sep); 16 and 17-Sep produced none either way. 15:05 is a hair
# higher but leaves no room before the 15:15 square-off, so 14:00 it is.
# The win rate falls (24-Sep 88% -> 74%) while the money rises: more trades, a
# lower hit rate, a bigger total.
LAST_ENTRY = "1400"

# SHORTS. Sri's own 21-Sep book was 3 shorts of 22 trades, worth Rs 10,738 --
# roughly a fifth of his names and a real slice of the money, so a long-only tab
# was leaving them on the table. The setup is NOT a mirror of the long one: the
# long gates (MIN_UP, MAX_OFF) say "up on the day and near its high", while
# eye_cfg's learned shorts gate is SHORTCUM = 6, i.e. only fade a name that is
# already up 6%+ and rolling over. So MIN_UP/MAX_OFF are applied to longs only;
# shorts inherit v6's own gate plus the universal ones (clock, score, leg room).
#
# RE-ENABLED 24-Sep. The rejection below was measured at MIN_LEG=4 with score
# ranking -- a baseline that no longer exists. Re-tested under the CURRENT
# config (RANK=leg, MIN_LEG=10, 2 slots) over 16/17/18/21/22/23-Sep:
#     shorts off .. Rs 223,063
#     shorts on ... Rs 230,992   shorts' own contribution +Rs 19,717
# They fired on 4 of the 6 days and LOST ON NONE of them (+12,531, +2,000,
# +3,014, +2,172). Yesterday's 17-of-19 losers were all short legs; MIN_LEG=10
# filters exactly those out. Only 17-Sep is worse overall (-9,277) and that is
# displacement of longs, not short losses.
# CAVEAT: 11 short trades over 6 days is thin. Watch it.
#
# The original, now-superseded finding:
# SHIPPED OFF. Implemented, verified (P&L, stop and trail all sign-checked
# against hand arithmetic) and then walk-forwarded through this same engine over
# 16/17/18/21/22-Sep. The shorts lost money on 4 of those 5 days, 17 of 19
# trades were losers, and they cost roughly Rs 11,000 net at 2 slots -- and that
# is before the displacement cost, because every short also occupies a slot a
# long would have used. Shorts-on was worse than shorts-off at EVERY slot count:
#     2 slots   shorts off +Rs 58,614     shorts on +Rs 20,758
#     3 slots   shorts off +Rs 59,008     shorts on +Rs 36,635
# Sri's own 3 shorts on 21-Sep made Rs 10,738, so the SETUP is real -- what is
# missing is the gate that tells his 3 from v6's 19. Flip this to True to trade
# them anyway; nothing else needs changing.
# WHY YOU SEE NO SHORTS (diagnosed 28-Sep, NOT a bug):
#   V6.sim produced 123 shorts on 28-Sep; 68 of them (55%) cleared SELECT_MIN
#   -- a BETTER pass rate than longs (48% of 3,015). They are not filtered out
#   by any gate here. They lose the RANKING contest in paper_live.run_book:
#   68 short candidates against 1,457 long candidates for 2 slots.
#   To actually trade shorts you must reserve a slot or rank them separately.
#   Note also the 23-Sep walk-forward: shorts lost at EVERY slot count
#   (17 of 19 were losers), so proving they help comes first.
SHORTS = False   # Sri's Dhan account is LONG-ONLY -- never emit short signals (01-Oct)

# MIN_LEG 4 -> 10, 23-Sep. The single biggest thing wrong with the logic, found
# by asking what today's 6 winners had at ENTRY that the 10 losers did not. Not
# the score, not the volume, not the angle -- all near-identical. It was the
# length of the leg v6 itself predicted:
#     winners  avg 12.5 bars     losers  avg 7.5 bars
#     leg <= 7 bars: 5 trades, ALL losers, -Rs 4,917, not one winner
# A short leg cannot pay. Rs 135 of charges on a Rs 2,50,000 round trip needs
# 0.054% just to break even, and a 5-bar leg rarely travels that far. Every one
# of today's small red numbers was a leg that was never long enough to pay for
# itself. Walk-forwarded through the real 2-slot engine over 5 days -- the
# mistake NOT repeated from the trend-exit episode, where isolation lied:
#     MIN_LEG    4        6        8       10       12
#     16-Sep  -1,648  +18,910  +24,551  +33,599  +33,277
#     17-Sep  21,585  +39,273  +41,976  +65,761  +58,961
#     18-Sep   3,644   +4,687   +9,051  +11,717   +9,862
#     21-Sep   8,346   +9,989  +14,652  +24,295  +36,249
#     22-Sep  26,687  +30,964  +17,017  +42,010  +52,174
#     TOTAL   58,614  103,823  107,247  177,382  190,523
# Monotone on every day independently, which is what separates a real effect
# from a fitted one. 12 totals more but wins on only 2 of 5 days and leans on
# 15-31 trades; 10 is profitable on all 5, best on 3, and lifts the win rate
# from 45-48% to 65-74%. Taking 10.
# ============================ LOOK-AHEAD FIX, 28-Sep ========================
# MIN_LEG MUST STAY 0. It gated entry on `j - i`, where j is the bar the trade
# ENDS on -- a value that cannot exist until the trade is over. With MIN_LEG=10
# the engine booked entries at prices from 9-17 bars (4.5-8.5 min) BEFORE the
# signal could be computed. Measured by truncating the tape and asking when
# entry[i] first appears:
#     MIN_LEG=10 -> CLEANMAX 9 bars late, PROTEAN 9, ZENSARTECH 17
#     MIN_LEG=0  -> 95 of 123 funnel names causal (lag <= 1 bar, which is
#                   correct: signal on bar i's close, buy at bar i+1's open)
# 28-Sep through the real engine, DISPLACE=1.5:
#     MIN_LEG=10, all names ............ Rs 134,540   57W/20L (74%)  <- fiction
#     MIN_LEG=0,  all names ............ Rs 122,723   63W/34L (65%)
#     MIN_LEG=0,  causal names only .... Rs  80,594   49W/43L (53%)  <- best est.
# The win rate falling 74% -> 53% is the hindsight coming out. 53% is what a
# real 30-second scalper looks like.
# STILL OPEN: 28 of 123 names (23%) remain non-causal for reasons not yet found
# (suspect rest75, computed from the first 40 bars). Run
# `python causality_audit.py <day>` after ANY change to analyse().
# EVERY OTHER KNOB in this file and in paper_live.py was tuned against the
# look-ahead numbers and is therefore UNVALIDATED: MIN_UP, MAX_OFF, SELECT_MIN,
# TRAIL_PCT, DISPLACE, SLOTS, LAST_ENTRY. Re-measure before trusting any of them.
MIN_LEG = 0             # bars. paper_live buys at bars[i+1], while eye_out is
# the exit of a leg that began at bar i -- so a leg with only a bar or two left
# opened ALREADY PAST its own exit and closed on the next tick, paying Rs 135
# of charges for a 30-second hold. Sri spotted the run of small red numbers:
# SHIPROCKET 10:32:30->10:33:00 -Rs 660, SUDEEPPHRM -Rs 810, JINDWORLD -Rs 614.
# A signal is only worth taking if the leg still has real room in it.

# MIN_EXP -- OFF by default, under test 26-Sep.
# Rs 136 of charges on a Rs 2,50,000 round trip is 0.054% before the trade has
# done anything. MIN_LEG=10 asks whether the leg is long enough in BARS; this
# asks whether it is long enough in MONEY: predicted leg length x how far this
# stock actually travels per bar lately. A 10-bar leg on a stock drifting
# 0.01% a bar cannot pay for itself no matter how clean the setup looks.
# Expressed as a multiple of the round-trip cost, so MIN_EXP = 3 means
# "only take it if the leg is expected to travel at least 3x the charges".
#
# TESTED 26-Sep against 25-Sep through the real 2-slot engine. REJECTED, OFF:
#     MIN_EXP   0       2       3       4       6       8
#     25-Sep  68,570  68,570  68,570  68,570  65,623  65,623
#                                             (-2,947, two fewer trades)
# Nothing at all up to 4x, then it starts removing WINNERS. The premise was
# wrong. The trades that finish small are not the ones with a small predicted
# move: 15 trades that day netted under Rs 200 and this filter caught none of
# them, because what makes a trade finish small is the EXIT cutting it short
# (rhythm/trail), not the leg lacking room at entry. An entry filter cannot
# fix something the exit decides. If the small-change trades are worth
# attacking, the exit is where to look.
# CAVEAT: one day only, by instruction. Do not promote on this evidence.
#
# NOTE if you re-run this: eye_strategy._cache is keyed (sym, nbars) and MUST
# be cleared between knob values, or every run after the first silently
# replays the first run's decisions. That bug made the first sweep read
# "no effect at any level" across six values and nine days.
MIN_EXP = 0.0
_RT_COST_PCT = 0.0544      # Rs 136 on Rs 2,50,000, both legs


def _exp_move_pct(d, i, j, look=10):
    """Predicted leg length x recent per-bar travel, in percent."""
    lo = max(0, i - look)
    if i <= lo:
        return None
    rng = 0.0
    n = 0
    for k in range(lo, i):
        c = float(d["C"][k])
        if c > 0:
            rng += abs(float(d["H"][k]) - float(d["L"][k])) / c
            n += 1
    if not n:
        return None
    return (rng / n) * 100.0 * (j - i)


def _open_plus(mins):
    """HHMM string `mins` minutes after 09:15, for the OPEN_FREE window."""
    t = 9 * 60 + 15 + int(mins)
    return "%02d%02d" % (t // 60, t % 60)


_cache = {}          # (sym, nbars) -> (entry, exit_i, score)


def analyse(sym, bars):
    """bars: paper_live's list of {hhmm,o,h,l,c,v}, previous session then today.
    Cached on bar count, so re-running the morning every cycle stays cheap."""
    key = (sym, len(bars))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    n = len(bars)
    blank = ([0] * n, [None] * n, [0.0] * n)
    if n < 40:
        return blank
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        rows = []
        for b in bars:
            hh = str(b.get("hhmm") or "")[:5].replace(":", "")
            o, h, l, c = b.get("o"), b.get("h"), b.get("l"), b.get("c")
            if not (hh and c):
                return blank
            rows.append(f"{hh},{o or c:g},{h or c:g},{l or c:g},{c:g},{int(b.get('v') or 0)}")
        # PER-PROCESS SCRATCH, 1-Oct. This path used to be pl_<sym>.csv, shared
        # by every process. paper_live and paper_tiers both call analyse() on
        # the same symbols at the same time, so one would overwrite the other's
        # CSV mid-read and V6.prep came back with a different number of rows
        # than `bars` had. That surfaced as
        #     ValueError: operands could not be broadcast together
        #                 with shapes (747,) (815,)
        # which cost the live engine a whole cycle at 09:57 on 30-Sep and again
        # during testing on 1-Oct. The pid makes the file private to the caller.
        f = SCRATCH / f"pl_{sym}_{os.getpid()}.csv"
        f.write_text(";".join(rows))
        d = V6.prep(str(f))
        trades = V6.sim(d, dict(CFG, SLOTS=99, MAXTR=999, EXITMODE=EXITMODE))
    except Exception:
        return blank

    import numpy as _np
    _H = d["H"]; _st = d["st"]
    if len(_H) != n:            # belt and braces -- never raise into the engine
        return blank
    runhi = _np.maximum.accumulate(_np.where(_np.arange(n) >= _st, _H, -1e9))
    entry, exit_i, score = [0] * n, [None] * n, [0.0] * n
    for t in trades:
        side = t["side"]
        if side == -1 and not SHORTS:
            continue
        i, j = t["i"], t["j"]
        if not (0 <= i < n):
            continue
        sc = float(d["volq"][i] + d["rngq"][i] + min(d["keep"][i], 1.0))
        score[i] = round(sc, 3)
        if LAST_ENTRY and d["T"][i] > LAST_ENTRY:
            continue                  # past the last-entry clock
        if DEAD_FROM and DEAD_TO and DEAD_FROM <= d["T"][i] < DEAD_TO:
            continue                  # midday lull -- stand aside
        if side == 1:
            _free = OPEN_FREE and d["T"][i] <= _open_plus(OPEN_FREE)
            if not _free and d["cum"][i] < MIN_UP:
                continue              # flat or down on the day
            if MAX_UP and d["cum"][i] > MAX_UP:
                continue              # already blown off -- do not chase
            if runhi[i] > 0 and (d["C"][i] - runhi[i]) / runhi[i] * 100 < MAX_OFF:
                continue              # too far off its own high
        if VOLX_MIN:
            _lo = max(0, i - 4)
            _av = float(d["V"][_lo:i].mean()) if i > _lo else 0.0
            if _av > 0 and float(d["V"][i]) / _av < VOLX_MIN:
                continue          # volume fading into the signal -- a stall
        if SELECT_MIN and sc < SELECT_MIN:
            continue                      # not exceptional for this name
        if MIN_EXP:
            _em = _exp_move_pct(d, i, j)
            if _em is not None and _em < MIN_EXP * _RT_COST_PCT:
                continue          # the leg cannot travel far enough to pay
        if j - i < MIN_LEG:
            continue                  # leg already nearly over -- do not enter
        entry[i] = side               # +1 long, -1 short
        exit_i[i] = j
    out = (entry, exit_i, score)
    _cache[key] = out
    if len(_cache) > 400:
        _cache.clear()
    return out
