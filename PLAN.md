# PLAN — rebuilding Project_Scalp around Sri's instruction sheet

> **STATUS 04-Sep-2026, 20:20.** Boxes 1-4 complete. Box 5 measured, nothing
> shipped. Box 6 partly complete. Boxes 7-8 measured on a repaired instrument.
> Two instrument defects found and fixed today; a third (price coverage for
> non-carded names) fixed for Monday. Nothing has been shipped to live trading:
> `live_config.json` is exactly as it was on 04-Sep morning.
>
> The single result worth acting on: **every dynamic exit beat every fixed
> target**, on a broad plateau, every covered session positive. Sri's instinct
> about exits was right, and it is the first thing the data has supported rather
> than contradicted. See OBSERVATIONS.md, 04-Sep 20:10.

Written 04-Sep-2026, from `Live_Trading_Instructions.xlsx`, the flow diagram and
the 04-Sep conversation. Nothing here has been started. Every box of the sheet
appears below with what exists, what is missing, and what has to be measured.

Status key:  **WORKS** = built and used by the trading engine.
**ORPHAN** = built, displayed, logged, and never read by the thing that trades.
**MISSING** = not built.

---

## THE HEADLINE

Of ~30 requirements in the sheet: **6 WORK**, **9 are ORPHANS**, the rest are
MISSING. The orphans are the interesting group. `live_paper.py` — the file that
actually places trades — contains **not one reference** to ema9, sma12, macd,
rsi, vwap, supertrend, ichimoku, psar, hott or the consolidation zone. All of
them are computed. All are drawn on the cards. All are written to the logs. The
engine has never looked at any of them. It trades on `urgency` and `from_open`
plus the four freshness gates added on 03-Sep.

That is the single biggest gap between what Sri watches and what the system does.

---

## FOUR LANES, RUN IN PARALLEL

| Lane | What | Why this order |
|---|---|---|
| **C** | DEXT capture (Time & Sales, order flow) | **Perishable.** Every unrecorded session is gone forever. |
| **E** | Indicator layer on 30s bars | Everything in Lane A depends on it. |
| **A** | Entry/exit measurement | 9 sessions already on disk — answerable today. |
| **B** | Pre-open pipeline (the flow diagram) | Cannot be tested before an open. Target: Monday. |

---

# BOX 1 — "Anytime before 9AM (whenever server starts)"

No 9AM boundary. Runs whenever the server starts. Output is a **watch list**,
never a gate — it decides what is looked at, never what is bought.

### 1.1 News tab — sentiment, order wins, approvals, results
- **Status:** WORKS, with one crash. `Movers_dhannews.py` fetches
  `news-live.dhan.co/news/snippetgrouping`, scores impact, tiers by day move,
  filters to NSE, drops noise. `Movers_premarket.py` classifies direction and
  confidence with Wilson intervals.
- **D1 — FIX:** line 477, `NameError: _dropped_bse is not defined`, kills
  `premarket_loop` in the pre-open window — exactly when this box needs it.
- **B1.1 — BUILD:** join news output to the watch list. Advisory only.

### 1.2 Past-1-month habitually bullish names (the "HFCL list")
- **Status:** MISSING as a list. Foundation exists — `Movers_context._daily()`
  already pulls daily bundles and computes run-up and volatility.
- **B1.2 — RESEARCH:** over ~60 sessions, which names *repeatedly* print real
  intraday runs? Not "% gain over the month" — frequency of intraday runs.
  Output is an objective list Sri can correct, not my opinion of one.
- Read-only. Can run today.

---

# BOX 2 — 9:00–9:08 PreMarket → "Preliminary list of stocks that may be bullish"

### 2.1 Pre-market indicative price / order book / indicators
- **Status:** WORKS. `Opus_preopen.py` scans 09:00–09:15, reads indicative price
  and buy/sell quantity, computes imbalance, and remembers each symbol's *first*
  imbalance to report whether it is **strengthening** — not just a snapshot.
- **Archived:** 22 sessions of `preopen_*.jsonl`. Enough to derive thresholds
  honestly.
- **A5 — MEASURE:** does pre-open imbalance (and its trend) predict the
  intraday run? Never tested. 22 sessions available.

### 2.2 Dhan Volume Shockers (except penny stocks)
### 2.3 Dhan Price Shockers (except low-volume stocks)
- **Status:** endpoints in use elsewhere (`scanx/daygnl`, `scanx/topvolume`,
  `scanx/mostactive`) but **not wired into this box**.
- **BLOCKER:** these snapshots are **not being archived** — last capture
  05-Aug. Sri's method (derive the volume/momentum cutoff from the past week)
  is right but the week does not exist yet.
- **C2 — START ARCHIVING TODAY.** Ten-line job. Until it has run a week, any
  threshold we set is a guess and will be labelled as one.
- **B2 — BUILD:** wire both into the preliminary list.

### 2.4 Exclusions
- Penny = **< ₹20** (Sri's rule).
- **CONFLICT, unresolved:** shipped config is `MIN_PRICE 100`, because ₹20–100
  measured 37% win / −0.04% over 27 samples. Sri's rule says ₹20. Keeping 100
  until more samples exist; to be revisited with the number, not the opinion.
- Low-volume cutoff: derive from the stock's own recent average (Sri, Q5).

---

# BOX 3 — 9:09 "List of stocks — Highly bullish probability" + MyWatchlist

### 3.1 The 9:09 list
- **Status:** MISSING as a single artefact.
- **B3 — BUILD:** freeze at 9:09, after matching completes and the real open is
  known. Inputs: preliminary list + pre-market imbalance + shockers + news +
  habitual list. Size uncapped (Sri: open, no restriction).
- Sri's note: the 9:09 list will differ from the 9:00 list, **usually larger**.

### 3.2 MyWatchlist — 4 lists from the Dhan account
- **Status:** MISSING. `Movers_watchlist.py` exists but is not fed from Dhan.
- **B4 — CAPTURE, ONE-TIME, TODAY:** read all 4 watchlists via Chrome, resolve
  each symbol against the scrip master, write to a file the board reads.
  Needs Chrome open. ~15 minutes. Fixed until Sri says they changed.
- Watchlist names are **monitored for the first 30 minutes regardless of
  filters** (Sri, Q4), then dropped unless something unusual appears.

---

# BOX 4 — 9:09–9:14 "Idle Time"

Not idle for us. Sri's human equivalent: opening every chart so he is ready.

- **B5 — BUILD:** pre-warm everything so nothing is computed at 9:15.
  - Pull 30-second history for every watched name via `fetch_tf30.py`
    (`ticks.dhan.co/getDataS` — the exact series Sri's chart is drawn from).
  - Compute the full indicator stack so EMA/MA/MACD/RSI are **live at
    09:15:00**, not warming up.
  - Attach the news reason to each name, so a move has an explanation attached
    when it happens.
- **CAUTION, to be tested not assumed:** on a gap-up the first candle opens far
  above yesterday's EMA, so the 8/12 cross fires instantly on nearly every
  gapper. At 9:15 the cross is close to free. It needs a companion condition in
  that window.

---

# BOX 5 — 9:15–9:16 "No trades until 100% confident"

- **B6 — BUILD:** hard no-entry window 09:15:00–09:16:00, config flag.
- **C1 — CAPTURE (see Lane C):** understand volume/order flow from DEXT.

---

# BOX 6 — 9:16 onwards — the trading window

### 6.1 "Bullish candle with volume and price up → take trade"
- **A2 — MEASURE:** one 30s candle, two candles, or partway into candle 2?
  Sri's concern is real and specific: a stock can peak in candle 1 and collapse
  in candle 2, and 09:15–09:30 has the **highest stop rate of the day (35%)**.
  Waiting 30s costs entry price; not waiting costs accuracy.
  **Middle path to test:** act 10–15s into candle 2 if price still holds above
  candle 1's high — confirmation without paying the full minute.

### 6.2 The dip at 9:16 — three cases from the sheet
1. profit booking from highs → wait for price up
2. dip-ok pullback → wait for price up
3. against-market news (good news, traders sell) → avoid
- **Status:** ORPHAN. The card already carries `off_peak`, `since_high_s`,
  `dipsUsed`, `ema_angle` — Sri's answer to "what tells you buyers still
  control". The engine reads them at **entry** only, never at exit.
- **A3 — MEASURE:** do these fields separate a resumable dip from a reversal?

### 6.3 Unusual volume, monitored for the green-candle jump
- **A6 — MEASURE:** unusual against **the stock's own recent average** (Sri,
  Q5), not a market-wide threshold. Backtestable from `bars30`.

### 6.4 "Take as many trades as possible"
- **Sri's correction, accepted:** the 11-trades-vs-29-trades comparison was the
  same day at six slot counts with the best reported after the fact — hindsight,
  and trade count was *correlated* with profit, not shown to cause it.
  What is arithmetic: charges were **₹2,584 against ₹3,252 gross** on 03-Sep.
- **A1 — TRADE AUTOPSY:** across all 9 sessions, why did the losers lose? Find
  what the losing cohort shares. Fewer trades is a symptom to explain, not a
  rule to adopt.

---

# BOX 7 — Sri's entry strategy (his exact chart stack)

Parameters read directly off his screenshots — the board does **not** currently
match any of them.

| Indicator | Sri's setting | Status |
|---|---|---|
| MA/EMA Cross | **MA 12 / EMA 8** | board computes **EMA 9** — wrong period |
| MACD | CM MACD Custom **12, 26, close, 9, EMA** | ORPHAN (`macd_series` exists) |
| RSI | **14, with SMA 14 signal** | ORPHAN (`rsi` exists) |
| Parabolic SAR | **0.02, 0.02, 0.2** | ORPHAN (`psar_series` exists) |
| Heiken Ashi SuperTrend | **10, 3** (chart) — handoff recorded 10,2 | to be tested both ways |
| Ichimoku | **9, 26, 52, 26, 26** | ORPHAN (`ichimoku_series` exists) |
| HOTT/LOTT | Pine port, in Board cards | ORPHAN |
| Consolidation zone | Pine port, in Board cards (`consolidation_zone`, `consolidation_zones` in `Opus_indicators.py`) | ORPHAN |
| VWAP | not on his chart | MISSING from engine; free from `bars30` |

Sri works **both 30s and 1m** (MAN INDUSTRIES 30s, GUJARAT MINERAL 1m).

### The signals, as he stated them
1. EMA 8 crossing above MA 12 — first signal
2. **Width** of the EMA/MA gap = strength
3. MACD crossover, usually simultaneous; **above the zero line = strongest**
4. RSI as a **strength reading**, not a hard gate (Sri, Q5)
5. SAR below the candle; **distance** = strength; SAR approaching = dip coming
6. HOTT/LOTT: candle crossing the upper band and turning cyan
7. Consolidation zone: exit near the upper edge = high probability of a jump
8. Ichimoku (Sri, Q3): **future cloud colour** = trend ahead; **thickness** =
   reversal risk; **candle-to-cloud distance** = trend strength

### Work
- **E1 — BUILD the indicator layer** on 30-second bars, every indicator above at
  Sri's exact parameters. New file. Read-only over `bars30_*.jsonl`.
- **E0 — CALIBRATION GATE, blocks everything:** reproduce one stock, one day,
  and Sri checks it against his own Dhan chart. If the numbers do not match his
  screen, every result downstream is fiction. This is the check `card_lab.py`
  did not have — it cost an evening there instead of a trading session.
- **A4 — MEASURE 10/3 vs 10/2** on SuperTrend rather than choosing by opinion.

---

# BOX 8 — Exit strategy

- **Sri's rule:** candle closes below EMA → exit. Explicitly "you need not
  follow the same".
- **Sri's stronger point (Q4 + Q1):** exits should be **dynamic** — hold while
  the stock keeps printing higher highs with shallow dips, exit when that stops.
  A trailing stop, not a fixed +2%.
- **Status:** MISSING, and this is the **largest untested hole in the system.**
  Wider fixed targets and wider fixed stops were all tested and all lost. A
  trailing stop and a hold-while-higher-highs rule have **never been tested at
  all.**
- **A7 — MEASURE:** trailing stop / higher-highs hold vs the fixed +2% / −1%.
  This is where TBZ's +8.4% after we sold went.

---

# BOX 9 — Sri's observations of the engine's failures

| His observation | Measured verdict |
|---|---|
| Picking non-momentum stocks | Confirmed — the 14–16 urgency band loses money |
| Picking penny stocks (<₹20) | Confirmed — ₹20–100 is 37% win, −0.04%, n=27 |
| Picking non-volume stocks | Confirmed — under ₹50L/min is the worst bucket, 31% win |
| Not in Board bull category / exhausted | Partly addressed 03-Sep (freshness gates) |
| Entering at the peak | **Inverted by data** — top-of-range entries are the *best* bucket; dip entries reached +2% zero times. The real fault is entering *after the roll-over*, which is a different thing |
| Exit unclear, holds after profit | **Unaddressed. See Box 8.** |

---

# BOX 10 — Session timing

Sri: 9:15–9:45 best; 9:45–10:30 also strong with monitoring; an abnormally large
candle usually invites profit booking on the next one.

Measured over 6 sessions — **and it corrects the intuition**:

```
09:15-09:30  123 cards  46% win  31% hit +2%  35% STOP   volume, but violent
09:30-09:45   25        52%      28%          28%
09:45-10:00   14        78%      28%           7%        few, but safe
10:00-10:15   11        63%       9%           0%
10:30-11:30   20        45%      15%          15%
```

85% of every opportunity ever found arrives before 10:30. But the open holds the
**volume**, not the **generosity** — it is the most violent hour of the day.

- **DO NOT REBUILD the phase table.** Time-banded gates were tried twice (5 then
  8 bands) and scored **₹31,178 vs ₹44,870 baseline — a −₹13,692 regression**,
  fitted to 6–14 samples per band. `PHASE_ENABLED=False`. Needs new evidence,
  not another attempt.
- **A8 — MEASURE:** the abnormal-candle → next-candle-profit-booking claim.
  Never tested, and testable directly on `bars30`.

---

# LANE C — DEXT CAPTURE (first, because the data is perishable)

- **C0 — CHECK LOOKBACK (2 minutes, read-only).** How far back does DEXT's
  history reach? It served yesterday's 15:03–15:12 footprint at 08:07 today, so
  at least a day. 30 days = no urgency. 2–3 days = capture jumps the queue.
- **C1 — BUILD the capture.** Order flow rides a live stream
  (`stream.dhan.co`), not a plain request — no endpoint to copy. Routes, in
  order of preference: official Dhan API if it exposes this; a stream client
  using Sri's logged-in session; screen reading as a last resort.
  **Capture Time & Sales first** — it is tick-level, so 30s, 1m and the
  footprint can all be rebuilt from it. The 1-minute footprint can never be
  broken back down.
  **Honest limit:** hundreds of symbols cannot be streamed. The capture set is
  itself a decision — watchlist plus carded names — and anything outside it
  produces no history at all.
- **C2 — ARCHIVE the shocker snapshots** (see 2.2). Ten lines. Today.
- Order flow timeframes are **1m / 5m / 10m / 15m — never 30s**, so it will
  always be coarser than the decision timeframe.

### Also on DEXT, worth having
- **Depth** — the live bid/ask ladder through the session; the same idea as
  pre-open imbalance, all day.
- **VWAP** — free, computable from `bars30` today, and the engine has none.
- **Heatmap** — market breadth. The engine currently cannot tell a day when
  everything is red from a day when everything is green.
- **Not for us:** all options/F&O widgets, the manual scalper tools, the
  real-money account panels.

---

# LANE D — KNOWN BUGS

1. **D1** — `Movers_dhannews.py:477` `NameError: _dropped_bse` kills
   `premarket_loop` in the pre-open window. Blocks Box 1.1 and Box 2.
2. **D2** — **`PREOPEN_BOOST` is dead code.** Of 164 early cards across 6
   sessions, **zero** carried `preopen=True`. A +4.0 entry-rank boost that has
   never once fired. Highest-value open bug — pre-open strength is precisely the
   signal for the 09:15–10:00 window.
3. **D3** — `MAYANVAR` never carded at all on 03-Sep despite a clear run. A
   detection gap, not a rules gap.
4. **D4** — `super_check.py` writes a test symbol `AAA` into the production
   super log. Every analysis file filters it. A workaround, not a fix.

---

# WHAT RUNS WHEN

**Today (market open, board running untouched on frozen config):**
- C0 lookback check · C2 start archiving · B4 watchlist capture (needs Chrome)
- B1.2 habitual-movers research · E0 calibration gate → **Sri checks**
- E1 indicator layer · A1 trade autopsy

**Today, after the calibration gate passes:**
- A2 entry test · A7 exit test (the biggest hole) · A4 SuperTrend 10/3 vs 10/2
- A3 dip fields · A6 unusual volume · A8 abnormal candle

**Weekend:**
- C1 DEXT capture client · B1–B3 pre-open pipeline · B5 pre-warm · B6 no-entry
  window · D1, D2 fixes

**Ship gate — nothing reaches the live engine without both:**
1. `replay_live.py` forward-only over all 9 sessions
2. `regress.py` clean against the locked baseline (exits 1 on regression)

Baseline, ₹1,00,000, full engine: 27-Aug −501 · 28-Aug 11,455 · 31-Aug −1,742 ·
01-Sep 20,298 · 02-Sep 14,265. **Two of five sessions already lose money.** Any
claim of improvement has to move those two without giving back the other three.

---

# STANDING CONSTRAINTS

1. A per-trade statistic is a **hypothesis**. It ships only when full-engine
   replay agrees. Violated three times; every violation cost money.
2. `superstocks.py` is **FROZEN** at Sri's instruction. Experiment in
   `superstocks_lab.py`.
3. **Savepoint before any code edit:** `python3 savepoint.py create "reason"`.
4. New files, not edits, wherever possible. `live_paper.py` stays untouched
   until a ship gate is passed.
5. Never run `tune.py` without `--dry`. It writes `live_config.json` and has
   mutated live config mid-session.
6. The auto-tuner is **off today** (`learn.py --brief --notune`) so this session
   is a clean 9th data point.
7. **Before trusting any replay result, ask what the replay does differently
   from the live engine.** It is never nothing. Four faults came from exactly
   this.
8. Widening what is **watched** (Lane B) and loosening what is **bought**
   (Lane A) must never blur, or we will not be able to tell which one moved the
   money.
