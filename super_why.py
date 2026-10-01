"""
super_why.py -- "why was this stock not on the tab?"

Point it at a stock and a day and it replays that session minute by minute,
showing which rule rejected it at each point and when it finally passed.

There is no guessing here. It runs the SAME gates superstocks.scan() runs, in
the same order, against real Dhan 1-minute bars, and names the first one that
said no.

    python super_why.py HFCL 20260824
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import superstocks as ss                                    # noqa: E402
import super_fetch                                          # noqa: E402
import super_backtest as sb                                 # noqa: E402


def trace(sym, day, log=print):
    cache = super_fetch.load()
    rec = next((r for r in cache.values() if r["sym"] == sym.upper()), None)
    if not rec:
        log(f"  {sym}: not in the downloaded history")
        return
    s = rec["sessions"].get(day)
    if not s:
        log(f"  {sym}: no {day} session")
        return
    bars, op, prev = s["bars"], s["open"], s.get("prev_close") or 0
    sess = sb._from_dhan({k: v for k, v in cache.items() if v["sym"] == sym.upper()}, day)
    al = sb.ReplayAlarm(sess)

    ss.reset_for_day()
    ss._record = lambda rows: None
    old_live = ss.MIN_LIVE_TICKS
    ss.MIN_LIVE_TICKS = 0.0          # not judgeable on 1-minute bars

    log("")
    log(f"  {sym}  {day[6:8]}-{day[4:6]}   open {op}   previous close {prev}")
    log(f"  {'time':<8}{'price':>9}{'from open':>11}{'90s':>8}{'sh/min':>10}"
        f"{'  what happened'}")
    log("  " + "-" * 78)

    shown = None
    last_reason = None
    for b in bars:
        t = b[0] + 60
        al.at(t)
        hhmmss = f"{t//3600:02d}:{t%3600//60:02d}:{t%60:02d}"
        rows = ss.scan(al, hhmmss)
        f = ss.summary()["funnel"]
        px = b[4]
        fo = (px / op - 1) * 100
        r90 = rows[0]["rise_90s"] if rows else None
        shm = rows[0]["sh_min"] if rows else None

        # the reason comes from the scan ITSELF, per symbol. Deriving it from
        # the funnel counters, as this did first, produced confident nonsense --
        # it blamed the liquidity rule for rejections the speed rule had made.
        if rows:
            reason = "ON THE TAB"
            if shown is None:
                shown = hhmmss
        else:
            reason = ss._reject.get(sym.upper(), "(no reason recorded)")

        if reason != last_reason or rows:
            log(f"  {hhmmss[:5]:<8}{px:>9.2f}{fo:>10.2f}%"
                f"{(f'{r90:.2f}%' if r90 is not None else '-'):>8}"
                f"{(f'{shm:,}' if shm else '-'):>10}  {reason}")
            last_reason = reason

    ss.MIN_LIVE_TICKS = old_live
    log("")
    log(f"  first shown: {shown or 'NEVER'}")
    hi = max(x[2] for x in bars)
    log(f"  the stock ran from {op} to {hi} = +{(hi/op-1)*100:.2f}% in this window")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print("usage: python super_why.py SYMBOL YYYYMMDD")
        sys.exit(1)
    trace(a[0], a[1])
