"""circuit_bands.py -- cache today's upper-circuit price for every symbol we may
trade, so the engine can get out 1% BEFORE the lock instead of discovering it
afterwards.

Sri, 05-Sep: "if its reaching upper circuit, you should exit 1% upper circuit."

Why a cache and not a live lookup: the band is fixed for the whole session and is
known before the open, so one pre-open call covers the day and costs nothing
during trading hours.

Writes logs/circuit_bands.json:
    {"<SYM>": {"upper": <price>, "lower": <price>, "prev_close": <price>}}
signal_sim.py reads it; when a symbol is absent the engine falls back to the
frozen-price detector, which needs no data at all but only fires AFTER the lock.

Run: python circuit_bands.py            (SUPERVISOR does this at 09:08)
"""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "logs" / "circuit_bands.json"
MASTER = HERE / "security_id_list.csv"


def _sids(syms):
    want, out = set(syms), {}
    import csv
    with MASTER.open(encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_EXM_EXCH_ID") != "NSE":
                continue
            if row.get("SEM_SERIES") not in ("EQ", "BE", "SM", "D1"):
                continue
            s = row.get("SEM_TRADING_SYMBOL")
            if s in want and s not in out:
                out[s] = int(row["SEM_SMST_SECURITY_ID"])
    return out


def _pick(q, *needles):
    """Dhan has changed these field names before, so match on the name rather
    than hard-coding one spelling."""
    for k, v in q.items():
        kl = k.lower().replace("_", "")
        if all(n in kl for n in needles):
            try:
                f = float(v)
                if f > 0:
                    return f
            except Exception:
                pass
    return None


def fetch(syms, log=print):
    import Opus_quotes_v3 as Q
    cid, tok, _ = Q._env()
    h = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}
    sid = _sids(syms)
    log(f"circuit_bands: {len(sid)} of {len(syms)} symbols resolved to security ids")
    rev = {v: k for k, v in sid.items()}
    # 07-Sep 09:10, the first time this ever ran live: one 559-symbol request
    # came straight back as _Retry429. Dhan rate-limits the quote endpoint, so
    # the batch is chunked and each chunk gets a retry after a back-off.
    out, ids, shown = {}, list(sid.values()), False
    CHUNK = 200
    for i in range(0, len(ids), CHUNK):
        body = {"NSE_EQ": [int(x) for x in ids[i:i + CHUNK]]}
        res = None
        for attempt in range(3):
            try:
                res = Q._quote_one(body, h)
                break
            except Exception as e:
                if attempt == 2:
                    log(f"circuit_bands: chunk {i//CHUNK+1} gave up -- "
                        f"{type(e).__name__} {e}")
                else:
                    time.sleep(3 * (attempt + 1))
        if res is None:
            continue
        data = ((res or {}).get("data") or {}).get("NSE_EQ") or {}
        for k, q in data.items():
            if not shown:
                log("circuit_bands: quote fields seen -> " + ", ".join(sorted(q)))
                shown = True
            sym = rev.get(int(k))
            if not sym:
                continue
            up = _pick(q, "upper", "circuit")
            lo = _pick(q, "lower", "circuit")
            pc = _pick(q, "prev", "close") or _pick(q, "close")
            if up:
                out[sym] = {"upper": up, "lower": lo, "prev_close": pc}
        time.sleep(1)
    return out


def main():
    syms = set()
    d = HERE / "logs" / "tape"
    days = sorted([p for p in d.glob("*") if p.is_dir()])
    if days:
        syms |= {f.stem for f in days[-1].glob("*.json")}
    try:
        wl = json.loads((HERE / "logs" / "mywatchlist.json").read_text(encoding="utf-8"))
        syms |= {r["sym"] for l in wl["lists"].values() for r in l}
    except Exception:
        pass
    if not syms:
        print("circuit_bands: nothing to look up"); return 1
    out = fetch(sorted(syms))
    if not out:
        print("circuit_bands: NO circuit fields returned -- engine will fall back "
              "to the frozen-price detector. Leaving the old cache in place.")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"circuit_bands: wrote {len(out)} bands to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
