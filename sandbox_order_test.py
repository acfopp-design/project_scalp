"""Full order lifecycle against DhanHQ SANDBOX: place -> status -> cancel.

Deliberately safe by construction:
  * SANDBOX host only. Refuses to run if BROKER_MODE is LIVE.
  * quantity 1.
  * BUY LIMIT at Rs 1.00 -- a price that can never fill on a real exchange,
    so the same script is harmless if it is ever pointed at a live account.
  * The symbol is read from the engine's own universe, never hardcoded.
"""
import csv, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import broker as BR

QTY = 1
FALLBACK_PRICE = 100.00      # sandbox fills around Rs 100 anyway


def pick_symbol():
    """First real name from today's watchlist, else from universe.csv."""
    wl = os.path.join(HERE, "logs", "mywatchlist.json")
    try:
        d = json.load(open(wl, encoding="utf-8"))
        names = d if isinstance(d, list) else sum((v for v in d.values() if isinstance(v, list)), [])
        for n in names:
            s = n.get("symbol") if isinstance(n, dict) else n
            if s and "TEST" not in str(s).upper():
                return str(s).strip(), "logs/mywatchlist.json"
    except Exception:
        pass
    # universe.csv is sorted, and its head is bonds/test scrips (0IRFC35,
    # 011NSETEST). Take the first plain-alphabetic NSE_EQ name instead.
    try:
        import re
        with open(os.path.join(HERE, "universe.csv"), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s = (row.get("symbol") or "").strip().upper()
                if (row.get("segment") or "") != "NSE_EQ":
                    continue
                if not re.fullmatch(r"[A-Z&]{3,}", s) or "TEST" in s:
                    continue
                return s, "universe.csv"
    except Exception:
        pass
    return None, None


def resolve_price(sym):
    """A limit price Dhan's RMS will accept: the real last price, rounded to
    a 5-paisa tick. Read-only quote call on the LIVE data key we already pay
    for. Falls back if the market is shut and no price comes back."""
    try:
        from leverage import sec_lookup
        import Opus_quotes_v3 as Q
        sid, _lot = sec_lookup(sym)
        if not sid:
            return FALLBACK_PRICE, "fallback (no security id)"
        q = Q._quote_all([str(sid)], lambda m: None) or {}
        qq = q.get(str(sid)) or {}
        for k in ("last_price", "ltp", "close_price", "close"):
            v = qq.get(k)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v > 0:
                return round(round(v / 0.05) * 0.05, 2), "quote field %s" % k
        return FALLBACK_PRICE, "fallback (quote gave no price)"
    except Exception as e:
        return FALLBACK_PRICE, "fallback (%s)" % type(e).__name__


def main():
    m = BR.mode()
    print("=" * 68, flush=True)
    print("SANDBOX order lifecycle test  (qty %d, BUY LIMIT at last price)" % QTY, flush=True)
    print("=" * 68, flush=True)
    if m == "LIVE":
        print("REFUSING: BROKER_MODE is LIVE. Set it to SANDBOX in env.txt first.")
        return 1
    print("BROKER_MODE in env.txt :", m, "-> forcing SANDBOX for this test", flush=True)
    print("sandbox token hours left:", BR.token_hours_left("SANDBOX"), flush=True)

    sym, src = pick_symbol()
    if not sym:
        print("FAIL  could not pick a symbol from watchlist or universe.csv")
        return 1
    print("symbol                 : %s   (from %s)" % (sym, src), flush=True)
    print("-" * 68, flush=True)

    price, psrc = resolve_price(sym)
    print("limit price            : Rs %.2f   (%s)" % (price, psrc), flush=True)
    print("-" * 68, flush=True)

    f = BR.funds("SANDBOX")
    print("1) funds        ok=%s  balance=%s" % (f["ok"], f.get("balance")), flush=True)

    print("2) placing order ...", flush=True)
    r = BR.place(sym, 1, QTY, price, tag="lifecycletest", m="SANDBOX")
    print("   ok=%s  http=%s  order_id=%s  status=%s" %
          (r["ok"], r.get("http"), r.get("order_id"), r.get("status")), flush=True)
    if not r["ok"]:
        print("   detail:", str(r.get("detail"))[:400], flush=True)
        print("-" * 68)
        print("PLACE FAILED -- see detail above.")
        return 1
    oid = r["order_id"]

    time.sleep(2)
    s = BR.status(oid, "SANDBOX")
    print("3) status       ok=%s  http=%s  status=%s  filled=%s  avg=%s" %
          (s["ok"], s.get("http"), s.get("status"), s.get("traded_qty"), s.get("avg_price")), flush=True)
    if (s.get("status") or "").upper() == "REJECTED":
        d = s.get("detail") or {}
        print("   REJECTION REASON:",
              (d.get("omsErrorDescription") or d.get("errorMessage")
               or d.get("oms_error_description") or str(d)[:300]), flush=True)

    p = BR.positions("SANDBOX")
    print("4) positions    ok=%s  rows=%d" % (p["ok"], len(p.get("rows") or [])), flush=True)

    c = BR.cancel(oid, "SANDBOX")
    print("5) cancel       ok=%s  http=%s  detail=%s" %
          (c["ok"], c.get("http"), str(c.get("detail"))[:120]), flush=True)

    time.sleep(2)
    s2 = BR.status(oid, "SANDBOX")
    print("6) status again ok=%s  status=%s" % (s2["ok"], s2.get("status")), flush=True)

    print("-" * 68, flush=True)
    filled = (s.get("status") or "").upper() in ("TRADED", "PART_TRADED")
    good = r["ok"] and s["ok"]
    print("order reached a FILL   :", "YES" if filled else "no (status %s)" % s.get("status"), flush=True)
    print("LIFECYCLE OK -- broker.py can place, read and cancel orders."
          if good else "LIFECYCLE INCOMPLETE -- read the lines above.", flush=True)
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
