import sys, importlib.util, pathlib
HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("signal_sim_old", HERE/"_sigold.py")
G = importlib.util.module_from_spec(spec); sys.modules["signal_sim_old"]=G
spec.loader.exec_module(G)
G.VOL_MODE = True
DAY="20260904"; SYMS=["IFCI","HEG","NIACL","LALITHAA","MARSONS"]
_orig=G.universe
def one(sym):
    def u(day):
        tape,syms,watch=_orig(day)
        return {sym:tape[sym]},{sym},watch
    return u
tot=0
for s in SYMS:
    G.universe=one(s)
    try: closed,net=G.run(DAY,log=lambda *a:None)
    except KeyError: print(s,"NO TAPE"); continue
    tot+=net; print(f"{s:10s} Rs {net:>10,.0f}  trades {len(closed)}")
print("TOTAL",f"{tot:,.0f}")
