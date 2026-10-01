import sys, signal_sim as G
G.VOL_MODE=True; G.MAX_POS_PCT=100.0; G.RISK_PCT=100.0; G.MAX_RISK_PCT=100.0; G.MIN_PRICE=20.0
_o=G.universe
SYMS=["IFCI","HEG","NIACL","LALITHAA","MARSONS"]
for mfo in [1.0, 0.5, 0.0, -1.0, -99.0]:
    G.MIN_FROM_OPEN=mfo; row={}
    for s in SYMS:
        G.universe=(lambda sym: (lambda d: ({sym:_o(d)[0][sym]},{sym},set())))(s)
        closed,net=G.run("20260904",log=lambda *a:None); row[s]=net
    print(f"from-open>={mfo:>6}: "+"  ".join(f"{s} {row[s]:>8,.0f}" for s in SYMS)+f"   TOTAL {sum(row.values()):,.0f}")
