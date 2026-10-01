"""
sector_probe.py -- is a stock->sector mapping reachable at all?

A sector view needs a sector for ~2,455 stocks. Nothing in this project has one:
security_id_list.csv has 16 columns and none of them is sector, universe.csv has
three, and logs/fundamentals holds exactly ONE file, written by hand.

Two candidates, and this settles both:
  A. Dhan's news items carry a `cat` field we currently ignore. If that is a
     sector or industry tag, the sector view costs no new source at all.
  B. Dhan's ScanX bundle references a SECTOR_LIST endpoint. My first three URL
     guesses returned 404/503, so this tries the other bases the bundle names.

Read-only. Output: console + logs\\SECTOR_PROBE.txt
"""
import json, sys, urllib.error, urllib.request, collections
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "SECTOR_PROBE.txt"
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_dhannews as dn
import Opus_quotes_v3 as q3
import Opus_engine as engine
p(f"SECTOR PROBE  {datetime.now(engine.IST):%d-%b %H:%M:%S}")

# ---- A. what is `cat`? --------------------------------------------------
items, _ = dn.fetch(log=lambda m: None)
cats = collections.Counter(str(i.get("cat") or "") for i in items)
p(f"\nA. `cat` on {len(items)} news items -- distinct values: {len(cats)}")
for v, n in cats.most_common(25):
    p(f"     {n:4d}  {v!r}")
p("   -> if these read like sectors, the sector view is free;")
p("      if they read like event types (results / order / rating), they are not sectors.")
lbl = collections.Counter(str(i.get("label") or "")[:40] for i in items)
p(f"\n   `label` sample: {[k for k,_ in lbl.most_common(6)]}")

# ---- B. sector list endpoints ------------------------------------------
cid, tok, jwt = q3._env()
p("\nB. SECTOR LIST CANDIDATES")
cands = [
    ("static-scanx.dhan.co", "/staticscanx/sectlist"),
    ("static-scanx.dhan.co", "/staticscanx/sector"),
    ("scanx.dhan.co", "/scanx/sectorlist"),
    ("scanx.dhan.co", "/customscan/sectlist"),
    ("scanx.dhan.co", "/scanx/sectorsummary"),
    ("ow.dhan.co", "/staticscanx/sectlist"),
]
for host, path in cands:
    for body in ({"Data": {}}, {"Data": {"Seg": 1}}, {}):
        try:
            req = urllib.request.Request(
                f"https://{host}{path}", data=json.dumps(body).encode(),
                headers={"accept": "application/json", "content-type": "application/json",
                         "auth": jwt, "authorisation": "Token",
                         "origin": "https://web.dhan.co", "referer": "https://web.dhan.co/"},
                method="POST")
            with urllib.request.urlopen(req, timeout=12) as r:
                t = r.read().decode()
            p(f"   {host}{path}  body={list(body)} -> {r.status}, {len(t)} bytes")
            if len(t) > 40:
                p(f"      {t[:400]}")
            break
        except urllib.error.HTTPError as e:
            p(f"   {host}{path}  body={list(body)} -> HTTP {e.code}")
        except Exception as e:
            p(f"   {host}{path} -> {type(e).__name__}")
            break
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nSaved to {OUT}")
