"""
Movers_sectornews.py -- sector headlines from the OPEN WEB, not from ScanX.

WHY THIS EXISTS
    The News tab's "WHY THEY'RE MOVING" sub-tab was fed by one source: Dhan's
    own news stream. Measured on 01-Sep it explained roughly 3% of board movers
    -- every other row read "no Dhan news". A tab where 97 rows in 100 say
    "nothing found" is not a tab, so the sub-tab is being replaced by a SECTOR
    view, and the sector view needs headlines that do not come from Dhan.

WHERE THESE COME FROM
    Google News RSS, one query per NSE sector, India edition. No key, no login,
    no scraping of a rendered page -- it is the public RSS endpoint a feed
    reader would use:

        news.google.com/rss/search?q=<terms>&hl=en-IN&gl=IN&ceid=IN:en

    Google News is an AGGREGATOR, so one query reaches Moneycontrol, Economic
    Times, Business Standard, Mint, Reuters, Upstox and the rest at once. That
    is the point: querying each publisher's own RSS separately would be a dozen
    feeds to maintain for a strictly smaller set of articles.

    Verified 01-Sep-2026 06:24 GMT: the "auto stocks" query returned a live
    <item> list including "Stocks to watch, Sept 1: Auto stocks, ITC, ..."
    dated the same morning. This is a working endpoint, not an assumption.

THE WINDOW IS THE TRADING SESSION, NOT "TODAY"
    Sri asked for previous-session close -> now. A headline at 21:40 last night
    is exactly the kind that gaps a sector at 09:15; a calendar-day filter would
    throw it away. session_cutoff() returns the previous trading close (15:30
    IST, walking back over the weekend), and every item older than that is
    dropped after the fetch.

WHAT THIS DELIBERATELY DOES NOT DO
    It does not decide WHY a stock is moving. That was the old sub-tab's promise
    and it could not keep it. Here, headlines are ATTACHED to a sector whose
    move was measured from price, and the price is what ranks the table. A
    sector with no news still shows its move; a sector with news gets context.
"""
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "logs" / "sector_news.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))

HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
       "Accept": "application/rss+xml,application/xml,text/xml,*/*",
       "Accept-Language": "en-IN,en;q=0.9"}

TTL = 300          # refresh at most every 5 minutes
PER_REQ_SLEEP = 0.4  # be a polite client; 22 sectors ~ 9s per full refresh
TIMEOUT = 15

# The 22 sectors NSE's own classification uses (see Movers_sectors.py), each
# with the words an Indian markets desk actually prints. Terms are quoted so
# Google matches the phrase, and every query is scoped to India by the locale
# parameters rather than by adding "India" to the query, which drowns out the
# sector words themselves.
QUERIES = {
    "Financial Services":        '"bank stocks" OR "Nifty Bank" OR "NBFC" OR "banking stocks"',
    "Capital Goods":             '"capital goods stocks" OR "engineering stocks" OR "order book" defence OR infrastructure',
    "Healthcare":                '"pharma stocks" OR "Nifty Pharma" OR "USFDA" OR "hospital stocks"',
    "Automobile and Auto Components": '"auto stocks" OR "Nifty Auto" OR "auto sales" OR "vehicle sales"',
    "Consumer Services":         '"retail stocks" OR "hotel stocks" OR "aviation stocks" OR "airline stocks"',
    "Fast Moving Consumer Goods": '"FMCG stocks" OR "Nifty FMCG" OR "consumer goods stocks"',
    "Chemicals":                 '"chemical stocks" OR "specialty chemicals" OR "agrochemical"',
    "Consumer Durables":         '"consumer durables stocks" OR "white goods" OR "electronics manufacturing"',
    "Information Technology":    '"IT stocks" OR "Nifty IT" OR "TCS Infosys" OR "IT services deal"',
    "Services":                  '"logistics stocks" OR "shipping stocks" OR "port stocks"',
    "Metals & Mining":           '"metal stocks" OR "Nifty Metal" OR "steel prices" OR "mining stocks"',
    "Construction":              '"infrastructure stocks" OR "construction stocks" OR "road orders" OR "EPC order"',
    "Power":                     '"power stocks" OR "PSU power" OR "electricity demand" OR "renewable energy stocks"',
    "Oil Gas & Consumable Fuels": '"oil stocks" OR "OMC stocks" OR "crude oil" India OR "gas stocks"',
    "Realty":                    '"realty stocks" OR "Nifty Realty" OR "real estate stocks"',
    "Construction Materials":    '"cement stocks" OR "cement prices" OR "cement demand"',
    "Telecommunication":         '"telecom stocks" OR "Bharti Airtel Vodafone" OR "tariff hike" telecom',
    "Textiles":                  '"textile stocks" OR "apparel exports" OR "cotton prices"',
    "Media Entertainment & Publication": '"media stocks" OR "entertainment stocks" OR "broadcast"',
    "Utilities":                 '"utility stocks" OR "water utility" OR "gas distribution"',
    "Diversified":               '"conglomerate stocks" OR "diversified stocks" India',
    "Forest Materials":          '"paper stocks" OR "paper prices" India',
}
# Broad market feed -- catches the session-wide driver (RBI, Fed, GST, budget)
# that belongs to no single sector but explains all of them at once.
MARKET_QUERY = ('"Sensex" OR "Nifty 50" OR "Indian stock market" '
                'OR "RBI policy" OR "FII selling"')

# ---------------------------------------------------------------- sentiment
# Dhan's own classifier returned "positive" for ~77% of its feed, which is why
# this project stopped trusting it. This is a plain lexicon: it counts words a
# markets desk uses when something got better or worse. It is not clever, and
# is not asked to be -- a headline it cannot read stays NEUTRAL and is shown
# as neutral rather than guessed at.
_POS = re.compile(r"\b(surge|surges|surged|jump|jumps|jumped|rally|rallies|rallied|"
                  r"gain|gains|gained|rise|rises|rose|soar|soars|soared|"
                  r"upgrade|upgrades|upgraded|beat|beats|record high|all-time high|"
                  r"profit rises|profit jumps|order win|wins order|bags order|"
                  r"approval|approved|hike|boost|boosts|outperform|buy rating|"
                  r"strong demand|multibagger|upper circuit|bullish|revival)\b", re.I)
_NEG = re.compile(r"\b(fall|falls|fell|drop|drops|dropped|slump|slumps|slumped|"
                  r"plunge|plunges|plunged|crash|crashes|crashed|decline|declines|"
                  r"declined|slide|slides|slid|tumble|tumbles|tumbled|"
                  r"downgrade|downgrades|downgraded|miss|misses|missed|"
                  r"loss|losses|weak|weakness|cut|cuts|probe|raid|penalty|fine|"
                  r"lower circuit|bearish|selloff|sell-off|warning|default|"
                  r"resign|resigns|resigned|recall|ban|banned|halt)\b", re.I)


def tone(text):
    t = text or ""
    p, n = len(_POS.findall(t)), len(_NEG.findall(t))
    if p > n:
        return "positive"
    if n > p:
        return "negative"
    return "neutral"


# ---------------------------------------------------------------- session window
def session_cutoff(now=None):
    """Previous trading session's close (15:30 IST), walking back over the
    weekend. Before today's open that is yesterday's close; after it, it is
    still yesterday's close, because Sri wants overnight news to stay visible
    through the morning he trades."""
    now = now or datetime.now(IST)
    close_today = now.replace(hour=15, minute=30, second=0, microsecond=0)
    # At 09:08 the last close was YESTERDAY's. After 15:30 it is today's.
    ref = now.date() if now >= close_today else now.date() - timedelta(days=1)
    while ref.weekday() >= 5:          # Sat/Sun -> walk back to Friday
        ref -= timedelta(days=1)
    return datetime(ref.year, ref.month, ref.day, 15, 30, tzinfo=IST)


_RSS_DATE = "%a, %d %b %Y %H:%M:%S %Z"


def _parse_date(s):
    try:
        # Google News stamps GMT; treat it as UTC and convert to IST
        dt = datetime.strptime(s.strip(), _RSS_DATE)
        return dt.replace(tzinfo=timezone.utc).astimezone(IST)
    except Exception:
        return None


_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_DATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_SRC = re.compile(r"<source[^>]*>(.*?)</source>", re.S)


def _clean(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s or "", flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _fetch_feed(query, log=lambda m: None):
    url = ("https://news.google.com/rss/search?q="
           # 4d, not 1d: on a Monday morning the previous close is FRIDAY, three
           # days back. The cutoff filter below trims whatever is too old.
           + urllib.parse.quote(query + " when:4d")
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "ignore")


def _items_from(xml, cutoff):
    out = []
    for blk in _ITEM.findall(xml or ""):
        t = _TITLE.search(blk)
        if not t:
            continue
        title = _clean(t.group(1))
        if not title:
            continue
        # Google appends " - Publisher" to every title; split it off so the
        # source is its own column instead of noise inside the headline.
        src = _SRC.search(blk)
        src = _clean(src.group(1)) if src else ""
        if src and title.endswith(" - " + src):
            title = title[: -(len(src) + 3)].strip()
        d = _DATE.search(blk)
        when = _parse_date(_clean(d.group(1))) if d else None
        if when is None or when < cutoff:
            continue                      # older than the previous close
        ln = _LINK.search(blk)
        out.append({
            "title": title,
            "url": _clean(ln.group(1)) if ln else "",
            "src": src or "Google News",
            "ts": when.timestamp(),
            "when": when.strftime("%d-%b %H:%M"),
            "sentiment": tone(title),
        })
    return out


# ---------------------------------------------------------------- stock tagging
# Which stocks a headline could actually touch. A headline is tagged with a
# symbol only when the SYMBOL ITSELF appears in it, on a word boundary, and is
# not an English word -- "CUB", "ITC", "SAIL" are real tickers, and SAIL is
# also a verb, so a stoplist is not optional.
_STOP = {"ALL", "AND", "ANY", "ARE", "BUY", "CAN", "FOR", "GET", "HIGH", "IT",
         "ITS", "LOW", "MAY", "NEW", "NOW", "ONE", "OUT", "SAIL", "SET", "TOP",
         "TWO", "WAY", "WIN", "YES", "BSE", "NSE", "IPO", "GDP", "CEO", "CFO",
         "GST", "RBI", "SEBI", "FII", "DII", "USA", "INR", "PSU", "NIFTY"}


def tag_stocks(title, symbols, sectors=None):
    """Symbols a headline plausibly touches.

    TWO passes, because headlines are written for humans:
      - the TICKER, when it is printed as a ticker ("ITC", "TBZ")
      - the COMPANY NAME, via Movers_sectors.tag_from_text ("Tata Motors")
    Name matching is what makes this usable -- measured on a real Google News
    auto feed, ticker-only tagging caught 2 of 22 headlines and missed
    "Tata Motors", "Maruti Suzuki" and "Lenskart" entirely.
    """
    hits, up = [], title.upper()
    for sym in symbols:
        if len(sym) < 3 or sym in _STOP:
            continue
        if re.search(r"(?<![A-Z0-9])" + re.escape(sym) + r"(?![A-Z0-9])", up):
            hits.append(sym)
    if sectors is not None:
        for sym in sectors.tag_from_text(title):
            if sym not in hits:
                hits.append(sym)
    return hits[:6]


# ---------------------------------------------------------------- public API
_lock = threading.Lock()
_state = {"t": 0.0, "by_sector": {}, "market": [], "err": None,
          "cutoff": None, "sectors_ok": 0, "sectors_err": 0}


def fetch(force=False, symbols=None, log=lambda m: None):
    """Refresh every sector feed. Returns ({sector: [items]}, market_items).

    Cached for TTL seconds, so the board's poll never triggers a fetch -- only
    the background loop does, and 22 requests every 5 minutes to a Google
    endpoint is nowhere near any of Dhan's limits because it is not Dhan.
    """
    with _lock:
        if not force and (time.time() - _state["t"]) < TTL and _state["by_sector"]:
            return dict(_state["by_sector"]), list(_state["market"])

    try:
        import Movers_sectors as _sectors
    except Exception:
        _sectors = None

    cutoff = session_cutoff()
    by_sec, ok, bad, err = {}, 0, 0, None
    syms = set(symbols or ())

    for sec, q in QUERIES.items():
        try:
            items = _items_from(_fetch_feed(q, log), cutoff)
            for it in items:
                it["stocks"] = tag_stocks(it["title"], syms, _sectors)
            items.sort(key=lambda x: -x["ts"])
            by_sec[sec] = items[:12]
            ok += 1
        except Exception as e:
            bad += 1
            err = f"{type(e).__name__}: {str(e)[:70]}"
            log(f"sectornews: {sec} failed -- {err}")
        time.sleep(PER_REQ_SLEEP)

    market = []
    try:
        market = _items_from(_fetch_feed(MARKET_QUERY, log), cutoff)
        for it in market:
            it["stocks"] = tag_stocks(it["title"], syms, _sectors)
        market.sort(key=lambda x: -x["ts"])
        market = market[:10]
    except Exception as e:
        log(f"sectornews: market feed failed -- {e}")

    # CROSS-FILE. A headline found by the auto query that names a cement company
    # belongs to Construction Materials too. Without this pass a sector only
    # ever sees what its own search terms happened to return, and the terms are
    # a guess; the stock named in the headline is not.
    if _sectors is not None:
        seen = {s: {i.get("url") for i in v} for s, v in by_sec.items()}
        for src_items in list(by_sec.values()) + [market]:
            for it in src_items:
                for sym in it.get("stocks") or []:
                    tgt = _sectors.sector_of(sym)
                    if not tgt or tgt not in by_sec:
                        continue
                    if it.get("url") in seen.setdefault(tgt, set()):
                        continue
                    seen[tgt].add(it.get("url"))
                    by_sec[tgt].append(dict(it, via=sym))
        for s in by_sec:
            by_sec[s].sort(key=lambda x: -x["ts"])
            by_sec[s] = by_sec[s][:12]

    with _lock:
        _state.update({"t": time.time(), "by_sector": by_sec, "market": market,
                       "err": (err if ok == 0 else None),
                       "cutoff": cutoff.strftime("%d-%b %H:%M"),
                       "sectors_ok": ok, "sectors_err": bad})
    try:
        CACHE.write_text(json.dumps({"t": _state["t"], "by_sector": by_sec,
                                     "market": market}, indent=1), encoding="utf-8")
    except Exception:
        pass
    log(f"sectornews: {ok} sectors ok, {bad} failed, "
        f"{sum(len(v) for v in by_sec.values())} headlines since {_state['cutoff']}")
    return dict(by_sec), list(market)


def cached():
    with _lock:
        return dict(_state["by_sector"]), list(_state["market"])


def summary():
    with _lock:
        return {"age_sec": (round(time.time() - _state["t"]) if _state["t"] else None),
                "cutoff": _state["cutoff"], "err": _state["err"],
                "sectors_ok": _state["sectors_ok"], "sectors_err": _state["sectors_err"],
                "headlines": sum(len(v) for v in _state["by_sector"].values()),
                "market_n": len(_state["market"])}


if __name__ == "__main__":
    def _log(m):
        print(m)
    t0 = time.time()
    bs, mk = fetch(force=True, log=_log)
    print(f"\n-- {time.time()-t0:.1f}s --")
    print("cutoff:", summary()["cutoff"])
    for s, v in sorted(bs.items(), key=lambda x: -len(x[1])):
        print(f"\n{s}  ({len(v)})")
        for it in v[:3]:
            print(f"   [{it['sentiment'][:3]}] {it['when']}  {it['title'][:88]}  ({it['src']})")
    print(f"\nMARKET ({len(mk)})")
    for it in mk[:5]:
        print(f"   [{it['sentiment'][:3]}] {it['when']}  {it['title'][:88]}  ({it['src']})")
    print("\n", summary())
