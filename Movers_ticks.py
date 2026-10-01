"""
Movers_ticks.py -- live 30-SECOND candle builder for the Movers Board.

WHY THIS EXISTS
    Dhan's /charts/intraday API only serves 1, 5, 15, 25 and 60-minute candles
    (confirmed in the official v2 docs). Sub-minute bars therefore cannot be
    fetched -- Dhan's own terminal builds them from its live tick feed.
    This module does the same thing: it polls the batched quote endpoint (ONE
    request covers up to 1000 instruments) and folds LTP + volume into 30s
    buckets.

HONEST LIMITS
    * O/H/L are sampled, not true tick extremes. With a POLL_SEC=5 cadence each
      30s bar is built from ~6 samples, so wicks are approximations. Close and
      volume are exact (volume = delta of the exchange's cumulative day volume).
    * No history before the app starts -- bars accumulate from launch. The board
      therefore uses 1-min candles until MIN_BARS_30S have formed (hybrid mode).

PUBLIC API
    TICKS.track(sids)        -> set the symbols to poll (called each cycle)
    TICKS.series(sid, n)     -> {"t","o","h","l","c","v"} last n 30s bars, or None
    TICKS.start(log)         -> start the background poller
    TICKS.status()           -> diagnostics for the UI/log
"""
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

import Opus_quotes_v3 as q

# Completed 30s bars are logged the moment they close. No API can serve
# sub-minute history, so if these aren't captured live they are gone forever.
HERE = os.path.dirname(os.path.abspath(__file__))
BARLOG_DIR = os.path.join(HERE, "logs", "movers_board")

BUCKET_SEC = 30          # bar size
POLL_SEC = 5             # quote poll cadence (1 batched request per poll)
MAX_BARS = 240           # keep 2 hours of 30s bars per symbol
EXTRA_MAX = 600          # scanner-requested symbols; board list + this must
                         # stay under the 1000-instrument quote limit
MIN_BARS_30S = 35        # bars needed before EMA-9/MA-12/MACD are meaningful


class TickAggregator:
    def __init__(self):
        self.lock = threading.Lock()
        self.bars = defaultdict(lambda: deque(maxlen=MAX_BARS))  # sid -> deque of bars
        self.cur = {}            # sid -> in-progress bar
        self.prev_vol = {}       # sid -> last cumulative day volume
        self.sids = []
        self.extra = []          # scanner-requested sids (see add())
        self.started = False
        self.polls = 0
        self.last_poll = None
        self.last_err = None
        self.sym_of = {}         # sid -> symbol (for readable bar logs)
        self._pending = []       # closed bars waiting to be flushed to disk
        self.logged = 0

    # ---------------- public ----------------
    def track(self, sids, sym_map=None):
        """The BOARD's list. Replaces, because the board owns its own universe."""
        with self.lock:
            self.sids = [str(s) for s in sids][:1000]
            if sym_map:
                self.sym_of.update({str(k): v for k, v in sym_map.items()})

    def add(self, sids, sym_map=None):
        """ADD stocks to build bars for, without disturbing the board's list.

        WHY THIS EXISTS
            track() replaces self.sids outright, so whichever caller ran last
            won. The board calls it every cycle with ~46 names, which silently
            wiped anything a scanner had asked for -- so Scanner1/Scanner2 were
            evaluating 267 candidates for which NO 30-second bars were ever
            built, and could never return a single result. They looked broken
            and were in fact starved.

            Kept in a separate bucket so the board's own tracking is untouched,
            and unioned at poll time. Capped so the union stays inside the
            1000-instrument limit of a single quote request.
        """
        with self.lock:
            have = set(self.extra)
            for s in sids:
                s = str(s)
                if s not in have:
                    have.add(s)
                    self.extra.append(s)
            # keep the most recent requests if we overflow
            if len(self.extra) > EXTRA_MAX:
                self.extra = self.extra[-EXTRA_MAX:]
            if sym_map:
                self.sym_of.update({str(k): v for k, v in sym_map.items()})

    def ready(self, min_bars):
        """How many tracked symbols have at least min_bars closed bars.
        Surfaced in the UI so 'still warming up' is visible, not mistaken for
        'nothing qualifies'."""
        with self.lock:
            n = sum(1 for dq in self.bars.values() if len(dq) >= min_bars)
            return {"ready": n, "tracked": len(set(self.sids) | set(self.extra)),
                    "polls": self.polls}

    def series(self, sid, n=40):
        sid = str(sid)
        with self.lock:
            dq = self.bars.get(sid)
            if not dq:
                return None
            bars = list(dq)[-n:]
        if not bars:
            return None
        return {
            "t": [b["t"] for b in bars],
            "o": [b["o"] for b in bars],
            "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars],
            "c": [b["c"] for b in bars],
            "v": [b["v"] for b in bars],
        }

    def last_price(self, sid):
        """Most recent traded price seen by the 5s quote poll (live, not a candle)."""
        with self.lock:
            b = self.cur.get(str(sid))
            return b["c"] if b else None

    def count(self, sid):
        with self.lock:
            dq = self.bars.get(str(sid))
            return len(dq) if dq else 0

    def status(self):
        with self.lock:
            return {"tracked": len(self.sids), "symbols_with_bars": len(self.bars),
                    "polls": self.polls, "last_poll": self.last_poll,
                    "bucket_sec": BUCKET_SEC, "min_bars": MIN_BARS_30S,
                    "bars_logged": self.logged, "err": self.last_err}

    def start(self, log=lambda m: None):
        if self.started:
            return
        self.started = True
        threading.Thread(target=self._loop, args=(log,), daemon=True).start()
        log(f"ticks: 30s builder started (poll {POLL_SEC}s, bucket {BUCKET_SEC}s)")

    # ---------------- internals ----------------
    def _loop(self, log):
        while True:
            t0 = time.time()
            try:
                self._poll(log)
            except Exception as e:
                self.last_err = str(e)[:120]
            time.sleep(max(1.0, POLL_SEC - (time.time() - t0)))

    def _poll(self, log):
        with self.lock:
            # UNION of the board's list and whatever the scanners asked for.
            # One quote request covers up to 1000 instruments, so both fit.
            seen, sids = set(), []
            for s in list(self.sids) + list(self.extra):
                if s not in seen:
                    seen.add(s)
                    sids.append(s)
            sids = sids[:1000]
        if not sids:
            return
        quotes = q._quote_all(sids, lambda m: None)
        if not quotes:
            return
        now = time.time()
        bucket = int(now // BUCKET_SEC) * BUCKET_SEC
        with self.lock:
            self.polls += 1
            self.last_poll = time.strftime("%H:%M:%S", time.localtime(now))
            for sid, qq in (quotes or {}).items():
                try:
                    lp = float((qq or {}).get("last_price") or 0)
                    cum = float((qq or {}).get("volume") or 0)
                except (TypeError, ValueError):
                    continue
                if lp <= 0:
                    continue
                sid = str(sid)
                prev = self.prev_vol.get(sid)
                dv = max(0.0, cum - prev) if prev is not None else 0.0
                self.prev_vol[sid] = cum
                b = self.cur.get(sid)
                if b is None or b["t"] != bucket:
                    if b is not None:
                        self.bars[sid].append(b)          # close the finished bar
                        # queue the CLOSED bar for the log (irreplaceable data)
                        self._pending.append({
                            "sid": sid, "sym": self.sym_of.get(sid, sid),
                            "t": b["t"],
                            "hhmm": datetime.fromtimestamp(b["t"]).strftime("%H:%M:%S"),
                            "o": round(b["o"], 2), "h": round(b["h"], 2),
                            "l": round(b["l"], 2), "c": round(b["c"], 2),
                            "v": int(b["v"]),
                        })
                    b = {"t": bucket, "o": lp, "h": lp, "l": lp, "c": lp, "v": 0.0}
                    self.cur[sid] = b
                b["h"] = max(b["h"], lp)
                b["l"] = min(b["l"], lp)
                b["c"] = lp
                b["v"] += dv
        self._flush_bars()

    def _flush_bars(self):
        """Append closed 30s bars to disk (outside the lock-critical path)."""
        with self.lock:
            pending, self._pending = self._pending, []
        if not pending:
            return
        try:
            os.makedirs(BARLOG_DIR, exist_ok=True)
            path = os.path.join(BARLOG_DIR,
                                f"bars30_{datetime.now().strftime('%Y%m%d')}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                for rec in pending:
                    f.write(json.dumps(rec) + "\n")
            self.logged += len(pending)
        except Exception as e:
            self.last_err = f"barlog: {str(e)[:80]}"


TICKS = TickAggregator()
