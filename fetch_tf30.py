"""
fetch_tf30.py -- pull Dhan's own 30-SECOND bars for a list of stocks.

This is the exact series Dhan's TradingView 30s chart is drawn from
(ticks.dhan.co/getDataS, INTERVAL "30S") -- the same numbers, read as data
instead of as pixels, so surge and dip times are exact to the bar rather than
estimated off a screenshot.

Writes logs/tf30_<SYM>.json for each. Read-only.
"""
import csv, json, sys
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Movers_chartfeed as cf
import Opus_engine as engine
IST = engine.IST

SYMS = [s.upper() for s in (sys.argv[1:] or
        ["LALITHAA", "SOTL", "KALYANIFRG", "YATRA", "GRAPHITE"])]

sid_of = {}
with (HERE / "security_id_list.csv").open(newline="", encoding="utf-8", errors="ignore") as f:
    for r in csv.DictReader(f):
        sym = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
        if (sym in SYMS and sym not in sid_of
                and (r.get("SEM_EXM_EXCH_ID") or "").upper() == "NSE"
                and (r.get("SEM_SEGMENT") or "").upper() == "E"):
            sid_of[sym] = str(r.get("SEM_SMST_SECURITY_ID")).strip()

# The instrument master is a downloaded snapshot and goes stale: LALITHAA is a
# recent listing, present in the board's own logs but absent from the CSV. The
# board log is the fresher source, so fall back to it rather than dropping the
# stock silently.
missing = [x for x in SYMS if x not in sid_of]
if missing:
    import json as _j
    lg = HERE / "logs" / "movers_board" / f"board_{datetime.now(IST):%Y%m%d}.jsonl"
    if lg.exists():
        for ln in lg.open(encoding="utf-8", errors="replace"):
            try: r = _j.loads(ln)
            except Exception: continue
            for _, cards in (r.get("panels") or {}).items():
                for c in cards:
                    sy = str(c.get("sym") or "").upper()
                    if sy in missing and sy not in sid_of and c.get("sid"):
                        sid_of[sy] = str(c["sid"])
            if not [x for x in SYMS if x not in sid_of]:
                break
    still = [x for x in SYMS if x not in sid_of]
    print("resolved from the board log:",
          {k: v for k, v in sid_of.items() if k in missing},
          "| still unknown:", still)
print("resolved:", sid_of)
out = HERE / "logs"
for sym in SYMS:
    sid = sid_of.get(sym)
    if not sid:
        print(f"  {sym:12s} NOT FOUND in the instrument master"); continue
    a, err = cf.get_seconds(sid, interval="30S", days=2)
    if err or not a:
        print(f"  {sym:12s} fetch failed: {err}"); continue
    today = datetime.now(IST).strftime("%Y-%m-%d")
    keep = [i for i in range(len(a["t"]))
            if datetime.fromtimestamp(a["t"][i], IST).strftime("%Y-%m-%d") == today]
    d = {k: [a[k][i] for i in keep] for k in ("o", "h", "l", "c", "v", "t")}
    d["sym"], d["sid"] = sym, sid
    (out / f"tf30_{sym}.json").write_text(json.dumps(d), encoding="utf-8")
    t0 = datetime.fromtimestamp(d["t"][0], IST).strftime("%H:%M:%S")
    t1 = datetime.fromtimestamp(d["t"][-1], IST).strftime("%H:%M:%S")
    print(f"  {sym:12s} sid={sid:7s} {len(keep):4d} bars  {t0} -> {t1}  "
          f"open={d['o'][0]} last={d['c'][-1]}")
print("\ndone -- files in logs/tf30_*.json")
