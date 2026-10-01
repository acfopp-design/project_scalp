"""
entry_lab.py -- Box 7: do SRI'S OWN indicators separate winners from losers?

His stack, read off his screenshots, computed by sri_stack.py:
  MA12 / EMA8 cross and the WIDTH of the gap
  CM MACD 12-26-9, and whether the cross is above the zero line
  RSI 14 with an SMA 14 signal
  Parabolic SAR 0.02/0.02/0.2 -- below the candle, and the DISTANCE
  Heiken Ashi SuperTrend 10,3 (and 10,2, since the handoff disagreed)
  Ichimoku 9-26-52-26-26 -- above cloud, cloud thickness
  VWAP -- not on his chart, free from the same bars

The trading engine reads NONE of these. This is the first measurement of
whether it should.

METHOD
  For every card in a session with a real 30-second tape, compute the full
  stack as at the card's first sighting -- causally, using only bars up to that
  second -- then walk FORWARD from the card price using bar LOWS for the stop
  and HIGHS for the rise. Same outcome definition as everything else today.

  Only sessions with a fetched tape are used. bars30 covers roughly half the
  carded names, and the half it covers are the ones that were already moving,
  which is exactly the bias that made every earlier number meaningless.

READ-ONLY.
"""
import json, glob, sys
from collections import defaultdict
from pathlib import Path
import sri_stack as S

TARGET, STOP = 2.0, -1.0
MINPX = 100.0
HERE = Path(__file__).resolve().parent


def tape_days():
    return sorted(p.name for p in (HERE / "logs" / "tape").iterdir()
                  if p.is_dir() and not p.name.endswith("DISCARD"))


def load_tape(day):
    out = {}
    d = HERE / "logs" / "tape" / day
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    for f in d.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = raw.get("t") or []
        c = raw.get("c") or []
        if not ts or len(c) != len(ts):
            continue
        bars = []
        for i, tv in enumerate(ts):
            dt = datetime.fromtimestamp(tv, IST)   # feed epochs are UTC; the board is IST
            if dt.strftime("%Y%m%d") != day:
                continue
            bars.append({"o": (raw.get("o") or c)[i], "h": (raw.get("h") or c)[i],
                         "l": (raw.get("l") or c)[i], "c": c[i],
                         "v": (raw.get("v") or [0]*len(c))[i],
                         "hhmm": dt.strftime("%H:%M:%S")})
        if len(bars) >= 40:
            out[f.stem] = bars
    return out


def cards(day):
    seen = {}
    f = HERE / "logs" / "movers_board" / f"super_{day}.jsonl"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            for r in snap.get("rows") or []:
                s = r.get("sym")
                if s and s != "AAA" and s not in seen:
                    seen[s] = dict(r, seen_at=r.get("first_seen") or snap.get("ts"))
    return seen


def forward(bars, i0, entry):
    best = 0.0
    for b in bars[i0:]:
        if b["l"] and (b["l"] - entry) / entry * 100 <= STOP:
            return best, True
        if b["h"]:
            best = max(best, (b["h"] - entry) / entry * 100)
    return best, False


def collect():
    rows = []
    for day in tape_days():
        tape = load_tape(day)
        for sym, c in cards(day).items():
            t, px = c.get("seen_at") or "", c.get("price") or 0
            if not t or px < MINPX or sym not in tape:
                continue
            bars = tape[sym]
            i = next((k for k, b in enumerate(bars) if b["hhmm"] >= t), None)
            if i is None or i < 40:
                continue
            st = S.compute(bars[:i + 1])[-1]      # causal: only bars up to the card
            up, stopped = forward(bars, i, px)
            rows.append({**st, "sym": sym, "day": day,
                         "up": up, "stopped": stopped,
                         "win": (up >= TARGET and not stopped)})
    return rows


def bucket(rows, key, label):
    agg = defaultdict(lambda: {"n": 0, "win": 0, "stop": 0, "up": 0.0})
    for r in rows:
        k = key(r)
        if k is None:
            continue
        a = agg[k]
        a["n"] += 1; a["win"] += r["win"]; a["stop"] += r["stopped"]; a["up"] += r["up"]
    print(f"\n  {label}")
    print(f"    {'bucket':<18}{'n':>5}{'reached +2%':>13}{'stopped':>9}{'avg best':>10}")
    for k in sorted(agg, key=str):
        a = agg[k]
        if a["n"] < 5:
            continue
        print(f"    {str(k):<18}{a['n']:>5}{100*a['win']/a['n']:>12.0f}%"
              f"{100*a['stop']/a['n']:>8.0f}%{a['up']/a['n']:>9.2f}%")


def main():
    rows = collect()
    days = sorted({r["day"] for r in rows})
    n = len(rows)
    print(f"{len(days)} tape-covered sessions {days}, {n} cards, price >= Rs{MINPX:.0f}")
    if not n:
        return
    print(f"BASE: reached +2% {100*sum(r['win'] for r in rows)/n:.0f}%   "
          f"stopped {100*sum(r['stopped'] for r in rows)/n:.0f}%   "
          f"avg best {sum(r['up'] for r in rows)/n:.2f}%")

    bucket(rows, lambda r: r.get("ema_above"), "EMA8 above MA12 at the card")
    def gapb(r):
        v = r.get("ema_gap_pct")
        if v is None: return None
        for lo, hi, nm in [(-99,-0.1,"below -0.1"),(-0.1,0,"-0.1 to 0"),(0,0.1,"0 to 0.1"),
                           (0.1,0.3,"0.1-0.3"),(0.3,99,"0.3%+ wide")]:
            if lo <= v < hi: return nm
    bucket(rows, gapb, "EMA-MA GAP WIDTH  (Sri: wider = more bullish)")
    bucket(rows, lambda r: r.get("macd_above_zero"), "MACD line above zero  (Sri: strongest)")
    def rsib(r):
        v = r.get("rsi")
        if v is None: return None
        for lo, hi, nm in [(0,40,"<40"),(40,50,"40-50"),(50,65,"50-65 (Sri)"),
                           (65,75,"65-75"),(75,101,"75+")]:
            if lo <= v < hi: return nm
    bucket(rows, rsib, "RSI 14")
    bucket(rows, lambda r: r.get("sar_below"), "SAR below the candle")
    def sarb(r):
        v = r.get("sar_dist_pct")
        if v is None: return None
        for lo, hi, nm in [(-99,0,"SAR above"),(0,0.3,"0-0.3"),(0.3,0.8,"0.3-0.8"),
                           (0.8,99,"0.8%+ far")]:
            if lo <= v < hi: return nm
    bucket(rows, sarb, "SAR DISTANCE  (Sri: far = strong)")
    bucket(rows, lambda r: r.get("st_dir"), "Heiken Ashi SuperTrend 10,3  (+1 = up)")
    bucket(rows, lambda r: r.get("st10_2_dir"), "SuperTrend 10,2  (handoff's version)")
    bucket(rows, lambda r: r.get("above_cloud"), "above the Ichimoku cloud")
    bucket(rows, lambda r: r.get("above_vwap"), "above VWAP")


if __name__ == "__main__":
    main()


def load_tape_warm(day, sessions_back=1):
    """Bars for `day` WITH the previous session(s) in front of them.

    Sri, 05-Sep: "from 9:15 till 9:30 maximum trading happens -- if you say you
    don't have something, that's a concern. A human eye doesn't need it."

    He is right, and the fix is trivial: the tape files already carry three
    sessions and load_tape() was throwing two of them away, so the code could
    not judge whether volume was unusual until it had watched 20 candles of the
    current morning -- blind until about 09:28, through the densest window of
    the day.

    Returns (bars, day_start_index).
    """
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    d = HERE / "logs" / "tape" / day
    out = {}
    for f in d.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts, c = raw.get("t") or [], raw.get("c") or []
        if not ts or len(c) != len(ts):
            continue
        by_day = {}
        for i, tv in enumerate(ts):
            dt = datetime.fromtimestamp(tv, IST)
            k = dt.strftime("%Y%m%d")
            hm = dt.strftime("%H:%M:%S")
            if not ("09:15:00" <= hm <= "15:30:00"):
                continue
            by_day.setdefault(k, []).append(
                {"o": (raw.get("o") or c)[i], "h": (raw.get("h") or c)[i],
                 "l": (raw.get("l") or c)[i], "c": c[i],
                 "v": (raw.get("v") or [0] * len(c))[i], "hhmm": hm, "day": k})
        if day not in by_day:
            continue
        prev = sorted(k for k in by_day if k < day)[-sessions_back:]
        warm = [b for k in prev for b in by_day[k]]
        today = by_day[day]
        if len(today) >= 40:
            out[f.stem] = (warm + today, len(warm))
    return out
