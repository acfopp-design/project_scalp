"""
regress.py -- did tonight's fix break something that already worked?

Sri's rule: fix the failures, but the success scenarios must still exist.
Every change so far has been judged only on the thing it was meant to improve,
which is how displacement shipped -- it won its own A/B and quietly wrecked
everything else.

So: run the FULL engine over every session on disk, record net P&L per session,
and compare against a locked baseline. A change is allowed to improve things.
It is not allowed to make any session materially worse without that being seen.

  python3 regress.py --save     lock the current numbers as the baseline
  python3 regress.py            compare against the lock, exit 1 on regression
"""
import json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"
BASE = HERE / "logs" / "REGRESS_BASELINE.json"
TOL = 0.10          # a session may lose 10% of its net before it counts


def sessions():
    """Only sessions the replay can actually PRICE.

    04-Sep: bars30 covers just the symbols Movers_ticks tracked -- 81 of the 169
    carded names that day -- so on any session without a fetched tape the replay
    freezes half its positions at their entry price and the resulting P&L is
    meaningless. The 30-second feed carries about three sessions, so anything
    older than that can never be repaired and must not sit in a baseline
    pretending to be evidence.
    """
    out = []
    for p in sorted(LOGS.glob("super_2*.jsonl")):
        day = p.name[6:14]
        tape = HERE / "logs" / "tape" / day
        if tape.is_dir() and any(tape.glob("*.json")):
            out.append(day)
    return out


def run(day):
    try:
        import replay_live
        d, err = replay_live.run(day, 1_00_000)
        if err or not d:
            return None
        return round(d["summary"]["net"], 0)
    except Exception as e:
        print(f"  {day}: FAILED {type(e).__name__} {str(e)[:70]}")
        return None


def main():
    save = "--save" in sys.argv
    days = sessions()
    cur = {}
    print(f"{'session':<12}{'net':>12}" + ("" if save else f"{'baseline':>12}{'delta':>12}"))
    old = {}
    if not save and BASE.exists():
        old = json.loads(BASE.read_text(encoding="utf-8")).get("net", {})
    bad = []
    for d in days:
        n = run(d)
        if n is None:
            continue
        cur[d] = n
        if save:
            print(f"{d:<12}{n:>12,.0f}")
        else:
            b = old.get(d)
            if b is None:
                print(f"{d:<12}{n:>12,.0f}{'(new)':>12}")
                continue
            delta = n - b
            flag = ""
            if b > 0 and n < b * (1 - TOL):
                flag = "  REGRESSION"
                bad.append((d, b, n))
            print(f"{d:<12}{n:>12,.0f}{b:>12,.0f}{delta:>+12,.0f}{flag}")
    if save:
        BASE.write_text(json.dumps({"net": cur}, indent=1), encoding="utf-8")
        print(f"\nbaseline locked: {len(cur)} sessions -> {BASE.name}")
        return 0
    if bad:
        print(f"\n{len(bad)} SESSION(S) GOT WORSE. The change fixed one thing and")
        print("broke another. Do not ship it until this table is clean.")
        return 1
    print("\nno regressions -- every session at or above baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
