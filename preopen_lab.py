"""
preopen_lab.py -- does the PRE-OPEN book actually predict the morning run?

Box 2.1 of PLAN.md. Opus_preopen.py has scanned 09:00-09:15 for 20 sessions and
archived every snapshot. Nothing has ever checked whether what it sees is worth
anything.

METHOD
  For each session:
    * read logs/movers_board/preopen_<day>.jsonl (many snapshots 09:00-09:15)
    * take the LAST snapshot at or before CUTOFF (default 09:08, when NSE's
      pre-open matching completes) -- that is the information actually available
      when the 9:09 list is frozen
    * also derive the imbalance TREND: last imb / first imb for the same symbol,
      i.e. is the book strengthening through the window (Sri, Box 2 Q1)
    * join to logs/movers_board/bars30_<day>.jsonl and measure what the stock
      then did between 09:15 and 10:30:
        exc   = best long excursion, max over i<=j of (high_j - low_i)/low_i
        hit2  = did it offer >= +2% (the system's target)
        openrun = (max high - first open) / first open, a stricter measure that
                  does NOT allow buying a later dip

  BASELINE is the point. A bucket only means something against the base rate of
  every stock on the tape that same morning, so the base rate is printed first.

READ-ONLY. Writes nothing.
"""
import json, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BD = HERE / "logs" / "movers_board"
CUTOFF = next((a.split("=")[1] for a in sys.argv if a.startswith("--cutoff=")), "09:08:00")
T0, T1 = "09:15:00", "10:30:00"
MIN_PX = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--minpx=")), 100.0))


def outcomes(day):
    """symbol -> (best excursion %, open-run %, avg price) for 09:15-10:30."""
    bars = defaultdict(list)
    f = BD / f"bars30_{day}.jsonl"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                b = json.loads(line)
            except Exception:
                continue
            if T0 <= (b.get("hhmm") or "") < T1 and b.get("sym"):
                bars[b["sym"]].append(b)
    out = {}
    for sym, bs in bars.items():
        if len(bs) < 10:
            continue
        bs.sort(key=lambda x: x.get("t") or 0)
        lo = None; best = 0.0
        for b in bs:
            l = b.get("l") or 0; h = b.get("h") or 0
            if l > 0 and (lo is None or l < lo):
                lo = l
            if lo and h > 0:
                best = max(best, (h - lo) / lo * 100)
        op = bs[0].get("o") or bs[0].get("c") or 0
        hi = max(b.get("h") or 0 for b in bs)
        openrun = ((hi - op) / op * 100) if op else 0.0
        px = [b.get("c") or 0 for b in bs if (b.get("c") or 0) > 0]
        out[sym] = (best, openrun, (sum(px) / len(px)) if px else 0.0)
    return out


def preopen_rows(day):
    """last snapshot at/before CUTOFF, plus imbalance trend across the window."""
    f = BD / f"preopen_{day}.jsonl"
    if not f.exists():
        return {}
    first_imb, last = {}, {}
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            hm = (snap.get("ts") or "")[-8:]
            for r in snap.get("rows") or []:
                sym = r.get("sym")
                if not sym:
                    continue
                if sym not in first_imb and r.get("imb") is not None:
                    first_imb[sym] = r.get("imb")
                if hm <= CUTOFF:
                    last[sym] = r
    for sym, r in last.items():
        f0 = first_imb.get(sym)
        r["imb_trend"] = (round(r["imb"] / f0, 2)
                          if f0 and f0 > 0 and r.get("imb") is not None else None)
    return last


def bucket(rows, keyfn, label):
    agg = defaultdict(lambda: {"n": 0, "exc": [], "hit2": 0, "openrun": []})
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        a = agg[k]
        a["n"] += 1
        a["exc"].append(r["exc"])
        a["openrun"].append(r["openrun"])
        if r["exc"] >= 2.0:
            a["hit2"] += 1
    print(f"\n  {label}")
    print(f"    {'bucket':<18}{'n':>5}{'avgExc%':>9}{'hit2%':>8}{'avgOpenRun%':>13}")
    for k in sorted(agg, key=lambda x: (str(type(x)), x)):
        a = agg[k]
        if a["n"] < 3:
            continue
        print(f"    {str(k):<18}{a['n']:>5}{sum(a['exc'])/a['n']:>9.2f}"
              f"{100*a['hit2']/a['n']:>8.0f}{sum(a['openrun'])/a['n']:>13.2f}")


def main():
    days = sorted(p.stem.split("_")[1] for p in BD.glob("preopen_*.jsonl"))
    rows, base_n, base_exc, base_hit = [], 0, 0.0, 0
    used = []
    for day in days:
        out = outcomes(day)
        if not out:
            continue
        used.append(day)
        for sym, (exc, openrun, px) in out.items():
            if px >= MIN_PX:
                base_n += 1; base_exc += exc; base_hit += (exc >= 2.0)
        for sym, r in preopen_rows(day).items():
            if sym not in out:
                continue
            exc, openrun, px = out[sym]
            if px < MIN_PX:
                continue
            rows.append({**r, "day": day, "exc": exc, "openrun": openrun})

    print(f"preopen_lab: {len(used)} sessions with both files, cutoff {CUTOFF}, "
          f"outcome window {T0}-{T1}, min price Rs{MIN_PX:.0f}")
    print(f"\nBASELINE -- every stock on the tape those mornings:")
    print(f"  n={base_n}  avgExc={base_exc/max(base_n,1):.2f}%  hit2={100*base_hit/max(base_n,1):.0f}%")
    print(f"\nPRE-OPEN FLAGGED and present on the tape: n={len(rows)}")
    if rows:
        e = [r["exc"] for r in rows]
        h = sum(1 for x in e if x >= 2.0)
        print(f"  avgExc={sum(e)/len(e):.2f}%  hit2={100*h/len(e):.0f}%")

    bucket(rows, lambda r: r.get("verdict"), "by VERDICT")
    bucket(rows, lambda r: r.get("side"), "by SIDE")

    def imb_b(r):
        v = r.get("imb")
        if v is None: return None
        for lo, hi, lbl in [(0,1,"<1"),(1,2,"1-2"),(2,5,"2-5"),(5,15,"5-15"),(15,1e9,"15+")]:
            if lo <= v < hi: return lbl
        return None
    bucket(rows, imb_b, "by IMBALANCE (buy value / sell value)")

    def pct_b(r):
        v = r.get("pct")
        if v is None: return None
        for lo, hi, lbl in [(-1e9,0,"gap down"),(0,1,"0-1%"),(1,2,"1-2%"),(2,4,"2-4%"),(4,1e9,"4%+")]:
            if lo <= v < hi: return lbl
        return None
    bucket(rows, pct_b, "by PRE-OPEN GAP")

    def tr_b(r):
        v = r.get("imb_trend")
        if v is None: return None
        return "strengthening" if v >= 1.2 else ("fading" if v <= 0.8 else "flat")
    bucket(rows, tr_b, "by IMBALANCE TREND since 09:00 (Sri's question)")


if __name__ == "__main__":
    main()
