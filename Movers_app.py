"""
Movers_app.py -- V4 "Movers Board" (port 5005). A DISPLAY-ONLY board.

Design brief (user spec, after Opus V2 was declared a failure):
  * No auto-trading, no scoring opinions. Show Dhan-grade mini charts and let
    the HUMAN decide the trade.
  * Three SEPARATE panels, one per source list (NOT merged):
        Intraday Movers | By Volume | Price Movers
  * Every card = exactly what Dhan shows on its 1-min chart:
        1-min candles, Hull Suite (colored by slope), Parabolic SAR dots,
        EMA-9, MA-12 (SMA), MACD sub-panel, volume bars.
  * Sorting:
        Intraday Movers & Price Movers -> MA/EMA buy-side cross first, then volume desc
        By Volume                      -> MA/EMA buy-side cross first (list is already volume-ordered)

Sources: Opus2_movers_source.cash_names() -- the 3 scanx lists with automatic
official-API sweep fallback, so the feed never halts on web-token expiry.

Runs on :5005 -- does not touch V3 (:5001), Fable (:5002), Opus3.5 (:5003) or
Opus V2 (:5004).
"""
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, request

import Opus_engine as engine
import Opus_candle_v3 as candle_v3
import Opus2_movers_source as movers_source
import Opus_quotes_v3 as quotes_v3
import Opus_preopen
try:
    import Movers_dhannews as dhannews      # Dhan's own overnight/session news
except Exception as _e:                     # pragma: no cover
    dhannews = None
    print("Movers_dhannews unavailable:", _e)
try:
    import Movers_premarket as premkt       # pre-09:00 news severity + validation
except Exception as _e:                     # pragma: no cover
    premkt = None
    print("Movers_premarket unavailable:", _e)
try:
    import Movers_master as master           # keeps security_id_list.csv current
except Exception as _e:                     # pragma: no cover
    master = None
    print("Movers_master unavailable:", _e)
try:
    import superstocks_lab                    # tuning copy -- trading engines read THIS
except Exception as _e:                     # pragma: no cover
    superstocks_lab = None
    print("superstocks_lab unavailable:", _e)
try:
    import live_paper                        # Live Paper Trading tab (forward test)
except Exception as _e:                     # pragma: no cover
    live_paper = None
    print("live_paper unavailable:", _e)
try:
    import paper_engine                      # Paper Trading tab: backtest on today's tape
except Exception as _e:                     # pragma: no cover
    paper_engine = None
    print("paper_engine unavailable:", _e)
try:
    import Movers_sectors as sectors        # NSE stock -> sector map + live breadth
except Exception as _e:                     # pragma: no cover
    sectors = None
    print("Movers_sectors unavailable:", _e)
try:
    import Movers_sectornews as sectornews  # sector headlines from the open web
except Exception as _e:                     # pragma: no cover
    sectornews = None
    print("Movers_sectornews unavailable:", _e)
try:
    import Movers_alarm as alarm            # 1-MIN ALARM: full-universe ignition detector
except Exception as _e:                     # pragma: no cover
    alarm = None
    print("Movers_alarm unavailable:", _e)
try:
    import Movers_scanner1 as scanner1      # Scanner1 = RLB 7-condition screener
except Exception as _e:                     # pragma: no cover
    scanner1 = None
    print("Movers_scanner1 unavailable:", _e)
try:
    import Movers_watchlist as watchlist    # Morning Watchlist
except Exception as _e:                     # pragma: no cover
    watchlist = None
    print("Movers_watchlist unavailable:", _e)
import indicators as I
from Movers_ticks import TICKS, MIN_BARS_30S
# The V3.5 (:5003) card renderer is reproduced exactly, so we build the card
# payload with the SAME scorer that feeds it -- candles, EMA-9, SMA-12, MACD,
# UAlgo cloud, consolidation zones, Buy/Sell markers and every badge field.
from Opus_badge_cash import CashScorer

try:
    import superstocks                   # SUPER STOCKS: whole-universe finder
except Exception as _e:                  # pragma: no cover
    superstocks = None
    print("superstocks unavailable:", _e)

try:
    import scanx_blast                   # ScanX Momentum Blast (Dhan's screener)
except Exception as _e:                  # pragma: no cover
    scanx_blast = None
    print("scanx_blast unavailable:", _e)

try:
    import super_monitor                 # watches the tab and says if it is broken
except Exception as _e:                  # pragma: no cover
    super_monitor = None
    print("super_monitor unavailable:", _e)

_scorer = CashScorer()
HERE = Path(__file__).resolve().parent

# ---- BUILD MARKER ---------------------------------------------------------
# Twice now, code changes appeared not to work when in fact the process had
# never restarted: Movers_START.bat kills whatever holds :5005, and if that
# kill fails the new Python cannot bind, exits, and the OLD process keeps
# running and keeps logging. Nothing on screen said which build was live, so
# the symptom looked like a broken rule instead of a stale process.
# This stamps the source mtime into the log and the UI header.
#
# AND IT LIED ON 28-AUG. It stamped only THIS file's mtime, so after a restart
# that correctly picked up brand-new superstocks.py it still printed
# "BUILD 27-Aug 10:51 -- if this is old, the restart did not take". The restart
# HAD taken; the marker was watching the wrong file. A staleness check that can
# report stale when the code is fresh is worse than none, because it sends you
# hunting for a restart problem that does not exist.
#
# It now reports the NEWEST of every module the board actually runs.
try:
    _srcs = [Path(__file__)] + sorted(HERE.glob("Movers_*.py")) + [
        HERE / "superstocks.py", HERE / "super_monitor.py",
        HERE / "Movers_Board.html"]
    _newest = max((p.stat().st_mtime, p.name) for p in _srcs if p.exists())
    BUILD_ID = (datetime.fromtimestamp(_newest[0]).strftime("%d-%b %H:%M")
                + f" ({_newest[1]})")
except Exception as _e:
    BUILD_ID = f"unknown ({type(_e).__name__})"
LOG = HERE / "logs" / "movers_app.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
PORT = 5005
CYCLE_SEC = 5          # rebuild the board every 5s (cards re-sort live)
PER_LIST = 0           # 0 = NO CAP: show every eligible stock in each panel
# ---- HOW OFTEN A CARD'S CANDLES ARE REFRESHED (31-Aug-2026) -------------
# Requirement: the whole board refreshed / reshuffled every 15 seconds.
#
# Two different clocks, and only one of them was ever the problem:
#   ORDER  -- cycle() re-sorts every CYCLE_SEC (5s) and the browser re-polls
#             /state every 5s. Reshuffling was already inside 15s.
#   BARS   -- each card's candles were re-fetched at most every 25s, and only
#             12 symbols were refreshed per cycle. With ~45 eligible names that
#             is 45/12 = 4 cycles = 20s before a card came round again, on top
#             of the 25s cache. A 30-second bar can close and be replaced
#             before the card ever shows it.
#
# 20 per 5s cycle = 4 requests/sec sustained, which refreshes 60 symbols inside
# 15s. HONEST LIMIT: on a busy morning the eligible list can exceed that, and
# the feed's rate limiter (Movers_chartfeed.REQ_PER_SEC) is then the binding
# constraint -- cards refresh in universe/5 seconds, not 15. Raising it further
# is not free: Dhan has warned this account about request volume before.
REFRESH_PER_CYCLE = 24 # symbols queued for re-candling each cycle. Since
                       # 01-Sep this is a QUEUE SIZE, not a wall-clock cost:
                       # candle_loop drains it in its own thread, so raising it
                       # cannot slow the board down -- only the feed's own rate
                       # limiter decides how fast it actually drains.
CANDLE_WORKERS = 4     # parallel candle fetches (engine.post is globally rate-limited)
WINDOW = 40            # bars shown per mini chart
# ---- CARD TIMEFRAME (31-Aug-2026) ---------------------------------------
# He trades off 15-second and 30-second charts, so the card should be one of
# those and not a 1-minute chart pretending. Real sub-minute bars now come from
# Dhan's own /getDataS feed, WITH previous-session warm-up, so a card is correct
# at 09:15:30 instead of ~18 minutes later.
#   "30S" -> 30-second cards      "15S" -> 15-second cards
#   "1m"  -> the old behaviour, if this ever needs turning off in a hurry
# Change this one line and restart. Nothing else needs touching.
CARD_TF = "30S"
_TF_LABEL = {"30S": "30s", "15S": "15s", "5S": "5s"}
_TF_SEC = {"30S": 30, "15S": 15, "5S": 5}

# ---- HOW MUCH CHART A CARD SHOWS ----------------------------------------
# The scorer draws the last Opus_badge_cash.WINDOW bars, and that was 18 --
# written when every card was 1-minute, so it meant 18 minutes of tape. On 30s
# bars the same 18 bars is NINE minutes, and on 15s four and a half: switching
# timeframe would have quietly halved the chart without anyone choosing that.
# Scale it so a card keeps covering the same ~18 minutes it always did,
# whatever bar size is in use.
import Opus_badge_cash as _badge
# Minutes of data SENT per card. The card only SHOWS the last 10 (VIS_BARS in
# Movers_Board.html); the rest is warm-up for the OTT / HOTT / LOTT lines the
# browser computes from this array. Sending only what is shown would draw those
# lines from a cold start and they would be wrong for the first third of the pane.
_CHART_MINUTES = 20
_badge.WINDOW = max(18, int(_CHART_MINUTES * 60 / _TF_SEC.get(CARD_TF, 60)))
CROSS_FRESH_BARS = 15  # a buy-cross within this many bars counts as "fresh"

# ---- UNIVERSE FILTERS (user spec) ----------------------------------------
# 1. drop anything priced under Rs 30
# 2. drop anything whose FIRST 2 MINUTES of the session traded < 200,000 shares
# 3. after those first 2 minutes, a stock is only ELIGIBLE once its session
#    volume crosses 300,000 shares
MIN_PRICE        = 30.0
OPEN_HHMM        = "09:15"
# ---- LIQUIDITY IS MEASURED IN RUPEES, NOT SHARES --------------------------
# The old share-count rule (200k / 300k shares) was price-blind: 200,000 shares is
# Rs 66 lakh in a Rs 33 stock but Rs 98 CRORE in HAL at Rs 4,900. It silently
# excluded every high-priced quality name (HAL traded Rs 936 Cr and was never once
# shown) while waving through cheap small-caps. Share counts are gone; a stock now
# qualifies purely on traded VALUE, which means the same thing at any price.
MIN_FIRST2_VAL   = 10_000_000    # Rs 1 Cr traded in the first 2 minutes
MIN_ENTRY_VAL    = 15_000_000    # Rs 1.5 Cr traded in the session so far
FIRST2_WINDOW_MIN = 5.0          # the first-2-minute rule only bites this early;
                                 # after that the session figure is the real test
MIN_DAY_TOVER    = 50_000_000    # Rs 5 Cr day turnover (cheap pre-filter)
# A stock sitting at (or within a hair of) its upper circuit is frozen -- there is
# no seller, so it cannot be traded. Hide it from the board entirely.
UC_TOUCH         = 0.999   # price >= UC * 0.999  ==>  treated as "at UC"

# ---- consolidation on the 30s timeframe ----------------------------------
# The scorer's zone detector is BAR-count based (lookback 10, min-length 5). On
# 30s bars those bars are half as long, so a zone would mean 5 minutes instead of
# 10. We re-run the detector with doubled params for 30s cards so a grey box
# always represents the same ~10 minutes of tape.
CONS_LB_30S      = 20
CONS_ML_30S      = 10


def _mins_since_open(now=None):
    """Minutes since 09:15 IST. Negative before the open, so the volume rules can
    stay switched off until the session has actually produced volume."""
    now = now or datetime.now(engine.IST)
    op = now.replace(hour=9, minute=15, second=0, microsecond=0)
    return (now - op).total_seconds() / 60.0

# ---- "no-dip" PIN --------------------------------------------------------
# A stock climbing continuously without dipping is pinned to the TOP of its
# column and stays there until it actually dips. A "dip" = closing below EMA-9,
# or giving back more than DIP_PCT from its running peak. Recomputed from the
# candles every cycle, so the pin releases itself the moment the dip happens.
DIP_PCT          = 0.006   # 0.6% giveback from the running peak counts as a dip
PIN_MIN_BARS     = 8       # need this many consecutive no-dip bars to pin
# A stock that merely fails to dip is NOT a runner. LICI held 11 bars without a
# dip while gaining 0.04% -- flat, but it was pinned and coloured like a trend.
# A pin now also requires a real climb and a stock that is actually up today.
PIN_MIN_GAIN     = 0.004   # >= 0.4% gained across the no-dip streak
PIN_MIN_DAY_PCT  = 0.0     # and green on the day

# ---- GAP FIXES, 24-Aug-2026 ---------------------------------------------
# Measured against the real move on 30 badged stocks that morning: the badge
# appeared a MEDIAN OF 7.2 MINUTES LATE, and in 10 cases stayed on for tens of
# minutes after the move had finished. Discovery was not the problem -- the
# stocks were on the board as their moves began (median -0.2 min).
#
# Three causes, three settings.
#
# 1. THE STREAK RESET ON EVERY SMALL DIP.
#    Any 0.6% giveback restarted the count from zero, so eight fresh bars were
#    needed all over again. RAMBHAJO reset NINE times before it could pin, by
#    which point 28 minutes of a 13.6% run had gone. The same rule threw the
#    badge away mid-run six times, which is the "cannot retain the badge"
#    complaint. Real climbs breathe; the rule did not allow breathing.
DIP_GRACE        = 1       # dips tolerated inside a streak before it resets
DIP_GRACE_MAX    = 0.010   # ...but a giveback over 1.0% always resets, grace or not
#
# 2. IT WAS WAITING OUT THE BAR COUNT.
#    Where no dips occurred, the badge was simply serving PIN_MIN_BARS. On
#    1-minute bars that is an 8-minute floor before a badge is even possible,
#    and EMA9>MA12 needs about 12 bars of history on top. A provisional badge
#    now comes from a fast 90-second ignition and the bar rule CONFIRMS it,
#    instead of being the only way in.
IGNITE_PCT       = 0.006   # +0.6% within...
IGNITE_WINDOW    = 90      # ...this many seconds = provisional badge
IGNITE_MIN_TOVER = 5_00_00_000   # Rs 5 Cr -- below this the spread eats a scalp
#
# 3. NOTHING EVER TOOK A DEAD BADGE OFF.
#    Un-pinning required a 0.6% dip or a close under EMA9. A stock that merely
#    STOPS RISING triggers neither, so FCL kept its badge 48 minutes after its
#    move ended and HSCL 45. A badge that outlives its move is worse than no
#    badge: it is a false invitation.
STALE_NO_HIGH_SEC = 300    # no new high for 5 minutes -> drop the badge

# ---- LIVE OVERRIDES from the 15-minute gap auditor -----------------------
# gap_auditor.py measures badge-vs-reality every 15 minutes and may nudge the
# settings above towards zero gap. It writes tuning.json; this reads it at
# startup. Deliberately read ONCE, not per cycle: a value that changes under a
# running calculation makes the morning's measurements incomparable, and the
# whole point of the exercise is comparable measurements.
try:
    _TUNE = json.loads((HERE / "tuning.json").read_text(encoding="utf-8"))
except Exception:
    _TUNE = {}
if _TUNE:
    DIP_GRACE = int(_TUNE.get("DIP_GRACE", DIP_GRACE))
    DIP_PCT = float(_TUNE.get("DIP_PCT", DIP_PCT))
    PIN_MIN_BARS = int(_TUNE.get("PIN_MIN_BARS", PIN_MIN_BARS))
    IGNITE_PCT = float(_TUNE.get("IGNITE_PCT", IGNITE_PCT))
    IGNITE_WINDOW = int(_TUNE.get("IGNITE_WINDOW", IGNITE_WINDOW))
    STALE_NO_HIGH_SEC = int(_TUNE.get("STALE_NO_HIGH_SEC", STALE_NO_HIGH_SEC))
    print(f"tuning.json applied: {_TUNE}")
# Likewise a "cross" needs real separation: EMA-9 sitting 0.003% above SMA-12 is
# noise, not a signal (7 cards were flagged on <0.03% separation).
CROSS_MIN_SEP    = 0.0005  # EMA-9 must be >= 0.05% above SMA-12
HTML_FILE = "Movers_Board.html"

app = Flask(__name__)
STATE = {"im": [], "bv": [], "pm": [], "mv": [], "ts": None, "src": None}
_cache = {}            # sid -> {"ts": epoch, "card": {...}}  (per-symbol candle cache)
# Position size the SUPER STOCKS tab screens for. Deliberately a constant: this
# is his trading board and nothing typed into a back-test may move it.
AUTOSTART_CAPITAL = 1_00_000.0
AUTOSTART_LEVERAGE = 5.0
AUTOSTART_TARGET = 30.0

AUTOSTART_CAPITAL = 1_00_000.0
AUTOSTART_LEVERAGE = 5.0
AUTOSTART_TARGET = 30.0

SUPER_POSITION_RS = 1_66_667.0        # Rs 1,00,000 at 5x across 3 positions

# ---- ENRICHMENT BUDGET ---------------------------------------------------
# 02-Sep 11:10: Dhan started returning 502s and the Super scan stopped
# producing for five minutes. It was not dead and it did not raise -- it was
# CRAWLING. Every outbound call has a 15-25s timeout, and the scan enriches
# each qualifier into a full card, so a slow upstream multiplies: eight names
# behind a sick API is minutes, not seconds, and the loop that feeds every
# trade simply stops arriving.
#
# The board has no business being as slow as the slowest thing it talks to.
# Enrichment now runs against a wall-clock budget: when it is spent, we publish
# the cards we already built and go round again. A partial board that is
# CURRENT beats a complete board that is four minutes old -- for a scalper the
# stale one is not merely less useful, it is wrong.
SUPER_ENRICH_BUDGET = 12.0     # seconds per pass, against a 10s loop

SUBMIN_TTL = 14        # sub-minute cards: re-candle at most once every 14s, so a
                       # 30-second bar cannot close unseen inside the 15s target.
CACHE_TTL = 40         # re-candle a symbol at most ~once every 40s. (Was 75s, which
                       # stacked on top of the 1-min bar to make charts feel ~2 min
                       # behind. 40s keeps ~45 symbols near 1.1 req/s -- well under
                       # Dhan's 5/s data limit.)


def log(m):
    line = f"[{datetime.now(engine.IST).strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _w(arr, s, n, nd=2):
    out = []
    for i in range(s, n):
        v = arr[i] if i < len(arr) else None
        out.append(None if v is None else round(v, nd))
    return out


# How far back the BUY JUST TRIGGERED column will still count a cross.
# 24 bars = 12 minutes on 30s cards, 24 minutes on 1-minute cards. Beyond this a
# cross is history, not a trigger.
BUY_CROSS_MAX_BARS = 24


def _scan_cross(e, s, warmup=2):
    """Index distance from the end to the last upward EMA/MA cross, or None.

    TWO GUARDS, both learned from GIPCL reporting a "buy 90s ago" while its
    EMA-9 sat below its MA-12 on every visible bar:

    1. STILL IN FORCE. A cross that has since reversed is not a trigger, it is
       history. If EMA is not above MA on the LAST bar, there is no live buy --
       whatever happened three bars ago. This was simply missing before.

    2. SKIP THE WARM-UP BOUNDARY. indicators.ema_series returns None until bar
       n-1 (8 for EMA-9) and sma_series until bar n-1 (11 for MA-12). The first
       bars where both exist have an EMA that has only just been seeded against
       an MA computed over a full 12 bars -- they are on different footings and
       cross each other for numerical reasons, not price reasons. Ignore crosses
       landing in that first couple of comparable bars.
    """
    if not e or not s:
        return None
    n = min(len(e), len(s))
    if n < 2:
        return None
    # ---- guard 1: the cross must still be in force RIGHT NOW
    if e[n - 1] is None or s[n - 1] is None or e[n - 1] <= s[n - 1]:
        return None
    first_ok = None
    for i in range(n):
        if e[i] is not None and s[i] is not None:
            first_ok = i
            break
    if first_ok is None:
        return None
    floor = first_ok + warmup                  # ---- guard 2
    for j in range(n - 1, max(floor, 0), -1):
        a0, b0, a1, b1 = e[j - 1], s[j - 1], e[j], s[j]
        if a0 is None or b0 is None or a1 is None or b1 is None:
            continue
        if a0 <= b0 and a1 > b1:              # upward cross on bar j
            return n - 1 - j
    return None


def _cross30(sid):
    """(bars_ago, seconds_ago) of the last EMA-9 x MA-12 buy cross ON THE
    30-SECOND SERIES, or (None, None).

    Forced to 30s regardless of what timeframe the CARD happens to be drawn on.
    A card falls back to 1-minute candles until ~35 live 30s bars exist, and a
    cross dated on 1-minute bars can only ever be a multiple of 60 seconds --
    which makes "just triggered" twice as coarse as it needs to be. The tick
    builder already keeps 30s bars for every tracked symbol, so the indicator is
    computed from those directly.
    """
    if TICKS is None:
        return None, None
    s30 = TICKS.series(str(sid), n=200)
    if not s30:
        return None, None
    c = s30.get("c") or []
    # Need real distance past the MA-12 warm-up, not just enough to compute it.
    # At 14 bars only two comparable points exist and both sit in the region
    # where EMA and MA are still settling against each other.
    if len(c) < 20:
        return None, None
    try:
        e9 = I.ema_series(c, 9)
        m12 = I.sma_series(c, 12)
    except Exception:
        return None, None
    ago = _scan_cross(e9, m12)
    return (ago, ago * 30) if ago is not None else (None, None)


def _cross_bars_ago(ch):
    """Fallback: bars since the last upward cross, from the CARD's own series.

    Used only while the 30-second history is still too short. The scorer's own
    crossAge cannot serve here -- it stops looking after FRESH_WIN (3) bars and
    so can only ever answer 0, 1 or 2, which is what left a single stock in the
    column.
    """
    return _scan_cross(ch.get("ema9"), ch.get("sma12"))


def build_card(name_row):
    """Fetch candles + compute the exact Dhan indicator set for one symbol."""
    sid = name_row["sid"]
    c = _cache.get(sid)
    # A card on the 30s timeframe is rebuilt far more often (its bars change every
    # 30s); a 1-min card obeys the slower CACHE_TTL to protect the candle API.
    ttl = SUBMIN_TTL if (c and (c["card"].get("tf") or "1m") != "1m") else CACHE_TTL
    if c and time.time() - c["ts"] < ttl:
        card = dict(c["card"])
        card["lists"] = name_row.get("lists") or []
        card["tvol"] = name_row.get("tvol")
        card["early"] = bool(name_row.get("early"))
        card["early_from_open"] = name_row.get("from_open")
        card["day_pct"] = name_row.get("day_pct")
        if not card.get("ucl"):
            card["ucl"] = _uc_from_alarm(sid)
        return card
    bundle, err = candle_v3.fetch(sid)
    if err or not bundle:
        return None
    # Opus_candle_v3.fetch nests the OHLCV arrays under "candles" (with
    # prev_close / session / today_bars alongside) -- read them from there.
    cd = bundle.get("candles") or bundle
    o, h, l, cl = cd["open"], cd["high"], cd["low"], cd["close"]
    v, ts = cd["volume"], cd["timestamp"]
    today_bars = bundle.get("today_bars")
    n = len(cl)
    if n < 20:
        return None
    # ---- session volume metrics come from the authoritative 1-min bundle ----
    m1 = {"o": o, "h": h, "l": l, "c": cl, "v": v, "t": ts}
    # ---- HYBRID TIMEFRAME: prefer live-built 30s bars once enough have formed,
    # else stay on the 1-min bundle (indicators need ~35 bars to be meaningful).
    tf = "1m"
    # today_bars counts bars in the ONE-MINUTE bundle and is used far below to
    # slice today out of the exchange volume arrays. The card's own series may
    # now be 30-second bars spanning THREE sessions, so it needs its own count.
    # Sharing one variable would have made the no-dip streak and the CVD run
    # across days, and the session-volume filters read a 30s bar count against a
    # 1-minute array. Both would have looked plausible and been wrong.
    card_today_bars = today_bars
    # ---- SUB-MINUTE CARD, three sources in order of quality -------------
    # 1. Dhan's own seconds feed: real bars, warmed up from prior sessions, so
    #    the card is right from the first minute of the session.
    # 2. the live tick builder, as before -- still the only source if the feed
    #    refuses, and still what _cross30() and momentum read.
    # 3. 1-minute bars.
    # Falling back is silent by design, but the card CARRIES which source it
    # used (tfSrc), because a 30s card built from 18 minutes of pre-open air and
    # one built from real bars looked identical on screen for three sessions.
    tf_src = None
    _prev_close = bundle.get("prev_close") if isinstance(bundle, dict) else None
    _session = bundle.get("session") if isinstance(bundle, dict) else None
    if CARD_TF != "1m":
        secb, _sec_err = candle_v3.fetch_seconds(sid, interval=CARD_TF)
        if secb and len(secb["candles"]["close"]) >= 20:
            sc = secb["candles"]
            o, h, l, cl = sc["open"], sc["high"], sc["low"], sc["close"]
            v, ts = sc["volume"], sc["timestamp"]
            n = len(cl)
            tf = _TF_LABEL.get(CARD_TF, CARD_TF)
            tf_src = "feed"
            card_today_bars = secb.get("today_bars") or len(cl)
            bundle = {"candles": sc, "prev_close": secb.get("prev_close") or _prev_close,
                      "session": secb.get("session") or _session,
                      "today_bars": secb.get("today_bars")}
    s30 = TICKS.series(sid, n=200)
    if tf_src is None and s30 and len(s30["c"]) >= MIN_BARS_30S:
        o, h, l, cl, v, ts = s30["o"], s30["h"], s30["l"], s30["c"], s30["v"], s30["t"]
        n = len(cl)
        tf = "30s"
        tf_src = "ticks"
        card_today_bars = len(cl)
        bundle = {"candles": {"open": o, "high": h, "low": l, "close": cl,
                              "volume": v, "timestamp": ts},
                  "prev_close": _prev_close,
                  "session": _session, "today_bars": len(cl)}

    # ---- EXACT V3.5 CARD PAYLOAD -----------------------------------------
    # Same scorer the :5003 board uses -> identical chart fields (ema9, sma12,
    # macdLine/Signal/Hist, cloudHi/Lo/State, czones, utBuy/utSell) and identical
    # row attributes (day_pct, ema_angle, vol_surge_x, day_vol, badges, chips).
    row = _scorer.compute_row(name_row["sym"], sid, bundle)
    if not row or row.get("error"):
        return None
    ch = row.get("chart") or {}
    # 30s cards: recompute the grey consolidation boxes with doubled bar params so
    # a zone still spans the same wall-clock window as it does on 1-min.
    if tf != "1m":
        try:
            import Opus_indicators as _OI
            _cz = _OI.consolidation_zones(h, l, cl, CONS_LB_30S, CONS_ML_30S)
            _sw = max(0, len(cl) - len(ch.get("c") or []))
            ch["czones"] = [{"s": max(0, z["s"] - _sw), "e": z["e"] - _sw,
                             "top": z["top"], "bottom": z["bottom"]}
                            for z in _cz if z["e"] >= _sw]
        except Exception:
            pass
    ema9 = I.ema_series(cl, 9)
    ma12 = I.sma_series(cl, 12)
    # ---- MA/EMA buy-side cross: EMA-9 crossing ABOVE MA-12, still above ----
    # Search only within today's session (see the streak note below).
    _cs_start = max(1, n - int(card_today_bars)) if card_today_bars else 1
    cross_idx = None
    for i in range(n - 1, _cs_start - 1, -1):
        if ema9[i] is None or ma12[i] is None or ema9[i - 1] is None or ma12[i - 1] is None:
            continue
        if ema9[i] > ma12[i] and ema9[i - 1] <= ma12[i - 1]:
            cross_idx = i
            break
        if ema9[i] < ma12[i]:
            break
    # a cross must have REAL separation, not 0.003% of price (see CROSS_MIN_SEP)
    _sep = ((ema9[-1] - ma12[-1]) / ma12[-1]) if (ema9[-1] is not None
                                                  and ma12[-1]) else 0.0
    cross_buy = (cross_idx is not None
                 and (n - 1 - cross_idx) <= CROSS_FRESH_BARS
                 and ema9[-1] is not None and ma12[-1] is not None
                 and ema9[-1] > ma12[-1]
                 and _sep >= CROSS_MIN_SEP)
    # ---- NO-DIP STREAK: how many consecutive bars has it climbed without a dip?
    # IMPORTANT: only TODAY's bars count. The bundle carries prior sessions for
    # indicator warm-up, and counting those produced impossible streaks (e.g. a
    # "220-bar no-dip run" 16 minutes into the session).
    _sess_start = max(0, n - int(card_today_bars)) if card_today_bars else 0
    run_peak = None
    last_dip = _sess_start - 1
    # GRACE (fix 1 of 3, 24-Aug). A live climb breathes. Allowing DIP_GRACE
    # small givebacks inside a streak, while still resetting hard on anything
    # over DIP_GRACE_MAX, is the difference between RAMBHAJO pinning at 09:16
    # and pinning at 09:44 after nine resets.
    grace_left = DIP_GRACE
    dips_used = 0
    for i in range(_sess_start, n):
        px_i = cl[i]
        if px_i is None:
            continue
        run_peak = px_i if run_peak is None else max(run_peak, px_i)
        dd = (run_peak - px_i) / run_peak if run_peak else 0.0
        below_ema = (ema9[i] is not None and px_i < ema9[i])
        if dd > DIP_PCT or below_ema:
            # A deep giveback, or a dip once the grace is spent, ends the streak.
            if dd > DIP_GRACE_MAX or below_ema or grace_left <= 0:
                last_dip = i
                run_peak = px_i                  # restart the peak after a dip
                grace_left = DIP_GRACE           # streak restarts with fresh grace
                dips_used = 0
            else:
                grace_left -= 1                  # forgiven, streak continues
                dips_used += 1
                run_peak = px_i                  # but re-base the peak
    nodip_bars = n - 1 - last_dip
    # ---- CVD (cumulative volume delta) over TODAY's session: up-bar volume
    # counts +, down-bar volume counts -. Feeds the buy/sell-control verdict.
    _cvd, _cser = 0.0, []
    for i in range(_sess_start, n):
        if i >= len(o) or o[i] is None or cl[i] is None:
            continue
        _cvd += float(v[i] or 0) * (1.0 if cl[i] >= o[i] else -1.0)
        _cser.append(_cvd)
    cvd_up = None
    if len(_cser) >= 4:
        cvd_up = bool(_cser[-1] >= 0 and _cser[-1] >= _cser[max(0, len(_cser) - 6)])
    uptrend_now = (ema9[-1] is not None and ma12[-1] is not None
                   and ema9[-1] > ma12[-1] and cl[-1] >= ema9[-1])
    # how much did it actually GAIN across the no-dip streak?
    gain_streak = None
    if last_dip + 1 < n and cl[last_dip + 1]:
        gain_streak = round((cl[-1] / cl[last_dip + 1] - 1) * 100, 2)
    _day_pct = row.get("day_pct") if isinstance(row, dict) else None
    # ---- STALENESS (fix 3 of 3, 24-Aug): how long since it made a new high?
    # A badge that outlives its move is worse than no badge -- it is a false
    # invitation. Un-pinning used to need a 0.6% dip or a close under EMA9, and
    # a stock that merely STOPS RISING triggers neither: FCL held its badge 48
    # minutes after its run finished, HSCL 45. Time since the last high catches
    # exactly that case, and nothing else does.
    # Use the timeframe THIS function actually built the card on. It used to read
    # row["tf"], which the scorer does not set on a sub-minute bundle -- so every
    # 30s card measured staleness in 60-second bars and a badge outstayed its
    # move by double the intended time without anything saying so.
    _bar_sec = _TF_SEC.get(CARD_TF, 60) if tf_src else (30 if tf == "30s" else 60)
    _since_high = None
    if n > _sess_start:
        _hi_i, _hi_px = None, None
        for i in range(_sess_start, n):
            if cl[i] is None:
                continue
            if _hi_px is None or cl[i] >= _hi_px:
                _hi_px, _hi_i = cl[i], i
        if _hi_i is not None:
            _since_high = (n - 1 - _hi_i) * _bar_sec
    _stale_pin = bool(_since_high is not None and _since_high >= STALE_NO_HIGH_SEC)

    # ---- IGNITION (fix 2 of 3, 24-Aug): a fast, provisional way onto the badge.
    # The bar rule is sound but slow -- eight bars is eight bars, and on 1-minute
    # bars that is an 8-minute floor before a badge can exist at all. Thirteen of
    # today's thirty late badges were simply serving that sentence while the
    # stock ran without them.
    #
    # This reads the alarm's rolling snapshots, which are already in memory and
    # cost NO extra Dhan request. A 0.6% rise inside 90 seconds on real turnover
    # raises the badge NOW; the eight-bar rule then confirms it a few minutes
    # later. Provisional badges are marked, so a card that got there fast is
    # never mistaken for one that earned it the slow way.
    # The bare `except: pass` this replaced could not tell "the rule said no"
    # from "the call threw and nobody heard". That is the exact pattern that
    # hid the UC failure for a whole session, so the numbers and any error are
    # now carried on the card.
    _ignited = False
    _ign_pct = _ign_rup = None
    _ign_err = None
    try:
        if alarm is None:
            _ign_err = "alarm module not loaded"
        elif _day_pct is None or _day_pct <= PIN_MIN_DAY_PCT:
            _ign_err = "red on the day"
        else:
            _pct, _rup, _win = alarm.delta(sid, seconds=IGNITE_WINDOW)
            _ign_pct, _ign_rup = _pct, _rup
            if _pct is None or _rup is None:
                # Most likely cause: fewer than two sweeps in the window, i.e.
                # the alarm has not built enough history yet.
                _ign_err = "no delta -- alarm history too short"
            elif _pct < IGNITE_PCT * 100:
                _ign_err = f"only {_pct:.2f}% in {IGNITE_WINDOW}s"
            elif _rup < IGNITE_MIN_TOVER:
                _ign_err = f"only Rs {_rup / 1e7:.1f}Cr in {IGNITE_WINDOW}s"
            else:
                _ignited = True
    except Exception as _e:
        _ign_err = f"{type(_e).__name__}: {str(_e)[:60]}"

    _pin_confirmed = bool(uptrend_now
                          and nodip_bars >= PIN_MIN_BARS
                          and (gain_streak or 0) >= PIN_MIN_GAIN * 100
                          and (_day_pct is None or _day_pct > PIN_MIN_DAY_PCT))
    # Staleness overrides BOTH routes. A stock that has not made a new high in
    # five minutes is not running, however it got its badge.
    pinned = bool((_pin_confirmed or _ignited) and not _stale_pin)
    _pin_provisional = bool(pinned and not _pin_confirmed)

    # ---- session volume metrics for the universe filters ------------------
    # ALWAYS from the 1-min bundle (exact exchange volume, full session), never
    # from the sampled 30s bars. Bundle can span prior days -> isolate today.
    mt, mv = m1["t"], m1["v"]
    if today_bars:
        sess_idx = list(range(max(0, len(mt) - int(today_bars)), len(mt)))
    else:
        last_day = datetime.fromtimestamp(mt[-1], engine.IST).strftime("%Y-%m-%d")
        sess_idx = [i for i in range(len(mt))
                    if datetime.fromtimestamp(mt[i], engine.IST).strftime("%Y-%m-%d") == last_day]
    first2_vol = 0.0
    sess_vol = 0.0
    first2_val = 0.0                       # rupees traded in the first 2 minutes
    sess_val = 0.0                         # rupees traded so far today
    mc = m1["c"]
    for j, i in enumerate(sess_idx):
        vol = float(mv[i] or 0)
        px_i = float(mc[i] or 0) if i < len(mc) and mc[i] is not None else 0.0
        sess_vol += vol
        sess_val += vol * px_i
        hhmm = datetime.fromtimestamp(mt[i], engine.IST).strftime("%H:%M")
        if j < 2 or hhmm <= "09:16":        # first two 1-min bars of the session
            first2_vol += vol
            first2_val += vol * px_i
    bars_since_open = len(sess_idx)
    # numeric epochs for the chart's time axis (the V3.5 UI reads chart.tsec)
    try:
        _cs = row["chart"]
        _sw = max(0, len(ts) - len(_cs.get("c") or []))
        _cs["tsec"] = [int(x) for x in ts[_sw:]]
        # VOLUME for the mini chart's volume pane. The V3.5 payload deliberately
        # carries no volume array (it only ever drew price + MACD), so the pane
        # would render empty without this. Sliced on the SAME window as tsec so
        # bar i of chart.v is bar i of chart.c -- any drift here mis-colours the
        # volume bars, which is exactly the class of bug that bit CVD earlier.
        _cs["v"] = [int(v[i] or 0) for i in range(_sw, len(v))]
    except Exception:
        pass

    # card = the FULL V3.5 row (chart + every badge/attribute) + Movers extras
    card = dict(row)
    card.update({
        "sym": name_row["sym"], "sid": sid,
        "lists": name_row.get("lists") or [],
        "tvol": name_row.get("tvol"),
        "tf": tf, "tfSrc": tf_src, "bars30": (len(s30["c"]) if s30 else 0),
        "first2_vol": int(first2_vol), "sess_vol": int(sess_vol),
        "first2_val": int(first2_val), "sess_val": int(sess_val),
        "bars": bars_since_open,
        "prev_close": bundle.get("prev_close") if isinstance(bundle, dict) else None,
        "cvd_up": cvd_up,
        "pinned": pinned, "nodip": int(nodip_bars), "streakPct": gain_streak,
        # Surfaced so the 15-minute auditor can tell WHY a badge was or was not
        # on, without guessing. Every one of these was a blind spot today.
        "dipsUsed": int(dips_used), "sinceHigh": _since_high,
        "stalePin": _stale_pin, "pinProvisional": _pin_provisional,
        "pinConfirmed": _pin_confirmed, "ignited": _ignited,
        "ignPct": (round(_ign_pct, 2) if _ign_pct is not None else None),
        "ignRup": (round(_ign_rup / 1e7, 2) if _ign_rup is not None else None),
        "ignErr": _ign_err,
        # ---- LIVE MOMENTUM on EVERY card -----------------------------------
        # The board used to have no momentum figure at all, so "sort by
        # momentum" could only ever have meant Scanner1. Filled in below from
        # the SAME function Scanner1 uses, so one number means one thing on
        # every tab. Costs no request -- it reads the alarm's rolling snapshots.
        "momentum": 0, "stale": False,
        "crossBuy": bool(cross_buy),
        "crossAge": (n - 1 - cross_idx) if cross_idx is not None else None,
        "ucl": name_row.get("ucl") or _uc_from_alarm(sid),
    })
    _cache[sid] = {"ts": time.time(), "card": card}
    return card


def _uc_from_alarm(sid):
    """Upper circuit for one stock, from the alarm's sweep. Costs NO request.

    The Board fills `ucl` with a batched circuit_limits call in cycle(), so its
    cards show "UC Rs x (y%)". Every other tab -- Scanner1, Super Stocks, ScanX
    Blast -- builds cards through the same build_card() but never got that
    backfill, so the same stock showed its upper circuit on one tab and not on
    another. The sweep already carries it (index 5 of every snapshot tuple), so
    reading it here makes UC universal for free.
    """
    if alarm is None:
        return None
    try:
        _, snap = alarm.snapshot()
        t = (snap or {}).get(str(sid))
        if t and len(t) > 5 and t[5]:
            return round(float(t[5]), 2)
    except Exception:
        pass
    return None


_mom_warn = {"t": 0.0}


def _stamp_momentum(cards):
    """Attach the live 0-100 momentum score to each card, in place.

    Uses Movers_scanner1.momentum() -- the same function Scanner1 scores with --
    so the number on a Board card and the number on a Scanner1 card are the same
    measurement. Silent no-op if the scanner module is unavailable, because the
    board must keep working without it.
    """
    # THREE SILENT RETURNS, and they were the whole problem.
    #
    # I added per-card failure reasons and still got nothing in the log, because
    # every one of these bails out BEFORE the loop and before any logging. Zero
    # momentum then produces zero log lines -- worse than the bug it was meant
    # to explain, and my own mistake, made in the middle of fixing the same
    # class of mistake. Each one now says so, once a minute.
    def _bail(reason):
        if time.time() - _mom_warn["t"] > 60:
            _mom_warn["t"] = time.time()
            log(f"momentum: NOT STAMPED -- {reason}")
    if scanner1 is None or alarm is None:
        _bail(f"module missing (scanner1={scanner1 is not None}, "
              f"alarm={alarm is not None})")
        return
    try:
        _, snap = alarm.snapshot()
    except Exception as e:
        _bail(f"alarm.snapshot() raised {type(e).__name__}: {str(e)[:80]}")
        return
    if not snap:
        _bail("the alarm has no snapshot yet (it sweeps every 5s; this is "
              "normal for the first few seconds after a restart)")
        return
    try:
        mins = scanner1.mins_since_open()
    except Exception as e:
        _bail(f"mins_since_open() raised {type(e).__name__}: {str(e)[:80]}")
        return
    stamped = 0
    # WHY IT FAILED, not just THAT it failed.
    #
    # This has stamped 0 of every card since 19-Aug and nobody could say why,
    # because each per-card failure hit a bare `except Exception: continue`.
    # "0 stamped" and "no data" then look identical, which is the single
    # mistake this project has repeated most -- it is written down twice in
    # HANDOVER and it was still live here. Reading the code did not settle it
    # either: snapshot keys are str(sid), delta() casts str(sid), and
    # momentum() returns a dict unless price or volume is zero. So the code
    # now records the FIRST reason of each kind and prints them.
    total = len(cards) if hasattr(cards, "__len__") else 0
    why = {}
    def _note(kind, detail=""):
        if kind not in why:
            why[kind] = detail
    for cc in cards:
        sid_s = str(cc.get("sid"))
        try:
            t = snap.get(sid_s)
            if not t:
                _note("sid not in the alarm snapshot",
                      f"{cc.get('sym')} sid={sid_s} (snapshot holds {len(snap)})")
                continue
            if len(t) < 5:
                _note("snapshot tuple too short", f"{cc.get('sym')} len={len(t)}")
                continue
            # (ltp, volume, open, high, low, upper_cct, lower_cct, prev_close)
            mm = scanner1.momentum(cc["sid"], t[0], t[1], t[2], t[3], t[4], mins)
            if mm:
                cc.update(mm)
                stamped += 1
            else:
                _note("momentum() returned None",
                      f"{cc.get('sym')} ltp={t[0]} vol={t[1]} open={t[2]} "
                      f"hi={t[3]} lo={t[4]} mins={mins}")
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            where = f"{tb[-1].name}:{tb[-1].lineno}" if tb else "?"
            _note(f"{type(e).__name__}", f"{cc.get('sym')} {str(e)[:70]} @{where}")
            continue
    # LOUD ON FAILURE.
    #   This stamped 0 of 4,393 cards today and nothing said so, because every
    #   error is swallowed per card. A silent zero is indistinguishable from
    #   "no data", which is how it went unnoticed for two days.
    if stamped == 0 and time.time() - _mom_warn["t"] > 60:
        _mom_warn["t"] = time.time()
        log(f"momentum: STAMPED 0 of {total} cards (snapshot has {len(snap)} sids)"
            f" mins_since_open={mins}")
        for k, v in why.items():
            log(f"   momentum WHY: {k} -- e.g. {v}")
        if not why:
            log("   momentum WHY: no cards were passed in at all")


# ---- ONE ordering rule for EVERY card grid -------------------------------
# The problem this solves: NO-DIP cards were scattered down the Board and
# Scanner1 grids because each grid had its own sort and pinning was only ever a
# tie-breaker inside a bias tier. Now a NO-DIP badge lifts a card to the TOP of
# whatever grid it is in, ordered among other NO-DIP cards by momentum -- and
# everything BELOW that block keeps the grid's own existing priorities exactly
# as they were.
def _nodip_first(cc):
    """Leading sort keys: NO-DIP block on top, ordered by momentum inside it.

    TWO THINGS THAT ARE DELIBERATE, both caught by the test rather than by
    reading the code:

    1. STALE IS CHECKED BEFORE MOMENTUM, not after. With momentum first, a
       pinned-but-dead card (momentum 70, stale) sorted above a pinned card
       still moving (momentum 52) -- the streak was real but finished, which is
       the one pinned card you do not want at the top of the screen.

    2. STALE ONLY APPLIES INSIDE THE PINNED BLOCK. Sinking every stale card
       everywhere would silently re-order the Board's unpinned cards too, and
       the instruction was that below the NO-DIP block each grid keeps its
       EXISTING priorities. Unpinned cards therefore return identical keys here
       and fall through to whatever that grid already did.
    """
    pinned = bool(cc.get("pinned"))
    if not pinned:
        return (True, False, 0)                   # neutral -> grid's own order decides
    return (False,                                # pinned block first
            bool(cc.get("stale")),                # finished streaks last inside it
            -(cc.get("momentum") or 0))           # then strongest momentum


_early_log = {"t": 0.0}


def _log_early(rows):
    """Record every promotion so the LEAD TIME can be measured tomorrow.

    Without this the whole change is unfalsifiable: I cannot replay the alarm
    sweep (it is not persisted), so the only way to know whether promotion
    actually surfaces a stock EARLIER than the scanx lists is to write down when
    each one was promoted and compare against when the board would otherwise
    have first seen it.
    """
    if time.time() - _early_log["t"] < 20:
        return
    _early_log["t"] = time.time()
    try:
        d = HERE / "logs" / "movers_board"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"early_{datetime.now(engine.IST).strftime('%Y%m%d')}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(engine.IST).strftime("%Y-%m-%d %H:%M:%S"),
                                "rows": rows}, default=str) + "\n")
    except Exception:
        pass


_names_cache = {"ts": 0.0, "names": None}
NAMES_TTL = 20         # re-fetch the 3 source lists at most every 20s (API friendly)
SNAP_PATH = HERE / "logs" / "movers_last_names.json"

# ---- BOARD LOGGING -------------------------------------------------------
# Everything the UI displays is written to disk for later analysis:
#   board_YYYYMMDD.jsonl  one line per snapshot: every card in every panel, in
#                         display order, with all badges/indicator values.
#   bars30_YYYYMMDD.jsonl completed 30s bars (IRREPLACEABLE - no API can serve
#                         sub-minute history, so if we don't log it it's gone).
#   news_YYYYMMDD.jsonl   headlines shown on the News tab.
BOARDLOG_DIR = HERE / "logs" / "movers_board"
BOARDLOG_SEC = 10          # snapshot cadence (UI refreshes faster; 10s keeps size sane)
_lastlog = {"t": 0.0, "news": 0.0}


def _logfile(prefix):
    BOARDLOG_DIR.mkdir(parents=True, exist_ok=True)
    return BOARDLOG_DIR / f"{prefix}_{datetime.now(engine.IST).strftime('%Y%m%d')}.jsonl"


def _card_record(c, rank):
    """Compact, analysis-ready record of exactly what the card shows."""
    def last(a):
        if not a:
            return None
        for x in reversed(a):
            if x is not None:
                return x
        return None
    ch = c.get("chart") or {}
    return {
        "rank": rank, "sym": c.get("sym"), "sid": c.get("sid"),
        "price": c.get("price"), "day_pct": c.get("day_pct"),
        "tvol": c.get("tvol"), "first2_vol": c.get("first2_vol"),
        "sess_vol": c.get("sess_vol"), "bars": c.get("bars"),
        "first2_val": c.get("first2_val"), "sess_val": c.get("sess_val"),
        "tf": c.get("tf"), "bars30": c.get("bars30"),
        "crossBuy": c.get("crossBuy"), "crossAge": c.get("crossAge"),
        "pinned": c.get("pinned"), "nodip": c.get("nodip"),
        "streakPct": c.get("streakPct"), "lists": c.get("lists"),
        # ---- BADGE DIAGNOSTICS ------------------------------------------
        # Added 25-Aug after these were put on the card but NOT here, so the
        # 15-minute auditor read them as absent and reported "0 badges via
        # fast ignition" when it simply could not see them. The card was fine;
        # the log was blind. Instrumentation that silently reads False is worse
        # than no instrumentation, because it is believed.
        "dipsUsed": c.get("dipsUsed"), "sinceHigh": c.get("sinceHigh"),
        "stalePin": c.get("stalePin"), "pinProvisional": c.get("pinProvisional"),
        "pinConfirmed": c.get("pinConfirmed"), "ignited": c.get("ignited"),
        # WHY ignition did or did not fire, in numbers rather than a boolean.
        # A bare True/False could not distinguish "the rule said no" from
        # "the call threw and was swallowed".
        "ignPct": c.get("ignPct"), "ignRup": c.get("ignRup"),
        "ignErr": c.get("ignErr"),
        "bias": c.get("bias"), "biasRank": c.get("biasRank"), "biasImb": c.get("biasImb"),
        # V3.5 row attributes shown on the card
        "ucl": c.get("ucl"), "day_vol": c.get("day_vol"),
        "ema_angle": c.get("ema_angle"), "vol_surge_x": c.get("vol_surge_x"),
        "stage": c.get("stage"), "frozen": c.get("frozen"), "entry": c.get("entry"),
        "igniting": c.get("igniting"), "fresh": c.get("fresh"),
        "ut_in_buy": c.get("ut_in_buy"), "cross_time": c.get("cross_time"),
        "dip_state": c.get("dip_state"), "score": c.get("score"),
        "sa": c.get("sa"), "sb": c.get("sb"), "sc": c.get("sc"),
        # latest indicator values as plotted
        "ema9": last(ch.get("ema9")), "sma12": last(ch.get("sma12")),
        "macd": last(ch.get("macdLine")), "macd_sig": last(ch.get("macdSignal")),
        "macd_hist": last(ch.get("macdHist")),
        "bar": {"o": last(ch.get("o")), "h": last(ch.get("h")),
                "l": last(ch.get("l")), "c": last(ch.get("c"))},
        # live order-book bias exactly as shown on the card
        "depth": _depth_rec(c.get("sym")),
    }


def _depth_rec(sym):
    """Whole-book buy/sell totals + imbalance for the board log."""
    d = (_depth_cache.get("data") or {}).get(sym)
    if not d:
        return None
    tb, ts_ = d.get("tot_buy") or 0, d.get("tot_sell") or 0
    imb = ((tb - ts_) / (tb + ts_) * 100.0) if (tb > 0 and ts_ > 0) else None
    return {"tot_buy": tb, "tot_sell": ts_,
            "imb_pct": round(imb, 1) if imb is not None else None,
            "ltp": d.get("ltp")}


def log_board():
    """Write one snapshot of the whole visible board."""
    now = time.time()
    if now - _lastlog["t"] < BOARDLOG_SEC:
        return
    _lastlog["t"] = now
    try:
        rec = {"ts": datetime.now(engine.IST).strftime("%Y-%m-%d %H:%M:%S"),
               "note": STATE.get("note"), "tf": STATE.get("tf"),
               "panels": {}}
        for key, label in (("mv", "Movers (Intraday+Price)"), ("bv", "By Volumes"),
                           ("im", "Intraday Movers"), ("pm", "Price Movers")):
            rec["panels"][label] = [_card_record(c, i + 1)
                                    for i, c in enumerate(STATE.get(key) or [])]
        with _logfile("board").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def _save_snapshot(names):
    """Persist the last GOOD list so the board can still render pre-market."""
    try:
        SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SNAP_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved": datetime.now(engine.IST).isoformat(),
                                   "names": names[:200]}), encoding="utf-8")
        tmp.replace(SNAP_PATH)
    except Exception:
        pass


def _load_snapshot():
    try:
        d = json.loads(SNAP_PATH.read_text(encoding="utf-8"))
        return d.get("names") or [], d.get("saved")
    except Exception:
        return [], None


def _day_key():
    return datetime.now().strftime("%Y%m%d")


def cycle():
    _t_start = time.time()
    now_t = time.time()
    stale_src = None
    if _names_cache["names"] and now_t - _names_cache["ts"] < NAMES_TTL:
        names, err = _names_cache["names"], None
    else:
        names, err = movers_source.cash_names(None, log)
        if names:
            _names_cache.update({"ts": now_t, "names": names})
            _save_snapshot(names)
    if not names:
        # Pre-market / holiday / feed outage: fall back to the last saved board
        # so the UI still shows charts instead of going blank.
        names, saved = _load_snapshot()
        stale_src = saved
        if names:
            log(f"cycle: live feed empty ({err or 'no names'}) -> last snapshot from {saved}")
        else:
            log(f"cycle: no names and no snapshot yet ({err or 'empty feed'})")
            STATE["note"] = "waiting for the market feed (pre-market)"
            return
    STATE["note"] = (f"showing last saved board ({stale_src[11:16]})" if stale_src else None)
    # ---- PROMOTE EARLY MOVERS THE SCANX LISTS HAVE NOT CAUGHT -------------
    #   Measured 20-Aug: 72 stocks were already up >=2% the FIRST time the board
    #   saw them, because a stock only joins Dhan's gainer lists after it has
    #   moved enough to rank. GENUSPOWER arrived at +5.11%, ISTLTD at +15.76%,
    #   MYSORPETRO with 100% of its move already done.
    #
    #   The NO-DIP badge itself is not the problem -- median pin lag after first
    #   sighting is 0 seconds. So nothing about the badge changes here. These
    #   names are simply appended to the board's list so build_card() runs on
    #   them and the SAME streak logic gets a chance to fire while the move is
    #   still happening. The alarm's sweep already quotes them every 5 seconds,
    #   so this costs no additional request.
    promoted = []
    if alarm is not None:
        try:
            have = {str(n.get("sym") or "").upper() for n in names}
            for r in alarm.early_movers(exclude=have):
                promoted.append({"sym": r["sym"], "sid": r["sid"],
                                 "lists": ["Intraday Movers"],
                                 "tvol": r.get("volume"), "day_pct": r.get("day_pct"),
                                 # ---- 21-Sep-2026: ltp/day_tover MUST be carried ----
                                 # Without them the board's FILTER 1 computes
                                 #   tov = tvol * (ltp or 0) = 0
                                 # and every promoted row is silently dropped by the
                                 # MIN_DAY_TOVER (Rs 5 Cr) gate. Measured over the 8
                                 # sessions to 21-Sep: 632 of 1058 early-flagged names
                                 # (60%) NEVER reached the board, and the survivors
                                 # arrived only once Dhan's own ScanX lists caught up
                                 # -- EMMVEE flagged 09:22:11 at +2.71%, on board
                                 # 09:29:41 at +5.14% with 80% of the move gone.
                                 # early_movers() already returns both values.
                                 "ltp": r.get("price"),
                                 "day_tover": (r.get("tover_cr") or 0) * 1e7,
                                 "early": True, "from_open": r["from_open"]})
            # OUR OWN SCANNER, 30-Sep. Same purpose as early_movers above, but
            # it sweeps the WHOLE NSE equity universe every 2 seconds from two
            # bulk quote requests, instead of waiting for Dhan's lists to rank
            # a stock. On 30-Sep the board flagged TVSELECT at 10:20:32 while
            # the engine had valid signals on it from 10:13 -- the entire
            # 389 -> 407 run was invisible to both. Additive: with
            # SURGE_SCAN=NO nothing here fires and the board is unchanged.
            try:
                import surge as _SG
                _have2 = have | {str(p["sym"]).upper() for p in promoted}
                for _sym, _r in _SG.rows(_day_key()).items():
                    if _sym.upper() in _have2 or not _r.get("sid"):
                        continue
                    promoted.append({"sym": _sym, "sid": _r["sid"],
                                     "lists": ["Surge"],
                                     "day_pct": _r.get("move"),
                                     "ltp": _r.get("px"),
                                     "early": True, "surge": True,
                                     "from_open": _r.get("move")})
            except Exception:
                pass
            if promoted:
                names = list(names) + promoted
                log("early: promoted " + ", ".join(
                    f"{p['sym']}+{p['from_open']}%" for p in promoted[:8])
                    + (f" (+{len(promoted)-8} more)" if len(promoted) > 8 else ""))
                _log_early(promoted)
        except Exception as e:
            log(f"early: {type(e).__name__} {str(e)[:110]}")
    # ---- KEEP A MOVING STOCK ON THE BOARD (01-Sep-2026) ------------------
    # A card exists only while its stock sits on one of Dhan's three ScanX
    # lists, and those lists churn constantly -- "Intraday Movers" ranged from
    # 12 to 50 names within single minutes on 01-Sep. When a stock rotates off,
    # its card vanishes mid-move even though nothing about the stock changed.
    #
    # Measured that morning, with the board running normally and writing 233
    # snapshots without a single gap over 45 seconds:
    #     SOTL        gone 09:17:31 -> 09:22:22   (291s, mid-surge)
    #     KALYANIFRG  gone 09:18:16 -> 09:25:42   (446s, mid-surge)
    #     GRAPHITE    gone 09:18:16 -> 09:22:41   (265s, mid-surge)
    # All three were climbing throughout. He was watching a card disappear
    # while the stock it described was still going up.
    #
    # So a name that was on the board recently is re-injected while it is still
    # green on the day. This costs no request -- the alarm's sweep already
    # quotes every stock -- and the ordinary price, liquidity and circuit
    # filters below still apply to it exactly as before. Nothing gets onto the
    # board this way that could not have been there on its own merits; it just
    # stops falling off for a reason that has nothing to do with the stock.
    _now_t = time.time()
    _have = {str(n.get("sid")) for n in names}
    for _sid, _rec in list(STICKY.items()):
        if _now_t - _rec["ts"] > STICKY_SEC:
            del STICKY[_sid]
            continue
        if _sid in _have:
            continue
        try:
            _, _snap = alarm.snapshot()
            _q = (_snap or {}).get(_sid)
            if not _q:
                continue
            _ltp, _prev = float(_q[0] or 0), float(_q[7] or 0) if len(_q) > 7 else 0.0
            if _ltp <= 0 or _prev <= 0 or (_ltp / _prev - 1) * 100 < STICKY_MIN_DAY_PCT:
                del STICKY[_sid]                 # no longer up on the day
                continue
        except Exception:
            continue
        names = list(names) + [{"sym": _rec["sym"], "sid": _sid,
                                "lists": _rec["lists"] or ["Price Movers"],
                                "tvol": _rec.get("tvol"), "sticky": True}]
        _have.add(_sid)

    panels = {"Intraday Movers": [], "By Volumes": [], "Price Movers": []}
    for nrow in names:
        for lbl in (nrow.get("lists") or []):
            if lbl in panels:
                panels[lbl].append(nrow)

    # ---- PROGRESSIVE candling: no display cap. Every eligible symbol gets a
    # card; we just refresh a STAGGERED slice each cycle (oldest cache first),
    # in parallel, so the 5s cycle never stalls and the API stays within budget.
    # FILTER 1 (pre-candle, free): price >= MIN_PRICE. Also drop names whose whole
    # day volume can't even reach the first-2-min floor -- saves API calls.
    # The volume rules only make sense once the session has actually traded. Before
    # 09:15 (and in the first couple of minutes) every stock shows ~0 volume, so
    # applying them then would empty the whole board -- which is exactly what it did.
    mins_open = _mins_since_open()
    vol_rules_on = mins_open >= 3
    dropped_px = dropped_vol = 0
    uniq = {}
    for lbl, lst in panels.items():
        lst.sort(key=lambda x: -(x.get("tvol") or 0))
        keep = []
        for nrow in lst:
            ltp = nrow.get("ltp")
            if ltp is not None and float(ltp) < MIN_PRICE:
                dropped_px += 1
                continue
            tov = nrow.get("day_tover") or ((nrow.get("tvol") or 0) * (ltp or 0))
            if vol_rules_on and float(tov or 0) < MIN_DAY_TOVER:
                dropped_vol += 1
                continue
            keep.append(nrow)
            uniq.setdefault(nrow["sid"], nrow)
        panels[lbl] = keep
    # feed the 30s tick builder with exactly the symbols on the board
    try:
        TICKS.track(list(uniq.keys()),
                    {sid: nr.get("sym") for sid, nr in uniq.items()})
    except Exception:
        pass
    stale = [nr for sid, nr in uniq.items()
             if (time.time() - _cache.get(sid, {}).get("ts", 0))
             >= (SUBMIN_TTL if (_cache.get(sid, {}).get("card", {}).get("tf") or "1m") != "1m"
                 else CACHE_TTL)]
    # ---- WHICH SYMBOLS GET RE-CANDLED THIS CYCLE -------------------------
    # Plain oldest-first spends the whole budget evenly, so a 15-second refresh
    # for the cards at the top of the screen would mean requesting EVERY
    # eligible symbol at 15s -- 4-6 requests/sec sustained against an
    # undocumented endpoint, on an account Dhan has already warned about volume.
    #
    # He looks at the top of the two columns. Those get priority; the rest still
    # get a guaranteed share, because starving the tail is a feedback loop:
    # a card that never refreshes keeps a stale price, so it never sorts up, so
    # it never refreshes. 70/30 keeps the top of the screen inside 15s at about
    # 3 requests/sec, and still brings every symbol round.
    _rank = {}
    for _k in ("ov", "bt", "mv"):
        for _i, _c in enumerate(STATE.get(_k) or []):
            _rank.setdefault(str(_c.get("sid")), _i)
    _age = lambda nr: _cache.get(nr["sid"], {}).get("ts", 0)
    stale.sort(key=_age)                                   # oldest first
    n_prio = max(1, int(REFRESH_PER_CYCLE * 0.7))
    by_rank = sorted(stale, key=lambda nr: (_rank.get(str(nr["sid"]), 9999), _age(nr)))
    todo, taken = [], set()
    for nr in by_rank[:n_prio]:
        todo.append(nr); taken.add(nr["sid"])
    for nr in stale:                                       # remainder: oldest first
        if len(todo) >= REFRESH_PER_CYCLE:
            break
        if nr["sid"] not in taken:
            todo.append(nr); taken.add(nr["sid"])
    # ---- CANDLE FETCHING IS NO LONGER DONE HERE (01-Sep-2026) -------------
    # It used to run inline, so every cycle blocked on the chart feed's rate
    # limiter: 20 symbols at 3.5 requests/sec is 5.7 SECONDS of pure waiting
    # before a single card could be re-sorted. Measured cycle time was 13-21s
    # against a 5-second target, and almost none of it was computation.
    #
    # The ordering, the live price, the bias and the badges do not need the
    # chart feed at all -- they come from the batched quote and the depth call,
    # which are one request each for the WHOLE board. So the fetching now runs
    # in its own thread at whatever rate is safe, and cycle() reads the cache
    # and returns. Same requests per minute, same data, board re-sorts every
    # 5 seconds instead of every 15.
    WANTED["rows"] = todo
    WANTED["ts"] = time.time()

    # cards = everything currently cached (fresh or slightly stale) + live list meta
    # FILTERS 2 & 3 (need candles): first-2-min volume floor, then the post-2-min
    # entry-volume trigger.
    cards = {}
    f_first2 = f_entry = f_price = f_uc = 0
    for sid, nrow in uniq.items():
        ent = _cache.get(sid)
        if not ent:
            continue
        card = dict(ent["card"])
        if card.get("price") is not None and card["price"] < MIN_PRICE:
            f_price += 1
            continue
        # Judge the first-2-minute volume only AFTER those 2 minutes exist -- before
        # that the figure is incomplete and would reject everything.
        # Liquidity in RUPEES only -- price-neutral, so a Rs 4,900 stock and a
        # Rs 33 stock are judged on the same amount of real money changing hands.
        # ---- THE FIRST-2-MINUTE GATE IS NO LONGER A LIFE SENTENCE ---------
        # It used to apply all session, so a verdict taken in the first two
        # minutes banned a stock for the whole day. That is the wrong shape for
        # a rule whose entire purpose is "is there real money here YET" -- and
        # it is exactly the stock this board exists to find.
        #
        # YATRA, 01-Sep, measured from its own 30-second bars:
        #     first 2 min     Rs 0.49 Cr   <- under the Rs 1 Cr floor, banned
        #     by 09:22        Rs 2.78 Cr      (over the Rs 1.5 Cr entry gate)
        #     by 09:30        Rs 8.25 Cr
        #     whole session   Rs 19.87 Cr, +5.4%, ranked on ScanX Price Movers
        # It never appeared on the board once, all day. The alarm even rescued
        # it -- promoted at 09:23 at +4.18% from open -- and the promoted row
        # was thrown out by this same gate on arrival. Super Stocks was the only
        # tab that ever showed it.
        #
        # Eight to twelve stocks were being dropped by this every single cycle.
        #
        # So it now only applies while the session is too young for the session
        # figure to mean anything. After that, sess_val -- the real measure,
        # already required below -- governs. Nothing is left ungated.
        _young = mins_open < FIRST2_WINDOW_MIN
        if vol_rules_on and _young and (card.get("bars") or 0) >= 2 \
                and (card.get("first2_val") or 0) < MIN_FIRST2_VAL:
            f_first2 += 1
            continue
        if vol_rules_on and (card.get("bars") or 0) > 2 \
                and (card.get("sess_val") or 0) <= MIN_ENTRY_VAL:
            f_entry += 1
            continue
        card["lists"] = nrow.get("lists") or []
        card["tvol"] = nrow.get("tvol")
        if nrow.get("ucl"):
            card["ucl"] = nrow.get("ucl")
        cards[sid] = card

    # ---- UC (upper circuit) backfill: one batched quote for cards missing it,
    # so the info line can show "UC ₹x (y%)" exactly like the :5003 board.
    need_uc = [sid for sid, cc in cards.items() if not cc.get("ucl")]
    if need_uc:
        try:
            ucl_map = quotes_v3.circuit_limits(need_uc[:200], lambda m: None)
            for sid, u in (ucl_map or {}).items():
                if sid in cards and u:
                    cards[sid]["ucl"] = u
                    ent = _cache.get(sid)
                    if ent:
                        ent["card"]["ucl"] = u
        except Exception:
            pass

    # ---- DEPTH refresh first: it carries the LIVE ltp used by both the price
    # overlay below and the order-book bias.
    try:
        refresh_depth({str(c["sid"]): c.get("sym") for c in cards.values()
                       if c.get("sid") is not None})
    except Exception:
        pass
    # ---- LIVE PRICE OVERLAY -------------------------------------------------
    # The card price used to come from the last COMPLETED 1-min candle, so it
    # could read up to a minute stale (RBA showed 90.85 while the tape was 90.0).
    # The depth quote (4s) and the tick builder (5s) both carry a live LTP --
    # use whichever is available so the headline price tracks the tape. The
    # candles/indicators stay bar-based, exactly as a chart should be.
    _dd = _depth_cache.get("data") or {}
    for cc in cards.values():
        live = (_dd.get(cc.get("sym")) or {}).get("ltp")
        if not live:
            try:
                live = TICKS.last_price(cc.get("sid"))
            except Exception:
                live = None
        try:
            if live and float(live) > 0:
                cc["price"] = round(float(live), 2)
                cc["live_px"] = True
                pc = cc.get("prev_close")
                if pc:
                    cc["day_pct"] = round((float(live) / float(pc) - 1) * 100, 2)
        except (TypeError, ValueError):
            pass

    # FILTER 4 (now judged on the LIVE price): hide anything locked at -- or
    # within 0.1% of -- its upper circuit. A frozen stock has no seller.
    for sid in list(cards.keys()):
        cc = cards[sid]
        try:
            if cc.get("ucl") and cc.get("price") and \
                    float(cc["price"]) >= float(cc["ucl"]) * UC_TOUCH:
                del cards[sid]
                f_uc += 1
        except (TypeError, ValueError):
            pass

    # ---- LIVE MOMENTUM, stamped fresh every cycle ---------------------------
    # NOT done inside build_card(): cards are CACHED, so a momentum computed at
    # build time would be served for as long as the cache holds the card and
    # every grid would be sorting on a number minutes out of date. Recomputed
    # here, after the live-price overlay, from the same shared function
    # Scanner1 uses.
    _stamp_momentum(cards.values())

    for cc in cards.values():
        r, lab, imb = bias_of(cc.get("sym"), cc)
        cc["biasRank"], cc["bias"] = r, lab
        cc["biasImb"] = round(imb, 1) if imb is not None else None

    def panel_cards(lbl, sort_vol_first):
        out = [cards[nr["sid"]] for nr in panels[lbl] if nr["sid"] in cards]
        # ORDER:
        #   1. BIAS TIER FIRST: buyers in control -> buy bias ->
        #      sellers in control -> sell bias -> balanced
        #   2. PINNED no-dip runners lead WITHIN their tier, longest streak first
        #   3. strongest imbalance
        #   4. buy-side MA/EMA cross, then volume (or freshest cross)
        #
        # WHY BIAS OUTRANKS PINNING (changed 07-Aug):
        #   Pinning used to be the first key, which let a pinned BALANCED runner
        #   sit above every unpinned BUY-BIAS name -- caught in monitoring as
        #   SWIGGY (rank 4, nodip 23) holding position 2 of By Volume while 14
        #   buy-bias cards sat below it. That broke the "all buy first, sell
        #   later" guarantee. Pinning is still honoured, just inside its tier.
        out.sort(key=lambda cc: (*_nodip_first(cc),        # NO-DIP block on top
                                 cc.get("biasRank", 4),
                                 -abs(cc.get("biasImb") or 0),
                                 not cc["crossBuy"],
                                 -(cc.get("tvol") or 0) if sort_vol_first
                                 else (cc.get("crossAge") if cc.get("crossAge") is not None else 999)))
        return out if not PER_LIST else out[:PER_LIST]

    for _sid, _cc in cards.items():
        STICKY[str(_sid)] = {"sym": _cc.get("sym"), "lists": _cc.get("lists"),
                             "tvol": _cc.get("tvol"), "ts": time.time()}

    STATE["im"] = panel_cards("Intraday Movers", True)
    STATE["bv"] = panel_cards("By Volumes", False)
    STATE["pm"] = panel_cards("Price Movers", True)
    # MOVERS (left column) = Intraday Movers + Price Movers, PLUS every stock
    # carrying an ENTRY badge no matter which source list it came from -- an
    # actionable entry must never be hidden in the By-Volume column alone.
    seen_mv, merged_mv = set(), []
    for c in STATE["im"] + STATE["pm"]:
        if c["sym"] in seen_mv:
            continue
        seen_mv.add(c["sym"])
        merged_mv.append(c)
    promoted = 0
    for c in cards.values():
        if c.get("entry") and c.get("sym") not in seen_mv:
            seen_mv.add(c["sym"])
            merged_mv.append(c)
            promoted += 1
    # Same rule as the panels: BIAS TIER decides the block, everything else
    # orders within it. ENTRY and PINNED still float to the top of their own
    # tier, so an actionable entry is never buried -- it just cannot outrank the
    # buy/sell separation.
    merged_mv.sort(key=lambda cc: (*_nodip_first(cc),     # NO-DIP block on top
                                   cc.get("biasRank", 4),
                                   not cc.get("entry"),          # ENTRY badges first in tier
                                   -abs(cc.get("biasImb") or 0),
                                   not cc.get("crossBuy"),
                                   -(cc.get("tvol") or 0)))
    STATE["mv"] = merged_mv
    # ---------------- BOARD TAB: two purpose-built lists ------------------
    # RIGHT "Overall stocks" = Movers + By Volume merged and DE-DUPLICATED.
    #   They overlapped heavily (INDSWFTLAB, FINCABLES and NATIONALUM were each
    #   showing twice because they sat on both panels), so a plain concatenation
    #   would repeat cards.
    seen_ov, overall = set(), []
    for c in merged_mv + (STATE.get("bv") or []):
        if c["sym"] in seen_ov:
            continue
        seen_ov.add(c["sym"])
        overall.append(c)
    overall.sort(key=lambda cc: (*_nodip_first(cc),       # NO-DIP block on top
                                 cc.get("biasRank", 4),
                                 not cc.get("entry"),
                                 -abs(cc.get("biasImb") or 0),
                                 not cc.get("crossBuy"),
                                 -(cc.get("tvol") or 0)))
    STATE["ov"] = overall

    # LEFT "Buy just triggered" = buyers in control OR buy bias, ordered
    # NEAREST cross first.
    #
    #   The scorer's own crossAge is capped by FRESH_WIN = 3, so it can only
    #   ever report 0, 1 or 2 bars and returns None for anything older. Filtering
    #   that to "<= 1 bar" left a single stock on screen. The cross age is
    #   therefore recomputed here directly from the card's EMA-9 / MA-12 series,
    #   which carries the full chart window -- so a cross 8 bars back is still
    #   found, dated, and sorted behind the fresher ones instead of vanishing.
    trig = []
    for c in overall:
        if c.get("biasRank") not in (0, 1):
            continue
        # THE BUY INDICATOR IS ALWAYS ON 30-SECOND BARS.
        # Only if the 30s history is too short do we fall back to the card's own
        # timeframe -- and the row says which was used, so a 60s-resolution
        # reading is never passed off as a 30s one.
        ago, secs = _cross30(c.get("sid"))
        src = "30s"
        if ago is None:
            ago = _cross_bars_ago(c.get("chart") or {})
            if ago is None:
                continue
            secs = ago * (30 if c.get("tf") == "30s" else 60)
            src = c.get("tf") or "1m"
        if ago > BUY_CROSS_MAX_BARS:
            continue
        c = dict(c)
        c["cross_bars"] = ago
        c["cross_sec"] = secs
        c["cross_tf"] = src
        trig.append(c)
    # NEAREST buy first, farthest last -- exactly the order asked for.
    # NO-DIP leads; BELOW it the column still answers "what fired most
    # recently", which is what this half of the board is for.
    trig.sort(key=lambda cc: (*_nodip_first(cc),
                              cc["cross_sec"],
                              cc.get("biasRank", 4),
                              -abs(cc.get("biasImb") or 0),
                              -(cc.get("tvol") or 0)))
    STATE["bt"] = trig
    STATE["ts"] = datetime.now(engine.IST).strftime("%H:%M:%S")
    STATE["build"] = BUILD_ID
    try:
        st = TICKS.status()
        n30 = sum(1 for c in cards.values() if (c.get("tf") or "1m") != "1m")
        STATE["tf"] = {"on30s": n30, "total": len(cards), "polls": st.get("polls"),
                       "need": st.get("min_bars")}
    except Exception:
        pass
    STATE["src"] = "scanx" if not any("API-fallback" in x for x in [""]) else "api"
    log_board()
    log(f"cycle[{time.time()-_t_start:.1f}s]: movers={len(STATE['mv'])} (im={len(STATE['im'])} pm={len(STATE['pm'])} "
        f"+entry={promoted}) bv={len(STATE['bv'])} "
        f"| universe={len(uniq)} eligible={len(cards)} refreshed={len(todo)} "
        f"| volrules={'ON' if vol_rules_on else 'OFF(pre-open/warmup)'} "
        f"filtered: px<{int(MIN_PRICE)}={dropped_px + f_price} "
        f"dayTover<{MIN_DAY_TOVER//10**7}Cr={dropped_vol} "
        f"first2<{MIN_FIRST2_VAL//10**7}Cr={f_first2} "
        f"sessVal<{MIN_ENTRY_VAL//10**5}L={f_entry} atUC={f_uc}")


WANTED = {"rows": [], "ts": 0.0}
# sid -> {sym, lists, tvol, ts} for names seen on the board recently
STICKY = {}
STICKY_SEC = 900           # keep re-injecting for 15 minutes after it drops off
STICKY_MIN_DAY_PCT = 1.0   # ...but only while it is still up on the day


def candle_loop():
    """Refresh card candles continuously, off the cycle's critical path.

    cycle() decides WHICH symbols are stalest and most visible; this decides
    WHEN, at whatever rate the feed allows. Nothing here can delay the board:
    if the feed is slow, cards simply age and the board keeps re-sorting the
    ones it has -- which is the correct failure, because a stale chart with a
    live price is far more useful than a frozen screen.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _safe(nr):
        try:
            return build_card(nr)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            where = f"{tb[-1].name}:{tb[-1].lineno}" if tb else "?"
            log(f"card error {nr.get('sym','?')}: {type(e).__name__}: "
                f"{str(e)[:90]} @{where}")
            return None

    while True:
        try:
            rows = list(WANTED.get("rows") or [])
            if not rows:
                time.sleep(1)
                continue
            with ThreadPoolExecutor(max_workers=CANDLE_WORKERS) as ex:
                list(ex.map(_safe, rows))
            time.sleep(0.5)
        except Exception:
            log("candle_loop: EXCEPTION\n" + traceback.format_exc())
            time.sleep(5)


def loop():
    while True:
        t0 = time.time()
        try:
            cycle()
        except Exception:
            log("cycle EXCEPTION\n" + traceback.format_exc())
        time.sleep(max(2, CYCLE_SEC - (time.time() - t0)))


@app.route("/")
def index():
    resp = Response((HERE / HTML_FILE).read_text(encoding="utf-8"), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/state")
def state():
    return Response(json.dumps(STATE), mimetype="application/json")


# ---- MARKET DEPTH (buyer/seller bias) -----------------------------------
# One batched quote covers every symbol on the board, cached for DEPTH_TTL so a
# fast UI poll can never hammer the API. Same plumbing the :5003 board uses.
DEPTH_TTL = 4.0
DEPTH_MAX = 200
_depth_cache = {"ts": 0.0, "data": {}, "stamp": None}
_depth_lock = threading.Lock()


def refresh_depth(sid_to_sym, force=False):
    """Fill the shared depth cache (one batched quote). Used by BOTH the /depth
    route and the scan cycle, so the board can be SORTED by order-book bias even
    when no browser is polling. Respects DEPTH_TTL so it costs one call per 4s."""
    now = time.time()
    with _depth_lock:
        fresh = (now - _depth_cache["ts"] < DEPTH_TTL) and _depth_cache["data"]
    if fresh and not force:
        return _depth_cache["data"]
    if not sid_to_sym:
        return _depth_cache["data"]
    try:
        raw = quotes_v3.depth_and_totals(list(sid_to_sym.keys())[:DEPTH_MAX],
                                         lambda m: None)
        data = {}
        for sid, d in (raw or {}).items():
            sym = sid_to_sym.get(str(sid))
            if sym:
                data[sym] = d
        if data:
            with _depth_lock:
                _depth_cache.update({"ts": now, "data": data,
                                     "stamp": datetime.now(engine.IST).strftime("%H:%M:%S")})
    except Exception as e:
        log(f"depth refresh error: {e}")
    return _depth_cache["data"]


# Order-book bias tiers -- the board's primary sort order:
#   0 BUYERS IN CONTROL -> 1 Buy bias -> 2 SELLERS IN CONTROL -> 3 Sell bias
#   -> 4 balanced / unknown (last)
BIAS_LABEL = {0: "BUYERS IN CONTROL", 1: "Buy bias", 2: "SELLERS IN CONTROL",
              3: "Sell bias", 4: "balanced"}


def _cvd_up(card):
    """Are executed trades net-buying and still building?

    NOTE: the V3.5 chart payload carries NO volume array (t/o/h/l/c + indicators
    only), so this MUST use the flag computed in build_card from the raw candles.
    Reading ch['v'] here silently returned None for every stock, which made every
    card fall through to 'balanced'.
    """
    return card.get("cvd_up")


def bias_of(sym, card=None):
    """(rank, label, imbalance%) -- the SAME three-factor call the :5003 board makes.

    FLOW (executed trades) and VWAP (where price actually is) decide the verdict.
    The order book is RESTING intent -- the weakest of the three -- so it only
    CONFIRMS or softens; it can never on its own claim "buyers in control".
    (A 6:1 resting bid queue sitting under the market is support, not buying:
    that is why a book-only reading called a flat stock "BUYERS IN CONTROL".)
    """
    d = (_depth_cache.get("data") or {}).get(sym)
    imb = None
    if d:
        tb, ts_ = d.get("tot_buy") or 0, d.get("tot_sell") or 0
        if tb > 0 and ts_ > 0:
            imb = (tb - ts_) / (tb + ts_) * 100.0
    if card is None:
        return 4, BIAS_LABEL[4], imb

    price = card.get("price")
    vwap = card.get("vwap")
    above = (price >= vwap) if (price is not None and vwap) else None
    up = _cvd_up(card)
    if up is None or above is None:
        return 4, BIAS_LABEL[4], imb                    # not enough tape to judge

    book_pos = (imb is not None and imb >= 12)
    book_neg = (imb is not None and imb <= -12)
    real = (1 if up else 0) + (1 if above else 0)       # flow + VWAP agreement (0..2)
    if real == 2:
        return (0, BIAS_LABEL[0], imb) if book_pos else (1, BIAS_LABEL[1], imb)
    if real == 0:
        return (2, BIAS_LABEL[2], imb) if book_neg else (3, BIAS_LABEL[3], imb)
    # flow and VWAP disagree -> the book only tilts it, never "in control"
    if book_pos:
        return 1, BIAS_LABEL[1], imb
    if book_neg:
        return 3, BIAS_LABEL[3], imb
    return 4, BIAS_LABEL[4], imb


@app.route("/depth")
def depth():
    try:
        sid_to_sym = {}
        for k in ("mv", "bv"):
            for c in STATE.get(k) or []:
                if c.get("sid") is not None:
                    sid_to_sym[str(c["sid"])] = c.get("sym")
        data = refresh_depth(sid_to_sym)
        return Response(json.dumps({"ok": True, "ts": _depth_cache.get("stamp"),
                                    "data": data}), mimetype="application/json")
    except Exception as e:
        log(f"/depth error: {e}")
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "data": {}}),
                        mimetype="application/json")


# ---- PRE-MARKET (09:00-09:15 call auction) -------------------------------
# Surfaces stocks whose indicative price gaps hard AND whose order book is
# lopsided, so a trade can be set up before the 09:15 open. Runs only inside the
# window; fully isolated from the live board.
PREOPEN_STATE = {"rows": [], "ts": None, "window": False, "note": None}
PREOPEN_DIR = HERE / "logs" / "movers_board"


# The pre-open tab's own calls, KEPT FOR THE SESSION. PREOPEN_STATE is a live
# snapshot that goes stale at 09:15; Super Stocks needs the names at 09:16, 09:40
# and 10:20, so the STRONG GAP-UP list is latched here the moment it is seen and
# held until the next trading day.
PREOPEN_CALLS = {"day": None, "syms": {}}


def preopen_calls():
    return set(PREOPEN_CALLS["syms"])


def preopen_loop():
    logged_day = {"d": None}
    while True:
        try:
            now = datetime.now(engine.IST)
            if Opus_preopen.in_window(now):
                day = now.strftime("%Y%m%d")
                if logged_day["d"] != day:          # fresh session -> reset the tracker
                    Opus_preopen.reset_pump()
                    logged_day["d"] = day
                rows = Opus_preopen.scan_both({}, log, 60)
                if PREOPEN_CALLS["day"] != day:
                    PREOPEN_CALLS.update({"day": day, "syms": {}})
                for _r in rows or []:
                    if (_r.get("side") == "UP"
                            and _r.get("verdict") == "STRONG GAP-UP"
                            and _r.get("sym")):
                        PREOPEN_CALLS["syms"].setdefault(
                            str(_r["sym"]).upper(), now.strftime("%H:%M:%S"))
                PREOPEN_STATE.update({
                    "rows": rows, "ts": now.strftime("%H:%M:%S"), "window": True,
                    "note": ("order entry closes 09:08 · matching 09:08-09:12"
                             if now.strftime("%H:%M") < "09:08"
                             else "order entry closed · price discovery in progress")})
                try:
                    PREOPEN_DIR.mkdir(parents=True, exist_ok=True)
                    with (PREOPEN_DIR / f"preopen_{day}.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                                            "rows": rows}, default=str) + "\n")
                except Exception:
                    pass
                log(f"preopen: {len(rows)} gap candidates "
                    f"(up={sum(1 for r in rows if r['side']=='UP')} "
                    f"down={sum(1 for r in rows if r['side']=='DOWN')})")
                time.sleep(15)
            else:
                if PREOPEN_STATE["window"]:
                    PREOPEN_STATE.update({"window": False,
                                          "note": "pre-open closed — live board is active"})
                time.sleep(20)
        except Exception:
            log("preopen_loop: EXCEPTION\n" + traceback.format_exc())
            time.sleep(20)


@app.route("/preopen")
def preopen():
    return Response(json.dumps(PREOPEN_STATE, default=str), mimetype="application/json")


PREMKT_STATE = {"rows": [], "ts": None, "phase": None, "track": None}


def premarket_loop():
    """Rank overnight news by severity BEFORE the open, enrich it with the
    pre-open auction from 09:00, and score yesterday's calls once the session
    has run (Phase 2 forward validation)."""
    scored_day = {"d": None}
    _last_outcome_score = {"t": 0.0}     # throttle the 5-minute full-session re-score
    while True:
        try:
            if premkt is None or dhannews is None:
                time.sleep(60)
                continue
            now = datetime.now(engine.IST)
            hhmm = now.strftime("%H:%M")
            items, _ = dhannews.fetch(log=log)
            hot, _e = dhannews.curated(drop_neutral=True)
            pre_rows = PREOPEN_STATE.get("rows") or []
            rows = premkt.build(hot, pre_rows, log=log)
            phase = ("pre-open auction (09:00-09:15) confirming" if pre_rows
                     else ("before the open — text-inferred severity"
                           if hhmm < "09:15" else "session live"))
            PREMKT_STATE.update({"rows": rows, "ts": now.strftime("%H:%M:%S"),
                                 "phase": phase, "track": premkt.track_record()})
            if hhmm < "09:15":
                premkt.record_calls(rows)                 # freeze today's calls
            if hhmm >= "09:22" and scored_day["d"] != now.strftime("%Y%m%d"):
                # measure over the SCALPING horizon (open +2min / +5min)
                premkt.score_scalp_outcomes(log=log)
                scored_day["d"] = now.strftime("%Y%m%d")
                PREMKT_STATE["track"] = premkt.track_record()
            # ---- FULL-SESSION OUTCOME, for the Premarket Predictor tab ------
            # score_outcomes() existed but was never called, so `actual_pct` was
            # absent on all 360 recorded calls and the prediction-vs-reality
            # half of the exercise never happened. Re-run through the session so
            # the tab is live, and once more after the close for the final mark.
            if "09:20" <= hhmm <= "15:40" and now.weekday() < 5:
                if time.time() - _last_outcome_score["t"] > 300:
                    _last_outcome_score["t"] = time.time()
                    try:
                        premkt.score_outcomes(log=log)
                    except Exception as e:
                        log(f"premarket: score_outcomes {type(e).__name__} {str(e)[:90]}")
            time.sleep(60 if hhmm < "09:15" else 300)
        except Exception:
            log("premarket_loop: EXCEPTION\n" + traceback.format_exc())
            time.sleep(120)


@app.route("/premarket")
def premarket():
    return Response(json.dumps(PREMKT_STATE, default=str), mimetype="application/json")


@app.route("/predictor")
def predictor_feed():
    """PREMARKET PREDICTOR -- the 09:00 call against what actually happened.

    Reads the frozen calls file for a session (?day=YYYYMMDD, default today) and
    returns each row with its reality check, the gap, and the suggested fix.
    Scoring itself is done by the premarket loop; this endpoint only reads, so
    it is safe to poll.
    """
    if premkt is None:
        return Response(json.dumps({"ok": False, "err": "Movers_premarket not loaded",
                                    "rows": []}), mimetype="application/json")
    try:
        day = (request.args.get("day") or "").strip() or None
        # Pass the LIVE premarket rows so that today renders even before the
        # 09:15 freeze writes a calls file.
        d = premkt.predictor(day, live_rows=(PREMKT_STATE.get("rows") or []))
        d["days"] = premkt.predictor_days()
        d["today"] = datetime.now(engine.IST).strftime("%Y%m%d")
        d["ok"] = True
        return Response(json.dumps(d, default=str), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


@app.route("/dhannews")
def dhan_news():
    """Dhan's own news from the previous session close (15:30) to now -- the
    window that explains today's gaps. Headlines for stocks currently on the
    board are flagged so they can be surfaced first."""
    if dhannews is None:
        return Response(json.dumps({"ok": False, "err": "module not loaded", "items": []}),
                        mimetype="application/json")
    try:
        dhannews.fetch(log=log)                 # refresh (cached 3 min)
        on_board, board_sids = set(), set()
        for k in ("mv", "bv"):
            for c in STATE.get(k) or []:
                s = (c.get("sym") or "").upper()
                if s:
                    on_board.add(s)
                if c.get("sid") is not None:
                    board_sids.add(str(c["sid"]))
        # ONE row per stock, neutral filings dropped -- only high-impact news
        out, err = dhannews.curated(on_board=on_board, board_sids=board_sids,
                                    drop_neutral=True)
        s = dhannews.summary()
        s["shown"] = len(out)
        s["high"] = sum(1 for x in out if x.get("tier") == 0)
        s["med"] = sum(1 for x in out if x.get("tier") == 1)
        s["tradeable"] = sum(1 for x in out if x.get("tradeable"))
        s["thin"] = sum(1 for x in out if x.get("tradeable") is False)
        s["live"] = dhannews._market_live()
        return Response(json.dumps({"ok": True, "summary": s, "items": out},
                                   default=str), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "items": []}),
                        mimetype="application/json")


def _board_syms():
    seen, out = set(), []
    for k in ("mv", "bv", "im", "pm"):
        for c in STATE.get(k) or []:
            if c["sym"] not in seen:
                seen.add(c["sym"])
                out.append(c["sym"])
    return out


@app.route("/dhannews_raw")
def dhannews_raw():
    """DIAGNOSTIC: the Dhan news stream BEFORE any curation.

    Exists to answer one question honestly -- when a stock is missing from the
    SINCE LAST CLOSE tab, was it filtered out by our rules, or did Dhan never
    carry it? Those two have completely different fixes (loosen the filter vs.
    add a second source) and guessing between them is how you end up 'fixing'
    the wrong one.

    Read-only, no side effects, not linked from the UI.
    """
    if dhannews is None:
        return Response(json.dumps({"ok": False, "err": "module not loaded", "items": []}),
                        mimetype="application/json")
    try:
        items, err = dhannews.fetch(force=False, log=log)
        want = (request.args.get("q") or "").upper().strip()
        rows = []
        for it in items:
            blob = " ".join(str(it.get(k) or "") for k in
                            ("sym", "name", "label", "title")).upper()
            if want and want not in blob:
                continue
            rows.append({"sym": it.get("sym"), "name": it.get("name"),
                         "when": it.get("when"), "sentiment": it.get("sentiment"),
                         "day_pct": it.get("day_pct"),
                         "title": (it.get("title") or "")[:150]})
        return Response(json.dumps({"ok": True, "raw_total": len(items),
                                    "matched": len(rows), "q": want,
                                    "err": err, "items": rows[:200]}, default=str),
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "items": []}),
                        mimetype="application/json")


@app.route("/alarm")
def alarm_feed():
    """1-MIN ALARM. Whatever is igniting right now, across the WHOLE EQ universe.

    Deliberately independent of the board's universe: the whole point is that
    Motherson was invisible to every list at 09:15 and still moved +5% in two
    minutes. Gating this on the same three scanx lists would reproduce the exact
    blind spot it exists to fix."""
    if alarm is None:
        return Response(json.dumps({"ok": False, "err": "alarm module not loaded", "rows": []}),
                        mimetype="application/json")
    try:
        rows, err = alarm.compute(log)
        s = alarm.summary()
        return Response(json.dumps({"ok": True, "summary": s, "rows": rows, "err": err},
                                   default=str), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


@app.route("/scanner1")
def scanner1_feed():
    """Scanner1 (RLB). Cards built with the SAME build_card() the board uses, so
    the card markup, chart, badges and depth strip are identical -- the only
    difference is WHICH stocks are in the list."""
    if scanner1 is None:
        return Response(json.dumps({"ok": False, "err": "Movers_scanner1 not loaded",
                                    "rows": []}), mimetype="application/json")
    try:
        hits = scanner1.rows()
        cards = []
        for hrow in hits:
            try:
                card = build_card({"sym": hrow["sym"], "sid": str(hrow["sid"]),
                                   "lists": ["Scanner1 · UC"],
                                   "tvol": hrow.get("tover_cr"),
                                   "day_pct": hrow.get("day_pct")})
            except Exception:
                card = None
            if not card:
                continue
            # carry the seven conditions onto the card so the tab can show WHY
            card["uc"] = {k: hrow.get(k) for k in
                          ("day_pct", "from_open_pct", "volume", "tover_cr",
                           "day_lo", "day_hi", "prev_close", "uc", "uc_room_pct",
                           "at_uc", "range_pos",
                           # live-momentum block -- the UC filters pass ~100 stocks
                           # and day % cannot rank them, so these do
                           "momentum", "stale", "mom_win", "thrust_pct", "money_cr",
                           "money_x", "win_sec",
                           "m_thrust", "m_money", "m_range", "m_open", "m_liq")}
            card["day_pct"] = hrow.get("day_pct", card.get("day_pct"))
            # ---- BUY ENTRY, for the left half of the tab ------------------
            # Same definition already agreed for the board's left column:
            # buyers in control OR buy bias, PLUS an MA x EMA buy cross that is
            # still in force. Computed on the 30-SECOND series where enough bars
            # exist, else the card's own timeframe -- and the row records which,
            # so a 60s reading is never passed off as a 30s one.
            ago, secs = _cross30(card.get("sid"))
            src = "30s"
            if ago is None:
                ago = _cross_bars_ago(card.get("chart") or {})
                if ago is not None:
                    secs = ago * (30 if card.get("tf") == "30s" else 60)
                    src = card.get("tf") or "1m"
            # BIAS IS NOT PART OF THIS.
            #   It used to be `biasRank in (0,1) AND a cross`, carried over from
            #   the board's left column where filtering was the point. Here it
            #   silently nullified the ordering: APEX had a cross 2 minutes old
            #   but bias "BOOK ?", so it scored as no-cross, every row tied, and
            #   the left column collapsed into the same order as the right.
            #   The column is "what crossed most recently" -- bias is still shown
            #   on the card, it just does not decide the sort.
            buy = bool(ago is not None and ago <= BUY_CROSS_MAX_BARS)
            card["buy_trig"] = buy
            card["cross_sec"] = secs if ago is not None else None
            card["cross_bars"] = ago
            card["cross_tf"] = src if ago is not None else None
            cards.append(card)
        TICKS.add([str(c["sid"]) for c in cards],
                  {str(c["sid"]): c["sym"] for c in cards})
        # NO-DIP LIFT.
        #   scanner1.scan() cannot do this itself -- `pinned` is computed from
        #   the candle history inside build_card(), which only runs here. So the
        #   scanner orders by momentum, and the pinned block is lifted to the top
        #   afterwards. Momentum on these cards is refreshed by the board cycle's
        #   _stamp_momentum(); the scanner row carries its own copy, so prefer
        #   whichever is present.
        for c in cards:
            if not c.get("momentum"):
                c["momentum"] = (c.get("uc") or {}).get("momentum") or 0
            if c.get("stale") is None:
                c["stale"] = bool((c.get("uc") or {}).get("stale"))
        cards.sort(key=lambda cc: (*_nodip_first(cc), -(cc.get("momentum") or 0)))
        s = scanner1.summary()
        s["cards"] = len(cards)
        s["buy_cards"] = sum(1 for c in cards if c.get("buy_trig"))
        s["pinned"] = sum(1 for c in cards if c.get("pinned"))
        return Response(json.dumps({"ok": True, "summary": s, "rows": cards},
                                   default=str), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


@app.route("/watchlist")
def watchlist_feed():
    if watchlist is None:
        return Response(json.dumps({"ok": False, "err": "Movers_watchlist not loaded",
                                    "rows": []}), mimetype="application/json")
    try:
        return Response(json.dumps({"ok": True, "summary": watchlist.summary(),
                                    "rows": watchlist.rows()}, default=str),
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


def audit_loop():
    """Regenerate the badge-vs-actual-surge table every 10 minutes.

    Runs unattended so the comparison always exists without anyone asking for
    it. Writes logs/movers_board/badge_audit_YYYYMMDD.json.
    """
    while True:
        try:
            if alarm is not None and alarm.market_live():
                import audit_badges
                audit_badges.main()
        except Exception as e:
            log(f"audit: {type(e).__name__} {str(e)[:110]}")
        time.sleep(600)


def watchlist_loop():
    if watchlist is None:
        return
    try:
        watchlist.loop(log)
    except Exception as e:
        log(f"watchlist_loop died: {type(e).__name__} {str(e)[:140]}")


def scanner1_loop():
    if scanner1 is None:
        return
    try:
        scanner1.loop(log)
    except Exception as e:
        log(f"scanner1_loop died: {type(e).__name__} {str(e)[:140]}")


def alarm_loop():
    if alarm is None:
        return
    try:
        alarm.loop(log)
    except Exception as e:
        log(f"alarm_loop died: {type(e).__name__} {str(e)[:140]}")


SUPER = {"rows": [], "day": None}


def superstocks_loop():
    """SUPER STOCKS -- scan the WHOLE market, then card only what qualifies.

    Deliberately independent of the ScanX lists the Board is built from. Those
    lists are why WELCORP, MOSCHIP, VINCOFE and BOMDYEING ran 4-5% on 26-Aug
    without ever becoming a card.

    The scan itself is arithmetic over a snapshot already in memory, so it costs
    no Dhan request. Only the qualifying handful are then enriched into full
    cards, which is the only part that touches the API at all.
    """
    if superstocks is None or alarm is None:
        return
    while True:
        try:
            now = datetime.now(engine.IST)
            if now.weekday() >= 5:
                time.sleep(60)
                continue
            hhmmss = now.strftime("%H:%M:%S")
            if not ("09:15:00" <= hhmmss <= "15:30:00"):
                # SAY SO. Outside the session this loop used to sleep without
                # touching STATE, so the tab sat blank with no explanation --
                # identical on screen to the two days it was genuinely broken.
                # An empty tab that cannot tell you WHY is the single failure
                # this project has repeated most often.
                STATE.setdefault("super", [])
                STATE["super_meta"] = {
                    "why_empty": ("market closed - Super Stocks scans the whole "
                                  "universe between 09:15 and 15:30. It arms "
                                  "itself at 09:15; nothing to do until then."),
                    "armed": False, "session": now.strftime("%d-%b %H:%M")}
                time.sleep(20)
                continue
            day = now.strftime("%Y%m%d")
            if SUPER["day"] != day:
                superstocks.reset_for_day()
                if super_monitor is not None:
                    super_monitor.reset_for_day()
                SUPER["day"] = day
                log("superstocks: armed -- scanning the whole universe")
            # Board first, universe second -- his instruction. The board's own
            # sids are handed in so the tab can lead with what he is already
            # watching and use the sweep to fill the gaps.
            _bsids = set()
            for _k in ("ov", "bt", "mv", "bv"):
                for _c in (STATE.get(_k) or []):
                    if _c.get("sid") is not None:
                        _bsids.add(str(_c["sid"]))
            # THE TAB HE TRADES IS NOT DRIVEN BY A BACK-TEST INPUT.
            # This used to read SUPER["position_rs"], which the Paper Trading
            # route set from whatever capital he typed -- so a number entered in
            # a back-test silently changed which stocks appeared here. Frozen at
            # SUPER_POSITION_RS; the LAB copy is where sizing varies.
            try:
                superstocks.set_position_size(SUPER_POSITION_RS)
            except Exception:
                pass
            hits = superstocks.scan(alarm, hhmmss, log, board_sids=_bsids,
                                    preopen_syms=preopen_calls())

            # ---- THE LAB, scanned in the same pass off the same sweep --------
            # Costs one more arithmetic walk over a dict already in memory and
            # no request at all. Its rows feed the paper-trading engines, so
            # trading logic can be changed without touching his board.
            if superstocks_lab is not None:
                try:
                    superstocks_lab.set_position_size(
                        SUPER.get("position_rs") or SUPER_POSITION_RS)
                    lab_hits = superstocks_lab.scan(
                        alarm, hhmmss, lambda _m: None, board_sids=_bsids,
                        preopen_syms=preopen_calls())
                    lab_cards = []
                    _enrich_deadline = time.time() + SUPER_ENRICH_BUDGET
                    _enrich_cut = 0
                    for h in lab_hits:
                        if time.time() > _enrich_deadline:
                            _enrich_cut += 1
                            continue
                        try:
                            c = build_card({"sym": h["sym"], "sid": h["sid"],
                                            "lists": ["Super Lab"],
                                            "day_pct": h.get("day_pct"),
                                            "from_open": h.get("from_open")})
                            if c:
                                c.update({k: h.get(k) for k in
                                          ("from_open", "rise_90s", "rel_vol", "urgency",
                                           "first_seen", "hot", "off_peak", "held_s",
                                           "state", "watch_since", "watch_px",
                                           "watch_why", "src", "preopen",
                                           "liq_rs_min", "liq_sh_min", "participation")})
                                lab_cards.append(c)
                        except Exception:
                            continue
                    if lab_cards:
                        _stamp_momentum(lab_cards)
                    STATE["superlab"] = lab_cards
                    STATE["superlab_meta"] = superstocks_lab.summary()
                except Exception as _e:
                    log(f"superlab: {type(_e).__name__} {str(_e)[:110]}")
            # Build real cards for the qualifiers, using the board's own scorer
            # so a Super Stocks card is identical to a Board card.
            cards = []
            _enrich_deadline = time.time() + SUPER_ENRICH_BUDGET
            _enrich_cut = 0
            for h in hits:
                if time.time() > _enrich_deadline:
                    _enrich_cut += 1
                    continue
                try:
                    c = build_card({"sym": h["sym"], "sid": h["sid"],
                                    "lists": ["Super Stocks"],
                                    "day_pct": h.get("day_pct"),
                                    "from_open": h.get("from_open")})
                    if c:
                        c.update({k: h.get(k) for k in
                                  ("from_open", "rise_90s", "rel_vol", "urgency",
                                   "first_seen", "hot", "off_peak", "held_s",
                                   "state", "watch_since", "watch_px",
                                   "watch_why", "src", "preopen")})
                        if c.get("preopen"):
                            c["preopen_at"] = PREOPEN_CALLS["syms"].get(
                                str(c.get("sym", "")).upper())
                        cards.append(c)
                except Exception:
                    continue
            if cards:
                _stamp_momentum(cards)
            if _enrich_cut:
                # Visible, not silent. A board quietly showing fewer stocks than
                # it found is the same failure mode as an empty tab that cannot
                # say why -- the one this project has repeated most often.
                log(f"superstocks: enrichment budget spent, {_enrich_cut} "
                    f"qualifier(s) not carded this pass (Dhan slow?)")
            STATE["super"] = cards
            STATE["super_meta"] = superstocks.summary()
            if super_monitor is not None:
                STATE["super_health"] = super_monitor.health()
            SUPER["rows"] = cards
            time.sleep(10)
        except Exception:
            log("superstocks_loop: EXCEPTION\n" + traceback.format_exc())
            time.sleep(30)


# ---- SCANX MOMENTUM BLAST -----------------------------------------------
# Dhan's published screener (intraday-momentum-blast-imb-411739), reproduced.
# Deliberately isolated: its own thread, every failure caught, its own STATE
# key and its own route. Nothing here can slow or break the Board, Super Stocks
# or any other tab -- that was the stated condition for building it.
def scanxblast_loop():
    if scanx_blast is None or alarm is None:
        return
    warmed = {"day": None}
    while True:
        try:
            now = datetime.now(engine.IST)
            if now.weekday() >= 5:
                time.sleep(120)
                continue
            hhmmss = now.strftime("%H:%M:%S")
            day = now.strftime("%Y%m%d")

            # ---- daily warm-up, once a day, well before he needs the board.
            # It reads a disk cache first, so a restart costs nothing.
            if warmed["day"] != day and hhmmss >= scanx_blast.WARM_START_HHMM:
                try:
                    n = scanx_blast.warm_daily(alarm, engine, log)
                    log(f"scanx blast: daily values ready for {n} stocks")
                except Exception as e:
                    log(f"scanx blast: warm_daily {type(e).__name__} {str(e)[:110]}")
                warmed["day"] = day
                try:
                    scanx_blast.reset_for_day()
                except Exception:
                    pass

            if not ("09:15:00" <= hhmmss <= "15:30:00"):
                # Same rule as Super Stocks: an empty tab must say WHY.
                STATE.setdefault("blast", [])
                STATE["blast_meta"] = {
                    "why_empty": ("market closed - ScanX Blast runs 09:15 to 15:30. "
                                  "Daily RSI, MACD and Supertrend are built from "
                                  f"{scanx_blast.WARM_START_HHMM[:5]}."),
                    "warm": len(getattr(scanx_blast, "_daily", {}) or {})}
                time.sleep(20)
                continue

            hits = scanx_blast.scan(alarm, hhmmss, log)
            cards = []
            for h in hits:
                try:
                    c = build_card({"sym": h["sym"], "sid": str(h["sid"]),
                                    "lists": ["ScanX Blast"],
                                    "day_pct": h.get("day_pct")})
                    if c:
                        c["imb"] = {k: h.get(k) for k in
                                    ("rsi", "macd_h", "st", "above_st", "vol_x",
                                     "tover_cr", "sh_min", "from_open", "fresh",
                                     "first_seen")}
                        c["day_pct"] = h.get("day_pct", c.get("day_pct"))
                        cards.append(c)
                except Exception:
                    continue
            if cards:
                _stamp_momentum(cards)
            cards.sort(key=lambda cc: (*_nodip_first(cc),
                                       -((cc.get("imb") or {}).get("fresh") or 0)))
            STATE["blast"] = cards
            STATE["blast_meta"] = scanx_blast.summary()
            time.sleep(scanx_blast.REFRESH_SEC)
        except Exception:
            log("scanxblast_loop: EXCEPTION\n" + traceback.format_exc())
            time.sleep(60)


# ==========================================================================
#  SECTORS -- what the News tab's WHY THEY'RE MOVING sub-tab became
# ==========================================================================
# The old sub-tab asked "why is this stock moving?" and answered "no news" for
# 97 movers in 100, because Sri's movers are small-caps nobody writes about.
# This asks a question that HAS an answer every morning: is the stock moving
# alone, or is its whole sector moving with it -- and is that still going?
#
# COST: zero Dhan requests. Breadth reads the alarm's existing full-market
# sweep. Headlines come from Google News RSS, a different host entirely, 23
# requests every 5 minutes. Nothing here can produce a Dhan rate warning.
SECTOR_EVERY = 15.0        # recompute breadth this often (matches _hist cadence)
NEWS_EVERY = 300.0         # refresh headlines this often


def sector_loop():
    if sectors is None or alarm is None:
        return
    mapped = {"day": None}
    last_news = {"t": 0.0}
    while True:
        try:
            now = datetime.now(engine.IST)
            day = now.strftime("%Y%m%d")
            if mapped["day"] != day:
                try:
                    n = sectors.refresh(log=log)
                    log(f"sectors: map ready, {n} stocks")
                    mapped["day"] = day
                except Exception as e:
                    log(f"sectors: refresh {type(e).__name__} {str(e)[:110]}")

            # HEADLINES RUN IN THEIR OWN THREAD, NOT IN THIS ONE.
            #     Fetching 23 feeds takes ~45 seconds, and it used to run BEFORE
            #     breadth in this loop -- so /sectors returned an empty table for
            #     the first 45 seconds after every restart, which looks exactly
            #     like a broken tab. Breadth is 2.4 ms of arithmetic over a
            #     snapshot already in memory; it must never wait on the network.
            if sectornews is not None and (time.time() - last_news["t"]) >= NEWS_EVERY:
                last_news["t"] = time.time()

                def _news():
                    try:
                        uni = alarm.universe() or {}
                        sectornews.fetch(symbols=set(uni.values()), log=log)
                    except Exception as e:
                        log(f"sectornews: {type(e).__name__} {str(e)[:110]}")
                threading.Thread(target=_news, daemon=True).start()

            news_items = []
            if sectornews is not None:
                by_sec, market = sectornews.cached()
                for sec, items in by_sec.items():
                    for it in items:
                        news_items.append(dict(it, sector=sec))
                STATE["sector_market"] = market

            rows, meta = sectors.breadth(alarm, news_items=news_items, log=log)
            if sectornews is not None:
                meta.update(sectornews.summary())
            STATE["sectors"] = rows
            STATE["sector_meta"] = meta
        except Exception as e:
            log(f"sector_loop: {type(e).__name__} {str(e)[:130]}")
        time.sleep(SECTOR_EVERY)


@app.route("/masterinfo")
def masterinfo():
    """Is the scrip master current? A stale master is a SILENT fault -- nothing
    errors, whole classes of stock simply never appear -- so it gets a route."""
    if master is None:
        return Response(json.dumps({"ok": False, "err": "Movers_master not loaded"}),
                        mimetype="application/json")
    return Response(json.dumps({"ok": True, **master.summary()}, default=str),
                    mimetype="application/json")


# ==========================================================================
#  AUTONOMY -- so nobody has to press anything   (Sri, 02-Sep)
# ==========================================================================
# "I can't keep restarting and monitoring what you do." Fair. Three pieces:
#   /admin/restart   the process re-execs itself, so a code change can be
#                    applied without a human at the keyboard
#   /admin/reload    hot-applies live_paper / superstocks_lab constants from
#                    live_config.json WITHOUT a restart -- most tuning needs
#                    only this, and it never interrupts an open book
#   AUTOSTART        live paper starts itself when the board boots inside
#                    trading hours, so a restart resumes trading by itself
#
# The restart is deliberately a GET with a token in the path rather than a bare
# route: this board listens on 127.0.0.1 only, but a route that re-execs the
# process should still not be reachable by an accidental click.
ADMIN_TOKEN = "scalp-restart"


def _apply_config(d, log=lambda m: None):
    """Push a {module: {CONST: value}} dict onto the live modules."""
    mods = {"live_paper": live_paper, "superstocks_lab": superstocks_lab,
            "paper_engine": paper_engine, "superstocks": superstocks}
    done = []
    for mname, kv in (d or {}).items():
        m = mods.get(mname)
        if m is None or not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            # only overwrite names that already exist -- never invent constants
            if hasattr(m, k):
                setattr(m, k, v)
                done.append(f"{mname}.{k}={v}")
    if done:
        log("config: " + ", ".join(done))
    return done


@app.route("/admin/reload")
def admin_reload():
    """Hot-apply live_config.json. No restart, no interruption to open trades."""
    f = HERE / "live_config.json" if "HERE" in globals() else Path("live_config.json")
    try:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}),
                        mimetype="application/json")
    done = _apply_config(d, log)
    return Response(json.dumps({"ok": True, "applied": done}), mimetype="application/json")


@app.route("/admin/engine/<token>")
def admin_engine(token):
    """Start a fresh paper_live.py, detached.

    The supervisor's job channel runs a script and WAITS for it, which is right
    for a five-second analysis script and wrong for an engine that runs till
    15:31 -- asking it to launch the engine froze the supervisor for the whole
    session. This launches it detached instead. paper_live's own single-instance
    check retires whatever was running, so pressing this twice is harmless.
    """
    if token != ADMIN_TOKEN:
        return Response(json.dumps({"ok": False, "err": "bad token"}),
                        mimetype="application/json"), 403
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        out = open(os.path.join(here, "logs", "paper_live_stdout.log"), "a",
                   encoding="utf-8", errors="replace")
        pr = subprocess.Popen([sys.executable, os.path.join(here, "paper_live.py")],
                              cwd=here, creationflags=flags,
                              stdout=out, stderr=subprocess.STDOUT)
        log(f"admin: launched paper_live.py detached, PID {pr.pid}")
        return Response(json.dumps({"ok": True, "pid": pr.pid}),
                        mimetype="application/json")
    except Exception as e:
        log(f"admin: could not launch paper_live -- {type(e).__name__} {e}")
        return Response(json.dumps({"ok": False, "err": f"{type(e).__name__}: {e}"}),
                        mimetype="application/json")


@app.route("/admin/restart/<token>")
def admin_restart(token):
    if token != ADMIN_TOKEN:
        return Response(json.dumps({"ok": False, "err": "bad token"}),
                        mimetype="application/json"), 403
    log("=" * 50)
    log("ADMIN RESTART requested -- re-exec'ing this process")

    def _go():
        time.sleep(1.0)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            log(f"restart failed: {type(e).__name__} {e}")
    threading.Thread(target=_go, daemon=True).start()
    return Response(json.dumps({"ok": True, "restarting_in_sec": 1}),
                    mimetype="application/json")


# ==========================================================================
#  AUTONOMY -- so nobody has to press anything   (Sri, 02-Sep)
# ==========================================================================
# "I can't keep restarting and monitoring what you do." Fair. Three pieces:
#   /admin/restart   the process re-execs itself, so a code change can be
#                    applied without a human at the keyboard
#   /admin/reload    hot-applies live_paper / superstocks_lab constants from
#                    live_config.json WITHOUT a restart -- most tuning needs
#                    only this, and it never interrupts an open book
#   AUTOSTART        live paper starts itself when the board boots inside
#                    trading hours, so a restart resumes trading by itself
#
# The restart is deliberately a GET with a token in the path rather than a bare
# route: this board listens on 127.0.0.1 only, but a route that re-execs the
# process should still not be reachable by an accidental click.
ADMIN_TOKEN = "scalp-restart"


@app.route("/tiers")
def tiers():
    """CAPITAL TIERS tab -- read-only view of logs/paper_tiers_<day>.json.

    Written by paper_tiers.py, a SEPARATE process that makes no Dhan API calls
    and never writes anything the Rs 1,00,000 tab owns. This route only reads a
    file; it cannot affect /livepaper.
    """
    from datetime import datetime as _dt
    day = _dt.now().strftime("%Y%m%d")
    cap = (request.args.get("cap") or "").strip()
    if cap.isdigit():
        # one tier's FULL snapshot, same shape as /livepaper
        f1 = HERE / "logs" / ("paper_tier_%s_%s.json" % (cap, day))
        if not f1.exists():
            return Response(json.dumps({"ok": False,
                "err": "no snapshot yet for capital %s" % cap}),
                mimetype="application/json")
        return Response(f1.read_text(encoding="utf-8"), mimetype="application/json")
    f = HERE / "logs" / ("paper_tiers_%s.json" % day)
    try:
        if not f.exists():
            return Response(json.dumps({"ok": False, "tiers": [],
                "err": "paper_tiers.py has not written a snapshot yet today."}),
                mimetype="application/json")
        return Response(f.read_text(encoding="utf-8"), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "tiers": [],
            "err": "%s %s" % (type(e).__name__, e)}), mimetype="application/json")


@app.route("/livepaper")
def livepaper():
    """Live paper trading -- owned entirely by paper_live.py.

    start / stop / reset write logs/paper_control.json; paper_live.py watches
    that file and trades nothing until Sri presses Start. The Board's own
    live_paper engine and the Super Stocks tab are NEVER touched from here, so
    nothing on this tab can change what those two show.

    EVERY return path goes through paper_live's own snapshot builder. The
    Board's JavaScript reads s.skipped.crowded_out with no guard on s.skipped,
    so a hand-written "nothing yet" payload that drops one key takes the tab
    down with a TypeError -- which is exactly what happened on the first press.
    """
    act = (request.args.get("action") or "").lower()

    def _json(obj):
        return Response(json.dumps(obj, default=str), mimetype="application/json")

    try:
        import paper_live as _pl
    except Exception as _e:
        log(f"livepaper: paper_live unavailable ({type(_e).__name__} {_e}) -- "
            f"falling back to the board engine")
        if live_paper is None:
            return _json({"ok": False, "err": f"paper_live not loaded: {_e}"})
        return _json(live_paper.snapshot())

    try:
        now = datetime.now().strftime("%H:%M:%S")
        day = datetime.now().strftime("%Y%m%d")
        st = _pl.control()

        if act in ("start", "stop", "reset"):
            if act == "start":
                cap = float(request.args.get("capital") or 1_00_000)
                lev = float(request.args.get("leverage") or 5)
                tgt = float(request.args.get("target") or 10)
                if not (1_000 <= cap <= 10_00_00_000):
                    return _json({"ok": False,
                                  "err": "capital must be Rs 1,000 to Rs 10 crore"})
                st = {"active": True, "started": now, "stopped": None,
                      "reset_at": st.get("reset_at"),
                      "capital": cap, "leverage": lev, "target": tgt}
                log(f"paper_live: STARTED at {now} -- Rs {cap:,.0f} x {lev:g} "
                    f"= Rs {cap * lev:,.0f} in one position, target {tgt:g}%")
            elif act == "stop":
                st["active"] = False
                st["stopped"] = now
                log(f"paper_live: STOPPED at {now}")
            else:                                    # reset / clear the table
                st = {"active": False, "started": None, "stopped": None,
                      "reset_at": now}
                try:
                    Path(str(_pl.SNAP).format(day)).unlink()
                except OSError:
                    pass
                log(f"paper_live: TABLE CLEARED at {now}")
            _pl.CTRL.parent.mkdir(parents=True, exist_ok=True)
            _pl.CTRL.write_text(json.dumps(st), encoding="utf-8")

        snap = None if act == "reset" else _pl.board_snapshot(day)
        if snap and isinstance(snap.get("summary"), dict):
            # The file on disk was written by the engine's last cycle, up to
            # 20 seconds ago. The control file is what Sri just pressed, so it
            # wins -- otherwise the buttons take a cycle to flip.
            snap["summary"]["active"] = bool(st.get("active"))
            snap["summary"]["started"] = st.get("started")
            snap["summary"]["stopped"] = None if st.get("active") else st.get("stopped")
            snap["summary"].setdefault("skipped", {})
            return _json(snap)

        note = None
        if st.get("active") and act != "start":
            note = ("running, but paper_live.py has not written a snapshot in the "
                    "last 3 minutes -- check logs/paper_live_%s.log" % day)
        return _json(_pl.idle_snapshot(st, note, day))

    except Exception as e:
        log(f"livepaper: {type(e).__name__} {str(e)[:140]}")
        return _json({"ok": False,
                      "err": f"{type(e).__name__}: {str(e)[:160]}"})


@app.route("/papertrade")
def papertrade():
    """Back-test today's own signals at whatever capital he types in.

    Runs on demand -- there is no loop and no cache beyond paper_engine's own
    30-second file cache, because he changes the inputs and expects the numbers
    to change. It reads two log files off disk and does arithmetic; it makes no
    request to Dhan, so hammering the button cannot produce a rate warning.
    """
    if paper_engine is None:
        return Response(json.dumps({"ok": False, "err": "paper_engine not loaded"}),
                        mimetype="application/json")
    try:
        cap = float(request.args.get("capital") or 1_00_000)
        lev = float(request.args.get("leverage") or 5)
        tgt = float(request.args.get("target") or 10)
    except (TypeError, ValueError):
        return Response(json.dumps({"ok": False,
                                    "err": "capital, leverage and target must be numbers"}),
                        mimetype="application/json")
    if not (1_000 <= cap <= 10_00_00_000):
        return Response(json.dumps({"ok": False,
                                    "err": "capital must be between Rs 1,000 and Rs 10 crore"}),
                        mimetype="application/json")
    try:
        # Whatever he types here is what Super Stocks should screen for -- one
        # position size, not two. Stored so superstocks_loop picks it up.
        try:
            SUPER["position_rs"] = cap * lev / max(1, int(out_slots) if False else
                                                   paper_engine.DEFAULT_SLOTS)
        except Exception:
            pass
        out = paper_engine.run(cap, lev, tgt,
                               now_hms=datetime.now(engine.IST).strftime("%H:%M:%S"),
                               log=log)
        return Response(json.dumps(out, default=str), mimetype="application/json")
    except Exception as e:
        log(f"papertrade: {type(e).__name__} {str(e)[:140]}")
        return Response(json.dumps({"ok": False,
                                    "err": f"{type(e).__name__}: {str(e)[:160]}"}),
                        mimetype="application/json")


@app.route("/sectors")
def sectors_feed():
    if sectors is None:
        return Response(json.dumps({"ok": False, "err": "Movers_sectors not loaded",
                                    "rows": []}), mimetype="application/json")
    try:
        return Response(json.dumps({"ok": True,
                                    "rows": STATE.get("sectors") or [],
                                    "market": STATE.get("sector_market") or [],
                                    "meta": STATE.get("sector_meta") or {}}, default=str),
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


@app.route("/scanxblast")
def scanxblast_feed():
    if scanx_blast is None:
        return Response(json.dumps({"ok": False, "err": "scanx_blast not loaded",
                                    "rows": []}), mimetype="application/json")
    try:
        return Response(json.dumps({"ok": True,
                                    "summary": STATE.get("blast_meta") or {},
                                    "rows": STATE.get("blast") or []}, default=str),
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "err": str(e)[:160], "rows": []}),
                        mimetype="application/json")


def gap_auditor_loop():
    """Every 15 minutes, 09:15-10:45: measure badge-vs-reality and tune.

    Isolated in its own thread and wrapped so that a failure here can only cost
    the audit, never the board. Measuring the board must not be able to break
    the board.
    """
    try:
        import gap_auditor
    except Exception as e:
        log(f"gap_auditor unavailable: {type(e).__name__} {str(e)[:110]}")
        return
    try:
        gap_auditor.loop(log)
    except Exception as e:
        log(f"gap_auditor_loop died: {type(e).__name__} {str(e)[:140]}")


def rate_loop():
    """Log the ACTUAL request rate every minute, per host.

    Dhan has warned this account about request volume, and until now nobody
    could say what the rate was -- only that it was probably fine. "Probably
    fine" is what the last warning was based on. This makes it a number in the
    log, and shouts if a 429 is ever seen.
    """
    import Movers_chartfeed as _cfeed
    while True:
        time.sleep(60)
        try:
            a = engine.req_stats(reset=True)
            b = _cfeed.req_stats(reset=True)
            c = quotes_v3.req_stats(reset=True)
            top = sorted(a["by_path"].items(), key=lambda kv: -kv[1])[:3]
            api_n = a["n"] + c["n"]
            api_ps = round(api_n / max(a["elapsed"], 0.001), 2)
            log(f"requests/min: api.dhan.co={api_n} ({api_ps}/s"
                f"  [quote {c['quote']} scanx {c['scanx']} engine {a['n']}]) "
                f"ticks.dhan.co={b['n']} ({b['per_sec']}/s) "
                f"| 429s: api={a['429'] + c['429']} ticks={b['429']} "
                f"| 5xx: {c.get('5xx', 0)} "
                f"| top={top}")
            # 02-Sep: gateway errors were only ever visible as individual log
            # lines scrolling past. Counted here they become a trend, which is
            # what tells the difference between a blip and an outage worth
            # standing down for.
            if c.get("5xx", 0) >= 10:
                log(f"!! DHAN GATEWAY UNHEALTHY: {c['5xx']} 5xx responses this "
                    f"minute -- sweeps will be short and cards will be stale")
            if a["429"] or b["429"] or c["429"]:
                log(f"!! RATE LIMITED this minute (api={a['429'] + c['429']} ticks={b['429']}) "
                    f"-- back off before Dhan does it for us")
        except Exception as e:
            log(f"rate_loop: {type(e).__name__} {str(e)[:90]}")


def _revive_loop():
    """Keep checking whether the engine is alive, forever.

    23-Sep: _revive_engine() used to be called ONCE, at the bottom of main(),
    and it returns immediately outside 09:05-15:31. Sri started EYE_START.bat at
    07:23 -- well before the open, exactly as the launcher tells him to -- so
    that single call fell outside the window, did nothing, and was never made
    again. The board came up fine, the tab rendered fine, and paper_live.py was
    simply never launched all morning. Starting the board EARLY guaranteed no
    paper trading; starting it after 09:05 was what had been quietly saving it
    on every previous day. Now it is polled, so the launch time cannot matter.
    """
    while True:
        try:
            _revive_engine()
        except Exception as e:
            log(f"revive loop: {type(e).__name__} {str(e)[:90]}")
        time.sleep(45)


def _revive_engine():
    # Tiers first. 30-Sep: this call used to sit at the END of this function,
    # which has two early returns -- outside market hours, and when the engine
    # is heartbeating normally. So the tiers watchdog only ran when the ENGINE
    # was also broken, which is precisely when it is least needed. The tiers
    # worker sat dead for 44 minutes and the board showed a stale tier tab
    # beside a live main tab.
    try:
        _revive_tiers()
    except Exception:
        pass

    """Start paper_live.py if nothing is heartbeating for it.

    The engine writes its PID to logs/paper_live.pid on every cycle, so a file
    older than three minutes means no engine is alive. The supervisor also keeps
    it up, but the supervisor can be busy; this board restarts itself whenever
    its own source changes, which makes it the more reliable place to check.
    paper_live retires any older instance on startup, so a double-start is safe.
    """
    try:
        hm = datetime.now().strftime("%H:%M")
        if not ("09:05" <= hm <= "15:31"):
            return
        here = os.path.dirname(os.path.abspath(__file__))
        pidf = os.path.join(here, "logs", "paper_live.pid")
        bootf = os.path.join(here, "logs", "paper_live.boot")
        # 23-Sep: this used to watch paper_live.py ALONE, so editing the actual
        # decision logic -- eye_strategy.py, eye_cfg.py, tune_v6.py -- left the
        # running engine on the old rules in memory while the file on disk said
        # something else. MIN_LEG was changed from 4 to 10 at 09:56 and the
        # engine kept taking 4-bar legs, because nothing told it to reload. The
        # brain now counts as source too.
        srcs = [os.path.join(here, f) for f in
                ("paper_live.py", "eye_strategy.py", "eye_cfg.py",
                 "tune_v6.py", "live_shadow.py")]
        stale_code = True
        try:                    # is the running engine on the current source?
            boot = float(open(bootf).read().strip())
            newest = max(os.path.getmtime(f) for f in srcs if os.path.exists(f))
            stale_code = boot < newest
        except (OSError, ValueError):
            pass                # no boot stamp at all -- treat as stale
        beating = (os.path.exists(pidf)
                   and time.time() - os.path.getmtime(pidf) < 180)
        if beating and not stale_code:
            return
        import subprocess
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        out = open(os.path.join(here, "logs", "paper_live_stdout.log"), "a",
                   encoding="utf-8", errors="replace")
        pr = subprocess.Popen([sys.executable, os.path.join(here, "paper_live.py")],
                              cwd=here, creationflags=flags,
                              stdout=out, stderr=subprocess.STDOUT)
        log(f"engine: no heartbeat -- launched paper_live.py, PID {pr.pid}")
    except Exception as e:
        log(f"engine: could not revive paper_live -- {type(e).__name__} {e}")


def _revive_tiers():
    """Keep the CAPITAL TIERS worker alive, the same way the engine is kept alive.

    30-Sep: paper_tiers.py had no heartbeat and no self-retire, so a code fix
    left it running stale in memory and only a hand-run .bat could recover it.
    It now writes logs/paper_tiers.pid every cycle and exits when its sources
    change; this brings it back. Read-only worker -- it places no orders and
    never writes anything the Rs 1,00,000 engine owns, so a double-start is
    harmless.
    """
    try:
        hm = datetime.now().strftime("%H:%M")
        if not ("09:05" <= hm <= "15:31"):
            return
        here = os.path.dirname(os.path.abspath(__file__))
        pidf = os.path.join(here, "logs", "paper_tiers.pid")
        if os.path.exists(pidf) and time.time() - os.path.getmtime(pidf) < 180:
            return                      # heartbeating, leave it alone
        import subprocess
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        out = open(os.path.join(here, "logs", "paper_tiers_stdout.log"), "a",
                   encoding="utf-8", errors="replace")
        pr = subprocess.Popen([sys.executable, os.path.join(here, "paper_tiers.py")],
                              cwd=here, creationflags=flags,
                              stdout=out, stderr=subprocess.STDOUT)
        log(f"tiers: no heartbeat -- launched paper_tiers.py, PID {pr.pid}")
    except Exception as e:
        log(f"tiers: could not revive paper_tiers -- {type(e).__name__} {e}")


def main():
    log("=" * 50)
    log(f"BUILD {BUILD_ID}  (source timestamp -- if this is old, the restart did not take)")
    log(f"MOVERS BOARD start :{PORT} cycle={CYCLE_SEC}s (display-only, no auto-trading)")
    # MASTER FILE FIRST. Movers_alarm caches the universe on first call and never
    # rebuilds it, so a refresh has to land before anything asks for it. On
    # 01-Sep the file was 73 days old and LALITHAA -- Rs 55 crore traded in
    # twelve minutes -- was invisible to every scanner because of it.
    if master is not None:
        try:
            if master.refresh(log=log):
                log("master: universe will be rebuilt from the new file")
        except Exception as _e:
            log(f"master: startup refresh {type(_e).__name__} {str(_e)[:110]}")
        threading.Thread(target=master.loop, args=(log,), daemon=True).start()
    TICKS.start(log)                                          # live 30s bar builder
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=candle_loop, daemon=True).start()   # candles, off the critical path
    threading.Thread(target=preopen_loop, daemon=True).start()  # 09:00-09:15 gap scanner
    threading.Thread(target=premarket_loop, daemon=True).start()  # news severity + validation
    threading.Thread(target=alarm_loop, daemon=True).start()  # 1-MIN ALARM full-universe sweep
    # SCANNER1 AND SCANX BLAST TABS REMOVED 01-Sep, on Sri's instruction: their
    # tabs were not being used and their loops were the two most expensive
    # things on the box -- Scanner1 alone re-scored 2,455 stocks every 5 seconds
    # and logged "2455 scanned -> 151 pass" hundreds of times an hour.
    # Movers_scanner1 STAYS IMPORTED: _stamp_momentum() calls its momentum() and
    # mins_since_open() for the Board's own momentum badge, which is unrelated
    # to the tab. Only the loops and the tabs are gone; the routes are left in
    # place so nothing 500s if a stale page is still open somewhere.
    # threading.Thread(target=scanner1_loop, daemon=True).start()  # tab removed
    threading.Thread(target=watchlist_loop, daemon=True).start()  # Morning Watchlist
    threading.Thread(target=audit_loop, daemon=True).start()   # badge-vs-reality audit
    threading.Thread(target=superstocks_loop, daemon=True).start()   # whole-universe finder
    if super_monitor is not None and superstocks is not None and alarm is not None:
        # Watches the Super Stocks tab against an INDEPENDENT view of the same
        # market, because the tab was broken for two days and its own status
        # said "no qualifying stocks" the whole time.
        threading.Thread(
            target=super_monitor.loop,
            args=(superstocks, alarm, log), daemon=True).start()
    threading.Thread(target=gap_auditor_loop, daemon=True).start()  # 15-min gap audit + auto-tune
    threading.Thread(target=rate_loop, daemon=True).start()      # measured request rate, per host
    # threading.Thread(target=scanxblast_loop, daemon=True).start()  # tab removed
    threading.Thread(target=sector_loop, daemon=True).start()     # sector breadth + open-web news

    # AUTOSTART. A restart used to mean somebody had to go and press Start
    # again, so every code change cost a manual step and often a lost hour.
    def _autostart():
        time.sleep(20)                      # let the sweep fill first
        try:
            f = Path(__file__).resolve().parent / "live_config.json"
            if f.exists():
                _apply_config(json.loads(f.read_text(encoding="utf-8")), log)
        except Exception as e:
            log(f"config: {type(e).__name__} {str(e)[:90]}")
        if live_paper is None:
            return
        # WAIT FOR THE OPEN -- do not give up on it.
        #
        # This used to check the clock once, 20 seconds after boot, and return
        # for good if the market was not already open. Sri starts the board at
        # 09:00 so it is warm for the 09:15 open, which meant autostart looked
        # at 09:00:20, said "outside trading hours", and never ran again. The
        # board would sit there all day looking perfectly healthy and never
        # place a trade -- and the log line explaining why would be one line,
        # nine hours earlier, in a file nobody reads until the evening.
        #
        # So it now waits for the window instead of declining it. Before 09:15
        # it sleeps until the open; after 15:10 there is genuinely nothing to
        # start, and that is the only case that still returns.
        while True:
            now = datetime.now(engine.IST).strftime("%H:%M:%S")
            if now > "15:10:00":
                log(f"autostart: {now} is past the trading window -- not starting")
                return
            if now >= "09:15:00":
                break
            if datetime.now(engine.IST).weekday() >= 5:
                log("autostart: weekend -- not starting")
                return
            log(f"autostart: {now} -- holding until the 09:15 open")
            time.sleep(30)
        try:
            snap = live_paper.snapshot()["summary"]
            if snap.get("active"):
                log("autostart: a run is already active -- left alone")
                return
            live_paper.start(AUTOSTART_CAPITAL, AUTOSTART_LEVERAGE,
                             AUTOSTART_TARGET, log=log)
            log("autostart: live paper trading resumed by itself after restart")
        except Exception as e:
            log(f"autostart: {type(e).__name__} {str(e)[:110]}")
    threading.Thread(target=_autostart, daemon=True).start()

    # CODE WATCHER. After this one bootstrap restart, nobody needs to restart
    # this board again: editing any of the modules below re-execs the process
    # automatically, and _autostart() then resumes trading. A change is only
    # acted on once the file has been QUIET for SETTLE seconds, so a half-written
    # file is never loaded.
    def _watch_code():
        watched = ["Movers_app.py", "live_paper.py", "superstocks_lab.py",
                   "paper_engine.py", "superstocks.py", "Movers_alarm.py",
                   "Movers_Board.html"]
        base = Path(__file__).resolve().parent
        SETTLE = 6.0
        seen = {}
        for f in watched:
            try:
                seen[f] = (base / f).stat().st_mtime
            except OSError:
                seen[f] = 0
        while True:
            time.sleep(4)
            try:
                changed, newest = [], 0.0
                for f in watched:
                    try:
                        m = (base / f).stat().st_mtime
                    except OSError:
                        continue
                    if m > seen.get(f, 0) + 0.5:
                        changed.append(f)
                        newest = max(newest, m)
                if changed and (time.time() - newest) >= SETTLE:
                    log("=" * 50)
                    log(f"CODE CHANGED ({', '.join(changed)}) -- restarting myself")
                    time.sleep(0.5)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                log(f"code watcher: {type(e).__name__} {str(e)[:90]}")
    threading.Thread(target=_watch_code, daemon=True).start()

    # AUTOSTART. A restart used to mean somebody had to go and press Start
    # again, so every code change cost a manual step and often a lost hour.
    def _autostart():
        time.sleep(20)                      # let the sweep fill first
        try:
            f = Path(__file__).resolve().parent / "live_config.json"
            if f.exists():
                _apply_config(json.loads(f.read_text(encoding="utf-8")), log)
        except Exception as e:
            log(f"config: {type(e).__name__} {str(e)[:90]}")
        if live_paper is None:
            return
        # WAIT FOR THE OPEN -- do not give up on it.
        #
        # This used to check the clock once, 20 seconds after boot, and return
        # for good if the market was not already open. Sri starts the board at
        # 09:00 so it is warm for the 09:15 open, which meant autostart looked
        # at 09:00:20, said "outside trading hours", and never ran again. The
        # board would sit there all day looking perfectly healthy and never
        # place a trade -- and the log line explaining why would be one line,
        # nine hours earlier, in a file nobody reads until the evening.
        #
        # So it now waits for the window instead of declining it. Before 09:15
        # it sleeps until the open; after 15:10 there is genuinely nothing to
        # start, and that is the only case that still returns.
        while True:
            now = datetime.now(engine.IST).strftime("%H:%M:%S")
            if now > "15:10:00":
                log(f"autostart: {now} is past the trading window -- not starting")
                return
            if now >= "09:15:00":
                break
            if datetime.now(engine.IST).weekday() >= 5:
                log("autostart: weekend -- not starting")
                return
            log(f"autostart: {now} -- holding until the 09:15 open")
            time.sleep(30)
        try:
            snap = live_paper.snapshot()["summary"]
            if snap.get("active"):
                log("autostart: a run is already active -- left alone")
                return
            live_paper.start(AUTOSTART_CAPITAL, AUTOSTART_LEVERAGE,
                             AUTOSTART_TARGET, log=log)
            log("autostart: live paper trading resumed by itself after restart")
        except Exception as e:
            log(f"autostart: {type(e).__name__} {str(e)[:110]}")
    threading.Thread(target=_autostart, daemon=True).start()
    if live_paper is not None and alarm is not None:
        # Reads STATE["super"] -- the same rows the tab shows -- and the alarm
        # sweep. No request of its own, so it is safe to leave running.
        threading.Thread(target=live_paper.loop,
                         args=(alarm,
                               # Trading reads the LAB, never his board.
                               lambda: STATE.get("superlab") or STATE.get("super") or [],
                               log),
                         daemon=True).start()
    threading.Thread(target=_revive_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
