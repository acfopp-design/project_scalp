"""
super_lag.py -- how LATE is the tab, against what the stock actually did?

His complaint, repeated across three sessions and checked against Dhan's own
charts: the stock starts moving at 09:15-09:20 and the tab says so at
09:23-09:44. Hit rate never measured that. This does.

THE REAL MOVE START, defined so it cannot be argued with
    From real Dhan 1-minute bars, find the stock's best CONTINUOUS climb inside
    09:15-10:30: a run that gains at least MIN_RUN% and never gives back more
    than GIVEBACK% from its own running peak. The first bar of that run is when
    the move started. No judgement, no eyeballing, same rule for every stock.

WHAT IS SCORED
    lag          tab's first flag time minus the real move start, in minutes.
                 Negative means the tab was EARLY.
    caught       flagged within CAUGHT_MIN minutes of the start
    missed       the move happened and the tab never flagged it at all
    left on table how much of the run was already gone by the time it flagged

    Reported alongside hit rate and cards a day, because a tab that flags
    everything instantly would score perfectly on lag and be useless.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import super_backtest as sb                                 # noqa: E402
import super_fetch                                          # noqa: E402
import superstocks as ss                                    # noqa: E402

MIN_RUN = 2.0            # a "move" is a continuous climb of at least this
GIVEBACK = 1.0           # ...that never gives back more than this from its peak
CAUGHT_MIN = 3.0         # flagged within this many minutes = caught in time


def real_moves(cache, day):
    """{sym: (start_sec, peak_sec, gain%)} -- every real continuous climb."""
    out = {}
    for rec in cache.values():
        s = rec["sessions"].get(day)
        if not s or len(s["bars"]) < 30:
            continue
        bars = s["bars"]
        best = None
        i0, pk, pkt = 0, bars[0][4], 0
        for i, b in enumerate(bars):
            c = b[4]
            if c > pk:
                pk, pkt = c, i
            if (pk - c) / pk * 100 > GIVEBACK:
                g = (bars[pkt][4] / bars[i0][4] - 1) * 100
                if g >= MIN_RUN and (best is None or g > best[2]):
                    best = (bars[i0][0], bars[pkt][0], g)
                i0, pk, pkt = i, c, i
        g = (bars[pkt][4] / bars[i0][4] - 1) * 100
        if g >= MIN_RUN and (best is None or g > best[2]):
            best = (bars[i0][0], bars[pkt][0], g)
        if best:
            # only moves that are TRADEABLE -- there is no point scoring the
            # tab on stocks it is right to refuse
            vol = sum(x[5] for x in bars)
            m = len(bars)
            px = bars[-1][4]
            if (vol / m >= ss.MIN_SHARES_PER_MIN
                    and vol * px / m >= ss.MIN_SUSTAINED_RS_PER_MIN):
                out[rec["sym"]] = best
    return out


def measure(sessions, cache, source, label, log=print):
    sup, _g, _b = sb.run(sessions, log=lambda m: None, source=source)
    first = {}
    for r in sup:
        first.setdefault((r["day"], r["sym"]), r["t"])
    lags, caught, missed, lost, total = [], 0, 0, [], 0
    for S in sessions:
        rm = real_moves(cache, S.day)
        for sym, (t0, tpk, gain) in rm.items():
            total += 1
            ft = first.get((S.day, sym))
            if ft is None:
                missed += 1
                continue
            lag = (ft - (t0 + 60)) / 60.0      # bars are stamped at their close
            lags.append(lag)
            if lag <= CAUGHT_MIN:
                caught += 1
            span = max(1.0, (tpk - t0) / 60.0)
            lost.append(min(100.0, max(0.0, (ft - t0) / 60.0 / span * 100)))
    st = sb._stats(sup)
    return {
        "label": label, "n": st["n"], "per_day": st["n"] / len(sessions),
        "worked": st["worked"], "lo": st["lo"], "hi": st["hi"],
        "med_gain": st["med_best"],
        "moves": total, "flagged": len(lags), "missed": missed,
        "med_lag": statistics.median(lags) if lags else None,
        "caught_pct": caught / total * 100 if total else 0,
        "cover": len(lags) / total * 100 if total else 0,
        "med_lost": statistics.median(lost) if lost else None,
    }


def report(variants, log=print):
    cache = super_fetch.load()
    sessions, src = sb.load_sessions(log=lambda m: None)
    rows = []
    for label, setup in variants:
        old = {k: getattr(ss, k) for k in setup}
        for k, v in setup.items():
            setattr(ss, k, v)
        try:
            rows.append(measure(sessions, cache, src, label, log))
        finally:
            for k, v in old.items():
                setattr(ss, k, v)
    log("")
    log(f"  {'variant':<26}{'cards/day':>10}{'worked':>8}{'med gain':>10}"
        f"{'catches':>9}{'in 3 min':>10}{'med lag':>9}{'of run gone':>13}")
    log("  " + "-" * 95)
    for r in rows:
        cov = f"{r['cover']:.0f}%"
        cau = f"{r['caught_pct']:.0f}%"
        lag = f"{r['med_lag']:+.1f}m" if r['med_lag'] is not None else "-"
        lost = f"{r['med_lost']:.0f}%" if r['med_lost'] is not None else "-"
        log(f"  {r['label']:<26}{r['per_day']:>10.1f}{r['worked']:>7.0f}%"
            f"{r['med_gain']:>9.2f}%{cov:>9}{cau:>10}{lag:>9}{lost:>13}")
    log("")
    log(f"  measured against {rows[0]['moves']} real tradeable climbs of "
        f"{MIN_RUN}%+ across {len(sessions)} sessions")
    log("  catches = share of those the tab flagged at all")
    log(f"  in 3 min = share flagged within {CAUGHT_MIN:.0f} minutes of the move starting")
    log("  of run gone = how much of the climb had already happened when it flagged")
    return rows


if __name__ == "__main__":
    report([
        ("now (fixed 2% + burst)", {"LEVEL_ON": False, "EARLY_NO_BURST_MIN": -1}),
        ("market-relative level", {"LEVEL_ON": True, "EARLY_NO_BURST_MIN": -1}),
        ("no burst first 15 min", {"LEVEL_ON": False, "EARLY_NO_BURST_MIN": 15.0}),
        ("both", {"LEVEL_ON": True, "EARLY_NO_BURST_MIN": 15.0}),
    ])
