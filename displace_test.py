"""displace_test.py -- is the eviction rule earning its keep, or reading the future?

READ-ONLY. Prints a table, writes nothing.

30-Sep: 53 of 60 exits were evictions and they produced +100,571 of the day's
+122,786. The rule that picks them reads bar indices that lie in the FUTURE of
the replay moment:

    rem = pos["eye_out"] - i_now            # "bars this leg has left"
    if v >= rem * DISPLACE and (ceout - ci) >= MIN_LEG_DISPLACE:

`eye_out` and `ceout` are where those legs END. At replay-time t the engine is
choosing what to evict by comparing how much future each candidate has left.
That is the same defect as the MIN_LEG look-ahead fixed on 28-Sep -- the entry
gate was repaired and the eviction gate was left alone.

This runs the same tape with eviction ON and OFF.
"""
import sys
sys.path.insert(0, ".")
import live_shadow as LS, paper_live as PL, funnel as FN, eye_strategy as ES

day = sys.argv[1] if len(sys.argv) > 1 else "20260930"
start = sys.argv[2] if len(sys.argv) > 2 else "10:03:17"
upto = "15:30:00"

warm = LS.load_warm(LS._prev_session_dir(day))
pairs, _ = LS.build(day, warm)
tape, d0 = {}, {}
for s, (bars, n) in pairs.items():
    bb = [x for x in bars[:n] if x.get("c")]
    tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= upto]
    if len(tb) < 3:
        continue
    tape[s] = bb + tb
    d0[s] = len(bb)
avail, _, _ = FN.build(day, tape)
pins = set(PL.nodip_watchlist(day))
avail = {s: t for s, t in avail.items() if s in pins}
fun = {s: tape[s] for s in avail if s in tape}
fd0 = {s: d0[s] for s in fun}

print("  day %s   window %s - %s   %d names" % (day, start, upto, len(fun)))
print()
print("  %-22s %7s %6s %9s %12s %12s" % ("SETTING", "TRADES", "WINS", "WIN%", "NET", "EVICTIONS"))
print("  " + "-" * 74)
keep, keepm = PL.DISPLACE, PL.DISPLACE_MODE
for label, val, mode in (
        ("LEGACY 1.5 (reads future)", 1.5, "legacy"),
        ("CAUSAL 1.5 (honest)", 1.5, "causal"),
        ("CAUSAL 1.2", 1.2, "causal"),
        ("OFF (no eviction)", 0, "legacy")):
    PL.DISPLACE, PL.DISPLACE_MODE = val, mode
    ES._cache.clear()
    closed, live = PL.run_book(fun, fd0, avail, start, upto)
    net = sum(c.get("net") or 0 for c in closed)
    wins = sum(1 for c in closed if (c.get("net") or 0) > 0)
    ev = sum(1 for c in closed if (c.get("why") or "").startswith("displaced"))
    print("  %-22s %7d %6d %8.0f%% %12s %12d"
          % (label, len(closed), wins, 100 * wins / max(1, len(closed)),
             format(net, "+,.0f"), ev))
PL.DISPLACE, PL.DISPLACE_MODE = keep, keepm
