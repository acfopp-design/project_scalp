"""
scanx_check.py -- the ScanX Momentum Blast tab's own test suite.

Written with the four fixture bugs that cost time on the Super Stocks tab
already designed out:

  * the snapshot is keyed by STRING and the universe by INT, exactly as the
    live alarm does it. Keying both with ints is how a whole tab sat empty for
    two days while every check passed.
  * volume always GROWS. A cumulative counter that goes backwards is a feed
    glitch, not a market, and a fixture that does it fails on the wrong rule.
  * every fixture states the numbers it feeds in its detail string, so a wrong
    fixture is visible in the output instead of being inferred later.
  * each check keeps the OTHER filters satisfied so it measures exactly one.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scanx_blast as sx                                    # noqa: E402

RESULTS = []


def ck(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class Alarm:
    """Shaped exactly like Movers_alarm: snapshot keyed str, universe keyed int."""

    MASTER = "does-not-exist.csv"          # forces universe() to fall back to EQ

    def __init__(self, px=110.0, op=100.0, prev=100.0, vol=500_000):
        self.px, self.op, self.prev, self.vol = px, op, prev, vol

    def universe(self):
        return {1: "AAA"}                                    # INT

    def snapshot(self):
        return None, {"1": (self.px, self.vol, self.op, self.px,
                            0.0, 0.0, 0.0, self.prev)}       # STR


def daily(rsi=75.0, macd=2.0, st=90.0, avg_vol=1_000_000, prev=100.0):
    sx._daily.clear()
    sx._daily["AAA"] = {"rsi": rsi, "macd": macd, "st": st,
                        "avg_vol": avg_vol, "prev_close": prev}


def fresh():
    sx._uni_cache["v"] = None
    sx.reset_for_day()


def main(log=print):
    sx._record = lambda rows: None            # never touch the live day log

    # ---- indicator maths, against values that can be checked by hand ----
    # NOT `x or default`. RSI of a straight decline is exactly 0.0, which is
    # falsy, so `or 100` silently replaced a correct answer with a wrong one and
    # the check failed against working code. Zero is a value, not a missing one.
    _up = sx.rsi([100 + i for i in range(40)])
    _dn = sx.rsi([200 - i for i in range(40)])
    ck("RSI of a steadily rising series is high",
       _up is not None and _up > 95, f"got {_up:.1f}")
    ck("RSI of a steadily falling series is low",
       _dn is not None and _dn < 5, f"got {_dn:.1f}")
    ck("RSI needs enough history", sx.rsi([1, 2, 3]) is None)
    _mu = sx.macd_hist([100 + i for i in range(60)])
    _md = sx.macd_hist([200 - i for i in range(60)])
    ck("MACD histogram is positive on a rising series",
       _mu is not None and _mu > 0, f"got {_mu:+.3f}")
    ck("MACD histogram is negative on a falling series",
       _md is not None and _md < 0, f"got {_md:+.3f}")
    c = [100 + i for i in range(60)]
    st = sx.supertrend([x + 1 for x in c], [x - 1 for x in c], c)
    ck("Supertrend sits BELOW a rising price", st is not None and st < c[-1],
       f"band {st:.1f} against price {c[-1]}")

    # ---- the filters, one at a time -------------------------------------
    fresh(); daily()
    r = sx.scan(Alarm(px=110.0, op=100.0, prev=100.0), "09:30:00")
    ck("a stock passing every filter is carded", len(r) == 1,
       "up 10% on the day, above open, RSI 75, MACD +2, above Supertrend 90")
    ck("the card carries the four screener values",
       r and all(r[0][k] is not None for k in ("rsi", "macd_h", "st", "vol_x")))

    fresh(); daily()
    ck("a stock BELOW its open is refused",
       sx.scan(Alarm(px=99.0, op=100.0, prev=90.0), "09:30:00") == [],
       "day change +10% but under the open -- ScanX requires both")

    fresh(); daily()
    ck("a stock up less than 1% on the day is refused",
       sx.scan(Alarm(px=100.5, op=100.0, prev=100.0), "09:30:00") == [],
       "+0.5% against the 1.00% filter")

    fresh(); daily(rsi=55.0)
    ck("daily RSI under 60 is refused",
       sx.scan(Alarm(), "09:30:00") == [], "RSI 55 against the 60 filter")

    fresh(); daily(macd=-0.5)
    ck("a negative daily MACD histogram is refused",
       sx.scan(Alarm(), "09:30:00") == [], "MACD -0.5 against the >= 0 filter")

    fresh(); daily(st=120.0)
    ck("a price below its daily Supertrend is refused",
       sx.scan(Alarm(px=110.0), "09:30:00") == [], "price 110 against band 120")

    fresh(); daily(avg_vol=50_000_000)
    ck("ordinary volume is refused",
       sx.scan(Alarm(), "09:30:00") == [],
       "today's pace well under 1.5x its own 20-day average")

    # ---- the switches actually switch -----------------------------------
    fresh(); daily(rsi=55.0)
    sx.USE_RSI = False
    ck("turning RSI off lets the same stock through",
       len(sx.scan(Alarm(), "09:30:00")) == 1)
    sx.USE_RSI = True

    # ---- it cannot be broken by the feed --------------------------------
    class Dead(Alarm):
        def snapshot(self):
            raise RuntimeError("feed down")
    fresh(); daily()
    ck("a dead feed cannot raise",
       sx.scan(Dead(), "09:30:00") == [] and sx.summary()["err"])

    class Junk(Alarm):
        def snapshot(self):
            return None, {"1": (None, "x", 0), "2": ()}
    fresh(); daily()
    ck("malformed quotes cannot raise", sx.scan(Junk(), "09:30:00") == [])

    fresh()
    sx._daily.clear()
    r = sx.scan(Alarm(), "09:30:00")
    ck("with no daily values it says so, and does not pretend",
       r == [] and "daily values not loaded" in (sx.summary()["why_empty"] or ""),
       sx.summary()["why_empty"])

    # ---- the str/int key trap that emptied the other tab ----------------
    fresh(); daily()
    sx.scan(Alarm(), "09:30:00")
    ck("string-keyed sweep resolves against an int-keyed universe",
       sx.summary()["scanned"] == 1,
       "this exact mismatch left Super Stocks empty for two days")

    # ---- an empty tab must name the rule that emptied it ----------------
    fresh(); daily()
    sx.scan(Alarm(px=100.2, op=100.0, prev=100.0), "09:30:00")
    ck("an empty tab explains itself",
       "up 1% on the day" in (sx.summary()["why_empty"] or ""),
       sx.summary()["why_empty"])

    fresh(); daily()
    sx.scan(Alarm(px=100.2, op=100.0, prev=100.0), "09:30:00")
    ck("and it names the rule for a single stock too",
       "day change" in (sx.why("AAA") or ""), sx.why("AAA"))

    # ---- every gating value is written to the log -----------------------
    for key in ("rsi", "macd_h", "st", "vol_x", "day_pct", "sh_min"):
        ck(f"{key} is written to the log", key in sx._LOGGED,
           "a gate nobody can see is a gate nobody can trust")

    ok = sum(1 for _n, c, _d in RESULTS if c)
    log("")
    log(f"  {'':<6}{'CHECK':<58}{'DETAIL'}")
    log("  " + "-" * 104)
    for n, c, d in RESULTS:
        log(f"  {'ok' if c else 'FAIL':<6}{n:<58}{d}")
    log("  " + "-" * 104)
    log(f"  {ok}/{len(RESULTS)} checks pass")
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
