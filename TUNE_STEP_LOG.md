# Human-eye logic — incremental tuning log

## Engine
- `ind_lib.py`  indicators (PSAR+flip flags, ATR, Heikin-Ashi, Supertrend, HA-Supertrend, Ichimoku, EMA angle in ATR units)
- `tune_dyn.py` loader + Dhan charge model + dynamic per-stock range R
- `tune_v5.py`  indicator prep (`prep`)
- `tune_v6.py`  **current engine** — 2 concurrent slots, opening-thrust path + anticipatory path, adaptive dip exit
- `eye_cfg.py`  locked parameter set

No stock names anywhere in the code. Every threshold is scaled to the stock's own
20-bar median range `R`, its own expanding-median volume, and its own day open.

## Entry paths
A. **Opening thrust** (first `OPENB` bars) — green bar, body >= `OPENBODY`% , up >= `OPENCUM`%
   from the day open, volume >= `OPENVOL` x own median. Needed because on a fresh
   intraday series the EMA/SAR/Ichimoku history does not exist yet, while on
   TradingView it carries over from the previous session.
B. **Anticipatory continuation** — (fresh SAR flip within `FLIPW` bars **or**
   EMA8 angle >= `ANG` deg in ATR units) AND EMA8 > SMA12 AND MACD histogram rising
   AND RSI rising AND HA-Supertrend bullish.

## Exit (unchanged, self-calibrating)
A dip must outlast every dip the leg has already survived:
`limit = max(DIPF, DIPM * longest dip so far)`; hard stop at `STOPR * R`.

## Step 1 — AGI GREENPAC, 21-Sep-2026  ✅ PASS
| | |
|---|---|
| Sri (human eye), 7 trades | **+29.38%** |
| Engine, 12 trades, 9 wins | **+29.78%** |
| Difference | +0.40 pts (band is +/-5) |

Robustness: 1,140 of 103,680 grid points land inside the band, and the winning
corner is also the better corner on the *median* of the grid (SHORTS=False,
USEHA=True, DIPF=12, DIPM=1.0, OPENVOL=1.0) — not a single needle.

Sanity check: replaying Sri's own 7 entry times through the engine's exit gives
+24.56%, i.e. the exit logic is sound and the whole gap was entry timing.

Locked config: ANG=10 FLIPW=1 STOPR=4 DIPF=12 DIPM=1.0 SHORTS=False USEHA=True
USECLOUD=False OPENB=4 OPENBODY=0.3 OPENCUM=0.5 OPENVOL=1.0 COOL=4 LASTT=1430 MAXTR=12

## Data format
`backtest_data/intraday/<SYM>_<YYYYMMDD>.csv` - `hhmm,o,h,l,c,v` rows joined by `;`.
Each file begins with ~60 bars of the PREVIOUS session so EMA / SMA / MACD / RSI / SAR
are already seeded at 09:15, exactly as a continuous TradingView chart shows them.
The loader finds the session boundary by itself (the clock jumps backwards) and only
trades from there; warm-up bars feed the indicators and nothing else.
Finding this mattered: without warm-up AGI scored +19.05%, with it +24.71%.

## Capital model
One position at a time per stock (`SLOTS=1`), Rs 2,50,000 a trade, results expressed
as % of Rs 1,00,000 - the same convention as Sri's own book, which is strictly
sequential. Two concurrent slots are only for trading several stocks at once.

## Step 1 - AGI GREENPAC  (single stock, first pass)  PASS
Sri +29.36% (7 trades) vs engine +29.78% (12 trades). That pass used 2 slots on one
stock, which double-deploys the same leg; superseded by the Step-2 rules above.

## Step 2 - AGI + PROTEAN, one shared config  PASS
| stock | Sri | engine | diff |
|---|---|---|---|
| AGI GREENPAC | +29.36% | +24.71% | -4.65 |
| PROTEAN | +36.60% | +38.64% | +2.04 |
| **portfolio** | **+65.96%** | **+63.35%** | **-2.61** |

Both inside +/-5, portfolio inside +/-10.
Shared config: SLOTS=1 ANG=20 FLIPW=1 STOPR=2 DIPF=12 DIPM=1.0 SHORTS=False
USEHA=False USECLOUD=False OPENB=4 OPENBODY=0.3 OPENCUM=0.5 OPENVOL=1.0 COOL=4
LASTT=1430 EXITT=1515 MAXTR=8

Caveat: only 18 of 103,680 grid points put BOTH stocks in band, so this is a narrow
ridge. It needs to survive the remaining stocks before it means anything.

## Steps 3-5 - INDO-MIM, RAYMOND, FILATEX added  ALL PASS

One shared config, tuned jointly. Each stock backtested on its own (the whole
account free for that name), Rs 2,50,000 a trade, net of Dhan charges.

| stock | Sri | engine | diff |
|---|---|---|---|
| AGI GREENPAC | +29.36% | +31.61% | +2.25 |
| PROTEAN | +36.60% | +36.06% | -0.54 |
| INDO-MIM | +21.05% | +20.90% | -0.15 |
| RAYMOND | +20.79% | +21.76% | +0.97 |
| FILATEX | +20.33% | +18.05% | -2.28 |
| **total** | **+128.13%** | **+128.39%** | **+0.26** |

Every stock inside +/-5, total inside +/-10. 288 of 46,656 grid points pass both.

## What actually closed the gap (in order of size)

1. **Previous-session warm-up bars.** TradingView seeds EMA/SAR/MACD/RSI from the
   prior session; a fresh intraday series does not. AGI: +19.05% -> +24.71%.
2. **Shorts only after the stock has already run.** Every short in Sri's book came
   with the name up more than 6% on the day - he fades an exhausted move, he never
   shorts weakness. `SHORTCUM=6`.
3. **Keep-ratio gate after 10:15.** Sri's own point: turnover can be huge while
   price goes nowhere. Over a rolling 40-bar window, net move / total distance
   travelled separates a trend from chop. Below 0.12 after 10:15, stand aside.
   This stops the engine burning its trade budget on midday noise.
4. **The opening-thrust path**, which needs no indicator history at all.
5. **RSI/MACD slope over 3 bars, not 1.** A single-bar tick is noise; his
   "RSI promisingly going up" is a slope.

Things that were tried and did NOT help, so they are off: Ichimoku cloud filter,
HA-Supertrend filter, expansion-bar (body vs range) gate, new-high breakout gate,
and replacing the fixed EMA-angle threshold with the stock's own angle percentile.

Locked config is in `eye_cfg.py`.

## The honest portfolio number

The per-stock table above gives each stock the whole account. Traded together out
of ONE account at 5x (Rs 5,00,000 = 2 slots of Rs 2,50,000 shared across all five
names), `eye_portfolio.py` gives:

| | trades | net on Rs 1,00,000 |
|---|---|---|
| Sri's own 22 trades, 2 shared slots | 16 fit | **+80.81%** |
| Engine, 2 shared slots | 75 taken | **+46.93%** |

So the engine matches him stock by stock but loses badly on capital allocation:
it fires 75 times and clogs both slots with small midday trades, while his 16
trades are all worth taking. Trade SELECTION when two names signal at once is the
next thing to solve - it is worth ~34 points, far more than any further parameter
tuning.

## Out-of-sample check - EMMVEE, same day, never used in tuning

Locked config, nothing re-fitted: **19 trades, 8 wins, +13.98%** on Rs 1,00,000.

| stock | open->high | open->close | engine | Sri |
|---|---|---|---|---|
| AGI GREENPAC | +9.96% | +9.31% | +31.61% | +29.36% |
| PROTEAN | +19.40% | +19.40% | +36.06% | +36.60% |
| INDO-MIM | +8.04% | +8.04% | +20.90% | +21.05% |
| RAYMOND | +14.47% | +12.59% | +21.76% | +20.79% |
| FILATEX | +9.19% | +9.03% | +18.05% | +20.33% |
| **EMMVEE** | **+7.19%** | **+2.72%** | **+13.98%** | not labelled |

EMMVEE was the weakest of the six - the smallest range and the only one that gave
most of it back into the close. The engine still returned roughly 1.9x the stock's
own open-to-high move, the same ratio it gets on the five tuned names (1.5x-3.2x),
so it scaled to a quieter stock instead of breaking on it.

Two things worth noting on EMMVEE specifically:
- It entered the ignition leg at **09:21** (up 1.48% from the open, EMA angle +15 deg)
  and held to 09:34 for +2.59%. The live board only flagged EMMVEE at 09:29, by
  which time that leg was done - the same gap that prompted the early-promotion fix.
- The 10:37 **short** fired only because the stock was already up 6.6% on the day,
  which is the SHORTCUM rule learned from Sri's own book. It made +1.93%.

Caveat: one stock, same day, same market regime. It is evidence the rule is not
purely curve-fitted to the five, not evidence that it generalises across days.

## Order-flow and volume analysis (22-Sep)

**There is no order-flow data in this project.** `logs/tape_live`, the board logs
and the badge audits are all OHLCV. No bid/ask depth, no buy/sell quantity, no
tape with an aggressor side. So true order flow could not be tested - only proxies
rebuilt from 30s bars (`flow_lab.py`).

### What predicts the next 10 minutes (6 stocks, 3,390 bars with real range)
Baseline: 20.4% of bars are followed by a move > +0.5%, 39.2% by nothing.

| feature (quintile 1 -> 5) | runs >+0.5% | goes flat | mean next 10 min |
|---|---|---|---|
| volume vs own median | 14.3% -> **32.2%** | 47.2% -> 27.0% | +0.08 -> +0.29 |
| bar range % | 16.5% -> **31.9%** | 50.7% -> 20.5% | +0.12 -> +0.26 |
| keep-ratio | 20.4 -> **36.1%** | 47.2% -> 22.9% | +0.16 -> +0.40 |
| **CVD slope (flow proxy)** | 18.9% -> 22.1% | 41.6% -> 33.9% | +0.15 -> +0.13 |
| **close-location (aggressor proxy)** | 20.2% -> 17.9% | 41.5% -> 41.3% | +0.15 -> +0.10 |

The two order-flow proxies are **flat lines**. Close-location is mildly inverted -
bars that close on their high are slightly LESS likely to run.

### The combined gate
| gate | % of day | runs | flat | mean |
|---|---|---|---|---|
| none | 100% | 20.4% | 39.2% | +0.145 |
| volume AND keep AND range, each top 40% | 16.2% | **34.1%** | **21.5%** | **+0.340** |
| ... plus CVD slope top 40% | 8.7% | 30.4% | 19.9% | +0.243 |
| ... plus closed-on-high | 6.4% | 31.3% | 22.6% | +0.301 |

Adding either flow proxy to the gate makes it worse.

### Where Sri's 22 entries sit (percentile inside that stock's own session)
| feature | median percentile | share in top 40% |
|---|---|---|
| bar range | **92** | 82% |
| keep-ratio | 80 | 77% |
| volume | 76 | 77% |
| CVD slope | 78 | 73% |
| close-location | 76 | 68% |

The CVD and close-location numbers look high only because they correlate with
volume and range; on their own they carry no forward information (table above).

**This resolves an earlier contradiction.** Sri's longs fired at 0.70x-1.00x the
raw median volume, which is why a fixed volume multiple failed as a gate. As a
*percentile inside the stock's own day* the same entries sit at the 76th. The
normalisation was wrong, not the idea.

### Where the ENGINE's entries sit
| | volume pctile | range pctile |
|---|---|---|
| Sri | 76 | 92 |
| engine, all 119 trades | **47** | **44** |
| engine winners (57) | 52 | 65 |
| engine losers (62) | 35 | 37 |
| a random bar | 50 | 50 |

The engine picks bars no more exceptional than random, and inside its own book the
two features separate winners from losers cleanly. That is the real gap to Sri.

### But the gate does not fix it
| gate | trades | win rate | total over 6 stocks |
|---|---|---|---|
| none | 119 | 47% | **+142.36%** |
| range >= 60th pctile | 93 | 49% | +135.71% |
| volume >= 40th | 111 | 45% | +140.98% |
| volume >= 60 AND range >= 60 | 89 | 46% | +126.00% |

A gate can only delete trades; it cannot steer the engine toward better bars. It
also lands hardest on the opening-thrust entries - the single biggest trade of the
day on every stock - because in the first few bars a percentile has nothing to
rank against yet. (Those are now exempt.)

### And it fails as a tie-breaker too
Resolving slot contests in `eye_portfolio.py`:

| rule | trades | net |
|---|---|---|
| earliest signal first | 78 | **+49.58%** |
| first-come (time order) | 75 | +46.93% |
| strongest day mover | 78 | +38.56% |
| steepest EMA angle | 83 | +33.66% |
| best volume+range+keep score | 83 | +17.70% |

This is the second independent time a ranking rule has lost to "take the first one
that qualifies" - the 16-day study found the same over 28 rules. Earliness is the
edge; sophistication in the tie-break destroys it.

### Conclusion
- Order flow: untestable here, and both bar-level proxies are worthless. If this is
  worth pursuing it needs real depth/tape capture, which the board does not store.
- Volume: real signal, but only as a percentile within the stock's own session, and
  it is already largely captured by the existing entry rule.
- Flat detection: volume and range percentiles are the best flat detectors found -
  bottom quintile of either means ~50% chance the next 10 minutes go nowhere.

## 22-Sep 08:30 - dry run of the new logic on YESTERDAY'S LIVE TAPE. It loses money.

Before swapping the Board's Live Paper tab, `live_eye.py` was pointed at 21-Sep's
real feed: the pre-open universe frozen at 09:14:45 (no look-ahead), the complete
30s tape from `logs/tape_live/20260921/`, 2 shared slots of Rs 2,50,000.

    100 trades   33% wins   NET -4,367 = -4.37% on Rs 1,00,000

The same logic scored +20% to +36% per stock on the same day. The whole difference
is WHICH NAME. It never touched AGI, PROTEAN, INDO-MIM, RAYMOND or FILATEX - the
slots were taken first by BODALCHEM, FEDDERSHOL, NIMBSPROJ (-4.34% in one bar),
MANALIPETC, DPABHUSHAN. Of 574 pre-open names only 94 had tape, and of those the
logic fired on whatever moved first.

Two data facts established on the way:
- `logs/tape_live/<day>/<SYM>.json` is a COMPLETE 744-bar 30s session series and
  matches the TradingView chart bar-for-bar on price AND volume. This is the live
  feed the logic needs.
- `bars30_*.jsonl` is NOT. It is the Board's membership log: 111 bars for AGI,
  37 for EMMVEE out of 750, starting at 09:17 and 09:28 - after the move.
- Order-flow data DOES exist after all: board rows carry
  `depth={tot_buy,tot_sell,imb_pct}`. `live_eye.py` records it at every entry but
  does not use it in any decision. Earlier claim that no order flow exists: wrong.

**Conclusion: do not put this in the Live Paper tab yet.** Per-stock the entry and
exit rules are sound and now match Sri trade for trade. Selection is unsolved and
is worth more than everything else combined: -4.37% with the engine choosing vs
+80.81% on Sri's own 16 fills under the same 2-slot constraint.
