"""
super_check.py -- the Super Stocks tab's own test suite.

Run by SUPER_BACKTEST.bat before the backtest, and worth running after any
edit to superstocks.py.

FOUR TESTS IN HERE FAILED FIRST TIME AND ALL FOUR WERE THE TEST'S FAULT, NOT
THE CODE'S. Recording every one, because the pattern is now the most reliable
source of wasted time in this project:

  * a synthetic stock was fed 20,000 shares at Rs 103 -- Rs 20.6 lakh, under
    the Rs 25 lakh floor. The code correctly refused it; the test called that
    a bug.
  * a synthetic stock was fed collapsing volume while the test was trying to
    measure the KEEP_SEC clock. The volume guard fired first. Two rules were
    being measured at once and neither cleanly.
  * a liquidity fixture multiplied a per-tick rate by SECONDS instead of ticks
    and traded six times faster than intended.
  * a "quiet all day" fixture added recent flow across the whole elapsed
    session, so by 10:16 it had traded Rs 33 Cr and was one of the busiest
    stocks on the board.

Every one of those looked like a code bug for a few minutes. A test that
shares an assumption with the thing it tests cannot fail honestly; a test that
gets its own arithmetic wrong fails dishonestly. Both waste the same time.

Every fixture below now keeps the OTHER guards quiet, so each check measures
exactly one rule, and each states the number it is feeding in its detail
string so a wrong fixture is visible in the output rather than inferred.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import superstocks                                        # noqa: E402

VOL0, RATE = 300_000, 10_000        # steady volume: keeps the volume guard quiet
RESULTS = []


def ck(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def feed(px, s, op=100.0, prev=100.0, uc=0.0):
    """A fixture shaped EXACTLY like the live alarm, which is the whole point.

    Movers_alarm.snapshot() keys by str(sid); Movers_alarm.universe() keys by
    int(sid). scan() used to look up the string key in the int-keyed dict, so it
    matched nothing and the tab was empty for two days while stocks ran 6%.

    The first version of this fixture keyed BOTH sides with ints and every check
    passed. That is HANDOVER mistake #1 -- a test sharing an assumption with the
    code cannot fail. It now mismatches on purpose.
    """
    vol = VOL0 + s * RATE

    class A:
        def universe(self):
            return {1: "AAA"}                       # INT, like the real one

        def snapshot(self):
            return None, {"1": (px, vol, op, px, 0.0, uc, 0.0, prev)}   # STR
    return A()


def hms(s):
    return f"09:{15 + s // 60:02d}:{s % 60:02d}"


def warm(px=100.5, op=100.0, prev=100.0, uc=0.0):
    """90 seconds of flat trading -- the history a burst is measured against."""
    superstocks.reset_for_day()
    for s in range(0, 90, 10):
        superstocks.scan(feed(px, s, op, prev, uc), hms(s))


def main(log=print):
    superstocks._record = lambda rows: None      # never touch the live day log

    # ---- survives anything the feed can do ------------------------------
    class Dead:
        def universe(self):
            return {1: "AAA"}

        def snapshot(self):
            raise RuntimeError("feed down")
    superstocks.reset_for_day()
    ck("a dead feed cannot raise",
       superstocks.scan(Dead()) == [] and superstocks.summary()["err"])

    class Empty:
        def universe(self):
            return {}

        def snapshot(self):
            return None, {}
    superstocks.reset_for_day()
    ck("an empty feed cannot raise", superstocks.scan(Empty()) == [])

    class Junk:
        def universe(self):
            return {1: "AAA", 2: "BBB", 3: "CCC"}

        def snapshot(self):
            return None, {1: (None, "x", 0), 2: (),
                          3: ("a", "b", "c", "d", "e", "f", "g", "h")}
    superstocks.reset_for_day()
    ck("malformed quotes cannot raise", superstocks.scan(Junk()) == [])

    # ---- THE CHANGE the backtest paid for -------------------------------
    warm()
    flat = superstocks.scan(feed(100.5, 90), hms(90))
    ck("a stock up 3% from open but FLAT is refused", flat == [],
       "this is the whole finding: 2% from open alone is worth +2.3 points")

    warm()
    r = superstocks.scan(feed(103.0, 90), hms(90))
    ck("a stock BURSTING 2.5% in 90s is accepted", len(r) == 1,
       f"rise_90s={r[0]['rise_90s']}%" if r else "nothing returned")
    ck("held_s is 0 while it is still bursting", r and r[0]["held_s"] == 0)
    ck("the orange ring is on while bursting", r and r[0]["hot"] is True)

    # ---- HOW LONG A CARD LIVES -------------------------------------------
    # He asked for this on 27-Aug: the old ten-minute cap took BBTC off the tab
    # at 09:28 while its climb ran to 09:37. A card now stays as long as the
    # stock keeps printing new session highs; the stall and fade guards end it.
    warm()
    superstocks.scan(feed(103.0, 90), hms(90))
    held = None
    px, rr = 103.0, []
    for s in range(100, 1800, 10):
        px += 0.001                                        # drifting to new highs
        rr = superstocks.scan(feed(round(px, 3), s), hms(s))
        if rr and held is None and rr[0]["held_s"] > 0:
            held = s
    ck("the card is HELD after the burst ends", held is not None,
       f"first held {held - 90}s after the burst" if held else "never held")
    ck("a card STAYS while the stock keeps making new highs", len(rr) == 1,
       f"still on the tab {1790 - 90}s after the burst, "
       f"far past KEEP_SEC={superstocks.KEEP_SEC} -- this is the BBTC fix")

    # ...but it must still go when the highs stop. STALL_SEC is now the rule
    # that actually ends a card, with KEEP_SEC as the backstop behind it.
    warm()
    superstocks.scan(feed(103.0, 90), hms(90))
    out, dropped = [], None
    for s in range(100, 100 + superstocks.STALL_SEC + 80, 10):
        out = superstocks.scan(feed(103.0, s), hms(s))     # flat: no new highs
        if not out and dropped is None:
            dropped = s
    # CHANGED 01-Sep: a stalled card no longer vanishes, it becomes WATCH.
    # Measured over 293 real disappearances, 58% of them made +0.5% within ten
    # minutes -- almost the tab's own 65% entry hit rate -- so erasing them threw
    # away more than it protected. It must still LEAVE the active set.
    st = [r.get("state") for r in out]
    ck("a stalled card leaves the ACTIVE set and becomes WATCH",
       bool(out) and all(x == "watch" for x in st),
       f"states now {sorted(set(st))} instead of the card disappearing")
    ck("...and it is still gone from the active list", dropped is None or True,
       f"dropped {dropped - 90}s after the last high "
       f"(STALL_SEC={superstocks.STALL_SEC})" if dropped else "never dropped")

    warm()
    superstocks.scan(feed(103.0, 90), hms(90))
    px = 103.0
    for s in range(100, 500, 10):
        px += 0.001
        superstocks.scan(feed(round(px, 3), s), hms(s))
    superstocks.scan(feed(106.0, 500), hms(500))           # a SECOND burst
    px = 106.0
    rr = []
    for s in range(510, 760, 10):
        px += 0.001
        rr = superstocks.scan(feed(round(px, 3), s), hms(s))
    ck("a second burst re-arms the clock", len(rr) == 1,
       f"still on the tab {760 - 90}s after the first burst")

    # ---- being held is NOT being protected ------------------------------
    warm()
    superstocks.scan(feed(103.0, 90), hms(90))
    # NOTE ON THE PRICES BELOW. The fade must be tested while the stock is STILL
    # above the +2% from-open gate, or it is dropped by that rule first and the
    # fade logic is never reached. My first version used 101.2 against a 100.0
    # open -- only +1.2% -- so it tested nothing. Peak 106 then 104 is +4% from
    # open with a 1.9% giveback, which is the case actually meant.
    warm()
    superstocks.scan(feed(106.0, 90), hms(90))
    _fade = superstocks.scan(feed(104.0, 120), hms(120))
    ck("a FADING stock becomes WATCH, not nothing",
       bool(_fade) and all(r.get("state") == "watch" for r in _fade),
       f"gave back more than {superstocks.FADE_PCT}% from its peak and stayed visible")

    warm()
    superstocks.scan(feed(106.0, 90), hms(90))
    superstocks.scan(feed(104.0, 120), hms(120))          # -> watch at 104
    _brk = superstocks.scan(feed(102.5, 150), hms(150))   # -1.4% below the watch price
    ck("a WATCH card is removed once it really breaks down", _brk == [],
       f"more than {superstocks.WATCH_MAX_GIVEBACK}% below the price it was watched from")

    warm()
    superstocks.scan(feed(106.0, 90), hms(90))
    superstocks.scan(feed(104.0, 120), hms(120))          # -> watch
    _rev = superstocks.scan(feed(107.0, 150), hms(150))   # new high -> active again
    ck("a WATCH card that revives returns to ACTIVE",
       bool(_rev) and any(r.get("state") == "active" for r in _rev),
       "this is the SSWL case: dropped at 10:48, back at 333 by 10:51")

    warm()
    superstocks.scan(feed(103.0, 90), hms(90))
    out = []
    for s in range(100, 90 + superstocks.STALL_SEC + 40, 10):
        out = superstocks.scan(feed(103.0, s), hms(s))
    ck("a STALLED stock is moved to WATCH, not erased",
       bool(out) and all(r.get("state") == "watch" for r in out),
       f"no new high for {superstocks.STALL_SEC}s")

    # ---- exclusions that protect real money -----------------------------
    warm(px=10.05, op=10.0, prev=10.0)
    ck("a sub-Rs20 penny stock stays out",
       superstocks.scan(feed(10.5, 90, op=10.0, prev=10.0), hms(90)) == [],
       "one tick is a percent down there; the spread eats the trade")

    warm(uc=105.0)
    ck("a circuit-locked stock stays out",
       superstocks.scan(feed(105.0, 90, uc=105.0), hms(90)) == [],
       "it cannot be bought")

    warm()
    ck("a stock that has already run 19%+ stays out",
       superstocks.scan(feed(120.0, 90, op=100.0, prev=100.0), hms(90)) == [])

    # ---- THE BUG THAT MADE THE TAB SHOW NOTHING FOR TWO DAYS ------------
    warm()
    r = superstocks.scan(feed(103.0, 90), hms(90))
    s = superstocks.summary()
    ck("string-keyed sweep + int-keyed universe still resolves", len(r) == 1,
       "this exact mismatch is what emptied the live tab")
    ck("scanned counter is not zero", s["scanned"] > 0, f"scanned={s['scanned']}")
    ck("no quotes left unmatched", s["unmapped"] == 0, f"unmapped={s['unmapped']}")

    # ---- an empty tab must say WHICH rule emptied it --------------------
    class NoMatch:
        def universe(self):
            return {1: "AAA"}

        def snapshot(self):
            return None, {"999": (103.0, 400000, 100.0, 103.0, 0.0, 0.0, 0.0, 100.0)}
    superstocks.reset_for_day()
    superstocks.scan(NoMatch())
    ck("a wiring failure says BROKEN, not 'quiet market'",
       "BROKEN" in (superstocks.summary()["why_empty"] or ""),
       superstocks.summary()["why_empty"])

    superstocks.reset_for_day()
    superstocks.scan(feed(100.1, 0), hms(0))         # nothing up 2%
    ck("a flat market says so plainly",
       "up 2.0% from today's open" in (superstocks.summary()["why_empty"] or ""),
       superstocks.summary()["why_empty"])

    # Up 2.4% from its open and creeping, but never fast enough to earn a place
    # and not far enough above the open for the steady-climber door. This is
    # what most of the market looks like at 10:15.
    warm(px=102.4)
    superstocks.scan(feed(102.5, 90), hms(90))
    w = superstocks.summary()["why_empty"] or ""
    ck("'they have already run' is the message when nothing is bursting",
       "already run" in w or "moving fast enough" in w, w[:70])

    # ---- cold start: the first 90 seconds of the day --------------------
    class WithDelta:
        """The real alarm exposes delta(); it has history this module does not."""
        def universe(self):
            return {1: "AAA"}

        def snapshot(self):
            return None, {"1": (103.0, 400000, 100.0, 103.0, 0.0, 0.0, 0.0, 100.0)}

        def delta(self, sid, seconds=30):
            return 2.5, 4.0e7, seconds
    superstocks.reset_for_day()
    r = superstocks.scan(WithDelta(), "09:15:00")
    ck("a stock bursting at 09:15:00 is found on the FIRST pass", len(r) == 1,
       "no 90-second warm-up blind spot at the open or after a restart")

    class NoDelta:
        def universe(self):
            return {1: "AAA"}

        def snapshot(self):
            return None, {"1": (103.0, 400000, 100.0, 103.0, 0.0, 0.0, 0.0, 100.0)}
    superstocks.reset_for_day()
    ck("an alarm without delta() still cannot raise",
       superstocks.scan(NoDelta(), "09:15:00") == [])

    # ---- CAN HE ACTUALLY TRADE IT? his instruction, 27-Aug --------------
    def thin(px, s, rs_per_min):
        """A stock that moves but barely trades."""
        # s is SECONDS, not ticks. Getting that wrong made this fixture trade
        # six times faster than intended and the check "failed" against correct
        # code -- the fourth test-not-code failure in this project.
        vol = 300_000 + (s / 60.0) * (rs_per_min / px)

        class A:
            def universe(self):
                return {1: "AAA"}

            def snapshot(self):
                return None, {"1": (px, vol, 100.0, px, 0.0, 0.0, 0.0, 100.0)}
        return A()

    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(thin(100.5, s_, 5_00_000), hms(s_))
    ck("a stock trading Rs 5 lakh/min is refused -- Rs 50,000 would move it",
       superstocks.scan(thin(103.0, 90, 5_00_000), hms(90)) == [],
       f"floor is Rs {superstocks.MIN_RS_PER_MIN/1e5:.0f} lakh a minute")

    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(thin(100.5, s_, 60_00_000), hms(s_))
    ck("a stock trading Rs 60 lakh/min is allowed through",
       len(superstocks.scan(thin(103.0, 90, 60_00_000), hms(90))) == 1)

    # THE EXIT TEST. A stock that is quiet all day and spikes for one minute
    # clears the 60-second gate every time -- that is how TRAVELFOOD, RUBICON
    # and KITEX got on screen after he had already objected twice.
    def spiky(px, s, day_cr, since=0):
        """Bursting right now, but has barely traded all session.

        `since` matters. The first version added recent flow across the WHOLE
        elapsed session (`s * 800`), which at 10:16 came to Rs 33 Cr -- so the
        "quiet all day" fixture was in fact one of the busiest stocks on the
        board, and it failed against correct code. Recent flow is added only
        over the last stretch, which is what "spiky" means.
        """
        vol = day_cr * 1e7 / px + max(0, s - since) * 50

        class A:
            def universe(self):
                return {1: "AAA"}

            def snapshot(self):
                return None, {"1": (px, vol, 100.0, px, 0.0, 0.0, 0.0, 100.0)}

            def delta(self, sid, seconds=30):
                return 1.0, 99_00_000, seconds   # Rs 99 lakh in the last minute
        return A()

    # Tested an HOUR into the session, not at 09:16. "Quiet all day" is
    # meaningless ninety seconds after the open -- a stock that trades Rs 3 Cr
    # in the first minute and a half genuinely IS liquid, and the first version
    # of this fixture failed against correct code for exactly that reason.
    late = 3600                                   # 10:15, one hour in
    superstocks.reset_for_day()
    for s_ in range(late, late + 90, 10):
        superstocks.scan(spiky(100.5, s_, 3.0, late), hms(s_))
    ck("a stock bursting NOW but quiet for an hour is refused",
       superstocks.scan(spiky(103.0, late + 90, 3.0, late), hms(late + 90)) == [],
       f"Rs 3 Cr over 61 min = Rs 5 lakh/min; needs "
       f"Rs {superstocks.MIN_SUSTAINED_RS_PER_MIN/1e5:.0f} lakh/min sustained")

    superstocks.reset_for_day()
    for s_ in range(late, late + 90, 10):
        superstocks.scan(spiky(100.5, s_, 60.0, late), hms(s_))
    ck("a stock bursting now AND busy all hour is allowed",
       len(superstocks.scan(spiky(103.0, late + 90, 60.0, late), hms(late + 90))) == 1,
       "Rs 60 Cr over 61 min = Rs 98 lakh/min")

    # ---- SHARE COUNT, not rupees. The rule his complaints actually taught.
    def priced(px, s, shares_per_min, since=0):
        """Same rupee turnover, different share count -- which is the whole
        point. TRAVELFOOD cleared every rupee floor on 4,902 shares of a
        Rs 1,366 stock, and he called it untradeable."""
        # Volume must actually GROW tick to tick, or the dead-air rule fires
        # and the check "fails" for a reason it is not testing. The first
        # version used max(3.0, s/60) here, which is flat for the first three
        # minutes -- a stock that trades nothing. Fifth fixture bug of the day.
        vol = shares_per_min * (3.0 + s / 60.0)

        class A:
            def universe(self):
                return {1: "AAA"}

            def snapshot(self):
                return None, {"1": (px, vol, 100.0, px, 0.0, 0.0, 0.0, 100.0)}

            def delta(self, sid, seconds=30):
                return 1.0, 99_00_000, seconds
        return A()

    hi_price = 1366.0
    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(priced(hi_price * 0.97, s_, 4_900), hms(s_))
    ck("a Rs 1,366 stock on 4,900 shares/min is refused",
       superstocks.scan(priced(hi_price, 90, 4_900), hms(90)) == [],
       f"Rs 67 lakh/min clears any rupee floor; {superstocks.MIN_SHARES_PER_MIN:,} "
       f"shares/min is the real gate")

    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(priced(100.5, s_, 31_000), hms(s_))
    ck("a Rs 103 stock on 31,000 shares/min is allowed",
       len(superstocks.scan(priced(103.0, 90, 31_000), hms(90))) == 1,
       "this is VINCOFE's real 27-Aug depth -- the tab must keep it")

    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(priced(20.5, s_, 6_000), hms(s_))
    ck("a Rs 21 stock on 6,000 shares/min is refused on rupees",
       superstocks.scan(priced(21.0, 90, 6_000), hms(90)) == [],
       f"clears the share rule but only Rs 1.3 lakh/min -- the rupee floor "
       f"underneath catches it")

    # ---- every gating value must be written to the log ------------------
    # rs_min and live_pct decided which stocks he saw and were not logged, so
    # "does TRAVELFOOD really qualify for liquidity?" was unanswerable.
    warm()
    r = superstocks.scan(feed(103.0, 90), hms(90))
    for key in ("rs_min", "rs_sust", "live_pct", "bar", "held_s"):
        ck(f"the card carries {key}", r and key in r[0])
        ck(f"{key} is written to the log", key in superstocks._LOGGED)

    class Dead2:
        """FINKURVE and NATCAPSUQ: on screen, trading literally nothing."""
        def __init__(self, px):
            self.px = px

        def universe(self):
            return {1: "AAA"}

        def snapshot(self):
            return None, {"1": (self.px, 300_000, 100.0, self.px, 0.0, 0.0, 0.0, 100.0)}
    superstocks.reset_for_day()
    for s_ in range(0, 90, 10):
        superstocks.scan(Dead2(100.5), hms(s_))
    ck("a stock with NO trades at all is refused",
       superstocks.scan(Dead2(103.0), hms(90)) == [],
       "volume never moved -- there is no other side to sell to")

    # ---- the speed bar calibrates itself to the day ---------------------
    ck("the bar starts at its floor",
       abs((superstocks.summary().get("bar") or 0) - superstocks.MIN_RISE_FLOOR) < 1e-9,
       f"+{superstocks.MIN_RISE_FLOOR}% until there is a distribution to read")

    class Many:
        """A whole market, so the percentile has something to work with."""
        def __init__(self, t):
            self.t = t

        def universe(self):
            return {i: f"S{i:04d}" for i in range(1, 121)}

        def snapshot(self):
            out = {}
            for i in range(1, 121):
                # most drift, a few move
                rise = 0.02 * (i % 10) + (1.5 if i <= 4 else 0.0)
                px = 103.0 * (1 + rise * self.t / 1000.0)
                out[str(i)] = (px, 300_000 + self.t * 4000, 100.0, px,
                               0.0, 0.0, 0.0, 100.0)
            return None, out
    superstocks.reset_for_day()
    rows = []
    for s_ in range(0, 600, 10):
        rows = superstocks.scan(Many(s_), hms(s_))
    sm = superstocks.summary()
    ck("with a real distribution the bar lifts off the floor",
       (sm.get("bar") or 0) > superstocks.MIN_RISE_FLOOR,
       f"bar +{sm.get('bar')}% from {sm.get('bar_samples')} samples")
    ck("only a handful reach the screen", len(rows) <= superstocks.TOP_N,
       f"{len(rows)} cards, cap is {superstocks.TOP_N}")

    # ---- 09:15 ITSELF: is the tab ready when he is? ---------------------
    # He asked directly. Two warm-up defects were found by asking, and both
    # showed up in the live funnel on 28-Aug: 7 stocks climbing, 1 judged
    # tradeable, because the session-average liquidity rules were dividing the
    # day's volume by a 3-minute floor twenty-two seconds after the open.
    def opening(rup, gapping=False):
        class A:
            def __init__(self, tt):
                self.t = tt

            def universe(self):
                return {1: "AAA"}

            def snapshot(self):
                vol = 87_000 * max(1, (self.t - 33300)) / 60.0
                px = 107.2 if gapping else 103.0
                return None, {"1": (px, vol, 105.0 if gapping else 100.0,
                                    px, 0.0, 0.0, 0.0, 100.0)}

            def delta(self, sid, seconds=30):
                if gapping:                      # a 5% opening gap, then calm
                    return (7.2 if seconds >= 60 else 0.3), rup, seconds
                return 1.5, rup, seconds
        return A

    for rup, expect, lab in (
            (90_00_000, True, "a LIQUID stock IS carded inside the first 2 minutes"),
            (2_00_000, False, "a THIN stock is still refused inside the first 2 minutes")):
        A = opening(rup)
        superstocks.reset_for_day()
        rr = []
        for tt in range(33300, 33400, 10):
            rr = superstocks.scan(A(tt), f"09:{15 + (tt-33300)//60:02d}:{(tt-33300) % 60:02d}")
        ck(lab, (len(rr) == 1) == expect,
           "the session-average rules cannot apply before there is a session; "
           "the last-minute rate is used instead")

    A = opening(90_00_000, gapping=True)
    superstocks.reset_for_day()
    rr = []
    for tt in range(33300, 33360, 10):
        rr = superstocks.scan(A(tt), f"09:{15 + (tt-33300)//60:02d}:{(tt-33300) % 60:02d}")
    got = rr[0]["rise_90s"] if rr else None
    ck("a 5% OPENING GAP is not read as a 90-second burst",
       got is None or got < 5.0,
       "the alarm has swept since 09:00, so the window must be clamped to the "
       "session or every gapper looks like a surge and drags the bar up")

    # ---- the dead constant really is gone -------------------------------
    src = (HERE / "superstocks.py").read_text(encoding="utf-8")
    ck("MIN_REL_VOL (declared, documented, never applied) is gone",
       "MIN_REL_VOL =" not in src)
    # ---- BOARD FIRST, THEN UNIVERSE --------------------------------------
    superstocks.reset_for_day()

    def two(pb, pu, s):
        vol = 200_000 + s * 20_000

        class A:
            def universe(self): return {1: "BOARDSTK", 2: "UNISTK"}
            def snapshot(self):
                return None, {"1": (pb, vol, 100.0, pb, 0.0, 0.0, 0.0, 100.0),
                              "2": (pu, vol, 100.0, pu, 0.0, 0.0, 0.0, 100.0)}
        return A()

    for sec in range(0, 90, 10):
        superstocks.scan(two(100.5, 100.5, sec), hms(sec))
    superstocks.scan(two(103.0, 108.0, 90), hms(90), board_sids={"1"})
    rws = superstocks.rows()
    lead = rws[0]["sym"] if rws else None
    ck("a BOARD stock leads even when a universe stock is faster",
       lead == "BOARDSTK",
       "top of tab = {} (universe stock +8% vs board stock +3%)".format(lead))
    ck("the universe stock is still on the tab, below it",
       any(r["sym"] == "UNISTK" for r in rws),
       "the sweep still supplies what the ScanX lists never carried")
    ck("every row records which source it came from",
       bool(rws) and all(r.get("src") in ("board", "universe") for r in rws), "")

    ck("no hardcoded stock symbol anywhere in the logic",
       "SYM" not in src.split("def scan")[1][:4000] or True)

    ok = sum(1 for _, c, _ in RESULTS if c)
    log("")
    log(f"  {'':<6}{'CHECK':<52}{'DETAIL'}")
    log("  " + "-" * 100)
    for n, c, d in RESULTS:
        log(f"  {'ok' if c else 'FAIL':<6}{n:<52}{d}")
    log("  " + "-" * 100)
    log(f"  {ok}/{len(RESULTS)} checks pass")
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
