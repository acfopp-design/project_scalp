"""### ACTIVE ENGINE ### paper_live.py -- this is the module that TRADES.
NOT to be confused with live_paper.py, which is the LEGACY engine and only
runs when EYE_LOGIC = False. If you are editing trading behaviour, you are in
the right file.
"""
"""paper_live.py -- LIVE PAPER TRADING with the strategy proven on 07-Sep.

Sri, 07-Sep evening: "fix this indicator and logic for tomorrow morning 9:15AM
paper trading... I will manually click the Start Live Paper trade button... I
should also get options of stop and clear trades table... ensure this is not
impacted by Board and Super Stocks."

WHAT IT TRADES
    Universe   Sri's own funnel: Dhan VOLUME shockers (no penny stocks), Dhan
               PRICE shockers (no thin stocks) and all four MyWatchlist lists.
               A stock becomes tradeable only from the moment the Board's panels
               actually showed it -- never earlier.
    Entry      HOTT/LOTT says UPTREND (close above HOTT, not in the flat zone)
               AND JustUncleL's Pullback ALT fires: EMA8>EMA21>EMA50, price dips
               to the EMA8 and closes back above it.
               When several fire at once, the one whose volume has expanded most
               out of its own quiet base wins.
    Size       ONE position, the whole Rs 5,00,000. Measured against 5 x
               Rs 1,00,000 on 07-Sep: 29,698 vs 9,565.
    Exit       -1% stop, 1.2% back off the peak, circuit guard, or square-off.

    07-Sep, forward-only, 09:16-10:30, this exact configuration: Rs 29,698.
    HONEST WARNING: that is ONE morning. The same family of rules lost money on
    04-Sep. Treat tomorrow as evidence-gathering, not as an expectation.

CONTROL -- it does NOTHING until Sri presses Start
    logs/paper_control.json  {"active": bool, "started": "HH:MM:SS", "reset_at": ...}
    written by the Board's Live Trading buttons (start / stop / reset).
    Trades are only opened at or after `started`, so pressing Start at 09:40
    begins a fresh session from 09:40 rather than back-filling the morning.

ISOLATION
    Imports nothing from Movers_app, live_paper, superstocks or paper_engine's
    state. It reads the Board's log files and writes its own. Nothing it does can
    change what the Board or the Super Stocks tab show.
"""
import json, os, signal, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import paper_engine as PE          # charges only -- no shared state
import combos2 as C2
import dirlib as DL                # Linear Regression Candles
import human_eye as HE             # _rsi / _sma for the RSI-smoothing exit
import funnel as FN
import leverage as LV
import Opus_indicators as IND        # per-stock intraday leverage (Rule A)
import live_shadow as LS           # reuse its proven Dhan 30-second fetch

CAPITAL, LEVERAGE = 100_000.0, 5.0     # defaults; the Board's boxes override
TARGET_PCT = 10.0
BOOK = CAPITAL * LEVERAGE
DEFAULTS = (CAPITAL, LEVERAGE, TARGET_PCT)
STOP_PCT, TRAIL_PCT = -1.0, 1.2

# When is the trail allowed to fire?
#   "close" = legacy. Only while the close is STILL above entry (long). A drop
#             violent enough to cross from the peak to below entry inside one
#             30s bar DISARMS the trail completely -- proven on RATNAVEER,
#             25-Sep 10:20:30, which then bled to a Rs 1,327 loss.
#   "peak"  = fire once the leg has EVER been in profit. Turns a profit-lock
#             into a real give-back cap.
# TESTED 27-Sep on 25-Sep through the real 2-slot engine. REJECTED, left at
# "close". Arming on the peak rescues RATNAVEER but dumps trades that recover:
#     TRAIL_PCT   0.8      1.0      1.2      1.5      2.0
#     close     44,740   58,964   68,570   66,450   43,704
#     peak      49,461   61,308   67,123   66,450   43,704
# It only helps at trails tighter than we run. At 1.2 it costs Rs 1,447.
# NOTE ALSO: 1.2 is a SHARP one-day optimum (1.0 loses 9,606; 2.0 loses 24,866).
# Treat the headline number as fitted to 25-Sep until it is checked on more days.
TRAIL_ARM = "close"
MIN_HOLD = 3             # bars; nobody exits 90 seconds after buying
# WARM_MIN was 55 -- 55 bars of 30s is 27.5 minutes, so no entry was possible
# before ~09:42 on today-only data, and the whole opening surge was invisible.
# Sri, 08-Sep: "eliminate your 9:28 dependency completely." History now comes
# from the warm-up load, not from refusing to trade until enough bars pile up.
WARM_MIN = 0

# THE TWO FILTERS THAT WERE IN THE RESEARCH ENGINE AND NOT IN THIS ONE.
# 08-Sep 09:39: without them the live engine had taken 7 signals in 14 minutes
# -- BEL, BHEL, GMRAIRPORT, ASTEC, CYIENTDLM -- mean +0.04%, one of them on
# volume that was CONTRACTING (0.4x). "EMA8>21>50 with a dip back above the
# EMA8" is something a big liquid stock satisfies all day whether or not it is
# moving, so the entry rule needs to be told that the stock must actually be
# going somewhere. Sri, that morning: "I see 100's of good stocks picked
# momentum. You got stuck with BEL."
# Sri, 08-Sep 09:52: "Change the LOGIC completely. Use only the indicator we
# discussed yesterday night." So the filters below are OFF. Every extra rule I
# added was mine, not the indicator's, and the 07-Sep table that made HOTT/LOTT +
# Pullback ALT the pick was measured WITHOUT them. Volume expansion still ranks
# the candidates when more fire than there are slots -- that is an ordering, not
# a filter, and nothing is refused because of it.
MIN_DAY_PCT = 0.0        # OFF -- was 2.0
MIN_VOLX = 0.0           # OFF -- was 1.5

# SLOTS -- 08-Sep, slot_study.py over 03/04/07-Sep with the filters on:
#   1 slot  Rs  8,326      3 slots Rs 21,314      5 slots Rs 12,252
# One slot lost to three on every one of the three days, so the earlier "one
# position is best" finding is dead -- it had been measured with an entry rule
# that fired on sluggish large caps, so it never compared concentration at all.
# Sri chose five: "Leverage 5 slots at a time." Rs 1,00,000 each, his own margin.
# ===========================================================================
#  EYE_LOGIC -- 22-Sep. Entries, exits and stock selection come from the
#  human-eye rules (tune_v6 + eye_cfg via eye_strategy.py). Everything else on
#  this tab is untouched: the control file, sizing, Dhan charges, the snapshot.
#  Set EYE_LOGIC = False to go straight back to linreg candles.
#  Measured on 21-Sep's real feed, 2 slots, after charges: +43.09%.
#  One day, and the selection threshold was fitted on it.
# ===========================================================================
# RANK -- walk-forwarded 16/17/18/21/22/23-Sep through this engine, 2 slots,
# MIN_LEG=10. Net Rs: score 206,080 | score_then_leg 212,694 | leg 223,137.
# "leg" wins on 4 of 6 days, ties one, loses one by Rs 253. It also has a
# MECHANISM rather than just a number: the score cannot rank anything in the
# first minutes because it is pinned at its 3.00 ceiling, so the slots were
# going to whichever tied name the dict yielded first. On 23-Sep it takes
# OLAELEC at 09:15:30 for +Rs 11,296 -- the trade Sri took himself and the
# engine skipped -- and lifts the day from Rs 28,698 to Rs 37,979.
# CLOUD GATE, 28-Sep. Sri, on ANTELOPUS 25-Sep: "green trend + ichimoku cloud +
# DISTANCE between trend and the cloud". Measured over 264 causal trades on
# 22/25/28-Sep, distance above the cloud at entry grades the outcome cleanly:
#     below cloud  46 tr  avg Rs   939  win 52%
#     0-1% above  119 tr  avg Rs   393  win 61%
#     1-2% above   53 tr  avg Rs   857  win 64%
#     2-4% above   33 tr  avg Rs 1,393  win 61%
#     >4%  above   13 tr  avg Rs 2,692  win 77%
# Ichimoku is causal here: the cloud at bar i is built from bars <= i-26.
# 0 = off. Set to a % to require that much clearance before entering.
MIN_CLOUD_PCT = 0.0

# TREND EXIT, 28-Sep. Sri held ANTELOPUS 25-Sep for 48 min (+8.49%) and took
# RATNAVEER at 16 min -- ONE rule, not two: stay while the Heiken-Ashi/ATR
# trend holds, leave when it flips. A fixed % trail decides the duration
# itself, which is why it cuts trends short AND gives fades back.
# 0 = off (use the % trail). 1 = exit when ualgo_trend flips against us.
# TESTED 28-Sep over 22/25/28-Sep causal, REJECTED, left OFF. Six variants,
# all worse than the plain 1.2% trail, and all with SHORTER holds -- the
# opposite of the intent. A supertrend on 30-second bars flips on noise:
#     trail 1.2 (baseline) 224,725   median hold 5.0 min
#     trend 10/2           205,191   3.5      trend 10/3  216,684  3.0
#     trend 20/2           208,453   3.0      trend 20/3  201,571  3.0
#     trend 20/3 + trail   203,325   3.0
TREND_EXIT = 0
TREND_ATR, TREND_MULT = 10, 2.0

COUNT_CROWDED = True    # record signals turned away because both slots were busy
CROWD = {"bars": 0, "signals": 0, "names": {}}

# SHIPPED 28-Sep. Measured through the real engine on three days, every value
# beating OFF on every day -- the most consistent result we have found:
#     DISPLACE   0        1.5       2        3
#     22-Sep   17,059   35,549   38,061   34,939
#     25-Sep   68,570  127,732  118,336  109,769
#     28-Sep*  13,835   19,037   13,105   16,145     (*part day, to ~10:45)
#     TOTAL    99,464  182,318  169,502  160,853
# 1.5 wins on total and on two of the three days; 2 is close behind and churns
# less. Was 0 because the engine had no way to let go of a weak position for a
# far better one -- AEQUS, 28-Sep, signalled at 10:11 at Rs 259.56 behind a
# LOSING GESHIP and was finally bought at 10:23 at Rs 272 for -Rs 2,570.
DISPLACE_MODE = "causal"   # "causal" = judge on what is visible now.
                           # "legacy" = the old rule, which reads eye_out
                           # (where the leg ENDS) and so peeks at the future.
DISPLACE = 1.5          # a new signal must have a leg this many times longer
                        # than what remains of the weakest holding's, to take its
                        # slot. 0 = off. See run_book.
MIN_LEG_DISPLACE = 12   # and the newcomer's own leg must be at least this long
COOLDOWN_MIN = 0        # minutes a LOSING name is barred from re-entry; 0 = off
RANK = "leg"            # "score" | "leg" | "score_then_leg" -- see run_book
EYE_LOGIC = True
EYE_EXIT = True    # the adaptive dip exit. 30-Sep: it cut BLEL at 512.80,
                   # three bars before the move to 524.80 it was bought for.
try:
    import eye_strategy as EYE
except Exception as _e:
    EYE = None
    EYE_LOGIC = False
    print("eye_strategy unavailable, keeping linreg candles:", _e)

SLOTS = 2 if EYE_LOGIC else 5
BOOTF = HERE / "logs" / "paper_live.boot"
# SQUARE_OFF 15:15 -> 15:05, 26-Sep. Dhan force-closes intraday positions at
# 15:10 ("Intraday orders are square-off automatically at 03:10 pm on NSE/BSE,
# or on hitting 80% margin utilisation, whichever earlier" - Dhan Margin tab).
# Squaring off at 15:15 meant Dhan would have closed the position five minutes
# earlier, at its own price and moment, and possibly with an auto-square-off
# fee. 15:05 keeps the exit ours. Dormant in practice so far - with
# LAST_ENTRY=1400 no position on 22/23/24/25-Sep survived past 14:35 - but it
# costs nothing to be the one holding the exit.
OPEN_T, SQUARE_OFF = "09:15:00", "15:05:00"

# CIRCUIT GUARD. A scrip locked at a circuit stops being tradeable: the book
# empties on the side you need and the position cannot be closed at any price
# until the band is revised. Getting stuck matters far more than the last
# fraction of the move, so a position is closed as price APPROACHES its band.
# Bands are set per scrip per day and are filled in by the live loop; when they
# are unknown (replay of an old day, or a fetch that failed) the guard simply
# does not fire, which is the same behaviour as before it existed.
CIRCUIT_BUF_PCT = 0.50      # exit within this % of the band. 0 = guard off
CIRC = {}                   # sym -> (lower, upper) for today
REFRESH = 20
MIN_ABOVE_PCT = 0.0   # close must be this %% above EMA8   (0 = off)
MIN_SLOPE_PCT = 0.0   # EMA8 3-bar rate of change, %%      (0 = off)
MIN_SEP_PCT   = 0.0   # EMA8 must be this %% above MA12    (0 = off)
MAX_ENTRY_POS = 100   # 100 = off. Refuse longs above this %% of the recent range.
HOT_FRESH = 20     # freshest movers swept every cycle, with whatever we hold
REST_EVERY = 4     # the long tail is swept every Nth sweep
CTRL = HERE / "logs" / "paper_control.json"
SNAP = HERE / "logs" / "paper_live_{}.json"
LOG = HERE / "logs" / "paper_live_{}.log"
PIDF = HERE / "logs" / "paper_live.pid"


_PINS = {"day": None, "off": 0, "syms": set()}


# ---------------------------------------------------------------- journal --
# ONCE A TRADE IS TAKEN, IT IS TAKEN. Sri, 30-Sep: "Once trade taken is taken
# and has to be there in trading table profit or loss whatever it is."
#
# run_book() is a stateless replay: every cycle it rebuilds the whole session
# from the tape, so as new bars arrive it can re-decide and a trade that was on
# the board a minute ago can simply vanish. That happened repeatedly on 30-Sep
# -- six names were "held" at some point and the final book contained none of
# them -- and it makes every intraday number untrustworthy.
#
# The journal is append-only and keyed on (symbol, entry time). The FIRST
# closed version of a trade is the one that stands; later replays may not
# revise or delete it. It survives a restart, and it is what the board shows.
JOURNAL = HERE / "logs" / "TRADE_JOURNAL_{}.json"
_JRN = {"day": None, "rows": {}}


def journal_merge(day, closed, start_t):
    """Fold this cycle's closed trades into the permanent book, return the book."""
    if _JRN["day"] != day:
        _JRN["day"] = day
        _JRN["rows"] = {}
        try:
            _JRN["rows"] = json.loads(
                Path(str(JOURNAL).format(day)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    new = 0
    for c in closed or []:
        if not c.get("in_t") or c["in_t"] < start_t:
            continue
        k = "%s|%s" % (c["sym"], c["in_t"])
        if k not in _JRN["rows"]:
            _JRN["rows"][k] = c
            new += 1
    if new:
        try:
            p = Path(str(JOURNAL).format(day))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_JRN["rows"], default=str), encoding="utf-8")
        except OSError:
            pass
    return sorted(_JRN["rows"].values(), key=lambda r: r.get("in_t") or "")


def tradeable(day):
    """The day's tradeable set: the board's no-dip badges, plus anything our
    own scanner flagged. Union, never a replacement -- turning the scanner off
    returns the engine to exactly its previous behaviour."""
    out = set(nodip_watchlist(day))
    try:
        import surge as SG
        out |= SG.names(day)
    except Exception:
        pass
    return out


def nodip_watchlist(day):
    """Sri, 09-Sep: "I want all the stocks with no-dip badges to be in that
    list... if the stock is displayed in the board with a no-dip badge at least
    ONCE today, put it in the list. Once you put that in your list, consider
    that is my watchlist for the day."

    The badge is the Board's `pinned` flag (PIN_MIN_BARS consecutive no-dip
    candles + a real climb + green on the day). It is written into every card
    record in logs/movers_board/board_<day>.jsonl. A stock never leaves this
    list once it has earned the badge, even after the pin releases.

    The board log reaches ~110 MB by the close, so this reads only the bytes
    added since the last call and keeps the set in memory.
    """
    f = HERE / "logs" / "movers_board" / ("board_%s.jsonl" % day)
    if _PINS["day"] != day:
        _PINS.update(day=day, off=0, syms=set())
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(_PINS["off"])
            while True:
                line = fh.readline()
                if not line:
                    break
                if not line.endswith("\n"):      # half-written last line
                    break
                _PINS["off"] += len(line.encode("utf-8", "replace"))
                if '"pinned": true' not in line and '"pinned":true' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                for rows in (d.get("panels") or {}).values():
                    for r in rows or []:
                        if r.get("pinned") and r.get("sym"):
                            _PINS["syms"].add(r["sym"])
    except OSError:
        pass
    return set(_PINS["syms"])


def control():
    """The control file with a DAY STAMP applied.

    23-Sep: the file carries `started` as a bare "HH:MM:SS" and nothing said
    which day it belonged to, so yesterday's value survived the night. Today the
    engine came up at 09:30 holding started="10:20:06" from 22-Sep and would
    have refused to open a single trade until 10:20 -- silently, with the tab
    showing ACTIVE the whole time. 09:15-11:30 is the only window this logic
    makes money in, so that is the entire day.

    A `started` from any earlier day is ignored and the session opens at
    OPEN_T, which is the 09:15 auto-start Sri asked for.
    """
    try:
        c = json.loads(CTRL.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False, "started": None, "reset_at": None}
    today = datetime.now().strftime("%Y%m%d")
    if c.get("started") and c.get("day") != today:
        c["started"] = OPEN_T
        c["active"] = True
        c["day"] = today
        try:
            CTRL.write_text(json.dumps(c), encoding="utf-8")
        except OSError:
            pass
    return c


def apply_control(ctrl):
    """The Board's capital / leverage / target boxes are the source of truth.

    Without this the tab would show whatever Sri typed while the engine quietly
    traded Rs 5,00,000 -- the kind of silent mismatch that makes a P&L number
    worthless.
    """
    global CAPITAL, LEVERAGE, BOOK, TARGET_PCT
    d_cap, d_lev, d_tgt = DEFAULTS
    try:
        # fall back to the DEFAULTS, not to whatever the last call left behind,
        # so a reset really does clear the numbers on screen
        CAPITAL = float(ctrl.get("capital") or d_cap)
        LEVERAGE = float(ctrl.get("leverage") or d_lev)
        TARGET_PCT = float(ctrl.get("target") or d_tgt)
    except (TypeError, ValueError):
        CAPITAL, LEVERAGE, TARGET_PCT = d_cap, d_lev, d_tgt
    BOOK = CAPITAL * LEVERAGE
    return BOOK


def strategy(bars):
    """Sri, 08-Sep: "replace this reg candle logic for paper trading with
    existing one."

    WAS: C2.combined(bars, C2.pullback_alt) -- HOTT/LOTT + Pullback ALT.
    Measured 08-Sep on his eight human-eye stocks, one slot of Rs 1,00,000,
    same exits and real charges:
        HOTT/LOTT + Pullback ALT   17 trades   net Rs    457
        Pullback ALT alone         18 trades   net Rs -2,209   <- only NEGATIVE
        Linear Regression Candles  91 trades   net Rs 20,656   <- best tested
    Pullback ALT was written from the author's description, never verified
    against Pine, and it was dragging HOTT/LOTT down from Rs 8,713 to Rs 457.
    """
    if EYE_LOGIC:
        # 22-Sep: replaced by the human-eye rules (tune_v6 + eye_cfg), the ones
        # matched to Sri's own 22 trades on 21-Sep. run_book() calls eye_strategy
        # directly for entries, exits and the selection score, so this returns a
        # flat list and is no longer the decision maker.
        return [0] * len(bars)
    return DL.linreg_candles(bars)


def day_pct(bars, i, d0):
    """Percent move on the day at bar i.

    The reference is the previous session's last close when the warm-up has it,
    and today's opening print otherwise -- weaker, but it still refuses a stock
    that has gone nowhere since the bell.
    """
    ref = bars[d0 - 1]["c"] if d0 > 0 and bars[d0 - 1].get("c") else None
    if not ref:
        ref = bars[d0]["o"] or bars[d0]["c"] if d0 < len(bars) else None
    if not ref:
        return 0.0
    return (bars[i]["c"] / ref - 1) * 100


def volx(bars, i, win=6, base=20):
    a = bars[max(0, i - win + 1):i + 1]
    b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
    if not a or not b:
        return 0.0
    ca = sum((x["v"] or 0) for x in a) / len(a)
    cb = sum((x["v"] or 0) for x in b) / len(b)
    return ca / cb if cb else 0.0


def _slip_rs(qty, price, v, hi, lo, c):
    """Rupee cost of pushing `qty` through one 30s bar that traded `v` shares.

    Measured 27-Sep on 25-Sep's tape: the median trade asks for 31% of a bar's
    entire volume and 23 of 69 asked for MORE than the bar contained. Taking a
    third of a bar moves the price; we book the fill as if it did not. This
    charges half the bar's range, scaled by how much of the bar we take.
    Reporting only -- it does not change any trading decision.
    """
    try:
        if not (qty and price and c):
            return 0.0
        part = 1.0 if not v or v <= 0 else min(1.0, float(qty) / float(v))
        rng = (float(hi or c) - float(lo or c)) / float(c)
        return abs(qty) * float(price) * part * rng * 0.5
    except Exception:
        return 0.0


def run_book(tape, d0, avail, start_t, now_t):
    """Deterministic forward-only replay of the session so far. Re-running the
    whole morning each cycle is safer than carrying mutable state through a live
    loop -- the engine is deterministic, so it yields the same decisions plus
    whatever the newest candle adds.

    `tape` is the PREVIOUS session's bars followed by today's, and d0[sym] is
    where today starts. The indicators are computed over the whole thing, so
    EMA50 and HOTT/LOTT are alive at 09:15:00 instead of being undefined until
    roughly 09:40; the clock and the index cover today only, so nothing from
    yesterday can ever be traded. Running without the warm-up was why the funnel
    sat at zero all morning.
    """
    dirs = {s: strategy(b) for s, b in tape.items()}
    trendser = {}
    if TREND_EXIT:
        for _s, _b in tape.items():
            try:
                trendser[_s] = IND.ualgo_trend(
                    [x.get("o") or x.get("c") or 0 for x in _b],
                    [x.get("h") or x.get("c") or 0 for x in _b],
                    [x.get("l") or x.get("c") or 0 for x in _b],
                    [x.get("c") or 0 for x in _b],
                    mult=TREND_MULT, atr_len=TREND_ATR)["trend"]
            except Exception:
                trendser[_s] = None
    cloudtop = {}
    if MIN_CLOUD_PCT:
        for _s, _b in tape.items():
            try:
                _A, _B = IND.ichimoku_series([x.get("h") or x.get("c") or 0 for x in _b],
                                             [x.get("l") or x.get("c") or 0 for x in _b])[:2]
                cloudtop[_s] = [None if (a is None or bb is None) else max(a, bb)
                                for a, bb in zip(_A, _B)]
            except Exception:
                cloudtop[_s] = None
    eye = {s: EYE.analyse(s, b) for s, b in tape.items()} if EYE_LOGIC else {}

    # GEOMETRY, 30-Sep. Sri's entry conditions were never in the decision path:
    # the engine gated on the selection score alone -- no EMA/MA separation, no
    # slope, no MACD, no RSI. Verified on LTTS 13:58, which entered with the
    # candle 0.10% above EMA8 and the two lines 0.05% apart -- on a chart they
    # are merged, the exact state he calls "no trend, don't trade".
    # Measured over 186,741 bar-observations today: the weak version of this
    # state has NEGATIVE forward returns, the strong version (>0.5% above,
    # slope >0.2%) is positive out to 10 bars. These gates make that testable.
    # All three are computed from bars up to i only.
    geo = {}
    for _s, _b in tape.items():
        _c = [x.get("c") or 0.0 for x in _b]
        if not _c:
            continue
        _k = 2.0 / 9.0
        _e = [_c[0]]
        for _p in _c[1:]:
            _e.append(_p * _k + _e[-1] * (1 - _k))
        _m, _run = [], 0.0
        for _j, _p in enumerate(_c):
            _run += _p
            if _j >= 12:
                _run -= _c[_j - 12]
            _m.append(_run / min(_j + 1, 12))
        geo[_s] = (_e, _m)
    # RSI vs its 14-SMA -- Sri, 08-Sep on HINDCOPPER: "13:58:30 is a bad exit,
    # 13:48 is good... look at the RSI and smoothing, it crossed and dipping
    # down." Two bars of confirmation because it can pop back above once.
    rsis = {}
    for s, b in tape.items():
        c = [x["c"] for x in b]
        r = HE._rsi(c)
        rsis[s] = (r, HE._sma(r, 14))
    idx = {s: {b["hhmm"]: i for i, b in enumerate(bars) if i >= d0.get(s, 0)}
           for s, bars in tape.items()}
    clock = sorted({bars[i]["hhmm"] for s, bars in tape.items()
                    for i in range(d0.get(s, 0), len(bars))
                    if start_t <= bars[i]["hhmm"] <= now_t})
    per = BOOK / max(1, SLOTS)
    # Real capital standing behind one slot. A stock Dhan allows only 1x on can
    # hold no more than this, not `per` -- see leverage.py.
    own_per_slot = CAPITAL / max(1, SLOTS)
    CROWD["bars"], CROWD["signals"], CROWD["names"] = 0, 0, {}
    live, closed = [], []
    for t in clock:
        still = []
        for pos in live:
            bars = tape[pos["sym"]]
            i = idx[pos["sym"]].get(t)
            if i is None:
                still.append(pos)
                continue
            lo, hi, c = bars[i]["l"], bars[i]["h"], bars[i]["c"]
            why = px = None
            r, rsm = rsis[pos["sym"]]
            side = pos.get("side", 1)
            held = i - pos.get("i", i)
            rsi_roll = (held >= MIN_HOLD and i >= 2
                        and r[i] < rsm[i] and r[i - 1] < rsm[i - 1] and r[i] < r[i - 1])
            if (EYE_LOGIC and EYE_EXIT and pos.get("eye_out") is not None
                    and held >= MIN_HOLD and i >= pos["eye_out"]):
                # v6's own adaptive dip exit: a dip must outlast every dip the
                # leg has already survived. This is the rule that beat RSI-chop
                # exits 7:1 on Sri's labelled trades, so it is tested FIRST and
                # the legacy stop/trail/RSI rules below never pre-empt it.
                px, why = c, "eye exit"
            elif TREND_EXIT and trendser.get(pos["sym"]) and \
                    i < len(trendser[pos["sym"]]) and \
                    trendser[pos["sym"]][i] == -side:
                px, why = c, "trend flip"
            elif t >= SQUARE_OFF:
                px, why = c, "square-off"
            elif CIRCUIT_BUF_PCT and pos["sym"] in CIRC:
                _lo_b, _hi_b = CIRC[pos["sym"]]
                _near_hi = _hi_b * (1 - CIRCUIT_BUF_PCT / 100)
                _near_lo = _lo_b * (1 + CIRCUIT_BUF_PCT / 100)
                if side == 1 and (hi or c) >= _near_hi:
                    px, why = min(c, _near_hi), "circuit guard"
                elif side == -1 and (lo or c) <= _near_lo:
                    px, why = max(c, _near_lo), "circuit guard"
            elif side == 1 and lo and lo <= pos["in"] * (1 + STOP_PCT / 100):
                px, why = pos["in"] * (1 + STOP_PCT / 100), "stop"
            elif side == -1 and hi and hi >= pos["in"] * (1 - STOP_PCT / 100):
                # a short is stopped by the price going UP the same 1%
                px, why = pos["in"] * (1 - STOP_PCT / 100), "stop"
            elif (not EYE_LOGIC) and rsi_roll:
                px, why = c, "RSI rolled under its smoothing"
            else:
                # "peak" is the best price the leg has seen: the high for a
                # long, the low for a short. The trail then measures how far
                # price has given back FROM that best, in the losing direction.
                if side == 1:
                    pos["peak"] = max(pos["peak"], hi or c)
                    armed = (c > pos["in"]) if TRAIL_ARM == "close" \
                        else (pos["peak"] > pos["in"])
                    if (pos["peak"] - c) / pos["peak"] * 100 >= TRAIL_PCT and armed:
                        px, why = c, "trail"
                else:
                    pos["peak"] = min(pos["peak"], lo or c)
                    armed = (c < pos["in"]) if TRAIL_ARM == "close" \
                        else (pos["peak"] < pos["in"])
                    if (c - pos["peak"]) / pos["peak"] * 100 >= TRAIL_PCT and armed:
                        px, why = c, "trail"
            # ---- SAFETY INVARIANT, 29-Sep -------------------------------
            # An exit may NEVER be booked at a price the bar did not trade.
            # ELECTCAST today: entered 73.63, "exited" at 3.58 in the SAME bar
            # on a circuit guard, booking -Rs 2,37,861 on Rs 1,00,000 of
            # capital. The band came back for the wrong instrument
            # (sec_lookup gave id 18116, whose bands sit near Rs 3.6), and the
            # guard exited straight at it. Clamping every exit into the bar's
            # own [low, high] makes any bad reference price harmless, not just
            # this one.
            if why and px is not None:
                _blo = lo if lo else c
                _bhi = hi if hi else c
                try:
                    if _blo and _bhi and float(_blo) <= float(_bhi):
                        px = min(max(float(px), float(_blo)), float(_bhi))
                except (TypeError, ValueError):
                    px = c
            if why:
                # A short sells first and buys back at the exit, so the two legs
                # swap: entry value is the SELL and exit value is the BUY. Dhan
                # prices the legs differently (STT is sell-side only), so the
                # charge call must see them the right way round. gross = sv - bv
                # is then correct for both sides without a second branch.
                ev, xv = pos["qty"] * pos["in"], pos["qty"] * px
                bv, sv = (ev, xv) if side == 1 else (xv, ev)
                ch = PE.charges(bv, sv)["total"]
                _sl = (pos.get("slip_in") or 0.0) + _slip_rs(
                    pos["qty"], px, bars[i].get("v"), hi, lo, c)
                closed.append({**pos, "out": px, "out_t": t, "why": why,
                               "chg": ch, "net": sv - bv - ch, "slip": round(_sl, 2)})
            else:
                pos["last"] = c
                still.append(pos)
        live = still
        if t >= SQUARE_OFF:
            continue
        if len(live) >= SLOTS and not DISPLACE:
            # COUNT WHAT WE TURN AWAY. Until 28-Sep this `continue` fired before
            # any candidate was examined, so the board's skipped.crowded_out was
            # a hardcoded 0 and could never report a missed signal. AEQUS on
            # 28-Sep signalled at 10:11 (Rs 259.56), was blocked by a LOSING
            # GESHIP, and was finally bought at 10:23 at Rs 272 for -Rs 2,570.
            # Nothing anywhere recorded the miss. Set COUNT_CROWDED = False to
            # switch this bookkeeping off; it changes no trading decision.
            if COUNT_CROWDED and EYE_LOGIC:
                _h = {p["sym"] for p in live}
                _n = 0
                for _s in tape:
                    if _s in _h or t < avail.get(_s, "99:99:99"):
                        continue
                    _i = idx[_s].get(t)
                    if _i is None:
                        continue
                    _ent = eye.get(_s, ([], [], []))[0]
                    if _i < len(_ent) and _ent[_i]:
                        _n += 1
                        CROWD["names"][_s] = CROWD["names"].get(_s, 0) + 1
                if _n:
                    CROWD["bars"] += 1
                    CROWD["signals"] += _n
            continue        # slots full and displacement off -- nothing to do
        held = {p["sym"] for p in live}
        # RE-ENTRY COOLDOWN. 23-Sep: ARCIL was entered, lost, and entered again
        # 18 minutes later for a second loss -- Rs 2,286 between them. Nothing
        # stopped the engine going straight back into a name that had just
        # failed it. 0 = off.
        if COOLDOWN_MIN:
            _cut = _hhmm_minus(t, COOLDOWN_MIN)
            held |= {c["sym"] for c in closed if c["net"] < 0 and c["out_t"] >= _cut}
        cands, cand_sc = [], {}
        for s, bars in tape.items():
            if s in held or t < avail.get(s, "99:99:99"):
                continue
            i = idx[s].get(t)
            # WARM_MIN: EMA50 needs 50 bars behind it. With the warm-up that is
            # satisfied from the first candle of the day; without it (a stock
            # with no previous session on file) this is what stops the engine
            # acting on an EMA that is still just the opening price.
            if i is None or i + 1 >= len(bars):
                continue
            if EYE_LOGIC:
                ent, exi, sc = eye.get(s, ([], [], []))
                if i >= len(ent) or not ent[i]:
                    continue
                # RANKING. 23-Sep: the selection score saturates at 3.00
                # (volq 1 + rngq 1 + keep 1) in the first minutes, when every
                # percentile is trivially 1.0 for want of bars. At 09:15 three
                # names tied at 3.00 and the two slots went to whichever the
                # dict happened to yield first. OPTIEMUS won one on a tied score
                # with the SHORTEST leg of the group (10) and lost Rs 2,633;
                # OLAELEC, leg 25, ranked sixth and was never taken -- Sri
                # traded it himself for +5.63%. Leg length is the feature that
                # actually separated today's winners from its losers (12.5 bars
                # vs 7.5), so it breaks the tie.
                if MIN_CLOUD_PCT:
                    _ct = cloudtop.get(s)
                    _top = _ct[i] if (_ct and i < len(_ct)) else None
                    _px = bars[i].get("c") or 0
                    if _top is None or not _px:
                        continue          # no cloud yet -- stand aside
                    if (_px - _top) / _px * 100 < MIN_CLOUD_PCT:
                        continue          # not enough clearance over the cloud
                _leg = (exi[i] - i) if exi[i] is not None else 0
                _key = (sc[i] + _leg / 100.0) if RANK == "score_then_leg" else (
                        _leg if RANK == "leg" else sc[i])
                if (MIN_ABOVE_PCT or MIN_SLOPE_PCT or MIN_SEP_PCT) and ent[i] == 1:
                    _g = geo.get(s)
                    if not _g or i < 4:
                        continue
                    _e, _m = _g
                    if not (_e[i] and _m[i] and _e[i - 3]):
                        continue
                    _above = (bars[i]["c"] / _e[i] - 1) * 100
                    _slope = (_e[i] / _e[i - 3] - 1) * 100
                    _sep = (_e[i] / _m[i] - 1) * 100
                    if (_above < MIN_ABOVE_PCT or _slope < MIN_SLOPE_PCT
                            or _sep < MIN_SEP_PCT):
                        continue
                cand_sc[s] = sc[i]
                cands.append((_key, s, i, exi[i], ent[i]))
                continue
            d = dirs[s]
            if not (d[i] == 1 and d[i - 1] != 1):
                continue
            v = volx(bars, i)
            if v < MIN_VOLX:                       # volume must be expanding
                continue
            if day_pct(bars, i, d0.get(s, 0)) < MIN_DAY_PCT:
                continue                           # and the stock must be moving
            cands.append((v, s, i, None, 1))
        cands.sort(key=lambda x: -x[0])
        # DISPLACEMENT. 24-Sep: Sri traded MONQ50 10:27:30 -> 10:45 for +16%.
        # The engine SAW it -- score 2.54, a 20-bar leg, every gate passed -- and
        # could not take it because both slots were busy: GNA (entered 10:24:30,
        # finished -Rs 217) and KSCL (entered 10:27:00, finished +Rs 343). A
        # sixteen-percent move queued behind a loser and a scratch. RANK=leg did
        # not fail; KSCL simply signalled one bar earlier and owned the slot.
        # The engine had no way to let go of a weak position for a far better
        # one. DISPLACE_MULT is how many times longer the newcomer's leg must be
        # than what remains of the weakest holding's. 0 = off.
        if DISPLACE and len(live) >= SLOTS and cands and DISPLACE_MODE == "causal":
            # CAUSAL EVICTION, 30-Sep. The legacy rule below ranks positions by
            # `eye_out - i_now`, "bars this leg has left" -- where the leg ENDS.
            # At this moment in the session that is unknowable; the replay only
            # answers it by reading the rest of the tape. Proof: switching
            # eviction off dropped the win rate from 73% to 39%, a coin flip.
            # A rule worth 34 points of win rate is not judgement, it is the
            # answer key.
            #
            # This version uses only what is on the screen at time t:
            #   - which holding is actually DOWN right now, and by how much
            #   - the newcomer's selection score, known at its own signal bar
            # It will never evict a position that is currently in profit, which
            # is the behaviour Sri described: drop the one that is losing, not
            # the one whose future happens to be shorter.
            worst = None
            for pos in live:
                i_now = idx[pos["sym"]].get(t)
                if i_now is None:
                    continue
                _b = tape[pos["sym"]][i_now]
                _c = _b.get("c")
                if not _c or not pos.get("in"):
                    continue
                _ret = (_c / pos["in"] - 1) * 100 * pos.get("side", 1)
                if worst is None or _ret < worst[0]:
                    worst = (_ret, pos, i_now)
            if worst is not None and worst[0] < 0:        # only evict a LOSER
                _ret, pos, i_now = worst
                v, cs, ci, ceout, cside = cands[0]
                if cand_sc.get(cs, 0) >= (pos.get("sc_in") or 0) * DISPLACE:
                    bars = tape[pos["sym"]]
                    px = bars[i_now]["c"]
                    side = pos.get("side", 1)
                    ev, xv = pos["qty"] * pos["in"], pos["qty"] * px
                    bv, sv = (ev, xv) if side == 1 else (xv, ev)
                    ch = PE.charges(bv, sv)["total"]
                    _sl = (pos.get("slip_in") or 0.0) + _slip_rs(
                        pos["qty"], px, bars[i_now].get("v"), bars[i_now].get("h"),
                        bars[i_now].get("l"), bars[i_now].get("c"))
                    closed.append({**pos, "out": px, "out_t": t,
                                   "why": f"displaced by {cs}", "chg": ch,
                                   "net": sv - bv - ch, "slip": round(_sl, 2)})
                    live = [p for p in live if p is not pos]
                    held.discard(pos["sym"])

        if DISPLACE and len(live) >= SLOTS and cands and DISPLACE_MODE == "legacy":
            worst = None
            for pos in live:
                i_now = idx[pos["sym"]].get(t)
                if i_now is None or pos.get("eye_out") is None:
                    continue
                rem = pos["eye_out"] - i_now         # bars this leg has left
                if worst is None or rem < worst[0]:
                    worst = (rem, pos, i_now)
            if worst is not None and worst[0] > 0:
                rem, pos, i_now = worst
                v, cs, ci, ceout, cside = cands[0]
                if v >= rem * DISPLACE and (ceout - ci) >= MIN_LEG_DISPLACE:
                    bars = tape[pos["sym"]]
                    px = bars[i_now]["c"]
                    side = pos.get("side", 1)
                    ev, xv = pos["qty"] * pos["in"], pos["qty"] * px
                    bv, sv = (ev, xv) if side == 1 else (xv, ev)
                    ch = PE.charges(bv, sv)["total"]
                    _sl = (pos.get("slip_in") or 0.0) + _slip_rs(
                        pos["qty"], px, bars[i_now].get("v"), bars[i_now].get("h"),
                        bars[i_now].get("l"), bars[i_now].get("c"))
                    closed.append({**pos, "out": px, "out_t": t,
                                   "why": f"displaced by {cs}", "chg": ch,
                                   "net": sv - bv - ch, "slip": round(_sl, 2)})
                    live = [p for p in live if p is not pos]
                    held.discard(pos["sym"])

        # Ranked cands beyond the free slots are turned away. This path runs
        # when DISPLACE is ON (the early `continue` above never fires then), so
        # without this the counter would silently report 0 again.
        if COUNT_CROWDED and EYE_LOGIC and cands:
            _blocked = cands[max(0, SLOTS - len(live)):]
            if _blocked:
                CROWD["bars"] += 1
                CROWD["signals"] += len(_blocked)
                for _c in _blocked:
                    CROWD["names"][_c[1]] = CROWD["names"].get(_c[1], 0) + 1

        for v, s, i, eout, eside in cands:
            if len(live) >= SLOTS:
                break
            bars = tape[s]
            entry = bars[i + 1]["o"] or bars[i + 1]["c"]
            if not entry:
                continue
            # ENTRY HEIGHT GATE. 100 = off. Sri's own diagnosis of his losses
            # is that he buys at the top of a move, after the ignition has
            # exhausted. Measured 30-Sep, the engine's median entry sits at
            # 83% of the stock's last-20-bar range -- the same mistake.
            if MAX_ENTRY_POS < 100 and eside == 1:
                _w = bars[max(0, i - 20):i + 1]
                _hi = max((x.get("h") or x.get("c") or 0) for x in _w)
                _lo = min((x.get("l") or x.get("c") or 0) for x in _w)
                if _hi > _lo and (entry - _lo) / (_hi - _lo) * 100 > MAX_ENTRY_POS:
                    continue
            # Rule A: cap the position at what this stock's true leverage allows.
            cap = LV.notional_cap(s, own_per_slot, per)
            qty = int(cap / entry)
            if qty > 0:
                _eb = bars[i + 1]
                live.append({"sym": s, "i": i + 1, "side": int(eside or 1),
                             "slip_in": _slip_rs(qty, entry, _eb.get("v"),
                                                 _eb.get("h"), _eb.get("l"), _eb.get("c")),
                             "in": entry, "in_t": bars[i + 1]["hhmm"],
                             "qty": qty, "sig_t": bars[i]["hhmm"], "peak": entry,
                             "last": entry, "str": round(v, 1),
                             "sc_in": cand_sc.get(s, 0),
                             "eye_out": eout})
    return closed, live


def _hhmm_minus(t, mins):
    """`t` ("HH:MM:SS") less `mins` minutes, same format."""
    h, m, sec = (int(x) for x in t.split(":"))
    v = max(0, h * 60 + m - int(mins))
    return "%02d:%02d:%02d" % (v // 60, v % 60, sec)


def _secs(a, b):
    try:
        ah, am, asec = (int(x) for x in a.split(":"))
        bh, bm, bsec = (int(x) for x in b.split(":"))
        return max(0, (bh * 3600 + bm * 60 + bsec) - (ah * 3600 + am * 60 + asec))
    except Exception:
        return 0


def snapshot(day, closed, live, ctrl, nsym, now_t):
    rows = []
    for pos in (live or []):
        side = pos.get("side", 1)
        ev, xv = pos["qty"] * pos["in"], pos["qty"] * pos["last"]
        bv, sv = (ev, xv) if side == 1 else (xv, ev)
        ch = PE.charges(bv, sv)
        rows.append({"sym": pos["sym"], "sid": "", "in": pos["in"],
                     "in_hms": pos["in_t"], "in_t": pos["in_t"], "qty": pos["qty"],
                     "sig_hms": pos.get("sig_t", pos["in_t"]),
                     "urgency": pos.get("str"), "leg": 1,
                     "side": side, "dir": "SHORT" if side == -1 else "LONG",
                     "out": round(pos["last"], 2), "out_hms": "—", "why": "open",
                     "gross": round(sv - bv, 2), "charges": ch, "chg": ch["total"],
                     "net": round(sv - bv - ch["total"], 2),
                     "held": _secs(pos["in_t"], now_t),
                     "live_pct": round((pos["last"] / pos["in"] - 1) * 100 * side, 2),
                     "status": "In-Progress", "value": round(ev, 2)})
    for c in closed:
        side = c.get("side", 1)
        ev, xv = c["qty"] * c["in"], c["qty"] * c["out"]
        bv, sv = (ev, xv) if side == 1 else (xv, ev)
        rows.append({"sym": c["sym"], "sid": "", "in": c["in"], "in_hms": c["in_t"],
                     "in_t": c["in_t"], "qty": c["qty"], "out": round(c["out"], 2),
                     "out_hms": c["out_t"], "why": c["why"],
                     "sig_hms": c.get("sig_t", c["in_t"]),
                     "urgency": c.get("str"), "leg": 1,
                     "side": side, "dir": "SHORT" if side == -1 else "LONG",
                     "gross": round(sv - bv, 2), "charges": {"total": c["chg"]},
                     "chg": c["chg"], "net": round(c["net"], 2),
                     "held": _secs(c["in_t"], c["out_t"]),
                     "slip": round(c.get("slip") or 0.0, 2),
                     "net_slip": round(c["net"] - (c.get("slip") or 0.0), 2),
                     "status": "Closed", "value": round(ev, 2)})
    rows.sort(key=lambda x: x["in_t"])
    done = [r for r in rows if r["status"] == "Closed"]
    wins = [r for r in done if r["net"] > 0]
    realised = round(sum(r["net"] for r in done), 2)
    unreal = round(sum(r["net"] for r in rows if r["status"] == "In-Progress"), 2)
    net = round(realised + unreal, 2)
    s = {"active": bool(ctrl.get("active")), "started": ctrl.get("started"),
         "stopped": None if ctrl.get("active") else ctrl.get("stopped"),
         "capital": CAPITAL, "leverage": LEVERAGE, "slots": SLOTS,
         "leverage_rule": "A: exactly 5.00X from Dhan = 5x, anything else = 1x",
         "leverage_cache": LV.summary(),
         "per_slot": round(BOOK / max(1, SLOTS), 2), "exposure": BOOK,
         "window": f"{ctrl.get('started') or '—'} - {now_t}",
         "trades": len(rows), "open_n": len(live or []), "done_n": len(done),
         "wins": len(wins), "losses": len(done) - len(wins),
         "win_pct": round(len(wins) * 100.0 / len(done)) if done else 0,
         "gross": round(sum(r["gross"] for r in rows), 2),
         "charges": round(sum(r["chg"] for r in rows), 2),
         "net": net, "realised": realised, "unrealised": unreal,
         "net_pct": round(net / CAPITAL * 100, 2),
         # SLIPPAGE-ADJUSTED. `net` books every fill at the bar price, which a
         # 2.5 lakh order in a thin 30-second bar would never get. `net_real`
         # is the number to judge the day by; see _slip_rs().
         "slip_cost": round(sum(r.get("slip") or 0.0 for r in rows), 2),
         "net_real": round(net - sum(r.get("slip") or 0.0 for r in rows), 2),
         "net_real_pct": round((net - sum(r.get("slip") or 0.0 for r in rows))
                               / CAPITAL * 100, 2),
         "target_pct": TARGET_PCT,
         "target_rs": round(CAPITAL * TARGET_PCT / 100, 2),
         "target_hit": net >= CAPITAL * TARGET_PCT / 100,
         "target_gap": round(net - CAPITAL * TARGET_PCT / 100, 2),
         "best": ({"sym": max(rows, key=lambda t: t["net"])["sym"],
                   "net": round(max(r["net"] for r in rows), 2)} if rows else None),
         "worst": ({"sym": min(rows, key=lambda t: t["net"])["sym"],
                    "net": round(min(r["net"] for r in rows), 2)} if rows else None),
         "ex_best": 0.0, "ex_best_pct": 0.0, "displaced": 0,
         "skipped": {"too_cheap": 0, "crowded_out": CROWD["signals"], "cooldown": 0,
                     "no_price": 0, "no_bars": 0},
         "crowded_out_bars": CROWD["bars"],
         "crowded_out_names": sorted(CROWD["names"].items(),
                                     key=lambda kv: -kv[1])[:10],
         "ticks": 0, "last_tick": now_t, "err": None, "window_closed": False,
         "entry_window": "09:16-15:05", "max_from_open": 0, "live": True,
         "rule": (f"HOTT/LOTT + Pullback ALT. Universe: Dhan volume shockers, price "
                  f"shockers and MyWatchlist ({nsym} names live now), each usable "
                  f"only from the moment it appeared. Enter when HOTT/LOTT is in "
                  f"UPTREND (not the flat zone) and price pulls back to the EMA8 in "
                  f"an 8>21>50 stack and closes back above it; strongest volume "
                  f"expansion wins. {SLOTS} positions of Rs {BOOK / max(1, SLOTS):,.0f}. "
                  f"Exit -1% stop, 1.2% off the peak, circuit guard, square-off 15:05. "
                  f"03/04/07-Sep with these filters: 1 slot Rs 8,326, 3 slots "
                  f"Rs 21,314, 5 slots Rs 12,252 -- three days only, and 07-Sep "
                  f"lost money on every setting."),
         }
    return {"ok": True, "summary": s, "trades": rows, "paper_live": True}


def idle_snapshot(ctrl=None, note=None, day=None):
    """A complete, zero-trade payload.

    The Board's JavaScript reads s.skipped.crowded_out with no guard on
    s.skipped itself, so a hand-written "nothing yet" payload that omits a key
    takes the whole tab down with a TypeError. Building it through snapshot()
    means the idle payload and the live payload can never drift apart.
    """
    day = day or datetime.now().strftime("%Y%m%d")
    ctrl = ctrl if ctrl is not None else control()
    apply_control(ctrl)
    snap = snapshot(day, [], [], ctrl, 0, datetime.now().strftime("%H:%M:%S"))
    if note:
        snap["summary"]["err"] = note
    return snap


def board_snapshot(day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    f = Path(str(SNAP).format(day))
    if not f.exists() or time.time() - f.stat().st_mtime > 180:
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def claim_single_instance(log):
    """One engine, and only one.

    A code fix landing mid-session is useless if the process holding the old
    code keeps running: it owns the snapshot file and would overwrite whatever
    the new one writes. So a starting instance retires the previous one and
    takes the PID file. The supervisor's keepalive then tracks whichever
    survives, which is always the newest code.
    """
    me = os.getpid()
    olds = []
    try:
        olds.append(int(PIDF.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    # The first instance of the day predates the PID file, so fall back to
    # asking Windows which python processes are running THIS script. Without
    # this, a code fix cannot take over mid-session and the stale engine keeps
    # writing the snapshot the Board reads.
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "Where-Object { $_.CommandLine -like '*paper_live.py*' } | "
                 "Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True, timeout=25).stdout
            olds += [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception:
            pass
    for old in sorted({p for p in olds if p and p != me}):
        try:
            os.kill(old, signal.SIGTERM)
            log(f"retired the previous engine (pid {old})")
        except (OSError, PermissionError, SystemError):
            pass                        # already gone, or not ours to signal
    if olds:
        time.sleep(2.0)
    try:
        PIDF.parent.mkdir(parents=True, exist_ok=True)
        PIDF.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def main():
    day = datetime.now().strftime("%Y%m%d")
    logf = Path(str(LOG).format(day))

    def log(m):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
        print(line, flush=True)
        with logf.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    claim_single_instance(log)
    # 23-Sep: this used to stamp paper_live.py's OWN mtime. The board compares
    # the stamp against the newest of the logic modules too, so the moment
    # eye_strategy.py was edited the stamp was permanently behind it, the engine
    # read as stale forever, and the reviver relaunched it every 45 seconds --
    # each new instance retiring the last, none of them ever trading. Stamping
    # the WALL CLOCK at boot is what the comparison actually wants: newer than
    # every source that existed when this process started, older than any edit
    # made after it.
    src_mtime = os.path.getmtime(__file__)   # still used by the self-restart check
    try:
        BOOTF.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    warm = LS.load_warm(LS._prev_session_dir(day))
    # FAST QUOTE FEED. One bulk /marketfeed/quote per second for the whole
    # watchlist -- Dhan allows 1000 instruments in a single request at 1/sec,
    # which the engine was not using. Builds 30s bars ~1s after each half
    # minute instead of the 20-68s the per-symbol sweep delivered. The sweep
    # stays as the authoritative backfill; this only fills the newest bars.
    try:
        import quote_feed as QF
        QF.start(day, lambda: tradeable(day), log)
    except Exception as _e:
        log(f"quote_feed unavailable -- {type(_e).__name__}: {_e}")
    # OUR OWN SURGE SCANNER. Off unless env.txt says SURGE_SCAN=YES. The board
    # only flags a stock AFTER it moves -- TVSELECT signalled at 10:13/10:15/
    # 10:17 on 30-Sep and the board flagged it at 10:20:32, so the whole
    # 389 -> 407 run was invisible. This watches the entire NSE equity universe
    # itself. First-seen is still recorded once and never revised, so widening
    # the universe cannot reintroduce look-ahead.
    try:
        import surge as SG
        if SG.start(day, log):
            log("surge scanner ON -- the engine no longer waits for the board")
    except Exception as _e:
        log(f"surge unavailable -- {type(_e).__name__}: {_e}")
    log(f"paper_live {day}: warm-up {len(warm)} symbols. Waiting for START.")

    # Per-stock intraday leverage for the warm-up universe, once per day.
    # Anything not resolved here sizes at 1x, which is safe but small, so the
    # count of 1x names is worth reading in the log each morning.
    try:
        _px = {}
        for _s, _b in (warm or {}).items():
            if _b:
                _c = _b[-1].get("c") or _b[-1].get("o")
                if _c:
                    _px[_s] = float(_c)
        if _px:
            LV.prefetch_symbols(day, _px, log=log)
    except Exception as _e:
        log(f"leverage prefetch failed: {type(_e).__name__} {_e} - all stocks size at 1x")
    last_fetch, seen = 0.0, set()
    _last_feed_ok = 1
    _held_syms = []
    _sweep_n = 0
    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:                     # heartbeat -- the Board revives a stale engine
            PIDF.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        try:                     # a code change retires this engine cleanly
            if os.path.getmtime(__file__) != src_mtime:
                log("paper_live.py changed on disk -- exiting so the new code "
                    "can take over")
                break
        except OSError:
            pass
        ctrl = control()
        apply_control(ctrl)
        if now >= "15:31:00":
            # VERSIONING, 24-Sep, Sri: "Take todays logic version as backup if
            # you are changing that... Lets maintain versioning from now on
            # everyday." Freeze the exact logic that traded today together with
            # the P&L it produced, so tomorrow's change is measured against a
            # fixed baseline instead of a moving one. Twice already a decision
            # was made against a baseline that had since moved (shorts, slots).
            try:
                import version_snap as _VS
                _VS.snap(day)
            except Exception as _e:
                log(f"version snapshot failed: {type(_e).__name__} {_e}")
            log("session over"); break
        if not ctrl.get("active"):
            time.sleep(REFRESH); continue
        # Adaptive: sweep every 15s until 09:25 so the opening minute is
        # actually available, then settle. See live_shadow.fetch_interval.
        if time.time() - last_fetch >= LS.fetch_interval(now, _last_feed_ok):
            last_fetch = time.time()
            try:
                # Sri, 09-Sep 09:18: "9:20 is too late. By that time ignitions
                # would have exhausted... If you need candles, who is stopping
                # you to take from dhan."
                # He is right. The sweep was pulling pick_universe() -- up to
                # MAX_FETCH=500 names, 150s a cycle with 231 failures, so the
                # watchlist names had no bars until well after the ignition.
                # The tradeable universe is the no-dip list, which was FOURTEEN
                # names at 09:16. Fetch those FIRST and the rest afterwards.
                # 09:23 -- fetching the watchlist AND then the other ~500 names
                # blocked the cycle for 150s+ and the heartbeat went stale, so
                # the board stopped updating entirely. Only no-dip names are
                # tradeable, so only they are fetched. Nothing else is needed.
                # HOT / REST SPLIT, 30-Sep. Sweeping all ~150 watchlist names
                # takes ~43s even in parallel, because Dhan rate-limits us --
                # so every name carried a ~68s median lag, including the two
                # we were actually holding. But only a handful of names can
                # affect a decision this cycle: what we hold, and the freshest
                # movers. Those get swept every cycle; the long tail, which
                # exists only so a name has history WHEN it later qualifies,
                # gets swept every REST_EVERY sweeps.
                want = sorted(tradeable(day))
                hot = set(_held_syms)
                try:
                    _sh = FN.shockers(day)
                    _fresh = sorted(((v[0], k) for k, v in _sh.items() if k in want),
                                    reverse=True)[:HOT_FRESH]
                    hot |= {k for _, k in _fresh}
                except Exception:
                    pass
                # OPENING SPRINT, 1-Oct. Before 09:25 there is no "long tail":
                # every name the board already knows at 09:15 can move in the
                # first three minutes, and a name without bars cannot be
                # signalled at all. Measured this morning:
                #     KARAMTARA  board flagged it 09:15:00, our tape had no
                #                bars until 09:18:40. Its best signal of the
                #                day -- score 3.00 at 395.90 -- was blocked,
                #                and it finally entered at 417.10.
                # Rs 21 lost to our own fetch order, not to Dhan. So in the
                # opening window we sweep the whole watchlist every pass and
                # let the hot/rest split take over once the dust settles.
                if now < "09:25:00":
                    hot, rest = list(want), []
                else:
                    hot = [s for s in want if s in hot]
                    rest = [s for s in want if s not in set(hot)]
                if hot:
                    _last_feed_ok = LS.fetch_live(day, hot, log) or 0
                elif want:
                    _last_feed_ok = LS.fetch_live(day, want, log) or 0
                    rest = []
                else:
                    _last_feed_ok = 0
                _sweep_n += 1
                if rest and _sweep_n % REST_EVERY == 0:
                    LS.fetch_live(day, rest, log)
                # Circuit bands for any tradeable name we do not have yet.
                # Set per scrip per day, so each name is fetched once.
                if CIRCUIT_BUF_PCT:
                    _new = [w for w in want if w not in CIRC]
                    if _new:
                        try:
                            import Opus_quotes_v3 as QU
                            _sids, _back = [], {}
                            for _w in _new:
                                _sid, _ = LV.sec_lookup(_w)
                                if _sid:
                                    _sids.append(str(_sid))
                                    _back[str(_sid)] = _w
                            if _sids:
                                _kept = _bad = 0
                                for _sid, _band in QU.circuit_bands(_sids, log).items():
                                    _nm = _back[_sid]
                                    # A band must BRACKET the stock's own last
                                    # price. ELECTCAST, 29-Sep: price 73.63,
                                    # band came back near Rs 3.6 -- the wrong
                                    # instrument for that security id -- and
                                    # the guard exited at it for -Rs 2,37,861.
                                    # An unbracketing band is not a band.
                                    try:
                                        _bars = tape.get(_nm) or []
                                        _px = (_bars[-1].get("c") if _bars else None)
                                        _lo_b, _hi_b = float(_band[0]), float(_band[1])
                                        # Inclusive, with 5% tolerance. A stock
                                        # sitting EXACTLY at its upper circuit
                                        # (price == hi) is the case this guard
                                        # exists for -- a strict < rejected
                                        # HAPPYFORGE 2096.5 vs 1896.9-2096.5 and
                                        # 9 others, switching the guard off for
                                        # precisely the names at risk. What is
                                        # actually wrong is a band from another
                                        # scrip: ELECTCAST price 73.63 against a
                                        # band of 3.5-3.6, i.e. 20x out.
                                        if _px and not (_lo_b * 0.95 <= float(_px)
                                                        <= _hi_b * 1.05):
                                            _bad += 1
                                            log(f"circuit band REJECTED for {_nm}: "
                                                f"price {_px} outside band "
                                                f"{_lo_b}-{_hi_b} (wrong scrip?)")
                                            continue
                                    except (TypeError, ValueError, IndexError):
                                        _bad += 1
                                        continue
                                    CIRC[_nm] = _band
                                    _kept += 1
                                log(f"circuit bands: {len(CIRC)} of {len(want)} names"
                                    + (f" ({_bad} rejected as implausible)" if _bad else ""))
                        except Exception as _e:
                            log(f"circuit bands failed: {type(_e).__name__} {_e}")
            except Exception as e:
                log(f"feed error: {type(e).__name__}: {e}")
        try:
            pairs, _today = LS.build(day, warm)
            tape, d0 = {}, {}
            for s, (bars, n) in pairs.items():
                bb = [x for x in bars[:n] if x.get("c")]
                tb = [x for x in bars[n:] if x.get("c") and OPEN_T <= x["hhmm"] <= now]
                if len(tb) < 3:
                    continue
                tape[s] = bb + tb
                d0[s] = len(bb)
            avail, _, _ = FN.build(day, tape)
            # THE DAY'S WATCHLIST = every stock that has worn the no-dip badge.
            # Nothing else is tradeable, however good it looks.
            pins = tradeable(day)
            if pins:
                avail = {s: t for s, t in avail.items() if s in pins}
            else:
                avail = {}
            fun = {s: tape[s] for s in avail if s in tape}
            fd0 = {s: d0[s] for s in fun}
            start_t = ctrl.get("started") or "09:16:00"
            closed, live = run_book(fun, fd0, avail, start_t, now)
            # ---- LIVE EXECUTION -------------------------------------------
            # run_book() above is a stateless replay: it says what SHOULD be
            # open right now, rebuilt from the tape every cycle. live_exec
            # compares that against a ledger of what IS open at Dhan and sends
            # only the difference -- so orders are never duplicated, a restart
            # resumes instead of re-entering, and every filled entry gets a
            # protective stop that lives at the broker rather than in this
            # process. Inert unless BROKER_MODE=LIVE and LIVE_ARMED=YES, so
            # the daily paper run is completely unaffected.
            # Wrapped: a broker problem must never stop the paper engine.
            _lx = None
            try:
                import live_exec
                _lx = live_exec.reconcile(day, live, closed, log)
            except Exception as _e:
                log(f"live_exec error: {type(_e).__name__}: {_e}")
            _held_syms = [p["sym"] for p in live]
            # The board shows the PERMANENT book, not this cycle's opinion.
            closed = journal_merge(day, closed, start_t)
            snap = snapshot(day, closed, live, ctrl, len(fun), now)
            if _lx and (_lx.get("actions") or _lx.get("open")):
                snap["live_exec"] = _lx
            Path(str(SNAP).format(day)).write_text(
                json.dumps(snap, default=str), encoding="utf-8")
            for c in closed:
                k = (c["sym"], c["in_t"], c["out_t"])
                if k in seen:
                    continue
                seen.add(k)
                log(f"  {c['sym']:12s} {c['in_t']}->{c['out_t']} {c['in']:.2f}->{c['out']:.2f} "
                    f"{(c['out']/c['in']-1)*100:+.2f}% {c['why']:11s} Rs {c['net']:,.0f}")
            log(f"no-dip watchlist {len(pins)} names | "
                f"{len(fun)} in funnel (of {len(tape)} with bars, "
                f"{sum(1 for s in fun if fd0[s] >= WARM_MIN)} warm) | "
                f"{len(closed)} closed | "
                f"{('holding ' + ','.join(p['sym'] for p in live)) if live else 'flat'} | "
                f"NET Rs {snap['summary']['net']:,.0f}")
        except Exception as e:
            log(f"cycle error: {type(e).__name__}: {e}")
        time.sleep(REFRESH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
