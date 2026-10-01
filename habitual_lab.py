"""
habitual_lab.py -- which stocks are HABITUALLY bullish intraday?

Sri, 04-Sep: "Nothing to do with %. Which all stocks are commonly bullish in
nature irrespective of the news. Ex: HFCL etc. This is one input for your logic
to keep an eye."

So the measure is NOT "up X% over the month". It is: how OFTEN does this name
offer a real intraday long run? A stock that runs 4% on six mornings out of
eight is habitually bullish. One that gapped +30% once and never moved again is
not.

METHOD
  For each symbol, each session, inside the trading window:
    best long excursion = max over i<j of (high[j] - low[i]) / low[i]
  i.e. the best move a long could have caught, buying any dip and selling any
  later high. Computed by carrying a running minimum low -- O(n).

  A session "runs" if that excursion >= RUN_PCT.
  habitual score = runs / sessions_present.

READ-ONLY. Reads logs/movers_board/bars30_*.jsonl and writes nothing except its
own report to stdout (and --json to logs/habitual_<date>.json).

HONEST LIMIT, stated up front so it is not forgotten:
  bars30 is written by Movers_ticks from the BOARD's own universe. It is
  therefore DOWNSTREAM of what the board already watches. This ranks names we
  already see; it cannot discover a habitual mover the board has never tracked.
  Widening that needs daily history for the full 2,673-name NSE EQ universe,
  which needs a Windows-side fetch (this shell has no route to Dhan).
  Same class of bias that killed card_lab.py -- named here so nobody forgets it.
"""
import json, glob, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BARS = HERE / "logs" / "movers_board"

RUN_PCT   = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--run=")), 3.0))
T_FROM    = next((a.split("=")[1] for a in sys.argv if a.startswith("--from=")), "09:15:00")
T_TO      = next((a.split("=")[1] for a in sys.argv if a.startswith("--to=")),   "10:30:00")
MIN_SESS  = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--minsess=")), 4))
MIN_PRICE = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--minpx=")), 20.0))


def sessions():
    for f in sorted(BARS.glob("bars30_*.jsonl")):
        day = f.stem.split("_")[1]
        rows = defaultdict(list)
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    b = json.loads(line)
                except Exception:
                    continue
                hm = b.get("hhmm") or ""
                if not (T_FROM <= hm < T_TO):
                    continue
                sym = b.get("sym")
                if not sym:
                    continue
                rows[sym].append(b)
        yield day, rows


def best_excursion(bars):
    """Max (high_j - low_i)/low_i for i <= j. Running-minimum, O(n)."""
    bars = sorted(bars, key=lambda b: b.get("t") or 0)
    lo = None
    best = 0.0
    for b in bars:
        l = b.get("l") or 0.0
        h = b.get("h") or 0.0
        if l > 0 and (lo is None or l < lo):
            lo = l
        if lo and lo > 0 and h > 0:
            e = (h - lo) / lo * 100.0
            if e > best:
                best = e
    return best


def main():
    stat = defaultdict(lambda: {"sess": 0, "runs": 0, "exc": [], "px": [], "days": []})
    ndays = 0
    for day, rows in sessions():
        ndays += 1
        for sym, bars in rows.items():
            if len(bars) < 10:                      # too thin to judge
                continue
            px = [b.get("c") or 0 for b in bars if (b.get("c") or 0) > 0]
            if not px or (sum(px) / len(px)) < MIN_PRICE:
                continue
            e = best_excursion(bars)
            s = stat[sym]
            s["sess"] += 1
            s["exc"].append(e)
            s["px"].append(sum(px) / len(px))
            if e >= RUN_PCT:
                s["runs"] += 1
                s["days"].append(day[4:])

    out = []
    for sym, s in stat.items():
        if s["sess"] < MIN_SESS:
            continue
        out.append({
            "sym": sym,
            "sessions": s["sess"],
            "runs": s["runs"],
            "hit_pct": round(100.0 * s["runs"] / s["sess"], 1),
            "avg_exc": round(sum(s["exc"]) / len(s["exc"]), 2),
            "max_exc": round(max(s["exc"]), 2),
            "avg_px": round(sum(s["px"]) / len(s["px"]), 1),
            "run_days": s["days"],
        })
    out.sort(key=lambda r: (-r["hit_pct"], -r["avg_exc"]))

    print(f"habitual_lab: {ndays} sessions, window {T_FROM}-{T_TO}, "
          f"run>={RUN_PCT}%, min {MIN_SESS} sessions, min price Rs{MIN_PRICE:.0f}")
    print(f"symbols judged: {len(out)} (of {len(stat)} seen)")
    print()
    print(f"{'SYM':<14}{'sess':>5}{'runs':>6}{'hit%':>7}{'avgRun%':>9}{'maxRun%':>9}{'px':>9}   run days")
    for r in out[:40]:
        print(f"{r['sym']:<14}{r['sessions']:>5}{r['runs']:>6}{r['hit_pct']:>7}"
              f"{r['avg_exc']:>9}{r['max_exc']:>9}{r['avg_px']:>9}   {','.join(r['run_days'])}")

    if "--json" in sys.argv:
        p = HERE / "logs" / "habitual_movers.json"
        p.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
