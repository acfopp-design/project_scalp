"""multi_capital.py -- sizing + fill-realism model for testing the engine at
capitals other than the live Rs 1,00,000 tab. STANDALONE. Nothing here is
imported by Movers_app.py or paper_live.py, so it cannot change today's live
Rs 1,00,000 run. Wiring this into the board is a separate, later step, done
only after the market closes.

Design agreed with Sri, 28-Sep:
  - Leg size is fixed at TARGET_LEG (Rs 2,50,000), not the 100k tab's number.
  - SLOTS = book / TARGET_LEG, clipped to [MIN_SLOTS, MAX_SLOTS].
  - A trade's quantity is additionally capped so it can never take more than
    PARTICIPATION_CAP of the 30-second bar's own traded volume, on both the
    entry and the exit bar. This is the "participation cap" -- without it, a
    bigger book just books fills the real order book could never have given.
"""
TARGET_LEG = 250_000.0
# Sri, 28-Sep: hold the LEG at Rs 2,50,000 for every tier at/above Rs 1L and
# let capital buy more legs. So Rs 3L -> 6 legs, Rs 4L -> 8 legs.
MIN_SLOTS, MAX_SLOTS = 1, 8
PARTICIPATION_CAP = 0.25    # a trade may take at most 25% of one 30s bar's volume


# Sri, 28-Sep: force Rs 10,000 into 2 legs even though its book (Rs 50,000)
# doesn't fund two Rs 2,50,000 legs. This SHRINKS each leg to Rs 25,000 --
# below the Rs 66,667 point where Dhan's brokerage stops being capped at
# Rs 20/leg, so this tier pays the full 0.03% rate on both legs, and a
# Rs 25,000 leg buys very few shares of anything over ~Rs 500 (more of the
# leg sits unused, rounded down to whole shares). Kept anyway, as asked --
# this is what we're testing.
# Below one full leg's worth of book you cannot fund a Rs 2.5L leg, so these
# two tiers are pinned to 2 slots and the leg simply comes out smaller:
#   Rs 50,000 x 5 = Rs 2,50,000 book / 2 = Rs 1,25,000 a leg
#   Rs 10,000 x 5 = Rs   50,000 book / 2 = Rs   25,000 a leg
TIER_SLOT_OVERRIDE = {10_000: 2, 50_000: 2}


def plan(capital, leverage=5.0):
    """(slots, leg_target, book) for a given capital. Pure function, no I/O.

    Below one full leg's worth of book, there is normally only one slot --
    you cannot manufacture a second Rs 2,50,000 leg out of Rs 10,000 of
    capital. TIER_SLOT_OVERRIDE above forces specific tiers past that.
    """
    book = capital * leverage
    if capital in TIER_SLOT_OVERRIDE:
        slots = TIER_SLOT_OVERRIDE[capital]
    else:
        slots = max(MIN_SLOTS, min(MAX_SLOTS, round(book / TARGET_LEG)))
    leg = book / slots
    return slots, leg, book


CAPITAL_TIERS = [10_000, 50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 400_000]

if __name__ == "__main__":
    print("%-12s %6s %6s %12s %12s" % ("capital", "slots", "leg", "leg Rs", "book Rs"))
    for c in CAPITAL_TIERS:
        slots, leg, book = plan(c)
        flag = "  <- Rs 1,00,000 tab (unchanged, lives in paper_live.py)" if c == 100_000 else ""
        print("%-12s %6d %6s %12s %12s%s" % (
            "{:,.0f}".format(c), slots, "-", "{:,.0f}".format(leg), "{:,.0f}".format(book), flag))
