"""Which 25-Sep exits left money behind? Read-only, no engine change."""
import json, sys
sys.path.insert(0, ".")
import live_shadow as LS

DAY = sys.argv[1] if len(sys.argv) > 1 else "20260925"
CUT = "15:05:00"

tr = json.load(open("logs/BT_%s.json" % DAY, encoding="utf-8"))
bars = LS.load_today(DAY) or LS.load_live(DAY)
print("bar sets loaded:", len(bars))

rows = []
for t in tr:
    sym, side, qty = t["sym"], t["side"], t["qty"]
    out, out_t, peak = t["out"], t["out_t"], t.get("peak")
    b = bars.get(sym) or []
    after = [x for x in b if out_t < x["hhmm"] <= CUT]
    best = None
    if after:
        best = max((x["h"] or x["c"]) for x in after) if side == 1 \
            else min((x["l"] or x["c"]) for x in after)
    # money surrendered from the in-trade peak
    give = (peak - out) * qty if side == 1 else (out - peak) * qty
    # money that kept running AFTER we were out
    left = None
    if best is not None:
        left = (best - out) * qty if side == 1 else (out - best) * qty
    rows.append({"sym": sym, "side": "L" if side == 1 else "S", "qty": qty,
                 "in_t": t["in_t"], "out_t": out_t, "in": t["in"], "out": out,
                 "peak": peak, "net": t["net"], "why": t["why"],
                 "give": round(give or 0), "left": None if left is None else round(left),
                 "best_after": best, "bars_after": len(after)})

print("\n### A. EXITED TOO EARLY -- price kept running our way after we left")
hdr = "%-13s %-2s %6s %8s %8s %9s %9s %9s %9s  %s"
print(hdr % ("stock", "d", "qty", "in_t", "out_t", "exit px", "best aft", "MISSED", "net got", "why"))
for r in sorted([x for x in rows if x["left"]], key=lambda x: -x["left"])[:10]:
    print(hdr % (r["sym"], r["side"], r["qty"], r["in_t"], r["out_t"],
                 "%.2f" % r["out"], "%.2f" % r["best_after"],
                 "{:,}".format(r["left"]), "{:,.0f}".format(r["net"]), r["why"]))

print("\n### B. EXITED TOO LATE -- gave back the most from the in-trade peak")
for r in sorted(rows, key=lambda x: -x["give"])[:10]:
    print(hdr % (r["sym"], r["side"], r["qty"], r["in_t"], r["out_t"],
                 "%.2f" % r["out"], "%.2f" % (r["peak"] or 0),
                 "{:,}".format(r["give"]), "{:,.0f}".format(r["net"]), r["why"]))

tot_left = sum(x["left"] or 0 for x in rows)
tot_give = sum(x["give"] or 0 for x in rows)
net = sum(x["net"] for x in rows)
print("\n%d trades | net Rs %s | surrendered from peak Rs %s | left on table after exit Rs %s"
      % (len(rows), "{:,.0f}".format(net), "{:,.0f}".format(tot_give), "{:,.0f}".format(tot_left)))
print("no post-exit bars for %d trades" % sum(1 for x in rows if x["left"] is None))
