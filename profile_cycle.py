"""
profile_cycle.py -- where does a board cycle actually spend its time?

The cycle was 13-21 seconds against a 5-second target. Candle fetching has been
moved off the critical path; this measures what is LEFT, so the next change is
aimed at something measured rather than guessed.

Read-only apart from the board's own caches. Makes a handful of requests.
Output: console + logs\\PROFILE_CYCLE.txt
"""
import sys, time
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_buf = []
def p(s=""):
    print(s, flush=True); _buf.append(str(s))

import Movers_app as MA
import Opus_candle_v3 as cv3
import Opus_engine as engine
from Opus_badge_cash import CashScorer

p(f"PROFILE   {datetime.now(engine.IST):%d-%b %H:%M:%S}")
p(f"  CARD_TF={MA.CARD_TF}  MAX_SEC_BARS={cv3.MAX_SEC_BARS}  "
  f"scorer WINDOW={__import__('Opus_badge_cash').WINDOW}")

import Opus2_movers_source as ms
names, _ = ms.cash_names(None, lambda m: None)
p(f"  {len(names)} names from the lists")

# ---- one fetch, then time the pure computation many times ---------------
sid, sym = str(names[0]["sid"]), names[0]["sym"]
t0 = time.time(); b, err = cv3.fetch_seconds(sid, interval=MA.CARD_TF); t_fetch = time.time() - t0
if err or not b:
    p(f"  fetch failed: {err}"); Path("logs/PROFILE_CYCLE.txt").write_text("\n".join(_buf)); sys.exit(0)
nbars = len(b["candles"]["close"])
p(f"  fetch (network)      : {t_fetch*1000:7.0f} ms   {nbars} bars")

sc = CashScorer()
N = 20
t0 = time.time()
for _ in range(N):
    sc.compute_row(sym, sid, b)
t_score = (time.time() - t0) / N
p(f"  compute_row (CPU)    : {t_score*1000:7.1f} ms per card")

for cap in (800, 400, 200, 120):
    c = b["candles"]
    k = {kk: vv[-cap:] for kk, vv in c.items()}
    bb = {"candles": k, "prev_close": b["prev_close"], "session": b["session"],
          "today_bars": min(b["today_bars"], cap)}
    t0 = time.time()
    for _ in range(N):
        sc.compute_row(sym, sid, bb)
    dt = (time.time() - t0) / N
    p(f"    at {cap:4d} bars       : {dt*1000:7.1f} ms per card")

p("")
p("  WHAT THIS MEANS")
p(f"  A 60-card board recomputing every card each cycle costs "
  f"{t_score*60:.1f}s at the current bar count.")
p("  The cycle target is 5s, so if that number is over ~3s the series must be")
p("  trimmed -- macd_check.py already proved 800 bars and 120 bars give the")
p("  SAME indicator values to four decimals, so shorter is free accuracy-wise.")
Path("logs/PROFILE_CYCLE.txt").write_text("\n".join(_buf), encoding="utf-8")
print("\nSaved to logs/PROFILE_CYCLE.txt")
