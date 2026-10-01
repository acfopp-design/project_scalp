import sys, importlib.util, pathlib
HERE = pathlib.Path(__file__).parent
def load(which):
    if which=="old":
        spec=importlib.util.spec_from_file_location("sig_old",HERE/"_sigold.py")
        m=importlib.util.module_from_spec(spec); sys.modules["sig_old"]=m; spec.loader.exec_module(m)
    else:
        import signal_sim as m
    return m
def go(which, full=True):
    G=load(which); G.VOL_MODE=True
    if full:
        G.MAX_POS_PCT=100.0; G.RISK_PCT=100.0; G.MAX_RISK_PCT=100.0; G.MIN_PRICE=20.0
    _o=G.universe
    def one(sym):
        def u(day):
            t,s,w=_o(day); return {sym:t[sym]},{sym},w
        return u
    out={}
    for s in ["IFCI","HEG","NIACL","LALITHAA","MARSONS"]:
        G.universe=one(s)
        try: closed,net=G.run("20260904",log=lambda *a:None)
        except KeyError: out[s]=(None,0); continue
        out[s]=(net,len(closed))
    return out
if __name__=="__main__":
    r=go(sys.argv[1], sys.argv[2]!="risk" if len(sys.argv)>2 else True)
    for k,(n,c) in r.items(): print(f"{k:10s} {n if n is None else format(n,',.0f'):>12}  trades {c}")
    print("TOTAL", f"{sum(v[0] or 0 for v in r.values()):,.0f}")
