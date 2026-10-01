"""
candle_v3.py -- shared candle fetch with cross-session warmup + holiday walk-back.

Used by BOTH pipelines (data layer, not badge logic).

- Fetches 1-min candles for the most recent trading session.
- If the latest session has too few bars for MACD (~35), it PREPENDS the prior
  trading day's bars so candles + MACD render fully from 09:15 (no blanks).
- "Previous trading day" = walk back through days that actually have data
  (skips weekends + holidays automatically; no holiday list).
- Also returns prev_close (close of the day before the latest session) for day%.
"""
from collections import OrderedDict
from datetime import datetime, timedelta

import Opus_engine as engine
try:
    import Movers_chartfeed as chartfeed          # Dhan's own web chart feed
except Exception as _e:                           # pragma: no cover
    chartfeed = None
    print("Movers_chartfeed unavailable:", _e)

IST = engine.IST
KEYS = ("open", "high", "low", "close", "volume", "timestamp")
# Warmup bars kept from prior sessions.
#   45 was enough for MACD (26+9) but NOT for the Ultimate Master Scalper signals,
#   which need 84 bars. With 45, compute_signals returned ok=False, the engine then
#   read sig["buy_event"] -> KeyError -> swallowed -> labels stayed EMPTY, so the
#   pine-script Buy markers never rendered before ~10:39 IST (when the current
#   session alone finally has 84 bars). 90 gives the warmup from the open.
#   No extra API cost: fetch() already pulls 12 days and simply trimmed them off.
MIN_BARS = 90


# ---- WHERE THE BARS COME FROM (changed 31-Aug-2026) ----------------------
# The official /v2/charts/intraday endpoint returns DH-905 on this account for
# every request shape tried (18 of them -- see Movers_chartfeed's docstring and
# logs/STEP0*_REPORT.txt). No candles means build_card() returns None for every
# symbol, which means an empty board that looks exactly like a quiet market.
#
# So the PRIMARY source is now Dhan's own web chart feed, which works today and
# was verified on 8 of 8 real board names before this line was written. The
# official API is kept as the FALLBACK rather than deleted: if the entitlement
# comes back, it takes over again by itself and nothing here needs editing.
#
# Everything below this fetch is untouched. Both sources are normalised into the
# same six arrays, so the session split, the holiday walk-back, prev_close and
# today_bars behave identically whichever one answered.
def _from_web(sid, lookback_days):
    if chartfeed is None:
        return None, "chartfeed module not loaded"
    a, err = chartfeed.get(sid, interval="1", days=lookback_days, tz=IST)
    if err or not a:
        return None, err or "no data"
    return {"open": a["o"], "high": a["h"], "low": a["l"], "close": a["c"],
            "volume": a["v"], "timestamp": a["t"]}, None


# ---- DON'T KEEP KNOCKING ON A DOOR THAT IS BOLTED --------------------------
# The official endpoint is kept as a fallback so it resumes by itself if Dhan
# restores the entitlement. But the per-minute rate log caught it being called
# 12 times in one minute -- every one of them a guaranteed DH-905. Those are
# requests spent on nothing, against an account Dhan has already warned about
# volume. After 5 consecutive failures it is left alone for 15 minutes, then
# tried once more; a single success clears the breaker permanently.
_OFFICIAL = {"fails": 0, "blocked_until": 0.0}
_OFFICIAL_MAX_FAILS = 5
_OFFICIAL_COOLDOWN = 900


def _from_official(sid, lookback_days):
    import time as _t
    if _OFFICIAL["fails"] >= _OFFICIAL_MAX_FAILS and _t.time() < _OFFICIAL["blocked_until"]:
        return None, "official chart API skipped (DH-905 breaker open)"
    end = datetime.now(IST)
    start = end - timedelta(days=lookback_days)
    d = engine.post("/charts/intraday", {
        "securityId": sid, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
        "interval": "1", "fromDate": start.strftime("%Y-%m-%d"),
        "toDate": end.strftime("%Y-%m-%d")})
    if "_error" in d:
        _OFFICIAL["fails"] += 1
        if _OFFICIAL["fails"] >= _OFFICIAL_MAX_FAILS:
            _OFFICIAL["blocked_until"] = __import__("time").time() + _OFFICIAL_COOLDOWN
        return None, d["_error"]
    _OFFICIAL.update({"fails": 0, "blocked_until": 0.0})   # it works again
    return {k: (d.get(k) or []) for k in KEYS}, None


def fetch(sid, lookback_days=12):
    full, err = _from_web(sid, lookback_days)
    if full is None:
        full, err2 = _from_official(sid, lookback_days)
        if full is None:
            # Report the WEB error, not the official one: the official endpoint
            # is known-broken here, so its message would say nothing useful and
            # would hide the reason the working source failed.
            return None, err or err2
    ts = full["timestamp"]
    if not ts or len(full["close"]) < 5:
        return None, f"few_bars({len(full['close'])})"

    days = [datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d") for t in ts]
    byday = OrderedDict()
    for i, dd in enumerate(days):
        byday.setdefault(dd, []).append(i)
    ordered = sorted(byday)                 # chronological trading days present
    latest = ordered[-1]

    # start with latest session; walk back (prior trading days) until MACD-ready
    idx = list(byday[latest])
    di = len(ordered) - 2
    while len(idx) < MIN_BARS and di >= 0:
        idx = list(byday[ordered[di]]) + idx
        di -= 1
    idx = sorted(idx)
    sess = {k: [full[k][i] for i in idx] for k in KEYS}

    prev_close = None                       # close of the day before 'latest' -> day%
    if len(ordered) >= 2:
        prev_idx = byday[ordered[-2]]
        prev_close = full["close"][prev_idx[-1]]

    return {"candles": sess, "prev_close": prev_close,
            "session": latest, "today_bars": len(byday[latest])}, None


# ---- SUB-MINUTE BARS (31-Aug-2026) --------------------------------------
# The board's 30-second cards were built LIVE by Movers_ticks, which needs 35
# bars before the indicators mean anything -- about 18 minutes after launch. It
# has no market-hours gate, so in practice those 35 bars were being filled from
# 08:17 with pre-open samples: flat price, zero volume. By 09:15 the card said
# "30s" and its EMA-9, MA-12 and MACD had been computed on dead air. Measured on
# his own logs: the first "30s" card appeared at 08:17 on 27-Aug, 08:25 on
# 28-Aug and 08:17 on 31-Aug -- every one of them before the market opened.
#
# Dhan's own chart does not do that. It asks /getDataS, which serves real 30s
# bars including PREVIOUS SESSIONS, so a card can be correctly warmed up at
# 09:15:30. Same normalisation as fetch(), so everything downstream is unchanged
# and only the bar size differs.
SEC_MIN_BARS = 90          # warm-up bars kept from prior sessions (as MIN_BARS)
# ...and a CEILING, which matters more than the floor here.
# A 5-day 30-second fetch is ~2,300 bars. Every one of them then goes through
# pure-python EMA/SMA/MACD/PSAR/Ichimoku/consolidation for EVERY card, every
# refresh -- six times the work the 1-minute series used to cost, which pushed
# the board's cycle from ~5s to ~20s and broke the 15-second reshuffle.
#
# It bought nothing. macd_check.py measured it directly on ICIL: today-only and
# the full continuous series agree to FOUR DECIMALS (0.1527 both ways, and the
# same at +50, +100, +200, +400 and +800 prior bars), because the EMAs have long
# since converged. 800 keeps all of today plus real warm-up in the morning, when
# today is short and warm-up is the only thing there is.
MAX_SEC_BARS = 800


def fetch_seconds(sid, interval="30S", lookback_days=5):
    """Bundle of sub-minute bars, in the SAME shape fetch() returns.

    lookback_days=5 is deliberate and measured: 1, 2 and 3-day windows come back
    with today only, while 5 returns three sessions. A card that opens on today's
    first bar alone is exactly the blind-at-09:15 problem this replaces.
    """
    if chartfeed is None:
        return None, "chartfeed module not loaded"
    a, err = chartfeed.get_seconds(sid, interval=interval, days=lookback_days, tz=IST)
    if err or not a:
        return None, err or "no data"
    full = {"open": a["o"], "high": a["h"], "low": a["l"], "close": a["c"],
            "volume": a["v"], "timestamp": a["t"]}
    ts = full["timestamp"]
    if not ts or len(full["close"]) < 5:
        return None, f"few_bars({len(full['close'])})"

    days = [datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d") for t in ts]
    byday = OrderedDict()
    for i, dd in enumerate(days):
        byday.setdefault(dd, []).append(i)
    ordered = sorted(byday)
    latest = ordered[-1]

    idx = list(byday[latest])
    di = len(ordered) - 2
    while len(idx) < SEC_MIN_BARS and di >= 0:
        idx = list(byday[ordered[di]]) + idx
        di -= 1
    idx = sorted(idx)
    # Trim from the FRONT only -- today's bars are all at the end, so today_bars
    # stays correct and the session split downstream is untouched.
    if len(idx) > MAX_SEC_BARS:
        idx = idx[-MAX_SEC_BARS:]
    sess = {k: [full[k][i] for i in idx] for k in KEYS}

    prev_close = None
    if len(ordered) >= 2:
        prev_idx = byday[ordered[-2]]
        prev_close = full["close"][prev_idx[-1]]

    today_n = len(byday[latest])
    return {"candles": sess, "prev_close": prev_close,
            "session": latest, "today_bars": min(today_n, len(idx))}, None
