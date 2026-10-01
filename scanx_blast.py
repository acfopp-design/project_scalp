"""
scanx_blast.py -- the SCANX MOMENTUM BLAST tab.

WHAT THIS REPRODUCES
    Dhan's published screener, read off the page on 28-Aug-2026:
        scanx.trade/stock-screener/intraday-momentum-blast-imb-442186

        NSE
        Price               above Open Price
        Price % Change      >= 1.00
        RSI (14)            >= 60
        MACD Histogram      >= 0
        Volume              1 Day Unusual Volume
        Price               above Supertrend
        Market Cap          >= Rs 50,000 Cr
        (the -411742 variant adds: F&O stocks only)

TWO SUBSTITUTIONS, BOTH DELIBERATE AND BOTH VISIBLE ON THE TAB

    MARKET CAP -> F&O ELIGIBILITY. Market capitalisation is in no file this
    project holds. F&O-eligible NSE stocks number 227, and EIGHT OF THE NINE
    stocks that screener actually returned on 28-Aug are F&O names. It is also
    literally the filter in the second variant of the same screener. So this is
    a substitute chosen because it matches the observed output, not because it
    was convenient.

    RSI / MACD / SUPERTREND ARE DAILY, NOT INTRADAY. On ScanX these are daily
    values -- HINDZINC showed a Supertrend of 557 against a price of 622, a 10%
    gap that only a daily chart produces. Computing them on 1-minute bars would
    be a different screener wearing the same name.

    Daily values barely move during a morning, so they are fetched ONCE before
    the open and held. That is not a shortcut, it is the correct reading of what
    they do: they decide WHICH STOCKS ARE ELIGIBLE TODAY, not when to buy. It
    also means this tab has no indicator warm-up and can fire at 09:15:01.

WHAT IS MEASURED, NOT ASSUMED
    Backtested over five sessions on real Dhan 1-minute bars before this tab
    existed (see imb_backtest.py):

        full screener            33 flagged/day   33% gained 0.5% in 10 min
        without RSI/MACD/ST      40 flagged/day   36%
        median flag time         09:40 with the confirmations, 09:36 without

    The confirmations cost four minutes and bought nothing. They are kept ON by
    default because the point of this tab is to be Dhan's screener, faithfully
    -- but each is a switch, so the comparison can be made live rather than
    argued about.

    For contrast, on the same five sessions the SUPER STOCKS tab flagged 39 a
    day at 65%, median 09:29. This tab is not expected to beat it. It is here so
    the two can be watched side by side on real mornings.

UNIVERSE -- CHANGED 30-Aug ON HIS INSTRUCTION
    Originally the 227 F&O names, as a stand-in for the screener's
    "Market Cap >= Rs 50,000 Cr". He asked for the whole market instead, so
    FNO_ONLY is off and this now scans all 2,455 NSE EQ stocks -- the same
    universe Super Stocks uses.

    Say plainly what that means: this is no longer Dhan's screener. It is
    Dhan's FILTERS applied to the whole market rather than to the large-cap
    slice they were written for. RSI >= 60 and a positive MACD mean something
    different on a Rs 400 Cr company than on HINDZINC. One constant switches
    back if the wide version turns out to be noise.

COST
    2,455 daily-bar requests once per session (was 227), which is about nine
    minutes at the shared rate. It therefore runs from 08:00, not 09:00 -- see
    WARM_START_HHMM. Intraday it reads the alarm sweep already in memory and
    costs nothing.
"""
from __future__ import annotations

import csv
import json
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGDIR = HERE / "logs" / "movers_board"
LOGDIR.mkdir(parents=True, exist_ok=True)

# ---- the screener's own filters -------------------------------------------
MIN_DAY_PCT = 1.0          # "Price % Change >= 1.00", against yesterday's close
REQUIRE_ABOVE_OPEN = True  # "Price above Open Price"
RSI_MIN = 60.0             # "RSI (14) >= 60"
MACD_MIN = 0.0             # "MACD Histogram >= 0"
UNUSUAL_X = 1.5            # "1 Day Unusual Volume" -- ScanX does not define it
USE_RSI = True             # each confirmation is a switch, see the docstring
USE_MACD = True
USE_SUPERTREND = True
# UNIVERSE -- his instruction, 30-Aug: "change the Universe from 227 F&O
# stocks to whole universe same like super stocks".
#
# So the market-cap stand-in is OFF and this scans all 2,455 NSE EQ stocks,
# exactly the set Super Stocks uses. That is a deliberate departure from the
# published screener, which is large-cap only -- this tab now applies Dhan's
# FILTERS to the whole market rather than to Dhan's chosen slice of it.
#
# It costs 2,455 daily-bar requests instead of 227. See WARM_START_HHMM.
FNO_ONLY = False

MIN_PRICE = 20.0

# ---- MARKET CAP -> MEASURED LIQUIDITY (31-Aug-2026) ----------------------
# The published screener ends with "Market Cap >= Rs 50,000 Cr". In the video
# the author says exactly why: "stock liquidity isn't always sufficient, so a
# portion of the 0.5% to 1.5% profit you intended to make gets lost to
# slippage." Market cap is his PROXY for tradeability; it is not the thing he
# cares about.
#
# This project already measures the thing itself, and measured it better:
# TRAVELFOOD traded Rs 67 lakh a minute -- clearing any rupee floor -- on 4,902
# shares of a Rs 1,366 stock. The book is thin and a scalp slips, and no
# market-cap filter catches that. Share count does. The same two gates took
# Super Stocks from 69% to 73%.
#
# Using them here keeps the non-F&O movers a market-cap filter would have
# thrown away, while serving the intent the author actually stated.
MIN_SHARES_PER_MIN = 5_000        # depth of book, price-neutral
MIN_RS_PER_MIN = 2_500_000        # Rs 25 lakh/min beneath it, so a cheap stock
                                  # cannot qualify on share count alone
# And a floor before the volume PROJECTION is allowed to mean anything. At
# 09:15:30 mins_open is 0.5, so one large opening print extrapolates to a
# gigantic "unusual volume". Requiring real turnover first stops the tab
# opening with a screen full of arithmetic artefacts.
MIN_TOVER_CR_EARLY = 0.25
TOP_N = 12
REFRESH_SEC = 30           # his instruction: 30-second refresh

# daily indicator settings, the platform defaults
RSI_LEN, MACD_F, MACD_S, MACD_SIG = 14, 12, 26, 9
ST_LEN, ST_MULT = 10, 3.0
DAILY_LOOKBACK_DAYS = 90   # enough for RSI(14) and MACD(26,9) to be settled

# WHEN THE DAILY WARM-UP RUNS, and why it is not 09:00 any more.
#
# 2,455 requests at the shared 4.5/s limit is about nine minutes. Started at
# 09:00 that would still be running when he reads the board at 09:08 and would
# be competing with the board's own polling into the open -- two clients at
# full speed on one account is exactly what produced 5,397 rate-limit
# rejections in August.
#
# Daily values only change when a session CLOSES, so they can be built any time
# after yesterday's close. 08:00 leaves an hour of headroom before anything
# else matters.
WARM_START_HHMM = "08:00:00"
# ...and a HARD STOP. If the warm-up is somehow still running at 09:10 it must
# give up rather than compete with the board's own polling into the open. Two
# clients at full speed on one account is precisely what produced 5,397
# rate-limit rejections in August and a warning from Dhan. Whatever it has by
# then is used; the rest falls back to yesterday's cached values.
WARM_STOP_HHMM = "09:10:00"

try:
    import Movers_chartfeed as chartfeed
except Exception as _e:                      # pragma: no cover
    chartfeed = None
    print("scanx_blast: Movers_chartfeed unavailable:", _e)

_lock = threading.Lock()
_daily = {}                # sym -> {rsi, macd_hist, st, above_st, avg_vol, day}
_hist = {}                 # sym -> deque[(t, price, dayvol)]
_first = {}                # sym -> first time it qualified
_reject = {}               # sym -> why it was rejected on the last pass
_state = {"rows": [], "ts": None, "scanned": 0, "qualified": 0, "warm": 0,
          "stale_daily": None, "why_empty": None, "err": None, "folded": None,
          "funnel": {"eligible": 0, "above_open": 0, "day1": 0,
                     "trend": 0, "volume": 0}}


# ============================================================== indicators
def rsi(closes, n=RSI_LEN):
    if len(closes) <= n:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _ema(v, n):
    k, e, out = 2 / (n + 1), None, []
    for x in v:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def macd_hist(closes, f=MACD_F, s=MACD_S, sig=MACD_SIG):
    if len(closes) < s + sig:
        return None
    ef, es = _ema(closes, f), _ema(closes, s)
    line = [a - b for a, b in zip(ef, es)]
    return line[-1] - _ema(line, sig)[-1]


def supertrend(highs, lows, closes, n=ST_LEN, mult=ST_MULT):
    """Returns the latest Supertrend band, or None."""
    if len(closes) < n + 2:
        return None
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    a = None
    atr = []
    for x in tr:
        a = x if a is None else (a * (n - 1) + x) / n
        atr.append(a)
    dirn, prev, band = 1, None, None
    for i in range(len(closes)):
        mid = (highs[i] + lows[i]) / 2
        ub, lb = mid + mult * atr[i], mid - mult * atr[i]
        if prev is not None:
            if closes[i - 1] <= prev[0]:
                ub = min(ub, prev[0])
            if closes[i - 1] >= prev[1]:
                lb = max(lb, prev[1])
        if dirn == 1 and closes[i] < lb:
            dirn = -1
        elif dirn == -1 and closes[i] > ub:
            dirn = 1
        band = lb if dirn == 1 else ub
        prev = (ub, lb)
    return band


# ============================================================== the universe
_uni_cache = {"v": None}


def universe(alarm):
    """The scan universe.

    FNO_ONLY is now OFF at his instruction, so this is all 2,455 NSE EQ stocks
    -- the same universe Super Stocks uses. The F&O path is kept because it is
    the faithful reading of the published screener, and one constant switches
    back to it.
    """
    if _uni_cache["v"] is not None:
        return _uni_cache["v"]
    eq = alarm.universe()                       # {int sid: sym}, EQ series only
    fno = set()
    try:
        with open(alarm.MASTER, encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                if (r.get("SEM_EXM_EXCH_ID") == "NSE"
                        and r.get("SEM_INSTRUMENT_NAME") in ("FUTSTK", "OPTSTK")):
                    u = (r.get("SEM_TRADING_SYMBOL") or "").split("-")[0].strip().upper()
                    if u:
                        fno.add(u)
    except Exception:
        fno = set()
    out = ({sid: s for sid, s in eq.items() if s.upper() in fno}
           if (FNO_ONLY and fno) else dict(eq))
    _uni_cache["v"] = out
    return out


# ========================================================= the daily warm-up
def _cache_path(day):
    return LOGDIR / f"scanx_daily_{day}.json"


def _newest_previous(day):
    """The most recent daily cache from BEFORE today, or None.

    Used so the tab is not blank while today's rebuild runs. Daily indicators
    only move when a session closes, so yesterday's file is one session stale
    -- worse than fresh, far better than nothing, and the tab says which.
    """
    old = sorted(p for p in LOGDIR.glob("scanx_daily_*.json")
                 if p.stem[12:] < day)
    return old[-1] if old else None


def warm_daily(alarm, engine, log=lambda m: None, force=False):
    """Fetch daily bars once and compute the four daily values per stock.

    Cached to disk by date: a restart mid-morning re-reads the file instead of
    spending 227 requests again.
    """
    day = datetime.now().strftime("%Y%m%d")
    p = _cache_path(day)
    if p.exists() and not force:
        try:
            with _lock:
                _daily.clear()
                _daily.update(json.loads(p.read_text(encoding="utf-8")))
                _state["warm"] = len(_daily)
            log(f"scanx: daily values loaded from cache ({len(_daily)} stocks)")
            return len(_daily)
        except Exception as e:
            log(f"scanx: daily cache unreadable ({type(e).__name__}), refetching")

    # Load the previous day's file first so the tab works from the first pass
    # while today's is being rebuilt, rather than showing nothing for minutes.
    prev = _newest_previous(day)
    if prev:
        try:
            with _lock:
                _daily.clear()
                _daily.update(json.loads(prev.read_text(encoding="utf-8")))
                _state["warm"] = len(_daily)
                _state["stale_daily"] = prev.stem[12:]
            log(f"scanx: using {prev.stem[12:]} daily values ({len(_daily)} stocks) "
                f"while today's rebuild")
        except Exception:
            pass

    uni = universe(alarm)
    end = datetime.now()
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)
    got, errs = {}, {}

    # ---- THE DAILY FEED RUNS ONE SESSION BEHIND -------------------------
    # Measured on 31-Aug against Dhan's own published screener values. The
    # daily feed's last bar was 28-Aug -- the previous COMPLETED session -- so
    # indicators built from it alone were a session stale:
    #
    #     SUNPHARMA  RSI 49.73 stale   vs  64.24 on ScanX
    #     AUROPHARMA RSI 58.42 stale   vs  68.89
    #     NYKAA      RSI 56.68 stale   vs  66.34
    #
    # Every one of those would have been REJECTED by "RSI >= 60" while Dhan's
    # screener was showing them. Supertrend flipped side too (SUNPHARMA band
    # 1982 stale vs 1840 current, against a price of 1984). Folding the missing
    # session in matched Dhan on 9 of 10 stocks EXACTLY.
    #
    # So: find the last session that SHOULD be in the daily series, and if the
    # feed is behind, rebuild that one bar from the 1-minute feed. Checked once
    # against a probe stock -- if the feed is current, not one extra request is
    # made.
    need_date = None
    try:
        pr, _e = chartfeed.get("2885", interval="1", days=5)      # RELIANCE
        if pr and pr.get("t"):
            today_s = datetime.now().strftime("%Y-%m-%d")
            days_seen = sorted({datetime.fromtimestamp(x).strftime("%Y-%m-%d")
                                for x in pr["t"]})
            past = [d for d in days_seen if d < today_s]
            need_date = past[-1] if past else None
        pd_, _e2 = chartfeed.daily("2885", "RELIANCE", days=30)
        have = None
        if pd_ and pd_.get("t"):
            have = datetime.fromtimestamp(pd_["t"][-1]).strftime("%Y-%m-%d")
        if have and need_date and have >= need_date:
            need_date = None                       # feed is current, nothing to fold
            log("scanx: daily feed is current -- no session to fold in")
        elif need_date:
            log(f"scanx: daily feed ends {have}, folding {need_date} in from "
                f"1-minute bars (one extra request per stock)")
    except Exception as e:
        log(f"scanx: lag probe failed ({type(e).__name__}) -- folding disabled")
        need_date = None
    _state["folded"] = need_date
    log(f"scanx: fetching daily bars for {len(uni)} stocks "
        f"(once per day, ~{len(uni)/4.5/60:.0f} min)")
    for n, (sid, sym) in enumerate(uni.items(), 1):
        try:
            # DAILY BARS COME FROM DHAN'S OWN CHART FEED, not /charts/historical.
            # That endpoint returns DH-905 on this account for every request
            # shape tried (18 of them, 31-Aug). Without daily values this whole
            # tab is dead: scan() drops any stock with no daily row.
            #
            # The daily path needs SYM as well as SEC_ID -- omit it and the
            # server cheerfully answers about a DIFFERENT STOCK rather than
            # erroring, which is the sort of wrong that never announces itself.
            a, e_ = chartfeed.daily(sid, sym, days=DAILY_LOOKBACK_DAYS + 90)
            if e_ or not a:
                errs[str(e_)[:18]] = errs.get(str(e_)[:18], 0) + 1
                continue
            c = [float(x) for x in (a.get("c") or []) if x]
            h = [float(x) for x in (a.get("h") or []) if x]
            lo = [float(x) for x in (a.get("l") or []) if x]
            v = [float(x) for x in (a.get("v") or []) if x is not None]
            # fold the missing session in, so the gates are not a day behind
            if need_date:
                try:
                    last_have = datetime.fromtimestamp(a["t"][-1]).strftime("%Y-%m-%d")
                except Exception:
                    last_have = None
                if last_have and last_have < need_date:
                    m1, _e3 = chartfeed.get(sid, interval="1", days=5)
                    if m1 and m1.get("t"):
                        ix = [i for i in range(len(m1["t"]))
                              if datetime.fromtimestamp(m1["t"][i]).strftime("%Y-%m-%d")
                              == need_date]
                        if ix:
                            c.append(float(m1["c"][ix[-1]]))
                            h.append(max(float(m1["h"][i]) for i in ix))
                            lo.append(min(float(m1["l"][i]) for i in ix))
                            v.append(sum(float(m1["v"][i] or 0) for i in ix))
            if len(c) < 30 or not (len(c) == len(h) == len(lo)):
                continue
            got[sym] = {
                "rsi": rsi(c), "macd": macd_hist(c),
                "st": supertrend(h, lo, c),
                # "1 day unusual volume" needs a normal to compare against
                "avg_vol": (sum(v[-20:]) / len(v[-20:])) if len(v) >= 20 else None,
                "prev_close": c[-1],
            }
        except Exception as e:
            errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1
        if datetime.now().strftime("%H:%M:%S") > WARM_STOP_HHMM:
            log(f"scanx: daily warm-up STOPPED at {WARM_STOP_HHMM} with "
                f"{len(got)} of {len(uni)} done -- the open takes priority")
            break
        if n % 250 == 0:
            # publish progressively: the tab improves as this fills in rather
            # than waiting nine minutes for everything
            with _lock:
                _daily.update(got)
                _state["warm"] = len(_daily)
            log(f"scanx: daily {n}/{len(uni)}  ({len(got)} usable)")
    with _lock:
        _daily.clear()
        _daily.update(got)
        _state["warm"] = len(got)
        _state["stale_daily"] = None
        _state["err"] = (None if got else "daily warm-up returned nothing")
    try:
        p.write_text(json.dumps(got), encoding="utf-8")
    except Exception as e:
        log(f"scanx: could not cache daily values -- {type(e).__name__} {e}")
    log(f"scanx: daily ready for {len(got)} stocks"
        + (f" | failures: {errs}" if errs else ""))
    return len(got)


# ==================================================================== state
def summary():
    with _lock:
        s = {k: v for k, v in _state.items() if k != "rows"}
    s["rows"] = len(_state["rows"])
    return s


def rows():
    with _lock:
        return list(_state["rows"])


def why(sym):
    return _reject.get(str(sym).upper())


def reset_for_day():
    with _lock:
        _hist.clear()
        _first.clear()
        _reject.clear()
        _state.update({"rows": [], "scanned": 0, "qualified": 0,
                       "why_empty": None, "err": None,
                       "funnel": {"eligible": 0, "above_open": 0, "day1": 0,
                                  "trend": 0, "volume": 0}})


def _sec(t):
    return int(t[0:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])


# ===================================================================== scan
def scan(alarm, now_hms=None, log=lambda m: None):
    """One pass. Cheap: reads the alarm sweep already in memory."""
    now_hms = now_hms or datetime.now().strftime("%H:%M:%S")
    t = _sec(now_hms)
    try:
        _ts, snap = alarm.snapshot()
    except Exception as e:
        with _lock:
            _state["err"] = f"{type(e).__name__}: {str(e)[:80]}"
        return []
    if not snap:
        with _lock:
            _state["why_empty"] = "the market sweep has no data yet"
            _state["rows"] = []
        return []

    uni = universe(alarm)
    # snapshot keys are str(sid), universe keys are int(sid). This exact
    # mismatch left the Super Stocks tab empty for two days.
    uni = {**uni, **{str(k): v for k, v in uni.items()}}

    f = {"eligible": 0, "above_open": 0, "day1": 0, "trend": 0, "volume": 0,
         "liquid": 0}
    _reject.clear()
    cand, scanned = [], 0
    mins_open = max(0.5, (t - 33300) / 60.0)

    for sid, q in snap.items():
        sym = uni.get(sid)
        if not sym:
            continue
        d = _daily.get(sym)
        if not d:
            continue                       # no daily values -> cannot judge it
        try:
            ltp = float(q[0] or 0)
            dayvol = float(q[1] or 0)
            op = float(q[2] or 0)
            prev = float(q[7] or 0) if len(q) > 7 else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        if ltp <= 0 or op <= 0 or ltp < MIN_PRICE:
            continue
        scanned += 1
        f["eligible"] += 1

        h = _hist.setdefault(sym, deque())
        h.append((t, ltp, dayvol))
        while h and h[0][0] < t - 900:
            h.popleft()

        if REQUIRE_ABOVE_OPEN and ltp <= op:
            _reject[sym] = "below its open"
            continue
        f["above_open"] += 1

        base = prev or d.get("prev_close") or 0
        day_pct = ((ltp / base - 1) * 100) if base else None
        if day_pct is None or day_pct < MIN_DAY_PCT:
            _reject[sym] = (f"day change only {day_pct:+.2f}%"
                            if day_pct is not None else "no previous close")
            continue
        f["day1"] += 1

        if USE_RSI and (d["rsi"] is None or d["rsi"] < RSI_MIN):
            _reject[sym] = f"daily RSI {d['rsi']:.1f} (needs {RSI_MIN:.0f})" \
                if d["rsi"] is not None else "no daily RSI"
            continue
        if USE_MACD and (d["macd"] is None or d["macd"] < MACD_MIN):
            _reject[sym] = f"daily MACD histogram {d['macd']:+.2f}" \
                if d["macd"] is not None else "no daily MACD"
            continue
        if USE_SUPERTREND and (d["st"] is None or ltp <= d["st"]):
            _reject[sym] = f"below its daily Supertrend ({d['st']:.1f})" \
                if d["st"] is not None else "no daily Supertrend"
            continue
        f["trend"] += 1

        # "1 day unusual volume": today's pace against its own 20-day average
        volx = None
        if d.get("avg_vol"):
            pace = dayvol / mins_open * 375.0          # projected full session
            volx = pace / d["avg_vol"]
            if volx < UNUSUAL_X:
                _reject[sym] = f"volume only {volx:.1f}x its normal day"
                continue
        f["volume"] += 1

        # ---- TRADEABLE? (replaces the screener's market-cap filter) -------
        sh_per_min = dayvol / mins_open
        rs_per_min = sh_per_min * ltp
        if ltp * dayvol / 1e7 < MIN_TOVER_CR_EARLY:
            _reject[sym] = (f"only Rs {ltp * dayvol / 1e5:.1f} lakh traded so far "
                            f"-- too early to judge its volume")
            continue
        if sh_per_min < MIN_SHARES_PER_MIN:
            _reject[sym] = (f"only {int(sh_per_min):,} shares/min -- the book is "
                            f"too thin to scalp without slippage")
            continue
        if rs_per_min < MIN_RS_PER_MIN:
            _reject[sym] = f"only Rs {rs_per_min / 1e5:.1f} lakh/min"
            continue
        f["liquid"] = f.get("liquid", 0) + 1

        first = _first.setdefault(sym, now_hms)
        # RANKED BY FRESHNESS, not by size. A stock that cleared its Supertrend
        # this morning is tradeable; one that cleared it last week is not, and
        # sorting by % change puts the stale ones on top.
        above_st = (ltp / d["st"] - 1) * 100 if d.get("st") else 0.0
        age_min = (t - _sec(first)) / 60.0
        fresh = round(max(0.0, 20.0 - age_min) + min(day_pct, 8.0) * 0.5
                      + min(volx or 0, 6.0) * 0.4, 2)
        cand.append({
            "sym": sym, "sid": str(sid), "price": round(ltp, 2),
            "day_pct": round(day_pct, 2),
            "from_open": round((ltp / op - 1) * 100, 2),
            "rsi": (round(d["rsi"], 1) if d["rsi"] is not None else None),
            "macd_h": (round(d["macd"], 2) if d["macd"] is not None else None),
            "st": (round(d["st"], 1) if d["st"] is not None else None),
            "above_st": round(above_st, 2),
            "vol_x": (round(volx, 1) if volx else None),
            "tover_cr": round(ltp * dayvol / 1e7, 2),
            "sh_min": int(dayvol / mins_open),
            "first_seen": first, "ts": now_hms, "fresh": fresh,
        })

    cand.sort(key=lambda r: -r["fresh"])
    out = cand[:TOP_N]
    with _lock:
        _state["rows"] = out
        _state["ts"] = now_hms
        _state["scanned"] = scanned
        _state["qualified"] = len(cand)
        _state["funnel"] = dict(f)
        if out:
            _state["why_empty"] = None
        elif not _daily:
            _state["why_empty"] = ("daily values not loaded yet -- RSI, MACD and "
                                   "Supertrend are fetched once before the open")
        elif scanned == 0:
            _state["why_empty"] = (f"BROKEN: {len(snap)} quotes arrived but none "
                                   f"matched an F&O stock with daily values.")
        elif f["day1"] == 0:
            _state["why_empty"] = (f"{scanned} F&O stocks scanned, none is up "
                                   f"{MIN_DAY_PCT:.0f}% on the day and above its open")
        elif f["trend"] == 0:
            _state["why_empty"] = (f"{f['day1']} are up {MIN_DAY_PCT:.0f}%+, but none "
                                   f"clears RSI {RSI_MIN:.0f}, MACD and Supertrend together")
        else:
            _state["why_empty"] = (f"{f['trend']} pass the trend filters, but none is "
                                   f"trading {UNUSUAL_X}x its normal volume")
        _state["err"] = None
    if out:
        _record(out)
    return out


_LOGGED = ("sym", "price", "day_pct", "from_open", "rsi", "macd_h", "st",
           "above_st", "vol_x", "tover_cr", "sh_min", "fresh", "first_seen")


def _record(rows_):
    try:
        p = LOGDIR / f"scanx_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now().strftime("%H:%M:%S"),
                                 "rows": [{k: r.get(k) for k in _LOGGED}
                                          for r in rows_]},
                                separators=(",", ":")) + "\n")
    except Exception as e:
        with _lock:
            _state["err"] = f"log write failed: {type(e).__name__} {str(e)[:50]}"
