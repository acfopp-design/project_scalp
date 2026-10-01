"""
warmup.py -- Box 4 of PLAN.md: the 09:09-09:14 "idle time" is not idle.

Sri: "As a human, I keep opening all those stocks manually and be ready so that
I can take trade on those which are getting into bullish. But you as an AI, you
should have much advance techniques."

The machine equivalent of opening every chart is to have every indicator ALREADY
WARM at 09:15:00, computed on real prior bars rather than starting from nothing.

WHY THIS IS NOT OPTIONAL -- measured on MANINDS, 21-Aug, 30-second bars:

    from a cold start at 09:15        with prior-session bars loaded
    EMA8   unavailable until 09:33    live at 09:15
    MA12   unavailable until 09:57    live at 09:15
    MACD   unavailable until 10:07    live at 09:15
    cold EMA8 only converges on the true value at 09:44
    cold MACD was still 0.85 vs 1.03 at 10:21 -- EMA(26) has a long memory

85% of every opportunity this scanner has ever found arrives before 10:30, and
09:15-09:30 alone holds 123 of 164 cards. A cold start means the entire richest
window of the day is traded on indicators that are missing or wrong.

WHAT IT DOES
  1. read the 09:09 list (bullish_list.py) and MyWatchlist
  2. pull true 30-second history per symbol from ticks.dhan.co/getDataS -- the
     same series Sri's own chart is drawn from (Movers_chartfeed.get_seconds)
  3. compute the full stack (sri_stack.py) and cache it under logs/warm/<day>/
  4. report coverage, so a symbol that failed to warm is VISIBLE rather than
     silently trading on nothing

Needs a live fetch, so it runs board-side.

Usage: python3 warmup.py [--day=YYYYMMDD] [--top=60] [--days=3] [--dry]
"""
import json, sys, time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"

DAY  = next((a.split("=")[1] for a in sys.argv if a.startswith("--day=")),
            datetime.now().strftime("%Y%m%d"))
TOP  = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--top=")), 60))
DAYS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--days=")), 3))
DRY  = "--dry" in sys.argv
# A bounded set by design. Fetching 30-second history for 400 names would hammer
# the feed and blow the 09:09-09:14 window; the quote throttle is global and one
# slow call stalls every thread (02-Sep). Warm what matters, and SAY what was
# left cold rather than pretending coverage we do not have.


def targets(day):
    """(sym, sid) to warm: the top of the 09:09 list, plus MyWatchlist names
    that the pre-open also flagged."""
    out, seen = [], set()
    lst = LOGS / f"bullish_0909_{day}.json"
    rows = []
    if lst.exists():
        rows = json.loads(lst.read_text(encoding="utf-8")).get("rows") or []
    for r in rows[:TOP]:
        s = r.get("sym"); sid = r.get("sid")
        if s and sid and s not in seen:
            seen.add(s); out.append((s, str(sid), ",".join(r.get("src") or [])))
    for r in rows[TOP:]:
        if ("preopen" in (r.get("src") or []) and "watchlist" in (r.get("src") or [])
                and r.get("sym") not in seen and r.get("sid")):
            seen.add(r["sym"]); out.append((r["sym"], str(r["sid"]), "watch+preopen"))
    return out


def warm_one(sym, sid, cf, stack, days=DAYS):
    # get_seconds returns (data, error), not the data alone -- unpacking this
    # wrongly looks like success, because a 2-tuple has len 2.
    bars, ferr = cf.get_seconds(sid, interval="30S", days=days)
    if ferr:
        return None, f"feed: {str(ferr)[:60]}"
    if not bars:
        return None, "no bars returned"
    # normalise whatever shape the feed hands back into o/h/l/c/v dicts
    if isinstance(bars, dict) and "c" in bars:
        n = len(bars["c"])
        rows = [{"o": bars["o"][i], "h": bars["h"][i], "l": bars["l"][i],
                 "c": bars["c"][i], "v": (bars.get("v") or [0] * n)[i],
                 "t": (bars.get("t") or [None] * n)[i]} for i in range(n)]
    else:
        rows = [{"o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
                 "c": b.get("c"), "v": b.get("v") or 0, "t": b.get("t")}
                for b in bars]
    rows = [r for r in rows if r["c"]]
    if len(rows) < 40:
        return None, f"only {len(rows)} bars -- MACD needs 35"
    return stack.compute(rows), None


def main():
    import Movers_chartfeed as cf
    import sri_stack as stack
    tg = targets(DAY)
    if not tg:
        print(f"warmup: no 09:09 list for {DAY} -- run bullish_list.py first")
        return
    outdir = LOGS / "warm" / DAY
    if not DRY:
        outdir.mkdir(parents=True, exist_ok=True)
    ok = cold = 0
    reasons = {}
    t0 = time.time()
    for sym, sid, src in tg:
        try:
            rows, err = warm_one(sym, sid, cf, stack)
        except Exception as e:
            rows, err = None, f"{type(e).__name__}: {str(e)[:60]}"
        if rows is None:
            cold += 1; reasons[sym] = err; continue
        ok += 1
        if not DRY:
            last = rows[-1]
            (outdir / f"{sym}.json").write_text(json.dumps(
                {"sym": sym, "sid": sid, "src": src, "bars": len(rows),
                 "warmed_at": datetime.now().strftime("%H:%M:%S"),
                 "state": last, "tail": rows[-60:]}, indent=None), encoding="utf-8")
    print(f"warmup {DAY}: {ok} warm, {cold} cold, of {len(tg)} targets "
          f"in {time.time()-t0:.0f}s -> {outdir}")
    for s, r in list(reasons.items())[:10]:
        print(f"  COLD {s}: {r}")
    if cold:
        print(f"  {cold} symbols will start 09:15 with no indicator history.")


if __name__ == "__main__":
    main()
