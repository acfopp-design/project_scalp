"""leverage_audit.py - work out a RULE for per-stock intraday leverage.

Why: /v2/margincalculator returns leverage "4.57X" for HEGAM while the Dhan
Margin tab plainly shows 1.00X (verified in the UI on 26-Sep). Same for
CLAYCRAFT (API 1.87X, UI 1.00X) and KANOHAR (API 4.00X, UI 1.00X).
ATHERENERG and WHIRLPOOL returned exactly "5.00X" and the UI agreed.

Hypothesis A: api_leverage == exactly 5.00  ->  真 5x ; anything else -> 1x
Hypothesis B: the 1x names are the ones Dhan flags ASM/GSM, or that are not
              SERIES == EQ.

This downloads Dhan's detailed scrip master (which carries ASM_GSM_FLAG,
ASM_GSM_CATEGORY, MTF_LEVERAGE, SERIES), calls the margin calculator for the
day's universe, joins the two, and prints whether either hypothesis is clean.

Ground truth we have checked by eye in the Dhan UI - the audit scores itself
against these.
"""
import csv, io, json, os, sys, time, urllib.request
import Opus_env_loader as EL

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "logs", "LEVERAGE_AUDIT.json")
DSM  = os.path.join(HERE, "api-scrip-master-detailed.csv")
MURL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
CURL = "https://api.dhan.co/v2/margincalculator"

UI_TRUTH = {"ATHERENERG": 5.0, "WHIRLPOOL": 5.0,
            "HEGAM": 1.0, "CLAYCRAFT": 1.0, "KANOHAR": 1.0}

_env = EL.load_env()
CID, TOK = _env["client_id"], _env["token"]


def fetch_master():
    if os.path.exists(DSM) and (time.time() - os.path.getmtime(DSM)) < 20 * 3600:
        print("using cached", os.path.basename(DSM))
    else:
        print("downloading detailed scrip master ...")
        req = urllib.request.Request(MURL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as r, open(DSM, "wb") as f:
            f.write(r.read())
        print("  saved", os.path.getsize(DSM), "bytes")
    rows = {}
    with open(DSM, encoding="utf-8", errors="ignore") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        for row in rd:
            seg = (row.get("EXCH_ID") or row.get("SEM_EXM_EXCH_ID") or "").strip()
            sym = (row.get("UNDERLYING_SYMBOL") or row.get("SYMBOL_NAME")
                   or row.get("DISPLAY_NAME") or row.get("SEM_TRADING_SYMBOL") or "").strip()
            sid = (row.get("SECURITY_ID") or row.get("SEM_SMST_SECURITY_ID") or "").strip()
            if seg == "NSE" and sid and sid not in rows:
                rows[sid] = row
    return rows, cols


def sec_ids():
    m = {}
    with open(os.path.join(HERE, "security_id_list.csv"), encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                s = (row.get("SEM_TRADING_SYMBOL") or "").strip()
                if s and s not in m:
                    try:
                        lot = max(1, int(float(row.get("SEM_LOT_UNITS") or 1)))
                    except (TypeError, ValueError):
                        lot = 1
                    m[s] = (row.get("SEM_SMST_SECURITY_ID"),
                            (row.get("SEM_SERIES") or "").strip(), lot)
    return m


def api_leverage(sid, price, qty):
    body = json.dumps({"dhanClientId": CID, "exchangeSegment": "NSE_EQ",
                       "transactionType": "BUY", "quantity": int(qty),
                       "productType": "INTRADAY", "securityId": str(sid),
                       "price": float(price)}).encode()
    req = urllib.request.Request(CURL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "access-token": TOK, "client-id": CID})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        d = d.get("data") if isinstance(d.get("data"), dict) else d
        v = d.get("leverage")
        if v in (None, "", 0, "0"):
            return None, d.get("totalMargin"), None
        return round(float(str(v).upper().replace("X", "").strip()), 2), d.get("totalMargin"), None
    except Exception as ex:
        raw = ""
        try:
            raw = ex.read().decode()[:200]
        except Exception:
            pass
        return None, None, f"{type(ex).__name__}: {ex} {raw}"


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "20260925"
    master, cols = fetch_master()
    print("master rows (NSE):", len(master))
    print("master columns:", ", ".join(cols[:40]))
    ids = sec_ids()

    syms = {}
    tf = os.path.join(HERE, "logs", f"paper_live_{day}.json")
    if os.path.exists(tf):
        for t in json.load(open(tf, encoding="utf-8")).get("trades", []):
            if t.get("sym") and t.get("in"):
                syms.setdefault(t["sym"], float(t["in"]))
    for s in UI_TRUTH:                       # always include the eye-checked names
        if s not in syms:
            sid = ids.get(s, (None, "", 1))[0]
            syms[s] = {"ATHERENERG": 1497.0, "CLAYCRAFT": 171.20,
                       "HEGAM": 244.35, "WHIRLPOOL": 918.90, "KANOHAR": 862.55}.get(s, 100.0)

    out = {"day": day, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "master_cols": cols, "rows": {}, "errors": {}}
    print(f"\nauditing {len(syms)} symbols\n")
    for n, (sym, px) in enumerate(sorted(syms.items()), 1):
        sid, series, lot = ids.get(sym, (None, "", 1))
        if not sid:
            out["errors"][sym] = "no security id"; continue
        lev, tm, err = api_leverage(sid, px, lot)
        if err:
            out["errors"][sym] = err
        m = master.get(str(sid), {})
        out["rows"][sym] = {
            "sid": sid, "px": px, "lot": lot, "series": series,
            "api_lev": lev, "totalMargin": tm,
            "asm_gsm": (m.get("ASM_GSM_FLAG") or "").strip(),
            "asm_cat": (m.get("ASM_GSM_CATEGORY") or "").strip(),
            "mtf_lev": (m.get("MTF_LEVERAGE") or "").strip(),
            "m_series": (m.get("SERIES") or "").strip(),
        }
        if n % 10 == 0:
            print("  ...", n, "of", len(syms))
        time.sleep(0.4)

    # ---- score the two hypotheses against what we saw with our own eyes ----
    def hyp_a(r):  return 5.0 if r["api_lev"] == 5.0 else 1.0
    def hyp_b(r):  return 1.0 if (r["asm_gsm"] == "Y" or (r["m_series"] or r["series"]) != "EQ") else 5.0

    score = {}
    for name, fn in (("A_exactly_5.00X", hyp_a), ("B_asm_gsm_or_series", hyp_b)):
        ok = bad = 0; misses = {}
        for s, want in UI_TRUTH.items():
            r = out["rows"].get(s)
            if not r:
                continue
            got = fn(r)
            if got == want:
                ok += 1
            else:
                bad += 1; misses[s] = {"want": want, "got": got, "row": r}
        score[name] = {"ok": ok, "wrong": bad, "misses": misses}
    out["hypotheses"] = score
    out["disagree"] = {s: r for s, r in out["rows"].items() if hyp_a(r) != hyp_b(r)}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)

    print("\n--- scored against the 5 stocks we checked by eye in the UI ---")
    for k, v in score.items():
        print(f"  {k:24} correct {v['ok']}/5   wrong {v['wrong']}")
    print(f"\nthe two rules disagree on {len(out['disagree'])} of {len(out['rows'])} symbols")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
