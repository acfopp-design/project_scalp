"""
card_lab.py -- test a CARDING rule offline. VERDICT: IT DOES NOT WORK. Read on.

    !!  02-Sep 19:30: THIS INSTRUMENT FAILED ITS OWN CALIBRATION AND MUST NOT
    !!  BE USED TO JUSTIFY A RULE CHANGE. It is kept, with its failure, because
    !!  the failure is the useful result: it says exactly what would have to
    !!  change before an offline carding test is possible at all.
    !!
    !!  Two independent reasons, either one fatal:
    !!
    !!  1. THE TAPE IS DOWNSTREAM OF THE DECISION UNDER TEST.
    !!     bars30 is written by Movers_ticks, whose symbol list comes from
    !!     TICKS.track() (the board's universe) and TICKS.add() (cards already
    !!     shown). So it records what the board ALREADY noticed. A looser
    !!     carding rule exists precisely to find stocks the board did not
    !!     notice -- and those can never be on this tape. Measured: only 59% of
    !!     the stocks the scanner really carded are even present (42-88% by
    !!     day). It looked like a superset because it holds 329-419 symbols
    !!     against 42-85 cards. It is not a superset. It is a different set.
    !!
    !!  2. THE GATE REBUILD IS NOT THE SCANNER.
    !!     superstocks_lab.py gates on roughly twenty things -- participation
    !!     liquidity, the rise percentile, sustained shares/min, stall and fade,
    !!     dwell, circuit proximity. This file rebuilds five. Result: 25% recall
    !!     against the real super log and 516 invented cards over 21 sessions.
    !!     Numbers off a rebuild that loose measure the rebuild, not the rule.
    !!
    !!  Symptom worth remembering: run anyway, it claims burst memory would have
    !!  carded some stocks up to 21,146 seconds earlier. Nearly six hours, i.e.
    !!  before the market opened. That is not an edge, that is a broken
    !!  instrument flattering itself -- and it is what a plausible-looking
    !!  offline result is worth without a calibration gate in front of it.
    !!
    !!  WHAT WOULD MAKE THIS WORK, in order of cost:
    !!    (a) Log bars for the scanner's eligible universe, not just the board's
    !!        members, so the tape becomes a genuine superset. This is a change
    !!        to Movers_ticks -- the file that produces IRREPLACEABLE data --
    !!        so it gets made deliberately, with Sri awake, not at 19:30
    !!        unattended on a hunch.
    !!    (b) Import the real gate stack from superstocks_lab instead of
    !!        restating it here, so calibration measures the rule and not my
    !!        transcription of it.
    !!  Until (a) and (b), burst memory and the from-low path can only be
    !!  validated by a live session. That is the honest answer, and it is worth
    !!  more than a fabricated number that agrees with what I hoped.

card_lab.py -- test a CARDING rule offline, without waiting for a live session.

THE PROBLEM THIS SOLVES
    replay_live.py replays super_YYYYMMDD.jsonl, which is a record of the cards
    the SHIPPED superstocks.py already decided to show. That makes it useless for
    testing a change to the carding rules themselves: if the from-low path or
    burst memory had been on, DIFFERENT stocks would have been carded, and those
    cards are not in the file. Every "should we loosen the gate?" question so far
    has therefore had exactly one instrument -- a live session -- which is why
    burst memory and the from-low path have been sitting unproven in
    superstocks_lab.py for two days.

    bars30_YYYYMMDD.jsonl is a different animal. It is the raw 30-second tape for
    every symbol the board SWEPT, not just the ones it carded: 329-419 symbols a
    day against 42-85 cards. That is a wide enough tape to re-run the gates on and
    ask what a looser rule would have carded.

WHAT IT DOES
    Walks the tape in chronological order, rebuilds the per-symbol state the
    scanner keeps (open, running high, running low, 90-second rise, seconds since
    the last new high, rupees traded per minute), applies a gate PROFILE, and
    emits a snapshot stream shaped exactly like super_*.jsonl -- which replay_live
    can then trade.

THE CALIBRATION, WHICH IS THE WHOLE POINT
    A reconstruction is worthless until it is shown to reproduce the thing it
    reconstructs. So `calibrate` runs the SHIPPED profile over the tape and scores
    the synthetic cards against the real super log for that day: what fraction of
    real cards it finds, how many it invents, and how far off its timing is.

    If that agreement is poor, the instrument is broken and any number it produces
    about the LAB profile is noise. In that case this file must report the
    disagreement and stop -- NOT quietly go on to print a lab-vs-shipped P&L that
    looks like evidence. Two findings this week (the pullback-limit entry and the
    from_open 3-4% bucket) died in exactly that way: a real-looking statistic from
    an instrument nobody had checked.

KNOWN APPROXIMATIONS -- read before believing any output
    - day_pct needs the previous close, which bars30 does not carry. from_open is
      used in its place, so the 19% circuit guard is looser here than live.
    - rel_vol is not reconstructible; it is passed as None. It contributes
      0.4 * rel to urgency, so synthetic urgency is biased LOW for high-rel-vol
      stocks. In the real log rel_vol is frequently null anyway.
    - The tape is 30-second bars. The live scanner sees ticks, so a burst that
      starts and ends inside one bar is invisible here.
    - Only swept symbols are on the tape. A stock the board never swept cannot be
      carded here however good the rule -- so this measures rule quality, not
      universe coverage. Those are separate problems and must not be conflated.
"""
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"

OPEN_S, CLOSE_S = 9 * 3600 + 15 * 60, 15 * 3600 + 30 * 60


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def hms(s):
    s = int(s)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


# ---------------------------------------------------------------- profiles
# Mirrors the constants in superstocks_lab.py. Kept as data, not imported, so a
# profile can be varied here without touching the module Sri trades from.
SHIPPED = {
    "name": "shipped",
    "min_from_open": 2.0,
    "min_price": 20.0,
    "min_rs_min": 25_00_000,
    "min_rise90": 1.00,
    "max_day_pct": 19.0,
    "from_low_path": False,
    "min_from_low": None,
    "min_from_low_abs": None,
    "burst_memory": False,
    "burst_sec": 0.0,
}
LAB = dict(SHIPPED, name="lab", from_low_path=True, min_from_low=3.0,
           min_from_low_abs=-6.0, burst_memory=True, burst_sec=150.0)


def load_tape(day):
    """symbol -> sorted [(t, o, h, l, c, v)], session bars only."""
    tape = defaultdict(list)
    sid_of = {}
    p = LOGS / f"bars30_{day}.jsonl"
    if not p.exists():
        return {}, {}
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            s = d.get("sym")
            if not s or s in ("AAA", "RUNNER"):
                continue
            t = sec(d["hhmm"])
            if not (OPEN_S <= t <= CLOSE_S):
                continue
            tape[s].append((t, d["o"], d["h"], d["l"], d["c"], d.get("v") or 0))
            if d.get("sid"):
                sid_of[s] = str(d["sid"])
    for s in tape:
        tape[s].sort()
    return dict(tape), sid_of


def real_cards(day):
    """symbol -> first second it was really carded, from the shipped super log."""
    out = {}
    p = LOGS / f"super_{day}.jsonl"
    if not p.exists():
        return out
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = sec(d["ts"])
            if not (OPEN_S <= t <= CLOSE_S):
                continue
            for r in d.get("rows") or []:
                s = r.get("sym")
                if s and s not in ("AAA", "RUNNER") and s not in out:
                    out[s] = t
    return out


def scan(day, profile):
    """Re-run the gates over the tape. Returns (snapshots, first_card).

    snapshots is [(t, [row, ...])] shaped like super_*.jsonl so replay_live can
    consume it unchanged.
    """
    tape, sid_of = load_tape(day)
    if not tape:
        return [], {}

    # index every symbol's bars by time so the walk is one pass over the clock
    at = defaultdict(list)
    for s, bars in tape.items():
        for b in bars:
            at[b[0]].append((s, b))
    times = sorted(at)

    st = {}                       # per-symbol running state
    burst_at = {}                 # sym -> last second a burst qualified
    first_seen = {}
    first_card = {}
    snaps = []

    for t in times:
        for s, (_t, o, h, l, c, v) in at[t]:
            d = st.get(s)
            if d is None:
                d = st[s] = {"open": o, "hi": h, "lo": l, "hi_t": t,
                             "closes": deque(maxlen=4), "vv": deque(maxlen=2),
                             "tover": 0.0}
            if h > d["hi"]:
                d["hi"], d["hi_t"] = h, t
            d["lo"] = min(d["lo"], l)
            d["closes"].append((t, c))
            d["vv"].append(v * c)
            d["tover"] += v * c

        rows = []
        for s, d in st.items():
            bars = tape[s]
            cur = None
            for b in bars:                       # last bar at or before t
                if b[0] <= t:
                    cur = b
                else:
                    break
            if cur is None or cur[0] != t:
                continue                          # no fresh print this second
            c = cur[4]
            if c < profile["min_price"] or d["open"] <= 0 or d["lo"] <= 0:
                continue

            from_open = (c / d["open"] - 1) * 100
            from_low = (c / d["lo"] - 1) * 100
            since_high = t - d["hi_t"]
            old = d["closes"][0][1] if len(d["closes"]) >= 4 else None
            rise90 = ((c / old - 1) * 100) if old and old > 0 else 0.0
            rs_min = sum(d["vv"]) * (60.0 / 60.0)   # two 30s bars = one minute

            # --- the gates -------------------------------------------------
            if from_open > profile["max_day_pct"]:
                continue
            if rs_min < profile["min_rs_min"]:
                continue

            burst = rise90 >= profile["min_rise90"]
            if burst:
                burst_at[s] = t
            elif profile["burst_memory"]:
                # A burst is an EVENT, not a state. The shipped rule demands the
                # burst be happening on the same scan pass that every other gate
                # passes -- the conjunction problem. Memory lets a stock that
                # burst BURST_SEC ago still qualify if it has held its gain.
                was = burst_at.get(s)
                burst = was is not None and (t - was) <= profile["burst_sec"]

            gate_open = from_open >= profile["min_from_open"]
            gate_low = (profile["from_low_path"]
                        and from_low >= profile["min_from_low"]
                        and from_open >= profile["min_from_low_abs"])
            if not (burst and (gate_open or gate_low)):
                continue

            first = first_seen.setdefault(s, t)
            age_min = (t - first) / 60.0
            urgency = (rise90 * 3.0
                       + max(0.0, 4.0 - since_high / 60.0) * 2.0
                       + min(from_open, 8.0) * 0.5
                       - min(age_min, 30.0) * 0.15)
            first_card.setdefault(s, t)
            rows.append({
                "sym": s, "sid": sid_of.get(s, s), "price": round(c, 2),
                "from_open": round(from_open, 2), "from_low": round(from_low, 2),
                "day_pct": round(from_open, 2), "rise_90s": round(rise90, 2),
                "rel_vol": None, "tover_cr": round(d["tover"] / 1e7, 2),
                "urgency": round(urgency, 1), "first_seen": hms(first),
                "rs_min": int(rs_min), "rs_sust": int(rs_min),
                "sh_min": int(rs_min / max(c, 1)), "since_high_s": since_high,
                "off_peak": round((d["hi"] - c) / d["hi"] * 100, 2) if d["hi"] else 0.0,
                "src": "universe", "state": "active", "bar": 1.0,
                "live_pct": None, "held_s": 0,
                "watch_since": None, "watch_px": None, "watch_why": None,
            })
        if rows:
            rows.sort(key=lambda r: -r["urgency"])
            snaps.append((t, rows[:8]))
    return snaps, first_card


# ------------------------------------------------------------- calibration
def calibrate(days, out=print):
    """Does the reconstruction reproduce the shipped scanner? Verdict first."""
    out("\nCALIBRATION -- synthetic SHIPPED gates vs the real super log")
    out("  The instrument is only usable if these agree. Recall = share of real")
    out("  cards it finds. Invented = synthetic cards that were never really shown.")
    out(f"  {'day':<10}{'real':>6}{'synth':>7}{'found':>7}{'recall':>8}"
        f"{'invented':>10}{'med lag':>9}")
    tot_real = tot_found = tot_synth = 0
    lags = []
    for day in days:
        real = real_cards(day)
        snaps, synth = scan(day, SHIPPED)
        if not real and not synth:
            continue
        hit = [s for s in real if s in synth]
        dl = sorted(synth[s] - real[s] for s in hit)
        med = dl[len(dl) // 2] if dl else 0
        lags += dl
        tot_real += len(real)
        tot_found += len(hit)
        tot_synth += len(synth)
        rec = 100 * len(hit) / max(1, len(real))
        out(f"  {day:<10}{len(real):>6}{len(synth):>7}{len(hit):>7}{rec:>7.0f}%"
            f"{len(synth)-len(hit):>10}{med:>8}s")
    rec = 100 * tot_found / max(1, tot_real)
    out(f"  {'POOLED':<10}{tot_real:>6}{tot_synth:>7}{tot_found:>7}{rec:>7.0f}%"
        f"{tot_synth-tot_found:>10}"
        f"{(sorted(lags)[len(lags)//2] if lags else 0):>8}s")
    ok = rec >= 60.0
    out("")
    if ok:
        out(f"  VERDICT: usable. It finds {rec:.0f}% of the real cards, so a")
        out("  difference between profiles is a difference in the RULE, not noise.")
    else:
        out(f"  VERDICT: NOT USABLE. Recall {rec:.0f}% is too low -- the")
        out("  reconstruction is not the shipped scanner, so any lab-vs-shipped")
        out("  number from it would be measuring my rebuild, not the rule change.")
        out("  Do not ship anything off this. Fix the reconstruction first.")
    return ok, rec


def compare(days, out=print):
    """What the LAB profile would card that the shipped one would not."""
    out("\nWHAT THE LAB GATES ADD  (from-low path + burst memory)")
    out(f"  {'day':<10}{'shipped':>9}{'lab':>6}{'extra':>7}   the extra names")
    extra_all = defaultdict(list)
    for day in days:
        _s, a = scan(day, SHIPPED)
        _s, b = scan(day, LAB)
        extra = sorted(set(b) - set(a), key=lambda s: b[s])
        for s in extra:
            extra_all[day].append((s, b[s]))
        out(f"  {day:<10}{len(a):>9}{len(b):>6}{len(extra):>7}   "
            + ", ".join(extra[:6]) + (" ..." if len(extra) > 6 else ""))
    return extra_all


def forward(days, out=print):
    """What did the EXTRA names actually do next? Rupees, not hit rate."""
    out("\nWHAT THE EXTRA NAMES DID NEXT  (+2% target / -1% stop / 30-min cap,")
    out("  Rs 83,333 a unit, Dhan intraday charges both legs)")
    POS = 83_333.0

    def chg(bv, sv):
        turn = bv + sv
        bro = min(20.0, 0.0003 * bv) + min(20.0, 0.0003 * sv)
        gst = 0.18 * (bro + 0.0000297 * turn + 0.000001 * turn)
        return (bro + 0.00025 * sv + 0.0000297 * turn + 0.000001 * turn
                + 0.00003 * bv + gst)

    def run(day, cards, tape):
        res = []
        for s, t0 in cards.items():
            b = tape.get(s)
            if not b:
                continue
            e = next((x for x in b if x[0] > t0), None)
            if not e or e[0] - t0 > 90 or e[4] < 20:
                continue
            px = e[4]
            qty = int(POS // px)
            if qty <= 0:
                continue
            tgt, stp = px * 1.02, px * 0.99
            why, out_px = "eod", b[-1][4]
            for (t, o, h, l, c, v) in b:
                if t <= e[0]:
                    continue
                if l <= stp:
                    out_px, why = stp, "stop"
                    break
                if h >= tgt:
                    out_px, why = tgt, "target"
                    break
                if t - e[0] >= 1800:
                    out_px, why = c, "time"
                    break
            bv, sv = qty * px, qty * out_px
            res.append({"sym": s, "net": sv - bv - chg(bv, sv), "why": why})
        return res

    out(f"  {'day':<10}{'n':>4}{'net':>11}{'per trade':>11}{'win%':>7}"
        f"{'tgt%':>7}{'stop%':>7}")
    pooled = []
    for day in days:
        tape, _ = load_tape(day)
        _s, a = scan(day, SHIPPED)
        _s, b = scan(day, LAB)
        extra = {s: t for s, t in b.items() if s not in a}
        r = run(day, extra, tape)
        pooled += r
        if not r:
            out(f"  {day:<10}{0:>4}{'-':>11}{'-':>11}")
            continue
        n = len(r)
        net = sum(x["net"] for x in r)
        w = sum(1 for x in r if x["net"] > 0) * 100 // n
        tg = sum(1 for x in r if x["why"] == "target") * 100 // n
        st = sum(1 for x in r if x["why"] == "stop") * 100 // n
        out(f"  {day:<10}{n:>4}{net:>11,.0f}{net/n:>11,.0f}{w:>6}%{tg:>6}%{st:>6}%")
    if pooled:
        n = len(pooled)
        net = sum(x["net"] for x in pooled)
        w = sum(1 for x in pooled if x["net"] > 0) * 100 // n
        tg = sum(1 for x in pooled if x["why"] == "target") * 100 // n
        st = sum(1 for x in pooled if x["why"] == "stop") * 100 // n
        out(f"  {'POOLED':<10}{n:>4}{net:>11,.0f}{net/n:>11,.0f}{w:>6}%{tg:>6}%{st:>6}%")
        out("")
        out("  This is a PER-TRADE number on signals in isolation. It is a")
        out("  hypothesis, not a result: it ignores that these names compete for")
        out("  the same capital as the shipped ones. Nothing ships until the full")
        out("  engine confirms it -- that rule has already saved this project")
        out("  twice this week.")
    return pooled


def sessions():
    return sorted(p.name[7:15] for p in LOGS.glob("bars30_2*.jsonl"))


if __name__ == "__main__":
    days = sys.argv[1:] or sessions()
    print(f"SESSIONS: {', '.join(days)}")
    ok, rec = calibrate(days)
    if not ok:
        raise SystemExit(2)
    compare(days)
    forward(days)
