import sys,itertools,pickle,os,numpy as np
import tune_v6 as V6
from eye_targets import TARGETS
FILES={'AGI':'AGIGREENPAC_20260921.csv','PROTEAN':'PROTEAN_20260921.csv',
       'INDOMIM':'INDOMIM_20260921.csv','RAYMOND':'RAYMOND_20260921.csv',
       'FILATEX':'FILATEX_20260921.csv'}
def main():
    syms=sys.argv[1].split(','); shard=int(sys.argv[2]); nsh=int(sys.argv[3])
    D={s:V6.prep('backtest_data/intraday/'+FILES[s]) for s in syms}
    keys=list(V6.GRID); combos=list(itertools.product(*[V6.GRID[k] for k in keys]))
    out=[]
    for n,combo in enumerate(combos):
        if n%nsh!=shard: continue
        p=dict(zip(keys,combo)); row={}; worst=0
        for s in syms:
            net=sum(x['net'] for x in V6.sim(D[s],p))/1000
            row[s]=round(net,2); worst=max(worst,abs(net-TARGETS[s]))
        tot=sum(row.values())
        out.append((round(worst,2),round(abs(tot-sum(TARGETS[s] for s in syms)),2),row,p))
    out.sort(key=lambda r:r[0])
    pickle.dump(out[:200],open(f'_shard{shard}.pkl','wb'))
    print('shard',shard,'done',len(out),'best worst',out[0][0])
main()
