"""
Movers_context.py -- the CONTEXT a headline needs to be judged.

WHY THIS EXISTS
    Six sessions of measurement showed that news text alone does not predict the
    tradeable (open->close) move. Base rate 54%; severity bands scored 55/54/55;
    fixing the headline classifier changed 52 calls and fixed 7 while breaking
    7. Good news on a small-cap is close to a coin flip.

    Case-by-case forensics said why. The moves that were explicable were
    explained by things absent from the headline:

      RAMCOCEM  PAT -63% but revenue +9.6%, volumes up, OPM 13% vs a 3-year
                range of 13-19%  -> the market priced the operating trend
      VIPIND    loss widened, but it was the FIRST revenue growth in 7 quarters
                and a live open offer at Rs 388 floored the price
      RAIN      PAT +116% and the stock fell 8% -- it had already run, and a
                downgrade landed the same day
      LENSKART  gapped on the print, faded intraday, then made its real move on
                D+1 when brokerages raised targets

    Every one of those needs PRIOR-STATE context: how far the stock had already
    run, where this quarter's margin sits in its own history, whether the
    quarter is an inflection or just a level.

WHAT THIS PROVIDES
    runup(sid)         from Dhan's own /charts/historical -- no external
                       dependency, no scraping, no terms-of-service question.
    fundamentals(sym)  read from a local cache of quarterly history. The cache
                       is written separately; this module only reads it, so the
                       board never scrapes anything on a timer.

DELIBERATELY NOT WIRED INTO `chance`
    Nothing here touches the Chance figure yet. It could not be validated -- the
    board's logs only cover stocks that were ALREADY moving, so a backtest of
    run-up against the 123 recorded calls had a sample of 8. Instead these
    values are recorded on every call and registered as calibration buckets, so
    Movers_premarket.calibration() will measure them forward and they earn their
    way into the score only once >= 25 outcomes exist. That is the opposite of
    how the original chance weights were built, and the reason they were wrong.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import Opus_engine as engine
import Opus_quotes_v3 as q

IST = engine.IST
HERE = Path(__file__).resolve().parent
CTX_DIR = HERE / "logs" / "context"
FUND_DIR = HERE / "logs" / "fundamentals"
CTX_DIR.mkdir(parents=True, exist_ok=True)
FUND_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 30          # calendar days -> ~20 sessions, enough for 10d runup
MAX_FETCH_PER_CALL = 6      # Dhan allows 1 req/sec; never stall the caller
_lock = threading.Lock()
_mem = {}


def _cache_path(day):
    return CTX_DIR / f"runup_{day}.json"


def _load(day):
    p = _cache_path(day)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(day, d):
    try:
        tmp = _cache_path(day).with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, _cache_path(day))
    except Exception:
        pass


def _daily(sid):
    """Daily OHLCV from Dhan. Same endpoint Scanner2 used for 160 days x 312
    stocks, so this is a proven path, not a new integration."""
    cid, tok, _ = q._env()
    if not tok:
        return None
    today = datetime.now(IST)
    frm = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    body = json.dumps({"securityId": str(sid), "exchangeSegment": "NSE_EQ",
                       "instrument": "EQUITY", "expiryCode": 0, "fromDate": frm,
                       "toDate": today.strftime("%Y-%m-%d")}).encode()
    req = urllib.request.Request(
        "https://api.dhan.co/v2/charts/historical", data=body, method="POST",
        headers={"access-token": tok, "client-id": cid,
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _compute(d):
    """Run-up and volatility context from a daily bundle.

    EXCLUDES TODAY. The last completed session is the reference, because at
    09:00 today's candle does not exist yet -- including a partial bar would
    make the morning figure disagree with the evening one.
    """
    if not d:
        return None
    c = [x for x in (d.get("close") or []) if x]
    h = d.get("high") or []
    lo = d.get("low") or []
    v = d.get("volume") or []
    n = min(len(c), len(h), len(lo), len(v))
    if n < 6:
        return None
    c, h, lo, v = c[:n], h[:n], lo[:n], v[:n]
    last = float(c[-1])
    if last <= 0:
        return None

    def chg(k):
        if n <= k or not c[-1 - k]:
            return None
        return round((last / float(c[-1 - k]) - 1) * 100, 2)

    # average true daily range as a % of price -- tells you whether a 2% gap is
    # large or routine FOR THIS STOCK
    trs = []
    for i in range(max(1, n - 14), n):
        tr = max(float(h[i]) - float(lo[i]),
                 abs(float(h[i]) - float(c[i - 1])),
                 abs(float(lo[i]) - float(c[i - 1])))
        trs.append(tr)
    atr_pct = round(sum(trs) / len(trs) / last * 100, 2) if trs else None
    win = [float(x) for x in c[-20:]]
    hi20, lo20 = max(win), min(win)
    pos = round((last - lo20) / (hi20 - lo20) * 100) if hi20 > lo20 else None
    vv = [float(x or 0) for x in v[-20:]]
    avgv = sum(vv[:-1]) / max(1, len(vv) - 1) if len(vv) > 1 else 0
    # PREVIOUS SESSION TURNOVER, in Rs crore.
    #   Needed because before 09:15 today's volume is legitimately 0 -- nothing
    #   has traded yet -- so any liquidity gate reading the live quote sees
    #   "unknown" for every stock on the board. Yesterday's turnover is the
    #   correct stand-in: it answers "can I get out of this?" just as well at
    #   08:10 as it does at 10:00.
    prev_tov = round(float(v[-1] or 0) * last / 1e7, 2) if v and v[-1] else None
    return {"runup_1d": chg(1), "runup_3d": chg(3), "runup_5d": chg(5),
            "runup_10d": chg(10), "atr_pct": atr_pct, "pos_20d": pos,
            "vol_x": round(vv[-1] / avgv, 2) if avgv > 0 else None,
            "prev_tover_cr": prev_tov, "sessions": n}


def prev_turnover(sid):
    """Last completed session's turnover in Rs crore, from cache only.

    Deliberately does NOT fetch -- callers that want a fetch call runup() with
    a budget. This keeps a hot loop (the watchlist re-ranks every 20s) from
    triggering network I/O.
    """
    day = datetime.now(IST).strftime("%Y%m%d")
    with _lock:
        disk = _mem.get(day)
        if disk is None:
            disk = _load(day)
            _mem[day] = disk
    return ((disk.get(str(sid)) or {}) or {}).get("prev_tover_cr")


def runup(sids, log=lambda m: None, budget=MAX_FETCH_PER_CALL):
    """Context for a list of security ids. Cached per day on disk.

    Daily candles do not change during the session, so each stock costs ONE
    request per day. The per-call budget keeps the pre-market loop responsive:
    at 1 request/second an unbounded fetch of 60 names would block for a minute.
    """
    day = datetime.now(IST).strftime("%Y%m%d")
    with _lock:
        disk = _mem.get(day)
        if disk is None:
            disk = _load(day)
            _mem[day] = disk
    fetched = 0
    for sid in sids:
        s = str(sid)
        if not s.isdigit() or s in disk:
            continue
        if fetched >= budget:
            break
        got = _compute(_daily(s))
        fetched += 1
        time.sleep(1.05)                      # Dhan: 1 request/second
        disk[s] = got or {}
    if fetched:
        with _lock:
            _save(day, disk)
        log(f"context: fetched {fetched} run-up histories ({len(disk)} cached today)")
    return disk


def fundamentals(sym):
    """Quarterly history for one symbol, from the local cache if present.

    The cache is written OUT OF BAND (see FUND_DIR). Nothing in the running
    board fetches it, so there is no always-on scraper and no terms-of-service
    exposure. Missing file simply means no fundamental context for that name.

    Expected shape:
        {"sym": "RAMCOCEM", "sector": "Cement & Cement Products",
         "opm": [15,17,19,...],          # oldest -> newest, % per quarter
         "sales": [...], "np": [...],
         "asof": "2026-08-17"}
    """
    p = FUND_DIR / f"{(sym or '').upper()}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def margin_context(sym):
    """Where THIS quarter's margin sits in the company's own history.

    The RAMCOCEM lesson: OPM fell to 13%, which reads as bad until you see the
    3-year range is 13-19% and revenue grew 9.6%. A margin at the bottom of its
    own range with growing revenue is a very different setup from the same
    margin in a shrinking business.
    """
    f = fundamentals(sym)
    if not f:
        return None
    opm = [x for x in (f.get("opm") or []) if isinstance(x, (int, float))]
    if len(opm) < 4:
        return None
    cur, hist = opm[-1], opm[:-1]
    lo_, hi_ = min(hist), max(hist)
    pct = None
    if hi_ > lo_:
        pct = round((cur - lo_) / (hi_ - lo_) * 100)
    sales = [x for x in (f.get("sales") or []) if isinstance(x, (int, float))]
    yoy = None
    if len(sales) >= 5 and sales[-5]:
        yoy = round((sales[-1] / sales[-5] - 1) * 100, 1)
    # INFLECTION -- the VIPIND mechanism. A first up-quarter after a run of down
    # ones moved the stock 7.3% despite the loss widening.
    infl = None
    np_ = [x for x in (f.get("np") or []) if isinstance(x, (int, float))]
    if len(np_) >= 4:
        prior_down = sum(1 for a, b in zip(np_[-5:-1], np_[-4:]) if b < a)
        infl = bool(np_[-1] > np_[-2] and prior_down >= 2)
    return {"opm_now": cur, "opm_lo": lo_, "opm_hi": hi_, "opm_pos_pct": pct,
            "sales_yoy": yoy, "inflection": infl, "sector": f.get("sector"),
            "quarters": len(opm), "asof": f.get("asof")}
