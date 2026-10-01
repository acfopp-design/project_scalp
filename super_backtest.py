"""
super_backtest.py -- does the SUPER STOCKS tab actually find anything?

WHAT IT DOES
    Replays past sessions second by second and calls the REAL
    `superstocks.scan()` -- the same function the live tab calls, not a
    re-implementation of it. If the tab is wrong, this is wrong in the same
    way, which is the only kind of backtest worth having.

    Every time a stock appears in the Super list for the FIRST time that
    session, that is one signal. What happened next is then measured.

THE FOUR NUMBERS THAT MATTER, AND WHY

    worked            gained >= 0.5% within 10 minutes of appearing.
                      Scored identically to every other signal in this project
                      (badges: ~44%, HOTT 09:15-09:20: 61%, baseline: ~29%)
                      so the answer is comparable rather than freestanding.

    beat costs        gained more than 0.107%. That is the real break-even
                      measured off Dhan's own pricing page: Rs 53.14 round trip
                      on a Rs 50,000 position. A signal that "works" 60% of the
                      time but only ever by 0.05% is a losing signal.

    MAE               how far it went AGAINST him before it went for him.
                      He has a hard stop; a signal that routinely dips 1% first
                      is untradeable however well it ends.

    time to peak      his actual question -- "how long can I continue to hold".

THE BASELINES -- WITHOUT THESE THE RESULT IS MEANINGLESS
    Four badges on this board were believed for weeks and all came in at 44%,
    against a 44% baseline. So three comparisons are run every time:

      1. ANY MOMENT        every stock, every tick. Doing nothing.
      2. THE PLAIN GATE    every stock up 2%+ from its open, no other rule.
                           This is the one that matters most: it isolates
                           whether the still-making-highs / relative-volume /
                           urgency machinery adds anything AT ALL, or whether
                           the whole tab reduces to "it went up 2%".
      3. SUPER STOCKS      the actual tab.

    If 3 does not clearly beat 2, the extra rules are decoration and should be
    deleted rather than defended.

DATA
    Prefers real Dhan 1-minute bars (`super_fetch.py`, whole universe, true
    session open). Falls back to the board-log replay (`super_replay.py`),
    which is honest but narrow -- see that file's docstring for exactly how
    narrow and why.
"""
from __future__ import annotations

import math
import statistics
import sys
from bisect import bisect_right
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import superstocks                                        # noqa: E402
import super_replay                                       # noqa: E402

BREAKEVEN = 0.107        # % -- Rs 53.14 round trip on Rs 50,000, measured
TARGET = 0.5             # % -- the project's standard "worked" bar
HORIZON = 600            # seconds to judge a signal over
TICK = 10                # the live tab scans every 10 seconds
WIN_START, WIN_END = 9 * 3600 + 15 * 60, 10 * 3600 + 30 * 60


# ============================================================== data adapters
class Session:
    """One trading day, in the shape the replay needs.

    sym -> {sid, open, prev_close, t[], px[], vol[], hi[], lo[], uc}
    """

    def __init__(self, day, stocks):
        self.day = day
        self.stocks = stocks

    def __len__(self):
        return len(self.stocks)


def _from_dhan(rec_by_sid, day):
    stocks = {}
    for sid, rec in rec_by_sid.items():
        s = rec["sessions"].get(day)
        if not s or len(s["bars"]) < 20:
            continue
        bars = s["bars"]
        t = [b[0] for b in bars]
        # A 1-minute bar stamped 09:20 covers 09:20:00-09:20:59, so its CLOSE is
        # only known at 09:21. Using it at 09:20 would hand the backtest one
        # minute of hindsight on every single tick -- the single easiest way to
        # fake a good result. Bars are therefore shifted forward by 60s.
        t = [x + 60 for x in t]
        cum, vols = 0.0, []
        for b in bars:
            cum += b[5]
            vols.append(cum)
        stocks[rec["sym"]] = {
            "sid": str(sid), "open": s["open"], "prev_close": s.get("prev_close") or 0.0,
            "t": t, "px": [b[4] for b in bars], "vol": vols,
            "hi": [b[2] for b in bars], "lo": [b[3] for b in bars], "uc": 0.0,
        }
    return Session(day, stocks)


def _from_board(day):
    raw = super_replay.build(day)
    stocks = {}
    for sym, d in raw.items():
        tr = d["trail"]
        stocks[sym] = {
            "sid": d["sid"], "open": d["open"],
            "prev_close": tr[0][1][3],
            "t": [x[0] for x in tr], "px": [x[1][0] for x in tr],
            "vol": [x[1][1] for x in tr],
            "hi": [x[1][0] for x in tr], "lo": [x[1][0] for x in tr],
            "uc": max((x[1][2] for x in tr), default=0.0),
        }
    return Session(day, stocks)


def load_sessions(log=print):
    """Real Dhan bars if they have been fetched, otherwise the board logs."""
    try:
        import super_fetch
        cache = super_fetch.load()
    except Exception as e:
        log(f"  (no Dhan bar cache: {type(e).__name__} {str(e)[:60]})")
        cache = {}
    if cache:
        days = sorted({d for r in cache.values() for d in r["sessions"]})
        log(f"  source: REAL Dhan 1-minute bars -- {len(cache)} stocks, {len(days)} sessions")
        return [_from_dhan(cache, d) for d in days], "dhan"
    log("  source: board logs (narrow universe -- see super_replay.py)")
    out = []
    for d in super_replay.sessions():
        s = _from_board(d)
        if len(s) >= 10:
            out.append(s)
    log(f"  {len(out)} sessions, {statistics.median([len(s) for s in out]):.0f} stocks median")
    return out, "board"


# ================================================== the alarm the scan expects
class ReplayAlarm:
    """superstocks.scan() only calls .snapshot() and .universe(), and reads
    q[0] ltp, q[1] day volume, q[2] open, q[5] upper circuit, q[7] prev close.

    Matching that surface exactly means the REAL scan runs -- including any bug
    in it. A re-implementation here could quietly disagree with the live tab
    and nobody would ever know."""

    def __init__(self, session):
        self.s = session.stocks
        self._uni, self._key = {}, {}
        for i, sym in enumerate(self.s):
            try:
                k = int(self.s[sym]["sid"])
            except (TypeError, ValueError):
                k = 900000 + i
            self._uni[k] = sym
            self._key[sym] = k
        self._snap = {}
        self._now = None

    def universe(self):
        return self._uni                      # INT keys, like the real one

    def snapshot(self):
        return None, self._snap

    def delta(self, sid, seconds=30):
        """(pct move, RUPEES TRADED, window) -- the same contract as the live
        alarm's delta(), which is what the liquidity gate calls.

        Only meaningful on real 1-minute bars. Without this the gate would fall
        back to reconstructing turnover from a board log's stale volume field,
        which is exactly the measurement that produced 43 zeroes and had to be
        thrown away.
        """
        d = self.s.get(self._uni.get(self._as_int(sid)))
        if not d or self._now is None:
            return None, None, None
        t = self._now
        i = bisect_right(d["t"], t) - 1
        if i < 1:
            return None, None, None
        j = i
        while j > 0 and d["t"][j] > t - seconds:
            j -= 1
        win = max(1, d["t"][i] - d["t"][j])
        vol = d["vol"][i] - d["vol"][j]
        px = d["px"][i]
        if d["px"][j] <= 0:
            return None, None, None
        return (px / d["px"][j] - 1) * 100, vol * px, win

    @staticmethod
    def _as_int(sid):
        try:
            return int(sid)
        except (TypeError, ValueError):
            return sid

    def at(self, t):
        self._now = t
        snap = {}
        for sym, d in self.s.items():
            i = bisect_right(d["t"], t) - 1
            if i < 0:
                continue
            if t - d["t"][i] > 180:          # gone quiet: not a live quote
                continue
            # STRING keys. The live Movers_alarm.snapshot() uses str(sid) while
            # universe() uses int(sid), and this replay originally used ints on
            # both sides -- so the backtest passed while the live tab found
            # nothing at all for two days. A fixture must mismatch wherever
            # production mismatches, or it is testing a different program.
            snap[str(self._key[sym])] = (d["px"][i], d["vol"][i], d["open"],
                                         d["hi"][i], 0.0, d["uc"], 0.0,
                                         d["prev_close"])
        self._snap = snap
        return snap


# ==================================================================== outcomes
def outcome(d, t):
    """(entry, best gain %, worst dip % before the best, seconds to the best).

    Highs and lows are used, not closes -- a scalper is filled at the price the
    stock actually traded at, not at the end of a minute.
    """
    i = bisect_right(d["t"], t) - 1
    if i < 0:
        return None
    entry = d["px"][i]
    if entry <= 0:
        return None
    best, best_t, mae, trough = 0.0, 0, 0.0, 0.0
    j = i + 1
    while j < len(d["t"]) and d["t"][j] <= t + HORIZON:
        up = (d["hi"][j] / entry - 1) * 100
        dn = (d["lo"][j] / entry - 1) * 100
        trough = min(trough, dn)
        if up > best:
            best, best_t, mae = up, d["t"][j] - t, trough
        j += 1
    if j == i + 1:
        return None                              # no forward data at all
    return entry, best, mae, best_t


def _wilson(k, n):
    if not n:
        return 0.0, 0.0
    p, z = k / n, 1.96
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - hw) / den * 100, (c + hw) / den * 100


# ==================================================================== the run
def run(sessions, params=None, log=print, collect=False, source="board"):
    """Replay every session. Returns (super_signals, gate_signals, baseline)."""
    # THE LIQUIDITY RULES CANNOT BE MEASURED ON THIS DATA, so they are switched
    # off here rather than left on to produce a number that means nothing.
    #
    # board_*.jsonl carries a day-volume field that does not refresh anywhere
    # near every 5 seconds. Reconstructing "rupees traded in the last minute"
    # from it gives EXACTLY ZERO for 43 of 130 past signals -- a recording
    # artefact, not a market. Left enabled, the gate rejected almost everything
    # and the backtest reported 1.6 signals a day, which would have been a
    # confident, precise, wrong answer.
    #
    # So: everything below measures the rules this data CAN judge. The
    # liquidity rules are measured FORWARD, live, by super_monitor.py.
    params = dict(params or {})
    if source == "dhan":
        # REAL 1-minute bars carry real volume, so rupees-a-minute IS
        # measurable here and both turnover rules run for real.
        #
        # `live_pct` -- the dead-air rule -- still cannot be judged: a 1-minute
        # bar either exists or does not, so it cannot show that a stock went
        # forty seconds without a print. It stays off and stays a live-only
        # rule.
        params.setdefault("MIN_LIVE_TICKS", 0.0)
    else:
        params.setdefault("MIN_RS_PER_MIN", 0)
        params.setdefault("MIN_SUSTAINED_RS_PER_MIN", 0)
        params.setdefault("MIN_LIVE_TICKS", 0.0)

    old = {}
    if params:
        for k, v in params.items():
            old[k] = getattr(superstocks, k)
            setattr(superstocks, k, v)
    rec_off = superstocks._record
    superstocks._record = lambda rows: None      # never touch the live log

    sup, gate, base = [], [], []
    try:
        for S in sessions:
            superstocks.reset_for_day()
            al = ReplayAlarm(S)
            seen_sup, seen_gate = set(), set()
            for t in range(WIN_START, WIN_END + 1, TICK):
                al.at(t)
                hms = f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
                rows = superstocks.scan(al, hms)
                for rank, r in enumerate(rows, 1):
                    sym = r["sym"]
                    if sym in seen_sup:
                        continue
                    seen_sup.add(sym)
                    o = outcome(S.stocks[sym], t)
                    if o:
                        sup.append({"day": S.day, "sym": sym, "t": t, "rank": rank,
                                    "urg": r["urgency"], "from_open": r["from_open"],
                                    "rel": r.get("rel_vol"), "cr": r["tover_cr"],
                                    "r90": r["rise_90s"], "since_high": r["since_high_s"],
                                    "entry": o[0], "best": o[1], "mae": o[2], "tpk": o[3]})
                # BASELINE 2 -- the plain gate, nothing else
                for sym, d in S.stocks.items():
                    i = bisect_right(d["t"], t) - 1
                    if i < 0 or t - d["t"][i] > 180:
                        continue
                    fo = (d["px"][i] / d["open"] - 1) * 100 if d["open"] else 0
                    if fo >= superstocks.MIN_FROM_OPEN and d["px"][i] >= superstocks.MIN_PRICE:
                        if sym not in seen_gate:
                            seen_gate.add(sym)
                            o = outcome(d, t)
                            if o:
                                gate.append({"day": S.day, "sym": sym, "t": t,
                                             "best": o[1], "mae": o[2], "tpk": o[3]})
                # BASELINE 1 -- any stock, any moment (sampled, for speed)
                if (t // TICK) % 6 == 0:
                    for sym, d in S.stocks.items():
                        o = outcome(d, t)
                        if o:
                            base.append({"best": o[1], "mae": o[2], "tpk": o[3]})
    finally:
        superstocks._record = rec_off
        for k, v in old.items():
            setattr(superstocks, k, v)
    return sup, gate, base


def _stats(rows):
    n = len(rows)
    if not n:
        return None
    w = sum(1 for r in rows if r["best"] >= TARGET)
    b = sum(1 for r in rows if r["best"] >= BREAKEVEN)
    lo, hi = _wilson(w, n)
    return {
        "n": n, "worked": w / n * 100, "lo": lo, "hi": hi,
        "cost": b / n * 100,
        "med_best": statistics.median(r["best"] for r in rows),
        "med_mae": statistics.median(r["mae"] for r in rows),
        "p10_mae": sorted(r["mae"] for r in rows)[max(0, int(0.10 * n) - 1)],
        "med_tpk": statistics.median(r["tpk"] for r in rows) / 60.0,
    }


def report(log=print):
    log("")
    log("=" * 74)
    log("  SUPER STOCKS -- backtest against recorded sessions")
    log("=" * 74)
    sessions, src = load_sessions(log)
    if not sessions:
        log("  no sessions to test")
        return
    sup, gate, base = run(sessions, log=log, source=src)
    S, G, B = _stats(sup), _stats(gate), _stats(base)
    if not S:
        log("  SUPER STOCKS produced no signals at all across these sessions.")
        log("  That is a finding, not an error: the gate is too tight.")
        return

    log("")
    log(f"  {'':<22}{'n':>7}{'per day':>9}{'worked':>9}{'honest':>13}"
        f"{'beat cost':>11}{'med gain':>10}{'med MAE':>9}{'to peak':>9}")
    log("  " + "-" * 89)
    for name, st in (("SUPER STOCKS", S), ("plain 2% gate", G), ("any moment", B)):
        if not st:
            continue
        band = f"{st['lo']:.0f}-{st['hi']:.0f}%"
        log(f"  {name:<22}{st['n']:>7}{st['n']/len(sessions):>9.1f}"
            f"{st['worked']:>8.0f}%{band:>13}"
            f"{st['cost']:>10.0f}%{st['med_best']:>9.2f}%{st['med_mae']:>8.2f}%"
            f"{st['med_tpk']:>8.1f}m")
    log("  " + "-" * 89)
    log(f"  EDGE over the plain 2% gate : {S['worked'] - G['worked']:+.1f} points")
    log(f"  EDGE over doing nothing     : {S['worked'] - B['worked']:+.1f} points")
    log("")

    # --- does the ranking mean anything? the tab's whole promise is the order
    log("  Does URGENCY rank actually order them? (rank at first appearance)")
    log(f"    {'rank':<10}{'n':>6}{'worked':>9}{'med gain':>11}")
    for lab, f in (("1 only", lambda r: r["rank"] == 1),
                   ("2-3", lambda r: 2 <= r["rank"] <= 3),
                   ("4-8", lambda r: 4 <= r["rank"] <= 8),
                   ("9+", lambda r: r["rank"] >= 9)):
        sel = [r for r in sup if f(r)]
        if sel:
            st = _stats(sel)
            log(f"    {lab:<10}{st['n']:>6}{st['worked']:>8.0f}%{st['med_best']:>10.2f}%")
    log("")
    log("  By clock -- his window is 09:15 to 10:30")
    log(f"    {'window':<14}{'n':>6}{'worked':>9}{'med gain':>11}{'to peak':>10}")
    for lab, a, b in (("09:15-09:30", WIN_START, WIN_START + 900),
                      ("09:30-09:45", WIN_START + 900, WIN_START + 1800),
                      ("09:45-10:00", WIN_START + 1800, WIN_START + 2700),
                      ("10:00-10:30", WIN_START + 2700, WIN_END + 1)):
        sel = [r for r in sup if a <= r["t"] < b]
        if sel:
            st = _stats(sel)
            log(f"    {lab:<14}{st['n']:>6}{st['worked']:>8.0f}%{st['med_best']:>10.2f}%"
                f"{st['med_tpk']:>9.1f}m")
    log("")
    log(f"  source: {src}")
    if src == "dhan":
        log(f"  liquidity rules ARE enforced above: {superstocks.MIN_SHARES_PER_MIN:,} "
            f"shares/min sustained, Rs {superstocks.MIN_SUSTAINED_RS_PER_MIN/1e5:.0f} lakh/min "
            f"sustained, Rs {superstocks.MIN_RS_PER_MIN/1e5:.0f} lakh in the last minute.")
        log("  NOT enforced: the dead-air rule. A 1-minute bar cannot show that a")
        log("  stock went forty seconds without a print. That one stays live-only.")
    else:
        log("  NOT measured here: the liquidity rules. This data's volume field does")
        log("  not refresh fast enough to judge them -- 43 of 130 past signals compute")
        log("  to exactly zero rupees a minute. Switched off; watched live instead.")
    return sup, gate, base, sessions


if __name__ == "__main__":
    report()
