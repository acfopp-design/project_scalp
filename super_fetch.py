"""
super_fetch.py -- pull REAL 1-minute bars for the WHOLE NSE EQ universe so the
                  Super Stocks tab can be backtested against what actually
                  happened, not against what the board happened to record.

WHY NOT USE THE LOGS
    A replay was built from board_*.jsonl first (`super_replay.py`) and then
    largely abandoned, for a reason worth writing down:

        The board only records stocks it already decided to show. Backtesting
        a whole-universe finder on the survivors of a narrow filter cannot
        find the thing it was built to find. It is the same circular-
        measurement error made once already on Scanner1 (HANDOVER mistake #3),
        and it would have produced a flattering, useless number.

    Worse, the session OPEN -- which every Super Stocks rule is anchored to --
    is not in the log at all. Only ~40 symbols a day had a trustworthy one.

    Dhan's /charts/intraday serves 1-minute OHLCV for any security id over a
    date range, in ONE request per stock for ALL the days. That gives a true
    open, a true high and low, and real volume, for all 2,455 stocks. It is the
    only honest basis for this backtest.

COST AND COURTESY
    One request per stock, not per stock per day. 2,455 requests total.

    The live board is usually running and shares this Dhan account; two clients
    at full speed is what caused 5,397 rate-limit rejections and a warning from
    Dhan in August. So this fetcher:
      * runs at HALF the board's rate by default,
      * refuses to start inside the trading window unless forced,
      * caches everything to disk, so it is paid for ONCE and every later
        backtest run is free.

RUN IT WITH:  SUPER_BACKTEST.bat
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import Opus_engine as engine                                            # noqa: E402
import Movers_alarm as alarm                             # noqa: E402

IST = engine.IST
CACHE = HERE / "logs" / "movers_board" / "_bars_cache"
CACHE.mkdir(parents=True, exist_ok=True)

WIN_START_MIN = 9 * 60 + 15          # 09:15
WIN_END_MIN = 10 * 60 + 30           # 10:30  -- his window, and only his window
LOOKBACK_DAYS = 14                   # calendar days; ~10 trading sessions

REQ_PER_SEC = 2.0                    # deliberately half of engine's 4.5
_last = [0.0]


def _throttle():
    dt = time.time() - _last[0]
    if dt < 1.0 / REQ_PER_SEC:
        time.sleep(1.0 / REQ_PER_SEC - dt)
    _last[0] = time.time()


def _post(path, body, tries=3):
    """One request, with named failures. `except: pass` has cost this project a
    whole session three times (HANDOVER mistake #2); every failure here is
    returned as a string a human can read."""
    for a in range(tries):
        _throttle()
        req = urllib.request.Request(engine.BASE + path,
                                     data=json.dumps(body).encode(),
                                     headers=engine.head(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode()), None
        except urllib.error.HTTPError as e:
            body_txt = ""
            try:
                body_txt = e.read().decode()[:120]
            except Exception:
                pass
            if e.code in (429, 805) and a < tries - 1:
                time.sleep(2.0 * (a + 1))           # back off, then retry
                continue
            return None, f"HTTP {e.code} {body_txt}"
        except Exception as e:
            if a < tries - 1:
                time.sleep(1.0)
                continue
            return None, f"{type(e).__name__}: {str(e)[:90]}"
    return None, "gave up"


def _slice_days(raw):
    """Dhan's flat arrays -> {'YYYYMMDD': {...bars in HIS window...}, ...}

    prev_close for a session is the LAST close of the previous session present
    in the same response -- which is why the whole range is fetched at once.
    """
    ts = raw.get("timestamp") or []
    if not ts:
        return {}
    o, h, l, c, v = (raw.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    if not (len(o) == len(h) == len(l) == len(c) == len(v) == len(ts)):
        return {}
    days = {}
    order = []
    for i, t in enumerate(ts):
        d = datetime.fromtimestamp(t, IST)
        key = d.strftime("%Y%m%d")
        if key not in days:
            days[key] = {"bars": [], "last_close": None}
            order.append(key)
        mins = d.hour * 60 + d.minute
        days[key]["last_close"] = float(c[i])
        if WIN_START_MIN <= mins <= WIN_END_MIN:
            days[key]["bars"].append((d.hour * 3600 + d.minute * 60 + d.second,
                                      float(o[i]), float(h[i]), float(l[i]),
                                      float(c[i]), float(v[i])))
    out = {}
    for n, key in enumerate(order):
        bars = days[key]["bars"]
        if len(bars) < 20:                       # a session that barely traded
            continue
        bars.sort()
        out[key] = {
            "bars": bars,
            "open": bars[0][1],                  # the 09:15 bar's OPEN = the day's open
            "prev_close": (days[order[n - 1]]["last_close"] if n else None),
        }
    return out


def fetch_all(days_back=LOOKBACK_DAYS, force=False, log=print):
    now = datetime.now(IST)
    trading_now = (now.weekday() < 5
                   and (9 * 60 + 10) <= now.hour * 60 + now.minute <= (15 * 60 + 30))
    if trading_now and not force:
        log("")
        log("  The market is open and the board is probably running on the same")
        log("  Dhan account. Two clients at once is what got this account a")
        log("  rate-limit warning in August, so this will not start now.")
        log("  Run it after 15:30, or with --force if the board is stopped.")
        log("")
        return None

    uni = alarm.universe()
    log(f"  universe: {len(uni)} NSE EQ-series stocks")
    end = now
    start = end - timedelta(days=days_back)
    body_base = {"exchangeSegment": "NSE_EQ", "instrument": "EQUITY", "interval": "1",
                 "fromDate": start.strftime("%Y-%m-%d"), "toDate": end.strftime("%Y-%m-%d")}

    tag = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    store = CACHE / f"bars_{tag}.pkl"
    done = {}
    if store.exists() and not force:
        with store.open("rb") as fh:
            done = pickle.load(fh)
        log(f"  resuming: {len(done)} stocks already cached")

    t0 = time.time()
    errs = {}
    n = 0
    todo = [(sid, sym) for sid, sym in uni.items() if str(sid) not in done]
    log(f"  to fetch: {len(todo)}  (about {len(todo)/REQ_PER_SEC/60:.0f} minutes)")
    for sid, sym in todo:
        body = dict(body_base, securityId=str(sid))
        raw, err = _post("/charts/intraday", body)
        n += 1
        if err:
            errs[err.split()[0] + " " + err.split()[1] if len(err.split()) > 1 else err] = \
                errs.get(err.split()[0], 0) + 1
        else:
            sess = _slice_days(raw)
            if sess:
                done[str(sid)] = {"sym": sym, "sessions": sess}
        if n % 200 == 0:
            with store.open("wb") as fh:
                pickle.dump(done, fh)
            el = time.time() - t0
            log(f"    {n}/{len(todo)}  kept={len(done)}  {el/60:.1f} min elapsed, "
                f"~{(len(todo)-n)/max(n,1)*el/60:.0f} min left")
    with store.open("wb") as fh:
        pickle.dump(done, fh)
    log(f"  done: {len(done)} stocks with usable sessions")
    if errs:
        log("  failures by reason: " + ", ".join(f"{k} x{v}" for k, v in
                                                 sorted(errs.items(), key=lambda x: -x[1])[:6]))
    return store


def load(store=None):
    """The most recent cached fetch."""
    if store is None:
        cands = sorted(CACHE.glob("bars_*.pkl"))
        if not cands:
            return {}
        store = cands[-1]
    with Path(store).open("rb") as fh:
        return pickle.load(fh)


if __name__ == "__main__":
    p = fetch_all(force="--force" in sys.argv)
    if p:
        d = load(p)
        sess = {}
        for rec in d.values():
            for day in rec["sessions"]:
                sess[day] = sess.get(day, 0) + 1
        print()
        for day in sorted(sess):
            print(f"  {day}: {sess[day]} stocks")
