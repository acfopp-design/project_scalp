
## 02-Sep-2026 09:23 IST -- Sri: "TBZ closed and not opening again"

CONFIRMED, and it is a defect rather than a tuning choice.

  TBZ entered 09:16:15 @ 383.75, exited 09:17:42 @ 391.20 (+1.94%, Rs 3,126).
  It has been carded CONTINUOUSLY since 09:15:22 -- 32 cards, urgency rising
  15.1 -> 22.7 -> 21.4, from_open +9.38%. Price reached 416.00 at 09:22:30.
  That is +8.40% from our own entry and +6.34% since we sold.

THE CAUSE (live_paper.tick, the "one card = one signal" dedup):

    was = _S["seen"].get(sym); _S["seen"][sym] = now
    if (was is not None and now - was < REENTRY_COOLDOWN
            and (sym in _S["taken"] or sym in held)):
        continue

  `seen` is refreshed on EVERY tick a card is on screen, so for a stock that
  stays carded, `now - was` is always ~3 seconds and the 15-minute re-entry
  clock NEVER STARTS. A continuously-carded stock can therefore never be
  re-entered at all -- the cooldown is infinite, not 900s. Proof: the state
  file shows skipped.cooldown = 0, i.e. TBZ never even reached the cooldown
  test; it was suppressed one line earlier.

  This is the SAME class of bug as "crowded out = locked out", fixed on 01-Sep
  for stocks that had never traded and left in place for ones that had.

COST TODAY, on this one stock: the position was Rs 1,66,667, so the unre-entered
move is ~Rs 10,600. At full capital (Rs 5,00,000, one name) the 383.75 -> 416.00
run is ~Rs 42,000. Sri's estimate of "30k easily" is the right order.

NOT FIXED DURING THE SESSION -- changing entry logic with positions open would
make today's result uninterpretable. Fix after 10:30, then re-run replay_live
over 02-Sep to measure what it would have been worth.

## 02-Sep-2026 10:00 IST -- Sri: "you are restricting 3 at a time; TBZ has much
## better momentum than BODALCHEM, why bet the same on both?"

MEASURED, 84 cards over 3 sessions. He is right that flat sizing is wrong:

    urgency at card    n   avg best   avg worst   reached +3%
      12-13            9     +1.11%     -0.31%         0%
      13-16           36     +1.90%     -1.12%        19%
      16-20           24     +1.47%     -1.23%        12%
      20+             15     +2.66%     -1.44%        26%

  A 20+ card is worth 2.4x a 12-13 card. Sizing them identically throws that
  away.

THE TWIST, and it changes the fix:

    TBZ        urgency at card 15.1  ->  went +10.26%
    BODALCHEM  urgency at card 15.5  ->  went  +2.86%

  At the moment of the card they were indistinguishable -- BODALCHEM scored
  HIGHER. So "bet more on TBZ" is not decidable at entry. What separated them
  is that TBZ kept running AFTER we bought it.

  => Size at entry by conviction (supported), but the big money has to come
     from ADDING to what is already working, decided by the tape, not by a
     guess at 09:15.

PLANNED (not yet built):
  1. Conviction base size -- urgency 20+ takes a larger tranche than 12-15.
  2. Pyramid -- a position at +1.5% that is still making new highs gets a
     second tranche. This is how the book ends up large in TBZ and small in
     BODALCHEM without predicting it.
  3. More slots, smaller base -- 6 x Rs 80k rather than 3 x Rs 1.67L. The
     Rs 5,00,000 of buying power is the real constraint; "3" was arbitrary.

CEILING, honestly stated: the Rs 2,88,000 "perfect" figure assumes buying 22
exact lows at once and is not reachable. The true 3-slot ceiling on 02-Sep was
Rs 67,552 (67%). More slots raises it. 20% is not 80% short of the possible --
it is short of a number nobody can trade.

## Fixes shipped 01-Sep / 02-Sep, in order

  01-Sep  participation-based liquidity (replaced a flat 5,000 shares/min that
          demanded Rs 52.5 lakh/min of a Rs 1,050 stock and Rs 2 lakh of a Rs 40
          one). Recovered KALYANIFRG, SAKAR.
  01-Sep  liquidity on a rolling 5-min window, not the session average -- a
          stock that wakes at noon could never qualify before.
  01-Sep  Movers_master.py: the scrip master was 73 days stale, so LALITHAA and
          365 other listings were invisible. Now refreshed daily, validated
          before it replaces anything.
  01-Sep  pre-open STRONG GAP-UP confirmations ranked first.
  01-Sep  quote throttle measured end-to-start + one 429 retry; a 429 used to
          silently drop ~1,000 stocks from a sweep.
  01-Sep  entry rules derived from 4 sessions, and TWO of my own one-day rules
          WITHDRAWN after an out-of-sample test (in-sample Rs 737/trade ->
          out-of-sample Rs 86).
  02-Sep  re-entry clock runs from the EXIT. It ran from "card last seen", so a
          continuously-carded stock could never be re-entered -- TBZ, caught by
          Sri live.
  02-Sep  target +3% -> +2%. Average best excursion after a card is +1.95%; a
          +3% target reached past where these moves actually go.
  02-Sep  Board feeds the Lab: a stock already on the Board needs only +1% from
          open, not +2%. COALINDIA ran +1.91% and was never examined.
  02-Sep  DISPLACEMENT OFF in live. It was validated on replay_live, which ticks
          every ~13s; live ticks every 3s. Same rule, 4x the firing rate: the
          whole book rotated the instant the 180s dwell expired, into names that
          stopped out within a minute. Every realised loss in that run was a
          displacement or a post-displacement stop.

## STANDING RULE, learned the hard way three times in two days
  Never validate on replay_live and ship to live without first making the
  replay tick at the live rate. They are not the same system.

## 02-Sep 10:25 -- ATR-scaled stops, and a measurement bug I made and caught

  Flat -1% is not the same risk on every stock. Measured over 3 sessions:
      ATR(14) on 30s bars    a -1% stop equals    hit -1% first
        0.15-0.30%                5.3 ATR              0%
        0.30-0.50%                3.0 ATR             66%
        over 0.50%                1.4 ATR             63%

  TBZ on 01-Sep: ATR 1.34%, dipped -1.37%, stopped -- then ran +10.28%.

  FIRST RESULT WAS WRONG. 2.0xATR appeared to pay Rs 39,527 vs Rs 27,315 flat.
  replay_live was computing ATR close-to-close because by_sym only stored
  closes; true range needs high/low. Fixed, and the same test then gave
  2.0x = Rs 11,651 with one session at -Rs 10,704. The apparent win was my own
  measurement error, in the same family as validating displacement on a slower
  clock.

  SHIPPED: stop = -min(2.5%, max(1.0%, 1.5 x ATR%)). One-sided by design -- it
  can never be tighter than the flat stop. Rs 28,738 vs Rs 27,315 over three
  sessions, and the whole gain is in one of them. Kept for bounded downside,
  not because it is proven.

## 02-Sep 10:20 IST -- SRI'S STANDING INSTRUCTION (read this before assuming anything)

  "I stopped trading and will not be able to trade till your live trading
   reaches minimum 30% profit... So, stop thinking I am trading till 10:30AM.
   Do all the things now when market is open so that everything can be tested,
   all fixes you make can be tested."

WHAT THIS CHANGES, operationally:
  * He is NOT trading. There is no live session to protect. The "do not change
    code mid-session" rule I invented is VOID -- it was costing whole days of
    work for a run nobody was taking.
  * Market hours are now the TEST BED, not a no-touch window. Fixes should be
    made and exercised against the live tape while it is running.
  * The 09:15-10:30 boundary no longer gates anything. Work all day.
  * His gate for resuming: live paper reaching ~30%. He believes it is
    achievable because the "perfect" column on 02-Sep showed ~100%.

WHAT I OWE HIM IN RETURN -- the honest counterweight, restated so it is not
lost between sessions:
  * The Rs 2,88,000 "perfect" figure buys the exact low of 22 stocks at once on
    a 3-slot book. It is a ceiling, not a target. The honest 3-slot ceiling on
    02-Sep was Rs 67,552 (67%), and that still assumes buying exact lows.
  * 30% in one morning is reachable on a good day (28-Aug replayed at 18.8%).
    30% SUSTAINED daily is not, and I will not tune toward it by raising
    leverage, widening targets or adding trades -- that manufactures variance,
    not edge.
  * So the target I will actually work to: a median session clearly positive
    after slippage, with the good days reaching 15-30%. If that arrives, the
    30% days come with it.

ALSO LOGGED, 02-Sep 10:15 -- A BUG I INTRODUCED AND MUST NOT REPEAT:
  replay_live imports live_paper and calls reset()/start()/tick(), each of which
  _persist()s -- to the SAME livepaper_YYYYMMDD.json the live engine writes.
  Every back-test I ran this morning overwrote the running session's saved
  state. In-memory was safe (separate process) but a board restart would have
  restored MY replay's positions as real trades. Fixed: live_paper.OFFLINE, set
  by replay_live at import, redirects to livepaper_replay_YYYYMMDD.json.

  That is the FOURTH time in two days the replay and the live engine sharing
  something they should not has caused a fault:
    1. displacement validated at a 13s tick, shipped to a 3s engine
    2. ATR measured close-to-close in replay, meaning something else live
    3. rules fitted on replay data that live had never generated
    4. this -- a shared state file
  STANDING RULE: before trusting any replay result, ask what the replay does
  differently from the live engine. It is never nothing.

## 02-Sep 10:30 -- conviction sizing vs pyramiding: Sri half right, and the
## half he was wrong about is the interesting one

  Tested over 28-Aug / 01-Sep / 02-Sep, 09:15-10:30, Rs 1L @5x:

    3 units, flat, no pyramid   (previous)   Rs 28,738   worst day +3,859
    6 units, flat, no pyramid                Rs 30,151   worst day +7,199
    6 units, CONVICTION, no pyramid          Rs 13,779   worst day -4,440
    6 units, flat, PYRAMID                   Rs 36,367   worst day +6,827   <- shipped
    6 units, conviction + pyramid            Rs 25,107   worst day +3,881
    8 units, conviction + pyramid            Rs 31,123   worst day +9,709

  PYRAMIDING WORKS: +Rs 6,216 over flat sizing, and every session positive.
  CONVICTION SIZING AT ENTRY HURTS: it cost money in every pairing it appears
  in, and produced the only negative session in the table.

  That is exactly what the TBZ / BODALCHEM pair predicted. Urgency ranks a
  POPULATION (20+ cards average +2.66% vs +1.11% for 12-13) but cannot separate
  two individual stocks -- BODALCHEM scored HIGHER than TBZ at the card and went
  nowhere. Betting more on the higher score is therefore betting on noise.
  Adding to what is already running uses the tape instead of a prediction.

  SHIPPED: 6 units of Rs 83,333, flat at entry, add one unit when a position is
  +1.2% and has made a new high in the last 45s, max 3 units in any one name.

## 02-Sep 11:00 -- "why is that fixed to 6? why can't that be dynamic?"

  He was right and "6" was indefensible: it won a 3-vs-6-vs-8 test and I froze
  the winner. Worse, it made position size independent of how risky the position
  was -- two names at the same rupee value but stops of -1% and -2.5% were
  risking 2.5x different amounts and treated as identical.

  REPLACED WITH RISK PARITY: position value = risk budget / its own stop
  distance. Same rupee risk on every trade; a wide-ATR stock gets a smaller
  position. The NUMBER of concurrent positions is then whatever the total risk
  budget allows -- 2 cards on the tab gives 2 positions, 12 cards gives 6 --
  and is never a number I chose.

    fixed 6 units             Rs 35,814   worst day +6,827
    risk 1.5%/trade, 8% book  Rs 31,119   worst day +8,528   <- shipped

  Fixed-6 tests HIGHER by Rs 4,695, all of it in one session. Shipped the more
  defensible system and recorded the cost, because three sessions cannot
  separate them and a constant I chose by hand is not a design.

## 02-Sep 11:05 -- Sri: "opening entry itself is a loss... you are taking the
## trade when the stock went to peak and started dipping"

  MEASURABLE, AND THE CONCLUSION INVERTS. Where the entry sits in the stock's
  last 5 minutes (0 = bottom of the range, 100 = buying the very top), 31 cards:

    entry sits at        n   avg best  avg worst  reached +2%
      0-50 (a dip)       3     +0.79%    -1.67%        0%
      50-75 (mid)       10     +1.95%    -1.33%       20%
      75-90 (high)       4     +3.25%    -2.23%       25%
      90-100 (THE TOP)  14     +2.19%    -1.54%       28%

  He is right that we buy the top: 45% of entries are in the top 10% of the
  5-minute range. He is wrong that this is the problem -- buying the top is the
  BEST bucket, and dip entries reached +2% exactly zero times. This is momentum:
  a dip in this population is a failing stock, not an opportunity.

  TESTED THE FIX HE IMPLIED -- a limit order below the card price:
    market at the card    110/110 filled   avg +1.57%   20% hit +2%   32% hit -1%
    limit -0.4%            61/110          avg +1.64%   26%           39%
    limit -1.0%            29/110          avg +1.87%   27%           41%
  Slightly better per fill, but a THIRD of the trades and a WORSE stop rate --
  a stock that pulls back 1% tends to keep going. Not shipped.

  TESTED WIDER STOPS for top-of-range entries (they average -1.54% adverse):
    +2.0%/-1.0%  Rs 31,119   <- current, best
    +2.0%/-1.5%  Rs 16,196
    +2.5%/-1.5%  Rs 26,332
    +3.0%/-2.0%  Rs  8,338
  Also not shipped. The current pair is already the optimum of the grid.

  CONCLUSION: entry timing and exit levels are at a local optimum. The money
  left on the table is NOT in the rules -- it is in COVERAGE. On 02-Sep, 17
  stocks that ran 3%+ were never carded (Rs 107,070 of ceiling) and 6 more were
  carded but never bought (Rs 54,096). That is where the next work goes.

## 02-Sep 11:15 -- COVERAGE: where the missing money actually is

  41 stocks ran 3%+ inside 09:15-10:30 across three sessions and were NEVER
  carded. Replaying the LAB's own gates against their tape to find which gate
  refused each one:

    reason                                    n    ceiling
    ------------------------------------------------------------
    passed price + burst, refused later      20   Rs 2,14,943   liquidity / TOP_N
    NEVER +2% from open (the blind spot)     16   Rs 1,24,623
    not in the scrip master at all            5   Rs   62,570
    never burst 1.0% in 90s                   7   -

  THE BLIND SPOT IS STRUCTURAL, not a threshold that needs nudging:
      TEMPSENS   tape offered +8.0%   never exceeded  0.00% from open
      DCI        tape offered +8.7%   never exceeded +0.13% from open
      PINELABS   tape offered +4.5%   never exceeded -0.38% from open
  These opened, sold off, and rallied hard off an intraday low. A rule that
  admits only stocks +2% ABOVE THE OPEN cannot see any of it, however the other
  gates are tuned.

  SHIPPED TO THE LAB ONLY (superstocks.py untouched): a SECOND admission path.
  A stock +MIN_FROM_LOW (3%) off its own low of the day qualifies even while
  still under its open, provided it is not more than MIN_FROM_LOW_ABS (-6%)
  below the open. Every other gate -- burst, participation, stall, fade,
  circuit -- is unchanged, so this widens what is LOOKED AT without loosening
  what is accepted. Unit-tested: TEMPSENS (at its open, +8.3% off the low) is
  admitted; a stock +3.4% off its low but -8% from open is still refused.

  HONEST LIMIT: the Rs 1,24,623 is a CEILING on those stocks' tape, not a
  forecast. It cannot be replayed end-to-end, because replay_live reads the
  recorded super log and that log was written by the OLD gate -- the new path
  can only be measured from tomorrow's live session onward. Falling knives live
  in exactly this space; the stall/fade test is what is expected to keep them
  off the tab, and that expectation is untested.

  STILL OPEN: 5 stocks (ANNU, BHAGERIA, BIRLACABLE, HTEL, SUMAX) are not in the
  refreshed scrip master at all -- Rs 62,570. The master is current as of today,
  so these are either not NSE EQ series or listed under another symbol. Worth
  one look.

## 02-Sep 14:20 -- THE CONJUNCTION PROBLEM (the real reason runners are missed)

  Of 41 stocks that ran 3%+ and were never carded, 20 were in the universe,
  cleared +2% from open, and passed the liquidity participation test. Fifteen of
  those eighteen I could reconstruct passed EVERY gate:
      MVELECTRO  Rs 1,989 lakh/min   0.1% participation   ran +10.8%
      COALINDIA  Rs 1,132 lakh/min   0.1%                 ran  +3.4%
      SKYWAYS    Rs   879 lakh/min   0.2%                 ran  +7.5%
  and still never appeared.

  WHY: every gate is evaluated on the SAME 10-second scan pass. A stock must be
  above its open AND bursting AND not stalled AND liquid at one instant.
  Replaying bar by bar:

      stock              bars   +2% open   bursting   BOTH AT ONCE
      BODALCHEM (02-Sep)   86       73        19          17
      SKYWAYS   (01-Sep)   60       51        16          15
      COALINDIA (02-Sep)  123        1         2           0
      SAGILITY  (02-Sep)  121        0         0           0

  Two different failures hiding under one symptom:
   (a) BODALCHEM qualified on 15 separate bars and was still never carded --
       a defect, since something downstream refused it every time.
   (b) COALINDIA and SAGILITY never satisfy burst-and-above-open together at
       all. That is the design being strict, not broken.

  SHIPPED TO THE LAB: BURST MEMORY. A burst is an EVENT, not a state. Once a
  stock prints a qualifying 90-second move, that is remembered for 150s so the
  other gates can catch up with it rather than having to coincide to the second.
  Stall and fade are untouched, so anything that has actually stopped is still
  removed.

  MEASURED on real tape, qualifying 30-second windows:
      strict (same pass)  40
      with 150s memory    89     (2.2x)
      BODALCHEM 02-Sep    15 -> 31      LALITHAA 01-Sep    4 -> 14
  Each extra window is a CHANCE for the remaining gates to admit the stock. It
  does not guarantee a card, and none of this can be replayed end-to-end because
  the recorded super log was written by the old gate.

## 02-Sep 14:30 -- rules_lab and replay_live DISAGREE, and replay wins

  With 5 sessions on disk, rules_lab produced its strongest signal yet:

      from_open at the card    n    net/trade   win%   hit target
        0-3%                  45      Rs     4    37%       6%
        3-4%                  15      Rs 2,296    73%      46%
        4-6%                  20      Rs   120    45%      10%

  A 45-trade bucket at dead flat, next to a 15-trade bucket at Rs 2,296. The
  obvious move is to raise MIN_FROM_OPEN from 2.0 to 3.0 and stop taking the
  flat ones.

  TESTED IT IN THE FULL ENGINE. It is wrong:

      MIN_FROM_OPEN   TOTAL       worst day   trades
        2.0 (current) Rs 31,119     +8,528       96
        2.5           Rs 24,339     +6,761       85
        3.0           Rs 21,995     +1,423       75
        4.0           Rs 24,525     +4,025       62

  WHY THEY DISAGREE, and this is the part worth remembering:
    rules_lab scores each signal ALONE, at a fixed position size, one trade at
    a time. replay_live runs the portfolio -- risk-based sizing, pyramiding,
    slot competition, re-entry. A 0-3% card that scores Rs 4 in isolation is
    often the position that later gets pyramided into a winner, or the one that
    holds a slot cheaply while a better card is still forming. Removing them
    removes that, and the portfolio loses more than the per-trade table gains.

  RULE: a per-trade statistic is a HYPOTHESIS. Nothing ships until the full
  engine confirms it. This is the second time today a strong-looking per-trade
  finding failed portfolio simulation (the other was the pullback-limit entry).
  MIN_FROM_OPEN stays at 2.0.

## 02-Sep 19:15 -- THE 5 "MISSING" STOCKS: resolved, and 4 of 5 are not bugs

Coverage analysis on 02-Sep flagged 5 runners (Rs 62,500 of theoretical profit)
as absent from the scrip master. After the master refresh (2,455 -> 2,673 NSE EQ),
the real position is:

  BHAGERIA    NSE 13427   present   series BE  -- NOT a gap
  BIRLACABLE  NSE 6815    present   series BE  -- NOT a gap
  HTEL        NSE 765468  present   series BE  -- NOT a gap
  ANNU        --          absent    closest is ANNAPURNA (NSE 764471, EQ)
  SUMAX       --          absent    no NSE row at all; BSE-only listing

Series BE is NSE's restricted / surveillance book: 100% upfront margin, no
intraday leverage, often trade-to-trade delivery. We cannot scalp them at 5x,
so excluding them is correct behaviour, not a coverage bug. Rs 37,500 of that
Rs 62,500 was never ours to make. The universe filter is right; my earlier
write-up was wrong to count it as loss.

ANNU is probably Annapurna Swadisht under a feed-side ticker the master spells
ANNAPURNA -- a NAMING mismatch, not a coverage gap, and the only one of the five
worth fixing. Left unfixed deliberately: adding a hand-written alias for one
stock is exactly the kind of single-observation change that has burned this
project twice. If ANNU appears again on a live board, the alias goes in.

SUMAX is not on NSE. Nothing to fix.

ACTION: none. The scrip master is doing its job.

## 02-Sep 19:30 -- CAN BURST MEMORY BE VALIDATED OFFLINE? No. And why not.

Burst memory and the from-low path have sat unproven in superstocks_lab.py for
two days because replay_live cannot test them: it replays super_*.jsonl, which
is a record of what the SHIPPED gates decided to card. Change the carding rule
and different stocks get carded -- and those cards are not in the file.

Built card_lab.py to close that hole: re-run the gates over bars30_*.jsonl (the
raw 30-second tape) and synthesise a super log a looser rule would have written.
The premise was that bars30 is a much wider net -- 329-419 symbols a day against
42-85 cards.

THE PREMISE WAS WRONG, and the calibration caught it.

  Recall of the real cards, synthetic SHIPPED gates:   25%
  Invented cards over 21 sessions:                     516
  Real cards actually PRESENT on the tape:             59% (42-88% by day)

Two independent failures:

  1. bars30 is written by Movers_ticks, fed by TICKS.track() (the board's
     universe) and TICKS.add() (cards already shown). The tape is DOWNSTREAM of
     the decision under test. A looser rule exists to find what the board missed;
     by construction those stocks are absent. 329-419 symbols is a wide set but
     a DIFFERENT set, not a superset. On 02-Sep, 42 of 85 real cards were not on
     the tape at all.

  2. My rebuild implements about five gates. superstocks_lab has roughly twenty
     -- participation liquidity, rise percentile, sustained shares/min, stall,
     fade, dwell, circuit proximity. A rebuild that loose measures the rebuild.

The symptom worth remembering: forced to run anyway, it reports burst memory
carding stocks up to 21,146s earlier -- nearly six hours, i.e. before the open.
That is a broken instrument flattering itself, and it is exactly what an
offline result is worth with no calibration gate in front of it. This is the
THIRD time this week a plausible number came from an unchecked instrument (the
pullback-limit entry and the from_open 3-4% bucket were the others). The
difference is that this time the check was built before the number, so it cost
an evening instead of a trading session.

card_lab.py is kept, with the verdict at the top of the file, so nobody
rediscovers this.

WHAT WOULD MAKE IT WORK
  (a) log bars for the scanner's eligible universe, not just board members, so
      the tape becomes a genuine superset -- a change to Movers_ticks, the file
      that produces irreplaceable data, so it is made with Sri awake, not
      unattended at 19:30 on a hunch;
  (b) import the real gate stack from superstocks_lab rather than restating it.

UNTIL THEN: burst memory and the from-low path can only be validated live.
They stay in superstocks_lab.py, unshipped, and are still unproven. Nothing
about them should be described as tested.

## 02-Sep 21:20 -- FIXES FOR THE 03/04-SEP AUTONOMOUS RUN

Sri: run the same loop unattended on 03 and 04 Sep, 09:15-15:30, learning every
10 minutes. Two days only, and only while his machine is on.

The blocker was never the trading rules. It was that on 02-Sep the board died
at 11:15 with six positions open and I could not restart it. So that came first.

### 1. WHAT ACTUALLY KILLED THE BOARD (I had this wrong all afternoon)
The log's last line was a normal cycle at 11:15:26. But five minutes earlier:

  [11:15:12] super_monitor [CRITICAL] scan() has not run for 240s -- last pass
             11:10:16. The Super Stocks thread is dead or stuck.

The thread that feeds EVERY trade had been silent since 11:10 while the app log
kept scrolling happily. So "the board died at 11:15" was wrong twice over: the
trading brain went blind at 11:10, and process-alive was never the right health
check. A monitor that logs CRITICAL and takes no action is not a monitor.

superstocks_loop was already wrapped in while True/try/except, so nothing was
raising. It was CRAWLING. Every outbound call has a 15-25s timeout and the loop
enriches each qualifier into a full card, so a sick upstream multiplies.

### 2. THE MECHANISM, which is worse than it looks
_QLOCK in Opus_quotes_v3 serialises every quote call across every thread --
correct, because the throttle must be global. But the lock is held for the whole
request. So a 25-second timeout is not a 25s delay to one caller, it is a 25s
stall of the entire board. One sick endpoint froze everything.

FIXED:
  - quote timeout 25s -> 8s. A quote that normally answers in under a second
    does not need 25 seconds to prove it is broken.
  - 502/503/504 now get one retry, as 429 already did. Previously a single
    gateway blip silently dropped a batch of up to 1000 stocks from the sweep.
  - 5xx counted and surfaced in the rate log; a warning at >=10/min.
  - SUPER_ENRICH_BUDGET = 12s on both enrich loops. When spent, publish what we
    have and go round again, and SAY how many were skipped. A partial board that
    is CURRENT beats a complete board four minutes old -- for a scalper the
    stale one is not less useful, it is wrong.

### 3. SUPERVISOR.py -- because I could not restart anything
There is NO network route from my shell (a Linux VM) to Windows: no default
gateway, 127.0.0.1:5005 unreachable, every host alias shut. Chrome is the only
network path and it needs Chrome open AND the process alive.

So the supervisor runs on Windows and is commanded through FILES, which I can
write into the mounted folder:
  logs/control/<action>.cmd   restart | stop | start | ping
  logs/control/status.json    rewritten every 5s
It restarts on a dead process, on a stale app log, and -- this is 02-Sep's
actual failure -- on a SUPER log that has stopped growing during market hours.
Backoff, a per-hour cap of 8, and it never restarts on staleness outside 09:10-
15:35, when the loops idle by design.

BUG FOUND IN TESTING, and it would have been ugly: the .cmd file was not being
removed, so a restart command would re-execute on every 5s poll -- an infinite
restart loop, strictly worse than the dead board it exists to fix. The mount
refuses delete. Now it MOVES the file into done/ (rename succeeds where delete
does not) with an in-memory seen-set as a backstop. Verified: five polls of two
undeleteable commands produced exactly two restarts, then an empty inbox.

### 4. learn.py -- the ten-minute review
Every trade against what the tape actually offered, sorted into the kind of
mistake, because "we should have made more" is true every time and points at
nothing: ENTRY (bought and it fell) / EXIT (sold and it ran) / STOP (stopped
then recovered) / CAPACITY (ran while carded, never bought).

Caught a false signal in its own first run: it reported 8 CAPACITY misses on
02-Sep from cards at 09:15-09:31, against a session that did not start until
10:25. A missed opportunity is only a capacity failure if we were in a position
to take it. Now bounded to the window where the engine was actually trading --
without which tomorrow's first act would have been tuning slot counts for a
problem that does not exist.

Re-run on the real 39-minute session: ENTRY 1, EXIT 2, STOP 0, CAPACITY 0.
Verdict "anecdote, change nothing", which is the correct answer for 39 minutes
and exactly the discipline the loop needs.

### 5. ENTRY_TO drift, found while verifying
live_config.json said 15:00; live_paper.py said 13:00. The JSON overrides the
module at boot, so the board would have run an entry window no test had ever
used. Set to 13:00 -- 13:00-15:30 is the only window that loses money across
every session measured.

### STILL TRUE, and unchanged by any of this
Burst memory and the from-low path remain UNPROVEN (see the 19:30 entry).
The best confirmed full-session result is +3.7%. The target is 30%.

## 02-Sep 21:30 -- THE CLOUD SCHEDULE CANNOT REACH THIS MACHINE. Loop moved.

Plan was a scheduled cloud task firing hourly on 03/04-Sep, each firing running
six ten-minute passes. Built it, bounded it to those two days, wrote the whole
prompt. It came back:

    not bound: no_signed_approval -- this task will run in the cloud only
    folders: not attached -- the task is not bound to the user's computer

Tried again with C:\Project_Scalp listed explicitly, which is documented to put
the folder on an approval card. Same refusal, and no card ever reached Sri.
The binding needs a signature only the desktop app can produce, and this session
reaches his machine remotely, so there is no signing path from here. A binding
also cannot be added after creation, so the task could never work. Deleted both
rather than leave a schedule that fires seven times a day only to discover it
cannot see a single file. My own standing rule applies: say it once, stop
retrying, route around it.

THE FIX, and it is better than what it replaces:
learn.py is a local script reading local logs, and SUPERVISOR.py is already
running on the machine that has them. So the review now runs THERE, every ten
minutes during market hours, writing logs/LEARN_YYYYMMDD.md and echoing the P&L
line and the verdict into logs/supervisor.log.

This splits the loop along the line of what each half actually needs:

  MEASUREMENT  mechanical, local, unattended. Runs whether or not anyone is
               connected. An unattended hour is no longer a lost hour.
  JUDGEMENT    which change to make, and confirming it against full-engine
               replay. Needs a model. Happens when Claude is open -- which is
               exactly the constraint Sri stated anyway.

Verified against today's data: "learn: P&L realised Rs 4,107 (4.11%)" and
"learn: Biggest is EXIT with only 2 cases -- an anecdote" both landed in the
supervisor log. Failure is contained -- a timeout or crash in learn.py logs and
returns, and cannot touch the board. Instrumentation that can take down the
thing it measures is worse than none.

## 02-Sep 21:35 -- AUTOSTART WOULD HAVE TRADED NOTHING TOMORROW

Sri said he will start SUPERVISOR_START.bat at 09:00 so the board is warm for
the open. Checked _autostart before agreeing, and it would have silently cost
the entire day:

    time.sleep(20)
    now = ...
    if not ("09:15:00" <= now <= "15:10:00"):
        log("autostart: outside trading hours -- not starting")
        return              # <-- for good

Booted at 09:00, it looks at the clock at 09:00:20, sees 09:00:20 < 09:15:00,
declines, and the thread ends. Nothing ever starts it again. The board would
have run all day looking completely healthy -- cards updating, sweeps logging,
supervisor reporting HEALTHY -- and placed not one trade. The only evidence
would have been a single line at 09:00:20 in a log nobody opens until evening.

This is the same failure shape as the empty Super tab that could not say why,
and as super_monitor logging CRITICAL and doing nothing: the system knows
something is wrong and the knowledge goes nowhere.

FIXED in both copies of _autostart: it now WAITS for the window rather than
declining it. Before 09:15 it sleeps 30s and re-checks, logging "holding until
the 09:15 open" so the waiting is visible. Past 15:10, or at a weekend, it
still returns -- those are the only cases where there is genuinely nothing to
start.

Verified across the boundary: 09:00:20 / 09:05 / 09:14:59 all WAIT; 09:15:00
onward STARTS; 15:10:01 returns; Saturday returns.

Worth noting AUTOSTART_TARGET is already 30.0, matching the target Sri set.

## 02-Sep 21:40 -- TWO STANDING RULES FROM SRI, made mechanical

1) "If you fix something, that should not impact the success scenarios."
Every change so far was judged only on what it was meant to improve. That is
exactly how displacement shipped: it won its own A/B and quietly wrecked
everything else. regress.py now runs the FULL engine over every session on disk
and compares net P&L against a locked baseline. A change may improve things; it
may not make any session more than 10% worse without that showing up.

BASELINE LOCKED (Rs 1,00,000, full engine, per session):
  27-Aug   -501     28-Aug  11,455    31-Aug  -1,742
  01-Sep  20,298    02-Sep  14,265
Note 27-Aug and 31-Aug are already NEGATIVE. Two of five sessions lose money
with the current rules -- that is the honest starting point, and any claim of
improvement has to move those two without giving back the other three.

Rule: before shipping anything tomorrow, `python3 regress.py` must come back
clean. It exits 1 on a regression.

2) "Use minimal tokens -- you may go blind again."
Correct, and it is a real failure mode: reading a 40-line report every ten
minutes burns the usage limit before lunch, and then nothing gets analysed at
all for the rest of the day. The detail was never worth that.
learn.py now has --brief: six lines back, full report still written to
LEARN_YYYYMMDD.md. The supervisor calls it that way. Fetch the detail only when
a bucket is big enough to act on.

TOMORROW'S ORDER OF OPERATIONS
  1. read the brief (6 lines)
  2. only if a bucket >= 5 cases, open the full LEARN file for that pass
  3. form the change
  4. replay_live to confirm it on the full session
  5. regress.py to confirm it broke nothing else
  6. only then ship, and record what was rejected and why

## 03-Sep 09:30 -- SHIPPED MID-SESSION: ENTRY_MIN_URGENCY 12 -> 18

Sri's feedback at 09:28 (DCX, BALUFORGE, PAR, KIRI, MARINE, FILATEX) all reduce
to one complaint: entries taken as the move rolls over. He is right, and the
tape says the cause is a bar set far too low.

LIVE EVIDENCE, today, first 13 minutes:
  urgency <17    16 trades   -Rs 12,603   (-Rs 788/trade)
  urgency >=17    3 trades    +Rs  6,114   (+Rs 2,038/trade)

This independently reproduces yesterday's rules_lab finding (urgency <12 loses,
20-30 best) on completely separate data. Two sources agreeing is the bar I set
for shipping without a replay, and the conservative direction: a higher floor
takes FEWER trades, so the failure mode of being wrong is missed profit, not
new loss. Applied by hot config + supervisor restart at 09:29:43.

THE BIGGER BUG, not yet fixed: 29 trades in 13 minutes, SEVEN of them at the
identical second 09:16:11. The book filled completely in the first minute of
the session, before any position had proved anything. Exposure was correctly
capped at Rs 499,935 of Rs 500,000 -- the money control worked. What is missing
is a control on RATE. Raising urgency will suppress most of this as a side
effect; a proper per-minute entry cap is the real fix and needs replay first.

SECOND BUG: micro positions. GUJTHEM was 3 shares (Rs 1,301) and MANGLMCEM 11
shares (Rs 12,275). Risk sizing shrank them to nothing against a wide stop, but
they still consumed a slot and paid both legs of charges. A position that
cannot clear its own charges should not be opened. MIN_POSITION_RS does not
exist in live_paper yet, so hot config ignored it -- add after close.

WHAT SRI IS RIGHT ABOUT AND I HAVE NOT FIXED: the Board cards already carry
ema9, sma12, macd, supertrend. live_paper reads NONE of them -- it decides on
urgency and from_open alone. The indicators he watches are computed, displayed,
logged, and then ignored by the thing placing the trades. That is the single
biggest structural gap in the system and it is the next real piece of work.

KIRIINDUS is the instructive case. Leg 1 at 09:16:11 (539.95) ran to 568.4,
about +5%. Leg 2 at 09:24:37 (570.80) bought the exhaustion and lost. Same
stock, same day: the entry rule was not wrong about the NAME, it was wrong
about the MOMENT. That is an argument for a re-entry/exhaustion guard, not for
dropping the stock.

## 03-Sep 09:36 -- AUTO-TUNER LIVE, and it tried to run away in its first 3 cycles

Sri: "not just report -- tune so you learn mistakes and reapply immediately,
every 10 minutes." Built tune.py, called from learn.py (which the supervisor
re-runs fresh each cycle, so it went live with no restart). It runs on Windows,
so unlike my shell it can call /admin/reload on localhost -- constants hot-apply
to the running board and OPEN POSITIONS ARE NOT DISTURBED.

IT MISBEHAVED IMMEDIATELY, which is the best thing that could have happened.
In three test cycles it raised ENTRY_MIN_URGENCY 18 -> 19.5 -> 21 -> 22.5.

The bug: when the newest segment had no closed trades yet, it fell back to ALL
of the day's trades. So it kept re-reading the same pre-change losers and
"confirming" a raise on evidence that predated the previous raise -- each pass
counting the same mistake again. Left alone it would have ratcheted to the
ceiling of 26 within the hour and stopped trading entirely, while reporting
that it was learning.

Fixed: a change is judged ONLY on trades taken under it. Fewer than MIN_N
trades in the current segment now returns "waiting for evidence under the
CURRENT setting" and changes nothing.

SECOND LESSON, about me: those three phantom raises were MY dry runs from the
Linux VM. The tuner has side effects -- it writes live_config.json -- so
testing it mutated the real trading config while the market was open. The
reload failed (my shell cannot reach the board) so the running engine was never
affected, but the file said 22.5 while the board ran 18.0. Added tune.DRY,
passed by --dry, so a test can never write. Config restored to 18.0 and the
phantom entries stripped from TUNES.jsonl.

GUARDS, each from a specific past failure:
  one change per cycle          -- two at once cannot be told apart
  bounded ranges + one step     -- 01-Sep: displacement moved twice in one
                                   afternoon in opposite directions
  minimum sample of 8           -- three per-trade findings failed replay
  reverts its own last change   -- if the segment since it did worse than the
                                   segment before it, it is undone
  cool-off after a revert       -- otherwise it oscillates all session
  drawdown brake                -- past -8%, cut risk rather than chase it back

## 03-Sep 10:25 -- URGENCY WAS THE WRONG KNOB. The engine ignored the chart.

Sri named six surging stocks that were never traded: MAYANVAR, RAYMONDREL,
JINDWORLD, HIKAL, LAXMIINDIA, LALITHAA. All but MAYANVAR WERE carded. Every one
was blocked by the urgency floor I raised to 18 an hour earlier. skipped["weak"]
had reached 5,075.

What they would have done at +2%/-1%:
  JINDWORLD  target +2.0    HIKAL      target +2.0    RAYMOND   target +2.0
  LALITHAA   stop   -1.0    RAYMONDREL open   -0.5    LAXMIINDIA open -0.7
Three clean targets out of six. He was right; the floor was costing real money.

URGENCY IS NOT MONOTONIC, which kills the whole premise of a floor:
  0-14    n=10   +Rs   263/trade      <- MADE money
  14-16   n=12   -Rs   826/trade      <- the only genuinely toxic band
  16-18   n= 4   +Rs   411/trade
  18-20   n= 5   +Rs   632/trade
  20+     n= 2   +Rs 3,210/trade
A single floor cannot express that shape. Raising it to 18 removed the bad band
by accident and the good low band along with it -- and blocked three winners.

THE REAL FAULT, which Sri has now said in three different ways and I kept
answering with the wrong knob: we buy AFTER the move rolls over. Urgency blends
speed, size and age, so a stock that surged hard and has just turned still
scores well for a while. It cannot express "is it going up right now".

Three fields could, and the engine looked at NONE of them:
  off_peak     how far below its own session high it is trading
  sinceHigh    seconds since it last printed a new high
  ema_angle    the slope of the EMA the board draws on the card
  dipsUsed     how many times it has given back and resumed  (chop)

All four are computed, displayed on the card, and written to the logs. The
trading engine has never read one of them. Sri watches these on his chart; the
system that places the orders has been blind to them the whole time.

SHIPPED 10:25 (restart, board pid 29136):
  ENTRY_NEEDS_FRESH_HIGH  on
  ENTRY_MAX_OFF_PEAK      0.40%   -- not more than this below its own high
  ENTRY_MAX_SINCE_HIGH    90s     -- must have made a new high recently
  ENTRY_MIN_EMA_ANGLE     0.05    -- flat (0) or falling is not a trend
  ENTRY_MAX_DIPS          2       -- up-down-up-down is chop, not a run
  ENTRY_MIN_URGENCY       18 -> 16 (excludes the 14-16 band, admits the rest)

New skip counters: rolled_over, stale_high, ema_flat, choppy -- so the next
review can say WHICH of Sri's four criteria is doing the work, instead of one
undifferentiated "weak".

NOTE ON since_high: the scanner writes since_high_s, build_card writes
sinceHigh. The gate reads both. A gate that silently reads None is not a gate,
and this project has already shipped one of those.

MAYANVAR never carded at all -- a detection gap, separate from this, still open.

## 03-Sep 10:31 -- THE SESSION IS NOT ONE MARKET. Gates now move with the clock.

Sri: "Maximum profits can be achieved from 9:15 till 10:30... after that only
few stocks get into surging mode. By now you should have realized all these."

He is right and I should have. I found the symptom an hour earlier -- a floor of
16 rejecting 100% of candidates at 10:20 because nothing scored above 13 -- and
treated it as a bad constant instead of what it actually was: evidence that the
session changes character and my settings do not.

MEASURED, 6 sessions, every carded stock at +2%/-1%:
  09:15-09:45   148 cards   47% win   31% reached +2%
  09:45-10:30    31 cards   70% win   19%
  10:30-11:30    20 cards   45% win   15%
  11:30-13:00     3 cards
  13:00-15:30     9 cards
85% of every opportunity this scanner has ever found arrives before 10:30.
The first half hour holds the VOLUME; 09:45-10:30 holds the best hit rate;
after that the market thins and chops.

Note this also explains the urgency drift. Urgency subtracts trade age and
leans on the 90-second rise, so the same number means something different at
09:20 and 11:20. A fixed floor therefore gets quietly stricter all day without
anyone changing it -- which is exactly what happened this morning.

SHIPPED: PHASES table, five bands, each setting urgency / off_peak /
since_high / ema_angle / dips together:
  <09:45   10.0  0.60  120s  0.00  3   everything is running -- get out of the way
  <10:30   11.0  0.45  100s  0.03  3   best hit rate of the day
  <11:30   14.0  0.30   75s  0.06  2   thinning out
  <13:00   16.0  0.25   60s  0.08  1   few real movers, demand near-perfection
  <15:30   15.0  0.30   75s  0.06  2   late-day moves exist but are fewer

PHASE_ENABLED can turn the whole thing off and fall back to the static
constants, so this is reversible in one flag.

WHAT I STILL OWE: these bands are set from measured card counts and hit rates,
not from a full-engine replay of the bands themselves. That confirmation is due
before market open tomorrow, along with regress.py to prove the earlier
sessions did not get worse.

## 03-Sep 10:45 -- 15-MINUTE PHASES, and the open is NOT what it looks like

Sri asked to subdivide 09:15-10:30 into five 15-minute bands. Measured first,
6 sessions, every carded stock at +2%/-1%:

  band          n   /day   avg%   win%   +2%   STOP%   med urg
  09:15-09:30  123    25   0.38%   46%   31%    35%      15.6
  09:30-09:45   25     5   0.31%   52%   28%    28%      15.0
  09:45-10:00   14     3   0.62%   78%   28%     7%      13.5   thin
  10:00-10:15   11     4   0.49%   63%    9%     0%      13.5   thin
  10:15-10:30    6     3   0.39%   83%   16%    16%      15.9   thin
  10:30-11:30   20     5   0.12%   45%   15%    15%      14.9

THIS CORRECTS WHAT I BUILT AN HOUR AGO. I had read "maximum profit 09:15-10:30,
most stocks go bull side" as "loosen the gates at the open". The data says the
open is where the VOLUME is -- 25 cards a day against 3 later -- but it is the
most VIOLENT hour, not the most generous: 31% reach +2% and 35% stop out. High
variance, not easy money.

09:45-10:15 is the mirror image: almost no candidates, but they barely ever
stop (7% and 0%). Fewer chances, far safer ones.

So the gates do not simply widen at the open and tighten later, which is what
my first table did:
  AT THE OPEN candidates are plentiful, so we can afford to be STRICT on
  freshness and still fill the book -- and strictness is precisely what avoids
  the 35% that roll over. off_peak 0.35, since_high 60s.
  MID-MORNING candidates are scarce and well-behaved, so we LOOSEN to catch the
  few that appear. off_peak 0.55, since_high 120s.
The first version had this backwards in both halves.

HONESTY: the three middle bands are 6-14 samples. Individually each is an
anecdote by my own standing rule. What justifies acting is that all three agree
on the stop rate -- 7%, 0%, 16% against 35% and 28% at the open -- which is a
consistent shape rather than one lucky cell. If tomorrow disagrees, the table
is wrong and it moves back. Logged here so that check actually happens.

STILL OWED, before tomorrow's open: full-engine replay of the phase table
itself, and regress.py to prove the six prior sessions did not get worse.

## 03-Sep 11:20 -- I TUNED THE WRONG VARIABLE ALL MORNING

Sri saw the Back Trade tab report Rs 7,478 (7.48%) and asked what was right
about it. Two answers, and the second matters far more.

1. PART OF IT IS HINDSIGHT, and I should have said so unprompted.
   paper_engine runs the day at six slot counts and reports the best:
     slots 1  8 trades  Rs 5,303      slots 4  20 trades  Rs 8,342  <- reported
     slots 2 14 trades  Rs 3,811      slots 5  22 trades  Rs 5,169
     slots 3 18 trades  Rs 8,006      slots 6  25 trades  Rs 1,765
   Choosing 4 after seeing all six is worth Rs 6,578 of spread and is not
   available at 09:15. replay_live exists for exactly this reason and says
   Rs 668 forward-only on the same day. I quoted both numbers side by side
   earlier as if they measured the same thing. They do not.

2. THE REAL SIGNAL: it is not about which stocks. It is about how many.
     25 trades -> Rs 1,765      20 trades -> Rs 8,342      8 trades -> Rs 5,303
   And the same shape appears in everything else today, which I read four
   separate times as being about entry gates:
     live opening segment      29 trades -> Rs   476
     urgency floor 18          11 trades -> Rs 17,290  (charges Rs 1,040)
     micro-position fix        45 -> 20 trades, Rs 1,496 -> Rs 3,919
     shipped config today      32 trades, charges Rs 2,584 vs gross Rs 3,252

   Charges are consuming 50-80% of gross profit. Every change I made today --
   urgency floors, freshness, EMA slope, chop, five phases, then eight -- was
   about WHICH stock to enter. The dominant variable is HOW FEW AND HOW BIG,
   and it was in every table I produced.

NEXT, and only this: test position count directly in replay_live, forward-only,
3/4/5 slots across all six sessions. One number, not five knobs. If the shape
holds without hindsight it is the first thing all week that would matter more
than a rule change.

METHOD NOTE: eight restarts in an hour left two boards running at once and hit
the supervisor's own cap. The guard worked; my pace did not. Tuning at that
speed was never justified by the evidence I had.

## 03-Sep 11:30 -- WHAT AN EARLY BULLISH STOCK ACTUALLY LOOKS LIKE

Sri: work out from the logs which kinds of stocks are generally bullish
09:15-10:00 and watch for those. 164 carded stocks, 6 sessions, +2%/-1%:

  PRICE                    n    win%   avg%
    Rs 20-100             27     37%  -0.04%   <- LOSES money outright
    Rs 100-300            57     57%  +0.59%
    Rs 300-800            51     41%  +0.37%
    Rs 800+               29     65%  +0.45%
  DAY TURNOVER
    <5 Cr                 78     47%  +0.40%
    20-60 Cr              27     62%  +0.72%   <- the sweet spot
    60 Cr+                12     41%  +0.05%   <- too big to move
  RUPEES/MIN
    <50 L                 16     31%  +0.10%   <- illiquid, worst bucket
    2-5 Cr                36     55%  +0.53%
  90-SECOND RISE
    1-2%                  79     58%  +0.49%   <- steady climb
    2-4%                  53     41%  +0.28%   <- the violent ones do WORSE
  ALREADY UP
    7%+                    6     33%   0.00%

Two things worth keeping. The 20-100 rupee band is a genuine money-loser, which
is exactly what Sri said about FILATEX from instinct -- now confirmed on 27
samples. And the FASTEST movers are not the best: a 1-2% ninety-second climb
beats 2-4%. Chasing the most violent spike is a losing habit, which is the same
lesson as buying the rollover, seen from another angle.

VALIDATED, 6 sessions, forward-only:
  shipped (MIN_PRICE 20)          Rs 51,432   worst -1,746
  MIN_PRICE 100                   Rs 50,513   worst -2,398   (alone: no help)
  MIN_PRICE 100 + minpos 60k      Rs 55,799   worst -1,693   <- SHIPPED
Price filter alone does nothing; it only pays combined with a bigger minimum
position. Consistent with the frequency/size lesson from an hour ago.

Swept the position floor further -- 60k Rs57,405, 100k Rs45,557, 150k Rs57,421.
Non-monotonic, so "bigger is always better" is NOT supported. 60k is where the
evidence stops; past that it is noise and I am not going to pretend otherwise.

BUG FOUND: of 164 early cards across 6 sessions, ZERO carried preopen=True.
The pre-open confirmation flag never reaches the super rows, so PREOPEN_BOOST
(+4.0 to entry rank) has never once fired in any session. I described this
path to Sri yesterday as working. It is dead code. Not fixed yet -- it needs
the pre-open loop's symbol set traced through to superstocks_lab, and trading
is halted, so it goes in before tomorrow's open.

## 04-Sep 09:40 -- THE REPLAY WAS SCORING A SYSTEM THE BOARD DOES NOT RUN

Found while testing Sri's "no trades 09:15-09:16" rule, which needed replay_live
to compare entry windows. Before trusting the comparison I checked what the
replay does differently from the live engine -- the standing rule -- and it
differed in the worst possible place.

  replay_live.py and regress.py run on live_paper's MODULE DEFAULTS.
  They never read live_config.json, which is what Movers_app hot-applies and
  what the board actually trades.

Three constants differed:

    MIN_POSITION_RS    board 60000    replay 40000
    MIN_PRICE          board   100    replay    20
    PHASE_ENABLED      board  False   replay   True    <-- the damaging one

PHASE_ENABLED=True is the time-banded phase table. It was measured on 03-Sep at
Rs 31,178 against a Rs 44,870 baseline -- a Rs 13,692 REGRESSION -- and switched
off in live config the same day. Every replay and every regress run since has
been scoring a configuration containing a feature that had already been
rejected, plus two other constants the board does not use.

WHAT IT COST, measured both ways over the six sessions on disk:

    session     module defaults     shipped config      delta
    27-Aug            1,165              -211          -1,376
    28-Aug           12,567            16,401          +3,834
    31-Aug           -1,746            -1,693             +53
    01-Sep           17,826            25,033          +7,207
    02-Sep           -2,259            13,287         +15,546
    03-Sep            6,950             9,042          +2,092
    TOTAL            34,503            61,859         +27,356

The shipped config is worth Rs 27,356 more than what the instrument was
reporting, and 02-Sep flips from a Rs 2,259 LOSS to a Rs 13,287 gain. So the
handoff's "Rs 55,799 validated over 6 sessions" was measured with the wrong
config too, and understates the system.

FIXED: replay_live applies live_config.json at import, exactly as the board does
at boot, and says which constants it changed (RL.APPLIED). --nocfg reproduces
the old behaviour for comparison. Baseline re-locked; the old one is kept as
logs/REGRESS_BASELINE.json.pre04sep.

This is the FIFTH time replay and live have differed in a way that changed a
conclusion: 13s vs 3s tick, close-to-close ATR, a shared state file, rules
fitted on data live never produced, and now the config itself. The rule keeps
paying for itself; what is missing is a test that ASSERTS the two agree, rather
than a habit of remembering to check.

## 04-Sep 09:45 -- SRI'S 09:15-09:16 NO-TRADE RULE: supported, but not the exact minute

His sheet: "Exactly at 9:15AM till 9:16AM - don't take any trades till you are
100% confident."

PER CARD, 7 sessions, walking forward from each card's first sighting so a stop
counts BEFORE any later high (price >= Rs100):

    window          cards   reached +2%   stopped -1%   avg best
    09:15-09:16        27          26%           52%      2.17%
    09:16-09:17        31          35%           48%      2.30%
    09:17-09:20        45          38%           29%      4.38%
    09:20-09:30        34          47%           21%      2.38%
    rest of day       213          36%           27%      2.53%

The first minute stops out at TWICE the rate of the rest of the day. He is right,
and 09:16-09:17 is nearly as bad.

FULL ENGINE, corrected instrument, 6 sessions:

    ENTRY_FROM    total     trades   worst day
    09:15:00     61,859       112      -1,693
    09:16:00     71,361       107        -211
    09:17:00     81,264       100        -213
    09:18:00     41,660       104      -6,032

09:17 tests best and 09:18 collapses by Rs 40,000. A one-minute change swinging
the total by that much means path dependence dominates -- one early entry
changes slot occupancy for the whole session. The SHAPE (skip the first minute)
is confirmed by two independent measurements; the exact minute is not
recoverable from six sessions.

RECOMMENDATION: ENTRY_FROM 09:16:00. It is Sri's own rule, it is supported by
the card-level stop rate, it adds Rs 9,502 and it improves the worst day from
-1,693 to -211. 09:17 is not taken: it is the peak of a curve whose next point
falls off a cliff, which is what chasing noise looks like.
NOT SHIPPED YET -- awaiting Sri.

## 04-Sep 18:50 -- THE REPLAY BARELY EVER STOPS OUT. Every validated number is inflated.

Found by comparing today's live session against a replay of the same day, same
config:

    LIVE 04-Sep     38 trades   net -Rs   747
    REPLAY 04-Sep   27 trades   net +Rs 31,562

Same engine, same config, same day. The replay's exits: target 9, TIME 16,
STOP 2. Its 17 losing trades cost a combined Rs 5,921 -- an average loss of
Rs 348 on positions averaging Rs 150,000, i.e. about -0.23%. The stop is -1%.
Live, roughly half the trades stopped.

THE CAUSE, in one line of replay_live.Alarm.snapshot():

    it returns rows[mid][1] -- the CLOSE of the 30-second bar, and nothing else.

by_sym holds (t, close, high, low). The high and low were added on 02-Sep when
the ATR bug was found ("by_sym only stored closes; true range needs high/low").
The data was fixed; the PRICE THE ENGINE READS was not. So the replay marks
every position to the 30-second close. A stock that trades down to -1.4% inside
a bar and closes at -0.6% never triggers the stop. The live engine ticks every
3 seconds and stops out.

Stops are therefore systematically converted into small time-capped losses.
On 04-Sep that turned roughly Rs 25,000 of real stop losses into Rs 5,900.

WHAT THIS INVALIDATES
  Every absolute figure produced by replay_live, which is every validation in
  this project: the "Rs 55,799 over 6 sessions", the Rs 61,859 measured today
  after the config fix, and every A/B run today (entry window, from_open cap,
  urgency floor). The RELATIVE comparisons are not automatically safe either:
  a config that takes more marginal trades benefits more from stops that never
  fire, so the bias runs in favour of looser settings.

  It also explains the standing puzzle -- replay says 8-9% a session while live
  delivers 3-5%. That gap was never strategy decay. It was the instrument.

THIS IS THE SEVENTH replay-vs-live divergence, and the second in this exact
place:
    1. displacement validated at a 13s tick, shipped to a 3s engine
    2. ATR measured close-to-close in replay
    3. rules fitted on replay data live had never generated
    4. a shared state file
    5. replay running module defaults instead of live_config.json (found today)
    6. stops evaluated on closes, not lows (this)
The pattern is not carelessness on any single day; it is that NOTHING ASSERTS
the replay and the live engine agree. Every one of these was found by a person
noticing. The fix that matters is not this bug -- it is a test that replays a
day and compares the result against what the live engine actually did, and fails
when they diverge.

PROPOSED FIX, not yet made:
  snapshot() should expose (close, high, low) and live_paper should test the
  stop against the LOW and the target against the HIGH, with the STOP taking
  precedence when both are hit inside the same bar. That is the pessimistic
  convention and it is the right default for a system whose worst failure is
  believing it makes money it does not.
  Then: re-run every conclusion in this file that rests on replay_live.

## 04-Sep 19:20 -- THE REPLAY CANNOT PRICE HALF THE STOCKS IT TRADES

Chasing the live-vs-replay gap further. The close-vs-low bug (previous entry) is
real and is now fixed, but it was the smaller half. The real defect:

  On 04-Sep the replay took 27 trades. THIRTEEN of them exited at EXACTLY the
  entry price. Ten of those symbols do not appear in bars30 at all.

    SANGHVIMOV  in 471.95  exit 471.95  -100  time   not in bars30
    BLUESTONE   in 834.15  exit 834.15  -100  time   not in bars30
    IGL         in 155.06  exit 155.06  -100  time   not in bars30

  by_sym is built ONLY from bars30. With no series for a symbol, alarm.snapshot()
  returns no price, live_paper leaves p["last"] at the entry price, and the
  position drifts to the 30-minute time cap dead flat, paying only charges.

  Those 13 trades cost Rs 1,300 in total.
  The 14 trades that COULD be priced made Rs 24,892.

WHY THIS IS NOT A ROUNDING ERROR
  bars30 is written by Movers_ticks from the BOARD's own universe -- fed by
  TICKS.track() and TICKS.add(). The symbols it contains are, by construction,
  the ones that were already moving enough to be tracked. So the replay prices
  the winners and FREEZES everything else into a free option that cannot lose.
  Today it covered 168 symbols; the engine opened positions in 24, ten of which
  it could not price.

  This is the card_lab failure again, in a different file: the tape is
  DOWNSTREAM of the decision under test. It cost an evening there because a
  calibration gate was built first. Here there was no gate, and it has been
  silently inflating every number for weeks.

WHAT IS NOW KNOWN TO BE UNSOUND
  Every absolute figure replay_live has produced. That includes:
    * "Rs 55,799 validated forward-only over 6 sessions" in the handoff
    * Rs 61,859 measured today after the live_config fix
    * today's A/Bs: entry window (61,859 / 71,361 / 81,264 / 41,660),
      from_open cap (66,339 / 24,694), urgency floor (53,214)
    * the regress.py baseline, which calls the same function
  Relative comparisons are not automatically rescued: a config that opens more
  positions collects more unpriced free options, so the bias favours looser
  settings -- exactly the direction that has repeatedly looked attractive.

FIXED SO FAR (04-Sep)
  live_paper.tick() takes an optional `extremes` map and judges the stop on the
  bar LOW and the target on the HIGH, stop first. Live passes nothing, so live
  behaviour is unchanged. On 04-Sep this alone moved the replay from
  Rs 31,562 to Rs 23,592.

STILL BROKEN, and this is the blocker
  Price coverage. The replay needs a 30-second series for EVERY symbol it can
  trade, not only those the board happened to track. The data exists --
  Movers_chartfeed.get_seconds() pulls true 30s bars from ticks.dhan.co, which
  is what fetch_tf30.py already uses and what warmup.py now calls. The fix is a
  cache: for each session, fetch 30s bars for every symbol appearing in that
  day's super log, store under logs/tape/<day>/, and have replay_live prefer the
  cache over bars30. Board-side, once per session, reusable forever.

UNTIL THAT EXISTS: replay_live is not a ship gate. Nothing should be shipped on
its numbers, including the three changes measured today. regress.py inherits the
same defect and its baseline means little.

THE REAL LESSON, and it is now unmistakable at seven occurrences: no one ever
built a test that asserts the replay and the live engine agree. Each defect was
found by a person noticing an odd number. The single highest-value thing in this
project is not a trading rule -- it is a harness that replays a completed
session and FAILS when its result diverges from what the live engine actually
did that day. Today's pair (live -Rs 747 vs replay +Rs 31,562) would have failed
it on the first run.

## 04-Sep 20:10 -- FIRST HONEST STRATEGY RESULTS, on a repaired instrument

Everything below is measured on the three sessions that have a real fetched
30-second tape (02, 03, 04-Sep), with stops judged on bar LOWS and targets on
HIGHS, and with a price series for EVERY symbol traded. Older sessions cannot be
included: the 30-second feed carries only about three sessions, so 27-Aug
through 01-Sep can never be priced properly and their historical "validations"
are unrecoverable.

Three sessions is a small sample and it is the same sample the parameters were
chosen on. What follows is a hypothesis with a plateau, not a proven result.

### 1. EXITS -- the largest lever, and Sri was right about the shape

    exit rule                     total   trades  stops   worst day  all sessions +
    trail arm1.1 give1.35        57,742      86      41     +8,145        YES
    trail arm1.2 give1.4         53,831      86      41     +8,145        YES
    trail arm1.0 give1.4         52,553      86      41     +8,145        YES
    trail arm1.1 give1.5         46,924      86      41    +12,389        YES
    hybrid alive60 give0.8       25,684      93      36     -7,780        no
    trail arm1.4 give1.2         10,123      94      49     +1,197        YES
    target +2/-1  (SHIPPED)      -6,997     116      54     -7,742        no
    target +3/-1                -23,819     106      56    -15,897        no
    target +1.5/-1              -32,869     134      61    -14,849        no

EVERY dynamic exit beat EVERY fixed target. That is Sri's instinct -- "if the
stock keeps making higher highs with minor dips, decide dynamically whether to
hold" -- and it is the first thing in this project that the data has actively
supported rather than contradicted.

It is a PLATEAU, not a peak. Givebacks of 1.30/1.35/1.40/1.45/1.50 all return
45k-58k with every session positive; 1.25 and below and 1.7 and above fall off a
cliff. That is the opposite shape to the 09:16/09:17 entry window, where the
best value had a -Rs 40,000 neighbour, and it is why this one is worth taking
seriously and that one was not.

### 2. ENTRIES -- Sri's stack contains real signal, but not where he thinks

177 cards, computed causally from the tape at each card's first sighting:

    signal                        n    reached +2%   stopped
    ABOVE VWAP                   75         65%         5%
    below VWAP                  102         11%        80%
    SuperTrend 10,3 up           80         48%        34%
    SuperTrend 10,3 down         97         23%        61%
    above Ichimoku cloud         68         44%        35%
    MACD above zero              83         41%        37%
    SAR below candle             84         38%        42%
    EMA8 above MA12              80         32%        50%
    EMA8 below MA12              97         35%        47%

  * VWAP -- which is NOT on his chart and was added for free from the same bars
    -- is by far the strongest separator, and it held every session (76/65/54%
    above, stops 4/8/4%).
  * SuperTrend 10,3 BEATS 10,2 (48%/34% vs 43%/38%). The chart was right and the
    handoff was wrong.
  * MACD above the zero line is confirmed exactly as he described it.
  * His FIRST signal, the EMA8/MA12 cross, shows NO discrimination at all --
    32% above vs 35% below. The cross tells you a move happened; it does not
    tell you whether the next one will.

### 3. AND THE VWAP GATE STILL SHOULD NOT SHIP

    baseline (shipped)      -6,997
    VWAP gate only          -2,724
    trail 1.1/1.4 alone     53,831   every session positive
    VWAP + trail            27,847   every session positive

The best per-card filter in the project HALVES the best exit rule. It blocks
cards that score badly at entry and that a trailing exit would have ridden into
the day's biggest winners. This is the 02-Sep rules_lab lesson repeating: a
per-trade statistic is a hypothesis, and only the portfolio can judge it. Built
as ENTRY_NEEDS_VWAP, defaulting OFF, kept for when the exit question is settled.

### WHAT I WOULD SHIP, AND WHAT I WOULD NOT
  SHIP (as a forward test, clearly labelled unproven): the trailing exit,
  TRAIL_ARM_PCT 1.1 / TRAIL_GIVE_PCT 1.4. Middle of a broad plateau, every
  covered session positive, worst day +8,145 against the shipped rule's -7,742.
  DO NOT SHIP: the VWAP gate, the 09:16 entry window, the from_open cap, the
  urgency floor change. Three of those were measured on the broken instrument
  and the fourth fails in the portfolio.

## 04-Sep 21:00 -- I HAD LOOK-AHEAD IN THE REPLAY. Everything above was inflated.

Sri asked for the new logic to be back-tested "assuming you don't have forward
timing candles knowledge". Checking that properly found a defect in code I had
written hours earlier.

  _bar_at() picked the bar whose START was at or before now, then judged the
  stop against that bar's LOW and the target against its HIGH. At 09:15:10 that
  is the 09:15:00-09:15:30 bar -- twenty seconds that have not happened yet.
  Alarm.snapshot() had the same fault on the CLOSE, so it also set entry fills
  from a price the engine could not yet have seen.

FIXED: both now require the bar to have CLOSED (start + 30s <= t). A stop can
therefore fire up to one bar late, which is the pessimistic direction. Verified
directly: at bar10+5s the engine now reads bar 9's high/low, not bar 10's.

WHAT IT COST -- every exit number in the 20:10 entry above was too high:

    04-Sep, trail 1.1/1.4     with look-ahead 14,368  ->  causal 4,421
    3-session totals          with look-ahead 53,831  ->  causal 17,389

This is the EIGHTH replay-vs-live defect, and the first one I introduced myself
rather than inherited. It was found because Sri asked the right question, not
because anything caught it. replay_check.py would not have caught it either --
it compares totals, and look-ahead flatters the replay in a way that still
lands in a plausible range.

## 04-Sep 21:10 -- EXITS, MEASURED CAUSALLY. Sri's instinct holds up.

Three tape-covered sessions, forward-only, stop on the bar low, target on the
high, real prices for every symbol, nothing read before it happened:

    exit rule                   total   trades  worst day   every session +
    hybrid give 1.1            47,048      81    +13,049         YES
    hybrid give 0.9            46,207      84    +10,215         YES
    hybrid give 1.0            39,206      79     +6,030         YES
    hybrid give 1.2            35,546      84     +9,336         YES
    hybrid give 0.8            32,177      87     +4,278         YES
    trail 0.8/1.4              20,394      81     -6,561          no
    hybrid give 1.5            20,208      82     -5,262          no
    trail 1.1/1.4              17,389      80       +560         YES
    target +2/-1 (SHIPPED)    -37,351     125    -37,035          no

Every giveback from 0.8 to 1.2 is positive on all three sessions; 1.5 breaks.
That is a plateau five points wide, not a peak beside a cliff -- the opposite
shape to the 09:16/09:17 entry window, and the reason this one is worth taking
and that one was not.

"hybrid" means: bank at +2% UNLESS the position is still making new highs, then
ride and exit on a giveback from the peak. That is Sri's own rule, almost word
for word. HYBRID_ALIVE_SEC turns out to be INERT -- 45s and 60s give identical
results, because a rising position refreshes its peak on nearly every tick -- so
in practice this is "ride past the target with a 1% trailing giveback".

SHIPPED: EXIT_MODE hybrid, HYBRID_GIVE_PCT 1.0 (middle of the plateau, not the
peak). regress.py clean: 02-Sep +56,906, 03-Sep +15,512, 04-Sep +4,139 against
the re-locked baseline, no session damaged.

STILL UNPROVEN: three sessions, and the giveback was chosen on the same three.
The plateau is the argument, not the total.

### DAY ONE, 04-Sep, forward-only with the new exit
    32 trades, NET +Rs 6,030 (6.03%), gross 9,265, charges 3,234
    11 wins / 21 losses -- a 34% win rate
    SHANTIGEAR +8,694, RML +9,158, XTRANET +6,423, all "rode + faded"
    the live board that day, on the old rule: -Rs 747

  A third of trades win and the book still makes 6%, because the exit lets the
  three real runners run instead of banking them at +2%. Charges fell to 35% of
  gross from the 50-80% that has dogged every previous session.

## 04-Sep 21:15 -- ENTRIES: VWAP, and what Sri's own stack is worth

177 cards, indicators computed causally at each card's first sighting:

    above VWAP        n=75    reached +2% 65%   stopped  5%
    below VWAP        n=102   reached +2% 11%   stopped 80%
    SuperTrend 10,3 up          48% / 34%      (10,2 gives 43% / 38%)
    above Ichimoku cloud        44% / 35%
    MACD above zero             41% / 37%
    EMA8 above MA12             32% / 50%   <- no discrimination at all

  * VWAP is the strongest single separator in this project, and it is the one
    indicator NOT on Sri's chart.
  * SuperTrend 10,3 beats 10,2. The chart was right, the handoff was wrong.
  * MACD above zero is confirmed exactly as he described it.
  * The EMA8/MA12 cross -- his stated FIRST signal -- separates nothing. It says
    a move has happened, not that another is coming.

VWAP as a hard gate: with the OLD (target) exit it HURT the portfolio. With the
hybrid exit it helps slightly (+4,587 over three sessions, all of it on one
session). Built as ENTRY_NEEDS_VWAP, left OFF -- one session is not evidence.

## 04-Sep 21:20 -- BOX 10: the abnormal-candle claim is real but tiny

Sri: "Any candle if it shoots up extremely high (abnormally), high chance that
next candle goes for profit booking." Measured against each stock's own average
candle size:

    candle size        n      next candle red   avg next move
    normal <2x     10,529          45%            -0.003%
    2-4x avg        1,741          49%            -0.017%
    4-8x avg          401          51%            -0.042%
    8x+ avg            63          51%            -0.112%

Directionally he is right and it is monotonic -- but a 51% chance of a red
candle against a 45% base, worth about a tenth of a percent, is not tradeable
after charges. Recorded, not acted on.

## 05-Sep -- VOLUME EXPANSION IS THE ENTRY SIGNAL. Read by eye first, then coded.

Sri: "you are hardcoding rules rather than learning... don't write rules on
rocks just because I gave them." So five stocks on 04-Sep were read by hand,
as a human would, with no code involved -- IFCI, HEG, NIACL, LALITHAA, MARSONS.

Every trade worth taking had the SAME signature, and it was not an indicator:

    MARSONS  10:16   35K -> 907K   (25x)   ran +6.1%
    NIACL    11:38  717K -> 7.3M   (10x)   ran +8.1%
    LALITHAA 13:21   80K -> 3.1M   (40x)   ran +2.2%
    HEG      13:37    6K -> 127K   (20x)   ran +2.7%
    IFCI     11:37  2.2M -> 15.3M   (7x)   ran +2.6%

A quiet stretch, then volume multiplying several-fold in a few candles, with
price making new highs at the same time. The engine read NO volume at all.

THE TRAP, and it must be excluded: MARSONS 09:50 printed 822K then 905K -- its
biggest volume of the morning -- and price went 127.47 -> 130.35 -> 128. Volume
without price progress is people leaving, not arriving.

### Coding it, and the three mistakes made on the way

1. MEASURED ON ONE CANDLE INSTEAD OF A WINDOW. The expansions above were seen on
   5-minute blocks; at 30-second resolution MARSONS' 25x reads 4.0, then 1.3,
   then 1.0. Real signal, invisible bar by bar. Fixed: compare the last 6
   candles against the 20 before them.

2. THE LIVENESS TEST VETOED THE WAKE-UP. HEG sat in a 5-point band for four
   hours and then ran +6.6%; the backward-looking "is this stock alive" test
   refused the breakout precisely BECAUSE the past was quiet. Fixed: judge the
   current block's own range, not history.

3. ONE EXIT FOR EVERY TRADE. NIACL exited at 11:50:30 on "volume faded" at
   +0.58% and the run to +8.13% began sixty seconds later; the re-entry cooldown
   then locked it out. Fixed with TWO exits: a trade under +0.5% is cut as soon
   as the volume that brought us in dries up; a trade above +0.5% is held on a
   wide trail and volume is IGNORED -- every big move in these five went quiet
   in the middle and then went further.

### And one over-fit, caught by Sri's own instruction

MARSONS' five entries separated beautifully: the winner had 32.7x volume and sat
0.88% above VWAP; the four duds had 2.7-8.9x and up to 3.34%. Raising the
threshold to 8x and capping VWAP stretch at 1.5% made MARSONS perfect --
and took IFCI, HEG and NIACL to ZERO trades, deleting NIACL's Rs 55,324.
Across all seven profitable entries in the five stocks, volume expansion ranges
3.0x to 32.7x and VWAP distance 0.56% to 3.25%. Neither threshold generalises.

WHAT DOES generalise is how far the stock has already travelled:

    every winner            day range so far  2.54% - 8.14%
    MARSONS afternoon duds  day range so far  10.41% - 11.05%

"Do not buy a stock that has already made its move." That single rule removed
the duds without touching a single winner -- and it is Sri's own point about
buying an exhausted peak, agreeing with the earlier independent finding that
entries 7%+ from the open win only 12% of the time.

### Where it stands, 04-Sep, five stocks, one at a time, Rs 1,00,000 at 5x

    stock       by eye     code    trades
    NIACL       54,994   54,193    2      (code held 10:39->12:06 for +9.62%)
    MARSONS     37,430   21,916    2
    HEG         15,411   13,849    2
    LALITHAA    27,792   11,089    3
    IFCI        29,466    5,540    2
    TOTAL      165,093  106,587

From -Rs 2,625 at the first attempt to Rs 106,587 -- 65% of what the eye got.

STILL MISSING: the opening drive. IFCI 09:21 and LALITHAA 09:17 were both large
early moves and the code cannot see them -- it needs 26 candles of history
before it can measure an expansion at all, so it is blind until roughly 09:28.
That is a warm-up problem, fixable the same way the indicator warm-up was:
seed the volume baseline from the previous session.

NOTHING SHIPPED. live_config.json is untouched; this all lives in signal_sim.py.

## 05-Sep -- THE HUMAN BENCHMARK, 09:15-11:00 on 04-Sep

Read by hand off the 30s tape, no code. Long only. Sri: "Super Super Super."

ONE BOOK, Rs 1,00,000 at 5x, three positions at a time, Rs 1,66,666 a slot:

    A  LALITHAA 09:16 +2.11 | JINDWORLD 09:22 +3.33 | XTRANET 09:46 +2.75 |
       XTRANET 10:04 +12.56 | NIACL 10:36 +1.37
    B  MARSONS 09:16 +1.74 | LALITHAA 09:28 +2.25 | NIACL 09:52 +2.99 |
       MARSONS 10:16 +6.76 | RML 10:30 +11.27
    C  IFCI 09:17 +3.59 | JINDWORLD 09:49 +5.21 | IFCI 10:30 +1.47

    13 trades, all winners (hindsight), NET Rs 94,128 = 94% of capital by 11:00.

Per stock with the full lakh each (8 accounts, NOT a real book): 309,685 --
XTRANET 76,071, RML 63,735, JINDWORLD 42,242, MARSONS 42,027, NIACL 30,162,
LALITHAA 26,245, IFCI 24,824, HEG 4,377 (HEG had a 1.4% range all morning).

CODE, same window, same one book, forward-only: Rs 16,269. 17% of the human.

AND THE GAP IS NOT WHAT I ASSUMED. Not sizing -- human slot 1.67L, code position
1.5L. Not exits -- the code held RML 10:31->11:00 for +10.9% against the human's
+11.27%. It is WHICH NAMES SIT IN THE THREE SLOTS: at the moments the human held
XTRANET, RML, MARSONS and JINDWORLD, the code held BIRLAPREC, KOPRAN, IKIO,
ROSSTECH and SKYWAYS. Same rules, same money, same window, different stocks.

Ranking is the whole game.

## 05-Sep -- LOSER AUTOPSY: winners and losers are identical at entry

Sri: "why cant you try to understand the root cause of losers?"

67 trades, 04-Sep, honest forward-only book. 30 winners, 37 losers. Every
entry-time feature measured at the decision bar:

    feature          winners   losers   w median  l median  overlap
    volume multiple    25.92    23.07      4.75      4.62      97%
    % above VWAP        1.30     1.35      1.15      1.25      96%
    % from open         4.41     4.84      4.62      4.63      97%
    entry block range   1.50     1.10      0.82      0.72      96%
    day range so far    6.01     6.19      5.74      5.77      94%
    RSI                74.85    73.00     70.08     73.47      96%
    day % on Board      6.00     5.90      5.88      5.76      96%
    time of day        11.40    11.97     10.92     11.80      99%
    price             773.73   726.82    219.46    279.70      97%

94-99% overlap on every one. At the moment money moves, a winner and a loser
look the same. There is no filter to write.

TWO FIXES TRIED AND MEASURED HARMFUL:
* QUICK CUT (out if underwater 90-240s and never got 0.3% ahead). It does what
  it says -- average loss falls from -0.76% to -0.53% -- and the book falls from
  28,967 to 1,827/12,753/15,017/6,263. It cuts the winners too: every big winner
  was also flat or negative in its first two minutes. `QUICK_SEC` left at 0.
* SPEED-SCALED TRAIL (giveback = 5x the stock's own average candle range).
  Holds XTRANET to +12.65% instead of +4.67% -- exactly what the eye did -- and
  the book finishes at 28,976 vs 28,967. Zero-sum. `TRAIL_SPEED` left at 0.

WHAT THIS MEANS: the losers are not a defect to be removed, they are the price
of the winners. The system makes money on a 45% win rate because the winners are
larger. The lever is therefore the SIZE OF THE WINNERS, not the count of losers.

AND: 67 trades on ONE session is too small to find a separator even if one
exists. Every number tuned today was fitted to 04-Sep. Next step is 02-Sep and
03-Sep, untouched, as a check.

## 07-Sep evening -- THE INDICATOR BAKE-OFF, AND WHAT IT ACTUALLY PROVED

Sri caught a real bug: my AlphaTrend fired 31 times on PWL where his chart shows
5. I had coded "price crosses the line". The published script compares the LINE
WITH ITSELF TWO BARS BACK -- crossover(AlphaTrend, AlphaTrend[2]) -- and uses the
Money Flow Index, not the up-volume share I substituted. Both fixed. The
replication now matches his chart trade for trade:

    my flips   10:11 SELL, 10:54 BUY, 11:34 SELL
    his chart  10:11 SELL, 10:53 BUY, 11:34 SELL
    (his 09:16 BUY is the line already pointing up at the open -- now seeded;
     his 12:42 BUY is missing because our PWL feed stops at 12:38:30)

To stop this class of bug recurring, every indicator was rewritten in dirlib.py
as ONE direction line, +1/-1 per candle. Long, short and always-in modes are all
derived from it mechanically, so there is no hand-written signal left to get
wrong and long/short are guaranteed mirrors.

### 13 indicators, two days, three universes

TOP 10 HINDSIGHT LIST (long the bullish 10, short the bearish 10), avg % a stock:
    UT Bot 37.7 / 37.3   LinReg Candles 32.0 / 31.0   WaveTrend 38.2 / 28.6
    SSL 30.9 / 25.8      Chandelier 26.3 / 17.6       AlphaTrend 14.7 / 8.0
Everything looks superb.

EVERY STOCK IN THE SESSION, one book, 3 slots:
    every single indicator LOSES on 04-Sep. Best worst-day: Jurik MA -Rs 5.
    UT Bot -11,508. SSL -7,721. AlphaTrend -4,280. LinReg -7,654.

EVERY STOCK, ITS OWN BOOK (no slots, no selection, avg % per stock):
    Volume expansion 3x   +0.60% / +0.37%   332 / 813 trades   <-- ONLY survivor
    WaveTrend             +1.86% / -1.34%   2800 / 4847
    Chandelier            +0.70% / -1.46%   1867 / 3620
    LinReg Candles        +2.20% / -1.50%   2669 / 4675
    UT Bot                +1.00% / -1.72%   2890 / 5037
    SuperTrend            +0.71% / -1.74%   1293 / 2903
    AlphaTrend            -0.20% / -1.87%   1145 / 2518
    SSL Channel           +0.51% / -2.07%   2322 / 4353

### THE CONCLUSION, AND IT IS THE WHOLE PROJECT IN ONE LINE
NO INDICATOR HAS AN EDGE. The entire apparent edge -- AlphaTrend's 86,393, the
37% a stock, all of it -- came from being handed the day's ten biggest movers,
chosen after the fact. Point any of them at the real market and they churn 2,000
to 5,000 trades and hand the profit to charges.

The one thing positive on both days is the volume-expansion rule, and the reason
is visible in the trade count: 332 trades where UT Bot takes 2,890. It is not
better at reading a trend. It is better at STAYING OUT.

So the search for a better indicator is over, and it answers the question that
has run through this whole project: the code loses to the eye because of WHICH
STOCKS IT IS IN, not what it does once it is in them. Selection is the edge.
Every hour spent on entry rules has been an hour spent on the wrong half.
