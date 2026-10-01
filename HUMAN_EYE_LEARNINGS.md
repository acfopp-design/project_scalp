# HUMAN EYE — WHAT HAS ACTUALLY BEEN MEASURED
**Written 21-Sep-2026. Everything here is measured, not assumed.**
Source labels: Sri's own take/exit calls on EMMVEE 21-Sep (4 longs, 2 shorts),
plus the 11-Sep two-stock exercise. Read this before modelling his eye again.

---

## 0. The headline
Sri's 6 labelled trades on EMMVEE 21-Sep: **6 wins, 0 losses, +Rs 20,914 =
+20.91%** on Rs 1,00,000 (Rs 2.5L per position, after Dhan charges).
The same day, the best rule-based book I could build made **+2.66%**.
The gap is ENTRY SELECTION, not exit management.

---

## 1. THE EXIT — the single biggest measured win
**Hold the leg. Do not exit on oscillator wobble.**

11-Sep, PINELABS + RAYMOND, IDENTICAL entries, only the exit rule changed:

| Exit rule | Trades | Net on Rs 1L |
|---|---|---|
| RSI crosses below its smoothing (fires on every wobble) | 26 | **+5.67%** |
| Hold the leg until it is genuinely over | 12 | **+38.36%** |

**7x the money from the same signals.** And it is not a tuned number — every
setting tested held: 3min/0.8% +38.97%, 6min/0.8% +42.55%, 10min/1.5% +46.21%,
15min/1.5% +49.49%. All eight cells positive in a tight band.

### What the exit is NOT
- **NOT RSI-below-smoothing.** That is the wobble detector. It chopped one day
  into 26 trades and destroyed 7/8 of the profit.
- **NOT "gap stops widening".** Tested on the 6 labelled trades: it fires
  2.5-9.5 minutes EARLY and cuts the 09:18 trade from +4.30% to +1.40%.
  He holds straight THROUGH gap narrowing.
- **NOT SAR flipping above.** His own words: a warning, not an exit.

### What the exit IS (EMMVEE 21-Sep, his 4 long exits)
Distance above VWAP at exit: **+1.46%, +1.40%, +1.42%** — three exits inside a
0.06% spread. The 4th long was the same trade mirrored: entered 1.68% BELOW
VWAP, exited at -0.19% (back to VWAP).
Shorts: entered -0.07% / -0.73%, covered at -0.80% / -1.11%.

**Working model: enter at or below VWAP, exit at the upper VWAP band (~+1.4%).**
Needs testing on other days/stocks — the band may scale with the stock's own
volatility rather than being a fixed 1.4%.

---

## 2. THE ENTRY — what is common to all 6 labelled trades

| Condition | Hit rate |
|---|---|
| SAR on the correct side (below for long, above for short) | **6/6** |
| EMA8/SMA12 gap WIDENING at the entry bar | **6/6** |
| MACD above the zero line (longs) | 4/4 |
| RSI above its smoothing (longs: +11.6, +0.1, +8.0, +11.9) | 4/4 |
| RSI far BELOW its smoothing (shorts: -25.7, -24.1) | 2/2 |

Matches the earlier 21-label finding: SAR below the candle was on 100% of takes.

### VOLUME IS INVERTED BETWEEN LONGS AND SHORTS — this was got wrong twice
Entry-candle volume vs 20-bar median:
- **His LONGS: 0.70x, 0.74x, 1.92x, 1.60x** — two BELOW median.
- **My LOSING longs: 3.75x, 1.56x, 2.53x, 7.54x** — all higher than his.
- **His SHORTS: 3.15x, 6.33x** — both high.

**A volume spike on a long entry candle is a WARNING (distribution), not a
confirmation.** He needs volume to SHORT, not to BUY. Do not put a
"volume >= Nx median" gate on long entries. It was added on 20-Sep on my own
reasoning and it is wrong.

---

## 3. THE SELECTOR — why those timings and not the other 13

His implied long stack (MACD>0, SAR below, RSI>=55 and above its smoothing,
gap widening) fires in **17 clusters** across EMMVEE 21-Sep. He took 4.

TWO gates separate them, and NEITHER WORKS ALONE:

### Gate 1 — real money in the window
Turnover during the signal cluster.
- Taken: 28.4, 36.3, 13.1, 13.0 Cr
- Skipped: max 11.1 Cr, nine of them under 2.9 Cr

### Gate 2 — the money went somewhere (Sri's own correction, 21-Sep)
**Keep-ratio = |net move| / range traversed.** Turnover is two-sided: 50%
buying and 50% selling gives huge turnover and zero price change. Churn burns
range and keeps none of it.
- Taken: 0.93, 0.96, 0.77, 0.71 — **all >= 0.71**
- Skipped: 0.00 - 0.65

### Why both are needed
| Gate used alone | What it wrongly takes |
|---|---|
| Turnover >= 12 Cr only | 10:11:30 — **Rs 11.1 Cr traded, price moved 0.14%**, then fell 1.63% |
| Keep-ratio >= 0.7 only | 12:31:30 / 12:36:30 — real ratio on Rs 9 LAKH of turnover, unexitable |
| **Both together** | selects his 4, rejects all 13 |

His skips were right: forward 20-bar return on the skipped clusters was
-2.21% (10:05), -1.63% (10:11:30), -0.39% (14:06:30), -0.54% (15:17).
The 11:02-13:01 dead zone had NINE qualifying clusters, all under 2.8 Cr.
He took none of them.

**This is the refined form of his own rule: "volume is a GATE ON THE STOCK,
not a trigger on the candle."** Turnover says whether you CAN trade it.
Keep-ratio says whether it is WORTH trading. Necessary and sufficient.

---

## 4. THE SHORT RULE IS NEARLY EXACT
SAR above close + RSI >= 15 below its smoothing + gap widening down +
volume >= 2x median + thrust >= 2x average range.

On EMMVEE 21-Sep this fired on **9 bars in the entire session**, and BOTH his
shorts are inside it — 10:38:30 to the bar, and 10:19:30-10:20:30 containing
his 14:20. High precision, very low frequency. Keep it.

---

## 5. MY REPEATED ENTRY MISTAKES (measured 20-Sep on 9 entries)
Losers spent **63%** of the next 20 bars below entry; winners 31%.
1. **Flat entries, no thrust.** Winners averaged 1.99x the recent candle range,
   losers 1.03x. SIKA's entry candle had a 0.08% body on a 0.21% average range.
2. **Mature gap treated as young.** "Gap just started" was coded as "gap crossed
   zero within 10 bars" and never capped the WIDTH. KIRLOSIND entered at RSI 95
   with a gap 0.92% wide, 5-11x the winners. That is a blow-off, not a young gap.
3. **Entering a spent move.** ORIENTTECH +4.71% and KIRLOSIND +4.54% from the
   open at entry. Both lost.
4. **Dead tape.** SIKA at 10:26, average range 0.21%, thrust 0.40.
5. **Volume spike at the high = distribution.** BANDHANBNK fired at 27.8x volume
   and never traded above the entry again — 100% of the next 20 bars below.

---

## 6. THE PART THAT IS STILL UNSOLVED
Selection, in real time, of WHICH stock.
- Causal selector (badge + volume gate + >=1% above open, first 2 to qualify),
  16 days: **+0.65%/day mean, 9/16 winning days**.
- Oracle (perfect daily choice, hindsight): **+20.5%/day**.
- 28 ranking rules over 14 causal features were tested. **Nothing beat "take the
  first two that qualify" (+1.00%/day)**, and the two that looked better flipped
  sign between the first and second half of the sample.
- On 11-Sep two stocks qualified in the SAME 30-second bar; PINELABS returned
  +22.14% and MOLBIO -3.47%. No feature called it.

**95% of the available money is in knowing which stock will run. None of the
measured features knows.** That is the open problem, not the exit and not the
parameters.

---

## 7. STANDING RULES FOR ANYONE MODELLING THIS
1. Never tune parameters on the day you measure.
2. Never gate entries on a minimum bar index (found and fixed 4 times).
3. Judge every rule out-of-sample. In-sample numbers in this project have been
   retracted twice (26.9%, and Gemini's 38-78%).
4. A result that flips sign when you change the selection protocol is noise.
5. Trade count is the enemy: across 522 configs, total return vs trade count was
   monotonic — <=40 trades +18.4%, 41-80 -31.7%, 81-150 -68.6%, >150 -96.6%.
6. Ask Sri. Every correction he has made to the model has been right, and two of
   them (hold-the-leg, keep-ratio) were worth more than any parameter search.
