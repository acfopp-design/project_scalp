"""
quotes_v3.py -- name sources for both pipelines.

Cash : scanx (topvolume/mostactive/daygnl) -> merged names + day% (pchng)
F&O  : full universe (logs/fnoscan/fno_full_universe.json) + one batch
       /v2/marketfeed/quote to get day% for all 211 (cheap Stage-1 funnel).
Both then gate to movers (+1.5%..+17%) BEFORE the expensive candle step.
"""
import csv
import glob
import json
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

import Opus_engine as engine

HERE = Path(__file__).resolve().parent
ENV = HERE / "env.txt"
SECMASTER = HERE / "security_id_list.csv"
FNO_UNIVERSE = HERE / "logs" / "fnoscan" / "fno_full_universe.json"
MIN_MOVE, MAX_MOVE = 0.5, 17.0     # admit names just starting to move (catch ignition early)
# --- ABSOLUTE liquidity (Rs traded per MINUTE, live) -------------------------
# Relative volume multiples lie on thin stocks: a sleepy counter doing 3x its own
# tiny average is still untradeable. These are rupee floors, overridable from
# Opus_config.json (MIN_TOVER_RATE / LIQ_*).
MIN_TOVER_RATE = 300_000           # hard floor: below this the name is dropped entirely
LIQ_THIN = 300_000                 # < Rs 3 L/min  -> THIN
LIQ_OK = 1_000_000                 # >= Rs 10 L/min -> OK
LIQ_GOOD = 3_000_000               # >= Rs 30 L/min -> GOOD
LIQ_DEEP = 10_000_000              # >= Rs 1 Cr/min -> DEEP
MIN_PRICE = 40.0
# --- ADMISSION LIQUIDITY GATE ------------------------------------------------
# OLD (removed): MIN_TURNOVER = Rs 1 Cr of CUMULATIVE day turnover. That gate was
# the single biggest cause of late entries: a stock cannot accumulate Rs 1 Cr until
# well after it starts moving, so a mid-cap igniting at 09:16 stayed invisible for
# 10-33 minutes. Measured effect: only 7% of stocks were caught early, 61% late.
#
# NEW: cumulative VOLUME (shares) must be >= MIN_VOL_SHARES, checked from the
# VOL_GATE_MIN-th minute of the session onward. Before that it is too early to
# judge, so nothing is excluded on volume. A stock under the bar is skipped until
# its volume reaches it.
MIN_VOL_SHARES = 300_000           # 3 lakh shares
VOL_GATE_MIN = 3                   # start enforcing from the 3rd minute of the session
VEL_ADMIT = 0.25                   # %/min: admit a fast mover even below +0.3% day move
                                   # (catches the first bars of an ignition)
MIN_TURNOVER = 0                   # retired (kept at 0 so any stale reference is a no-op)

_EQ = None
_UNI = None
def _eq_universe():
    """sid -> symbol for ALL NSE series=EQ stocks (the market-wide sweep universe)."""
    global _UNI
    if _UNI is None:
        _UNI = {}
        try:
            with SECMASTER.open(encoding="utf-8", errors="ignore") as f:
                rd = csv.reader(f); next(rd, None)
                for c in rd:
                    if len(c) >= 15 and c[0] == "NSE" and c[1] == "E" and c[14] == "EQ":
                        _UNI[c[2].strip()] = c[5].strip().upper()
        except Exception:
            _UNI = {}
    return _UNI


import threading
_QLOCK = threading.Lock()       # GLOBAL quote throttle (bucket = 1 req/sec, shared by both pipelines)
_QLAST = [0.0]

# ---- REQUEST ACCOUNTING (31-Aug-2026) -----------------------------------
# These two functions are the BULK of this account's traffic -- the alarm's
# whole-universe sweep, market depth, circuit limits and the three ScanX lists
# all come through here, not through Opus_engine.post. The new per-minute rate
# line was therefore reporting "api.dhan.co = 0 requests" while the board was
# making hundreds. That is the third time in this project that instrumentation
# has read zero because it was looking in the wrong place, and a zero that means
# "not measured" is worse than no number at all, because it gets believed.
REQ_LOG = {"quote": 0, "scanx": 0, "429": 0, "since": time.time()}


def req_stats(reset=False):
    out = dict(REQ_LOG)
    out["n"] = out["quote"] + out["scanx"]
    out["elapsed"] = max(0.001, time.time() - REQ_LOG["since"])
    out["per_sec"] = round(out["n"] / out["elapsed"], 2)
    if reset:
        REQ_LOG.update({"quote": 0, "scanx": 0, "429": 0, "5xx": 0,
                    "since": time.time()})
    return out

class _Retry429(Exception):
    """Internal: tells _quote_all to try this chunk once more after a back-off."""


class _Retry5xx(Exception):
    """A transient Dhan gateway fault (502/503/504) worth exactly one retry."""



# GAP BETWEEN QUOTE CALLS.
#     1.15s was measured from the START of the previous request, so two calls
#     could still land inside one second of Dhan's clock whenever a response was
#     fast. 01-Sep logged 429s at an average of 0.98 requests/sec -- under the
#     documented 1/sec -- which is what a burst inside the minute looks like.
#     The gap is now taken from the END of the previous response, and widened.
# ADAPTIVE QUOTE GAP, 23-Sep. A fixed 1.30s tripped Dhan's limiter constantly:
# 3,000+ 429s today, 929 of them in the 09:00 hour alone -- precisely the window
# where a new mover has to be discovered before it can be traded. Each rejection
# costs a 2.5s backoff taken while holding _QLOCK, so at ~15 rejections a minute
# the quote path spent well over half of every hour asleep, and every caller
# queued behind it. That is what left EKC undiscovered until 09:27:42 while Sri
# traded it at 09:17 for +11.5%.
#
# Rather than guess a new constant with the market closed and no way to test it,
# the gap now tunes itself: every 429 widens it, a clean run narrows it back.
# It can only slow down when Dhan actually objects, and it recovers on its own.
_QGAP_MIN, _QGAP_MAX = 1.30, 4.00
_QGAP = 1.30
_QGAP_OK = [0]         # consecutive clean calls


def _gap_widen():
    global _QGAP
    _QGAP_OK[0] = 0
    _QGAP = min(_QGAP_MAX, _QGAP * 1.5)


def _gap_narrow():
    global _QGAP
    _QGAP_OK[0] += 1
    if _QGAP_OK[0] >= 20 and _QGAP > _QGAP_MIN:
        _QGAP = max(_QGAP_MIN, _QGAP * 0.9)
        _QGAP_OK[0] = 0
# ONE RETRY ON 429, THEN GIVE UP.
#     A 429 on batch 3 of the market sweep silently dropped ~1000 stocks from
#     that pass: the exception was logged and the chunk was simply missing, so
#     "2455 scanned" quietly became "1455 scanned" with nothing saying so. A
#     dropped third of the market is exactly how a stock goes unnoticed.
_Q429_BACKOFF = 2.5

# ---- WHY THE TIMEOUT IS SHORT AND THE GATEWAY ERRORS ARE RETRIED ---------
# 02-Sep 11:10-11:15: Dhan returned HTTP 502 on nearly every batch, and the
# board stopped producing entirely -- Super Stocks went silent for five minutes
# and the process logged its last line at 11:15:26 with six positions open and
# nobody at the machine.
#
# The mechanism was _QLOCK. It serialises EVERY quote call across EVERY thread,
# deliberately, so the throttle is honoured globally. That is correct, but it
# means the lock is held for the whole request -- so a 25-second timeout is not
# a 25-second delay to one caller, it is a 25-second stall of every thread in
# the board, including the scan that feeds the trades.
#
# A quote that normally answers in well under a second does not need 25 seconds
# to prove it is broken. The timeout is now 8s: still generous, but it caps how
# long one sick upstream can hold the entire board still. A short sweep is
# already logged loudly, and a short sweep is far cheaper than a frozen board.
#
# 502/503/504 are transient gateway faults and now get one retry, which 429
# already had. Previously they were not retried at all -- a single blip dropped
# a whole batch of up to 1000 stocks from the sweep.
_QTIMEOUT = 8.0
_Q5XX_BACKOFF = 1.5
_Q5XX = (502, 503, 504)


def _quote_one(body, headers, _retry=True):
    with _QLOCK:                 # serialize ALL quote calls, across every thread
        dt = time.time() - _QLAST[0]
        if dt < _QGAP:
            time.sleep(_QGAP - dt)
        REQ_LOG["quote"] += 1
        req = urllib.request.Request("https://api.dhan.co/v2/marketfeed/quote",
            data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_QTIMEOUT) as r:
                out = json.loads(r.read().decode())
                _gap_narrow()
                return out
        except urllib.error.HTTPError as e:
            if e.code == 429:
                REQ_LOG["429"] += 1
                _gap_widen()
                if _retry:
                    raise _Retry429()
            if e.code in _Q5XX:
                REQ_LOG["5xx"] = REQ_LOG.get("5xx", 0) + 1
                if _retry:
                    raise _Retry5xx()
            raise
        finally:
            # measured from the END of the response, not the start
            _QLAST[0] = time.time()


def _quote_all(sids, log):
    """Market-wide batch quote (chunks of 1000), globally throttled. Returns {sid: quote}."""
    cid, tok, _ = _env()
    headers = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    quotes = {}
    missing = 0
    for i in range(0, len(sids), 1000):
        chunk = sids[i:i + 1000]
        body = {"NSE_EQ": [int(s) for s in chunk]}
        try:
            try:
                res = _quote_one(body, headers)
            except _Retry429:
                log(f"quote batch {i//1000+1}: 429 -- backing off {_Q429_BACKOFF}s, one retry")
                time.sleep(_Q429_BACKOFF)
                res = _quote_one(body, headers, _retry=False)
            except _Retry5xx:
                log(f"quote batch {i//1000+1}: gateway error -- "
                    f"backing off {_Q5XX_BACKOFF}s, one retry")
                time.sleep(_Q5XX_BACKOFF)
                res = _quote_one(body, headers, _retry=False)
            if res.get("status") == "success":
                quotes.update((res.get("data") or {}).get("NSE_EQ") or {})
            else:
                log(f"quote batch {i//1000+1} non-success: {str(res)[:120]}")
        except urllib.error.HTTPError as e:
            log(f"quote batch {i//1000+1} HTTP {e.code}: {e.read().decode()[:120]}")
        except Exception as e:
            log(f"quote batch {i//1000+1} err: {e}")
            missing += len(chunk)
    if missing:
        # SAY IT. A short sweep must never be mistaken for a quiet market.
        log(f"quote sweep INCOMPLETE: {missing} of {len(sids)} stocks were not "
            f"quoted this pass -- anything among them is invisible until the next")
    return quotes


def circuit_limits(sids, log=lambda m: None):
    """Return {sid: upper_circuit_limit} for the given NSE_EQ security-ids in ONE
    batch. Used to backfill UC on cards during market-closed replay (when the
    scanx/last-session fallback carries ucl=None). Safe: returns {} on any error."""
    if not sids:
        return {}
    try:
        q = _quote_all([str(s) for s in sids], log)
    except Exception:
        return {}
    out = {}
    for sid, qq in (q or {}).items():
        try:
            u = float((qq or {}).get("upper_circuit_limit") or 0)
        except (TypeError, ValueError):
            u = 0
        if u:
            out[str(sid)] = round(u, 2)
    return out
def circuit_bands(sids, log=lambda m: None):
    """{sid: (lower_circuit_limit, upper_circuit_limit)} in ONE batch.

    circuit_limits() above returns only the upper band. The paper engine needs
    both: a long must be out before price is pinned at the UPPER band and a
    short before the LOWER one, because once a scrip locks at a circuit the
    book empties on that side and the position cannot be closed at any price.
    Safe: returns {} on any error, and skips any scrip missing either band.
    """
    if not sids:
        return {}
    try:
        q = _quote_all([str(s) for s in sids], log)
    except Exception:
        return {}
    out = {}
    for sid, qq in (q or {}).items():
        try:
            lo = float((qq or {}).get("lower_circuit_limit") or 0)
            hi = float((qq or {}).get("upper_circuit_limit") or 0)
        except (TypeError, ValueError):
            continue
        if lo > 0 and hi > lo:
            out[str(sid)] = (round(lo, 2), round(hi, 2))
    return out


def depth_and_totals(sids, log=lambda m: None):
    """Return {sid: {...}} with LIVE 5-level market depth + CONSOLIDATED whole-book
    totals for the given NSE_EQ security-ids, in ONE batched quote call.

      ltp        - last traded price
      tot_buy    - total pending BUY qty across the ENTIRE book (not just 5 levels)
      tot_sell   - total pending SELL qty across the entire book
      bid_px/bid_qty, ask_px/ask_qty - top-5 levels (nearest touch first)

    Used by the DEPTH & FLOW tab. Safe: returns {} on any error so it can never
    disturb the scanner pipelines."""
    if not sids:
        return {}
    try:
        q = _quote_all([str(s) for s in sids], log)
    except Exception as e:
        log(f"depth: quote error {e}")
        return {}
    out = {}
    for sid, qq in (q or {}).items():
        try:
            qq = qq or {}
            dep = qq.get("depth") or {}
            buy = dep.get("buy") or []
            sell = dep.get("sell") or []

            def _px(rows):
                return [round(float(r.get("price") or 0), 2) for r in rows[:5]]

            def _qt(rows):
                return [int(r.get("quantity") or 0) for r in rows[:5]]

            tb = qq.get("buy_quantity", qq.get("total_buy_quantity"))
            ts_ = qq.get("sell_quantity", qq.get("total_sell_quantity"))
            out[str(sid)] = {
                "ltp": round(float(qq.get("last_price") or 0), 2),
                "tot_buy": int(tb or 0),
                "tot_sell": int(ts_ or 0),
                "bid_px": _px(buy), "bid_qty": _qt(buy),
                "ask_px": _px(sell), "ask_qty": _qt(sell),
                "volume": int(qq.get("volume") or 0),
            }
        except Exception:
            continue
    return out


def _eq_syms():
    """NSE series=EQ tickers only -> excludes SME (SM/ST), BE, MF, etc."""
    global _EQ
    if _EQ is None:
        _EQ = set()
        try:
            with SECMASTER.open(encoding="utf-8", errors="ignore") as f:
                rd = csv.reader(f); next(rd, None)
                for c in rd:
                    if len(c) >= 15 and c[0] == "NSE" and c[1] == "E" and c[14] == "EQ":
                        _EQ.add(c[5].strip().upper())
        except Exception:
            _EQ = set()
    return _EQ

# --- ignition velocity state -------------------------------------------------
# {sid: (epoch, last_price, cum_volume, avg_vol_rate_per_sec)} from the PREVIOUS
# sweep. Lets us measure %/min and a volume-surge multiple at 30s resolution using
# only the quotes we already fetch. Memory: a few hundred KB for the full universe.
_PREV = {}


def _mins_since_open(now_t):
    """Minutes elapsed since 09:15 IST (>=1), for an average volume-rate baseline."""
    import datetime as _dt
    try:
        ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        n = _dt.datetime.fromtimestamp(now_t, ist)
        op = n.replace(hour=9, minute=15, second=0, microsecond=0)
        return max(1.0, (n - op).total_seconds() / 60.0)
    except Exception:
        return 60.0


def _liq_from_quote(sid, lp, vol, now_t):
    """Shared velocity/turnover maths against the PREVIOUS sweep.
    Returns (vel_pct_min, vol_rate_x, tover_min)."""
    vel = volx = 0.0
    tover = 0.0
    mins_open = _mins_since_open(now_t)
    p = _PREV.get(str(sid))
    if p:
        dt = max(1.0, now_t - p[0])
        if p[1] > 0:
            vel = ((lp - p[1]) / p[1] * 100.0) * (60.0 / dt)
        dv = vol - p[2]
        if dv > 0:
            if p[3] > 0:
                volx = (dv / dt) / p[3]
            tover = (dv * lp) * (60.0 / dt)
    if tover <= 0:
        tover = (lp * vol) / max(1.0, mins_open)
    avg_rate = (vol / max(1.0, mins_open * 60.0)) or 0.0
    _PREV[str(sid)] = (now_t, lp, vol, avg_rate if avg_rate > 0 else (p[3] if p else 0.0))
    return vel, volx, tover


def enrich_liquidity(names, log=lambda m: None):
    """Backfill vel/turnover for name-sources that don't compute it themselves
    (F&O pipeline, scanx fallback, market-closed replay).

    Without this those rows carry tover_min=None, which the UI would otherwise read
    as zero and grey out as 'illiquid' -- wrong, and especially wrong for F&O
    underlyings, which are the most liquid names on the exchange. One batched quote."""
    if not names:
        return names
    sids = [str(n.get("sid")) for n in names if n.get("sid") is not None]
    if not sids:
        return names
    try:
        quotes = _quote_all(sids, log)
    except Exception as e:
        log(f"enrich_liquidity: {e}")
        return names
    now_t = time.time()
    for n in names:
        q = (quotes or {}).get(str(n.get("sid"))) or {}
        try:
            lp = float(q.get("last_price") or 0)
            vol = float(q.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if lp <= 0:
            continue
        vel, volx, tover = _liq_from_quote(n.get("sid"), lp, vol, now_t)
        n["vel_pct_min"] = round(vel, 3)
        n["vol_rate_x"] = round(volx, 2)
        n["tover_min"] = int(tover)
        n["day_tover"] = int(lp * vol)
    return names


def liquidity_grade(tover_min):
    """Absolute tradeability from rupees-per-minute actually changing hands.
    A relative volume 'surge' on a thin stock is meaningless -- this is the check
    that says whether a price move can be trusted or acted on at all."""
    t = tover_min or 0
    if t >= LIQ_DEEP:  return "DEEP"
    if t >= LIQ_GOOD:  return "GOOD"
    if t >= LIQ_OK:    return "OK"
    if t >= LIQ_THIN:  return "THIN"
    return "ILLIQUID"


def ignition_score(m, w_vel=1.0, w_vol=1.0):
    """How strongly is this stock igniting RIGHT NOW (not how far it has travelled).

    Velocity dominates, but it is now SCALED BY ABSOLUTE LIQUIDITY. A 0.5%/min move
    on Rs 2 lakh/min is noise -- you cannot trust the direction and cannot get filled.
    The same move on Rs 50 lakh/min is a real, tradeable move."""
    vel = m.get("vel_pct_min") or 0.0
    volx = m.get("vol_rate_x") or 0.0
    day = m.get("day_pct") or 0.0
    tover = m.get("tover_min") or 0.0
    # liquidity multiplier: 0 below the floor, ramping to 1.0 at LIQ_GOOD and
    # capped at 1.25 for genuinely deep names.
    if tover <= LIQ_THIN:
        liq = 0.15
    elif tover >= LIQ_DEEP:
        liq = 1.25
    else:
        liq = 0.35 + 0.65 * min(1.0, (tover - LIQ_THIN) / max(1.0, LIQ_GOOD - LIQ_THIN))
    raw = vel * 10.0 * w_vel + min(volx, 8.0) * 3.0 * w_vol + day * 0.3
    return round(raw * liq, 3)


SCANX = [
    ("topvolume", "https://scanx.dhan.co/scanx/topvolume",
     {"Seg": 1, "SecIdxCode": 700, "Count": 50, "TypeFlag": "", "DayLevelIndicator": 0, "ExpCode": -1, "Instrument": "EQUITY"}),
    ("mostactive", "https://scanx.dhan.co/scanx/mostactive",
     {"Seg": 1, "SecIdxCode": 700, "Count": 50, "TypeFlag": "", "DayLevelIndicator": 0, "ExpCode": -1, "Instrument": "EQUITY"}),
    ("daygnl", "https://scanx.dhan.co/scanx/daygnl",
     {"Seg": 1, "SecIdxCode": 700, "Count": 50, "TypeFlag": "G", "DayLevelIndicator": 1, "ExpCode": -1, "Instrument": "EQUITY"}),
]


def _env():
    cid = tok = jwt = ""
    if ENV.exists():
        pend = None
        for l in ENV.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            l = l.strip()
            if not l:
                continue
            if "=" in l and not l.endswith(":"):
                k, v = l.split("=", 1); k = k.strip().upper(); v = v.strip()
                if k == "DHAN_SCANX_JWT": jwt = v
                elif k in ("CLIENT", "CLIENT_ID", "DHAN_CLIENT_ID"): cid = v
                elif k in ("TOKEN", "ACCESS_TOKEN", "DHAN_ACCESS_TOKEN"): tok = v
                pend = None; continue
            low = l.lower().rstrip(":")
            if low.startswith("client"): pend = "c"; continue
            if low.startswith("token") or low.startswith("access"): pend = "t"; continue
            if pend == "c": cid = l; pend = None
            elif pend == "t": tok = l; pend = None
    return cid, tok, jwt


def _scanx_post(url, body, jwt):
    REQ_LOG["scanx"] += 1
    req = urllib.request.Request(url, data=json.dumps({"Data": body}).encode(),
        headers={"accept": "application/json", "content-type": "application/json",
                 "auth": jwt, "authorisation": "Token",
                 "origin": "https://web.dhan.co", "referer": "https://web.dhan.co/"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _items(d):
    if isinstance(d, list): return d
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list): return v
    return []


_UISTATE_DIR = HERE / "logs" / "opus_uistate"


def _last_session_names(pipe, log=lambda m: None, target="15:30:00", cap=30):
    """MARKET-CLOSED fallback: replay the last trading day\'s board for `pipe`
    from the newest opus_uistate log, taking the snapshot nearest to (but not
    after) 15:30 IST. Shown symbols are resolved back to security-ids so the
    normal candle+score pipeline rebuilds full cards from last-session data."""
    try:
        files = sorted(glob.glob(str(_UISTATE_DIR / "uistate_*.jsonl")))
    except Exception:
        files = []
    if not files:
        return []
    # newest file first; skip empty (no-data) days until we find a populated board
    best = None
    for fpath in reversed(files):
        cand = None
        try:
            for line in open(fpath, encoding="utf-8"):
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("pipe") != pipe or not o.get("rows"):
                    continue
                t = (o.get("ts") or "")[11:]
                if t <= target:
                    cand = o             # advance toward 15:30
                elif cand is None:
                    cand = o
        except Exception:
            cand = None
        if cand:
            best = cand
            break
    if not best:
        return []
    syms = [r.get("sym") for r in best.get("rows", []) if r.get("sym")]
    if pipe == "cash":
        rev = {v: k for k, v in _eq_universe().items()}
    else:
        try:
            rev = {u["underlying"].upper(): str(u["sid"])
                   for u in json.loads(FNO_UNIVERSE.read_text())["underlyings"]}
        except Exception:
            rev = {}
    out = []
    for sym in syms[:cap]:
        sid = rev.get(str(sym).upper())
        if sid:
            out.append({"sym": str(sym).upper(), "sid": str(sid), "ucl": None})
    log(f"{pipe}: last-session replay snapshot={best.get('ts')} names={len(out)}")
    return out


def cash_names(resolver=None, log=lambda m: None):
    """MARKET-WIDE SWEEP: quote every NSE-EQ stock, admit early movers (+0.5%..17%)
    with day-turnover >= Rs 1 Cr, not at upper circuit. Catches the ignition the
    moment a stock starts moving -- no scanx top-50 discovery lag. EQ-series only."""
    uni = _eq_universe()
    if not uni:
        return [], "no_universe"
    quotes = _quote_all(list(uni.keys()), log)
    if not quotes:
        return [], "no_quotes"
    movers = []
    now_t = time.time()          # single timestamp for this whole sweep
    for sid, q in quotes.items():
        try:
            lp = float(q.get("last_price") or 0)
            nc = float(q.get("net_change") or 0)
            vol = float(q.get("volume") or 0)
            ucl = float(q.get("upper_circuit_limit") or 0)
        except (TypeError, ValueError):
            continue
        prev = lp - nc
        pct = (nc / prev * 100) if prev else 0.0
        if lp < MIN_PRICE:                       continue
        # volume gate: >= MIN_VOL_SHARES shares, enforced only from the 3rd minute on
        if _mins_since_open(now_t) >= VOL_GATE_MIN and vol < MIN_VOL_SHARES:
            continue
        if pct >= MAX_MOVE:                      continue   # already too extended
        if ucl and lp >= ucl * 0.999:            continue   # at upper circuit
        # velocity/turnover for EVERY surviving stock (updates _PREV for all, so a
        # stock can be admitted on speed the moment it starts, before +0.3% day move)
        vel, volx, tover_min = _liq_from_quote(sid, lp, vol, now_t)
        # ADMIT: normal early-surge band OR moving fast right now even below the band.
        # This catches the very first bars of a HINDALCO/VEDL-type ignition.
        if not ((MIN_MOVE <= pct < MAX_MOVE) or (vel >= VEL_ADMIT and pct > 0)):
            continue
        # --- IGNITION VELOCITY (free: derived from the sweep we already do) ------
        # We sweep every ~30s, so consecutive sweeps give 30-SECOND price/volume
        # resolution without a single extra API call. Absolute day% tells you what
        # already happened; velocity tells you what is happening RIGHT NOW.
        movers.append({"sym": uni.get(str(sid), str(sid)), "sid": str(sid),
                       "day_pct": round(pct, 2), "tvol": vol,
                       "vel_pct_min": round(vel, 3),
                       "vol_rate_x": round(volx, 2),
                       "tover_min": int(tover_min),          # Rs/min traded RIGHT NOW
                       "day_tover": int(lp * vol),           # Rs traded so far today
                       "ucl": round(ucl, 2) if ucl else None})
    movers.sort(key=lambda x: -x["day_pct"])
    log(f"cash sweep: universe={len(uni)} quoted={len(quotes)} movers(+{MIN_MOVE}..{MAX_MOVE}%)={len(movers)}")
    if not movers:        # market closed / after-hours net_change~0
        fb = _cash_scanx(resolver, log)
        if fb:
            return enrich_liquidity(fb, log), None
        ls = _last_session_names("cash", log)   # replay last trading day (~15:30 close)
        if ls:
            return enrich_liquidity(ls, log), None
    return movers, None


def _cash_scanx(resolver, log=lambda m: None):
    """Fallback discovery via scanx (returns last-session gainers even when market closed)."""
    cid, tok, jwt = _env()
    if not jwt:
        return []
    merged = {}
    for _, url, body in SCANX:
        try:
            for it in _items(_scanx_post(url, body, jwt)):
                sym = it.get("sym")
                if not sym:
                    continue
                if sym not in merged or (it.get("tvol", 0) or 0) > (merged[sym].get("tvol", 0) or 0):
                    merged[sym] = {"sym": sym, "pchng": it.get("pchng", 0), "tvol": it.get("tvol", 0)}
        except Exception:
            continue
    eqset = _eq_syms()
    out = []
    for s in merged.values():
        sym = s["sym"].upper()
        p = float(s.get("pchng") or 0)
        if MIN_MOVE <= p < MAX_MOVE and (not eqset or sym in eqset):
            sid = resolver.resolve(sym) if resolver else None
            if sid:
                out.append({"sym": sym, "sid": sid, "day_pct": p, "tvol": s.get("tvol", 0)})
    out.sort(key=lambda x: -x["day_pct"])
    log(f"cash scanx fallback: movers={len(out)}")
    return out


_FUT_SUFFIX = re.compile(r"-[A-Za-z]{3}\d{4}-FUT$", re.I)

SCANX_FNO = [
    ("fut_daygnl", "https://scanx.dhan.co/scanx/daygnl",
     {"Seg": 2, "SecIdxCode": 700, "Count": 50, "TypeFlag": "G", "DayLevelIndicator": 1, "ExpCode": -1, "Instrument": "FUTSTK"}),
    ("fut_topvolume", "https://scanx.dhan.co/scanx/topvolume",
     {"Seg": 2, "SecIdxCode": 700, "Count": 50, "TypeFlag": "", "DayLevelIndicator": 0, "ExpCode": -1, "Instrument": "FUTSTK"}),
    ("fut_mostactive", "https://scanx.dhan.co/scanx/mostactive",
     {"Seg": 2, "SecIdxCode": 700, "Count": 50, "TypeFlag": "", "DayLevelIndicator": 0, "ExpCode": -1, "Instrument": "FUTSTK"}),
]


def fno_names(log=lambda m: None):
    """F&O SWEEP: quote all 211 F&O underlyings (NSE-EQ sids), admit early movers
    (+0.5%..17%) with turnover >= Rs 1 Cr. Candles come from the CASH segment."""
    if not FNO_UNIVERSE.exists():
        return [], "no_universe"
    uni = json.loads(FNO_UNIVERSE.read_text())["underlyings"]   # [{underlying, sid}]
    sid2und = {str(u["sid"]): u["underlying"].upper() for u in uni}
    quotes = _quote_all(list(sid2und.keys()), log)
    if not quotes:
        return [], "no_quotes"
    out = []
    _fno_now = time.time()
    for sid, q in quotes.items():
        try:
            lp = float(q.get("last_price") or 0)
            nc = float(q.get("net_change") or 0)
            vol = float(q.get("volume") or 0)
            ucl = float(q.get("upper_circuit_limit") or 0)
        except (TypeError, ValueError):
            continue
        prev = lp - nc
        pct = (nc / prev * 100) if prev else 0.0
        if lp < MIN_PRICE:                             continue
        if _mins_since_open(_fno_now) >= VOL_GATE_MIN and vol < MIN_VOL_SHARES:  continue
        if pct >= MAX_MOVE:                            continue
        if ucl and lp >= ucl * 0.999:                  continue
        # ignition telemetry for EVERY surviving underlying (tracks _PREV for all)
        _vel, _volx, _tov = _liq_from_quote(sid, lp, vol, _fno_now)
        # admit on the early-surge band OR on live speed below it (catch the start)
        if not ((MIN_MOVE <= pct < MAX_MOVE) or (_vel >= VEL_ADMIT and pct > 0)):
            continue
        out.append({"sym": sid2und.get(str(sid), str(sid)), "sid": str(sid), "day_pct": round(pct, 2), "tvol": vol,
                    "vel_pct_min": round(_vel, 3), "vol_rate_x": round(_volx, 2),
                    "tover_min": int(_tov), "day_tover": int(lp * vol),
                    "ucl": round(ucl, 2) if ucl else None})
    out.sort(key=lambda x: -x["day_pct"])
    log(f"fno sweep: universe={len(sid2und)} quoted={len(quotes)} movers(+{MIN_MOVE}..{MAX_MOVE}%)={len(out)}")
    if not out:        # market closed -> last-session scanx FUTSTK
        fb = _fno_scanx(sid2und, log)
        if fb:
            return enrich_liquidity(fb, log), None
        ls = _last_session_names("fno", log)    # replay last trading day (~15:30 close)
        if ls:
            return enrich_liquidity(ls, log), None
    return out, None


def _fno_scanx(sid2und, log=lambda m: None):
    """Fallback F&O discovery via scanx FUTSTK (last-session gainers when market closed)."""
    cid, tok, jwt = _env()
    if not jwt:
        return []
    und2sid = {v: k for k, v in sid2und.items()}
    merged = {}
    for _, url, body in SCANX_FNO:
        try:
            for it in _items(_scanx_post(url, body, jwt)):
                sym = it.get("sym") or ""
                if not sym:
                    continue
                und = _FUT_SUFFIX.sub("", sym).upper()
                p = float(it.get("pchng") or 0)
                if und not in merged or p > merged[und]:
                    merged[und] = p
        except Exception:
            continue
    out = []
    for und, p in merged.items():
        if MIN_MOVE <= p < MAX_MOVE and und in und2sid:
            out.append({"sym": und, "sid": und2sid[und], "day_pct": round(p, 2)})
    out.sort(key=lambda x: -x["day_pct"])
    log(f"fno scanx fallback: movers={len(out)}")
    return out
