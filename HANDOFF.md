# HANDOFF — Project_Scalp

Read this first in a new session. Written 03-Sep-2026 ~11:40 IST.
Everything here is reconstructible from files in `C:\Project_Scalp`; this is the
index and the judgement, not a substitute for the logs.

---

## 1. WHO AND WHAT

Sri trades NSE India intraday, **long only**, via Dhan. A local Flask board runs
at `C:\Project_Scalp` on **port 5005**. Capital ₹1,00,000 at **5× leverage**.
Stated target **30% of capital per day**; best validated config delivers ~8–9%
per session, and the gap is honest, not a rounding error.

**How Claude reaches the machine:** `mcp__remote-devices__device_bash` gives a
Linux VM with `C:\Project_Scalp` mounted at `$HOME/mnt/Project_Scalp`.
**There is NO network route from that shell to Windows.** `http://127.0.0.1:5005`
is unreachable from Claude. Do not try; it has been proven twice.

Control therefore happens through **files**:
- `logs/control/<action>.cmd` — `restart` / `stop` / `start` / `ping`. The
  supervisor picks these up within 5s and writes results to `logs/control/done/`.
- `logs/control/status.json` — supervisor health, rewritten every 5s.
- `logs/control/HALT_TRADING` — containing `HH:MM:SS`, halts live trading at or
  after that time (executed by `learn.py`, which runs on Windows and *can* reach
  the board).

---

## 2. HOW IT RUNS

**Sri starts `SUPERVISOR_START.bat` once, around 09:00.** Nothing else.
Never `Movers_START.bat` — the supervisor owns the board, and a second launcher
kills its child and fights over the port.

`SUPERVISOR.py` (runs on Windows):
- launches and owns `Movers_app.py`
- restarts it if the process dies, if the app log stops growing, or — the case
  that mattered — if the **super log stops growing during market hours**, which
  means the trading brain is blind while the app log still looks healthy
- caps at **8 restarts/hour**, then refuses and says so
- kills *all* `Movers_app.py` processes before starting one (added after two
  boards ran at once)
- runs `learn.py --brief` every 10 minutes during market hours

`learn.py` → reviews the last cycle, writes `logs/LEARN_YYYYMMDD.md`, and calls
`tune.py`, which may apply **one** bounded config change and can revert its own
last change if the segment since it did worse. Guards are in `tune.py`'s
docstring; they all exist because of a specific past failure.

**Auto-start:** the board starts live paper trading itself at 09:15 (it now
*waits* for the open if booted earlier — it used to check once and give up).

---

## 3. CURRENT CONFIG — validated

`live_config.json` is hot-applied at boot and by `/admin/reload`.

```
ENTRY_MIN_URGENCY    12.0      PHASE_ENABLED       False
ENTRY_NEEDS_FRESH_HIGH True    MIN_PRICE           100.0
ENTRY_MAX_OFF_PEAK   0.40      MIN_POSITION_RS     60000.0
ENTRY_MAX_SINCE_HIGH 90        RISK_PER_TRADE_PCT  1.5
ENTRY_MIN_EMA_ANGLE  0.05      MAX_TOTAL_RISK_PCT  8.0
ENTRY_MAX_DIPS       2         TARGET 2.0 / STOP -1.0
```

**Validated forward-only across 6 sessions (`replay_live`, no future knowledge):**

| Config | Total | Worst day |
|---|---|---|
| baseline at start of 03-Sep | ₹44,870 | −1,742 |
| + freshness/EMA/chop + min position | ₹51,432 | −1,746 |
| **+ MIN_PRICE 100 + min position 60k** | **₹55,799** | **−1,693** |

---

## 4. WHAT IS TRUE (measured, not assumed)

**Entry quality — Sri's criteria, all confirmed:**
- Buying within 0.4% of the stock's own high, requiring a new high in the last
  90s, skipping flat/falling EMA and choppy names is worth **+₹7,534 over six
  sessions**. Every time Sri said "you entered as it started dipping", he was
  pointing at real money.
- **₹20–100 stocks lose outright**: 37% win, −0.04% avg over 27 samples. Sri
  said this about FILATEX from instinct.
- **The fastest movers are worse**: a 1–2% ninety-second rise wins 58%; 2–4%
  wins 41%. Chasing the violent spike is the same error as buying the rollover.
- Day turnover **20–60 Cr** is the sweet spot (62% win). Under ₹50L/min is the
  worst liquidity bucket (31% win).
- `from_open` above 7% — already run, 33% win.

**Frequency and size dominate selection.** This is the biggest single lesson and
it took all day to see:
```
29 trades → ₹476      25 trades → ₹1,765
20 trades → ₹8,342    11 trades → ₹17,290
```
Charges consume **50–80% of gross profit**. On 03-Sep: ₹2,584 charges against
₹3,252 gross. Every "improvement" made that day was about *which* stock to
enter; the variable that actually moved P&L was *how few and how big*.

**Time of day:** 85% of all cards ever found arrive before 10:30.
```
09:15-09:30  123 cards  46% win  31% hit +2%  35% STOP   ← volume, but violent
09:30-09:45   25        52%      28%          28%
09:45-10:00   14        78%      28%           7%        ← few, but safe
10:00-10:15   11        63%       9%           0%
10:30-11:30   20        45%      15%          15%
```
The open is **high-variance, not generous**. Encoding this as a phase table was
tried and **failed** (see §5).

---

## 5. WHAT FAILED — do not retry without new evidence

- **Phase-aware time-banded gates.** Built on the table above; 5 bands then 8.
  Scored **₹31,178 vs ₹44,870 baseline** — a −₹13,692 regression. Fitted to
  6–14 samples per band. `PHASE_ENABLED=False`.
- **Urgency floor of 18.** Looked brilliant twice (₹17,290 on one day) and is a
  wash over six sessions with a deeper worst day. **Urgency is not monotonic**:
  0–14 makes money, 14–16 loses, 16+ makes money. A single floor cannot express
  that. It also *decays through the session* (it subtracts trade age), so a
  fixed floor silently tightens all day — at 10:20 a floor of 16 rejected 100%
  of candidates.
- **Displacement** (rotating capital to stronger signals). Won its A/B on a 13s
  replay clock, shipped to a 3s live clock, and churned the whole book. OFF.
- **ATR 2.0× stops.** The A/B was computed close-to-close; with true high/low
  ATR it loses. Shipped 1.5×, which is marginal, not proven.
- **Pullback-limit entries** and the **from_open 3–4% bucket** — both were
  convincing per-trade findings that failed full-engine replay.
- **`card_lab.py`** — an attempt to test carding rules offline. Failed its own
  calibration (25% recall) because `bars30` is written from the board's own
  universe and is therefore *downstream* of the decision under test. Kept with
  the verdict at the top of the file so it isn't rediscovered.

---

## 6. KNOWN BUGS, OPEN

1. **Pre-open boost is dead code.** Of 164 early cards across 6 sessions,
   **zero** carried `preopen=True`. `PREOPEN_BOOST` (+4.0 to entry rank) has
   never fired. Needs the pre-open loop's symbol set traced through to
   `superstocks_lab`. **Highest-value open item** — pre-open strength is exactly
   the signal for the 09:15–10:00 window.
2. **`MAYANVAR` never carded at all** on 03-Sep despite a clear run — a
   detection gap, not a rules gap.
3. `Movers_dhannews.py` line 477: `NameError: _dropped_bse is not defined`,
   kills `premarket_loop` in the pre-open window.
4. `super_check.py` writes a test symbol `AAA` into the production super log.
   Every analysis file filters it — a workaround, not a fix.
5. Indicator mismatch: Sri's chart uses **MA/EMA Cross 12/8** and **Heiken Ashi
   SuperTrend (10,2)**; the board computes EMA 9 and HOTT/OTT. Not the same
   instrument.
6. `learn.py` and `tune.py` are wired via `learn.py` importing `tune`, so they
   go live without a supervisor restart. `SUPERVISOR.py` changes DO need one.

---

## 7. STANDING RULES — earned, not stylistic

1. **A per-trade statistic is a hypothesis.** It ships only when
   `replay_live.py` over the full session agrees. This has saved the project
   four times and been violated three times, every violation costing money.
2. **Run `regress.py`** before shipping — full engine over every session on
   disk against a locked baseline, exits 1 on regression.
3. **`superstocks.py` is FROZEN** at Sri's instruction. Experiment in
   `superstocks_lab.py`.
4. **Savepoint before any code edit**: `python3 savepoint.py create "reason"`.
5. **`node --check`** the extracted script after any HTML/JS edit — a stray
   quote once killed every tab on the board.
6. **Never test `tune.py` without `--dry`** — it writes `live_config.json` and
   has mutated live config mid-session.
7. **Do not restart repeatedly.** Eight restarts in one hour left two boards
   running at once and hit the supervisor's cap. The guard worked; the pace
   did not.
8. Sri wants **brief replies**, 1–2 lines where possible, and an action item at
   the end only when there genuinely is one.
9. **Token economy matters** — a session that burns its limit by midday goes
   blind for the rest of the day. Use `learn.py --brief` (6 lines); open the
   full `LEARN_*.md` only when a bucket is worth acting on.

---

## 8. STATE AT HANDOFF

- **Live trading HALTED** (Sri's instruction, 11:00 on 03-Sep).
  Result: 46 closed, realised **+₹5,411 (5.41%)**.
- **Two duplicate board processes are running**; the supervisor lost track of
  its child and hit the 8-restart cap, so the web page is unreachable and the
  Back Trade button appears broken. The engine itself is fine (tested directly:
  20 trades, ₹8,342).
  **PENDING SRI: Ctrl+C the SUPERVISOR window and re-run `SUPERVISOR_START.bat`.**
  The new orphan-killing code makes that a clean single board.
- Latest savepoint: `savepoint_20260903_055848`.

---

## 9. NEXT, IN ORDER

1. Sri restarts the supervisor → confirm one board, page responds.
2. Fix the pre-open flag (§6.1) — likely the largest single source of missed
   early runners.
3. Test **position count** directly in `replay_live` forward-only (3/4/5 slots,
   all sessions). Frequency/size is the dominant variable and has never been
   tested without hindsight. Note `paper_engine`'s "Back Trade" tab sweeps six
   slot counts and reports the **best**, which is a hindsight choice worth
   ~₹6,578 — it is NOT comparable to `replay_live`.
4. Only then return to entry rules.

**Read `OBSERVATIONS.md`** for the full chronological log, including every
change that was rejected and the number that killed it. The rejected half is
the more valuable half.
