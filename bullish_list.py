"""
bullish_list.py -- Box 3 of PLAN.md: the 9:09 "Highly bullish probability" list.

Sri's flow diagram: the pre-9AM watch inputs and the 09:00-09:08 pre-market
signals converge at 09:09, once matching is done and the real open is known,
into ONE list. MyWatchlist sits alongside it as a parallel input.

WHAT THIS IS, AND IS NOT
  It is a WATCH list. It decides what gets LOOKED AT from 09:15. It never
  gates, ranks-for-size, or lowers an entry bar. Sri, 04-Sep: "NO WAY. You
  decide based on your intelligence. This should be just one input to pay
  attention or keep an eye."  The tape still decides every buy.

INPUTS (each optional -- a missing one degrades the list, never breaks it)
  logs/movers_board/preopen_<day>.jsonl   pre-open gap + book imbalance
  logs/habitual_movers.json               habitually bullish names  (habitual_lab.py)
  logs/mywatchlist.json                   Sri's W1-W4 from Dhan
  logs/movers_board/shockers_<day>.jsonl  volume/price shockers  (from 08-Sep)
  Movers_dhannews.curated()               news reasons  (Windows-side only)

WHY EACH INPUT EARNED ITS PLACE
  pre-open  -- measured, 20 sessions: flagged names reached +2% on 61% of
               mornings against a 28% base rate, and it held in both halves
               of the sample (65%/26% then 55%/35%). Gap >=4% reached 86%.
               This is the strongest single signal found in the project.
  habitual  -- Sri's HFCL instinct, measured at the +2% target over 23 sessions.
  watchlist -- Sri's own names; monitored for the first 30 minutes regardless
               of the other filters (his instruction), then dropped unless
               something unusual appears.
  shockers  -- his sheet's Volume/Price Shockers, penny and illiquid excluded.
  news      -- the REASON a stock moves. Never sufficient on its own: "good
               news, traders sell off" is his own warning.

Usage:  python3 bullish_list.py [--day=YYYYMMDD] [--cutoff=09:08:00] [--quiet]
Writes: logs/bullish_0909_<day>.json
"""
import json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BD = HERE / "logs" / "movers_board"
LOGS = HERE / "logs"

DAY    = next((a.split("=")[1] for a in sys.argv if a.startswith("--day=")),
              datetime.now().strftime("%Y%m%d"))
CUTOFF = next((a.split("=")[1] for a in sys.argv if a.startswith("--cutoff=")), "09:08:00")
QUIET  = "--quiet" in sys.argv
MIN_PRICE = 100.0          # matches shipped MIN_PRICE; Rs20-100 measured 37% win
PENNY     = 20.0           # Sri's own definition of a penny stock


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def preopen(day):
    """symbol -> pre-open facts at CUTOFF, plus imbalance trend since 09:00."""
    f = BD / f"preopen_{day}.jsonl"
    if not f.exists():
        return {}
    first, last = {}, {}
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            hm = (snap.get("ts") or "")[-8:]
            for r in snap.get("rows") or []:
                s = r.get("sym")
                if not s:
                    continue
                if s not in first and r.get("imb") is not None:
                    first[s] = r["imb"]
                if hm <= CUTOFF:
                    last[s] = r
    for s, r in last.items():
        f0 = first.get(s)
        r["imb_trend"] = (round(r["imb"] / f0, 2)
                          if f0 and f0 > 0 and r.get("imb") is not None else None)
    return last


def shockers(day):
    """symbol -> last archived shocker row for the day (volume / price lists)."""
    f = BD / f"shockers_{day}.jsonl"
    if not f.exists():
        return {}
    out = {}
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            if (snap.get("ts") or "") > CUTOFF:
                continue
            for r in snap.get("rows") or []:
                if r.get("sym"):
                    out[r["sym"]] = r
    return out


def news_reasons(log=lambda m: None):
    """symbol -> short reason. Needs a live fetch, so Windows-side only."""
    try:
        import Movers_dhannews as dn
        rows, _ = dn.curated(drop_neutral=True)
        out = {}
        for it in rows or []:
            s = (it.get("sym") or "").upper()
            if s and it.get("dir") == "UP":
                out[s] = {"headline": (it.get("title") or "")[:120],
                          "score": it.get("score"), "tier": it.get("tier_lbl")}
        return out
    except Exception as e:
        log(f"news unavailable ({type(e).__name__}) -- list built without it")
        return {}


def build(day=DAY, log=print):
    po   = preopen(day)
    sh   = shockers(day)
    hab  = {r["sym"]: r for r in (_load(LOGS / "habitual_movers.json", []) or [])}
    wl   = _load(LOGS / "mywatchlist.json", {}) or {}
    news = news_reasons(log)

    watch = {}
    for name in ("W1", "W2", "W3", "W4"):
        for r in (wl.get("lists") or {}).get(name, []):
            watch.setdefault(r["sym"], {"sid": r.get("sid"),
                                        "series": r.get("series"), "lists": []})
            watch[r["sym"]]["lists"].append(name)

    rows = defaultdict(lambda: {"why": [], "src": []})
    def add(sym, src, why, **kw):
        r = rows[sym]
        if src not in r["src"]:
            r["src"].append(src)
        if why:
            r["why"].append(why)
        r.update(kw)

    for s, r in po.items():
        v = r.get("verdict") or ""
        add(s, "preopen",
            f"pre-open {v.lower()} {r.get('pct')}% imb {r.get('imb')}",
            price=r.get("price"), gap=r.get("pct"), imb=r.get("imb"),
            imb_trend=r.get("imb_trend"), verdict=v, side=r.get("side"),
            sid=str(r.get("sid") or ""))

    for s, r in sh.items():
        px = r.get("ltp") or 0
        if px and px < PENNY:                      # Sri: penny = under Rs20
            continue
        lists = ",".join(r.get("lists") or [])
        add(s, "shocker", f"shocker [{lists}] {r.get('day_pct')}%",
            price=rows[s].get("price") or px, day_pct=r.get("day_pct"),
            tover_min=r.get("tover_min"), sid=rows[s].get("sid") or str(r.get("sid") or ""))

    for s, r in hab.items():
        add(s, "habitual",
            f"habitual {r['hit_pct']}% of {r['sessions']} sessions offer +2%",
            hab_hit=r["hit_pct"], hab_sessions=r["sessions"])

    for s, r in watch.items():
        add(s, "watchlist", f"MyWatchlist {'/'.join(r['lists'])}",
            sid=rows[s].get("sid") or r.get("sid"), series=r.get("series"))

    for s, r in news.items():
        add(s, "news", f"news: {r['headline']}", news_score=r.get("score"))

    out = []
    for s, r in rows.items():
        px = r.get("price") or 0
        r["sym"] = s
        r["n_src"] = len(r["src"])
        # NOT a score for sizing -- only an ordering, so the eye starts where
        # the evidence is.
        #
        # First version ranked by "how many inputs agree". Measured over 21
        # sessions that was worth almost nothing: 2+ sources reached +2% on 34%
        # of mornings against a 28% base rate, while pre-open alone reached 62%
        # and a pre-open gap >= 4% reached 86%. Habitual (29%) and watchlist
        # (25%) do not predict a GIVEN morning at all -- they say a name often
        # moves, not that it will move today. So they stay as watch inputs and
        # carry no ordering weight.
        po_up = 1 if r.get("side") == "UP" else 0
        r["attention"] = (po_up * (40 + 6 * min(r.get("gap") or 0, 10)
                                   + (10 if "STRONG" in (r.get("verdict") or "") else 0))
                          + (8 if r.get("side") == "DOWN" else 0)
                          + (2 if "shocker" in r["src"] else 0))
        r["tradeable"] = (px >= MIN_PRICE) if px else None
        r["why"] = "; ".join(r["why"])
        out.append(r)
    out.sort(key=lambda r: -r["attention"])

    p = LOGS / f"bullish_0909_{day}.json"
    p.write_text(json.dumps({"day": day, "cutoff": CUTOFF,
                             "inputs": {"preopen": len(po), "shockers": len(sh),
                                        "habitual": len(hab), "watchlist": len(watch),
                                        "news": len(news)},
                             "n": len(out), "rows": out}, indent=1), encoding="utf-8")
    if not QUIET:
        log(f"bullish_list {day}: preopen={len(po)} shockers={len(sh)} "
            f"habitual={len(hab)} watchlist={len(watch)} news={len(news)} -> {len(out)} names")
        log(f"{'SYM':<14}{'src':>4}  {'why'}")
        for r in out[:20]:
            log(f"{r['sym']:<14}{r['n_src']:>4}  {r['why'][:96]}")
        log(f"wrote {p}")
    return out


if __name__ == "__main__":
    build()
