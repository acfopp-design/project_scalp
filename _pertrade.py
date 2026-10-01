import sys, signal_sim as G
DAY = "20260904"
SYMS = ["IFCI","HEG","NIACL","LALITHAA","MARSONS"]
G.VOL_MODE = True
_orig = G.universe
def one(sym):
    def u(day):
        tape, syms, watch = _orig(day)
        return {sym: tape[sym]}, {sym}, watch
    return u
res = {}
for s in SYMS:
    G.universe = one(s)
    try:
        closed, net = G.run(DAY, log=lambda *a: None)
    except KeyError:
        print(s, "NO TAPE"); continue
    res[s] = (net, len(closed))
    print(f"{s:10s} Rs {net:>10,.0f}  trades {len(closed)}")
print("TOTAL", f"{sum(v[0] for v in res.values()):,.0f}")
