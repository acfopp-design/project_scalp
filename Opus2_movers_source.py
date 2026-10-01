"""
Opus2_movers_source.py  --  Opus2-ONLY name source (scanx 3-list discovery).

Replaces the full-universe sweep (Opus_quotes_v3.cash_names) for the Opus2
project ONLY. Discovery now comes from EXACTLY the three Dhan web-terminal
(scanx) lists, pulled with the DHAN_SCANX_JWT in env.txt:

    Price Movers     -> scanx/daygnl         TypeFlag=G, DayLevelIndicator=1
    By Volumes       -> scanx/topvolume      TypeFlag="", DayLevelIndicator=0
    Intraday Movers  -> scanx/intrarecfallv2 TypeFlag=H, DayLevelIndicator=0

Endpoints + params were verified LIVE against web.dhan.co (Markets -> Stocks
tabs) by capturing the page's own network calls.

Behaviour:
  * The three lists are merged and de-duped by securityId. Each surviving row
    keeps a `lists` tag naming which of the three lists it came from.
  * NO admission gates are applied (no price floor / move-band / liquidity /
    circuit filtering) -- every name in the lists flows through, so the board
    matches what Dhan shows.
  * scanx returns each row's securityId directly, so no name->sid resolver is
    needed, and liquidity is estimated from the list's own ltp/tvol/tval, so
    this works with markets CLOSED (last trading session's lists).

Isolation: the shared file Opus_quotes_v3.py is intentionally NOT modified, so
the Opus :5003 project is completely unaffected. We only reuse its read-only
helpers (_env, _scanx_post, _items, liquidity_grade).
"""
from pathlib import Path
import Opus_quotes_v3 as q

SECIDX_ALL_NSE = 700        # All-NSE equity universe (same code the Dhan tabs use)
COUNT = 50                  # Dhan shows top 50 per list
SESSION_MINUTES = 375.0     # 09:15-15:30 -> used to estimate a per-minute turnover

# label -> (endpoint, extra params).  Verified via network capture on web.dhan.co
LISTS = [
    ("Price Movers",    "daygnl",         {"TypeFlag": "G", "DayLevelIndicator": 1}),
    ("By Volumes",      "topvolume",      {"TypeFlag": "",  "DayLevelIndicator": 0}),
    ("Intraday Movers", "intrarecfallv2", {"TypeFlag": "H", "DayLevelIndicator": 0}),
]

_BASE = {"Seg": 1, "SecIdxCode": SECIDX_ALL_NSE, "Count": COUNT,
         "ExpCode": -1, "Instrument": "EQUITY"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_list(endpoint, params, jwt, log):
    body = dict(_BASE); body.update(params)
    try:
        res = q._scanx_post(f"https://scanx.dhan.co/scanx/{endpoint}", body, jwt)
        return q._items(res)
    except Exception as e:
        log(f"movers: {endpoint} error {e}")
        return []


def cash_names(resolver=None, log=lambda m: None):
    """Return (names, err). `names` = merged/de-duped rows from the 3 scanx lists,
    in the shape the Opus2 pipeline expects:
        sym, sid, day_pct, tvol, tover_min, day_tover, vel_pct_min, vol_rate_x,
        ucl, lists

    `resolver` is accepted (and ignored) so this is a drop-in replacement for
    Opus_quotes_v3.cash_names(resolver, log)."""
    cid, tok, jwt = q._env()
    if not jwt:
        log("movers: no scanx jwt in env.txt -> API sweep fallback")
        return _api_sweep_lists(log)

    merged = {}
    per_list = {}
    for label, endpoint, params in LISTS:
        items = _fetch_list(endpoint, params, jwt, log)
        per_list[label] = len(items)
        for it in items:
            sid = it.get("sid")
            sym = it.get("sym")
            if sid is None or not sym:
                continue
            sid = str(sid)
            ltp = _f(it.get("ltp"))
            vol = _f(it.get("tvol")) or 0.0
            pch = _f(it.get("pchng"))
            tval = _f(it.get("tval"))
            day_tover = tval if tval else ((ltp or 0.0) * vol)

            rec = merged.get(sid)
            if rec is None:
                merged[sid] = {
                    "sym": str(sym).upper(),
                    "sid": sid,
                    "ltp": ltp,              # for the price filter (pre-candle)
                    "day_pct": round(pch, 2) if pch is not None else None,
                    "tvol": vol,
                    "day_tover": int(day_tover) if day_tover else 0,
                    "tover_min": int(day_tover / SESSION_MINUTES) if day_tover else 0,
                    "vel_pct_min": 0.0,      # no intra-sweep history when list-sourced
                    "vol_rate_x": None,
                    "ucl": None,             # app backfills upper-circuit in one batch
                    "lists": [label],
                }
            else:
                if label not in rec["lists"]:
                    rec["lists"].append(label)
                if rec.get("ltp") is None and ltp is not None:
                    rec["ltp"] = ltp
                if rec.get("day_pct") is None and pch is not None:
                    rec["day_pct"] = round(pch, 2)
                if vol and vol > (rec.get("tvol") or 0):
                    rec["tvol"] = vol
                if day_tover and day_tover > (rec.get("day_tover") or 0):
                    rec["day_tover"] = int(day_tover)
                    rec["tover_min"] = int(day_tover / SESSION_MINUTES)

    names = list(merged.values())
    names.sort(key=lambda x: -(x.get("day_pct") or 0))
    _archive(names, per_list, log)
    log("movers: 3 scanx lists "
        + " ".join(f"{lbl}={per_list.get(lbl, 0)}" for lbl, _, _ in LISTS)
        + f" -> {len(names)} unique names")
    if not names:
        # SCANX DOWN (token expired / 401): fall back to the official API sweep so
        # the feed NEVER halts. Uses the API access token, which lasts all day.
        log("movers: scanx empty -> falling back to API market sweep")
        return _api_sweep_lists(log)
    return names, None



# ---------------------------------------------------------------- archiving
# 04-Sep: Sri's instruction sheet sets the pre-market shortlist from Dhan's
# Volume Shockers and Price Shockers, and says to derive the volume/momentum
# cutoff from "past 1 week history of this criteria". That history did not
# exist -- the last snapshot on disk was 05-Aug. These lists are fetched every
# cycle and then thrown away.
#
# So keep one snapshot a minute during market hours. One line per snapshot,
# ~100 rows, a few hundred KB a day.
#
# Wrapped so it can NEVER break the feed it observes. Instrumentation that can
# take down the thing it measures is worse than none (02-Sep, super_monitor).
_ARCH = {"t": 0.0}
ARCH_EVERY = 60.0


def _archive(names, per_list, log=lambda m: None):
    try:
        import time as _t, json as _j
        from datetime import datetime as _dt
        now = _t.time()
        if now - _ARCH["t"] < ARCH_EVERY:
            return
        hm = _dt.now().strftime("%H:%M:%S")
        if not ("09:00:00" <= hm <= "15:35:00"):
            return
        _ARCH["t"] = now
        d = Path(__file__).resolve().parent / "logs" / "movers_board"
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"sym": r.get("sym"), "sid": r.get("sid"), "ltp": r.get("ltp"),
                 "day_pct": r.get("day_pct"), "tvol": r.get("tvol"),
                 "day_tover": r.get("day_tover"), "tover_min": r.get("tover_min"),
                 "lists": r.get("lists")}
                for r in names]
        p = d / f"shockers_{_dt.now().strftime('%Y%m%d')}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(_j.dumps({"ts": hm, "per_list": per_list, "rows": rows},
                             separators=(",", ":")) + "\n")
    except Exception as e:
        try:
            log(f"shockers archive: {type(e).__name__} {str(e)[:80]}")
        except Exception:
            pass


def _api_sweep_lists(log=lambda m: None):
    """FALLBACK discovery via api.dhan.co (official API token, no web login):
    one market-wide NSE-EQ quote sweep, recomposed into the same 3 lists:
      Price Movers    = top 50 by |day %|        (gainers AND losers -> shorts too)
      By Volumes      = top 50 by day volume
      Intraday Movers = top 50 by live velocity (|%/min| from the previous sweep)
    Output shape identical to the scanx path, so the whole pipeline is unaffected."""
    import time as _t
    uni = q._eq_universe()
    if not uni:
        return [], "no_universe"
    quotes = q._quote_all(list(uni.keys()), log)
    if not quotes:
        return [], "no_quotes (scanx expired AND api sweep failed)"
    now_t = _t.time()
    rows = []
    for sid, qq in quotes.items():
        try:
            lp = float(qq.get("last_price") or 0)
            nc = float(qq.get("net_change") or 0)
            vol = float(qq.get("volume") or 0)
            ucl = float(qq.get("upper_circuit_limit") or 0)
        except (TypeError, ValueError):
            continue
        # NOTE: do NOT require vol > 0 -- before 09:15 the exchange reports zero
        # volume for every stock, which would empty the whole universe.
        if lp <= 0:
            continue
        prev = lp - nc
        pct = (nc / prev * 100) if prev else 0.0
        vel, volx, tover = q._liq_from_quote(sid, lp, vol, now_t)
        rows.append({"sym": uni.get(str(sid), str(sid)), "sid": str(sid),
                     "ltp": lp,
                     "day_pct": round(pct, 2), "tvol": vol,
                     "day_tover": int(lp * vol),
                     "tover_min": int(tover),
                     "vel_pct_min": round(vel, 3), "vol_rate_x": round(volx, 2),
                     "ucl": round(ucl, 2) if ucl else None, "lists": []})
    by_pct = sorted(rows, key=lambda x: -abs(x["day_pct"] or 0))[:COUNT]
    by_vol = sorted(rows, key=lambda x: -(x["tvol"] or 0))[:COUNT]
    by_vel = sorted(rows, key=lambda x: -abs(x["vel_pct_min"] or 0))[:COUNT]
    merged = {}
    for label, lst in (("Price Movers", by_pct), ("By Volumes", by_vol),
                       ("Intraday Movers", by_vel)):
        for r in lst:
            rec = merged.setdefault(r["sid"], r)
            if label not in rec["lists"]:
                rec["lists"].append(label)
    names = list(merged.values())
    names.sort(key=lambda x: -abs(x.get("day_pct") or 0))
    log(f"movers[API-fallback]: sweep={len(rows)} -> pct50/vol50/vel50 merged={len(names)}")
    return names, None
