# HANDOVER — Project_Scalp

**Written 08-Sep-2026, ~10:15 IST, mid-session.** Read this before touching anything.

---

## 1. Who and what

Sri trades **NSE India intraday, long only**, through Dhan, with a local Flask board
at `C:\Project_Scalp` on port **5005**. Capital **₹1,00,000 at 5× leverage** =
₹5,00,000 of buying power. Everything below is **paper trading**. No order has
ever been placed.

The workspace runs on his Windows machine. A cloud session reaches it through the
remote-devices bridge; `device_bash` runs in a Linux VM with `C:\Project_Scalp`
mounted at `$HOME/mnt/Project_Scalp`. **That VM is UTC — always prefix analysis
with `TZ=Asia/Kolkata`** or every time comparison silently breaks.

---

## 2. What is running right now

One `.bat` starts everything: **`SUPERVISOR_START.bat`**.

```
SUPERVISOR.py
   ├── Movers_app.py      the board on :5005 (restarts itself when its source changes)
   └── paper_live.py      the paper-trading engine (09:10–15:31)
```

**The chain is self-healing.** Three independent mechanisms, added today:

- `paper_live.py` exits when **its own source file changes** on disk.
- `SUPERVISOR.shadow_keepalive()` restarts it whenever it dies.
- `Movers_app._revive_engine()` spawns one at board boot if the heartbeat
  (`logs/paper_live.pid`, rewritten every cycle) is older than 180 s **or** the
  boot stamp (`logs/paper_live.boot`) is older than `paper_live.py`'s mtime.

**Practical consequence: to deploy a change to the engine, just edit
`paper_live.py`. Do not ask Sri to restart the .bat.** It hands over in ~20 s.
Editing `Movers_app.py` likewise restarts the board by itself.

---

## 3. The live strategy (as Sri approved it this morning)

`paper_live.py`, all constants at the top of the file:

| | value |
|---|---|
| Signal | **HOTT/LOTT + Pullback ALT**, `combos2.combined(bars, pullback_alt)` |
| Slots | **5**, ₹1,00,000 each |
| Stop / trail | −1% / 1.2% off the peak |
| Square-off | 15:15 |
| `MIN_DAY_PCT` | **0.0 — OFF** (was 2.0) |
| `MIN_VOLX` | **0.0 — OFF** (was 1.5) |
| `WARM_MIN` | 55 bars of history before an entry is allowed |
| Cycle | 20 s; Dhan fetch every 90 s |

Sri, 09:52: *"Change the LOGIC completely. Use only the indicator we discussed
yesterday night."* Both filters are therefore **off by his instruction**. Volume
expansion still **ranks** candidates when more than five fire at once — that is an
ordering, not a filter, and nothing is refused because of it. **Do not re-add
filters without asking him.**

### Universe — `funnel.py`

Union of three sources, each gated to the moment it actually appeared
(a stock is untradeable before its first-seen time — no hindsight):

1. **Super Stocks cards** — `logs/movers_board/super_<day>.jsonl` (added today)
2. **Dhan shocker panels** — `logs/movers_board/board_<day>.jsonl`
   ("By Volumes" ≥ ₹20; "Price Movers"/"Intraday Movers" ≥ ₹0.5 Cr turnover)
3. **MyWatchlist** — `logs/mywatchlist.json`, 414 names

### Control

`logs/paper_control.json` — written by the board's Start/Stop/Clear buttons:

```json
{"active": true, "started": "09:58:40", "stopped": null,
 "reset_at": "09:56:40", "capital": 100000.0, "leverage": 5.0, "target": 10.0}
```

The engine **only opens trades at or after `started`.** Pressing Start at 09:58
does not back-fill the morning. Outputs: `logs/paper_live_<day>.json` (what the
board reads) and `logs/paper_live_<day>.log` (every decision).

---

## 4. Today's numbers

**Backtest** — pure indicator, funnel + Super Stocks universe, 09:16–10:30,
₹5,00,000 split N ways:

| day | 1 slot | 3 slots | 5 slots |
|---|---|---|---|
| 03-Sep | 11,811 | 11,521 | **13,959** |
| 04-Sep | 2,637 | 4,087 | **7,850** |
| 07-Sep | 571 | −461 | −1,626 |
| **total** | 15,019 | 15,146 | **20,183** |

With the two filters ON (the config that ran 09:39–09:52) it was
1/3/5 = **8,326 / 21,314 / 12,252** — 3 slots best, 1 slot worst on every day.

**Live, 08-Sep at 10:09** (started 09:58:40): 4 closed, all −1% stops
(XTRANET, RAYMOND, NOVARTIND, +1); holding ONESOURCE, EMMVEE, KRBL, ZYDUSWELL,
ANUHPHR; **net −₹3,498**.

Run it yourself: `TZ=Asia/Kolkata python slot_study.py` (~100 s).

---

## 5. ⚠ OPEN — READ THIS FIRST

### 5.1 The ₹29,698 number is WITHDRAWN
Last night's headline — *07-Sep, one position, 09:16–10:30, HOTT/LOTT + Pullback
ALT, ₹29,698* — **does not reproduce.** The same day, same rule now gives **+571**
(1 slot, no filters) and **−3,010** (with filters). That is not a rounding
difference; one of the two runs was reading a different tape or a different rule.
**Until the cause is found, no decision should rest on that number, and every
figure derived from it is suspect** — including "1 position ₹29,698 vs 5 positions
₹9,565", which is what made the engine single-slot in the first place.
Likeliest culprits: `live_shadow`-fetched bars vs the `logs/tape` cache, a
different `UPTO`, or a different `funnel.build` result. **Not yet investigated.**

### 5.2 Start behaviour is unresolved
The engine ignores every flip that happened before Start. From a 09:58 press that
meant a cold start; the same rule counting from 09:16 would have been holding 5
positions and 6 closed trades by 10:02. Sri saw this as *"nothing is happening"*.
Three options were put to him and **he has not chosen** — he stopped the thread
with *"I will tell you evening what to do and how to fix it."*
  a) adopt the session from 09:16, b) keep it as-is, c) adopt only what is still
  open, at real entry prices, without claiming the closed trades.

### 5.3 Board coverage was badly wrong until 10:07
`live_shadow.pick_universe()` read only the **last 40 lines** of the board log and
**never looked at the Super Stocks cards at all**. Result: the board had surfaced
251 names today and only 121 had bars. Fixed — `MAX_FETCH` 240 → 500, full-day
scan of shockers + super cards appended behind the current panels. Funnel went
228 → 426 names immediately. **The historical backtests in §4 were run before this
fix**, on tape coverage of only 116 / 547 / 154 names for 03 / 04 / 07-Sep, so the
Super Stocks contribution is essentially **untested historically** (it moved only
03-Sep). Re-run the study once the tape is rebuilt with full coverage.

### 5.4 Unverified indicator implementations
Cipher B, Pullback ALT and Price Action v0.3 are written from the **authors'
published descriptions**, not line-by-line Pine ports — TradingView would not
render the source panel. **AlphaTrend is the precedent: its real Pine was read and
the implementation was found to be wrong** (signal was "price crosses the line"
instead of `crossover(line, line[2])`, and momentum was an up-volume share instead
of real MFI on hlc3). Sri caught it because his chart showed 5 trades and the code
showed 31. **His standing instruction: verify against source before trusting any
indicator.** Pullback ALT is currently half of the live signal and has never been
verified.

---

## 6. What was fixed today (08-Sep)

| # | defect | cause | status |
|---|---|---|---|
| 1 | Board crashed on Start — `Cannot read properties of undefined (reading 'crowded_out')` | `Movers_Board.html` does `s.skipped.crowded_out` with no guard; my minimal JSON payloads omitted `skipped` | Fixed. Every `/livepaper` path now goes through `paper_live.idle_snapshot()`/`board_snapshot()`, so a key cannot go missing again |
| 2 | Engine took no trades at all — `0 in funnel` every cycle | It loaded 159 symbols of warm-up and then **ignored them**; EMA50 and HOTT/LOTT started cold at 09:15 | Fixed — uses `live_shadow.build()` (warm + today, `d0` marks today's start); clock and index cover today only |
| 3 | Supervisor froze for 30 min | I launched the engine through its **job channel**, which `subprocess.run(...)`-waits for the script to finish. The engine is a daemon | Fixed by the self-healing chain in §2. **`paper_live.py` should be removed from `SUPERVISOR.RUNNABLE`** — still listed, still a trap |
| 4 | Engine ignored capital/leverage/target boxes | hard-coded constants | Fixed — read from the control file each cycle, defaults restored on Clear |
| 5 | Board's own names uncollected | §5.3 | Fixed |

Backups on disk: `Movers_app.py.bak_skipfix`, `Movers_app.py.bak_paperlive`,
`paper_live.py.bak_warm`, `paper_live.py.bak_1slot`, `funnel.py.bak_super`,
`live_shadow.py.bak_collect`.

---

## 7. Sri's standing rules — do not relearn these the hard way

- **A human cannot act at 09:15:00.** Benchmarks start at **09:16:00**
  (`eye_bench.EYE_FROM`).
- **Do not consider gap-up stocks** (`eye_bench.GAP_MAX = 5.0`).
- **Never tune on the day you are measuring.** Overfitting has burned this project
  repeatedly: ₹28,967 on 04-Sep became ₹1,965 live on 07-Sep; AlphaTrend's ₹86,393
  became −₹5,488 on the full market. Walk-forward or it does not count.
- **Do not invent parameters and let them become law.** The 3-slot count was mine,
  never justified; he caught it with *"who told to take 3 positions?"*
- **Verify indicators against source.** See §5.4.
- **One `.bat` file only.** He starts `SUPERVISOR_START.bat` and presses the
  buttons; he should never be asked to run a script.
- **The Board and Super Stocks tabs must keep working exactly as they do.**
  `paper_live` reads their **log files** and shares no state with them. Reading
  their logs is not "impacting" them — that misreading is why Super Stocks were
  missing from the universe for a day.
- He wants **honesty over polish**. Withdraw a number the moment it stops
  reproducing, and say so plainly.

---

## 8. Measured findings worth keeping

- **Winners and losers are indistinguishable at entry** — 9 features, 94–99%
  overlap across 67 trades on 04-Sep. There is no entry filter that separates them.
- **No indicator has an edge** once the stock list is not hand-picked. Every one
  tested loses on 04-Sep on the full market.
- The only rule positive on all four days is **volume expansion**, and it wins by
  being better at **staying out** — 332 trades where UT Bot takes 2,890.
- **The system makes money on a ~45% win rate because the winners are larger.**
  The lever is the size of the winners, not the count of losers.
- **Sri's funnel is worth ~₹11,450** — 07-Sep 09:16–10:30, ₹18,248 on all 275
  stocks vs ₹29,698 on his 213. (Note: that second figure is the withdrawn one.)

---

## 9. File map

| file | role |
|---|---|
| `paper_live.py` | **the live engine.** Strategy, slots, control, snapshot |
| `funnel.py` | universe: `superstocks()`, `shockers()`, `watchlist()`, `build()` |
| `live_shadow.py` | Dhan 30-s fetch, `pick_universe()`, `load_warm/today/live()`, `build()` |
| `combos2.py` | `hott_lott`, `pullback_alt`, `price_action_v03`, `combined` |
| `Movers_app.py` | the board. `/livepaper`, `/admin/restart/<token>`, `/admin/engine/<token>`; token `scalp-restart` |
| `SUPERVISOR.py` | keepalive, health, file command channel |
| `slot_study.py` | **new today** — 1/3/5 slots across days, filters on or off |
| `signal_sim.py` | research engine (hindsight backtests) |
| `eye_bench.py` | mechanical human-eye ceiling |
| `dirlib.py` | 13 indicators as ±1 direction lines |
| `replayN.py` | forward-only replay, N positions (no warm-up handling — careful) |
| `INDICATORS.md` | 25+ indicators, lists A–E, tested vs untested |
| `OBSERVATIONS.md` | the running lab notebook |

**Command channel** (the supervisor polls every 5 s):
drop `logs/control/<action>.cmd` — actions `restart`, `stop`, `start`, `ping`,
`run`. **Filenames are remembered forever within a run**, so reuse needs a unique
name: `run-swap0908a.cmd`. Results land in `logs/control/done/`.

---

## 10. Pending, in rough priority order

1. **Find why ₹29,698 does not reproduce** (§5.1). Everything else is downstream.
2. **Get Sri's answer on Start behaviour** (§5.2) — he said he would decide tonight.
3. **Remove `paper_live.py` from `SUPERVISOR.RUNNABLE`** (§6 #3).
4. Rebuild the tape with full board coverage, then **re-run `slot_study.py`** —
   the current numbers rest on partial universes (§5.3).
5. **Verify Pullback ALT against real Pine source.** It is half the live signal.
6. Validate the funnel's ₹11,450 value on 03-Sep and 04-Sep.
7. Eight of Sri's TradingView favourites still untested: Lorentzian
   Classification, ML Adaptive SuperTrend, ALPHAX Vision, Accurate Swing Trading
   System, Consolidation Zones, Lakshmi Range Breakout, Luxy Adaptive MA Cloud,
   Buy Sell Volume Separate.
8. BCA ORB indicator with trailing stop — the one BlueChipAlgos free indicator
   worth testing.
9. Three long Telugu videos (#23–25) not yet read frame by frame.
10. Older bugs: MAYANVAR never carded (D3); `super_check.py` writes test symbol
    `AAA` into the production log (D4).

---

## 11. Where the session ended

Sri: *"I think you are messing up. Just collect the stocks. You are not properly
using the board. I will tell you evening what to do and how to fix it."*

So: **the engine is left running and collecting. No further logic changes until he
says so.** Being recorded for tonight — `board_20260908.jsonl`,
`super_20260908.jsonl`, `shockers_20260908.jsonl`, 474+ symbols of 30-second bars
in `logs/tape_live/20260908`, and the engine's full decision log.
