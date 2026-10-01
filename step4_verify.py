"""
step4_verify.py -- do OUR daily indicators match Dhan's own screener?

The ScanX page publishes RSI(14), MACD Histogram and Supertrend for every stock
it returns. Those are the exact three values this tab gates on, so they are a
free, authoritative answer key -- far better than trusting our own arithmetic.

The values below were read off intraday-momentum-blast-imb-411739 on 31-Aug-2026.

ONE THING TO SETTLE HERE. The daily feed's last bar is the previous COMPLETED
session -- on 31-Aug it returned data ending 28-Aug. If ScanX includes today's
forming daily candle and we do not, every value is one session stale, and a
gate computed on stale RSI is a gate that quietly admits the wrong stocks. So
this computes BOTH ways:
    A. completed daily bars only          (what the feed gives)
    B. plus today's session folded in     (built from the 1-minute feed)
and prints them side by side with what Dhan actually showed.

Read-only. Output: console + logs\\STEP4_VERIFY.txt
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "STEP4_VERIFY.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_chartfeed as cf
import scanx_blast as bl
import Opus_engine as engine
IST = engine.IST

# symbol -> what ScanX displayed on 31-Aug (rsi, macd_hist, supertrend, price)
REF = {
    "SUNPHARMA":  (64.24,  3.30, 1835.36, 1984.80),
    "AXISBANK":   (64.38,  8.18, 1207.66, 1300.00),
    "GRASIM":     (63.37,  0.37, 3091.11, 3371.00),
    "DLF":        (61.71,  1.18,  641.22,  691.05),
    "BHEL":       (64.88,  1.82,  398.63,  442.65),
    "BPCL":       (60.55,  0.56,  304.56,  324.05),
    "NYKAA":      (66.34,  0.90,  318.16,  349.55),
    "AUROPHARMA": (68.89,  2.64, 1558.14, 1717.00),
    "MCX":        (72.73, 42.10, 3052.80, 3400.00),
    "IPCALAB":    (69.21, 12.14, 1799.89, 1974.90),
}

sid_of = {}
with (HERE / "security_id_list.csv").open(newline="", encoding="utf-8", errors="ignore") as f:
    for r in csv.DictReader(f):
        sym = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
        if (sym in REF and sym not in sid_of
                and (r.get("SEM_EXM_EXCH_ID") or "").upper() == "NSE"
                and (r.get("SEM_SEGMENT") or "").upper() == "E"
                and (r.get("SEM_SERIES") or "").upper() in ("EQ", "BE")):
            sid_of[sym] = str(r.get("SEM_SMST_SECURITY_ID")).strip()

p(f"STEP 4 VERIFY   {datetime.now(IST):%d-%b %H:%M:%S}")
p(f"resolved {len(sid_of)} of {len(REF)} symbols")
p("")
p(f"  {'SYMBOL':<11}{'RSI ours':>9}{'RSI +today':>11}{'RSI Dhan':>9}   "
  f"{'MACD ours':>10}{'+today':>8}{'Dhan':>7}   {'ST ours':>9}{'+today':>9}{'Dhan':>9}  lastbar")
p("  " + "-" * 108)

hits = 0
for sym, (r_ref, m_ref, s_ref, px) in REF.items():
    sid = sid_of.get(sym)
    if not sid:
        p(f"  {sym:<11} not in the instrument master"); continue
    a, err = cf.daily(sid, sym, days=400)
    if err or not a:
        p(f"  {sym:<11} daily fetch failed: {err}"); continue
    c, h, l = [float(x) for x in a["c"]], [float(x) for x in a["h"]], [float(x) for x in a["l"]]
    last = datetime.fromtimestamp(a["t"][-1], IST).strftime("%d-%b")

    rA, mA, sA = bl.rsi(c), bl.macd_hist(c), bl.supertrend(h, l, c)

    # B: fold today's session in, from the 1-minute feed
    rB = mB = sB = None
    m1, e2 = cf.get(sid, interval="1", days=2)
    if m1 and m1.get("c"):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        idx = [i for i in range(len(m1["t"]))
               if datetime.fromtimestamp(m1["t"][i], IST).strftime("%Y-%m-%d") == today]
        if idx:
            c2 = c + [float(m1["c"][idx[-1]])]
            h2 = h + [max(float(m1["h"][i]) for i in idx)]
            l2 = l + [min(float(m1["l"][i]) for i in idx)]
            rB, mB, sB = bl.rsi(c2), bl.macd_hist(c2), bl.supertrend(h2, l2, c2)

    def f(x, nd=2):
        return "--" if x is None else f"{x:.{nd}f}"
    p(f"  {sym:<11}{f(rA):>9}{f(rB):>11}{r_ref:>9.2f}   "
      f"{f(mA):>10}{f(mB):>8}{m_ref:>7.2f}   {f(sA):>9}{f(sB):>9}{s_ref:>9.2f}  {last}")
    if rB is not None and abs(rB - r_ref) < 2.0:
        hits += 1

p("")
p("=" * 108)
p(f"  {hits} of {len(REF)} match Dhan's RSI within 2 points when today's session is included.")
p("  If column '+today' tracks Dhan and 'ours' does not, the daily values must be")
p("  built from completed bars PLUS today -- otherwise every gate here runs one")
p("  session behind, and nothing on screen would reveal it.")
p("=" * 108)
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nReport written to {OUT}")
