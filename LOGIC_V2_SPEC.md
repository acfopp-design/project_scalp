# LEG HARVESTER — logic spec (draft, NOT backtest-validated)
Written 21-Sep-2026 from 30 labelled trades across 9 stock-days (all 21-Sep).
NO code changed. This is a specification only.

## 0. What the labels say the trader actually does
- 30 labelled trades, 30 winners.
- Median hold **11 minutes**. Shortest 1 min, longest 36 min.
- Trades **both directions** on the same stock, same day.
- 2-7 trades per stock per day. Harvests each leg, steps aside, re-enters.
- Enters AT the base or on the first thrust — never after confirmation.
- Never trades a dead tape, regardless of how big the volume RATIO looks.

## 1. UNIVERSE  [WEAKEST PART — see section 6]
- No-dip badged names only, tradeable from badge_start (never before).
- Price >= Rs 30.
- Median 30s turnover >= Rs 2,00,000 (ABSOLUTE rupees, never a ratio).
  Rationale: 13.8x median volume on Rs 13k of turnover is still nothing.

## 2. ENTRY — the thrust bar. Valid from bar 2 of the session.
LONG:
  close > open
  AND volume >= 2.5 x median volume so far
  AND body% >= 1.0 x median 20-bar range%        (a real body, not a doji)
  AND SAR < close
  AND MACD > signal
SHORT: exact mirror (close<open, SAR>close, MACD<signal).

Explicitly NOT required:
- No minimum bar index, no warm-up. Two of his four morning entries were on
  bar 2-3, before any 12-bar indicator could compute. (Handover trap #1.)
- No RSI ceiling. His KPIGREEN entry was at RSI 82.3.
- No "gap must be positive". His KPIGREEN entry had gap -0.068 and NARROWING.
- No turnover-cluster gate. His 10:58 RATNAVEER entry had 6.5 Cr trailing;
  the 23 Cr figure that gate used only existed in hindsight.

## 3. EXIT — duration only. No oscillator. Ever.
- Track the extreme (high for long, low for short) since entry.
- EXIT when no new extreme for **STALE** bars.
- Hard stop 1.2% adverse.
- Square off 15:15.
- No entries after 14:30.

FORBIDDEN as exits (all measured, all destroy money):
- RSI crossing below its smoothing -> chopped 11-Sep into 26 trades,
  +5.67% instead of +38.36%. Same rule readmitted as "RSI vs smoothing
  snapped" on APOLLO and it cut a +5.33% trade to +1.10%.
- "EMA/MA gap stops widening" -> fires 2.5-9.5 min early on all 6 EMMVEE labels.
- Fixed VWAP band -> +1.40% on EMMVEE, +3.2-3.6% on RATNAVEER. No constant.
- SAR flip -> his own words: a warning, not an exit.

## 4. SIZE
Rs 2,50,000 per position, 2 slots, first-come-first-served by signal time.

## 5. PARAMETERS AND THEIR SOURCE
| Param | Value | Where it came from |
|---|---|---|
| VOLX | 2.5 | his ignition entries ran 2.8x / 4.1x / 17.8x |
| BODYX | 1.0 | his losers-equivalent had body 0.06-0.31 of range |
| STALE | UNRESOLVED | see below |
| STOP | 1.2% | his worst labelled excursion |
| MINTO | Rs 2L/30s bar | absolute-money lesson |

## 6. WHAT IS NOT SOLVED — read this before trusting anything above
1. **STALE has no stable value.** Tested 8/16/24/40 bars x both-sides x slots:
   16 of 16 configurations, only ONE had a positive mean, and its per-day range
   was -15% to +27%. A parameter whose sign flips with the day is not a
   parameter, it is noise.
2. **Stock selection is unsolved and it is 95% of the money.**
   Causal selector measured over 16 days: +0.65%/day. Oracle: +20.5%/day.
   28 ranking rules over 14 features were tested; nothing beat "take the first
   two that qualify" (+1.00%/day), and the two that looked better flipped sign
   between halves of the sample.
3. **Trading the whole badged universe produces 66 trades/day.** He trades
   2-7 per stock on 5 stocks he chose. Trade count vs return was monotonic
   across 522 earlier configs: <=40 trades +18.4%, >150 trades -96.6%.
   Without selection, more signals is strictly worse.
4. **All 30 labels are from ONE market day (21-Sep).** Anything fitted to them
   is fitted to one day's regime.

## 7. HONEST STATUS
Entry logic reproduces his entries well (it picked his exact bar on KPIGREEN,
within 2-3 minutes on EMMVEE and APOLLO).
Exit logic has no validated setting.
Selection is unsolved.
Therefore this spec is NOT ready to trade or to quote a return for.
