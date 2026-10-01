# TradingView indicators seen so far

Two lists. The FIRST is what Sri actually trades on and what has been measured
against his own 30-second data. The SECOND is what has come in from videos and
TradingView links and has NOT been tested yet.

Nothing here is a recommendation. An indicator earns a place in the engine by
beating the current logic on Sri's own tape, not by looking good on a chart.

---

## A. IN THE CODE, AND MEASURED

Measured on 177 cards, 04-Sep, indicators computed causally at each card's first
sighting. "reached +2%" vs "stopped" is the split.

| # | indicator | settings | where | verdict |
|---|-----------|----------|-------|---------|
| 1 | **VWAP** | session, resets at 09:15 | sri_stack.py | **STRONGEST single separator.** above: 65% reached +2%, 5% stopped. below: 11% / 80%. Not on Sri's own chart -- found here. Hard gate in the volume and breakout entries. |
| 2 | **Volume expansion** | last 6 candles vs the 20 before | signal_sim.py | **THE entry signal.** 3.0x-32.7x at every winning entry on the five stocks read by hand. This is the primary rule. |
| 3 | **SuperTrend (Heiken Ashi)** | 10, 3 | sri_stack.py | 48% / 34%. **10,3 beats 10,2** (43% / 38%) -- the chart was right, the handoff was wrong. Used as a direction gate. |
| 4 | **Parabolic SAR** | 0.02 / 0.02 / 0.2 | sri_stack.py | direction gate: SAR must be under the candle. |
| 5 | **RSI + SMA** | 14 / 14 | sri_stack.py | Sri: "majorly i use rsi to see strength". Kept as a band (RSI_MIN/RSI_MAX), no discrimination on its own: winners median 70, losers 73. |
| 6 | **MACD (CM 12-26-9)** | 12, 26, 9 | sri_stack.py | above zero = 41% / 37%. The CROSS alone is noise; what works is "histogram expanding AND MACD rising over 3 bars". |
| 7 | **EMA 8 / MA 12 cross** | 8, 12 | sri_stack.py | **NO discrimination: 32% / 50%** against a 35% base. Sri's stated FIRST signal, and it separates nothing -- it says a move has happened, not that another is coming. Left in the stack, not used as a gate. |
| 8 | **Ichimoku** | 9-26-52-26-26 | sri_stack.py | above the cloud 44% / 35%. Weak. Not gating. |
| 9 | **HOTT / LOTT (OTT, VIDYA)** | as per Sri's Pine | Archive/code/hott_lott.py | ported from the Board card Pine. Not yet gating anything. |
| 10 | **Consolidation zone** | Sri's Pine | Opus_indicators.py | ported. The IDEA now lives in `_breakout()` -- tight band, then a break to a new day high. Worth +81,218 across 13 stocks on 04-Sep. |
| 11 | **ATR** | 20-candle average range | signal_sim.py | liveness only: the stock must move enough to clear its own charges (MIN_ATR_X x 0.067%). |

---

## B. UNTESTED — INDICATORS (a script exists, it can be coded and measured)

| # | indicator | source | what it draws | first read |
|---|---|---|---|---|
| 12 | **AlphaTrend** by KivancOzbilgic | YT short 1kWT3wyjXqw. Settings visible on screen: `AT 1 14 close`, NIFTY 15m | one trailing line under price, flips green/red, prints BUY at the flip up | a cousin of SuperTrend -- same job, money-flow instead of ATR decides the flip. The question is whether it beats SuperTrend 10,3, not whether it "works". |
| 13 | **Bollinger + RSI Double Strategy v1.1** by ChartArt | tradingview.com/v/uCV8I4xA. Pine v2, open source, FULL SOURCE CAPTURED | BUY when price crosses back UP through the lower Bollinger band (SMA 200, 2 SD) AND RSI(6) crosses above 50. Short is the mirror. Stop = the band | **Opposite of how Sri trades.** Buys weakness expecting a bounce; we buy strength breaking out. Built for 1h charts. The top comment on its own page says it misfires in a strong trend and only suits a range. |
| 14 | **Donchian Channels** | Sri, 07-Sep. Also the subject of YT short 2_y-mMRY070 ("1936 Don Chian") | highest high and lowest low of the last N candles, drawn as two lines. Break of the upper line = breakout | **The closest thing here to something we already proved.** `_breakout()` requires a tight band then a close above it AND above the day's high -- that IS a Donchian break with two extra conditions. Worth testing as a straight swap: does a plain N-bar high beat our band test? |
| 15 | **VuManChu Cipher B + Divergences** | tradingview.com/v/Msm4SjwI. Open source, updated Oct 2023, 613k views | WaveTrend oscillator with green/red dot buy-sell signals, money flow, RSI and stochastic in one pane, plus divergence marks | built and tuned on crypto (its chart is BTCUSD 2h). Many moving parts, so easy to over-fit. Test only the WaveTrend dot, not the whole panel. |
| 16 | **Scalping Swing Trading Tool R1-4** by JustUncleL | tradingview.com/v/oIGL7n3h. Open source | not one signal -- a whole workspace: EMA 89/200/633 on 15m (75/180/540 on 1m), a 10-EMA high-low price channel, fractals, pivots, HH/LH/LL/HL labels, bar colouring by the channel | **not an indicator, a methodology.** Needs two panes and hand-drawn trendlines. The one testable piece is the rule "only long above EMA200, only short below" -- that is a filter we can measure in an afternoon. |

---

## C. UNTESTED — CONCEPTS (no script; an idea to judge, then maybe code)

| # | concept | source | what it says | first read |
|---|---|---|---|---|
| 17 | **Market structure / Smart Money** | YT short P5NwuKVwv6w | higher highs + higher lows = uptrend; the break of that pattern ends it | already in the code as `BRK_NEW_DAY_HIGH`, added because MARSONS broke three LOCAL bands under its day high and paid a full stop each time. Confirms a rule we already earned. |
| 18 | **Volume + price together** | YT short vTmhQjXmhiU, "Volumes + Price is King of Technical Analysis". Frame shows a fall on red volume then a rally on rising green volume | price alone is not the signal; the volume behind it is | **this is exactly what the engine already does** and the single most valuable thing found so far: volume expanding 3x out of a quiet base, with price confirming. Nothing new to build -- but it is independent support for the main rule. |
| 19 | **Momentum stocks via Donchian** | YT short 2_y-mMRY070, "Identify Momentum Stocks Easily". Frame: "1936 Don Chian" | find momentum names by their break of an N-day high | the selection half of item 14. Relevant, because SELECTION is where the code loses to the eye (28,967 vs 94,128) -- not the entry rules. Worth a look as a morning-list ranker. |
| 20 | **Trend exhaustion** | YT short biwJo7E6Yzg | spot where a trend is running out and fade / stop trading it | related to two things ALREADY measured: the day-range cap (do not buy a stock that has already made its move) and Sri's own abnormal-candle claim (8x+ candle -> 51% chance the next is red vs a 45% base -- real, monotonic, but too small to trade after charges). |
| 21 | **5 candlestick patterns** -- IDENTIFIED 07-Sep from Sri's screenshots | YT short NeqzQqbEtFg | **1. Bullish Engulfing** -- after a downtrend, a green candle whose body completely covers the previous red one. Buy above the high of the green candle, stop below the low of the pair. **2. Bearish Engulfing** -- the mirror, after an uptrend. **3. Hammer** -- at the end of a downtrend, a small body at the top with a lower wick at least 2x the body. Buy above its high, stop below its low. **4. Shooting Star** -- the mirror at a top: small body at the bottom, long upper wick. **5. Doji** -- open and close almost equal, indecision, wait for the next candle. | ALL FIVE are reversal patterns and we are long-only momentum, so only the two bullish ones (Engulfing, Hammer) can ever fire for us -- and both are *catch the falling knife* signals, the opposite of buying a breakout. The card itself says "best used with volume confirmation and RSI/MACD divergence", i.e. the pattern alone is not the signal. Cheap to code (they are pure OHLC arithmetic, no parameters to fit) so worth measuring honestly rather than arguing about. Note the shooting-star idea is close to Sri's own abnormal-candle claim, already measured at 51% vs a 45% base -- real but too small after charges. |
| 22 | **"Swing Trading Indicator for Day Traders"** | YT short XSRuIn8oAjc | UNIDENTIFIED -- the frames captured were the talking intro, the indicator is named later in the clip | ask Sri for a screenshot of the frame where the indicator name or the TradingView search box is visible, the same way `AT 1 14` gave away AlphaTrend. |

| 23 | **"Super Perfect Intraday Strategy"** | youtube.com/watch?v=s69h54nfAdo, 4:30, Telugu, @Nikhita_Telugu_Trading | not yet read -- full video, needs frame-by-frame | UNREAD. Description links to bluechipalgos.com and a sign-up form, so expect a paid-tool pitch. Worth one pass because 4:30 is long enough to show real rules rather than a slogan. |
| 24 | **"Intraday Strategy for Options and Equity Traders"** | youtube.com/watch?v=SO4Md98KUpM, 8:35, Telugu, same channel | not yet read | UNREAD. |
| 25 | **"Best 1-Minute Scalping Strategy for Intraday Trading"** | youtube.com/watch?v=QdtqToIfNgQ, 7:35, Telugu, same channel | not yet read | UNREAD, and the most relevant of the three -- 1-minute scalping is the closest thing on this channel to what Sri actually does. Read this one first. |

### How I read a long video
Shorts I can catch in two or three frames. A 4-8 minute video needs stepping
through it, so it costs real time. The cheap way is for Sri to screenshot the
one frame where the rules or the indicator settings are on screen -- that is how
`AT 1 14` gave away AlphaTrend in seconds, and how the five candle patterns above
were identified in one message instead of twenty screenshots.

---

## How anything in B or C gets into A
Implement it, then run it on 04-Sep as one book of Rs 1,00,000 at 5x,
forward-only, honest universe. Judge against two numbers:

    current logic   Rs 28,967   (09:15-11:00, 04-Sep)
    human eye       Rs 94,128   (same window, same book, hindsight ceiling)

and it must not damage the 13 single-stock studies, which total Rs 3,17,670.

PRIORITY ORDER, on what the gap actually is:
  1. Donchian (14/19)  -- selection is the whole problem, and this is a
     selection tool we already half-use.
  2. AlphaTrend (12)   -- straight swap against SuperTrend 10,3, one afternoon.
  3. EMA200 filter from JustUncleL (16) -- one line, easy to measure.
  4. VuManChu WaveTrend dot only (15).
  5. ChartArt BB+RSI (13) -- expected to lose; run it to settle the argument.

All 6 shorts are @Nikhita_Telugu_Trading, in Telugu, and each one ends in a
promo for a paid workshop or an algo tool. Treat the teaching as a pointer to
an idea, never as evidence -- the evidence is Sri's own tape.


---

## D. BLUECHIPALGOS (BCA) — checked 07-Sep, bluechipalgos.com/indicators

Sri was right to push: the channel's sponsor DOES publish free indicators, and
I had written them off without opening the site. Six free, three premium, two
third-party picks. Only three of the free ones are for Sri's segment at all --
he trades EQUITY intraday, long only, so every straddle/strangle/option-chain
tool is irrelevant no matter how good it is.

| # | indicator | BCA type | relevance to Sri |
|---|---|---|---|
| 26 | **ORB indicator with trailing Stop-loss** | free | **THE ONE TO TEST.** Opening Range Breakout: mark the high and low of the first N minutes, buy the break of the high, trail the stop. It lands exactly where the engine is weakest -- 09:15 to 09:30, the window we spent Friday fixing -- and it is a SELECTION-AND-ENTRY rule, which is where the code loses to the eye (28,967 vs 94,128). Also close to what `_breakout()` already does, so it is a fair head-to-head. |
| 27 | **Candlestick Pattern Condition Builder** | free | build your own candle pattern and plot the signal. This is the tooling for item 21 (Engulfing / Hammer / Shooting Star / Doji). Useful for LOOKING; we would code the patterns ourselves anyway. |
| 28 | **Trade Wave (Reversal Identifier)** | free | "detects sharp reversals with magic bands". Reversal-hunting is the opposite of buying strength, same objection as ChartArt's BB+RSI. Low priority, and "magic bands" with no published method means we cannot verify what it does. |
| 29 | Rolling Straddle & Strangle Chart | free | options only -- NOT Sri's segment. |
| 30 | Fixed Straddle & Strangle Chart | free | options only -- NOT Sri's segment. |
| 31 | Equity Option Chain (live greeks, IV) | free | options only -- NOT Sri's segment. |
| 32 | BCA Index Predictor | **premium** | "accurate market predictions" for sectors and the index. Closed method, paid. Cannot be verified, so cannot be tested honestly. |
| 33 | Strategy Builder with Backtesting | **premium** | a backtester. We already have one that is stricter than most -- forward-only, no look-ahead, real charges, and a human benchmark to beat. |
| 34 | BitSwinger | **premium** | crypto. Not applicable. |
| 35 | Candlestick Pattern Identifier | third-party pick | names every standard candle type. Same ground as item 21. |
| 36 | Trend Lines | third-party pick | auto trendlines. |

### The honest read
ORB (26) is the only one of the eleven that could move Sri's number, and it is
worth a proper test. Everything else is either the wrong segment (options,
crypto), a reversal tool aimed against his style, a paid black box we cannot
verify, or something we already have.

BCA's own FAQ says none of their tools ship with ready-made strategies -- the
trader configures everything. So the videos point at TOOLS, not at an edge. The
edge still has to come from Sri's own tape.

### Still to do
The three long videos (23-25) have not been read frame by frame, so the
indicators they actually put on the chart are still unknown -- and until they are
named there is nothing to look up in TradingView's library. AlphaTrend was
identified only because the settings `AT 1 14` were visible in one frame.

Last updated 07-Sep 09:25 IST.

---

## E. TESTED 07-Sep EVENING — the combination runs

All on 07-Sep, TOP 10 BULLISH (hindsight list), long only, 09:16 to close,
Rs 1,00,000 at 5x, -1% stop / 1.2% trail. "1 position" = all-in one at a time.

| strategy | 1 position | 3 positions | 03-Sep | 04-Sep |
|---|---:|---:|---:|---:|
| **HOTT/LOTT + Pullback ALT** | **110,017** | 73,591 | 50,804 | **-28,215** |
| Pullback ALT alone | 92,481 | 67,132 | | |
| Cipher B, big green dot (wt2<=-53) | 80,174 | 48,136 | | |
| Chandelier 22/3 | — | 79,986 | | |
| Price Action v0.3 alone | 53,459 | 80,422 | | |
| HOTT/LOTT + Price Action v0.3 | 52,113 | 61,795 | | |
| Cipher B, small green dot | 56,414 | 44,772 | | |
| HOTT/LOTT alone | 32,233 | 60,742 | | |
| human eye (to 10:30) | — | 105,733 | | |

### What the numbers say
* HOTT/LOTT + Pullback ALT is the best single-day figure ever produced here --
  Rs 1,10,017, above the human eye's 1,05,733 -- carried by ONE trade:
  PASHUPATI 09:18 -> 09:23, +15.12%, Rs 75,375.
* It then makes 50,804 on 03-Sep and **loses 28,215 on 04-Sep**. Same pattern as
  everything else: one glorious day, one bad one.
* HOTT/LOTT's FLAT ZONE does real work: adding it to Pullback ALT lifts the day
  from 92,481 to 1,10,017 at one position. Doing nothing in the flat zone is the
  contribution, which is the same "stay out" property that made the volume rule
  the only survivor of the four-day test.
* Cipher B's OVERSOLD condition matters: the big green dot (65 signals) beats the
  small dot (194 signals), 80,174 vs 56,414.

### NOT VERIFIED AGAINST SOURCE
TradingView would not render the Source code panel in the browser pane and
WebFetch is blocked on those pages, so Cipher B, Pullback ALT and Price Action
v0.3 are implementations of the AUTHORS' PUBLISHED DESCRIPTIONS, not line-by-line
ports. HOTT/LOTT is ported from Archive/code/hott_lott.py (VAR/VIDYA + the OTT
ratchet, written from the published algorithm) but fed true bar highs/lows here.
AlphaTrend is the one indicator whose real Pine was read and whose signal was
found to be wrong -- so treat these three as indicative until the same is done
for them. Sri should sanity-check the signal times against his own chart.

Last updated 07-Sep 20:30 IST.
