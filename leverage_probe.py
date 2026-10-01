"""leverage_probe.py - ask Dhan what intraday leverage each stock actually has.

Runs on the Windows box (needs real network to api.dhan.co).
Writes logs/LEVERAGE_PROBE.json.

Step 1 verifies the endpoint on 4 known names (CLAYCRAFT must come back 1x,
ATHERENERG 5x - those are confirmed from the Dhan web UI on 26-Sep).
Step 2 fetches leverage for every symbol traded on the day given as argv[1]
(default 20260925).
"""
import csv, json, os, sys, time, urllib.request
import Opus_env_loader as EL

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "logs", "LEVERAGE_PROBE.json")
URL  = "https://api.dhan.co/v2/margincalculator"

_env = EL.load_env()
CID, TOK = _env["client_id"], _env["token"]


def sec_ids():
    """trading symbol -> (security id, series, lot) for NSE cash."""
    m = {}
    p = os.path.join(HERE, "security_id_list.csv")
    with open(p, encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                sym = (row.get("SEM_TRADING_SYMBOL") or "").strip()
                if sym and sym not in m:
                    try:
                        lot = max(1, int(float(row.get("SEM_LOT_UNITS") or 1)))
                    except (TypeError, ValueError):
                        lot = 1
                    m[sym] = (row.get("SEM_SMST_SECURITY_ID"),
                              (row.get("SEM_SERIES") or "").strip(), lot)
    return m


def ask(sid, price, qty=1):
    body = json.dumps({
        "dhanClientId": CID, "exchangeSegment": "NSE_EQ", "transactionType": "BUY",
        "quantity": int(qty), "productType": "INTRADAY",
        "securityId": str(sid), "price": float(price),
    }).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "access-token": TOK, "client-id": CID})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()), None
    except Exception as ex:
        raw = ""
        try:
            raw = ex.read().decode()[:300]
        except Exception:
            pass
        return None, f"{type(ex).__name__}: {ex} {raw}"


def leverage_of(resp, price, qty):
    """ONLY Dhan's own leverage field. Never derived.

    A missing leverage field does NOT mean 5x and does NOT mean "compute it
    from totalMargin" - deriving it produced 4.57x for HEGAM on 26-Sep when
    the Dhan UI plainly said 1.00X. Missing means UNKNOWN, and the caller
    must treat unknown as 1x (no leverage) so we never oversize.
    """
    if not isinstance(resp, dict):
        return None
    d = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    for k in ("leverage", "Leverage"):
        v = d.get(k)
        if v in (None, "", 0, "0"):
            continue
        try:
            return round(float(str(v).upper().replace("X", "").strip()), 2)
        except ValueError:
            continue
    return None


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "20260925"
    ids = sec_ids()
    out = {"day": day, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "endpoint": URL, "verify": {}, "leverage": {}, "errors": {}, "raw_sample": None}

    print("STEP 1  verifying endpoint on 4 known names")
    for sym, px in (("CLAYCRAFT", 171.20), ("ATHERENERG", 1497.00),
                    ("HEGAM", 244.35), ("WHIRLPOOL", 919.45)):
        sid, series, lot = ids.get(sym, (None, "", 1))
        if not sid:
            out["verify"][sym] = "no security id"
            print("  ", sym, "no security id"); continue
        resp, err = ask(sid, px, lot)
        if err:
            out["verify"][sym] = err
            print("  ", sym, "ERROR", err[:120])
        else:
            out.setdefault("raw_all", {})[sym] = resp
            if out["raw_sample"] is None:
                out["raw_sample"] = resp
            lv = leverage_of(resp, px, lot)
            out["verify"][sym] = lv
            print("  ", sym, "leverage", lv)
        time.sleep(0.4)

    ok = [v for v in out["verify"].values() if isinstance(v, (int, float))]
    if not ok:
        out["verdict"] = "ENDPOINT UNUSABLE - see verify/errors"
        print("\nendpoint did not return leverage. stopping.")
    else:
        cc = out["verify"].get("CLAYCRAFT")
        ae = out["verify"].get("ATHERENERG")
        hg = out["verify"].get("HEGAM")
        want = {"CLAYCRAFT": 1.0, "ATHERENERG": 5.0, "HEGAM": 1.0}
        bad = {k: out["verify"].get(k) for k, v in want.items() if out["verify"].get(k) != v}
        out["verdict"] = "MATCHES UI" if not bad else f"MISMATCH vs UI {want} -> got {bad}"
        print("\nverdict:", out["verdict"])

        tf = os.path.join(HERE, "logs", f"paper_live_{day}.json")
        syms = {}
        if os.path.exists(tf):
            for t in json.load(open(tf, encoding="utf-8")).get("trades", []):
                if t.get("sym") and t.get("in"):
                    syms.setdefault(t["sym"], float(t["in"]))
        print(f"\nSTEP 2  {len(syms)} symbols traded on {day}")
        for n, (sym, px) in enumerate(sorted(syms.items()), 1):
            sid, series, lot = ids.get(sym, (None, "", 1))
            if not sid:
                out["errors"][sym] = "no security id"; continue
            resp, err = ask(sid, px, lot)
            if err:
                out["errors"][sym] = err
            else:
                out["leverage"][sym] = {"lev": leverage_of(resp, px, lot),
                                        "series": series, "lot": lot, "px": px,
                                        "totalMargin": (resp.get("data") if isinstance(resp.get("data"), dict) else resp).get("totalMargin")}
            if n % 10 == 0:
                print("   ...", n, "of", len(syms))
            time.sleep(0.4)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\nwrote", OUT)
    print("got leverage for", len(out["leverage"]), "symbols,", len(out["errors"]), "errors")


if __name__ == "__main__":
    main()
