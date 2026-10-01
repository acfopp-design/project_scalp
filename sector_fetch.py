"""
sector_fetch.py -- get the stock->sector map, and cache it.

The last probe returned HTTP 405 (method not allowed) on
    static-scanx.dhan.co/staticscanx/sectlist
405 is not 404: the path EXISTS, my POST was simply the wrong verb. Everything
else came back 404. So this tries GET, and if the response carries sectors it
writes logs/sector_map.json -- the mapping the whole sector view depends on and
that this project has never had.

If it works this is a once-a-day file, not a live dependency.
Read-only apart from that cache. Output: console + logs\\SECTOR_FETCH.txt
"""
import json, sys, urllib.error, urllib.request
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "SECTOR_FETCH.txt"
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Opus_quotes_v3 as q3
import Opus_engine as engine
cid, tok, jwt = q3._env()
HDR = {"accept": "application/json", "auth": jwt, "authorisation": "Token",
       "origin": "https://web.dhan.co", "referer": "https://web.dhan.co/",
       "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"}
p(f"SECTOR FETCH  {datetime.now(engine.IST):%d-%b %H:%M:%S}")

def get(url):
    try:
        req = urllib.request.Request(url, headers=HDR, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        try: return e.code, e.read().decode()[:200]
        except Exception: return e.code, ""
    except Exception as e:
        return -1, f"{type(e).__name__}"

best = None
for url in ("https://static-scanx.dhan.co/staticscanx/sectlist",
            "https://static-scanx.dhan.co/staticscanx/sectlist?seg=1",
            "https://static-scanx.dhan.co/staticscanx/sectlist/1",
            "https://static-scanx.dhan.co/staticscanx/industrylist",
            "https://static-scanx.dhan.co/staticscanx/scriplist"):
    st, body = get(url)
    p(f"  GET {url.split('staticscanx')[-1]:28s} -> {st}  {len(body)} bytes")
    if st == 200 and len(body) > 200:
        p(f"     {body[:300]}")
        if best is None:
            best = (url, body)

if not best:
    p("\n  No sector list retrieved. The sector view cannot be built from Dhan.")
    OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

url, body = best
try:
    d = json.loads(body)
except Exception as e:
    p(f"\n  response is not JSON ({e}) -- cannot use it")
    OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

def walk(o, depth=0):
    if depth > 3: return
    if isinstance(o, dict):
        p("   " * depth + f"dict keys: {list(o.keys())[:12]}")
        for k in list(o.keys())[:3]:
            walk(o[k], depth + 1)
    elif isinstance(o, list):
        p("   " * depth + f"list of {len(o)}")
        if o: walk(o[0], depth + 1)
    else:
        p("   " * depth + f"{type(o).__name__}: {str(o)[:80]}")

p("\n  SHAPE OF THE RESPONSE")
walk(d)
(HERE / "logs" / "sector_raw.json").write_text(json.dumps(d)[:2_000_000], encoding="utf-8")
p("\n  raw saved to logs/sector_raw.json -- I will read it and build the map")
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nSaved to {OUT}")
