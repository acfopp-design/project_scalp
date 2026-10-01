# STATUS — what is actually shipped, what is only measured, what is unproven

Written 02-Sep-2026 19:35 IST, after the board died at 11:15 and the session
could not be restarted from here. This is the ledger version of OBSERVATIONS.md:
that file is the running log, this one answers "what do I actually have?"

The split that matters is not shipped/not-shipped. It is **confirmed by the full
engine** vs **supported only by a per-trade statistic**. Three times this week a
strong-looking per-trade number failed portfolio simulation. Anything in the
third table below is a hypothesis wearing a number.

---

## 1. LIVE — running in `live_paper.py`, confirmed by full-engine replay

| Change | Setting | Evidence |
|---|---|---|
| Risk-parity sizing | `RISK_SIZING=True`, 1.5% of capital per trade | position value = risk budget ÷ own stop distance, so a wide-stop name gets fewer shares instead of the same rupees |
| Book risk cap | `MAX_TOTAL_RISK_PCT=8.0`, `MAX_POS_PCT_OF_BOOK=40.0` | stops one name owning the book |
| Exposure units | `UNITS=6` (₹83,333 each, was 3 × ₹1.67L) | a cap, not a target — risk sizing decides the real size |
| Pyramiding | `PYRAMID_AT=1.2%`, needs a new high within 45s, max 3 units/name | fired 3× on 02-Sep; ANTELOPUS unit 2 made ₹4,324 |
| ATR stops | `ATR_MULT=1.5`, capped 2.5% | ₹28,738 in full-engine replay. **1.5, not 2.0** — see §4 |
| Re-entry clock | `REENTRY_COOLDOWN=180s` **measured from the exit** | was measured from the card, which for a continuously-carded stock never elapsed. TBZ ran +8.4% after we sold and could not be re-bought |
| Exit mode | `EXIT_MODE="target"` at +2.0%, stop −1.0% | every test used `"target"`; the default said `"hybrid"` — caught before restart |
| Entry window | 09:15–13:00 | 13:00–15:30 is the only window that loses money |
| Displacement | `DISPLACE_ENABLED=False` | **deliberately off** — see §4 |
| Scrip master | 2,455 → 2,673 NSE EQ, auto-refresh with validation | LALITHAA now in universe (sid 765361) |
| Sector news | own thread, not in the breadth loop | `/sectors` was empty for ~45s after every restart; breadth costs 2.4ms/call |
| Scanner1 / Blast | removed | Scanner1 re-scored 2,455 stocks every 5s for a tab nobody read |

Last completed live run, 10:25:48–11:15 on 02-Sep: 8 closed / 6 open,
realised **+₹4,107**, unrealised −₹420, **net +₹3,687 (3.7%)** — 3 targets,
4 stops, 1 time, zero displacements.

---

## 2. WRITTEN BUT NOT YET RUNNING — needs one restart

These are on disk and will take effect the next time the board is started.
Nothing here has traded a single rupee.

- Self-restart route `/admin/restart/<token>`, autostart thread, code watcher,
  hot config reload (`live_config.json`) — the autonomy stack.
- Everything in `superstocks_lab.py` (the duplicate tab; `superstocks.py` is
  frozen and untouched, as instructed).
- **Note the drift:** `live_config.json` says `ENTRY_TO: "15:00:00"` while
  `live_paper.py` says `13:00:00`. The JSON wins at boot. 13:00 is the tested
  value; the JSON is not. This wants a decision before the next session.

---

## 3. UNPROVEN — in the Lab, no valid evidence either way

| Idea | Where | Why it is still unproven |
|---|---|---|
| Burst memory (a burst is an event, not a state) | `superstocks_lab.py`, 150s latch | cannot be tested offline — §5 |
| From-low path (`MIN_FROM_LOW=3.0`) | `superstocks_lab.py` | same |
| Board-min from open (1.0%) | `superstocks_lab.py` | same |

Do not describe any of these as tested. They are arguments, not results.

---

## 4. THINGS I GOT WRONG AND CORRECTED — kept because they explain the settings

- **Displacement.** An A/B said ₹36,833 vs ₹3,147 and it shipped. It was measured
  on `replay_live`'s ~13s tick and shipped to a 3s live tick: the whole book
  rotated at the 180s dwell floor into names that stopped out inside a minute.
  Now off.
- **ATR multiplier.** "2.0× pays ₹39,527 vs ₹27,315" was computed close-to-close
  because the replay stored only closes. With true high/low ATR, 2.0× gives
  ₹11,651 and a −₹10,704 session. Shipped 1.5×, which is marginal, not proven.
- **"23-minute detection lag."** Polluted by 27-Aug and 31-Aug, when the Super
  loop wrote 21 and 77 snapshots — an outage, not a lag. Real median 1.2 min.
- **"2 of 4 sessions lost."** 27-Aug had bars for 4 of 19 symbols and a test
  symbol called `RUNNER` in the data.
- **`super_check.py` writes a test symbol `AAA` into the production Super log.**
  Still true. Every analysis file now filters it, which is a workaround, not a fix.
- **The 5 "missing" runners.** BHAGERIA, BIRLACABLE and HTEL are in the master
  but on NSE **series BE** — 100% margin, no intraday leverage. Excluding them is
  correct. SUMAX is not on NSE at all. Only ANNU is a real (naming) gap. So
  ₹37,500 of that "₹62,500 lost to coverage" was never ours to make.

---

## 5. THE INSTRUMENT I BUILT TODAY AND THEN REJECTED

`card_lab.py` was meant to test carding-rule changes offline by re-running the
gates over the recorded 30-second tape. **It failed its own calibration and must
not be used.** Both reasons are structural:

1. `bars30` is written by `Movers_ticks`, fed the board's universe and the cards
   already shown. The tape is **downstream of the decision under test**. A looser
   rule exists to find what the board missed; those stocks are absent by
   construction. Only **59%** of really-carded stocks are even on the tape.
2. The rebuild implements ~5 of ~20 gates. Recall **25%**, 516 invented cards
   across 21 sessions.

Run anyway, it claims burst memory would have carded stocks **21,146 seconds
earlier** — nearly six hours, i.e. before the open. That is the value of an
offline result with no calibration gate in front of it.

The file is kept with the verdict at the top so this is not rediscovered.

**What would make offline carding tests possible:** log bars for the scanner's
eligible universe rather than only board members (a change to `Movers_ticks`,
which produces irreplaceable data — to be made deliberately, not unattended),
and import the real gate stack instead of restating it.

---

## 6. STILL OPEN

- Board dead since 11:15:26 after three Dhan HTTP 502s, no traceback. Cannot be
  restarted from here — the bridge reaches a Linux VM, not Windows processes.
- Indicator mismatch: the Dhan chart uses **MA/EMA Cross 12/8** and the green
  band is **Heiken Ashi SuperTrend (10,2)**; the board computes EMA 9 and
  HOTT/OTT. Not the same instrument, so "the board disagrees with my chart" is
  expected until this is reconciled.
- `ENTRY_TO` drift between `live_config.json` (15:00) and `live_paper.py` (13:00).
- Dhan support ticket for the DH-905 chart endpoints.

---

## 7. THE HONEST HEADLINE ON THE 30% TARGET

Best confirmed full-session result is **+3.7%** on the one clean live run.
The perfect-hindsight ceiling on the same tape is near +100%, and that gap is
real information — but the gap is mostly *capacity and timing*, not a missing
rule, and no change tested so far closes more than a few points of it.

30% a day is the target and it stays the target. What I will not do is report a
number that came from an instrument I did not check, which is how the last three
"improvements" were born.
