"""
test_paper_engine.py -- proves the two fixes made on 01-Sep actually work.

Run:  python test_paper_engine.py     (no market data needed -- synthetic tape)

WHY THIS FILE EXISTS
    Displacement did not fire once on 01-Sep's real tape, even with the margin
    dropped to 0.5 -- the strongest signals happened to arrive first that day.
    Shipping it on "it looks right" is how this project has been burned before,
    so the behaviour is pinned here on a tape built to exercise it.

    TEST 4 is a regression test for a bug that was live for one run: WEL was
    "displaced" at a bar's CLOSE (83.63, -3.5%) when its resting stop at 85.79
    (-1%) had already been hit inside that same bar. Settling happens against
    the challenger's ENTRY time now, not its signal time.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_engine as pe


def bar(t, px):
    return (t, px, px, px, px, 1000)


def sig(t, urg, pre=None):
    """One signal. simulate() takes a LIST per symbol now, because a stock can
    signal again later in the session -- see t6_reentry."""
    return [{"t": t, "urgency": urg,
             "rank": urg + (pe.PREOPEN_BOOST if pre else 0),
             "preopen": pre, "from_open": 2.5, "day_pct": 3.0, "tover_cr": 30}]


def sigs(*specs):
    out = []
    for t, urg in specs:
        out += sig(t, urg)
    return out


S = pe.sec("09:15:00")
BARS = {
    # DUD drifts sideways: never hits +3% or -1%, so it squats in the slot
    # until the 30-minute cap. Exactly the position worth displacing.
    "DUD":  [bar(S + i * 30, 100.0 + (i % 3) * 0.05) for i in range(120)],
    "STAR": [bar(S + i * 30, 200.0 * (1 + min(i, 20) * 0.003)) for i in range(120)],
}


def t1_displaces():
    tr, sk, dp = pe.simulate(100000, 5, 1, BARS,
                             {"DUD": sig(S + 10, 12.0), "STAR": sig(S + 130, 25.0)},
                             S + 3600)
    assert dp == 1, f"displacement did not fire (dp={dp})"
    assert any(t["sym"] == "DUD" and t["why"] == "displaced" for t in tr)
    assert any(t["sym"] == "STAR" for t in tr)
    return "weak holder displaced, strong signal filled"


def t2_no_churn():
    tr, sk, dp = pe.simulate(100000, 5, 1, BARS,
                             {"DUD": sig(S + 10, 12.0), "STAR": sig(S + 130, 14.0)},
                             S + 3600)
    assert dp == 0, "a 2-point edge must not pay two sets of charges"
    assert sk["crowded_out"] == 1, sk
    return "no churn on a 2-point edge (margin is 3.0)"


def t3_preopen_breaks_tie():
    tr, sk, dp = pe.simulate(100000, 5, 1, BARS,
                             {"DUD": sig(S + 10, 12.0),
                              "STAR": sig(S + 130, 12.0, "09:07:56")},
                             S + 3600)
    assert dp == 1, "pre-open boost (+4.0) should clear the 3.0 margin on equal urgency"
    return "equal urgency: the pre-open STRONG GAP-UP call wins the slot"


def t4_stop_beats_displacement():
    b = dict(BARS)
    b["FALLER"] = ([bar(S, 100.0), (S + 30, 100.0, 100.0, 98.0, 98.0, 1000)]
                   + [bar(S + i * 30, 98.0) for i in range(2, 120)])
    tr, sk, dp = pe.simulate(100000, 5, 1, b,
                             {"FALLER": sig(S - 5, 12.0), "STAR": sig(S + 40, 25.0)},
                             S + 3600)
    f = next(t for t in tr if t["sym"] == "FALLER")
    assert f["why"] == "stop" and abs(f["out"] - 99.0) < 0.01, f
    assert dp == 0, "a position already stopped out is not available to displace"
    return "resting stop fills at 99.00, not at the 98.00 bar close"


def t5_intrabar_resolves_against():
    # one bar that touches BOTH the +3% target and the -1% stop
    b = {"BOTH": [bar(S, 100.0), (S + 30, 100.0, 104.0, 98.0, 103.0, 1000)]
                 + [bar(S + i * 30, 103.0) for i in range(2, 60)]}
    tr, _s, _d = pe.simulate(100000, 5, 1, b, {"BOTH": sig(S - 5, 20.0)}, S + 3600)
    assert tr[0]["why"] == "stop", tr[0]
    return "a bar hitting target AND stop books the STOP"



def t6_reentry():
    """A stock that exits and signals again LATER is traded again."""
    # RUNNER: +4% by 09:20 (hits target), flat, then +4% again from 09:40
    r = []
    for i in range(160):
        t = S + i * 30
        if i <= 10:      px = 100.0 * (1 + i * 0.004)
        elif i <= 50:    px = 104.0
        else:            px = 104.0 * (1 + min(i - 50, 12) * 0.004)
        r.append(bar(t, px))
    b = {"RUNNER": r}
    sg = {"RUNNER": sigs((S - 5, 20.0), (S + 51 * 30, 22.0))}
    tr, sk, dp = pe.simulate(100000, 5, 1, b, sg, S + 4800)
    legs = [t for t in tr if t["sym"] == "RUNNER"]
    assert len(legs) == 2, f"expected two legs, got {len(legs)}"
    assert legs[0]["leg"] == 1 and legs[1]["leg"] == 2, legs
    assert legs[1]["in_t"] > legs[0]["out_t"], "leg 2 must start after leg 1 exits"
    return f"traded twice: {pe.hhmm(legs[0]['in_t'])} and {pe.hhmm(legs[1]['in_t'])}"


def t7_cooldown_blocks_immediate_reentry():
    """A card that reappears seconds after the exit is NOT a new trade."""
    r = [bar(S + i * 30, 100.0 * (1 + min(i, 10) * 0.004)) for i in range(160)]
    sg = {"RUNNER": sigs((S - 5, 20.0), (S + 11 * 30, 22.0))}   # ~30s after exit
    tr, sk, dp = pe.simulate(100000, 5, 1, {"RUNNER": r}, sg, S + 4800)
    legs = [t for t in tr if t["sym"] == "RUNNER"]
    assert len(legs) == 1, f"cooldown breached: {len(legs)} legs"
    assert sk["cooldown"] >= 1, sk
    return "second card inside the 5-min cooldown correctly ignored"


def t8_never_two_legs_at_once():
    """The same stock is never held in two slots simultaneously."""
    r = [bar(S + i * 30, 100.0 + i * 0.01) for i in range(160)]   # drifts, no exit
    sg = {"RUNNER": sigs((S - 5, 20.0), (S + 400, 22.0))}
    tr, sk, dp = pe.simulate(100000, 5, 4, {"RUNNER": r}, sg, S + 4800)
    assert len([t for t in tr if t["sym"] == "RUNNER"]) == 1, tr
    return "a stock already held is not bought again in a second slot"


if __name__ == "__main__":
    fails = 0
    for fn in (t1_displaces, t2_no_churn, t3_preopen_breaks_tie,
               t4_stop_beats_displacement, t5_intrabar_resolves_against,
               t6_reentry, t7_cooldown_blocks_immediate_reentry,
               t8_never_two_legs_at_once):
        try:
            print(f"  PASS  {fn.__name__:<28} {fn()}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {fn.__name__:<28} {e}")
    print(("ALL PASSED" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
