import pandas as pd, tune_dyn as TD
SLOT=250000
rows=[
("PROTEAN","0921",497,"0931",523,1),("PROTEAN","0940",529.5,"0946",540,1),
("PROTEAN","0943",543,"1003",554,1),("PROTEAN","1207",562,"1218",586,1),
("PROTEAN","1114",565,"1129",557,-1),
("INDOMIM","0916",1085,"0927",1134,1),("INDOMIM","0938",1125,"0944",1145,1),
("INDOMIM","1200",1140,"1201",1155,1),("INDOMIM","1335",1148,"1343",1160,1),
("RAYMOND","0919",980,"0930",1014,1),("RAYMOND","0932",1014,"0938",1025,1),
("RAYMOND","1511",1068,"1526",1110,1),
("FILATEX","0916",95,"0931",100,1),("FILATEX","0941",101,"0945",99.69,-1),
("FILATEX","1108",99.47,"1113",101.2,1),
("AGI","0915",752,"0922",788,1),("AGI","0933",799,"0945",785,-1),
("AGI","1013",795,"1018",805,1),("AGI","1144",795,"1150",803,1),
("AGI","1220",806,"1225",813,1),("AGI","1434",799,"1447",809,1),
("AGI","1452",810,"1505",820,1)]
out=[]
for s,ti,pi,to,po,side in rows:
    q=int(SLOT/pi); g=(po-pi)*q*side
    vin,vout=(q*pi,q*po) if side==1 else (q*po,q*pi)
    fee=TD.charges(vin,vout); net=g-fee
    out.append(dict(stock=s,ti=ti,to=to,side='L' if side==1 else 'S',
                    pin=pi,pout=po,gross=round(g),fee=round(fee),net=round(net),
                    pct_1L=round(net/1000,2)))
T=pd.DataFrame(out)
print(T.to_string(index=False))
print()
S=T.groupby('stock',sort=False).agg(trades=('net','size'),net=('net','sum'),pct=('pct_1L','sum'))
print(S.to_string())
print('\nTOTAL %+.2f%% over %d trades'%(T.pct_1L.sum(),len(T)))
