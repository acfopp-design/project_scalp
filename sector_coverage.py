"""How much of the real board does the NSE sector map actually cover?"""
import json, sys
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Movers_sectors as S
n = S.refresh(print, force=True)
print("\nmapped:", n, S.summary())
if not n:
    raise SystemExit("could not download the NSE classification")
board = set()
lg = HERE / "logs" / "movers_board" / f"board_{datetime.now():%Y%m%d}.jsonl"
for ln in lg.open(encoding="utf-8", errors="replace"):
    try: r = json.loads(ln)
    except Exception: continue
    for _, cards in (r.get("panels") or {}).items():
        for c in cards:
            if c.get("sym"): board.add(str(c["sym"]).upper())
k, unk = S.coverage(board)
print(f"\nboard movers today : {len(board)}")
print(f"  with a sector    : {k}  ({k*100//max(1,len(board))}%)")
print(f"  UNKNOWN          : {len(unk)}")
print(f"  examples unknown : {sorted(unk)[:12]}")
import collections
c = collections.Counter(S.sector_of(s) for s in board if S.sector_of(s))
print("\nsectors present on the board today:")
for sec, cnt in c.most_common(12):
    print(f"   {cnt:3d}  {sec}")
