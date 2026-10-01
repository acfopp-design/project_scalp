"""
Movers_alarm.py -- the 1-MIN ALARM.

WHY THIS EXISTS (the Motherson case, 07-Aug-2026)
    09:00-09:14 pre-market   Rs 155.17   +0.34%    <- flat, nothing to see
    09:15 open               Rs 157.00   +1.53%
    09:16                    Rs 161.09   +3.93%
    09:17                    Rs 163.17   +5.27%
    Rs 51 CRORE traded in those two minutes, against Rs 617 crore for the WHOLE
    previous session. News feed for the day: one Moneycontrol headline saying
    "shares climb 2.04%" -- a reporter describing the move after it happened.

    So there was NO pre-market signal. The pre-open price was flat and there was
    no news. Nothing before 09:15 could have told you. Worse, the pre-open
    scanner never even looked at Motherson: it only checked the 96 names already
    sitting on the gainer/loser lists, and Motherson was flat, so it was invisible.

    What WAS available: at 09:16:05, sixty-five seconds after the bell, the stock
    was +2.6% from its own open on Rs 6.5 crore in a single minute. That is not a
    prediction, it is a detection -- and it is catchable.

    This module does that and nothing else.

WHAT IT MEASURES -- deliberately only two things
    1. move from TODAY'S OPEN.  Not day %. Day % hides an intraday ignition
       behind yesterday's gap: a stock that gapped +4% and has since done
       nothing looks identical to one that opened flat and is ripping right now.
    2. money traded in the LAST ~60 SECONDS. Rupees, never share count -- 200k
       shares is Rs 2 crore in a Rs 100 stock and Rs 98 crore in HAL.

    News score, order-book bias, MACD, moving averages are all EXCLUDED on
    purpose. Every one of them is slower than the thing being caught.

HONEST LIMITS -- read these before trusting the list
    * You are buying AFTER the first move, not before it. Motherson had already
      done +2.6% by the time this would have flagged it. You are catching the
      tail, and the first part belongs to whoever moved it.
    * A flag is not a trade. Motherson then chopped between Rs 161 and Rs 163 for
      forty minutes. The clean scalp was ONE minute.
    * This cannot and does not predict anything. If a stock ignites with no
      warning, the earliest possible knowledge is ~60 seconds after it starts.
"""
import csv
import threading
import time
from datetime import datetime
from pathlib import Path

import Opus_engine as engine
import Opus_quotes_v3 as q

IST = engine.IST
HERE = Path(__file__).resolve().parent
MASTER = HERE / "security_id_list.csv"

# ---- tuning -------------------------------------------------------------
SWEEP_SEC = 5             # how often to re-quote the universe (SHARED -- see snapshot())
WINDOW_SEC = 60           # "the last minute"
# ---- QUALITY GATES ------------------------------------------------------
# These were deliberately loosened once and produced 59 rows, which is a feed
# you cannot act on, not a signal. A scalper can watch maybe 5-10 names. So the
# floors below are set where they cut hard: Rs 2 Cr in a single minute is real
# institutional participation, not a retail flurry; Rs 25 Cr on the day is the
# liquidity you need to leave a position the moment it is green.
MIN_VAL_1M = 2_00_00_000      # Rs 2 Cr traded in the LAST MINUTE  (was Rs 50 L)
MIN_MOVE_OPEN = 1.5           # % from today's open               (was 1.0)
MIN_DAY_TOVER = 25_00_00_000  # Rs 25 Cr on the day               (was Rs 5 Cr)
MIN_PCT_60S = 0.10            # must still be MOVING in the last 60s, not stalling
MIN_QUALITY = 55              # 0-100 composite floor -- see quality()
UC_TOUCH = 0.999          # at/over upper circuit -> cannot buy
LC_TOUCH = 1.001          # at/under lower circuit -> cannot short
MAX_ROWS = 10             # a shortlist you can actually watch (was 80)
# Snapshots retained. At SWEEP_SEC=5 this is 100 seconds of history -- the old
# value of 10 gave only 50s, which is LESS than the 60-second window everything
# here measures over, so the lookback silently fell short of its own definition.
HIST = 20

_lock = threading.Lock()
_snaps = []               # [(epoch, {sid: (ltp, vol)})]
_uni = None               # {sid: sym}  EQ series only
_last = {"ts": 0.0, "rows": [], "err": None, "swept": 0, "universe": 0}


def universe(log=lambda m: None):
    """Every NSE cash stock on the EQ series.

    SERIES MATTERS. The scanx lists filter on Instrument=EQUITY, which happily
    includes BE / BZ / SM / ST. BE is trade-for-trade: the exchange PROHIBITS
    intraday square-off, so every share bought must go to demat. That is how
    NINSYS (series BE, Rs 21,529 traded in the first two minutes) ended up
    ranked #1 on the board. Building the universe from the master, filtered on
    series, removes that whole class of stock permanently.
    """
    global _uni
    if _uni is not None:
        return _uni
    out = {}
    try:
        with MASTER.open(encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                if (r.get("SEM_EXM_EXCH_ID") == "NSE"
                        and r.get("SEM_INSTRUMENT_NAME") == "EQUITY"
                        and r.get("SEM_SERIES") == "EQ"):
                    try:
                        out[int(r["SEM_SMST_SECURITY_ID"])] = r["SEM_TRADING_SYMBOL"]
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        log(f"alarm: universe load failed {e}")
    _uni = out
    log(f"alarm: universe = {len(out)} NSE EQ-series stocks")
    return out


def market_live(now=None):
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return (now.replace(hour=9, minute=15, second=0)
            <= now <= now.replace(hour=15, minute=30, second=0))


def sweep_window(now=None):
    """When the SWEEPER should be collecting -- from 09:00, not 09:15.

    WHY THIS IS SEPARATE FROM market_live(), 25-Aug-2026
        market_live() means "the market is trading" and eight other places rely
        on exactly that meaning -- the watchlist, Scanner1, the news tab. Moving
        its boundary to catch one more thing would quietly change all of them.

    WHY THE SWEEPER NEEDS AN EARLIER START
        The fast ignition badge compares a price against the same price 90
        seconds earlier. At 09:15 the sweeper had just begun, so there was no
        earlier price to compare against -- and it stayed that way through the
        first minutes of the session.

        Measured on 25-Aug: of 264 card readings, 175 reported "no alarm
        history". Zero reported an error. The rule was never broken; it was
        blind, and blind precisely during the window it exists to serve.
        GENESYS ran 10.5% from 09:16:20 and the badge arrived at 09:22:40.

        Sweeping from 09:00 means fifteen minutes of history is already in
        memory when the bell rings, so ignition can fire on the very first
        move of the day.

    THE COST
        Roughly 6 requests a minute for fifteen minutes, at a time when the
        board's own polling is light and nothing is competing for the quota.
    """
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return (now.replace(hour=9, minute=0, second=0)
            <= now <= now.replace(hour=15, minute=30, second=0))


def sweep(log=lambda m: None):
    """Quote the WHOLE universe and keep the snapshot.

    2,455 stocks / 1000 per request = 3 requests. Dhan's documented limit is
    1000 instruments per call at 1 request/second, so the entire NSE cash market
    costs ~3.5 seconds. Opus_quotes_v3._quote_all goes through the SAME global
    1.15s throttle the board already uses, so this queues behind the board's own
    polling instead of racing it into a 429.
    """
    uni = universe(log)
    if not uni:
        return 0
    sids = [str(s) for s in uni]
    try:
        quotes = q._quote_all(sids, log)
    except Exception as e:
        with _lock:
            _last["err"] = f"{type(e).__name__}: {str(e)[:110]}"
        log(f"alarm: sweep failed {e}")
        return 0
    now = time.time()
    snap = {}
    for sid, d in (quotes or {}).items():
        try:
            lp = float(d.get("last_price") or 0)
            vol = float(d.get("volume") or 0)
            if lp <= 0:
                continue
            oh = d.get("ohlc") or {}
            # index 7 = PREVIOUS CLOSE. Carried so Scanner1/Scanner2 can compute
            # day % off this same snapshot instead of sweeping the market again.
            snap[str(sid)] = (lp, vol, float(oh.get("open") or 0),
                              float(oh.get("high") or 0), float(oh.get("low") or 0),
                              float(d.get("upper_circuit_limit") or 0),
                              float(d.get("lower_circuit_limit") or 0),
                              float(oh.get("close") or 0))
        except (TypeError, ValueError):
            continue
    with _lock:
        _snaps.append((now, snap))
        while len(_snaps) > HIST:
            _snaps.pop(0)
        _last["swept"] = len(snap)
        _last["universe"] = len(uni)
        _last["err"] = None
    return len(snap)


def snapshot():
    """The NEWEST full-market quote snapshot, shared with Scanner1 and Scanner2.

    Returns (epoch, {sid: (ltp, volume, open, high, low, upper_cct, lower_cct,
    prev_close)}) or (None, {}).

    WHY THIS IS SHARED
        The alarm, Scanner1 and Scanner2 all need exactly the same thing: a live
        quote for every EQ stock. Each running its own sweep meant THREE
        identical passes over the market, three times the requests, for one set
        of numbers -- and Dhan allows 1 request/second, shared with the board's
        own polling and the depth strip. Sweeping once and reading it three times
        is what makes a 5-second refresh affordable at all; separately they would
        queue behind the global throttle and each end up SLOWER and staler than
        they are now.
    """
    with _lock:
        if not _snaps:
            return None, {}
        ts, snap = _snaps[-1]
        return ts, snap


def snapshot_age():
    """Seconds since the shared snapshot was taken. Surfaced in the UI so a
    stalled sweep shows up as a visible age rather than silently frozen data."""
    ts, _ = snapshot()
    return None if ts is None else max(0.0, time.time() - ts)


# ---- EARLY MOVERS, straight off the full-universe sweep -------------------
# THE PROBLEM THIS SOLVES, measured on 20-Aug
#   The board's stocks come from Dhan's three scanx lists. A stock only enters
#   those lists AFTER it has moved enough to rank on them, so by the time the
#   board sees it the move is largely over:
#
#       stock         first seen   day% then   peak     already done
#       MYSORPETRO      09:07        +37.69%   37.69%       100%
#       PRESSTONIC      09:07        +25.00%   25.00%       100%
#       ISTLTD          09:16        +15.76%   16.33%        97%
#       GENUSPOWER      09:26         +5.11%    6.44%        79%
#
#   72 stocks were already up >=2% the first time the board saw them. The
#   NO-DIP badge is not at fault -- median pin lag after first sighting is 0
#   seconds and 20 of 37 pinned instantly. The stocks simply arrive too late.
#
#   This sweep already quotes all 2,455 NSE EQ stocks every 5 seconds for the
#   1-MIN ALARM, and every one of those names was visible here from 09:15. So
#   finding them costs nothing extra; the board just has to be told to look.
EARLY_MIN_FROM_OPEN = 2.0     # % above TODAY'S OPEN -- not day %, which hides
                              # an intraday move behind yesterday's gap
EARLY_MAX_DD = 0.6            # % below the session high -- still near the top
EARLY_MIN_TOVER_CR = 3.0      # Rs Cr traded today -- must be exitable
EARLY_MAX = 15                # how many to promote per cycle


def early_movers(limit=EARLY_MAX, exclude=None):
    """Stocks moving NOW that the scanx lists have not surfaced yet.

    Uses `high` straight from the quote -- the exchange's own session high --
    so there is no need to accumulate one by watching, and no blind spot for
    the period before we started watching.

    Returns rows sorted strongest first. Cheap: reads the shared snapshot.
    """
    ts, snap = snapshot()
    if not snap:
        return []
    uni = universe()
    skip = {str(s).upper() for s in (exclude or ())}
    out = []
    for sid, t in snap.items():
        try:
            lp, vol = float(t[0]), float(t[1])
            op, hi = float(t[2]), float(t[3])
            uc = float(t[5]) if len(t) > 5 else 0.0
            if lp <= 0 or op <= 0 or hi <= 0 or vol <= 0:
                continue
            sym = uni.get(int(sid))
            if not sym or sym.upper() in skip:
                continue
            frm = (lp / op - 1.0) * 100.0
            dd = (hi - lp) / hi * 100.0
            tov = vol * lp / 1e7
            if frm < EARLY_MIN_FROM_OPEN or dd > EARLY_MAX_DD or tov < EARLY_MIN_TOVER_CR:
                continue
            # locked at the upper circuit -> cannot be bought, so not a candidate
            if uc > 0 and lp >= uc * UC_TOUCH:
                continue
            pc = float(t[7]) if len(t) > 7 else 0.0
            out.append({"sid": str(sid), "sym": sym, "price": round(lp, 2),
                        "from_open": round(frm, 2), "dd_from_hi": round(dd, 2),
                        "tover_cr": round(tov, 2), "volume": int(vol),
                        # carried so a promoted row ranks on the SAME basis as a
                        # scanx row -- without it every promotion sorts to the
                        # bottom of By Volume and is never seen
                        "day_pct": (round((lp / pc - 1.0) * 100.0, 2) if pc > 0 else None),
                        "score": round(frm - dd * 2, 2)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def delta(sid, seconds=30):
    """(pct_move, rupees_traded, actual_window_sec) for one stock over the last
    `seconds`, or (None, None, None).

    Shared so Scanner1 can measure live momentum without sweeping the market
    again -- the alarm's rolling snapshots already hold everything needed.

    The window is chosen by ELAPSED TIME, not by counting back N snapshots.
    Sweeps drift whenever the global 1-req/sec throttle makes them queue behind
    the board's own polling, so a fixed index would silently measure 40s on a
    quiet cycle and 90s on a busy one, and the numbers would not be comparable
    between stocks.
    """
    sid = str(sid)
    with _lock:
        if len(_snaps) < 2:
            return None, None, None
        now_t, cur = _snaps[-1]
        c = cur.get(sid)
        if not c:
            return None, None, None
        best, best_gap = None, None
        for ts, snap in _snaps[:-1]:
            if sid not in snap:
                continue
            gap = now_t - ts
            if gap < seconds * 0.5:          # too recent to be a real window
                continue
            d = abs(gap - seconds)
            if best_gap is None or d < best_gap:
                best, best_gap = (ts, snap[sid]), d
        if not best:
            return None, None, None
    ref_t, p = best
    span = max(1.0, now_t - ref_t)
    lp, vol = float(c[0]), float(c[1])
    plp, pvol = float(p[0]), float(p[1])
    if lp <= 0 or plp <= 0:
        return None, None, None
    pct = (lp / plp - 1.0) * 100.0
    dvol = max(0.0, vol - pvol)
    # normalise to a true `seconds` window so a 48s and a 71s gap compare fairly
    rupees = dvol * lp * (seconds / span)
    return pct, rupees, int(span)


def _ref_snapshot(now_t):
    """The snapshot closest to WINDOW_SEC ago -- the 'one minute back' baseline.

    Picked by actual elapsed time, not by index. Sweeps drift when the throttle
    makes us wait behind the board's own quote calls, so counting back N
    snapshots would silently measure a 40s or a 90s window depending on load.
    """
    best, best_gap = None, None
    for ts, snap in _snaps[:-1]:
        gap = now_t - ts
        if gap < WINDOW_SEC * 0.5:
            continue
        d = abs(gap - WINDOW_SEC)
        if best_gap is None or d < best_gap:
            best, best_gap = (ts, snap), d
    return best


def compute(log=lambda m: None):
    """Rank whatever is igniting RIGHT NOW. Newest snapshot vs ~60s ago."""
    uni = universe(log)
    with _lock:
        if len(_snaps) < 2:
            return list(_last["rows"]), _last["err"]
        now_t, cur = _snaps[-1]
        ref = _ref_snapshot(now_t)
    if not ref:
        return list(_last["rows"]), _last["err"]
    ref_t, prev = ref
    span = max(1.0, now_t - ref_t)
    rows = []
    for sid, c in cur.items():
        lp, vol, op, hi, lo, uc, lc = c[:7]
        p = prev.get(sid)
        if not p or op <= 0:
            continue
        plp, pvol = p[0], p[1]
        dvol = vol - pvol
        if dvol <= 0:
            continue
        val_1m = dvol * lp * (WINDOW_SEC / span)      # normalise to a true minute
        if val_1m < MIN_VAL_1M:
            continue
        day_tover = vol * lp
        if day_tover < MIN_DAY_TOVER:
            continue
        pct_open = (lp / op - 1.0) * 100.0
        if abs(pct_open) < MIN_MOVE_OPEN:
            continue
        pct_60 = (lp / plp - 1.0) * 100.0 if plp > 0 else 0.0
        up = pct_open > 0
        # circuit: you cannot buy a locked upper circuit or short a locked lower
        blocked = bool((up and uc > 0 and lp >= uc * UC_TOUCH)
                       or ((not up) and lc > 0 and lp <= lc * LC_TOUCH))
        if blocked:
            continue
        if (pct_60 if up else -pct_60) < MIN_PCT_60S:
            continue                                  # stalled -- the ignition is over
        rng = (hi - lo)
        at_edge = None
        if rng > 0:
            at_edge = round((lp - lo) / rng, 3)       # 1.0 = at day's high
        # ---- QUALITY 0-100 -------------------------------------------------
        # Four things, all of which must be true of a scalp worth taking:
        #   money    -- is real size trading RIGHT NOW
        #   thrust   -- is it still moving in the last 60s, not drifting
        #   depth    -- can you get out (day turnover)
        #   position -- is it at the working end of its own range
        money = min(val_1m / 8e7, 1.0) * 38           # Rs 8 Cr/min saturates
        thrust = min(abs(pct_60) / 0.6, 1.0) * 27
        depth = min(day_tover / 2e9, 1.0) * 20        # Rs 200 Cr saturates
        edgep = at_edge if at_edge is not None else 0.5
        pos = (edgep if up else (1.0 - edgep)) * 15
        qual = int(round(max(0, min(100, money + thrust + depth + pos))))
        if qual < MIN_QUALITY:
            continue
        rows.append({
            "quality": qual,
            "q_money": int(money), "q_thrust": int(thrust),
            "q_depth": int(depth), "q_pos": int(pos),
            "sym": uni.get(int(sid), sid), "sid": str(sid),
            "price": round(lp, 2),
            "open": round(op, 2),
            "pct_open": round(pct_open, 2),
            "pct_60s": round(pct_60, 2),
            "val_1m_cr": round(val_1m / 1e7, 2),
            "tover_cr": round(day_tover / 1e7, 1),
            "edge": at_edge,
            "dir": "UP" if up else "DOWN",
            "window_s": int(span),
        })
    # UP block first, then DOWN. Inside each, by QUALITY -- not raw rupees, so a
    # huge but stalling name cannot outrank a smaller one that is actually moving.
    rows.sort(key=lambda r: (0 if r["dir"] == "UP" else 1, -r["quality"]))
    rows = rows[:MAX_ROWS]
    with _lock:
        _last["rows"] = rows
        _last["ts"] = time.time()
    return rows, _last["err"]


def summary():
    with _lock:
        rows = list(_last["rows"])
        return {
            "ts": datetime.now(IST).strftime("%H:%M:%S"),
            "live": market_live(),
            "universe": _last["universe"],
            "swept": _last["swept"],
            "shown": len(rows),
            "up": sum(1 for r in rows if r["dir"] == "UP"),
            "down": sum(1 for r in rows if r["dir"] == "DOWN"),
            "window_s": rows[0]["window_s"] if rows else WINDOW_SEC,
            "err": _last["err"],
            "min_val_cr": MIN_VAL_1M / 1e7,
            "min_move": MIN_MOVE_OPEN,
            "min_tover_cr": MIN_DAY_TOVER / 1e7,
            "min_quality": MIN_QUALITY,
            "max_rows": MAX_ROWS,
        }


def loop(log=lambda m: None, stop=None):
    """Background sweeper.

    TWO WINDOWS, DELIBERATELY DIFFERENT -- 25-Aug-2026.

        SWEEPING starts at 09:00, during the pre-open auction, purely to build
        the rolling price history that the fast ignition badge compares against.
        Without it there is nothing to compare a 09:16 price WITH, and the badge
        stays blind through the opening minutes.

        THE ALARM'S OWN OUTPUT still waits for 09:15, because compute() measures
        percent-from-OPEN and there is no open price before the bell. Firing on
        that would produce exactly the meaningless numbers the original 09:15
        gate was written to prevent. That reasoning was right; it was only being
        applied to one step too many.
    """
    log("alarm: loop started")
    announced_pre = False
    while not (stop and stop.is_set()):
        try:
            if sweep_window():
                n = sweep(log)
                if n and market_live():
                    rows, _ = compute(log)
                    if rows:
                        top = rows[0]
                        log(f"alarm: {len(rows)} firing | top {top['sym']} "
                            f"{top['pct_open']:+}% from open, Rs {top['val_1m_cr']}Cr/min")
                elif n and not announced_pre:
                    announced_pre = True
                    log(f"alarm: pre-open sweeping started ({n} quotes) -- building "
                        f"history so the ignition badge can fire at 09:15")
            else:
                announced_pre = False
                with _lock:
                    _snaps.clear()
                    _last["rows"] = []
        except Exception as e:
            log(f"alarm: {type(e).__name__} {str(e)[:120]}")
        time.sleep(SWEEP_SEC)
