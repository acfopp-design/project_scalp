"""
Opus_preopen.py -- NSE PRE-OPEN (09:00-09:15 IST) indicative scanner.

During the pre-open call auction there are no 1-min candles yet, so the normal
scanner is blind. This module quotes the cash universe via Dhan and surfaces
stocks with a strong INDICATIVE move (price vs prev close) AND significant BID
demand (buy quantity dominating sell quantity), so they show before 09:15.

Fully ISOLATED: runs in its own thread; every error is swallowed so it can never
slow or break the live candle scanner.

SELF-VERIFYING FIELD MAPPING:
  * The extractors below try ALL the likely Dhan field names/shapes, so the exact
    naming doesn't need to be known in advance.
  * On the first pre-open cycle it writes logs/opus_preopen_sample.json with a few
    raw quotes, so the mapping can be confirmed/finalised just by reading that file
    (no manual copying needed).
  * If the quote endpoint carries NO bid/ask quantities, it falls back to ranking
    by the indicative move alone, so the pre-open column is never empty.
"""
import json
from datetime import datetime, time as dtime
from pathlib import Path

import Opus_quotes_v3 as q
import Opus_engine as engine

IST = engine.IST
HERE = Path(__file__).resolve().parent
SAMPLE_FILE = HERE / "logs" / "opus_preopen_sample.json"
PREOPEN_START = dtime(9, 0)
PREOPEN_END = dtime(9, 15)

# defaults (override in Opus_config.json)
PREOPEN_MIN_PCT = 1.0             # indicative move vs prev close to count as "good"
PREOPEN_MIN_BIDVAL = 5_000_000   # min bid value (buy_qty * price), Rs 50 lakh
PREOPEN_MIN_PRICE = 40.0
_sampled = {"done": False}

# candidate field names Dhan may use (checked in order, nested dicts handled)
_F_PRICE = ("last_price", "ltp", "last_traded_price", "lastTradedPrice", "LTP", "close")
_F_CHANGE = ("net_change", "change", "net_chng", "netChange", "day_change")
_F_PREV = ("prev_close", "previous_close", "close", "prevClose", "close_price")
_F_BUYQ = ("buy_quantity", "total_buy_quantity", "totalBuyQuantity", "buy_qty",
           "totBuyQty", "total_buy_qty", "bid_quantity")
_F_SELLQ = ("sell_quantity", "total_sell_quantity", "totalSellQuantity", "sell_qty",
            "totSellQty", "total_sell_qty", "ask_quantity")


def in_window(now=None):
    now = (now or datetime.now(IST)).time()
    return PREOPEN_START <= now <= PREOPEN_END


def _num(d, keys):
    """First present numeric value among keys (searches the dict and one nested level)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    for v in d.values():                 # one level of nesting (e.g. Dhan "ohlc", "depth")
        if isinstance(v, dict):
            for k in keys:
                if k in v and v[k] is not None:
                    try:
                        return float(v[k])
                    except (TypeError, ValueError):
                        pass
    return None


def scan_both(cfg=None, log=lambda m: None, limit=80):
    """PRE-OPEN gap scanner -- BOTH directions, with an order-pumping trend.

    Returns rows for stocks whose indicative price gaps meaningfully AND whose
    order book is lopsided:
        gap UP   + buy quantity dominating   -> likely to open strong
        gap DOWN + sell quantity dominating  -> likely to open weak

    Each row also carries how the imbalance has MOVED since 09:00 (`trend`), which
    is the real "orders are being pumped in" signal -- a book that is 3x buy-heavy
    and still growing is very different from one that has been static since 09:00.

    NOTE on the NSE pre-open session:
        09:00-09:08  order entry / modification   <- the book we read
        09:08-09:12  matching + price discovery   (no new orders)
        09:12-09:15  buffer
    So the ~09:07:40 snapshot is the last complete picture before entry closes.
    """
    cfg = cfg or {}
    min_price = cfg.get("PREOPEN_MIN_PRICE", PREOPEN_MIN_PRICE)
    up_pct = cfg.get("PREOPEN_MIN_PCT", PREOPEN_MIN_PCT)          # 1.0
    strong_pct = cfg.get("PREOPEN_STRONG_PCT", 2.0)
    min_val = cfg.get("PREOPEN_MIN_BIDVAL", PREOPEN_MIN_BIDVAL)   # Rs 50 L
    strong_val = cfg.get("PREOPEN_STRONG_VAL", 10_000_000)        # Rs 1 Cr
    strong_ratio = cfg.get("PREOPEN_STRONG_RATIO", 2.0)
    try:
        uni = q._eq_universe()
    except Exception:
        uni = {}
    if not uni:
        return []
    quotes = q._quote_all(list(uni.keys()), log)
    if not quotes:
        return []
    now_s = datetime.now(IST).strftime("%H:%M:%S")
    any_bids = False
    for qq in quotes.values():
        if _num(qq, _F_BUYQ) or _num(qq, _F_SELLQ):
            any_bids = True
            break

    rows = []
    for sid, qq in quotes.items():
        lp = _num(qq, _F_PRICE)
        if lp is None or lp < min_price:
            continue
        nc = _num(qq, _F_CHANGE)
        prev = _num(qq, _F_PREV)
        if nc is None and prev:
            nc = lp - prev
        if prev is None and nc is not None:
            prev = lp - nc
        pct = (nc / prev * 100) if (nc is not None and prev) else 0.0
        bq = _num(qq, _F_BUYQ) or 0
        sq = _num(qq, _F_SELLQ) or 0
        bid_val = bq * lp
        ask_val = sq * lp
        side = verdict = None
        if any_bids:
            if pct >= up_pct and bq > sq and bid_val >= min_val:
                side = "UP"
                ratio = (bq / sq) if sq else 99.0
                verdict = ("STRONG GAP-UP" if (pct >= strong_pct and ratio >= strong_ratio
                                               and bid_val >= strong_val) else "GAP-UP")
            elif pct <= -up_pct and sq > bq and ask_val >= min_val:
                side = "DOWN"
                ratio = (sq / bq) if bq else 99.0
                verdict = ("STRONG GAP-DOWN" if (pct <= -strong_pct and ratio >= strong_ratio
                                                 and ask_val >= strong_val) else "GAP-DOWN")
        else:                                   # no book data -> gap size only
            if pct >= up_pct:
                side, verdict = "UP", "GAP-UP"
            elif pct <= -up_pct:
                side, verdict = "DOWN", "GAP-DOWN"
        if not side:
            continue
        imb = (bq / sq) if sq else None          # >1 buy-heavy, <1 sell-heavy
        sym = uni.get(str(sid), str(sid))
        trend, trend_x = _pump_trend(sym, imb, bq, sq, now_s, side)
        rows.append({
            "sym": sym, "sid": str(sid),
            "price": round(lp, 2), "pct": round(pct, 2),
            "buy_qty": int(bq), "sell_qty": int(sq),
            "bid_val": round(bid_val), "ask_val": round(ask_val),
            "imb": round(imb, 2) if imb is not None else None,
            "side": side, "verdict": verdict,
            "trend": trend, "trend_x": trend_x,
            "bids": any_bids, "ts": now_s,
        })
    # strongest conviction first: STRONG verdicts, then |gap|, then money behind it
    rows.sort(key=lambda r: (0 if r["verdict"].startswith("STRONG") else 1,
                             -abs(r["pct"]),
                             -max(r["bid_val"], r["ask_val"])))
    return rows[:limit]


# ---- order-pumping tracker -------------------------------------------------
# Remembers each symbol's FIRST imbalance of the session and compares it to the
# current one, so we can say whether demand is being pumped in or is static.
_pump = {}          # sym -> {"first_imb", "first_bq", "first_sq", "t0"}


def reset_pump():
    _pump.clear()


def _pump_trend(sym, imb, bq, sq, now_s, side="UP"):
    """How has the DOMINANT side's order quantity moved since 09:00?

    Measuring the dominant side directly (buy qty for a gap-up, sell qty for a
    gap-down) is the honest read of "orders are being pumped in". Using the
    imbalance ratio instead mislabels things: on a sell-heavy book the ratio
    FALLS as selling intensifies, which reads backwards.
    """
    try:
        e = _pump.get(sym)
        if e is None:
            _pump[sym] = {"imb": imb, "bq": bq, "sq": sq, "t0": now_s}
            return "new", None
        base = e["sq"] if side == "DOWN" else e["bq"]
        cur = sq if side == "DOWN" else bq
        if not base:
            return "—", None
        x = round(cur / base, 2)
        if x >= 1.25:
            return ("selling" if side == "DOWN" else "rising"), x
        if x <= 0.8:
            return ("easing" if side == "DOWN" else "fading"), x
        return "flat", x
    except Exception:
        return "—", None


def scan(cfg=None, log=lambda m: None, limit=80):
    """Return good pre-open movers, sorted by indicative % then bid value."""
    cfg = cfg or {}
    min_pct = cfg.get("PREOPEN_MIN_PCT", PREOPEN_MIN_PCT)
    min_bidval = cfg.get("PREOPEN_MIN_BIDVAL", PREOPEN_MIN_BIDVAL)
    min_price = cfg.get("PREOPEN_MIN_PRICE", PREOPEN_MIN_PRICE)
    try:
        uni = q._eq_universe()                       # {sid: symbol}
    except Exception:
        uni = {}
    if not uni:
        return []
    quotes = q._quote_all(list(uni.keys()), log)
    if not quotes:
        return []

    # one-time raw sample dump so the field mapping can be confirmed by reading the file
    if not _sampled["done"]:
        _sampled["done"] = True
        try:
            sample = {sid: quotes[sid] for sid in list(quotes.keys())[:8]}
            SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SAMPLE_FILE.write_text(json.dumps(
                {"ts": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                 "note": "first 8 raw Dhan pre-open quotes; used to confirm field mapping",
                 "sample_raw": sample}, indent=2, default=str), encoding="utf-8")
            log(f"preopen: wrote raw sample -> {SAMPLE_FILE.name}")
        except Exception:
            pass

    # detect whether bid/ask quantities are available anywhere in this batch
    any_bids = False
    for qq in quotes.values():
        if _num(qq, _F_BUYQ) or _num(qq, _F_SELLQ):
            any_bids = True
            break

    rows = []
    for sid, qq in quotes.items():
        lp = _num(qq, _F_PRICE)
        if lp is None or lp < min_price:
            continue
        nc = _num(qq, _F_CHANGE)
        prev = _num(qq, _F_PREV)
        if nc is None and prev:
            nc = lp - prev
        if prev is None and nc is not None:
            prev = lp - nc
        pct = (nc / prev * 100) if (nc is not None and prev) else 0.0
        bq = _num(qq, _F_BUYQ) or 0
        sq = _num(qq, _F_SELLQ) or 0
        bid_val = bq * lp
        if any_bids:                                  # true bid-demand filter
            good = bool(pct >= min_pct and bq > sq and bid_val >= min_bidval)
        else:                                         # bids unavailable -> indicative move only
            good = bool(pct >= min_pct)
        if not good:
            continue
        rows.append({
            "sym": uni.get(str(sid), str(sid)), "sid": str(sid),
            "price": round(lp, 2), "pct": round(pct, 2),
            "buy_qty": int(bq), "sell_qty": int(sq),
            "bid_val": round(bid_val), "imb": round(bq / sq, 2) if sq else None,
            "bids": any_bids,
        })
    rows.sort(key=lambda r: (-r["pct"], -r["bid_val"]))
    return rows[:limit]
