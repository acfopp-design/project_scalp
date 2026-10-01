"""
Movers_sectors.py -- stock -> sector, from NSE itself.

WHY NOT DHAN
    This project had no sector data at all. security_id_list.csv has 16 columns
    and none is sector; universe.csv has three; logs/fundamentals held exactly
    ONE file, written by hand. Dhan's news items carry a `cat` field that turns
    out to be a news-desk category, not a sector -- 477 of 498 items on 01-Sep
    were simply 'companies'. Six ScanX sector endpoints were probed: five 404,
    one 405 (exists, wrong verb) and nothing usable came back.

WHERE THIS COMES FROM INSTEAD
    NSE publishes its own classification as a plain CSV, no auth, no key:

        nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv
        header: Company Name,Industry,Symbol,Series,ISIN Code

    That is NSE Indices' own industry classification -- the same taxonomy the
    sectoral indices are built from -- for the 750 stocks of the Total Market
    index. ind_nifty500list.csv is the fallback if the first is unavailable.

HONEST LIMIT, STATED UP FRONT
    750 stocks is not 2,455. The board's movers are heavily small-cap and SME,
    and those will not be in it. A stock with no sector is reported as UNKNOWN
    and never silently folded into a sector it does not belong to. Coverage is
    printed every refresh so the gap is visible rather than assumed.

    That limit is much less painful than it sounds: the stocks that generate
    sector NEWS are the ones in this file. The 97% of movers with no headline
    are mostly the same names that have no sector here.

Refreshed once a day and cached to logs/sector_map.json, so a restart is free.
"""
import csv
import io
import json
import re
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "logs" / "sector_map.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)

SOURCES = [
    ("NIFTY Total Market (750)",
     "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"),
    ("NIFTY 500",
     "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"),
]
# NSE refuses a bare urllib user-agent, so present as a browser. Nothing here is
# behind a login; these are the same public files their website links to.
HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
       "Accept": "text/csv,*/*", "Accept-Language": "en-US,en;q=0.9"}

_lock = threading.Lock()
_map = {}          # SYMBOL -> sector
_names = {}        # normalised company name -> SYMBOL (for headline tagging)
_meta = {"day": None, "n": 0, "source": None, "err": None}


def _download(url):
    req = urllib.request.Request(url, headers=HDR, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def refresh(log=lambda m: None, force=False):
    """Rebuild the map at most once a day. Returns the number of stocks mapped."""
    day = datetime.now().strftime("%Y%m%d")
    with _lock:
        if _map and _meta["day"] == day and not force:
            return len(_map)
    if not force and CACHE.exists():
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if d.get("day") == day and d.get("map") and d.get("names"):
                with _lock:
                    _map.clear(); _map.update(d["map"])
                    _names.clear(); _names.update(d["names"])
                    _meta.update({"day": day, "n": len(_map),
                                  "source": d.get("source"), "err": None})
                log(f"sectors: {len(_map)} stocks from cache ({d.get('source')})")
                return len(_map)
        except Exception:
            pass
    for name, url in SOURCES:
        try:
            txt = _download(url)
            rows = list(csv.DictReader(io.StringIO(txt)))
            got, nm = {}, {}
            for r in rows:
                sym = (r.get("Symbol") or "").strip().upper()
                ind = (r.get("Industry") or "").strip()
                if sym and ind:
                    got[sym] = ind
                # The same CSV carries "Company Name". A headline says "Tata
                # Motors", never "TATAMOTORS", so without this the news tagger
                # can only catch the handful of tickers printed as tickers.
                key = _norm_name(r.get("Company Name"))
                if key and sym:
                    nm[key] = sym
            if len(got) < 100:
                raise ValueError(f"only {len(got)} rows parsed")
            with _lock:
                _map.clear(); _map.update(got)
                _names.clear(); _names.update(nm)
                _meta.update({"day": day, "n": len(got), "source": name, "err": None})
            CACHE.write_text(json.dumps({"day": day, "source": name, "map": got,
                                         "names": nm}), encoding="utf-8")
            log(f"sectors: {len(got)} stocks mapped from {name}")
            return len(got)
        except Exception as e:
            _meta["err"] = f"{name}: {type(e).__name__} {str(e)[:80]}"
            log(f"sectors: {name} failed -- {type(e).__name__} {str(e)[:80]}")
    return 0


# Company-name suffixes that carry no identity. "Tata Motors Limited" and
# "Tata Motors" must reduce to the same key, or a headline never matches.
_DROP = re.compile(r"\b(limited|ltd|industries|industry|corporation|corp|"
                   r"company|co|enterprises|india|indian|the|and|of|"
                   r"international|holdings|group|plc|inc)\b", re.I)


def _norm_name(s):
    """A company name reduced to its identifying words. Returns '' when what is
    left is too short to be safe to match on."""
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    s = _DROP.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 2+ words, or one long word. "3M" and "ITC" reduce to something a headline
    # would hit by accident, so they are left to the ticker matcher instead.
    if len(s) < 6 or (" " not in s and len(s) < 8):
        return ""
    return s


def tag_from_text(text):
    """Symbols a headline plausibly refers to, matched on COMPANY NAME.

    Longest name first, so "Tata Motors Finance" wins over "Tata Motors" and a
    headline is not credited to both."""
    t = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    t = " " + re.sub(r"\s+", " ", t).strip() + " "
    with _lock:
        items = sorted(_names.items(), key=lambda x: -len(x[0]))
    out, used = [], []
    for key, sym in items:
        if (" " + key + " ") in t and not any(key in u for u in used):
            used.append(key)
            out.append(sym)
            if len(out) >= 6:
                break
    return out


def sector_of(sym):
    """Sector for one symbol, or None. Never guesses."""
    with _lock:
        return _map.get(str(sym or "").upper())


def summary():
    with _lock:
        return dict(_meta)


def coverage(symbols):
    """(known, unknown) for a list of symbols -- so the gap is always visible."""
    known = [s for s in symbols if sector_of(s)]
    return len(known), [s for s in symbols if not sector_of(s)]


# ==========================================================================
#  SECTOR BREADTH -- the part that does NOT depend on news
# ==========================================================================
# The News tab's WHY THEY'RE MOVING sub-tab matched board movers to Dhan
# headlines by symbol and got a 3% hit rate: of 280 movers on 01-Sep, 32 had any
# headline and 10 had a curated one. It was not broken -- his movers are
# small-caps that generate no coverage, so "no news" was the correct answer 97%
# of the time. A tab that is right and useless is still useless.
#
# The replacement does not start from news. It starts from PRICE, across the
# whole 752-stock mapped universe, and asks a question news cannot answer:
#
#     is this stock moving on its own, or is its whole sector moving?
#
# That reads from the alarm's existing sweep, so it costs no request, covers
# every mapped stock rather than the ~40 on the board, and works on a morning
# with no news at all. Headlines are then ATTACHED to sectors where they exist,
# instead of being the thing the tab depends on.
_hist = []          # [(epoch, {sector: avg_pct})] -- for "is it still going?"
_HIST_MAX = 40      # ~10 minutes at a 15s cadence


def breadth(alarm, news_items=None, min_pct=1.0, log=lambda m: None):
    """Live per-sector breadth from the whole-market sweep. No extra requests.

    Returns rows sorted by how strongly the sector is moving, each with the
    stocks driving it and any headlines that belong to it.
    """
    import time as _t
    if alarm is None:
        return [], {"err": "alarm not loaded"}
    try:
        _, snap = alarm.snapshot()
        uni = alarm.universe()
    except Exception as e:
        return [], {"err": f"{type(e).__name__}: {str(e)[:70]}"}
    if not snap:
        return [], {"err": "no sweep yet"}
    name_of = {str(k): v for k, v in (uni or {}).items()}
    name_of.update({str(k): v for k, v in (uni or {}).items()})

    buckets = {}
    for sid, q in snap.items():
        sym = name_of.get(str(sid))
        if not sym:
            continue
        sec = sector_of(sym)
        if not sec:
            continue                      # never guess; unmapped stays unmapped
        try:
            ltp = float(q[0] or 0)
            prev = float(q[7] or 0) if len(q) > 7 else 0.0
            vol = float(q[1] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        if ltp <= 0 or prev <= 0:
            continue
        pct = (ltp / prev - 1.0) * 100.0
        b = buckets.setdefault(sec, {"n": 0, "up": 0, "dn": 0, "sum": 0.0,
                                     "movers": [], "tover": 0.0})
        b["n"] += 1
        b["sum"] += pct
        b["tover"] += ltp * vol
        if pct >= min_pct:
            b["up"] += 1
        elif pct <= -min_pct:
            b["dn"] += 1
        b["movers"].append((sym, round(pct, 2)))

    # headlines -> sector, where the stock is one we can map
    heads = {}
    for it in (news_items or []):
        sym = str(it.get("sym") or "").upper()
        sec = sector_of(sym)
        if not sec:
            continue
        heads.setdefault(sec, []).append(
            {"sym": sym, "title": (it.get("title") or "")[:110],
             "when": it.get("when"), "sentiment": it.get("sentiment")})

    now = _t.time()
    prev_avg = _hist[0][1] if _hist else {}
    rows = []
    for sec, b in buckets.items():
        if b["n"] < 3:
            continue                      # too few names to call it a sector move
        avg = b["sum"] / b["n"]
        b["movers"].sort(key=lambda x: -x[1])
        h = heads.get(sec, [])
        pos = sum(1 for x in h if x.get("sentiment") == "positive")
        neg = sum(1 for x in h if x.get("sentiment") == "negative")
        was = prev_avg.get(sec)
        rows.append({
            "sector": sec, "n": b["n"], "up": b["up"], "dn": b["dn"],
            "avg_pct": round(avg, 2),
            "breadth": round(b["up"] * 100.0 / b["n"], 0),
            "tover_cr": round(b["tover"] / 1e7, 0),
            "top": b["movers"][:5], "worst": b["movers"][-3:][::-1],
            "news_n": len(h), "news_pos": pos, "news_neg": neg,
            "news": h[:4],
            # IS THE SENTIMENT STILL GOING? Not an opinion -- the sector's own
            # average move now against where it was ~10 minutes ago.
            # CONVICTION, the sort key. Realty read +1.88% in a test where ONE
            # stock was +9% and four were flat -- ranking on the average alone
            # would have put that above a sector where all five stocks were
            # +3%. Participation is what separates a sector move from an
            # outlier, so it is priced into the ranking, and Breadth is shown
            # beside it so the order never looks arbitrary.
            "conviction": round(abs(avg) * (0.4 + 0.6 * ((b["up"] + b["dn"]) / b["n"])), 3),
            "was_pct": (round(was, 2) if was is not None else None),
            "delta": (round(avg - was, 2) if was is not None else None),
            "continuing": (None if was is None else bool(avg > was and avg > 0)),
        })
    rows.sort(key=lambda r: -r["conviction"])
    _hist.append((now, {r["sector"]: r["avg_pct"] for r in rows}))
    while len(_hist) > _HIST_MAX:
        _hist.pop(0)
    meta = {"sectors": len(rows), "mapped": len(_map),
            "span_min": round((now - _hist[0][0]) / 60.0, 1) if _hist else 0,
            "source": _meta.get("source"), "err": None}
    return rows, meta
