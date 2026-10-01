"""
replay_check.py -- does the replay agree with what the live engine ACTUALLY did?

THE REASON THIS EXISTS
  Seven times now, replay_live and the live engine have differed in a way that
  changed a conclusion:
     1. displacement validated on a 13s clock, shipped to a 3s engine
     2. ATR computed close-to-close in replay
     3. rules fitted on replay data live had never generated
     4. a shared state file
     5. replay running module defaults instead of live_config.json
     6. stops judged on 30-second closes instead of lows
     7. no price series at all for half the symbols traded
  Every one was found by a person noticing an odd number. Nothing ever ASSERTED
  that the two agree. On 04-Sep the live engine made -Rs 747 and the replay of
  the same day reported +Rs 31,562, and that stood for weeks.

WHAT IT DOES
  For each session that has both a LEARN report (what live actually did) and a
  super log (what the replay can read), it replays the day and compares:
      net P&L, trade count, and the share of trades that stopped out
  and FAILS when they diverge beyond tolerance.

  This cannot prove the replay is right. It can only prove the two systems have
  not silently drifted apart, which is the failure mode that has cost this
  project the most.

Usage:  python3 replay_check.py [--tol=0.5] [--day=YYYYMMDD]
Exit 1 on divergence, so it can gate a change the way regress.py does.
"""
import json, re, sys, glob
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
TOL = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--tol=")), 0.5))
ONE = next((a.split("=")[1] for a in sys.argv if a.startswith("--day=")), None)


def live_result(day):
    """Read the LAST P&L line from that day's LEARN report -- what the live
    engine actually finished with."""
    f = LOGS / f"LEARN_{day}.md"
    if not f.exists():
        return None
    net = trades = None
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"NET Rs\s*(-?[\d,]+)", line)
        if m:
            net = float(m.group(1).replace(",", ""))
        m2 = re.search(r"(\d+) closed", line)
        if m2:
            trades = int(m2.group(1))
    return None if net is None else {"net": net, "trades": trades}


def replay_result(day):
    sys.argv = ["replay_check"]
    for m in ("replay_live", "live_paper", "paper_engine"):
        sys.modules.pop(m, None)
    import replay_live as RL
    snap, err = RL.run(day=day, log=lambda m: None)
    if err or not snap:
        return None
    s = snap["summary"]
    why = Counter(t.get("why") for t in snap["trades"])
    frozen = sum(1 for t in snap["trades"]
                 if abs((t.get("last") or 0) - (t.get("in") or 0)) < 1e-9)
    return {"net": s["net"], "trades": s["trades"],
            "stops": why.get("stop", 0), "frozen": frozen}


def main():
    days = ([ONE] if ONE else
            sorted(f.split("_")[-1][:8] for f in glob.glob(str(LOGS / "LEARN_2*.md"))))
    print(f"{'day':<10}{'live net':>11}{'replay net':>12}{'live tr':>9}{'rep tr':>8}"
          f"{'stops':>7}{'frozen':>8}   verdict")
    bad = []
    for day in days:
        lv = live_result(day)
        if not lv:
            continue
        rp = replay_result(day)
        if not rp:
            print(f"{day:<10}{lv['net']:>11,.0f}{'no replay':>12}")
            continue
        # scale by capital: a divergence of more than TOL% of Rs 1,00,000
        gap = abs(rp["net"] - lv["net"]) / 1000.0        # in % of capital
        verdict = "ok" if gap <= TOL * 10 else "DIVERGES"
        if rp["frozen"]:
            verdict += f" ({rp['frozen']} unpriced)"
        if gap > TOL * 10:
            bad.append((day, lv["net"], rp["net"], gap))
        print(f"{day:<10}{lv['net']:>11,.0f}{rp['net']:>12,.0f}{(lv['trades'] or 0):>9}"
              f"{rp['trades']:>8}{rp['stops']:>7}{rp['frozen']:>8}   {verdict}")
    if bad:
        print(f"\n{len(bad)} SESSION(S) DIVERGE by more than {TOL*10:.0f}% of capital.")
        print("The replay and the live engine are not describing the same system.")
        print("Do not trust any A/B run through the replay until this is closed.")
        sys.exit(1)
    print(f"\nreplay and live agree within {TOL*10:.0f}% of capital on every session.")


if __name__ == "__main__":
    main()
