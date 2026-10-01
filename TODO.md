# Open items

## 1. Premarket Predictor is hard-capped at 60 calls  (NOT YET FIXED)
`record_calls()` freezes `rows[:60]`. On 19-Aug the feed carried **241 items**
and the cap kept severity **44-100** — anything below 44 is never frozen and
therefore **can never be scored**, however well it performs. This is a blind
spot in the measurement, not just the display.

Options: raise the cap; or keep 60 for the UI but freeze all of them for
scoring; or record the below-the-line rows separately so the gap is visible.

## 2. `_stamp_momentum` stamps 0 cards live  (ROUTED AROUND, NOT ROOT-CAUSED)
momentum/thrust_pct/money_x were populated on 0 of 4,393 cards on 19-Aug while
the modules import cleanly and `momentum()` works standalone. RUNNER no longer
depends on it and it now logs loudly on failure, but the cause is unknown.
Check the next session's log for `momentum: STAMPED 0 cards`.

## 3. Scanner1 vs Board mismatch  (NOW MEASURABLE, NOT YET MEASURED)
Scanner1 began writing `scanner1_YYYYMMDD.jsonl` on 19-Aug. Compare against
`board_YYYYMMDD.jsonl` next session to quantify the difference.
