"""
macd_compare.py -- what the BOARD is serving vs what the feed says it should be.

The values matched Dhan to four decimals on the last bar, but the drawn line
does not match the shape on his screen. A last-value match cannot prove the
SERIES is right, so this pulls the actual arrays the board is sending to the
browser (GET /state) and lays them next to the same window computed straight
from the feed. Any divergence in shape shows up immediately.

Read-only: it only GETs from the running board and from the chart feed.
Output: console + logs\\MACD_COMPARE_REPORT.txt
"""
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "MACD_COMPARE_REPORT.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_chartfeed as cf
import Opus_indicators as I
import Opus_engine as engine
IST = engine.IST
SYM = (sys.argv[1] if len(sys.argv) > 1 else "ICIL").upper()

p(f"MACD COMPARE   {datetime.now(IST):%d-%b %H:%M:%S}   symbol={SYM}")

# ---- 1. what the board is actually serving --------------------------------
try:
    with urllib.request.urlopen("http://127.0.0.1:5005/state", timeout=20) as r:
        st = json.loads(r.read().decode())
except Exception as e:
    p(f"  cannot reach the board on :5005 -- is it running?  ({type(e).__name__}: {e})")
    OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

card = None
for k in ("ov", "bt", "mv", "bv", "im", "pm", "super"):
    for c in (st.get(k) or []):
        if (c.get("sym") or "").upper() == SYM:
            card = c; break
    if card: break
if not card:
    p(f"  {SYM} is not on the board right now. Cards present: "
      + ", ".join(sorted({c.get('sym') for c in (st.get('ov') or [])})[:25]))
    OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

ch = card.get("chart") or {}
p(f"  card: tf={card.get('tf')} src={card.get('tfSrc')} price={card.get('price')} "
  f"day%={card.get('day_pct')} bars={card.get('bars')}")
p(f"  chart arrays: " + ", ".join(f"{k}={len(ch.get(k) or [])}" for k in
  ("c", "tsec", "ema9", "sma12", "macdLine", "macdSignal", "macdHist")))

tsec = ch.get("tsec") or []
if tsec:
    p(f"  window shown: {datetime.fromtimestamp(tsec[0], IST):%d-%b %H:%M:%S}"
      f" .. {datetime.fromtimestamp(tsec[-1], IST):%H:%M:%S}")

# ---- 2. the same window, computed straight from the feed ------------------
import Opus_candle_v3 as cv3
b, err = cv3.fetch_seconds(str(card["sid"]), interval="30S")
if err or not b:
    p(f"  feed error: {err}"); OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)
fc = b["candles"]["close"]
ft = b["candles"]["timestamp"]
m = I.macd_series(fc)
n = len(fc)
w = len(ch.get("macdLine") or [])
s = max(0, n - w)
p(f"  feed: {n} bars, comparing the last {w}")

p("")
p(f"  {'time':>9} | {'board line':>11} {'feed line':>11} | {'board sig':>10} {'feed sig':>10} | "
  f"{'board hist':>11} {'feed hist':>10}")
p("  " + "-" * 92)
bad = 0
for i in range(w):
    j = s + i
    bl = (ch.get("macdLine") or [None] * w)[i]
    bs_ = (ch.get("macdSignal") or [None] * w)[i]
    bh = (ch.get("macdHist") or [None] * w)[i]
    fl, fs_, fh = m["line"][j], m["signal"][j], m["hist"][j]
    tm = datetime.fromtimestamp(ft[j], IST).strftime("%H:%M:%S")
    def f(x):
        return "None" if x is None else f"{x:.4f}"
    flag = ""
    if bl is not None and fl is not None and abs(bl - fl) > 0.005:
        flag = "  <-- LINE DIFFERS"; bad += 1
    p(f"  {tm:>9} | {f(bl):>11} {f(fl):>11} | {f(bs_):>10} {f(fs_):>10} | "
      f"{f(bh):>11} {f(fh):>10}{flag}")

p("")
p("=" * 92)
if bad:
    p(f"  {bad} of {w} bars disagree -> the board is computing a DIFFERENT series,")
    p("     not merely drawing it differently.")
else:
    p("  Every bar agrees. The numbers the browser receives are correct, so any")
    p("  remaining difference is in the DRAWING (scale, window length, or the")
    p("  number of bars shown), not the maths.")
    p("")
    p(f"  Board draws {w} bars = {w*30/60:.0f} minutes of tape.")
    p("  If Dhan's pane is showing a longer span, the two curves cannot look the")
    p("  same even though every value is identical -- more of the move fits on")
    p("  his screen than on the card.")
p("=" * 92)
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nReport written to {OUT}")
