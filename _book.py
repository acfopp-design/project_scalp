import json, signal_sim as G, entry_lab as E
TAPE=E.load_tape_warm("20260904")
bl=json.load(open("logs/bullish_0909_20260904.json",encoding="utf-8"))
AV={r["sym"]:"09:15:00" for r in bl["rows"]}
for line in open("logs/movers_board/board_20260904.jsonl",encoding="utf-8"):
    try: d=json.loads(line)
    except: continue
    hm=max(d.get("ts","")[11:19],"09:15:00")
    for rows in (d.get("panels") or {}).values():
        for r in rows or []:
            sy=r.get("sym")
            if sy and hm<AV.get(sy,"99:99:99"): AV[sy]=hm
AV={s:AV[s] for s in AV if s in TAPE}
DP={}
for line in open("logs/movers_board/board_20260904.jsonl",encoding="utf-8"):
    try: d=json.loads(line)
    except: continue
    hm=d.get("ts","")[11:19]
    if not hm: continue
    for rows in (d.get("panels") or {}).values():
        for r in rows or []:
            sy,p_=r.get("sym"),r.get("day_pct")
            if sy and p_ is not None: DP.setdefault(sy,[]).append((hm,float(p_)))
for k in DP: DP[k].sort()

def setup(gate=True):
    G.VOL_MODE=True; G.USE_BREAKOUT=True; G.BRK_NEW_DAY_HIGH=True
    G.STRENGTH_MODE="vol"; G.AVAILABLE_FROM=AV if gate else {}
    G.DAY_PCT=DP
    G.universe=lambda d: (TAPE,set(TAPE),set())
def day():
    G.SQUARE_OFF="15:15:00"; G.ENTRY_TO="14:30:00"
    return G.run("20260904",log=lambda *a:None)
