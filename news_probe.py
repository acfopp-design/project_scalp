"""
news_probe.py -- three questions about the News tab, answered with numbers.

1. HOW BAD IS "no news"?  WHY THEY'RE MOVING matches board movers to Dhan
   headlines by exact symbol. If most movers genuinely have no headline the tab
   is working and simply has nothing to say -- which is a reason to replace it,
   not to debug it. Measured against today's actual board movers.

2. DOES THE FEED CARRY A SECTOR?  We parse only a handful of fields out of each
   news item. If Dhan already tags sector or industry, a sector view costs no
   new data source at all. This dumps every key an item actually has.

3. IF NOT, IS THERE A SECTOR LIST?  Dhan's ScanX bundle references a
   SECTOR_LIST endpoint. This tries it with the JWT we already hold.

Read-only. Output: console + logs\\NEWS_PROBE.txt
"""
import json, sys, urllib.error, urllib.request
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "NEWS_PROBE.txt"
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_dhannews as dn
import Opus_quotes_v3 as q3
import Opus_engine as engine

p(f"NEWS PROBE  {datetime.now(engine.IST):%d-%b %H:%M:%S}")

# ---- 1. coverage --------------------------------------------------------
items, err = dn.fetch(log=lambda m: None)
p(f"\n1. FEED: {len(items)} raw items since the previous close (err={err})")
cur, _ = dn.curated(drop_neutral=True)
p(f"   curated to {len(cur)} rows (one per stock, neutral filings dropped)")

board = set()
lg = HERE / "logs" / "movers_board" / f"board_{datetime.now():%Y%m%d}.jsonl"
if lg.exists():
    for ln in lg.open(encoding="utf-8", errors="replace"):
        try: r = json.loads(ln)
        except Exception: continue
        for _, cards in (r.get("panels") or {}).items():
            for c in cards:
                if c.get("sym"): board.add(str(c["sym"]).upper())
raw_syms = {str(i.get("sym") or "").upper() for i in items if i.get("sym")}
cur_syms = {str(i.get("sym") or "").upper() for i in cur if i.get("sym")}
hit_raw = board & raw_syms
hit_cur = board & cur_syms
p(f"   board movers today: {len(board)}")
p(f"   ...with ANY headline    : {len(hit_raw):3d}  ({len(hit_raw)*100//max(1,len(board))}%)")
p(f"   ...with a CURATED row   : {len(hit_cur):3d}  ({len(hit_cur)*100//max(1,len(board))}%)")
p(f"   -> so 'no news' is correct for {100-len(hit_cur)*100//max(1,len(board))}% of movers")
p(f"   examples WITH news   : {sorted(hit_cur)[:10]}")
p(f"   examples WITHOUT news: {sorted(board-raw_syms)[:10]}")

# ---- 2. does an item carry a sector? -----------------------------------
p("\n2. FIELDS ON A RAW NEWS ITEM")
if items:
    keys = sorted({k for i in items[:200] for k in i.keys()})
    p(f"   parsed keys: {keys}")
    sect = [k for k in keys if any(w in k.lower() for w in ("sect", "indus", "categ"))]
    p(f"   sector-ish keys: {sect or 'NONE -- the parser keeps no sector'}")
    p(f"   sample item: {json.dumps(items[0], default=str)[:400]}")

# ---- 3. is there a sector list endpoint? -------------------------------
p("\n3. SCANX SECTOR LIST")
cid, tok, jwt = q3._env()
for host, path in (("scanx.dhan.co", "/scanx/sectlist"),
                   ("ow.dhan.co", "/scanx/sectlist"),
                   ("scanx.dhan.co", "/customscan/sectlist")):
    try:
        req = urllib.request.Request(
            f"https://{host}{path}", data=json.dumps({"Data": {}}).encode(),
            headers={"accept": "application/json", "content-type": "application/json",
                     "auth": jwt, "authorisation": "Token",
                     "origin": "https://web.dhan.co", "referer": "https://web.dhan.co/"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            t = r.read().decode()
        p(f"   {host}{path} -> {r.status}, {len(t)} bytes")
        p(f"      {t[:300]}")
    except urllib.error.HTTPError as e:
        p(f"   {host}{path} -> HTTP {e.code}")
    except Exception as e:
        p(f"   {host}{path} -> {type(e).__name__}")

OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nSaved to {OUT}")
