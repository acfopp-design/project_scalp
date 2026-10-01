"""
Movers_chartfeed.py -- candles from Dhan's OWN chart feed, not the official API.

WHY THIS EXISTS (31-Aug-2026)
    The official Data API stopped serving charts on this account. Measured, not
    guessed: 18 different request shapes were tried against /v2/charts/intraday
    and /v2/charts/historical -- interval as string and as integer, date-only and
    datetime ranges, ranges of 1/5/12/30 days, with and without oi and
    expiryCode, token-only headers, on HDFCBANK, TCS, RELIANCE, SBIN and on the
    board's own ScanX names. Every single one returned:

        HTTP 400  DH-905  "Missing required fields, bad values for parameters"

    while /v2/marketfeed/quote, /ltp, depth and circuit limits answered 200 on
    the SAME token, in the same run, seconds apart. So the shape was eliminated,
    the security IDs were eliminated, and the account's chart entitlement is the
    only thing left. build_card() needs candles before anything else, so this
    blanked the whole board: 0 cards, every cycle.

    Dhan's own web terminal draws those charts perfectly well. A browser capture
    on 31-Aug showed it does not use the documented API at all:

        POST https://ticks.dhan.co/getData
        headers  Auth: <web JWT>   Cid   Bid   Src   Content-Type
        body     {"EXCH","SEG","INST","SEC_ID","START","END",
                  "START_TIME","END_TIME","INTERVAL"}
        returns  {"success":true,"data":{o,h,l,c,v,t,oi,Time},"nextTime":...}

    Which is the same arrays Opus_candle_v3 already hands to the scorer.

    MEASURED BEFORE ANY CODE CHANGED (logs/STEP1_CHARTS_REPORT.txt):
      * the DHAN_SCANX_JWT already in env.txt is accepted here -- no new
        credential to keep alive, and one less thing to break at 09:14
      * the official access-token is REJECTED (401) -- these are separate worlds
      * 8 of 8 real board names returned 1-minute candles, SME and thin names
        included, not just large caps
      * 12-day window -> 2,881 bars, so the cross-session warm-up still works
      * INTERVAL below 1 minute is silently served as 1 minute. '15S', '30S' and
        '0.5' all came back with 60-second spacing. Sub-minute bars still come
        from Movers_ticks, exactly as before -- do not believe the chart menu.

HONEST LIMITS
    * This is not a documented endpoint. It can change without notice, which is
      why fetch() falls back to the official API instead of replacing it: if the
      entitlement is restored, the official path takes over again on its own.
    * The JWT is short-lived (~24h), same as the ScanX one. When it expires the
      board loses candles again -- so expiry is reported loudly, by name, rather
      than counted as a fetch failure. A silent zero here reads exactly like a
      quiet market, and that is the mistake this project has already paid for
      twice.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://ticks.dhan.co"
PATH_MIN = "/getData"      # INTERVAL "1", "5", "15" ... minute bars
PATH_SEC = "/getDataS"     # INTERVAL "30S", "15S", "5S" ... TRUE sub-minute bars

# MEASURED 31-Aug on /getDataS, so nobody has to guess later:
#   30S -> 720 bars/day, spacing exactly 30s      15S -> 1440, spacing 15s
#   5S  -> 4320, spacing 5s                       1S  -> silently served as 5S
#   a 5-day window returns THREE sessions (2,160 bars) -- so a card can open
#   at 09:15:30 already warmed up, instead of waiting ~18 minutes for the
#   live tick builder to accumulate 35 bars. 1/2/3-day windows return only
#   today, which is why the window below is 5 and not 2.
#   Volume on these bars does NOT match the exchange 1-minute total (7.7% low
#   on HDFCBANK, because the seconds feed stops at 15:14:30 while the minute
#   feed runs to 15:29). Session volume therefore still comes from the 1-minute
#   bundle -- see _min_cache below, which stops that costing a second request
#   on every refresh.
URL = BASE + PATH_MIN
SEC_TTL = 120.0            # 1-minute bundles are only used for volume totals,
                           # which do not need 25-second precision
_min_cache = {}            # sid -> (epoch, arrays)

# Own budget, deliberately separate from Opus_engine.post: that limiter guards
# api.dhan.co, and this is a different host with its own limits. Keeping them
# apart also means candle fetches no longer eat into the quote/depth budget --
# which is what was producing "Too many requests" warnings during testing.
# 5/s matches what the board needs to refresh ~60 cards inside 15 seconds
# (20 symbols per 5-second cycle) with a little headroom. Not raised further on
# purpose: Dhan has warned this account about request volume before, and a block
# costs the whole morning, not one cycle.
# 3.5/s, not 5. With rank-priority refresh the cards at the top of the screen
# still come round inside 15 seconds, and this is an UNDOCUMENTED endpoint on an
# account Dhan has already warned about volume -- the cost of being wrong is the
# whole morning, not one cycle. Movers_app logs the measured rate every minute;
# tune this against that number, not against a guess.
REQ_PER_SEC = 3.5
_lock = threading.Lock()
_last = [0.0]
_cooldown = [0.0]

# Reported through status() so the board can SAY the token died instead of just
# showing an empty screen.
STATUS = {"ok": 0, "fail": 0, "last_err": None, "token_expired": False,
          "last_ok": None, "source": None}
REQ_LOG = {"n": 0, "429": 0, "since": time.time()}


def req_stats(reset=False):
    out = dict(REQ_LOG)
    out["elapsed"] = max(0.001, time.time() - REQ_LOG["since"])
    out["per_sec"] = round(out["n"] / out["elapsed"], 2)
    if reset:
        REQ_LOG.update({"n": 0, "429": 0, "since": time.time()})
    return out


def _read_kv(path):
    out = {}
    p = HERE / path
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


_creds = {"ts": 0.0, "val": None}


def creds(force=False):
    """(jwt, cid, bid, src). Re-read from disk every 60s so pasting a fresh token
    into env.txt takes effect WITHOUT restarting the board -- the token expires
    mid-session and a restart at 09:40 costs the morning."""
    if not force and _creds["val"] and time.time() - _creds["ts"] < 60:
        return _creds["val"]
    env = _read_kv("env.txt")
    ch = _read_kv("env_charts.txt")
    jwt = env.get("DHAN_SCANX_JWT") or ch.get("DHAN_CHART_JWT") or ""
    val = (jwt, ch.get("DHAN_CHART_CID", "9632772723"),
           ch.get("DHAN_CHART_BID", "DHN1804"), ch.get("DHAN_CHART_SRC", "T"))
    _creds.update({"ts": time.time(), "val": val})
    STATUS["source"] = ("env.txt scanx jwt" if env.get("DHAN_SCANX_JWT")
                        else ("env_charts.txt" if ch.get("DHAN_CHART_JWT") else "NONE"))
    return val


IST_FMT = "%a %b %d %Y %H:%M:%S GMT+0530 (India Standard Time)"


def get(sid, interval="1", days=12, tz=None, path=PATH_MIN, cache=False):
    """Raw candle arrays for one security, or (None, error).

    Returns ({"o":[],"h":[],"l":[],"c":[],"v":[],"t":[]}, None) on success.
    `t` is true epoch seconds (verified against the feed's own ISO strings).
    """
    if cache:
        hit = _min_cache.get((str(sid), str(interval)))
        if hit and time.time() - hit[0] < SEC_TTL:
            return hit[1], None
    jwt, cid, bid, src = creds()
    if not jwt:
        STATUS["last_err"] = "no JWT (env.txt DHAN_SCANX_JWT / env_charts.txt)"
        return None, STATUS["last_err"]
    from datetime import timezone
    tz = tz or timezone(timedelta(hours=5, minutes=30))
    end = datetime.now(tz)
    start = end - timedelta(days=days)
    body = {"EXCH": "NSE", "SEG": "E", "INST": "EQUITY", "SEC_ID": int(sid),
            "START": int(start.timestamp()), "END": int(end.timestamp()),
            "START_TIME": start.strftime(IST_FMT), "END_TIME": end.strftime(IST_FMT),
            "INTERVAL": str(interval)}
    headers = {"Auth": jwt, "Cid": cid, "Bid": bid, "Src": src,
               "Content-Type": "application/json", "Accept": "*/*",
               "Origin": "https://tv.dhan.co", "Referer": "https://tv.dhan.co/",
               "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/151.0.0.0 Safari/537.36")}
    for attempt in range(3):
        with _lock:
            now = time.time()
            if now < _cooldown[0]:
                time.sleep(_cooldown[0] - now)
            gap = time.time() - _last[0]
            if gap < 1.0 / REQ_PER_SEC:
                time.sleep(1.0 / REQ_PER_SEC - gap)
            _last[0] = time.time()
            REQ_LOG["n"] += 1
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                # NAME IT. An expired chart token and a dead market look
                # identical on screen, and that ambiguity has cost this project
                # two sessions already.
                STATUS["token_expired"] = True
                STATUS["fail"] += 1
                STATUS["last_err"] = ("CHART TOKEN EXPIRED (HTTP 401) -- paste a "
                                      "fresh DHAN_SCANX_JWT into env.txt")
                return None, STATUS["last_err"]
            if e.code == 429:
                REQ_LOG["429"] += 1
            if e.code == 429 and attempt < 2:
                wait = 3.0 * (attempt + 1)
                with _lock:
                    _cooldown[0] = max(_cooldown[0], time.time() + wait)
                time.sleep(wait)
                continue
            STATUS["fail"] += 1
            STATUS["last_err"] = f"HTTP {e.code}"
            return None, STATUS["last_err"]
        except Exception as e:
            STATUS["fail"] += 1
            STATUS["last_err"] = f"{type(e).__name__}: {str(e)[:90]}"
            return None, STATUS["last_err"]
        if not d.get("success"):
            STATUS["fail"] += 1
            STATUS["last_err"] = f"success=false {str(d)[:90]}"
            return None, STATUS["last_err"]
        a = d.get("data") or {}
        if not (a.get("c") and a.get("t")):
            STATUS["fail"] += 1
            STATUS["last_err"] = "empty arrays"
            return None, STATUS["last_err"]
        STATUS["ok"] += 1
        STATUS["token_expired"] = False
        STATUS["last_ok"] = datetime.now(tz).strftime("%H:%M:%S")
        out = {"o": a["o"], "h": a["h"], "l": a["l"], "c": a["c"],
               "v": a["v"], "t": a["t"]}
        if cache:
            _min_cache[(str(sid), str(interval))] = (time.time(), out)
        return out, None
    STATUS["fail"] += 1
    STATUS["last_err"] = "rate-limited after retries"
    return None, STATUS["last_err"]


def status():
    return dict(STATUS)


def get_seconds(sid, interval="30S", days=5, tz=None):
    """TRUE sub-minute bars from /getDataS. See the constants above for what the
    feed actually returns -- all of it measured, none of it assumed."""
    return get(sid, interval=interval, days=days, tz=tz, path=PATH_SEC)


# ---- DAILY BARS (31-Aug-2026) -------------------------------------------
# Captured from Dhan's own 1-Day chart. My first guess at this failed and
# returned two bars for the WRONG stock, because the daily path differs from
# the intraday one in two ways that are easy to miss:
#
#   1. it wants SYM (the trading symbol) as well as SEC_ID -- without it the
#      server ignores the security id and answers about something else
#   2. START_TIME / END_TIME are ISO-8601 UTC ("2025-04-20T00:00:00.000Z"),
#      not the long IST string the intraday feed uses
#
# Verified shape (ICIL): 339 daily bars from 21-Apr-2025 to 28-Aug-2026.
# Note the last bar is the previous COMPLETED session, not today -- which is
# exactly right for daily RSI / MACD / Supertrend, whose whole purpose is to say
# what was true before the open.
PATH_DAY = "/getDataH"
ISO = "%Y-%m-%dT%H:%M:%S.000Z"


def daily(sid, sym, days=400, tz=None):
    """Daily OHLCV for one security, or (None, error)."""
    from datetime import timezone as _tzm
    jwt, cid, bid, src = creds()
    if not jwt:
        return None, "no JWT"
    tz = tz or _tzm(timedelta(hours=5, minutes=30))
    end = datetime.now(tz)
    start = end - timedelta(days=days)
    body = {"EXCH": "NSE", "SYM": str(sym), "SEG": "E", "INST": "EQUITY",
            "EXPCODE": 0, "SEC_ID": int(sid),
            "START": int(start.timestamp()), "END": int(end.timestamp()),
            "START_TIME": start.astimezone(_tzm.utc).strftime(ISO),
            "END_TIME": end.astimezone(_tzm.utc).strftime(ISO),
            "INTERVAL": "D"}
    headers = {"Auth": jwt, "Cid": cid, "Bid": bid, "Src": src,
               "Content-Type": "application/json", "Accept": "*/*",
               "Origin": "https://tv.dhan.co", "Referer": "https://tv.dhan.co/",
               "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/151.0.0.0 Safari/537.36")}
    with _lock:
        now = time.time()
        gap = now - _last[0]
        if gap < 1.0 / REQ_PER_SEC:
            time.sleep(1.0 / REQ_PER_SEC - gap)
        _last[0] = time.time()
        REQ_LOG["n"] += 1
    req = urllib.request.Request(BASE + PATH_DAY, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            STATUS["token_expired"] = True
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"
    if not d.get("success"):
        return None, "success=false"
    a = d.get("data") or {}
    if not (a.get("c") and a.get("t")):
        return None, "empty"
    return {"o": a["o"], "h": a["h"], "l": a["l"], "c": a["c"],
            "v": a["v"], "t": a["t"]}, None
