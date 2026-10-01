"""
step4_daily_probe.py -- find a working DAILY candle source.

The ScanX Momentum Blast tab needs 30+ DAILY bars per stock (RSI-14, MACD,
Supertrend and a 20-day average volume). It was written against
/v2/charts/historical, which returns DH-905 on this account, so the tab cannot
warm up at all until a daily source is found.

Two hosts appear in Dhan's own chart bundle -- ticks.dhan.co (which serves the
1-minute and 30-second feeds we already use) and charts-api.dhan.co -- and two
paths, getData and getDataH. The H almost certainly means "historical". This
tries every sensible combination and reports which returns daily bars.

Read-only. Output: console + logs\\STEP4_DAILY_PROBE.txt
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "STEP4_DAILY_PROBE.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_chartfeed as cf
import Opus_engine as engine
IST = engine.IST
JWT, CID, BID, SRC = cf.creds(force=True)
FMT = "%a %b %d %Y %H:%M:%S GMT+0530 (India Standard Time)"
SID = 1333            # HDFCBANK

end = datetime.now(IST)
start = end - timedelta(days=120)


def probe(host, path, interval):
    body = {"EXCH": "NSE", "SEG": "E", "INST": "EQUITY", "SEC_ID": SID,
            "START": int(start.timestamp()), "END": int(end.timestamp()),
            "START_TIME": start.strftime(FMT), "END_TIME": end.strftime(FMT)}
    if interval is not None:
        body["INTERVAL"] = interval
    hd = {"Auth": JWT, "Cid": CID, "Bid": BID, "Src": SRC,
          "Content-Type": "application/json", "Accept": "*/*",
          "Origin": "https://tv.dhan.co", "Referer": "https://tv.dhan.co/",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"}
    label = f"{host}{path}  INTERVAL={interval!r}"
    try:
        req = urllib.request.Request(f"https://{host}{path}",
                                     data=json.dumps(body).encode(), headers=hd,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        p(f"  {label:52s} -> HTTP {e.code}")
        return None
    except Exception as e:
        p(f"  {label:52s} -> {type(e).__name__}")
        return None
    finally:
        time.sleep(0.35)
    if not d.get("success"):
        p(f"  {label:52s} -> success=false")
        return None
    a = d.get("data") or {}
    t = a.get("t") or []
    if not t:
        p(f"  {label:52s} -> 0 bars")
        return None
    sp = [t[i + 1] - t[i] for i in range(min(8, len(t) - 1))]
    common = max(set(sp), key=sp.count) if sp else 0
    kind = ("DAILY" if common >= 80000 else
            f"{common}s bars -- not daily")
    p(f"  {label:52s} -> {len(t):4d} bars  {kind}  "
      f"{datetime.fromtimestamp(t[0], IST):%d-%b} .. {datetime.fromtimestamp(t[-1], IST):%d-%b}")
    return (len(t), common, a) if common >= 80000 else None


p(f"STEP 4 DAILY PROBE   {datetime.now(IST):%d-%b %H:%M:%S}   HDFCBANK sid 1333")
p(f"asking for 120 days; a daily source should return roughly 80 bars")
p("")
p("=" * 78)
win = None
for host in ("ticks.dhan.co", "charts-api.dhan.co"):
    for path in ("/getDataH", "/getData"):
        for iv in ("D", "1D", "1440", None):
            r = probe(host, path, iv)
            if r and not win:
                win = (host, path, iv, r)
p("=" * 78)
p("")
if win:
    host, path, iv, (n, sp, a) = win
    p(f"  DAILY SOURCE FOUND: https://{host}{path}  INTERVAL={iv!r}")
    p(f"    {n} daily bars, spacing {sp}s")
    p(f"    last 3 closes: {a['c'][-3:]}")
    p(f"    last 3 volumes: {a['v'][-3:]}")
    p("    -> enough for RSI-14, MACD and Supertrend."
      if n >= 30 else "    -> NOT enough bars for the indicators (need 30+).")
else:
    p("  No daily source found on either host.")
    p("  Fallback: build daily bars by aggregating the 1-minute feed, which")
    p("  reaches back about 8 sessions -- too few for RSI-14 and MACD, so the")
    p("  tab's daily confirmations would have to be switched off (USE_RSI /")
    p("  USE_MACD / USE_SUPERTREND are already switches for exactly this).")
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nReport written to {OUT}")
