"""shadow_run.py -- what the NEW logic (signal_sim) would have done TODAY.

Paper only, no orders, no board involvement. Reads today's 30-second tape,
replays it forward-only from 09:15, and prints the book trade by trade.

    python shadow_run.py             -> whole session so far
    python shadow_run.py 11:00:00    -> stop at that clock time

Universe is honest: the 09:08 frozen list plus every name the Board had carded
by the moment of each decision -- nothing is tradeable before the Board saw it.
"""
import json, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
import signal_sim as G
import entry_lab as E


def load_day_pct(day):
    out = {}
    f = HERE / "logs" / "movers_board" / f"board_{day}.jsonl"
    if not f.exists():
        return out
    for line in f.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        hm = (d.get("ts") or "")[11:19]
        if not hm:
            continue
        for rows in (d.get("panels") or {}).values():
            for r in rows or []:
                sy, p = r.get("sym"), r.get("day_pct")
                if sy and p is not None:
                    out.setdefault(sy, []).append((hm, float(p)))
    for k in out:
        out[k].sort()
    return out


def load_available(day):
    av = {}
    f = HERE / "logs" / f"bullish_0909_{day}.json"
    if f.exists():
        for r in json.loads(f.read_text(encoding="utf-8")).get("rows", []):
            av[r["sym"]] = "09:15:00"
    b = HERE / "logs" / "movers_board" / f"board_{day}.jsonl"
    if b.exists():
        for line in b.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            hm = max((d.get("ts") or "")[11:19], "09:15:00")
            if len(hm) != 8:
                continue
            for rows in (d.get("panels") or {}).values():
                for r in rows or []:
                    sy = r.get("sym")
                    if sy and hm < av.get(sy, "99:99:99"):
                        av[sy] = hm
    return av


def main():
    day = datetime.now().strftime("%Y%m%d")
    stop = sys.argv[1] if len(sys.argv) > 1 else "15:15:00"
    tape = E.load_tape_warm(day)
    if not tape:
        print(f"shadow_run: no tape for {day}. Run BUILD_TAPE_TODAY.bat first.")
        return 1
    av = load_available(day)
    dp = load_day_pct(day)
    print(f"shadow_run {day}: {len(tape)} symbols with a tape, "
          f"{len(av)} knowable, day% for {len(dp)}, stopping at {stop}")

    G.VOL_MODE = True
    G.DAY_PCT = dp
    G.AVAILABLE_FROM = {s: av[s] for s in av if s in tape}
    G.SQUARE_OFF = stop
    G.ENTRY_TO = min(G.ENTRY_TO, stop)
    G.universe = lambda d: (tape, set(tape), set())
    closed, net = G.run(day, log=print)

    closed.sort(key=lambda c: c["in_t"])
    print(f"\n{'in':10s}{'out':10s}{'sym':13s}{'buy':>10}{'sell':>10}{'qty':>7}"
          f"{'value':>11}  {'why':15s}{'net':>10}")
    for c in closed:
        print(f"{c['in_t']:10s}{c['out_t']:10s}{c['sym']:13s}{c['in']:>10.2f}"
              f"{c['out']:>10.2f}{c['qty']:>7,}{c['qty']*c['in']:>11,.0f}  "
              f"{c['why']:15s}{c['net']:>10,.0f}")
    w = [c for c in closed if c["net"] > 0]
    print(f"\n{len(closed)} trades, {len(w)} wins, charges "
          f"{sum(c['chg'] for c in closed):,.0f}, NET Rs {net:,.0f} "
          f"({net / G.CAPITAL * 100:.2f}% of capital)")
    out = HERE / "logs" / f"SHADOW_{day}.json"
    out.write_text(json.dumps({"day": day, "stop": stop, "net": net,
                               "trades": closed}, indent=1, default=str),
                   encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
