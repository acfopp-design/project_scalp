"""paper_tiers.py -- the SAME engine at other capitals, side by side.

WHAT IT IS
    A second, detached process that re-runs paper_live.run_book() at each
    capital tier and writes logs/paper_tiers_<day>.json for the board's
    CAPITAL TIERS tab.

WHAT IT DOES NOT DO -- and cannot
    * It makes NO Dhan API calls. Not one. It reads the 30-second tape that
      paper_live.py has ALREADY fetched and only redoes the arithmetic. The
      fetch entry points are replaced with functions that raise, so a later
      edit cannot quietly reintroduce a call (see _BLOCK below).
    * It never writes logs/paper_live_<day>.json, logs/LEVERAGE_<day>.json,
      or paper_control.json. The Rs 1,00,000 tab owns those.
    * It runs in its own OS process, so nothing it does can touch the memory
      of the engine that is trading.

Every tier uses the identical logic, signals and exits as the Rs 1,00,000 tab.
The only things that change are capital, slots and leg size (multi_capital.py).
"""
import json, os, sys, time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import live_shadow as LS
import paper_live as PL
import funnel as FN
import leverage as LV
import eye_strategy as ES
import multi_capital as MC

OUT = HERE / "logs" / "paper_tiers_{}.json"          # light index for the sub-tabs
ONE = HERE / "logs" / "paper_tier_{}_{}.json"        # full snapshot per tier
LOGF = HERE / "logs" / "paper_tiers_{}.log"
REFRESH = 45


# ---- hard guard: this process must never reach Dhan -----------------------
def _BLOCK(name):
    def _boom(*a, **k):
        raise RuntimeError(
            "paper_tiers.py tried to call %s(). This process is READ-ONLY on "
            "market data -- it must never add load to the Dhan APIs that the "
            "live Rs 1,00,000 engine depends on." % name)
    return _boom


# NOTE leverage.load_sheet / sheet_lev are deliberately NOT blocked: they read
# Dhan's published MIS Google Sheet, not the Dhan trading/data API, so they add
# no load to what the live engine depends on. Without them every tier would
# size at 1x and understate by ~5x.
LS.fetch_live = _BLOCK("live_shadow.fetch_live")
LV.prefetch = _BLOCK("leverage.prefetch")
LV.prefetch_symbols = _BLOCK("leverage.prefetch_symbols")
LV._ask = _BLOCK("leverage._ask")
try:
    import Opus_quotes_v3 as _QU
    _QU._quote_all = _BLOCK("Opus_quotes_v3._quote_all")
    _QU.circuit_bands = _BLOCK("Opus_quotes_v3.circuit_bands")
except Exception:
    pass


def log(m, day):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), m)
    print(line, flush=True)
    try:
        with open(str(LOGF).format(day), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def build_tape(day):
    """Exactly how paper_live builds its universe -- from files already on disk."""
    warm = LS.load_warm(LS._prev_session_dir(day), want_day=None)
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
        if len(tb) < 3:
            continue
        tape[s], d0[s] = bb + tb, len(bb)
    # leverage from the MIS sheet only -- same source the board is using
    # (its own summary reports api=0, sheet_only=N). No Dhan API call.
    try:
        LV._load(day)
        LV.load_sheet(day, log=lambda m: None, force=False)
        for _s in tape:
            if _s not in LV._mem:
                LV._mem[_s] = LV.combine(None, LV.sheet_lev(_s))
    except Exception:
        pass
    avail, _, _ = FN.build(day, tape)
    pins = PL.nodip_watchlist(day)
    if pins:
        avail = {s: t for s, t in avail.items() if s in pins}
    fun = {s: tape[s] for s in avail if s in tape}
    return fun, {s: d0[s] for s in fun}, avail


def _started(day):
    """Honour the board's reset, exactly as the Rs 1,00,000 engine does.

    30-Sep: the tiers hardcoded "09:15:00" and so ignored paper_control.json
    entirely. When the main book was reset at 10:03 the tier tabs carried on
    replaying from the open -- still showing the contaminated pre-fix morning
    while the main tab showed a clean book. Two tabs, two different mornings.
    """
    try:
        c = json.loads((HERE / "logs" / "paper_control.json")
                       .read_text(encoding="utf-8"))
        if c.get("day") == day and c.get("started"):
            return c["started"]
    except (OSError, ValueError, KeyError):
        pass
    return "09:15:00"


def one_tier(day, fun, fd0, avail, capital, now_t):
    slots, leg, book = MC.plan(capital)
    keep = (PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK)
    PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK = capital, 5.0, slots, book
    ES._cache.clear()
    try:
        _st = _started(day)
        closed, live = PL.run_book(fun, fd0, avail, _st, now_t)
        snap = PL.snapshot(day, closed, live,
                           {"active": True, "started": _st}, len(fun), now_t)
        s = snap["summary"]
        row = {"capital": capital, "slots": slots, "leg": round(leg),
               "trades": s["trades"], "wins": s["wins"], "losses": s["losses"],
               "win_pct": s["win_pct"], "net": s["net"], "net_pct": s["net_pct"],
               "slip_cost": s.get("slip_cost", 0),
               "net_real": s.get("net_real", s["net"]),
               "net_real_pct": s.get("net_real_pct", s["net_pct"]),
               "charges": s["charges"], "open_n": s["open_n"]}
        return row, snap
    finally:
        PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK = keep
        ES._cache.clear()


# Sources whose change must retire this worker, mirroring paper_live.py.
# 30-Sep: paper_tiers had NO self-retire, so after funnel.py and paper_tiers.py
# were fixed the tier tabs carried on running the old code in memory -- still
# replaying from 09:15 with the look-ahead while the main tab showed a clean
# book. It needed a hand-run .bat to recover, which is one more thing to
# remember at 09:00. Retiring on a source change lets the board's own watchdog
# bring it back, the same way the engine recovers.
_WATCH = ("paper_tiers.py", "paper_live.py", "eye_strategy.py", "eye_cfg.py",
          "funnel.py", "multi_capital.py")


def _srcs_mtime():
    newest = 0.0
    for f in _WATCH:
        p = HERE / f
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return newest


def main():
    day = datetime.now().strftime("%Y%m%d")
    log("paper_tiers starting -- READ-ONLY, no Dhan calls. tiers: %s"
        % ", ".join("{:,}".format(c) for c in MC.CAPITAL_TIERS), day)
    born = _srcs_mtime()
    while True:
        now_t = datetime.now().strftime("%H:%M:%S")
        if now_t > "15:35:00":
            log("session over", day)
            break
        if _srcs_mtime() != born:
            log("source changed on disk -- exiting so the new code can take "
                "over (the board's watchdog restarts this worker)", day)
            break
        try:
            (HERE / "logs" / "paper_tiers.pid").write_text(str(os.getpid()),
                                                           encoding="utf-8")
        except OSError:
            pass
        try:
            fun, fd0, avail = build_tape(day)
            if not fun:
                # Pre-open, or no tradeable name has bars yet. Publish a status
                # anyway: writing nothing made the tab read "no snapshot yet"
                # for the whole hour before 09:15, which looks like a failure.
                msg = ("waiting for the 09:15 open" if now_t < "09:15:00"
                       else "no tradeable names with bars yet")
                tmp = Path(str(OUT).format(day) + ".tmp")
                tmp.write_text(json.dumps(
                    {"ok": True, "day": day, "as_of": now_t, "universe": 0,
                     "tiers": [], "err": msg}, default=str), encoding="utf-8")
                tmp.replace(Path(str(OUT).format(day)))
                log("%s | %s" % (now_t, msg), day)
                time.sleep(REFRESH)
                continue
            tiers = []
            for cap in MC.CAPITAL_TIERS:
                try:
                    row, snap = one_tier(day, fun, fd0, avail, cap, now_t)
                    tiers.append(row)
                    # full snapshot, same shape as /livepaper, so the board can
                    # render it with the SAME renderer as the Rs 1,00,000 tab
                    f1 = Path(str(ONE).format(cap, day))
                    t1 = Path(str(f1) + ".tmp")
                    t1.write_text(json.dumps(snap, default=str), encoding="utf-8")
                    t1.replace(f1)
                except Exception as e:
                    log("tier %s failed: %s %s" % (cap, type(e).__name__, e), day)
            payload = {"ok": True, "day": day, "as_of": now_t,
                       "universe": len(fun), "tiers": tiers,
                       "note": ("Same logic/signals/exits as the Rs 1,00,000 tab. "
                                "Only capital, slots and leg size differ. "
                                "net_real is after the slippage estimate.")}
            tmp = Path(str(OUT).format(day) + ".tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(Path(str(OUT).format(day)))
            log("%s | %d names | %s" % (now_t, len(fun),
                " ".join("%s:%s" % ("{:,}".format(t["capital"])[:-4] + "k"
                                    if t["capital"] < 100000 else
                                    "%.1fL" % (t["capital"] / 100000.0),
                                    "{:,.0f}".format(t["net_real"])) for t in tiers)), day)
        except Exception as e:
            log("cycle error: %s %s" % (type(e).__name__, e), day)
        time.sleep(REFRESH)


if __name__ == "__main__":
    main()
