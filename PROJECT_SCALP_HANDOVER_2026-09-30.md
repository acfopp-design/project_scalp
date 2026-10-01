# Project_Scalp — full handover

**Written:** 30 Sep 2026 · **Updated:** 1 Oct 2026 (day 2 — see §11b) · **For:** any new session or model taking this over
**Machine:** `C:\Project_Scalp` (Windows) · **Owner:** Sri · **Broker/data:** Dhan

---

# 0. Read this first — the one thing that matters

**Every performance number this project produced before 30 Sep 2026 is void.**

Not "approximate". Void. The engine was reading the future in two separate
places, and one of them produced roughly 80% of all reported profit. If you
find an old document, a chart, a `versions/` snapshot or a comment quoting a
P&L figure, assume it is fiction until re-measured.

There is exactly **one honest day of data** (30-Sep, from 10:03 onward) and it
shows a far smaller, far less certain edge than anything previously claimed.

The second thing that matters: this project's recurring failure mode is
**concluding from one day**. It has happened at least four times. Do not do it.

---

# 1. What the system is

An intraday scalping engine for NSE India equities.

- 30-second candles, 2 concurrent positions, notional ₹2.5 lakh per slot
  (₹1 lakh capital at 5x leverage)
- Entry window 09:15–14:00, hard square-off 15:05 (Dhan force-closes ~15:10)
- Universe: Dhan's "shocker" lists (price movers, volume movers, intraday
  movers) plus Sri's watchlist, filtered to a "no-dip" list
- Exits: −1% stop, 1.2% trail off peak, an adaptive "eye exit", circuit guard,
  square-off, and eviction ("displaced by X")
- Paper trading is the default. Live trading works and is currently **paused**.

**Two engines exist. Do not confuse them.**
`paper_live.py` is the active engine. `live_paper.py` is legacy and idle.
The names are nearly identical and this has cost time before.

---

# 2. The look-ahead problem — history and current status

## 2.1 What "look-ahead" means here

`paper_live.run_book()` is a **stateless replay**. Every 20 seconds it re-reads
the whole session from the open and rebuilds its decisions from scratch. That
is deliberate and makes the engine reproducible.

The danger is that at replay-time `t`, the code has the *whole* tape in memory —
including bars after `t`. Any decision that touches data after `t` is cheating,
and it is very easy to do by accident.

## 2.2 Defect 1 — `MIN_LEG` (found 28-Sep, fixed)

Entry was gated on `j - i < MIN_LEG` where `j` is the bar the trade *ends* on.
Fixed by `MIN_LEG = 0`. `causality_audit.py` guards against reintroduction, and
pre-flight fails if `MIN_LEG != 0`.

## 2.3 Defect 2 — the watchlist availability override (found 30-Sep, fixed)

`funnel.build()` contained:

```python
for s in wl:
    if s in tape:
        av[s] = min(av.get(s, "99:99:99"), "09:15:00")
```

That `min()` overwrote each name's real discovery time with a flat 09:15:00.
When a stock's bars arrived late, the replay was told it could have traded it
since the open, and entered at a price from before it knew the stock existed.

Measured on 30-Sep — four of six trades:

| Stock | Flagged available | "Entered" | Hindsight |
|---|---|---|---|
| NAZARA | 09:19:56 | 09:15:30 | 4m 26s |
| GANDHAR | 09:22:18 | 09:15:30 | 6m 48s |
| WELSPUNLIV | 09:28:29 | 09:17:00 | 11m 29s |
| ASTEC | 09:33:02 | 09:16:00 | 17m 02s |

**Fix:** `funnel.first_seen()` persists, per symbol per day, the wall-clock
moment the engine first held that symbol's bars, in
`logs/FIRST_SEEN_<day>.json`. Availability is now
`max(flag_time, first_seen)`. The record is written once and never revised.

**Measured impact: small.** Re-running 25/28/29-Sep with and without the
override changed P&L by −1%, −13% and +4%. I initially called this "the
residual contamination" and that was an overstatement based on one day.

## 2.4 Defect 3 — the eviction rule (found 30-Sep, fixed) — THE BIG ONE

Inside `run_book`:

```python
rem = pos["eye_out"] - i_now            # "bars this leg has left"
if v >= rem * DISPLACE and (ceout - ci) >= MIN_LEG_DISPLACE:
```

`eye_out` and `ceout` are the bar indices where those legs **end**. At
replay-time `t` the engine chose which position to evict by comparing *how much
future each one had left*. Same defect family as `MIN_LEG`, in the eviction gate
instead of the entry gate.

This mattered enormously: on 30-Sep, **53 of 60 exits were evictions**, and they
produced **+₹100,571 of the day's +₹122,786**.

**Proof, same tape, four settings (causal engine otherwise):**

| Setting | Trades | Win% | Net |
|---|---|---|---|
| Legacy 1.5 (reads the future) | 62 | **73%** | **+21,977** |
| Causal 1.5 (honest) | 67 | 42% | +4,406 |
| Causal 1.2 (honest) | 81 | 38% | +7,714 |
| Eviction off entirely | 72 | 39% | +5,849 |

All three honest settings land in the same place. **Honest eviction is worth
approximately nothing** — the entire contribution was the look-ahead.

**Fix:** `DISPLACE_MODE = "causal"` (now the default) evicts only a position
that is **currently losing**, and only if the newcomer's selection score is at
least `DISPLACE ×` the evicted position's score at its own entry. Both inputs
are knowable at time `t`. `DISPLACE_MODE = "legacy"` still exists to reproduce
old numbers — never trade on it.

## 2.5 Defect 4 — THE BINDING CONSTRAINT: the engine waits for Dhan's board

Found late on 30-Sep, and it is bigger than anything above.

**The engine can only trade a stock once Dhan's board has flagged it**, and
never before. Dhan flags a stock *after* it has moved enough to rank, so the
engine is structurally late by design. `funnel.build()` sets availability from
the board's first sighting; everything before that is invisible.

Two verified cases from 30-Sep, both stocks the engine never traded at all:

| Stock | Own signals (score) | Board flagged it | Move missed |
|---|---|---|---|
| TVSELECT | 10:13 (1.76), 10:15 (2.29), 10:17 (2.47) | **10:20:32** | 389 → 407, day +17.9% |
| VALIANTORG | 10:07 (2.38), 10:10 (2.55) | **10:13:19** | 419 → 455, day +14.0% |

The signal logic was right every time. It simply was not allowed to look. Sri's
own blind eye-test called entries at 10:06/10:08 on VALIANTORG and 10:14/10:51
on TVSELECT — the engine had matching signals and could act on none of them.

Across those two stocks, **31 valid signals** fired and **zero** trades were
taken. The board's own code comments already documented the pattern:
*"EMMVEE flagged 09:22:11 at +2.71%, on board 09:29:41 at +5.14% with 80% of
the move gone."*

**Fix, built 30-Sep: `surge.py`** — see §4.3. Off by default.

## 2.6 What is NOT contaminated

The **selection score itself is causal.** `volq` and `rngq` are computed with
`slice(st, i+1)` — only bars up to `i`. `keep` uses a trailing 40-bar window.
Verified 30-Sep.

---

# 3. Live trading — built 29-Sep, first traded 30-Sep

## 3.1 Result of the first live day

₹500 in the wallet, `LIVE_MAX_QTY=1`, `LIVE_MAX_ENTRY_VALUE=400`.

| Stock | Qty | Buy | Sell | P&L |
|---|---|---|---|---|
| Delta Corp | 1 | 80.89 | 81.31 | +0.42 |
| Ujjivan SFB | 2 | 67.51 | 67.23 | −0.56 |
| Union Bank | 1 | 172.02 | 171.67 | −0.35 |
| Texmaco Rail | 1 | 132.50 | 132.01 | −0.49 |
| | | | **Realised** | **−₹0.98** |

About −₹1.30 after charges. **The pipe works end to end:** order out, fill back,
position visible in Dhan, exit, flat. Entry fills matched our log to the paisa.

## 3.2 Architecture — why execution is a reconciler

`run_book()` is stateless, so calling `broker.place()` inside it would re-fire
every order every 20 seconds. Instead:

```
run_book()   ->  what SHOULD be open now   (rebuilt each cycle)
the ledger   ->  what IS open at Dhan      (logs/LIVE_LEDGER_<day>.json)
reconcile()  ->  issues only the difference
```

This buys three things: no duplicate orders; a restart resumes instead of
re-entering; and a manual exit in the Dhan app is detected and the slot released.

**State machine:** `ENTRY_SENT → OPEN → EXIT_SENT → CLOSED`, plus
`STOPPED_BY_BROKER`, `GONE_AT_BROKER`, `ENTRY_FAILED`.

## 3.3 Three broker bugs found by real money on day one

None were catchable by the nine mock tests — those passed against a fake broker
that accepted whatever was sent.

**`DH-905 Invalid correlationId`.** The order tag was built from the ledger key,
`EUJJIVANSFB|09:24:00`. Dhan accepts alphanumerics only. Killed the first
routable order outright. Fixed by `broker._corr()`, which strips to
alphanumerics, max 20 chars.

**`DH-906 Trigger Price should be greater than Price`.** Dhan rejects `price=0`
on `STOP_LOSS_MARKET`. The first protective stop ever sent failed, leaving a
position unprotected for 80 seconds. Fixed: `broker.place_sl()` falls back to
`STOP_LOSS` with a limit placed `LIMIT_BUF = 0.60%` beyond the trigger.

**Orphaned exits — the severe one.** `_close()` set `state = CLOSED` when Dhan
*accepted* the exit order. Acceptance is not a fill. A SELL limit at 67.53 rested
unfilled while price fell to 67.16; the stop had already been cancelled, so a
real position sat on the account with no stop, no manager and no exit. Sri found
it on his own screen. Fixed: `EXIT_SENT` state, `_poll_exit()` chases the fill
through a `CROSS_LADDER = (0.30, 1.00, 2.50)`, and if the ladder is exhausted the
position is **re-protected and held** rather than left naked, with
`EXIT_COOLDOWN = 3` cycles before retrying.

## 3.4 Two safety rules that came out of this

**Never read a failed API call as "flat".** `broker.positions()` returns
`{ok: False, rows: []}` on failure. Reading `rows` without checking `ok` reported
an empty account three times while two positions were open. `_dhan_open_syms()`
now returns `None` (unknown) rather than an empty set, and the reconciler skips
that cycle. **From the Cowork cloud container, `api.dhan.co` is blocked by a
proxy 403 — remote diagnostics of live state are unreliable. Use the Chrome
extension against `web.dhan.co` instead.**

**The launcher lock.** Live now needs two independent things: `BROKER_MODE=LIVE`
in `env.txt` **and** `SCALP_LIVE_OK=1` in the process environment, which only the
live branch of `SCALP.bat` sets. A stale `BROKER_MODE=LIVE` cannot produce a real
order on its own, and a double-click can never go live. Failure direction is
one-way: the lock can only downgrade to paper.

---

# 4. Data pipeline

## 4.1 The stages

| # | Stage | File | Known weakness |
|---|---|---|---|
| 1 | Universe | `Movers_app.py` | refreshes on its own clock |
| 2 | Watchlist | `nodip_watchlist()` | nothing else is tradeable |
| 3 | Bar fetch | `live_shadow.py`, `quote_feed.py` | see 4.2 |
| 4 | Eligibility | `funnel.py` | fixed 30-Sep (§2.3) |
| 5 | Signal | `eye_strategy.py` | thresholds unvalidated |
| 6 | Sizing | `leverage.py` | paper sizes by capital, live caps at 1 share |
| 7 | Decision | `paper_live.run_book()` | stateless replay, re-decides every 20s |
| 8 | Routing | `live_exec.reconcile()` | the only place orders are born |
| 9 | Order | `broker.py` | LIMIT only, crossing 0.30% |
| 10 | Fill | Dhan | exits can rest unfilled |
| 11 | Protection | `broker.place_sl()` | SL-M, falls back to stop-limit |
| 12 | Truth check | `_dhan_open_syms()` | fixed 30-Sep |
| 13 | Close | `_poll_exit()` | fixed 30-Sep |
| 14 | Square-off | 15:05 / Dhan ~15:10 | two independent backstops |

## 4.2 The data-lag problem and what was done

At the open on 30-Sep the funnel held **zero names until 09:18:06** while the
board booked entries at 09:15:30. Causes:

- `FETCH_EVERY = 90` seconds between sweeps
- a sweep took 43–74 seconds for ~150 names, **fetched one symbol at a time**
- the 09:15:04 sweep ran before the first 30s bar had closed, found nothing, and
  then slept the full 90 seconds

Measured staleness: median 68s, worst ~180s.

**Three fixes, in order of value:**

1. **`quote_feed.py` (new).** Dhan's docs: *"You can fetch upto 1000 instruments
   in single API request with rate limit of 1 request per second"*. The engine
   was making ~150 separate requests. `quote_feed` makes **one bulk
   `/v2/marketfeed/quote` call per second** for the whole watchlist and builds
   its own 30-second bars into `logs/tape_fast/<day>/`, in exactly the columnar
   shape `live_shadow.load_live()` already reads. Dhan's official candles stay
   authoritative; the fast bars only fill timestamps the sweep has not reached.
   **Volume is stored as a delta between readings, not the cumulative day total**
   — a cumulative first bar would score a false 1.0 volume percentile and invent
   a signal.
2. **Adaptive sweep interval.** `live_shadow.fetch_interval()` — 15s before
   09:25, 20s after an empty sweep, otherwise `FETCH_EVERY`.
3. **Hot/rest split.** Held positions plus the `HOT_FRESH = 20` most recently
   flagged names are swept every cycle (18 names in ~5s); the long tail every
   `REST_EVERY = 4` sweeps. Measured after: hot names at **20s** lag, tail at
   170s (harmless — a tail name becomes hot the moment Dhan flags it).

**`quote_feed` has never run against the live API.** This VM cannot reach
`api.dhan.co`. Bucketing and the merge are unit-tested offline; the network path
gets its first real exercise at 09:15 on 1-Oct. Watch for `quote_feed: started`
in the engine log and a non-zero funnel within a minute of the open.

## 4.3 `surge.py` — finding the move ourselves (new, OFF by default)

The answer to §2.5. Dhan allows 1000 instruments per `/marketfeed/quote`
request at 1/sec, and the NSE equity universe is ~2000 names — so the **whole
market can be scanned every 2 seconds from two requests**. `surge.py` does
that, ranks every stock against **its own** behaviour, and writes the movers to
`logs/SURGE_<day>.json`.

```
vol_rate   volume/second now vs this stock's own median rate today   (VOL_X = 3.0)
day_move   % from today's open                                       (MIN_MOVE = 1.0)
tover      rupees traded in the window, to skip illiquid names       (MIN_TOVER = 300000)
score      volx * sqrt(abs(move))
```

Judging each stock against itself means a ₹50 and a ₹5000 stock are treated
identically — the same principle as the selection score.

**Causality is preserved.** The moment we first flag a name is written once,
never revised, into the same `logs/FIRST_SEEN_<day>.json` the engine already
honours. Widening the universe therefore cannot reintroduce look-ahead.

**Wiring, both additive:**
- `paper_live.tradeable(day)` = `nodip_watchlist(day)` **∪** `surge.names(day)`.
  Every `nodip_watchlist()` call site now goes through it.
- `Movers_app.cycle()` promotes surge flags alongside its existing
  `early_movers()` path, tagged `lists: ["Surge"]`, so they get cards and the
  board stops lagging too.

With `SURGE_SCAN=NO` neither fires and behaviour is exactly as before.

**Untested against the live API** — offline unit tests only (a quiet stock is
correctly ignored; a stock at 40× its own volume rate and +3% flags with score
69.3). First real run is 1-Oct.

---

# 5. Configuration — every knob that matters

## `paper_live.py`
```
CAPITAL, LEVERAGE = 100_000.0, 5.0    board boxes override
STOP_PCT, TRAIL_PCT = -1.0, 1.2       TRAIL_PCT is a SHARP one-day optimum
TRAIL_ARM = "close"                   "peak" tested, costs ₹1,447, rejected
DISPLACE_MODE = "causal"              "legacy" reads the future — never trade it
DISPLACE = 1.5
EYE_EXIT = True                       it recycles the slots - off = 5 trades/day
MIN_ABOVE_PCT / MIN_SLOPE_PCT / MIN_SEP_PCT = 0   geometry gates, tested+rejected
MIN_LEG_DISPLACE = 12                 legacy path only
COOLDOWN_MIN = 0
EYE_LOGIC = True
SLOTS = 2
OPEN_T, SQUARE_OFF = "09:15:00", "15:05:00"
CIRCUIT_BUF_PCT = 0.50
REFRESH = 20                          engine cycle, seconds
MAX_ENTRY_POS = 100                   100 = off. Tested and REJECTED — see §6.2
HOT_FRESH = 20 · REST_EVERY = 4
```

## `eye_strategy.py`
```
SELECT_MIN = 1.5      score = volq + rngq + min(keep, 1.0), max 3.0
MIN_LEG = 0           MUST stay 0 — pre-flight fails otherwise
LAST_ENTRY = "1400"   14:15–15:00 worth only +2.6%, left alone
SHORTS = True         they DO fire now (VESUVIUS, SUNTV, TVSELECT 30-Sep)
MIN_UP = 0.5 · MAX_OFF = -3.5 · EXITMODE = 'rhythm'
```

## `live_shadow.py` / `quote_feed.py` / `live_exec.py` / `broker.py`
```
FETCH_EVERY = 90 · WORKERS = 5 · MAX_FETCH = 500
BUCKET = 30 · POLL = 1.0
CROSS_LADDER = (0.30, 1.00, 2.50) · EXIT_COOLDOWN = 3
LIMIT_BUF = 0.60
```

## `env.txt` — re-read every cycle, no restart needed
```
DHAN_WHITELIST_IP=49.43.246.104     IPv4 only; api.dhan.co has no AAAA record
BROKER_MODE=LIVE
LIVE_ARMED=YES
LIVE_NO_NEW_ENTRIES=YES             <-- LIVE IS PAUSED. Deliberate.
LIVE_MAX_QTY=1
LIVE_MAX_ENTRY_VALUE=400
LIVE_SL_PCT=-2.5
LIVE_CROSS_PCT=0.30
SURGE_SCAN=NO                       our own whole-market scanner (section 4.3)
```

**Dhan static IP:** portal slot 1 holds an IPv6 address (inert — `api.dhan.co`
publishes no AAAA record), slot 2 holds the IPv4 that matters. Dhan allows a
re-set only **once every 7 days**, so if the IP rotates, stop and tell Sri —
do not touch the portal.

---

# 6. What has been tested and rejected

Do not retry these without new evidence. All are documented in code comments.

## 6.1 Entry filters — five attempts, five failures

| Idea | Result |
|---|---|
| `MIN_EXP` expected-move gate | Inert — scaled ~50x too small |
| `TRAIL_ARM = peak` | −₹1,447 at TRAIL_PCT 1.2 |
| VWAP cross / distance | Winners 1.151% above VWAP, losers 1.158%. No signal. |
| Ichimoku cloud distance gate | Grades outcomes but every threshold loses |
| Trend-flip exit, 6 variants | All worse than the 1.2% trail |

**The pattern:** median loss −₹386, average win ₹2,148. Any gate strict enough
to cut the small losers also cuts the rare large winners, and those winners are
the entire edge — 10 trades carry 69–81% of a day.

## 6.2 Everything tested on the evening of 30-Sep

All on today's tape, causal engine, ₹1 lakh / 2 slots. Baseline **+₹4,406**.

| Test | Result | Verdict |
|---|---|---|
| Entry-height cap (see §6.3) | 80% → +536, 60% → −2,975 | rejected |
| EMA/MA geometry entry gate | .30/.10 → +2,525; .50/.20/.15 → −1,819 | rejected |
| Next-candle prediction from geometry | entry state −0.007%, exit state +0.007% | **backwards** |
| Longer horizons, strong state only | 5bar +0.033%, 10bar +0.069% | real but ≈ cost |
| **Trail 1.2% → 2.0%** | **+₹7,150, win 42% → 48%** | **ADOPT** |
| Trail 3.0% / no trail | +2,415 | worse |
| Eye exit OFF | 5 trades, −₹1,834 | it recycles the slots |
| More slots (implied by BLEL) | disproved — see below | rejected |

**The slot hypothesis was mine and it was wrong.** BLEL was evicted after 60
seconds and then ran +4%, which looked like a slot shortage. But given free
rein on just TVSELECT and VALIANTORG — two slots, two stocks, no competition —
the engine took 23 trades, won 8 (35%), and **lost ₹807**. It shorted 14 times
on two stocks that rose +17.9% and +14.0%. Remove its one good trade (+₹1,889)
and the other 22 lose ₹2,696.

So the problem was never slots, exits, or entry filters. It was §2.5 — the
engine could not see the stocks until their moves were over.

**The IRCON proof.** Legacy (reading the future) entered 11:28 @ 105.51 and
rode to 118.97, **+12.76%**. Causal entered 11:53 @ 118.95 — after the whole
move — for ₹0.01 a share. Same stock, same day, same rules. That one comparison
explains why six entry filters all failed: you cannot filter your way to an
entry only foresight could have found.

## 6.3 Entry-height cap — tested 30-Sep, REJECTED

Sri's own diagnosis of his losses: *"I buy at peak price, sellers take that
opportunity and sell, price suddenly dips and my SL hits."* Measured: the
engine's median entry sits at **83% of the stock's last-20-bar range**, and 21
of 60 entries were in the top 15% — it does exactly what he describes.

So `MAX_ENTRY_POS` was added to refuse high-in-range entries. Result, causal
engine, three days:

| Cap | 30-Sep | 29-Sep | 28-Sep |
|---|---|---|---|
| **100 (off)** | **+4,406** | **+2,239** | **+3,989** |
| 80 | +536 | −5,340 | −4,440 |
| 60 | −2,975 | −10,161 | −2,130 |

Worse every day, worse the harder you cap. **This is a momentum strategy —
buying strength is the edge, not the flaw.** Left at 100 (off).

This test also killed a much larger proposal: building a websocket tick feed to
compute order-flow delta. Delta cannot be backtested (no tick history), so it
would have been weeks of work answered only in November. The cheap proxy said no
three times out of three. Caveat: range position is a *proxy* for "ignition
exhausted", not the same measurement as delta — this weakens the case for order
flow without disproving it.

---

# 7. Where the numbers actually stand

**One honest day.** 30-Sep, 10:03–15:30, causal eviction, ₹1 lakh, 2 slots:
roughly **+₹5,000–8,000, win rate ~39%**.

The shape: **loses about 6 trades in 10, and makes money only because winners
are much larger than losers.** That matches the causal days in the 28-Sep state
doc — coin-flip direction, winners 3.7–5.8× losers.

**Known unresolved discrepancy:** the live board showed +₹122,786 for a window
where an offline replay of the same settings gives +₹21,977. Those should match
and do not. At least one more gap exists. Finding it is high priority.

**Still open from the 28-Sep state doc:** 23% of names non-causal (suspect
`rest75`, built from each stock's first 40 bars); no out-of-sample day; no losing
day ever observed on the honest engine; slippage not in the headline number;
capital tiers show ₹4 lakh earning *less* than ₹2.5 lakh, which is itself a sign
the numbers don't describe a real system.

---

# 8. Operating the system

**One launcher: `SCALP.bat`.** It is the only `.bat` in the folder; 27 others
are parked in `_superseded_bats\`. Double-click runs, with no input:

```
[1/5] Morning check    (prints, waits 10s)
[2/5] Free port 5005
[3/5] Clear bytecode, check deps
[4/5] Pre-flight       (stops here if it fails)
[5/5] Start board + engine   PAPER
```

Other uses: `SCALP.bat live` (needs typing `LIVE` in capitals),
`SCALP.bat status`, `SCALP.bat backup`.

**The board starts and revives everything else** — the trading engine and the
capital-tiers worker. Both write heartbeats and retire themselves when their
source changes; `Movers_app._revive_engine()` / `_revive_tiers()` bring them
back. Nothing else needs launching, and no manual restart should ever be needed.

**Daily:** fresh Dhan access token pasted into `env.txt` before ~08:50 (Sri does
this himself — Claude must never enter tokens or keys).

**Intraday kill switches**, effective next cycle, no restart:
`LIVE_NO_NEW_ENTRIES=YES` (manage and exit what is open, take nothing new) and
`LIVE_ARMED=NO` (hard stop — but positions then keep only their broker stop and
Dhan's square-off, so exit them by hand).

---

# 9. Working with Sri

- **Keep responses to 1–2 lines where possible.** He has said this repeatedly
  and means it. End with a conclusion and next step.
- **He considers himself non-technical for setup.** Click-by-click, never
  "run this command". One icon, not five.
- **Work one step at a time**, not a ten-part plan.
- **Do not wait for permission to fix things** — but **do ask before changing
  `env.txt` or anything that alters live trading behaviour.** I changed
  `LIVE_NO_NEW_ENTRIES` without asking on 30-Sep; he noticed and was right to.
- **Do not compare the engine's performance to his.** He handles one stock at a
  time by necessity; the engine does not. His trades are evidence of what is
  findable, not a ceiling.
- **Never rely on his indicators alone** — his standing instruction is to do
  your own homework and decide whether they hold up.
- **Deletion on his machine is not permitted.** Move files into `_to_delete/`
  or `_superseded_bats/`; never `rm`.
- **No stock names hardcoded in the code.** Standing rule.
- **Don't re-run old days' tests unprompted** — he has asked for this twice.
- He is token-conscious. Do not burn context on ceremony.

---

# 10. Traps that have already cost time

1. **`paper_live.py` vs `live_paper.py`** — the first trades, the second is dead.
2. **Parentheses inside `.bat` `if (...)` blocks** broke `EYE_START.bat` on
   21-Sep. Keep them out of `REM` lines too.
3. **Python caches modules.** Editing `live_exec.py` mid-session does nothing
   until the engine restarts. Touch `paper_live.py` to force a clean retire.
4. **Replaying an old day used to stamp `FIRST_SEEN` with the current clock**,
   silently returning zero trades. Guarded 30-Sep — `first_seen()` only records
   when `day == today`. If a backtest returns zero trades, check this first.
5. **`broker.status()` returns `None` for completed orders.** It works at entry
   time. Do not rely on it to confirm an exit fill after the fact — the ledger
   still has no `exit_fill` for closed trades, which is an open gap.
6. **The board's rule text is wrong.** It says *"Enter when HOTT/LOTT is in
   UPTREND"*. `HOTT`/`LOTT` and the consolidation zone appear **zero times** in
   `eye_strategy.py`, `tune_v6.py` and `eye_cfg.py`. They exist only in the
   board's display code. Sri has been reading a description of something the
   decision code does not contain.
7. **Shared scratch paths are poison.** `analyse()` wrote `pl_<sym>.csv` for
   every process; two engines racing on it produced a `broadcast` ValueError
   that looked like a maths bug. Fixed with a pid suffix. If you see that
   error again, hunt for another shared temp path before anything else.
8. **Three parameter changes have now been withdrawn after a second day**
   (trail 2.0, SHORTCUM 0, more slots). Never adopt a knob on one day's
   evidence, however large the improvement looks.
9. **The tiers tab and the main tab will differ by ~20s** — two independent
   processes on different cycles. That is benign. A 44-minute gap is not.

---

# 11. What I would do next, in order

1. **Turn on `SURGE_SCAN=YES` and run one paper day.** This is the only change
   that addresses the binding constraint (§2.5). Everything else we tested on
   30-Sep failed. Watch for `surge scanner ON` in the log and check
   `python surge.py` after the close — did it flag TVSELECT-type names *before*
   the board did?
2. **Set `TRAIL_PCT = 2.0`.** The one parameter change with evidence behind it
   (+₹7,150 vs +₹4,406). One number, so tomorrow's result is interpretable.
3. **Collect 4–5 clean days after that. Change nothing else.** Every conclusion
   here rests on a single day.
4. **Find the +₹122,786 vs +₹21,977 discrepancy** (§7). A live board and an
   offline replay of the same settings must agree.
5. **Record exit fills** (`exit_fill` in the ledger). Without it live P&L is
   unknowable — it caused a real reporting error on 30-Sep where two trades
   were called losses when the day was in fact +₹0.21.
6. **Re-validate the remaining knobs causally** once ≥3 honest days exist:
   `SELECT_MIN` 1.5, `SLOTS` 2, `LAST_ENTRY` 1400, `MIN_UP`, `MAX_OFF`.
7. **Fix the 23% non-causal names** (suspect `rest75`).

**Do not raise live size above 1 share until items 3–5 are done.**

**And do not add another indicator or threshold.** Nine separate filters and
parameters have now been tested and rejected. The evidence says the constraint
is *what the engine is allowed to see*, not how it decides.

---

# 11b. Day 2 — 1 Oct 2026

First full paper day with the surge scanner on. `BROKER_MODE=PAPER`,
`LIVE_ARMED=NO` — live is paused until Sri says otherwise.

## The scanner works

| | |
|---|---|
| Surge flagged | 99 names by 09:37 |
| **Board never flagged** | **89** — invisible to the old engine |
| We were earlier | 3 (ENRIN, ELECTCAST by **3m 07s**) |
| Board earlier | 7 |
| Tradeable universe | **35 board badges → 130** |

Wired as a union, not a replacement — the board still beat us on 7 names.

## Three real bugs found and fixed

**1. Shared scratch file — a production race.** `eye_strategy.analyse()` wrote
`logs/_eye_scratch/pl_<sym>.csv`, a path shared by every process. `paper_live`
and `paper_tiers` call `analyse()` on the same symbols at the same time, so one
overwrote the other's CSV mid-read and `V6.prep` returned a different row count
than `bars` had:

```
ValueError: operands could not be broadcast together with shapes (747,) (815,)
```

That cost the live engine a whole cycle at 09:57 on 30-Sep. Now
`pl_<sym>_<pid>.csv`, plus a length guard that returns blank rather than
raising into the engine. **If this error ever reappears, look for another
shared scratch path.**

**2. The scanner was blind for the first 2½ minutes.** It needs
`WARM_SAMPLES = 5` readings to build each stock's volume baseline, so its first
flags only appeared at 09:17:34 — and the opening is where the sharpest moves
are. Measured the same morning: TARIL had a valid signal at **09:18:00, score
2.86, price 287.00**, blocked because `FIRST_SEEN` was recorded 09:19:04. It
entered at 293.40 instead. Six rupees lost to our own warm-up.

*Opening mode* added: before the baseline exists, judge on absolutes that need
no history — `OPEN_MOVE = 2.0%` from the open on `OPEN_TOVER = ₹1 crore` of
turnover. Replayed against the real tape:

| | before | after |
|---|---|---|
| TARIL | 09:19:04 | **09:16:00 @ 278.00** |
| PACEDIGITK | 09:18:40 | **09:15:00 @ 169.78** |

**3. The watchlist was fetched late at the open.** KARAMTARA: the board flagged
it at **09:15:00**, our tape held no bars for it until **09:18:40**. Its best
signal of the day — score 3.00 at **395.90** — was blocked, and it entered at
**417.10**. ₹21 lost to our own fetch order, not to Dhan. Before 09:25 the
engine now sweeps the entire watchlist every pass; the hot/rest split resumes
afterwards.

## Four parameter changes tested — ALL REJECTED

Baseline: causal eviction, 2 slots, trail 1.2, SHORTCUM 6.

| Change | 30-Sep | 29-Sep | 28-Sep | Verdict |
|---|---|---|---|---|
| Slots 2 → 3 | +2,439 vs **4,406** | — | — | no |
| Slots 2 → 5 | +3,173 vs **4,406** | — | — | no (174 trades for less money) |
| SHORTCUM 6 → 0 | **+9,214** | +810 vs **2,239** | +2,096 vs **3,989** | **no — 1 of 3** |
| Trail 1.2 → 2.0 | +7,150 | +6,654 | **−1,494** vs 3,989 | **no — coin flip** |

**Two retractions worth remembering:**

*The slot hypothesis was mine and it was wrong — twice.* BLEL evicted after 60
seconds then running +4% looked like a slot shortage; given 2 slots and only 2
stocks it still lost ₹807. Then the sweep above confirmed more slots is worse.

*Trail 2.0 was recommended on 30-Sep alone and is withdrawn.* Across three days
it wins twice and loses ₹5,483 on the third. That is exactly the one-day
conclusion this project keeps making. **Trail stays at 1.2.**

*SHORTCUM 0 looked spectacular on one day (+9,214, win rate 54% — the only
thing that ever pushed win rate above 50%) and failed on both others.* It also
matches the 23-Sep walk-forward, which already found shorts losing at every
slot count. **Stays at 6.** The rule only shorts an exhausted up-move, never
weakness — which is why MRPL's clean 166.50 → 162.16 dip was never signalled.
That came from Sri's own book: every short he marked was a fade of a spike.

**Net for the day: three genuine bugs gone, zero parameters changed.**

## Still open after day 2

- The `+₹122,786` vs `+₹21,977` discrepancy (§7) is still unexplained.
- `exit_fill` still not recorded in the live ledger.
- The journal is new and has survived one restart (13 trades, +₹5,962) — watch
  that it keeps matching what the board shows.

# 12. File inventory — what changed on 30-Sep

| File | Change |
|---|---|
| `funnel.py` | `first_seen()` + causality gate; today-only guard |
| `paper_live.py` | causal eviction; `live_exec` hook; adaptive fetch; hot/rest split; `MAX_ENTRY_POS` gate; `quote_feed` start |
| `live_exec.py` | **new 29-Sep**, heavily revised: `EXIT_SENT`, `_poll_exit`, `_drop_sl`, `_dhan_open_syms`, mode announcement, `show()` |
| `broker.py` | `place_sl()`, `_corr()`, stop-limit fallback, launcher lock (`mode()` / `configured_mode()`) |
| `live_shadow.py` | parallel fetch, `fetch_interval()`, fast-feed overlay |
| `quote_feed.py` | **new** — 1/sec bulk quote → own 30s bars |
| `surge.py` | **new** — whole-universe scanner, + opening mode (1-Oct). `SURGE_SCAN=YES` |
| `paper_tiers.py` | honours the board reset; self-retire; heartbeat |
| `Movers_app.py` | `_revive_tiers()`, called first so early returns can't skip it |
| `eye_preflight.py` | LIVE sanity block; IP whitelist check |
| `morning_check.py` | **new** — read-only pre-open check |
| `SCALP.bat` | **new** — the single launcher |
| `honest_replay.py`, `displace_test.py`, `range_pos_test.py` | **new** — read-only measurement harnesses |

Backups follow the project convention `<file>.bak_before_<reason>_<date>`.
Every change on 30-Sep has one.

---

# 13. Related project documents

- `claude/live-day-1-findings-and-two-fixes-2026-09-30.md`
- `claude/live-day-1-fix3-orphaned-exits-2026-09-30.md`
- `claude/going-live-runbook-2026-09-29.md`
- `claude/disaster-restore-kit-2026-09-29.md`
- `claude/state-and-open-items-2026-09-28.md` — still the best summary of open
  strategy questions, but **its P&L figures are void**
- `claude/CRITICAL-lookahead-bias-2026-09-28.md`
- `claude/human-eye-logic-2026-09-21.md`, `claude/human-eye-exit-audit-2026-09-27.md`

---

# 14. Closing note for whoever picks this up

The engineering is in reasonable shape. Orders reach the exchange correctly,
positions are protected, the system recovers from crashes and restarts, and the
data is now roughly a second old instead of a minute.

What is *not* established is whether the strategy makes money. One honest day
suggests a modest edge with a 39% win rate carried by rare large winners. That
could be real, or it could be one lucky Tuesday. Nothing in this repository yet
distinguishes those two possibilities.

The temptation will be to add a signal, tune a threshold, or build the order-flow
feed. Resist it until there are enough honest days to measure against. This
project has spent a week producing confident numbers that turned out to be
fiction, and the cause every time was measuring before the measurement itself was
trustworthy.
