"""quote_feed.py -- build our own 30-second bars from one bulk quote per second.

WHY THIS EXISTS
    Sri pays for Dhan's Data API. Dhan's own documentation says:

        "You can fetch upto 1000 instruments in single API request with rate
         limit of 1 request per second"

    The engine was instead calling a PER-SYMBOL historical-candle endpoint,
    once per name, ~150 times a sweep. Measured on 30-Sep that is a ~43s sweep
    on a 90s cadence, so every price the engine reasoned about was 20-68
    seconds old, the worst case ran to ~180s, and Dhan answered 37 requests
    with HTTP 429. At 09:15 the funnel held ZERO names until 09:18:06 -- the
    open did not exist for the engine at all.

    None of that was a Dhan limit, a plan limit or a rate limit. It was 150
    calls doing the work of one.

WHAT THIS DOES
    One /v2/marketfeed/quote call per second covering every watchlist name,
    accumulated into 30-second buckets that we close ourselves and write in
    exactly the shape live_shadow already reads:

        {"o":[...], "h":[...], "l":[...], "c":[...], "v":[...], "t":[...]}

    Nothing downstream needs to change. Bars land ~1 second after the half
    minute closes instead of a minute later.

WHAT IT DOES NOT DO
    It does not predict anything. It tells the engine where the price IS, not
    where it is going. Those are different problems and only the first one is
    fixed here.

VOLUME
    The quote carries CUMULATIVE volume for the day, so a bar's own volume is
    the difference between successive readings. The first reading after start
    has no predecessor, so that bucket's volume is left at 0 rather than
    reporting the whole day's volume as one bar's -- an inflated first bar
    would score a false 1.0 on volume percentile and invent a signal.
"""
import json, os, threading, time
from datetime import datetime
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
OUT = HERE / "logs" / "tape_fast"
BUCKET = 30                 # seconds per bar, matching the rest of the engine
POLL = 1.0                  # Dhan allows 1 quote request per second
OPEN_T, CLOSE_T = "09:15:00", "15:30:30"

_state = {"thread": None, "stop": False, "bars": {}, "cur": {}, "lastvol": {},
          "polls": 0, "errs": 0, "last_ok": None}
_lock = threading.Lock()


def _bucket_start(ts):
    return int(ts) - (int(ts) % BUCKET)


def _flush(sym, day):
    """Append the finished bucket for `sym` to its file."""
    b = _state["bars"].get(sym)
    if not b:
        return
    d = OUT / day
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / f"{sym}.json").write_text(
            json.dumps(b, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _ingest(day, sym, px, cumvol, now):
    """Fold one quote reading into this symbol's current 30s bucket."""
    if not px:
        return
    bs = _bucket_start(now)
    cur = _state["cur"].get(sym)
    prev = _state["lastvol"].get(sym)
    step = 0
    if prev is not None and cumvol is not None and cumvol >= prev:
        step = cumvol - prev
    if cumvol is not None:
        _state["lastvol"][sym] = cumvol

    if cur is None or cur["t"] != bs:
        if cur is not None:                       # the previous bucket closed
            b = _state["bars"].setdefault(
                sym, {"o": [], "h": [], "l": [], "c": [], "v": [], "t": []})
            for k in ("o", "h", "l", "c", "v", "t"):
                b[k].append(cur[k])
            _flush(sym, day)
        _state["cur"][sym] = {"o": px, "h": px, "l": px, "c": px,
                              "v": step, "t": bs}
        return
    cur["h"] = max(cur["h"], px)
    cur["l"] = min(cur["l"], px)
    cur["c"] = px
    cur["v"] += step


def _loop(day, symbols_fn, log):
    import Opus_quotes_v3 as QU
    import leverage as LV
    sidmap = {}
    log("quote_feed: started -- one bulk quote per second, %ds bars" % BUCKET)
    while not _state["stop"]:
        t0 = time.time()
        try:
            hms = datetime.now().strftime("%H:%M:%S")
            if hms < OPEN_T or hms > CLOSE_T:
                time.sleep(2)
                continue
            syms = list(symbols_fn() or [])
            for s in syms:
                if s not in sidmap:
                    sid, _ = LV.sec_lookup(s)
                    sidmap[s] = str(sid) if sid else None
            sids = [sidmap[s] for s in syms if sidmap.get(s)]
            if not sids:
                time.sleep(POLL)
                continue
            q = QU._quote_all(sids, lambda m: None)
            with _lock:
                _state["polls"] += 1
                if q:
                    _state["last_ok"] = hms
                else:
                    _state["errs"] += 1
                now = time.time()
                back = {sidmap[s]: s for s in syms if sidmap.get(s)}
                for sid, qq in (q or {}).items():
                    sym = back.get(str(sid))
                    if not sym or not isinstance(qq, dict):
                        continue
                    _ingest(day, sym, float(qq.get("last_price") or 0),
                            float(qq.get("volume") or 0), now)
        except Exception as e:
            with _lock:
                _state["errs"] += 1
            log("quote_feed error: %s %s" % (type(e).__name__, str(e)[:70]))
        time.sleep(max(0.0, POLL - (time.time() - t0)))
    log("quote_feed: stopped")


def start(day, symbols_fn, log=print):
    """Begin polling. `symbols_fn()` returns the current watchlist."""
    if _state["thread"] and _state["thread"].is_alive():
        return _state["thread"]
    _state["stop"] = False
    th = threading.Thread(target=_loop, args=(day, symbols_fn, log), daemon=True)
    _state["thread"] = th
    th.start()
    return th


def stop():
    _state["stop"] = True


def stats():
    with _lock:
        return {"polls": _state["polls"], "errs": _state["errs"],
                "last_ok": _state["last_ok"], "symbols": len(_state["cur"])}


def load(day):
    """{sym: {o,h,l,c,v,t}} for whatever this feed has built today."""
    d = OUT / day
    out = {}
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


if __name__ == "__main__":
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    b = load(day)
    print("tape_fast for %s: %d symbols" % (day, len(b)))
    for s, v in list(b.items())[:5]:
        print("  %-12s %d bars  last close %s" % (s, len(v.get("c") or []),
                                                  (v.get("c") or [None])[-1]))
