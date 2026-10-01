"""
Movers_scanner1.py -- "Scanner1 / UC STOCKS".

Reimplements the Chartink screener at /screener/uc-stocks-118, read directly
from its filter panel on 13-Aug-2026:

    Daily Close       >=  1 day ago Close
    Daily % Change    >   1
    Daily Low         >   20
    Daily Volume      >   200000

A NOTE ON THE NAME, because it matters before you trade from it:
    The screener is called "UC_Stocks", which reads as upper-circuit stocks.
    Its published conditions contain NO upper-circuit test of any kind. What it
    actually selects is "up more than 1% today, priced above Rs 20, on at least
    200k shares" -- on the day it was captured that was 238 stocks. It is a
    broad bullish filter, not a circuit screener. Implemented faithfully here,
    but do not expect a list of stocks locked at upper circuit.

WHY THE PREVIOUS VERSION WENT
    Scanner1 used to hold a bespoke "pullback after ignition" idea. It was
    measured at -0.116% per trade over 185 trades -- better than chasing
    breakouts (-0.480%) but still losing, and barely ahead of random entry
    (-0.129%). Replaced wholesale rather than tuned.

DATA
    Everything here is DAILY. Today's values come from the shared quote sweep
    (price, open, previous close, day volume, day low). Nothing needs the
    30-second bar builder, so this populates from the first sweep after 09:15.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import Opus_engine as engine

try:
    import Movers_alarm as alarm
except Exception:                                  # pragma: no cover
    alarm = None

IST = engine.IST
HERE = Path(__file__).resolve().parent

# ---- Chartink UC_Stocks conditions ---------------------------------------
MIN_DAY_CHG_PCT = 1.0          # Daily % Change > 1
MIN_DAY_LOW = 20.0             # Daily Low > 20
MIN_DAY_VOLUME = 200_000       # Daily Volume > 200000
# ---- ours, not Chartink's ------------------------------------------------
MIN_TOVER_CR = 1.0             # a stock you cannot exit is not a candidate
UC_TOUCH = 0.999               # at/over this fraction of the upper circuit = locked
# ---- momentum scoring ----------------------------------------------------
# The four Chartink conditions are a wide net -- "up 1% on 200k volume" returned
# ~100 stocks. Day % cannot rank them, because it measures DISTANCE TRAVELLED,
# not whether the stock is still moving: a name up 15% that peaked at 09:20 and
# has been flat since outranks one up 3% that is moving right now. These weights
# score LIVE momentum instead.
#
# WINDOW = 30 SECONDS.
#   Every threshold below that measures MOVEMENT is expressed per-window, so
#   changing MOM_WINDOW_SEC alone is NOT enough -- THRUST_FULL and STALE_THRUST
#   are both halved from their 60s values, and the money baseline is rescaled at
#   the point of use (see own_rate). A 30s window reacts roughly half a minute
#   sooner than a 60s one and is correspondingly about twice as jumpy: a single
#   bad print is 1/30th of the window rather than 1/60th, so expect the top of
#   the list to reshuffle more often.
MOM_WINDOW_SEC = 30            # the momentum measurement window, in seconds
W_THRUST = 30.0                # % moved in the window          -- is it moving NOW
W_MONEY = 25.0                 # window rupees vs its OWN rate  -- money flowing NOW
W_RANGE = 20.0                 # where it sits in today's range -- at highs or faded
W_OPEN = 15.0                  # % above today's open           -- today's own drive
W_LIQ = 10.0                   # day turnover                   -- can you exit
THRUST_FULL = 0.25             # % in 30s that scores full marks (was 0.5 at 60s)
MONEY_FULL = 4.0               # x its own average rate = full marks (window-relative)
OPEN_FULL = 4.0                # % above open that scores full marks
LIQ_FULL_CR = 50.0             # Rs Cr day turnover that scores full marks
# STALE = the move has STOPPED. Defined by activity, not by position.
#   The first version also required the stock to have faded from its highs, and
#   so missed the commonest case: a name up 18% on the day, sitting at 86% of
#   its range, moving 0.00% in the window on a tenth of its normal rate.
#   That is parked, not fading -- and it is exactly what you do not want to buy.
STALE_THRUST = 0.025           # moving less than this % in 30s (was 0.05 at 60s) ...
STALE_MONEY_X = 0.5            # ... and trading below half its own normal rate
SCAN_SEC = 5
MAX_ROWS = 120                 # the raw list is wide; the UI filters/sorts it

_last_log_t = 0.0
_lock = threading.Lock()
_state = {"ts": 0.0, "rows": [], "err": None, "universe": 0,
          "scanning": False, "snap_age": None, "passed": 0}


def mins_since_open(now=None):
    """Minutes elapsed since 09:15, floored at 1 so it can never divide by zero."""
    n = now or datetime.now(IST)
    return max(1.0, (n - n.replace(hour=9, minute=15, second=0,
                                   microsecond=0)).total_seconds() / 60.0)


def momentum(sid, lp, vol, op, hi, lo, mins=None):
    """The 0-100 live-momentum score, as ONE function.

    Lives here rather than in the board because Scanner1 defined it, but the
    board, the scanners and every card grid now sort by it -- and a score that
    meant slightly different things on different tabs would be worse than no
    score at all. Both callers go through this, so there is exactly one formula.

    Returns a dict, or None when the stock is not in the rolling snapshots yet.
    """
    if alarm is None:
        return None
    try:
        lp, vol = float(lp), float(vol)
        op = float(op or 0)
        hi, lo = float(hi or 0), float(lo or 0)
    except (TypeError, ValueError):
        return None
    if lp <= 0 or vol <= 0:
        return None
    mins = mins or mins_since_open()
    rng = hi - lo
    range_pos = ((lp - lo) / rng) if rng > 0 else 0.5
    from_open = ((lp / op - 1.0) * 100.0) if op > 0 else 0.0
    tov = vol * lp / 1e7
    thrust, rupees, win = alarm.delta(sid, MOM_WINDOW_SEC)
    # baseline rescaled to the window -- see the long note in scan()
    per_min = (vol * lp) / mins if mins > 0 else None
    own_rate = (per_min * (MOM_WINDOW_SEC / 60.0)) if per_min else None
    money_x = (rupees / own_rate) if (own_rate and own_rate > 0
                                      and rupees is not None) else None
    s_thrust = (min(max(thrust or 0.0, 0.0) / THRUST_FULL, 1.0) * W_THRUST
                if thrust is not None else 0.0)
    s_money = (min((money_x or 0.0) / MONEY_FULL, 1.0) * W_MONEY
               if money_x is not None else 0.0)
    s_range = range_pos * W_RANGE
    s_open = min(max(from_open, 0.0) / OPEN_FULL, 1.0) * W_OPEN
    s_liq = min(tov / LIQ_FULL_CR, 1.0) * W_LIQ
    return {
        "momentum": int(round(s_thrust + s_money + s_range + s_open + s_liq)),
        "stale": bool(thrust is not None and abs(thrust) < STALE_THRUST
                      and money_x is not None and money_x < STALE_MONEY_X),
        "mom_win": MOM_WINDOW_SEC,
        "thrust_pct": round(thrust, 3) if thrust is not None else None,
        "money_cr": round(rupees / 1e7, 2) if rupees is not None else None,
        "money_x": round(money_x, 2) if money_x is not None else None,
        "win_sec": win, "range_pos": round(range_pos, 3) if rng > 0 else None,
        "m_thrust": int(s_thrust), "m_money": int(s_money), "m_range": int(s_range),
        "m_open": int(s_open), "m_liq": int(s_liq),
    }


def scan(log=lambda m: None):
    if alarm is None:
        with _lock:
            _state["err"] = "Movers_alarm (universe/sweep) unavailable"
        return []
    with _lock:
        if _state["scanning"]:
            return list(_state["rows"])
        _state["scanning"] = True
    try:
        uni = alarm.universe(log)
        snap_ts, snap = alarm.snapshot()
        if not snap:
            with _lock:
                _state["err"] = "waiting for the first market sweep"
            return []
        # minutes since the open -- used to turn each stock's cumulative day
        # turnover into an average rupees-per-minute baseline
        _n = datetime.now(IST)
        mins_elapsed = max(1.0, (_n - _n.replace(hour=9, minute=15, second=0,
                                                 microsecond=0)).total_seconds() / 60.0)
        out = []
        for sid, t in snap.items():
            try:
                # (ltp, volume, open, high, low, upper_cct, lower_cct, prev_close)
                lp, vol, op = float(t[0]), float(t[1]), float(t[2])
                hi, lo = float(t[3]), float(t[4])
                uc = float(t[5]) if len(t) > 5 else 0.0
                pc = float(t[7]) if len(t) > 7 else 0.0
                if lp <= 0 or pc <= 0 or vol <= 0:
                    continue
                # ---- Chartink's four conditions, in order ----------------
                if lp < pc:                                   # close >= prev close
                    continue
                chg = (lp / pc - 1.0) * 100.0
                if chg <= MIN_DAY_CHG_PCT:                    # % change > 1
                    continue
                if lo <= MIN_DAY_LOW:                         # day low > 20
                    continue
                if vol <= MIN_DAY_VOLUME:                     # volume > 200000
                    continue
                tov = vol * lp / 1e7
                if tov < MIN_TOVER_CR:
                    continue
                # AT UPPER CIRCUIT -> DROP IT.
                #   A stock locked at its upper circuit cannot be bought -- there
                #   are no sellers at that price. Showing it is worse than
                #   useless: it occupies a slot you would otherwise be watching a
                #   tradeable name in. Previously these were kept with a warning
                #   badge; there is no reason to see them at all.
                if uc > 0 and lp >= uc * UC_TOUCH:
                    continue
                # how far it still is from the circuit, since the screener is
                # named for this but its filters never test it
                uc_pct = ((uc / lp - 1.0) * 100.0) if uc > 0 else None
                rng = (hi - lo)
                from_open = ((lp / op - 1.0) * 100.0) if op > 0 else 0.0
                # ---- LIVE MOMENTUM ------------------------------------------
                # One shared function, so the board and the scanner cannot drift
                # apart. Costs no extra request -- it reads the alarm's rolling
                # snapshots, which the shared sweep already refreshes.
                mm = momentum(sid, lp, vol, op, hi, lo, mins_elapsed) or {}
                row = dict(mm)
                row.update({
                    "sid": str(sid), "sym": uni.get(int(sid), str(sid)),
                    "price": round(lp, 2), "open": round(op, 2),
                    "prev_close": round(pc, 2),
                    "day_hi": round(hi, 2), "day_lo": round(lo, 2),
                    "day_pct": round(chg, 2),
                    "from_open_pct": round((lp / op - 1.0) * 100.0, 2) if op > 0 else None,
                    "volume": int(vol), "tover_cr": round(tov, 2),
                    "uc": round(uc, 2) if uc > 0 else None,
                    "uc_room_pct": round(uc_pct, 2) if uc_pct is not None else None,
                    "at_uc": False,      # locked names are dropped above
                })
                row.setdefault("momentum", 0)
                row.setdefault("stale", False)
                out.append(row)
            except (TypeError, ValueError):
                continue
        passed = len(out)
        # Default order is MOMENTUM, not day %. Day % ranks by distance already
        # travelled, which puts the most exhausted stock at the top.
        # (NO-DIP pinning is applied later, in the board's /scanner1 endpoint --
        #  `pinned` comes from build_card and is not known here.)
        out.sort(key=lambda r: (r["stale"], -r["momentum"]))
        out = out[:MAX_ROWS]
        with _lock:
            _state.update({"rows": out, "ts": time.time(), "err": None,
                           "universe": len(uni), "passed": passed,
                           "snap_age": round(time.time() - snap_ts, 1) if snap_ts else None})
        log(f"scanner1/UC: {len(uni)} scanned -> {passed} pass -> showing {len(out)}")
        # PERSIST THE LIST.
        #   "Scanner1 does not match the Board" was unanswerable because nothing
        #   recorded what Scanner1 actually showed. One line per scan makes the
        #   comparison a lookup instead of an argument. Throttled to 30s so it
        #   costs a few hundred KB a session, not hundreds of MB.
        global _last_log_t
        if time.time() - _last_log_t >= 30:
            _last_log_t = time.time()
            try:
                d = HERE / "logs" / "movers_board"
                d.mkdir(parents=True, exist_ok=True)
                p = d / f"scanner1_{datetime.now(IST).strftime('%Y%m%d')}.jsonl"
                with p.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                        "universe": len(uni), "passed": passed,
                        "rows": [{k: r.get(k) for k in
                                  ("sym", "sid", "price", "day_pct", "momentum",
                                   "stale", "tover_cr", "range_pos")} for r in out]
                    }, default=str) + "\n")
            except Exception:
                pass
        return out
    finally:
        with _lock:
            _state["scanning"] = False


def rows():
    with _lock:
        return list(_state["rows"])


def summary():
    with _lock:
        return {"ts": datetime.fromtimestamp(_state["ts"], IST).strftime("%H:%M:%S")
                if _state["ts"] else None,
                "universe": _state["universe"], "passed": _state["passed"],
                "shown": len(_state["rows"]), "err": _state["err"],
                "scanning": _state["scanning"], "snap_age": _state["snap_age"],
                "source": "chartink.com/screener/uc-stocks-118",
                "rules": ["Close >= prev Close", "% Change > 1",
                          "Day Low > 20", "Volume > 200,000",
                          f"+ ours: turnover >= Rs {MIN_TOVER_CR:g} Cr",
                          "+ ours: drop anything locked at upper circuit"],
                "mom_win": MOM_WINDOW_SEC,
                "momentum": [
                    f"MOMENTUM 0-100, measured over {MOM_WINDOW_SEC}s = "
                    f"thrust {W_THRUST:g} + money {W_MONEY:g} + range {W_RANGE:g} + "
                    f"from-open {W_OPEN:g} + liquidity {W_LIQ:g}",
                    f"thrust: % moved in the last {MOM_WINDOW_SEC}s "
                    f"({THRUST_FULL:g}% = full marks)",
                    f"money: last {MOM_WINDOW_SEC}s rupees vs the stock's OWN average "
                    f"{MOM_WINDOW_SEC}s ({MONEY_FULL:g}x = full marks)",
                    "range: where it sits between today's low and high",
                    f"from-open: % above today's open ({OPEN_FULL:g}% = full marks)",
                    f"liquidity: day turnover (Rs {LIQ_FULL_CR:g} Cr = full marks)",
                    f"STALE = moving under {STALE_THRUST:g}% in {MOM_WINDOW_SEC}s AND "
                    f"trading below {STALE_MONEY_X:g}x its own rate; sorted to the bottom",
                ]}


def loop(log=lambda m: None, stop=None):
    log("scanner1/UC: loop started")
    while not (stop and stop.is_set()):
        try:
            if alarm is not None and alarm.market_live():
                scan(log)
        except Exception as e:
            log(f"scanner1: {type(e).__name__} {str(e)[:120]}")
        time.sleep(SCAN_SEC)
