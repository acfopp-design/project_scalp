"""
macd_check.py -- why does the card's MACD not match Dhan's 30-second chart?

TWO CANDIDATE CAUSES, and this settles which:

1. WARM-UP. Opus_candle_v3 only prepends a previous session when TODAY has
   fewer than 90 bars. Today's 30s session has ~750, so from about 09:16 the
   series handed to the scorer is TODAY ONLY. Every EMA therefore restarts at
   09:15. TradingView does not restart: its MACD is continuous across sessions.
   Two different numbers, both "correct", and the card's is not the one on his
   screen. (This predates the 30s switch -- the 1-minute cards had it too.)

2. THE LAST BAR. The seconds feed truncates some symbols early (HDFCBANK
   stopped at 15:14:30 on 31-Aug while DIFFNKG ran to 15:29:30). A card whose
   last bar is 15 minutes behind Dhan's cannot agree with it.

Also checked: he is running "MA/EMA Cross 12 8" on Dhan -- SMA-12 and EMA-*8*.
The board computes EMA-*9*. If that is right, every cross the board reports is
computed on a different average from the one he is watching.

Read-only. Output: console + logs\\MACD_CHECK_REPORT.txt
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "MACD_CHECK_REPORT.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_chartfeed as cf
import Opus_indicators as I
import Opus_engine as engine
IST = engine.IST

SYM = (sys.argv[1] if len(sys.argv) > 1 else "ICIL").upper()

# ---- what Dhan showed on his screen, for a like-for-like comparison --------
REF = {"ICIL": {"when": "31-Aug 15:29:30 (last 30s bar)",
                "o": 470.35, "h": 471.00, "l": 470.35, "c": 470.90,
                "macd": 0.15, "signal": 0.20, "hist": -0.04,
                "ma12": 470.23, "ema8": 470.33}}

# ---- resolve the security id ---------------------------------------------
sid = None
with (HERE / "security_id_list.csv").open(newline="", encoding="utf-8", errors="ignore") as f:
    for r in csv.DictReader(f):
        if ((r.get("SEM_TRADING_SYMBOL") or "").strip().upper() == SYM
                and (r.get("SEM_EXM_EXCH_ID") or "").upper() == "NSE"
                and (r.get("SEM_SEGMENT") or "").upper() == "E"
                and (r.get("SEM_SERIES") or "").upper() in ("EQ", "BE")):
            sid = str(r.get("SEM_SMST_SECURITY_ID")).strip(); break
p(f"MACD CHECK   {datetime.now(IST):%d-%b-%Y %H:%M:%S}   symbol={SYM} sid={sid}")
if not sid:
    p("  could not resolve the security id"); OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

a, err = cf.get_seconds(sid, interval="30S", days=5)
if err or not a:
    p(f"  feed error: {err}"); OUT.write_text("\n".join(_buf), encoding="utf-8"); sys.exit(0)

t, c = a["t"], a["c"]
days = sorted({datetime.fromtimestamp(x, IST).strftime("%Y-%m-%d") for x in t})
today = days[-1]
first_today = min(i for i in range(len(t))
                  if datetime.fromtimestamp(t[i], IST).strftime("%Y-%m-%d") == today)
p(f"  fetched {len(t)} bars across {len(days)} sessions {days}")
p(f"  today starts at index {first_today} ({len(t)-first_today} bars today)")
p(f"  last 3 bars: " + ", ".join(
    f"{datetime.fromtimestamp(x, IST):%H:%M:%S}" for x in t[-3:]))
p(f"  last bar OHLC: o={a['o'][-1]} h={a['h'][-1]} l={a['l'][-1]} c={c[-1]}")

ref = REF.get(SYM)
if ref:
    p("")
    p(f"  DHAN SHOWED, {ref['when']}:")
    p(f"    OHLC o={ref['o']} h={ref['h']} l={ref['l']} c={ref['c']}")
    p(f"    MACD line={ref['macd']} signal={ref['signal']} hist={ref['hist']}")
    p(f"    MA12={ref['ma12']}  EMA8={ref['ema8']}")


def block(label, series):
    m = I.macd_series(series)
    p(f"  {label}")
    p(f"    bars used   : {len(series)}")
    p(f"    MACD line   : {None if m['line'][-1] is None else round(m['line'][-1], 4)}")
    p(f"    MACD signal : {None if m['signal'][-1] is None else round(m['signal'][-1], 4)}")
    p(f"    MACD hist   : {None if m['hist'][-1] is None else round(m['hist'][-1], 4)}")
    for n_ in (8, 9):
        e = I.ema_series(series, n_)
        p(f"    EMA-{n_}       : {None if e[-1] is None else round(e[-1], 4)}")
    sm = I.sma_series(series, 12)
    p(f"    SMA-12      : {None if sm[-1] is None else round(sm[-1], 4)}")


p("")
p("=" * 74)
p("A. TODAY ONLY  -- what the card computes right now")
p("=" * 74)
block("session-only series", c[first_today:])

p("")
p("=" * 74)
p("B. CONTINUOUS  -- every bar the feed returned, like TradingView")
p("=" * 74)
block("continuous series", c)

p("")
p("=" * 74)
p("C. HOW MUCH WARM-UP IS ACTUALLY NEEDED?")
p("=" * 74)
full = I.macd_series(c)["line"][-1]
for extra in (0, 50, 100, 200, 400, 800):
    st = max(0, first_today - extra)
    v = I.macd_series(c[st:])["line"][-1]
    d = None if (v is None or full is None) else round(abs(v - full), 5)
    p(f"    today + {extra:4d} prior bars -> MACD line {round(v,4) if v is not None else None}"
      f"   diff vs continuous {d}")

p("")
p("=" * 74)
p("Read B against the DHAN SHOWED block. If B matches and A does not, the fix")
p("is warm-up: hand the scorer the continuous series instead of today only.")
p("If NEITHER matches, compare the last bar time and OHLC first -- a card whose")
p("last bar is not Dhan's last bar can never agree, whatever the maths.")
p("=" * 74)
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nReport written to {OUT}")
