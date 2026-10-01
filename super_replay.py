"""
super_replay.py -- rebuild past sessions from this project's OWN recordings so
                   superstocks.py can be replayed against them.

WHY THIS FILE EXISTS
    The alarm's 2,455-stock sweep is NOT persisted. `_log_early()` in
    Movers_app.py says so in its own docstring. So a perfect replay of what
    Super Stocks would have seen is impossible, and any file claiming to do one
    would be lying.

    What IS on disk, every session:

        board_YYYYMMDD.jsonl    every card in every panel, roughly every 5s,
                                with price, day %, day volume and upper circuit.
                                ~200-300 symbols a day, because alarm-promoted
                                early movers become cards too.
        bars30_YYYYMMDD.jsonl   completed 30-second OHLCV bars.

    Between them these give a real, dense, per-symbol trail. That is enough to
    answer the question that actually matters -- WHEN Super Stocks names a
    stock, what happens next -- for every symbol the board saw.

THE LIMITATION, STATED UP FRONT AND NOT BURIED
    The replay universe is the ~200-300 stocks the board recorded, not 2,455.
    Every stock outside that set is invisible to this backtest. That biases the
    result in a KNOWN direction: the thinnest names -- exactly the ones Super
    Stocks was built to catch -- are under-represented, because the board's own
    liquidity floors kept most of them out. So these numbers measure Super
    Stocks' TIMING and RANKING honestly, and understate its DISCOVERY.

TODAY'S OPEN -- and the two proxies that were TESTED AND REJECTED
    The scan gates on "up 2% from today's open", and the open is not stored as
    a card field. Three candidate sources were checked against each other
    before one was chosen:

      1. `early_from_open` on a promoted card.  REJECTED -- the field is on the
         card but was never in the board-log projection, so it is absent from
         every one of the 17 recorded sessions. Zero symbols. This is the same
         instrumentation gap that made the auditor report "0 badges via fast
         ignition" (HANDOVER mistake #3); it is now fixed forward, but it
         cannot be recovered backwards.

      2. `first2_val / first2_vol` -- the VWAP of the first two minutes.
         Available for every symbol, and superficially attractive. REJECTED:
         measured against a known-good open it is off by a MEDIAN of 1.2-2.2%
         and a 90th percentile of 7-11%. The gate itself is 2%. A proxy whose
         typical error is the size of the threshold would decide the backtest
         by itself.

      3. bars30, the open of the first 30-second bar, ACCEPTED ONLY when that
         bar starts at or before 09:15:30 -- i.e. the recorder was already
         watching the stock when the session opened. A later first bar is the
         open of whenever the stock became a card, not the open of the day, and
         silently using it would understate every from_open.

    Source 3 gives 63-80 symbols a session. Symbols without it are DROPPED.
    A narrower honest sample beats a wide invented one.
"""
from __future__ import annotations

import json
import os
import pickle
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGDIR = HERE / "logs" / "movers_board"
CACHE = HERE / "logs" / "movers_board" / "_replay_cache"
CACHE.mkdir(parents=True, exist_ok=True)

WIN_START, WIN_END = "09:15:00", "10:30:00"


def _sec(t):
    return int(t[0:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])


S_START, S_END = _sec(WIN_START), _sec(WIN_END)


def sessions():
    """Every day for which a board log exists, oldest first."""
    return sorted(p.name[6:14] for p in LOGDIR.glob("board_2026*.jsonl"))


# --------------------------------------------------------------- today's open
OPEN_CUTOFF = "09:15:30"   # a first bar later than this is not the day's open


def _opens_from_bars30(day):
    """{sym: open} -- but ONLY where the first bar starts at the session open.

    The cutoff is the whole point. bars30 begins recording a stock when it
    becomes a card, so a stock that first carded at 09:47 has a "first bar"
    at 09:47. Treating that as the open would make a stock that had already
    run 6% look flat, and it would look flat in exactly the cases this backtest
    is supposed to catch.
    """
    f = LOGDIR / f"bars30_{day}.jsonl"
    if not f.exists():
        return {}, {}
    best, sids = {}, {}
    for ln in f.open(encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        hh = r.get("hhmm") or ""
        if len(hh) != 8 or hh < WIN_START:
            continue
        sym = r.get("sym")
        o = r.get("o")
        if not sym or not o:
            continue
        # keep the EARLIEST bar of the session for each symbol
        if sym not in best or hh < best[sym][0]:
            best[sym] = (hh, float(o))
            sids[sym] = str(r.get("sid") or "")
    return ({s: v[1] for s, v in best.items() if v[0] <= OPEN_CUTOFF},
            sids)


# ------------------------------------------------------------------ the trail
def build(day, force=False):
    """One session -> {sym: {...}} with a time-ordered trail.

    Each trail entry: (sec, price, day_volume, upper_circuit, prev_close)
    """
    cf = CACHE / f"replay_{day}.pkl"
    if cf.exists() and not force:
        with cf.open("rb") as fh:
            return pickle.load(fh)

    bf = LOGDIR / f"board_{day}.jsonl"
    if not bf.exists():
        return {}

    opens, sids_b30 = _opens_from_bars30(day)
    trail = defaultdict(dict)          # sym -> {sec: (px, vol, uc, prev)}
    sids = {}
    fo_open = {}                       # open recovered from early_from_open

    for ln in bf.open(encoding="utf-8", errors="replace"):
        i = ln.find('"ts": "')
        if i < 0:
            continue
        ts = ln[i + 18:i + 26]
        if len(ts) != 8 or ts[2] != ":":
            continue
        t = _sec(ts)
        if t < S_START or t > S_END:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        for cards in (r.get("panels") or {}).values():
            for c in cards:
                sym = str(c.get("sym") or "").upper()
                px = c.get("price")
                if not sym or not px:
                    continue
                px = float(px)
                if px <= 0:
                    continue
                dp = c.get("day_pct")
                prev = (px / (1 + dp / 100.0)) if (dp is not None and dp > -99) else 0.0
                vol = float(c.get("day_vol") or c.get("sess_vol") or c.get("tvol") or 0)
                uc = float(c.get("ucl") or 0)
                trail[sym].setdefault(t, (px, vol, uc, prev))
                if sym not in sids:
                    sids[sym] = str(c.get("sid") or "")
                # kept only to REPORT how often the field is missing; never used
                # as an open. See the module docstring.
                fo = c.get("early_from_open")
                if fo is not None and sym not in fo_open:
                    try:
                        fo_open[sym] = px / (1 + float(fo) / 100.0)
                    except (TypeError, ZeroDivisionError):
                        pass

    out = {}
    for sym, d in trail.items():
        if len(d) < 12:                       # too sparse to say anything
            continue
        op = opens.get(sym)
        if not op or op <= 0:
            continue                          # never guess the open
        out[sym] = {
            "sid": sids.get(sym) or sids_b30.get(sym) or sym,
            "open": float(op),
            "trail": sorted(d.items()),
        }
    with cf.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# ------------------------------------------------------- a stand-in for alarm
class FakeAlarm:
    """Presents recorded data with the exact surface superstocks.scan() uses.

    scan() only ever calls alarm.snapshot() and alarm.universe(), and reads
    q[0] ltp, q[1] day volume, q[2] open, q[5] upper circuit, q[7] prev close.
    Matching that surface means the REAL scan() runs here -- not a re-write of
    it that could quietly disagree with what the board actually does.
    """

    def __init__(self, session):
        self.session = session
        self._uni = {}
        self._by_sym = {}
        for i, (sym, d) in enumerate(session.items()):
            sid = d["sid"]
            try:
                key = int(sid)
            except (TypeError, ValueError):
                key = 900000 + i
            self._uni[key] = sym
            self._by_sym[sym] = key
        self._snap = {}

    def universe(self):
        return self._uni

    def snapshot(self):
        return None, self._snap

    def at(self, t):
        """Rebuild the snapshot as it stood at second `t`, using the last
        observation at or before `t` for each symbol -- which is exactly how a
        live quote feed behaves between ticks."""
        snap = {}
        for sym, d in self.session.items():
            tr = d["trail"]
            lo, hi = 0, len(tr) - 1
            if tr[0][0] > t:
                continue
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if tr[mid][0] <= t:
                    lo = mid
                else:
                    hi = mid - 1
            sec, (px, vol, uc, prev) = tr[lo]
            if t - sec > 120:                 # gone quiet -> not quoted
                continue
            snap[self._by_sym[sym]] = (px, vol, d["open"], px, 0.0, uc, 0.0, prev)
        self._snap = snap
        return snap


# ------------------------------------------------------------------- outcomes
def forward(session, sym, t, horizon):
    """(best gain %, worst dip % before that best, seconds to the best)."""
    tr = session[sym]["trail"]
    entry = None
    peak = None
    trough = 0.0
    worst_before = 0.0
    for sec, (px, *_ ) in tr:
        if sec < t:
            continue
        if entry is None:
            entry = px
            peak = px
            peak_t = sec
            t0 = sec
            continue
        if sec > t0 + horizon:
            break
        if px > peak:
            peak, peak_t = px, sec
            worst_before = trough
        trough = min(trough, (px / entry - 1) * 100)
    if entry is None or peak is None:
        return None
    return ((peak / entry - 1) * 100, worst_before, peak_t - t0)


if __name__ == "__main__":
    import sys
    force = "--rebuild" in sys.argv
    for day in sessions():
        s = build(day, force=force)
        if not s:
            print(f"{day}  no usable trail")
            continue
        n = sum(len(v["trail"]) for v in s.values())
        print(f"{day}  symbols={len(s):4d}  observations={n:7d}")
