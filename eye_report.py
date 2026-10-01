"""P&L read of BOTH paper books.

  LIVE PAPER TRADING tab  -> paper_live.py   -> logs/paper_live_<day>.json
  the Board's own engine  -> live_paper.py   -> logs/movers_board/livepaper_<day>.json

Both now run the human-eye logic; they differ in universe and entry plumbing,
so keeping both visible is a free A/B. Reads the same files the tabs render.
"""
import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
HERE = Path(__file__).parent
IST = timezone(timedelta(hours=5, minutes=30))
day = datetime.now(IST).strftime("%Y%m%d")
now = datetime.now(IST).strftime("%H:%M:%S")
CAP = 100000.0
print(f"=== {now} IST ===")

# ---- 1. the Live Paper Trading tab ---------------------------------------
f = HERE / "logs" / f"paper_live_{day}.json"
print("\nLIVE PAPER TRADING tab  (paper_live.py)")
if not f.exists():
    print("  no snapshot yet")
else:
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = d.get("rows") or d.get("trades") or []
    cl = [r for r in rows if (r.get("status") or "").lower() not in ("in-progress", "open", "running")]
    op = [r for r in rows if r not in cl]
    real = sum(float(r.get("net") or 0) for r in cl)
    unre = sum(float(r.get("net") or 0) for r in op)
    wins = sum(1 for r in cl if float(r.get("net") or 0) > 0)
    s = d.get("summary") or {}
    print(f"  {s.get('state') or d.get('note') or ''}".rstrip())
    print(f"  CLOSED {len(cl)} trades, {wins} wins"
          + (f" ({wins/len(cl)*100:.0f}%)" if cl else "")
          + f"   realised Rs {real:+,.0f}")
    print(f"  OPEN   {len(op)} positions            unrealised Rs {unre:+,.0f}")
    print(f"  TOTAL  Rs {real+unre:+,.0f}  =  {(real+unre)/CAP*100:+.2f}% on Rs {CAP:,.0f}")
    for r in (cl + op)[-8:]:
        print(f"    {str(r.get('sym','?')):<12} {str(r.get('in_t','')):<9}"
              f" -> {str(r.get('out_t','—')):<9} {str(r.get('why','open')):<12}"
              f" Rs {float(r.get('net') or 0):+8,.0f}")

# ---- 2. the Board's own engine -------------------------------------------
sf = HERE / "logs" / "movers_board" / f"livepaper_{day}.json"
print("\nBoard engine  (live_paper.py)")
if not sf.exists():
    print("  no state file")
else:
    d = json.loads(sf.read_text(encoding="utf-8"))
    op, cl = d.get("open") or {}, d.get("closed") or []
    real = sum(t.get("net", 0) for t in cl)
    unre = 0.0
    try:
        import paper_engine as pe
        for p in op.values():
            bv, sv = p["qty"] * p["in"], p["qty"] * p.get("last", p["in"])
            unre += sv - bv - pe.charges(bv, sv)["total"]
    except Exception:
        pass
    wins = sum(1 for t in cl if t.get("net", 0) > 0)
    print(f"  active={d.get('active')} slots={d.get('slots')}")
    print(f"  CLOSED {len(cl)} trades, {wins} wins   realised Rs {real:+,.0f}")
    print(f"  OPEN   {len(op)} positions            unrealised Rs {unre:+,.0f}")
    print(f"  TOTAL  Rs {real+unre:+,.0f}  =  {(real+unre)/CAP*100:+.2f}%")

es = HERE / "logs" / f"EYE_SIGNALS_{day}.json"
if es.exists():
    e = json.loads(es.read_text(encoding="utf-8"))
    print(f"\nsignal thread: clock {e.get('clock')} cycles {e.get('cycles')} "
          f"tape {e.get('tape')} names, {len(e.get('open') or [])} wanted open")
