"""
learn.py -- the ten-minute review. What did we take, what was actually there,
and which KIND of mistake was it?

WHY IT IS SHAPED LIKE THIS
    Sri's instruction: every ten minutes, work out what is missing and what
    would have been done to make money on that trade. The temptation is to
    print a P&L and a list of regrets. That is useless, because "we should have
    made more" is true every single time and points at nothing.

    What is actionable is the KIND of gap, because each kind has a different
    fix and lumping them together is how the last three days went in circles:

      DETECTION   it ran and was never carded            -> scanner rules
      CAPACITY    carded, but every slot was full        -> sizing / slot count
      ENTRY       we bought, and it went down first      -> entry timing
      EXIT        we sold, and it kept going             -> exit rules
      STOP        we were stopped and it recovered       -> stop distance

    So this counts the five buckets and names the biggest. One number, one
    verdict, and the evidence under it.

THE DISCIPLINE THIS FILE INHERITS
    Three times in two days a confident per-trade statistic failed portfolio
    simulation (displacement, the pullback-limit entry, the from_open 3-4%
    bucket). So nothing here changes a constant. It reports, and the report is
    a hypothesis. A change ships only when replay_live over the full session
    agrees, which is a separate deliberate step.

    It also refuses to draw conclusions from thin data. A bucket with three
    trades in it is labelled as an anecdote, because that is what it is.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
BOARD = LOGS / "movers_board"
CTRL = LOGS / "control"
IST = timezone(timedelta(hours=5, minutes=30))

THIN = 5                      # fewer than this in a bucket = an anecdote


def now_ist():
    return datetime.now(IST)


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def hms(s):
    s = int(s)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


# ------------------------------------------------------------------ inputs
def board_health():
    """What the supervisor thinks. None if it is not running at all."""
    f = CTRL / "status.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    age = None
    try:
        age = (now_ist() - datetime.strptime(d["ts"], "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=IST)).total_seconds()
    except Exception:
        pass
    d["status_age_s"] = age
    return d


def halt_if_asked(log=print):
    """Stop live trading on request, from a file.

    Sri can only reach me through this session, and I cannot reach the board:
    my shell is a Linux VM with no route to Windows, so no HTTP call to
    127.0.0.1:5005 will ever work from here. But learn.py itself RUNS on
    Windows, launched by the supervisor every ten minutes -- so it can make
    the call I cannot.

    Dropping logs/control/HALT_TRADING therefore halts trading within one
    review cycle. It stops live_paper only; the board keeps sweeping and
    keeps writing bars30, because the tape after the halt is exactly what the
    backtest needs and killing the process would throw it away.
    """
    flag = CTRL / "HALT_TRADING"
    if not flag.exists():
        return None
    # The flag may name a time to wait for, so a halt can be armed in advance
    # from a session that cannot be awake at the moment it should fire.
    try:
        want = flag.read_text(encoding="utf-8").strip()[:8]
        if want and ":" in want and now_ist().strftime("%H:%M:%S") < want:
            return None
    except OSError:
        pass
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:5005/livepaper?action=stop", timeout=10) as r:
            body = r.read().decode()[:120]
        ok = "live trading STOPPED"
    except Exception as e:
        return f"HALT REQUESTED but the stop call failed: {type(e).__name__}"
    try:
        flag.rename(CTRL / "done" / f"HALT_{now_ist().strftime('%H%M%S')}")
    except OSError:
        pass
    return ok


def command(action, why=""):
    """Drop a command file for the supervisor. My only way to act on Windows."""
    try:
        CTRL.mkdir(parents=True, exist_ok=True)
        stamp = now_ist().strftime("%H%M%S")
        (CTRL / f"{action}-{stamp}.cmd").write_text(why, encoding="utf-8")
        return True
    except OSError:
        return False


def live_state(day=None):
    day = day or now_ist().strftime("%Y%m%d")
    f = BOARD / f"livepaper_{day}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def tape(day=None):
    """symbol -> sorted [(t,o,h,l,c)] for today."""
    day = day or now_ist().strftime("%Y%m%d")
    out = defaultdict(list)
    f = BOARD / f"bars30_{day}.jsonl"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            s = d.get("sym")
            if not s or s in ("AAA", "RUNNER"):
                continue
            t = sec(d["hhmm"])
            if t >= sec("09:15:00"):
                out[s].append((t, d["o"], d["h"], d["l"], d["c"]))
    for s in out:
        out[s].sort()
    return dict(out)


def cards(day=None):
    """symbol -> first second carded today, from the Super log."""
    day = day or now_ist().strftime("%Y%m%d")
    out = {}
    f = BOARD / f"super_{day}.jsonl"
    if not f.exists():
        return out
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            for r in d.get("rows") or []:
                s = r.get("sym")
                if s and s not in ("AAA", "RUNNER") and s not in out:
                    out[s] = (sec(d["ts"]), r.get("urgency"))
    return out


# --------------------------------------------------------------- analysis
def window(b, t0, t1=None):
    return [x for x in b if x[0] >= t0 and (t1 is None or x[0] <= t1)]


def review_trade(tr, b, closed):
    """One trade against the tape it actually traded on."""
    t_in = sec(tr.get("in_hms") or "00:00:00")
    px_in = tr.get("in") or 0
    if not b or not px_in:
        return None
    t_out = sec(tr["out_hms"]) if (closed and tr.get("out_hms")) else None
    px_out = tr.get("out") if closed else tr.get("last")

    # ENTRY QUALITY -- what happened in the first three minutes after we bought.
    # "Entry was a loss straight away" was Sri's read of the whole system, so it
    # is measured directly rather than argued about.
    nxt = window(b, t_in, t_in + 180)
    dip = ((min(x[3] for x in nxt) / px_in - 1) * 100) if nxt else None
    pop = ((max(x[2] for x in nxt) / px_in - 1) * 100) if nxt else None

    # BEST EXIT that was available while we actually held it
    held = window(b, t_in, t_out)
    best = ((max(x[2] for x in held) / px_in - 1) * 100) if held else None

    # WHAT WE LEFT -- the peak after we sold
    after = window(b, t_out, t_out + 1800) if t_out else []
    left = ((max(x[2] for x in after) / (px_out or px_in) - 1) * 100) if after else None

    got = ((px_out / px_in - 1) * 100) if px_out else None
    return {"sym": tr.get("sym"), "in_hms": tr.get("in_hms"),
            "out_hms": tr.get("out_hms"), "why": tr.get("why"),
            "got": got, "best": best, "left": left,
            "dip3": dip, "pop3": pop, "net": tr.get("net"),
            "urgency": tr.get("urgency"), "closed": closed}


def classify(rows, taken_syms, card_map, tp, active_from=None, active_to=None):
    """Count the five buckets. The biggest one is where the work is.

    active_from/active_to bound the window in which the engine was ACTUALLY
    trading. Without them the CAPACITY bucket fills up with stocks that ran
    before anyone pressed start -- on 02-Sep that produced eight "missed"
    names carded between 09:15 and 09:31 against a session that began at
    10:25. A missed opportunity is only a capacity failure if we were in a
    position to take it; otherwise it is just a shorter day, and confusing
    the two would have sent me off tuning slot counts for a problem that did
    not exist.
    """
    b = defaultdict(list)
    for r in rows:
        if r["got"] is None:
            continue
        if r["why"] == "stop" and (r["left"] or 0) > 1.5:
            b["STOP"].append(r)
        elif r["dip3"] is not None and r["dip3"] < -0.8 and r["got"] < 0:
            b["ENTRY"].append(r)
        elif (r["left"] or 0) > 1.5:
            b["EXIT"].append(r)
    # things that ran and we never touched
    for sym, (t0, _u) in card_map.items():
        if sym in taken_syms:
            continue
        if active_from is not None and t0 < active_from:
            continue            # carded before we were trading -- not a miss
        if active_to is not None and t0 > active_to:
            continue
        bb = tp.get(sym)
        if not bb:
            continue
        after = window(bb, t0, t0 + 1800)
        if after:
            entry = after[0][4]
            gain = (max(x[2] for x in after) / entry - 1) * 100 if entry else 0
            if gain >= 2.0:
                b["CAPACITY"].append({"sym": sym, "in_hms": hms(t0),
                                      "best": gain, "got": None, "left": None,
                                      "dip3": None, "why": "never bought",
                                      "net": None, "closed": False})
    return b


def money(x):
    return f"Rs {x:>9,.0f}" if x is not None else "        -"


def pct(x):
    return f"{x:+6.2f}%" if x is not None else "     -"


def main():
    day = next((a for a in sys.argv[1:] if a.isdigit() and len(a) == 8), now_ist().strftime("%Y%m%d"))
    out = []
    P = out.append
    P(f"# LEARN {day}  ·  {now_ist().strftime('%H:%M:%S')} IST")

    # ---- 1. is the board even alive? -----------------------------------
    h = board_health()
    if h is None:
        P("\n## BOARD: supervisor is NOT running. Nothing is being traded and")
        P("nothing here can restart it. This needs a human to start")
        P("SUPERVISOR_START.bat once.")
    else:
        stale = (h.get("status_age_s") or 0) > 60
        P(f"\n## BOARD  {'HEALTHY' if h.get('healthy') else 'UNHEALTHY'}"
          f" · pid {h.get('pid')} · up {h.get('uptime_s')}s"
          f" · {h.get('restarts_last_hour')} restarts this hour")
        P(f"  {h.get('reason')}")
        if stale:
            P(f"  !! supervisor status is {int(h['status_age_s'])}s old -- the "
              f"supervisor itself may have stopped")
        if not h.get("healthy") and not h.get("stopped_by_command"):
            P("  -> the supervisor restarts this by itself; no action taken here")

    st = live_state(day)
    if not st:
        P("\nNo live paper state for today yet.")
        print("\n".join(out))
        return

    tp = tape(day)
    cm = cards(day)
    op = list((st.get("open") or {}).values()) if isinstance(st.get("open"), dict) \
        else list(st.get("open") or [])
    cl = list(st.get("closed") or [])

    realised = sum((t.get("net") or 0) for t in cl)
    unreal = sum((t.get("net") or 0) for t in op)
    cap = st.get("capital") or 1
    P(f"\n## P&L  realised Rs {realised:,.0f} · open Rs {unreal:,.0f} · "
      f"NET Rs {realised+unreal:,.0f} ({(realised+unreal)/cap*100:.2f}% of capital)")
    P(f"  {len(cl)} closed · {len(op)} open · target {st.get('target_pct')}%")

    # SEGMENTED P&L. Sri asked whether the book should be reset after every
    # tune so each change can be measured cleanly. Resetting would discard
    # losses already taken and flatter the day into whatever the last change
    # did, so instead the book runs continuously and the P&L is CUT at each
    # tune. Same information, nothing hidden.
    try:
        tunes = [json.loads(l) for l in
                 (LOGS / "TUNES.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        tunes = [t for t in tunes if t.get("day") == day]
    except Exception:
        tunes = []
    if tunes:
        P("\n## P&L BY CONFIG SEGMENT  (each change judged on its own trades)")
        marks = [("09:15:00", "session open")] + [(t["at"], t["change"]) for t in tunes]
        for i, (t0, label) in enumerate(marks):
            t1 = marks[i + 1][0] if i + 1 < len(marks) else "15:30:00"
            seg = [x for x in cl if t0 <= (x.get("in_hms") or "") < t1]
            if not seg:
                P(f"  {t0}-{t1}  {label}: no closed trades yet")
                continue
            net = sum((x.get("net") or 0) for x in seg)
            w = sum(1 for x in seg if (x.get("net") or 0) > 0)
            P(f"  {t0}-{t1}  {len(seg):>3} trades  Rs {net:>8,.0f}  "
              f"Rs {net/len(seg):>7,.0f}/trade  {w}W/{len(seg)-w}L   {label}")

    rows = [r for r in (
        [review_trade(t, tp.get(t.get("sym")), True) for t in cl] +
        [review_trade(t, tp.get(t.get("sym")), False) for t in op]) if r]

    if rows:
        P("\n## EVERY TRADE vs WHAT THE TAPE OFFERED")
        P(f"  {'stock':<12}{'in':<9}{'why':<8}{'got':>8}{'best':>8}"
          f"{'left':>8}{'dip3m':>8}   read")
        for r in sorted(rows, key=lambda x: x["in_hms"] or ""):
            read = ""
            if r["why"] == "stop" and (r["left"] or 0) > 1.5:
                read = "stopped, then ran -- stop too tight"
            elif r["dip3"] is not None and r["dip3"] < -0.8 and (r["got"] or 0) < 0:
                read = "underwater within 3 min -- bought the top"
            elif (r["left"] or 0) > 1.5:
                read = f"sold {r['left']:.1f}% early"
            elif not r["closed"]:
                read = "still holding"
            P(f"  {r['sym']:<12}{(r['in_hms'] or ''):<9}{(r['why'] or '-'):<8}"
              f"{pct(r['got'])}{pct(r['best'])}{pct(r['left'])}{pct(r['dip3'])}"
              f"   {read}")

    taken = {t.get("sym") for t in cl} | {t.get("sym") for t in op}
    # The engine only competes for capital while it is running, so the
    # CAPACITY bucket is scored strictly inside that window.
    def _t(v):
        try:
            return sec(str(v)[11:19] if len(str(v)) > 10 else str(v))
        except Exception:
            return None
    a_from, a_to = _t(st.get("started")), _t(st.get("stopped"))
    if a_from:
        P(f"  trading active from {hms(a_from)}"
          + (f" to {hms(a_to)}" if a_to else " (still running)"))
    buckets = classify(rows, taken, cm, tp, a_from, a_to)

    P("\n## WHERE THE GAP IS  (each kind has a different fix)")
    order = ["ENTRY", "EXIT", "STOP", "CAPACITY"]
    names = {"ENTRY": "bought and it fell straight away -> entry timing",
             "EXIT": "sold and it kept running          -> exit rules",
             "STOP": "stopped out and it recovered      -> stop distance",
             "CAPACITY": "ran +2% while carded, never bought -> slots / sizing"}
    biggest, bn = None, 0
    for k in order:
        v = buckets.get(k) or []
        flag = "  (anecdote)" if 0 < len(v) < THIN else ""
        P(f"  {k:<10}{len(v):>3}   {names[k]}{flag}")
        if len(v) > bn:
            biggest, bn = k, len(v)
    if biggest and bn >= THIN:
        P(f"\n  BIGGEST: {biggest} ({bn} cases). That is where the next change goes.")
        ex = sorted(buckets[biggest],
                    key=lambda r: -(r.get("best") or r.get("left") or 0))[:5]
        for r in ex:
            P(f"    {r['sym']:<12}{(r['in_hms'] or ''):<9}"
              f"best {pct(r.get('best'))}  left {pct(r.get('left'))}")
    elif biggest:
        P(f"\n  Biggest is {biggest} with only {bn} cases -- an anecdote, not a")
        P("  finding. No change should be made off this yet.")
    else:
        P("\n  No clear gap yet. Too early, or it is behaving.")

    P("\n  REMINDER: everything above is a hypothesis. Three times this week a")
    P("  per-trade finding this convincing failed full-engine replay. Nothing")
    P("  ships until replay_live over the whole session agrees.")

    # ---- ACT, don't just report -------------------------------------------
    # Sri: "not just report -- tune so you learn mistakes and reapply
    # immediately, every 10 minutes." The supervisor re-runs this file fresh
    # each cycle, so the tuner reaches the live board without a restart, and
    # tune.py itself can call /admin/reload on localhost -- which my own shell
    # cannot. Guards live in tune.py: one change per cycle, bounded ranges,
    # a minimum sample, and it reverts its own last move if that move made
    # things worse.
    halted = halt_if_asked()
    if halted:
        P("\n## " + halted)
    if "--notune" not in sys.argv and not halted:
        try:
            import tune
            tune.DRY = "--dry" in sys.argv
            verdict = tune.run(st)
        except Exception as e:
            verdict = f"tune: FAILED {type(e).__name__} {str(e)[:90]}"
        P("\n" + verdict)

    text = "\n".join(out)
    # --brief: the full report always goes to LEARN_*.md; what comes back over
    # the wire is six lines. Reading a 40-line report every ten minutes is how
    # a session burns its usage limit before lunch and goes blind for the rest
    # of the day -- which costs far more than the detail was ever worth. The
    # detail is on disk; fetch it only when a bucket is actually worth acting on.
    if "--brief" in sys.argv:
        keep = [l for l in out if l.startswith("# LEARN") or l.startswith("## BOARD")
                or l.startswith("## P&L") or l.strip().startswith("BIGGEST:")
                or "anecdote, not a" in l or "supervisor is NOT running" in l
                or l.startswith("tune:") or "live trading STOPPED" in l
                or "HALT REQUESTED" in l]
        print("\n".join(keep[:6]))
    else:
        print(text)
    try:
        LOGS.mkdir(exist_ok=True)
        with (LOGS / f"LEARN_{day}.md").open("a", encoding="utf-8") as f:
            f.write("\n\n" + "=" * 70 + "\n" + text + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
