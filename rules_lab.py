"""
rules_lab.py -- derive the trading rules from the tape, instead of guessing them.

WHY THIS EXISTS
    On 01-Sep I changed the displacement margin twice in one afternoon, in
    OPPOSITE directions, each time off a single observation. 3.0 was too wide to
    ever fire; 8% fired constantly. That is not rule-making, it is reacting to
    the last thing I saw -- and it is exactly how a system gets fitted to noise.

    Sri's rule of thumb, which is the right one: AI decides what the rules should
    be; code decides the trades. This file is the first half of that, done
    honestly. It measures every recorded signal across every session on disk and
    reports what actually happened next. I read the report, and only then do
    constants change in superstocks_lab.py -- with the numbers written beside
    them, and never in the module he trades from.

WHAT IT MEASURES, AND WHY THAT AND NOT WIN RATE
    Win rate is the wrong yardstick: a rule that wins 80% of the time taking
    +0.3% and loses 20% taking -2% is a losing rule. Everything here is scored
    in EXPECTED NET RUPEES PER TRADE at his real position size, after Dhan's
    actual intraday charges on both legs. A rule that cannot pay the charges is
    not an edge, however good its hit rate looks.

HONESTY RULES BUILT IN
    - Every bucket prints its n. A cell with four trades in it is labelled
      thin, because four trades is an anecdote.
    - Results are shown PER SESSION as well as pooled. A rule that only works on
      one day out of four is a property of that day.
    - Nothing here writes to any module. It prints and it saves a report. The
      decision to change a constant stays a decision, taken once, deliberately.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"

POSITION_RS = 1_66_667.0        # Rs 1,00,000 at 5x across 3 positions
FILL_WINDOW = 90                # no recorded price within 90s of the signal = no fill
MIN_PRICE = 20.0


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def charges(buy_val, sell_val):
    turn = buy_val + sell_val
    bro = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
    stt = 0.00025 * sell_val
    exch = 0.0000297 * turn
    sebi = 0.000001 * turn
    stamp = 0.00003 * buy_val
    gst = 0.18 * (bro + exch + sebi)
    return bro + stt + exch + sebi + stamp + gst


def sessions():
    return sorted(p.name[6:14] for p in LOGS.glob("super_2*.jsonl")
                  if (LOGS / f"bars30_{p.name[6:14]}.jsonl").exists())


def load(day):
    bars = defaultdict(list)
    with (LOGS / f"bars30_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("sym") == "AAA":
                continue
            t = sec(d["hhmm"])
            if sec("09:15:00") <= t <= sec("15:30:00"):
                bars[d["sym"]].append((t, d["o"], d["h"], d["l"], d["c"]))
    for s in bars:
        bars[s].sort()
    sig = {}
    with (LOGS / f"super_{day}.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = sec(d["ts"])
            if not (sec("09:15:00") <= t <= sec("15:30:00")):
                continue
            for r in d.get("rows") or []:
                s = r.get("sym")
                if s and s != "AAA" and s not in sig:
                    sig[s] = {"t": t, "from_open": r.get("from_open"),
                              "urgency": r.get("urgency"), "day_pct": r.get("day_pct"),
                              "tover_cr": r.get("tover_cr")}
    return dict(bars), sig


def trade(b, t_sig, target, stop, timecap):
    """One trade on the recorded tape. STOP wins an ambiguous bar."""
    entry = next((x for x in b if x[0] > t_sig), None)
    if entry is None or entry[0] - t_sig > FILL_WINDOW or entry[4] < MIN_PRICE:
        return None
    e_t, e_px = entry[0], entry[4]
    qty = int(POSITION_RS // e_px)
    if qty <= 0:
        return None
    tgt, stp = e_px * (1 + target / 100), e_px * (1 + stop / 100)
    for (t, o, h, l, c) in b:
        if t <= e_t:
            continue
        if l <= stp:
            x, why = stp, "stop"
            break
        if h >= tgt:
            x, why = tgt, "target"
            break
        if t - e_t >= timecap:
            x, why = c, "time"
            break
    else:
        x, why = b[-1][4], "eod"
    bv, sv = qty * e_px, qty * x
    return {"net": sv - bv - charges(bv, sv), "why": why, "px": e_px, "qty": qty}


def report(days, target=3.0, stop=-1.0, timecap=1800, out=print):
    rows = []
    for day in days:
        bars, sig = load(day)
        for s, m in sig.items():
            b = bars.get(s)
            if not b:
                continue
            r = trade(b, m["t"], target, stop, timecap)
            if r:
                rows.append({**m, **r, "day": day, "sym": s})
    return rows


def bucket(rows, key, edges, label):
    out = defaultdict(list)
    for r in rows:
        v = key(r)
        if v is None:
            continue
        name = None
        for lo, hi, nm in edges:
            if lo <= v < hi:
                name = nm
                break
        if name:
            out[name].append(r)
    print(f"\n{label}")
    print(f"  {'bucket':<16}{'n':>5}{'net/trade':>12}{'total':>12}{'win%':>7}"
          f"{'tgt%':>7}{'stop%':>7}   verdict")
    order = [nm for _l, _h, nm in edges]
    for nm in order:
        v = out.get(nm) or []
        if not v:
            continue
        avg = sum(x["net"] for x in v) / len(v)
        w = sum(1 for x in v if x["net"] > 0) * 100 // len(v)
        tg = sum(1 for x in v if x["why"] == "target") * 100 // len(v)
        st = sum(1 for x in v if x["why"] == "stop") * 100 // len(v)
        flag = ("THIN" if len(v) < 10 else ("" if avg > 0 else "LOSES"))
        print(f"  {nm:<16}{len(v):>5}{avg:>11,.0f}{sum(x['net'] for x in v):>12,.0f}"
              f"{w:>6}%{tg:>6}%{st:>6}%   {flag}")
    return out


def main():
    days = sessions()
    if not days:
        print("no sessions with both a super log and bars30")
        return
    print(f"SESSIONS ON DISK: {', '.join(days)}")
    print(f"Position size Rs {POSITION_RS:,.0f} · Dhan intraday charges both legs")
    print("Scored in EXPECTED NET RUPEES PER TRADE, not win rate.")

    rows = report(days)
    print(f"\nBASELINE  (+3.0% target / -1.0% stop / 30-min cap)")
    print(f"  {len(rows)} fillable signals across {len(days)} sessions · "
          f"net Rs {sum(r['net'] for r in rows):,.0f} · "
          f"Rs {sum(r['net'] for r in rows)/max(1,len(rows)):,.0f} per trade")

    print("\n  PER SESSION -- a rule that only works on one day is a property of that day")
    per = defaultdict(list)
    for r in rows:
        per[r["day"]].append(r)
    for d in days:
        v = per.get(d) or []
        if not v:
            print(f"    {d}      no fillable signals")
            continue
        avg = sum(x["net"] for x in v) / len(v)
        print(f"    {d}   {len(v):>4} trades   net Rs {sum(x['net'] for x in v):>9,.0f}"
              f"   Rs {avg:>8,.0f}/trade")

    bucket(rows, lambda r: r.get("from_open"),
           [(0, 3, "0-3%"), (3, 4, "3-4%"), (4, 6, "4-6%"), (6, 8, "6-8%"),
            (8, 12, "8-12%"), (12, 999, "12%+")],
           "HOW FAR IT HAD ALREADY RUN when the card appeared")

    bucket(rows, lambda r: r["t"],
           [(sec("09:15:00"), sec("09:45:00"), "09:15-09:45"),
            (sec("09:45:00"), sec("10:30:00"), "09:45-10:30"),
            (sec("10:30:00"), sec("13:00:00"), "10:30-13:00"),
            (sec("13:00:00"), sec("15:31:00"), "13:00-15:30")],
           "WHEN the card first appeared")

    bucket(rows, lambda r: r.get("urgency"),
           [(0, 12, "<12"), (12, 15, "12-15"), (15, 20, "15-20"),
            (20, 30, "20-30"), (30, 999, "30+")],
           "URGENCY at the signal")

    bucket(rows, lambda r: r.get("tover_cr"),
           [(0, 10, "<10 Cr"), (10, 30, "10-30 Cr"), (30, 100, "30-100 Cr"),
            (100, 1e9, "100 Cr+")],
           "DAY TURNOVER at the signal")

    print("\nTARGET / STOP GRID  (net rupees per trade, all sessions pooled)")
    hdr = "stop / target"
    print("  " + hdr.ljust(14) + "".join(f"{t:>10.1f}%" for t in (1.5, 2, 3, 4, 5)))
    best = None
    for stp in (-0.5, -0.75, -1.0, -1.5, -2.0):
        cells = []
        for tg in (1.5, 2, 3, 4, 5):
            rr = report(days, target=tg, stop=stp)
            avg = sum(x["net"] for x in rr) / len(rr) if rr else 0
            cells.append(avg)
            if best is None or avg > best[0]:
                best = (avg, tg, stp, len(rr))
        print(f"  {stp:>12.2f}%" + "".join(f"{c:>11,.0f}" for c in cells))
    print(f"\n  best cell: +{best[1]}% / {best[2]}%  ->  Rs {best[0]:,.0f} per trade "
          f"over {best[3]} trades")
    print("  CAUTION: this is the best cell OF THE GRID ON THIS DATA. Picking it")
    print("  is fitting unless it also holds per session -- check the table above.")

    try:
        (HERE / "logs" / "RULES_LAB.json").write_text(
            json.dumps({"days": days, "n": len(rows),
                        "net": sum(r["net"] for r in rows),
                        "best_target": best[1], "best_stop": best[2]}, indent=1),
            encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    main()
