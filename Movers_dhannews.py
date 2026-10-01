"""
Movers_dhannews.py -- OVERNIGHT / SESSION news straight from Dhan.

WHY
    A stock that gaps at 09:15 usually gapped for a reason published AFTER
    yesterday's close -- results, an order win, a downgrade. Dhan's own terminal
    carries these (e.g. "Navin Fluorine Q1FY26 profit surges", 05-Aug 17:03), so
    we read the same feed and show everything from the PREVIOUS SESSION CLOSE
    (15:30) up to now. That is the window that explains today's bias.

ENDPOINT (captured from web.dhan.co -> News)
    POST https://news-live.dhan.co/news/snippetgrouping
    headers: Auth: <DHAN_SCANX_JWT>, Authorisation: Token, Content-Type, Accept
    body:    {"limit":50,"page":N,"reqfor":"","firstpubdate":0,"lastpubdate":0}
    returns: data.latest[] -> stocknews{ display_symbol, stock_name, isin_code,
             publishdate_timestamp (ms), newsobject{ article_title, article_text,
             overall_sentiment } , nse_scrip_code, ... }

Isolated: any failure returns an empty list; never raises into the app.
"""
import json
import math
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import Opus_quotes_v3 as q
import Opus_engine as engine

try:
    import Movers_alarm as alarm       # NSE EQ security master
except Exception:                       # pragma: no cover
    alarm = None

IST = engine.IST
URL = "https://news-live.dhan.co/news/snippetgrouping"
# VERIFIED AGAINST THE LIVE FEED:
#   * the "page" parameter is IGNORED -- pages 1/2/3 return the identical 50
#     items, which is what produced all the duplicate rows.
#   * "limit" is the real control. limit=800 reaches back past yesterday 15:30
#     (verified: oldest item 04-Aug 18:58), so one call covers the whole window.
#   * the NSE ticker is sm_symbol ("SOTL"); nse_scrip_code is a numeric security
#     id and display_symbol is a long name ("Savita Oil").
FETCH_LIMIT = 800
TTL = 180                      # refresh at most every 3 minutes

_cache = {"ts": 0.0, "items": [], "err": None}
_lock = threading.Lock()


def _headers():
    _, _, jwt = q._env()
    if not jwt:
        return None
    return {"Content-Type": "application/json", "Accept": "application/json",
            "Auth": jwt, "Authorisation": "Token",
            "Origin": "https://web.dhan.co", "Referer": "https://web.dhan.co/"}


def _post(headers, limit=FETCH_LIMIT):
    body = json.dumps({"limit": limit, "page": 1, "reqfor": "",
                       "firstpubdate": 0, "lastpubdate": 0}).encode()
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def session_cutoff(now=None):
    """Epoch ms of the PREVIOUS session's close (15:30 IST).

    Before today's close we look back to yesterday 15:30; after it, to today's
    15:30. Weekends walk back to the last weekday so a Monday morning still
    shows Friday-evening news.
    """
    now = now or datetime.now(IST)
    cut = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now <= cut:
        cut -= timedelta(days=1)
    while cut.weekday() >= 5:               # Sat/Sun -> walk back to Friday
        cut -= timedelta(days=1)
    return int(cut.timestamp() * 1000), cut


def fetch(force=False, log=lambda m: None):
    """All Dhan news published since the previous session close. Newest first."""
    now_t = time.time()
    with _lock:
        if not force and (now_t - _cache["ts"] < TTL) and _cache["items"]:
            return _cache["items"], _cache["err"]
    h = _headers()
    if not h:
        with _lock:
            _cache.update({"ts": now_t, "items": [], "err": "no DHAN_SCANX_JWT in env.txt"})
        return [], _cache["err"]
    cut_ms, cut_dt = session_cutoff()
    items, err = [], None
    seen_ids = set()
    try:
        j = _post(h, FETCH_LIMIT)
        arr = ((j or {}).get("data") or {}).get("latest") or []
        for it in arr:
            sn = (it or {}).get("stocknews") or {}
            ts = sn.get("publishdate_timestamp")
            if not ts or ts < cut_ms:        # older than the previous close
                continue
            aid = sn.get("article_id")
            if aid and aid in seen_ids:      # the feed repeats articles
                continue
            if aid:
                seen_ids.add(aid)
            obj = sn.get("newsobject") or {}
            items.append({
                # sm_symbol IS the NSE ticker; display_symbol is a long name
                "sym": (sn.get("sm_symbol") or sn.get("display_symbol") or "").strip().upper(),
                "label": (sn.get("display_symbol") or "").strip(),
                "name": (sn.get("stock_name") or "").strip(),
                "nse": sn.get("nse_scrip_code"),      # numeric security id
                "isin": sn.get("isin_code"),
                "ts": ts,
                "when": datetime.fromtimestamp(ts / 1000, IST).strftime("%d-%b %H:%M"),
                "age_min": int((time.time() - ts / 1000) / 60),
                "title": (obj.get("article_title") or "").strip(),
                "text": (obj.get("article_text") or "").strip()[:400],
                "sentiment": (obj.get("overall_sentiment") or "").lower(),
                "cat": sn.get("category"),
            })
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
        log(f"dhannews: {err}")
    items.sort(key=lambda x: -x["ts"])
    with _lock:
        if items or not _cache["items"]:
            _cache.update({"ts": now_t, "items": items, "err": err})
    log(f"dhannews: {len(items)} items since {cut_dt.strftime('%d-%b %H:%M')}"
        + (f" (err {err})" if err else ""))
    return _cache["items"], _cache["err"]


def attach_moves(items, log=lambda m: None):
    """Attach each stock's LIVE day move to its headline.

    The market's own reaction is the honest impact measure -- a keyword score
    cannot tell you that Savita Oil is +19% while Motherson's aerospace filing
    moved it 0.2%. nse_scrip_code IS the Dhan security id, so one batched quote
    covers every stock in the feed.
    """
    sids = []
    for it in items:
        s = str(it.get("nse") or "").strip()
        if s.isdigit():
            sids.append(s)
    sids = list(dict.fromkeys(sids))
    if not sids:
        return items
    try:
        quotes = q._quote_all(sids, lambda m: None)
    except Exception as e:
        log(f"dhannews: quote failed {e}")
        return items
    for it in items:
        qq = (quotes or {}).get(str(it.get("nse") or "")) or {}
        try:
            lp = float(qq.get("last_price") or 0)
            nc = float(qq.get("net_change") or 0)
            prev = lp - nc
            if lp > 0 and prev:
                it["ltp"] = round(lp, 2)
                it["day_pct"] = round(nc / prev * 100, 2)
            # TRADEABILITY. The same quote already carries the day's volume, so
            # the turnover gate costs no extra request. Rupee turnover, never
            # share count -- 200k shares is Rs 2cr in a Rs 100 stock and Rs 98cr
            # in HAL, and ranking on share count systematically buried every
            # high-priced name.
            vol = float(qq.get("volume") or 0)
            if lp > 0 and vol > 0:
                it["tover_cr"] = round(vol * lp / 1e7, 2)
        except (TypeError, ValueError):
            pass
    return items


# impact tiers, driven by how far the stock has ACTUALLY moved today
HIGH_MOVE = 5.0      # |day %| at or above this = high impact
MED_MOVE = 2.0
# A stock moving this hard is kept even if every headline about it reads
# "neutral" to the classifier. See the note in curated().
FORCE_KEEP_MOVE = 4.0


_uni_cache = {"t": 0.0, "syms": None}


def nse_universe(log=lambda m: None):
    """Symbols on the NSE EQ series, cached. Empty set means 'unknown' -- and
    callers must then NOT filter, because filtering on an empty master would
    silently blank every tab."""
    now = time.time()
    if _uni_cache["syms"] is not None and now - _uni_cache["t"] < 900:
        return _uni_cache["syms"]
    syms = set()
    if alarm is not None:
        try:
            syms = {str(s).upper().strip() for s in alarm.universe(log).values()}
        except Exception:
            syms = set()
    _uni_cache.update({"t": now, "syms": syms})
    return syms


def is_nse(it, universe=None):
    """True when this headline belongs to a stock tradeable on NSE.

    WHY BOTH CHECKS
        The feed carries BSE-only companies, and they are identifiable two ways.
        Dhan leaves `nse_scrip_code` empty for them -- 8 of 60 headlines on
        18-Aug -- and it also falls back to the full registered name instead of
        a ticker ("MAXGROW INDIA LIMITED", "RADHA MADHAV CORPORATION LIMITED"),
        because there is no NSE symbol to print. Requiring a numeric NSE
        security id removes the whole class; checking the symbol against the EQ
        master then also removes general market copy such as the item filed
        under the symbol "MARKETS".

        The universe check is skipped when the master is unavailable, so a
        failed master fetch degrades to "show everything" rather than "show
        nothing".
    """
    if not str(it.get("nse") or "").strip().isdigit():
        return False
    if universe:
        return (it.get("sym") or "").upper().strip() in universe
    return True


def only_nse(items, log=lambda m: None):
    """Drop BSE-only names. Returns (kept, dropped_count).

    NOT CALLED ANYWHERE. Kept for reference, disabled after it blanked the News
    and Pre-market tabs in production.

    WHAT WENT WRONG -- worth reading before re-enabling
        The id check (`nse_scrip_code` must be numeric) is sound on its own.
        The SYMBOL check is not: this feed's `sm_symbol` does not reliably equal
        the trading symbol in the NSE EQ master, so `sym not in universe`
        matched almost everything and the filter removed nearly every row.

        My test did not catch it because I built the test universe FROM the same
        calls file I was filtering -- so every symbol matched by construction and
        the check could not fail. A filter must be validated against the REAL
        master, on rows it did not come from.

    SAFE VERSION IF RESUMED
        Filter on the numeric id ONLY (drops the 8 genuine BSE-only rows on
        18-Aug), and never on the symbol until the two symbol spaces have been
        reconciled against alarm.universe() directly.
    """
    uni = nse_universe(log)
    keep = [it for it in (items or []) if is_nse(it, uni)]
    return keep, len(items or []) - len(keep)


def _tier(day_pct):
    if day_pct is None:
        return 2, ""
    a = abs(day_pct)
    if a >= HIGH_MOVE:
        return 0, "HIGH"
    if a >= MED_MOVE:
        return 1, "MED"
    return 2, ""


# ============================ IMPACT SCORING ============================
# WHY THIS EXISTS
#   Dhan's own `overall_sentiment` is a HEADLINE-TONE classifier, not an impact
#   estimate. On a normal evening it returns positive for ~77% of the feed
#   (measured: 54 of 70), so it cannot rank anything -- "profit up 53%" and
#   "profit up 1079%" are both just `positive`.
#
#   Worse, the HIGH/MED tier above is keyed off day_pct, which is +0.00% for
#   every row once the market shuts. That is precisely the evening you want the
#   ranking, and it was switching itself off. So after the close we rank by the
#   score below instead, and keep the real-move tiering for live hours.
#
#   The score is deliberately AUDITABLE: every row carries the parts that made
#   it, so a wrong ranking can be traced to the component that caused it rather
#   than being a black box.

_PCT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*%")
_RS_RE = re.compile(r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)\s*"
                    r"(crore|cr\b|lakhs?|lacs?|million|mn\b|billion|bn\b)?", re.I)

# Corporate hygiene filings. They are published constantly, they read "positive"
# to a tone classifier, and they have never moved a scalp: an AGM ratifying a
# dividend that was announced weeks ago, a promoter buying 67,081 shares, a tea
# estate resuming plucking.
NOISE_RE = re.compile(
    r"\b(agm\b|annual general meeting|ratif\w*|re-?appoint\w*|record date|book closure"
    r"|newspaper (publication|advertisement)|intimation under|disclosure under"
    r"|regulation 3\d|trading window|duplicate share|scrutiniz\w*|voting result"
    r"|postal ballot|loss of share certificate|analyst meet|investor meet"
    r"|earnings call (invite|intimation)|resumes plucking)\b", re.I)

# Events that genuinely re-rate a stock overnight.
HARD_RE = re.compile(
    r"(q[1-4]\s*fy|net profit|\bpat\b|ebitda|revenue|profit (surge|jump|rise|fall|dip|declin)"
    r"|order (win|worth|book)|wins? (an? )?(order|contract)|bags\b|contract worth"
    r"|acquisi\w*|merger|demerger|open offer|stake (sale|buy|purchase)|block deal"
    r"|upgrade|downgrade|target price|guidance|fund rais\w*|qip\b|preferential"
    r"|bonus issue|stock split|buyback|dividend declar|approval|licen[cs]e"
    r"|usfda|resolution plan|insolvency)", re.I)

# Tradeability gate. Below this you cannot get Rs 10k in AND out inside 2-5
# minutes without the spread taking more than the move. This is a HARD gate, not
# a score term -- the loudest headlines in the feed are consistently the least
# tradeable names, and no amount of good news fixes an empty book.
MIN_TURNOVER_CR = 5.0
GOOD_TURNOVER_CR = 25.0


def pct_surprise(text):
    """Largest percentage figure quoted. Caps at 300% -- beyond that it is a
    base effect (a company going from Rs 1cr to Rs 14cr PAT prints +1079%) and
    the extra magnitude carries no extra information."""
    best = 0.0
    for m in _PCT_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0 < v < 5000 and v > best:
            best = v
    return min(best, 300.0)


def rupee_cr(text):
    """Largest rupee figure in the text, normalised to Rs CRORE. Dhan mixes
    units freely inside one snippet ('Rs 102.47 million', 'Rs 13,747 crore',
    'Rs 699.03 lacs'), so a raw number comparison is meaningless."""
    best = 0.0
    for m in _RS_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = (m.group(2) or "").lower()
        if u.startswith(("lakh", "lac")):
            v /= 100.0
        elif u.startswith(("million", "mn")):
            v /= 10.0
        elif u.startswith(("billion", "bn")):
            v *= 100.0
        elif not u:
            v /= 1e7                     # bare rupees
        if v > best:
            best = v
    return best


def is_noise(item):
    """Hygiene filing -- unless the HEADLINE itself carries a hard event, in
    which case the noise word is incidental ('Q1 results approved at AGM')."""
    title = item.get("title") or ""
    blob = title + " " + (item.get("text") or "")
    return bool(NOISE_RE.search(blob)) and not HARD_RE.search(title)


def impact_score(item):
    """0-100 with its own receipt. Returns (score, parts)."""
    title = item.get("title") or ""
    blob = title + " " + (item.get("text") or "")
    noise = is_noise(item)
    hard = bool(HARD_RE.search(blob))
    pct = pct_surprise(blob)
    cr = rupee_cr(blob)
    tov = item.get("tover_cr")

    cls = 25.0 if hard else 0.0
    shock = (pct / 3.0) * 0.55                       # 0..55
    scale = min(math.log10(1.0 + cr) * 22.0, 66.0) * 0.5   # 0..33
    if tov is None:
        liq = 0.0                                    # unknown -> do not reward
    elif tov >= GOOD_TURNOVER_CR:
        liq = 12.0
    elif tov >= MIN_TURNOVER_CR:
        liq = 6.0
    else:
        liq = -40.0                                  # untradeable, bury it
    pen = -60.0 if noise else 0.0

    score = cls + shock + scale + liq + pen
    score = int(max(0, min(100, round(score))))
    parts = {"cls": int(cls), "shock": int(round(shock)), "scale": int(round(scale)),
             "liq": int(liq), "noise": bool(noise), "hard": bool(hard),
             "pct": round(pct, 1), "cr": round(cr, 1)}
    return score, parts


def _market_live(now=None):
    """True during a live NSE session. Decides which ranking is honest: the
    stock's ACTUAL move (best evidence, only available intraday) or the text
    score (the only evidence available overnight)."""
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=15) <= now <= now.replace(hour=15, minute=30)


def _impact(it):
    """Rough importance so the single kept row per stock is the meaningful one.
    Results / orders / guidance outrank routine filings."""
    t = (it.get("title") or "").lower() + " " + (it.get("text") or "").lower()
    score = 0
    for kw, w in (("result", 6), ("profit", 6), ("q1", 5), ("q2", 5), ("q3", 5), ("q4", 5),
                  ("revenue", 4), ("pat ", 4), ("ebitda", 4), ("earnings", 4),
                  ("order", 4), ("contract", 4), ("win", 3), ("bags", 3),
                  ("dividend", 3), ("bonus", 3), ("split", 3), ("merger", 4),
                  ("acquisition", 4), ("stake", 3), ("upgrade", 4), ("downgrade", 4),
                  ("target price", 3), ("guidance", 4), ("expansion", 3),
                  ("approval", 3), ("licence", 3), ("license", 3), ("fund rais", 3)):
        if kw in t:
            score += w
    if it.get("sentiment") in ("positive", "negative"):
        score += 3
    return score


def curated(on_board=None, board_sids=None, drop_neutral=True):
    """ONE row per stock, neutral headlines dropped.

    Dhan publishes several snippets for the same company (an AGM note, a filing,
    then the actual result). Showing all of them buried the signal, so we keep a
    single best row per stock: highest impact first, newest as the tiebreak.
    """
    on_board = {str(s).upper() for s in (on_board or set())}
    board_sids = {str(s) for s in (board_sids or set())}
    items, err = fetch()
    # BIG MOVERS ARE NEVER DROPPED.
    #   The neutral filter exists to stop routine filings flooding the tab, but
    #   it was silently hiding real events: Chennai Petroleum ran +9.45% on
    #   Rs 492 Cr of turnover and did not appear anywhere in 128 news rows,
    #   because everything written about it was tagged "neutral". A stock moving
    #   this hard is newsworthy BY DEFINITION -- whatever a sentiment classifier
    #   thinks of the prose. So we price-check first and exempt the movers.
    attach_moves(items)
    for it in items:
        try:
            it["_big"] = abs(float(it.get("day_pct") or 0)) >= FORCE_KEEP_MOVE
        except (TypeError, ValueError):
            it["_big"] = False
    best = {}
    for it in items:
        if drop_neutral and not it.get("_big") \
                and it.get("sentiment") not in ("positive", "negative"):
            continue
        sym = (it.get("sym") or "").upper().strip()      # NSE ticker (sm_symbol)
        sid = str(it.get("nse") or "").strip()           # numeric security id
        key = sym or sid or (it.get("isin") or "")
        if not key:
            continue
        d = dict(it)
        hit = (sym and sym in on_board) or (sid and sid in board_sids)
        d["on_board"] = bool(hit)
        d["board_sym"] = sym if hit else None
        d["impact"] = _impact(it)
        cur = best.get(key)
        if cur is None or (d["impact"], d["ts"]) > (cur["impact"], cur["ts"]):
            best[key] = d
    out = list(best.values())
    attach_moves(out)                      # refresh (already attached pre-filter)
    live = _market_live()
    for d in out:
        d["tier"], d["tier_lbl"] = _tier(d.get("day_pct"))
        sc, parts = impact_score(d)        # needs tover_cr -> after attach_moves
        d["score"] = sc
        d["parts"] = parts
        d["noise"] = parts["noise"]
        d["dir"] = "DOWN" if d.get("sentiment") == "negative" else "UP"
        tov = d.get("tover_cr")
        d["tradeable"] = (tov is None) or (tov >= MIN_TURNOVER_CR)
        # tier_lbl is derived from day_pct, which is 0.00 for EVERY row once the
        # market shuts. Overnight, promote on the score instead so the column
        # still means something.
        if not live and not d["tier_lbl"]:
            if sc >= 60:
                d["tier"], d["tier_lbl"] = 0, "HIGH"
            elif sc >= 40:
                d["tier"], d["tier_lbl"] = 1, "MED"
    out = [d for d in out if not d["noise"]]      # drop hygiene filings outright
    # NSE-ONLY FILTER IS DISABLED.
    #   It blanked the News and Pre-market tabs. The helpers below still exist
    #   (is_nse / only_nse / nse_universe) but nothing calls them, because the
    #   symbol in this feed does not reliably match the NSE master's trading
    #   symbol -- so matching on it dropped essentially every row. See the note
    #   on only_nse() before re-enabling.
    # ORDER
    #   LIVE  -- the stock's ACTUAL move is the best evidence there is, so keep
    #            the old rule: HIGH movers first by size, then latest-first.
    #   CLOSED-- day_pct is dead, so rank by the text score, then latest-first.
    #            Untradeable names sink below everything regardless of score.
    if live:
        out.sort(key=lambda x: (0 if x["tier"] == 0 else 1,
                                -abs(x.get("day_pct") or 0) if x["tier"] == 0 else 0,
                                -x["ts"]))
    else:
        out.sort(key=lambda x: (0 if x["tradeable"] else 1, -x["score"], -x["ts"]))
    return out, err


def summary():
    cut_ms, cut_dt = session_cutoff()
    with _lock:
        items = list(_cache["items"])
        err = _cache["err"]
    pos = sum(1 for i in items if i["sentiment"] == "positive")
    neg = sum(1 for i in items if i["sentiment"] == "negative")
    return {"since": cut_dt.strftime("%d-%b %H:%M"), "count": len(items),
            "positive": pos, "negative": neg, "err": err,
            "ts": datetime.now(IST).strftime("%H:%M:%S")}
