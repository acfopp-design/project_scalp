"""surge.py -- find the move ourselves, instead of waiting for Dhan's board.

WHY THIS EXISTS
    30-Sep. The engine could only trade a stock once Dhan's board had flagged
    it, and never before. Dhan flags a stock AFTER it moves, so the engine was
    structurally late. Two examples from the same day, both verified:

        TVSELECT    signals at 10:13 (1.76), 10:15 (2.29), 10:17 (2.47)
                    board flagged it 10:20:32 -- the whole 389 -> 407 run
                    happened while the engine was blind to it
        VALIANTORG  signal at 10:07 scoring 2.38
                    board flagged it 10:13:19 -- the 419 -> 455 run missed

    The signal logic was right both times. It simply was not allowed to look.

WHAT THIS DOES
    Dhan allows 1000 instruments in one /marketfeed/quote request at 1 req/sec.
    The whole NSE equity universe is ~2000 names, so the entire market can be
    scanned every ~2 seconds from two requests. This module does that, ranks
    every stock on its OWN behaviour, and writes the ones that are moving to
    logs/SURGE_<day>.json.

THE RANKING -- all relative to the stock itself, never to other stocks
    vol_rate   traded volume per second in the last window, measured against
               that stock's own median rate so far today. A Rs 50 stock and a
               Rs 5000 stock are judged the same way.
    day_move   % from today's open.
    range_pos  where the last price sits in the day's range (0-100).
    A name is flagged when its volume rate is VOL_X times its own median AND it
    has moved at least MIN_MOVE from the open.

CAUSALITY
    The moment we first flag a stock is written once and never revised, into
    the same logs/FIRST_SEEN_<day>.json the rest of the engine already honours.
    So a stock we flag at 10:13 cannot be traded at 10:07 -- the replay stays
    honest even though the universe is now wider.

OFF BY DEFAULT. env.txt SURGE_SCAN=YES turns it on.
"""
import json, os, statistics, threading, time
from datetime import datetime
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
OUT = HERE / "logs" / "SURGE_{}.json"

POLL = 2.0          # seconds between full-market sweeps
VOL_X = 3.0         # volume rate must be this many times the stock's own median
MIN_MOVE = 1.0      # and it must be at least this % from today's open
MIN_TOVER = 300000  # and this many rupees traded in the window -- skip illiquid
WARM_SAMPLES = 5    # need this many readings before a median means anything
OPEN_MOVE = 2.0     # opening mode: % from open that counts as a surge
OPEN_TOVER = 10000000   # ...on at least this much turnover today (Rs 1 cr)
OPEN_T, CLOSE_T = "09:15:00", "15:30:00"

_st = {"thread": None, "stop": False, "hist": {}, "flagged": {},
       "sweeps": 0, "errs": 0, "last": None}
_lock = threading.Lock()


def _enabled():
    try:
        import broker
        return broker._plain().get("SURGE_SCAN", "").upper() in ("YES", "TRUE", "1")
    except Exception:
        return False


def rank(sym, px, cumvol, dayopen, dayhigh, daylow, now, hist):
    """One stock's surge state. Pure function -- unit-testable without network.

    Returns (score, detail) or (None, reason).
    """
    h = hist.setdefault(sym, {"t": [], "v": [], "rate": []})
    if h["t"] and now <= h["t"][-1]:
        return None, "stale"
    if h["t"]:
        dt = now - h["t"][-1]
        dv = cumvol - h["v"][-1]
        if dt > 0 and dv >= 0:
            h["rate"].append(dv / dt)
            if len(h["rate"]) > 240:
                h["rate"].pop(0)
    h["t"].append(now)
    h["v"].append(cumvol)
    if len(h["t"]) > 240:
        h["t"].pop(0); h["v"].pop(0)

    # OPENING MODE, 1-Oct. The volx path needs WARM_SAMPLES readings before a
    # median means anything, so the scanner's first flags did not appear until
    # 09:17:34 -- it was blind for the first two and a half minutes, which is
    # where the day's sharpest moves happen. Measured the same morning:
    #     TARIL  valid signal 09:18:00, score 2.86, price 287.00
    #            scanner recorded it at 09:19:04 -- 64 seconds too late
    #            next qualifying signal 09:25:30, entered at 293.40
    # Six rupees of a move lost to our own warm-up.
    #
    # Before the baseline exists, judge on absolutes instead, which need no
    # history at all: a real % move from the open, carried on real rupees of
    # turnover. A stock up 3% in three minutes on a crore of turnover is a
    # surge whatever its past looked like.
    if len(h["rate"]) < WARM_SAMPLES:
        move0 = (px / dayopen - 1) * 100 if dayopen else 0.0
        tover0 = cumvol * px
        if abs(move0) >= OPEN_MOVE and tover0 >= OPEN_TOVER:
            rng0 = ((dayhigh - daylow) or 1e-9)
            return abs(move0) * 2.0, {"volx": None, "move": round(move0, 2),
                                      "range_pos": round((px - daylow) / rng0 * 100, 1),
                                      "px": px, "opening": True,
                                      "side": "LONG" if move0 > 0 else "SHORT"}
        return None, "warming"
    med = statistics.median(h["rate"])
    cur = h["rate"][-1]
    if med <= 0:
        return None, "no volume"
    volx = cur / med
    move = (px / dayopen - 1) * 100 if dayopen else 0.0
    tover = cur * px * POLL
    if tover < MIN_TOVER:
        return None, "illiquid"
    if volx < VOL_X:
        return None, "no volume surge"
    if abs(move) < MIN_MOVE:
        return None, "not moving"
    rng = (dayhigh - daylow) or 1e-9
    pos = (px - daylow) / rng * 100
    score = volx * (abs(move) ** 0.5)
    return score, {"volx": round(volx, 2), "move": round(move, 2),
                   "range_pos": round(pos, 1), "px": px,
                   "side": "LONG" if move > 0 else "SHORT"}


def _note_first_seen(day, syms, hm):
    """Write once, never revise -- the same record the engine already honours."""
    try:
        import funnel
        funnel.first_seen(day, list(syms), now=hm)
    except Exception:
        pass


def _sweep(day, log):
    import Opus_quotes_v3 as QU
    uni = QU._eq_universe()
    sids = list(uni.keys())
    q = QU._quote_all(sids, lambda m: None)
    if not q:
        with _lock:
            _st["errs"] += 1
        return
    now = time.time()
    hm = datetime.now().strftime("%H:%M:%S")
    fresh = []
    with _lock:
        for sid, qq in q.items():
            if not isinstance(qq, dict):
                continue
            sym = uni.get(str(sid))
            if not sym:
                continue
            o = (qq.get("ohlc") or {})
            s, d = rank(sym, float(qq.get("last_price") or 0),
                        float(qq.get("volume") or 0),
                        float(o.get("open") or 0), float(o.get("high") or 0),
                        float(o.get("low") or 0), now, _st["hist"])
            if s is None:
                continue
            if sym not in _st["flagged"]:
                fresh.append(sym)
                _st["flagged"][sym] = {"first": hm, "sid": str(sid),
                                       "score": round(s, 2), **d}
            else:
                _st["flagged"][sym].update(score=round(s, 2), **d)
        _st["sweeps"] += 1
        _st["last"] = hm
        snap = dict(_st["flagged"])
    if fresh:
        _note_first_seen(day, fresh, hm)
        log("surge: %d new (%s)" % (len(fresh), ", ".join(sorted(fresh)[:6])))
    try:
        p = Path(str(OUT).format(day))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(snap, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _loop(day, log):
    log("surge: scanning the whole NSE equity universe every %.0fs" % POLL)
    while not _st["stop"]:
        t0 = time.time()
        try:
            hm = datetime.now().strftime("%H:%M:%S")
            if OPEN_T <= hm <= CLOSE_T:
                _sweep(day, log)
        except Exception as e:
            with _lock:
                _st["errs"] += 1
            log("surge error: %s %s" % (type(e).__name__, str(e)[:70]))
        time.sleep(max(0.0, POLL - (time.time() - t0)))
    log("surge: stopped")


def start(day, log=print):
    if not _enabled():
        return None
    if _st["thread"] and _st["thread"].is_alive():
        return _st["thread"]
    _st["stop"] = False
    th = threading.Thread(target=_loop, args=(day, log), daemon=True)
    _st["thread"] = th
    th.start()
    return th


def stop():
    _st["stop"] = True


def names(day):
    """Everything we have flagged today."""
    return set(rows(day))


def rows(day):
    """{sym: {first, sid, score, volx, move, range_pos, px, side}} for today."""
    try:
        return json.loads(Path(str(OUT).format(day)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stats():
    with _lock:
        return {"sweeps": _st["sweeps"], "errs": _st["errs"],
                "last": _st["last"], "flagged": len(_st["flagged"])}


if __name__ == "__main__":
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    try:
        d = json.loads(Path(str(OUT).format(day)).read_text(encoding="utf-8"))
    except Exception:
        d = {}
    print("surge flags for %s: %d names   (enabled: %s)" % (day, len(d), _enabled()))
    for s, v in sorted(d.items(), key=lambda kv: -kv[1].get("score", 0))[:20]:
        print("  %-12s first %s  score %-7s volx %-6s move %+6.2f%%  %s"
              % (s, v.get("first"), v.get("score"), v.get("volx"), v.get("move"), v.get("side")))
