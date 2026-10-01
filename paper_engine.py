"""
paper_engine.py -- what the board's own signals would have paid, on today's tape.

NOT A SIMULATION. Every entry and exit price is a price the tick builder actually
recorded today in logs/movers_board/bars30_YYYYMMDD.jsonl. Nothing is
interpolated, nothing is invented, and no trade is entered at a time its signal
had not yet fired.

WHAT COUNTS AS A SIGNAL
    1. A stock's FIRST APPEARANCE on the Super Stocks tab (super_YYYYMMDD.jsonl)
    2. A stock the PRE-OPEN tab called STRONG GAP-UP before 09:11
       (preopen_YYYYMMDD.jsonl), entered only when Super Stocks confirms it

    (2) is here because of 01-Sep. The pre-open tab named TBZ and KALYANIFRG at
    09:07:56 -- one minute before Sri reads the board -- and they finished the
    day's #1 and #2 runners (+16.19% and +12.03% from the open). Nine names were
    called; only three were later confirmed by Super Stocks, and those three
    were the top three. The pre-open call alone is a bad trade (6 of 9 went
    nowhere); the pre-open call CONFIRMED by Super Stocks was the best filter
    the board produced that session.

DISPLACEMENT -- the fix for the trade that got away
    The first version of this engine was first-come-first-served: when every
    slot was busy, a new signal was simply dropped. TBZ signalled at 09:16:32
    and was thrown away because three slots were held by JINDWORLD (urgency
    15.8), MANALIPETC (16.9) and KALYANIFRG (20.1), taken 47-80 seconds earlier.
    TBZ's urgency was 17.8 -- higher than two of the three holding the capital,
    and it ran +13.3% from its signal.

    Now a materially stronger signal can CLOSE the weakest open position and
    take its slot. "Materially" is DISPLACE_MARGIN: without a margin the book
    churns on noise, paying two sets of charges for a coin flip.

INTRABAR AMBIGUITY RESOLVES AGAINST THE TRADE
    A 30-second bar whose low breaks the stop AND whose high reaches the target
    is booked as a STOP. There is no way to know the order inside the bar, and
    assuming the good one is how backtests lie.
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs" / "movers_board"

# EXPLICIT IST. datetime.now() is the host's local clock, and this module is read
# from more than one machine -- on a UTC host every entry landed before 09:15 and
# the engine reported "no trades" on a session that had 48 signals. The market
# runs on IST, so the code says IST.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)

# ---- the strategy, fixed before any result is looked at -------------------
# 02-Sep. Measured BEFORE the grid was run: across 81 first-cards on 28-Aug,
# 01-Sep and 02-Sep the average best excursion after a card is +1.95% and the
# average worst is -1.24%. A +3% target was therefore reaching past where these
# moves actually go -- most winners timed out instead of banking. Setting the
# target at the average excursion is mechanism, not curve-fitting.
#
# The grid then confirmed it, and +2.0%/-1.0% was the ONLY cell positive on all
# three sessions (18,776 / 18,624 / 2,814 = Rs 40,213 vs Rs 28,251 at +3%/-1%).
TARGET_PCT = 2.0           # take profit
STOP_PCT = -1.0            # cut
TIME_CAP = 1800            # 30 minutes in a position is not a scalp any more
MIN_PRICE = 20.0           # below this the spread eats the arithmetic
FILL_WINDOW = 90           # no recorded price within 90s of the signal = no fill
DISPLACE_MARGIN = 3.0      # urgency points a new signal must beat the weakest
                           # open position by before it is allowed to take the slot
PREOPEN_BOOST = 4.0        # urgency added to a pre-open STRONG GAP-UP confirmation
SESSION_START = "09:15:00"
SESSION_HARD_END = "15:30:00"
SLOT_CHOICES = (1, 2, 3, 4, 5, 6)

# What Super Stocks screens for when nothing has told it otherwise. These are
# the defaults the Paper Trading tab opens with, so the live tab and the
# back-test agree on position size instead of quietly using different numbers.
DEFAULT_CAPITAL = 1_00_000.0
DEFAULT_LEVERAGE = 5.0
DEFAULT_SLOTS = 3

# RE-ENTRY (fix 4). KALYANIFRG was traded once at 09:16, exited at target
# 09:25:30, then ran +7.22% again between 09:47 and 09:57 -- and the engine
# could not see it, because a stock was only ever considered at its FIRST card.
# A fresh card after a cooldown is a fresh signal. The cooldown exists so an
# exit and an immediate re-card do not become a round trip in charges for the
# same move.
# MEASURED, 01-Sep, before these numbers were chosen. Six re-entry settings run
# against the same tape, ranked on profit excluding each run's best trade:
#
#   09:15-10:30 (his window)          trades   NET       ex-best
#     no re-entry at all                   4   22,896      8,142
#     5-min cooldown, 3 legs              20   19,906     15,017
#     15-min cooldown, 3 legs             19   22,688     17,799
#     stronger-only + 15-min + 2 legs      8   24,368     17,008   <-- shipped
#
#   full session 09:15-14:30
#     no re-entry at all                   8   29,407     14,648
#     5-min cooldown, 3 legs              55    3,668      1,248   <-- churn
#     stronger-only + 15-min + 2 legs     19   18,643     13,753   <-- shipped
#
# Unrestricted re-entry is a money loser: 55 trades, Rs 4,209 in charges, and it
# keeps buying names that already made their move and are now grinding sideways
# into the 30-minute cap. Requiring the second signal to be AT LEAST AS STRONG
# as the first is what separates "it is going again" from "it is still on the
# tab". Chosen after seeing these numbers, on one session -- treat as provisional.
REENTRY_COOLDOWN = 900     # 15 minutes out of the book before the same stock
MAX_ENTRIES_PER_SYM = 2    # two bites at one name, never three
REENTRY_NEEDS_STRONGER = True


def sec(x):
    p = [int(v) for v in str(x).split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def hhmm(s):
    return f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"


# ---------------------------------------------------------------- charges
def charges(buy_val, sell_val):
    """Dhan equity INTRADAY, both legs. Every component, not a round number."""
    turn = buy_val + sell_val
    bro = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
    stt = 0.00025 * sell_val              # sell side only
    # 23-Sep: reconciled against Dhan's own brokerage calculator
    # (dhan.co/calculators/brokerage-calculator). Two corrections:
    #   exchange was 0.00297% -- NSE's current rate is 0.00307% (Rs 307/crore)
    #   GST is charged on brokerage + exchange ONLY, not on the SEBI fee
    # With both, 100 x Rs 1,248 both legs gives Rs 91.44, matching Dhan to the
    # paisa. Dhan rounds each component for DISPLAY but totals the unrounded
    # values, which is why its own lines appear not to add up.
    exch = 0.0000307 * turn               # NSE transaction charge
    sebi = 0.000001 * turn
    stamp = 0.00003 * buy_val             # buy side only
    gst = 0.18 * (bro + exch)
    return {"brokerage": round(bro, 2), "stt": round(stt, 2), "exchange": round(exch, 2),
            "sebi": round(sebi, 2), "stamp": round(stamp, 2), "gst": round(gst, 2),
            "total": round(bro + stt + exch + sebi + stamp + gst, 2)}


# ---------------------------------------------------------------- data
_cache = {"day": None, "t": 0.0, "bars": None, "signals": None, "pre": None}


def load(day=None, force=False, log=lambda m: None):
    """Bars + signals for one session. Cached 30s -- the tab can be hammered."""
    day = day or now_ist().strftime("%Y%m%d")
    if (not force and _cache["day"] == day and _cache["bars"] is not None
            and (time.time() - _cache["t"]) < 30):
        return _cache["bars"], _cache["signals"], _cache["pre"]

    bars = defaultdict(list)
    fb = LOGS / f"bars30_{day}.jsonl"
    if fb.exists():
        with fb.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("sym") == "AAA":
                    continue          # super_check.py's test symbol
                t = sec(d["hhmm"])
                if t < sec(SESSION_START) or t > sec(SESSION_HARD_END):
                    continue
                bars[d["sym"]].append((t, d["o"], d["h"], d["l"], d["c"], d.get("v") or 0))
    for s in bars:
        bars[s].sort()

    # PRE-OPEN: STRONG GAP-UP calls made before 09:11, while he is still reading
    pre = {}
    fp = LOGS / f"preopen_{day}.jsonl"
    if fp.exists():
        with fp.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if str(d.get("ts"))[11:19] > "09:11:00":
                    continue
                for r in d.get("rows") or []:
                    if r.get("side") == "UP" and r.get("verdict") == "STRONG GAP-UP":
                        pre.setdefault(r["sym"], str(d.get("ts"))[11:19])

    signals = {}
    seen_at = {}
    fs = LOGS / f"super_{day}.jsonl"
    if fs.exists():
        with fs.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = sec(d["ts"])
                for r in d.get("rows") or []:
                    sym = r.get("sym")
                    if not sym or sym == "AAA":
                        continue
                    # EVERY appearance is kept, not just the first -- but cards
                    # refresh every ~13 seconds, so a stock sitting on the tab
                    # produces hundreds of rows for ONE signal. A row is only a
                    # new signal if the card had GONE for a while first.
                    prevs = seen_at.get(sym)
                    if prevs is not None and t - prevs < REENTRY_COOLDOWN:
                        seen_at[sym] = t
                        continue
                    seen_at[sym] = t
                    urg = float(r.get("urgency") or 0)
                    signals.setdefault(sym, []).append({
                        "t": t, "urgency": urg,
                        # A pre-open STRONG GAP-UP that Super Stocks then confirms
                        # is not the same signal as a cold one. It is ranked above
                        # a cold signal of equal urgency, which is the whole point.
                        "rank": urg + (PREOPEN_BOOST if sym in pre else 0.0),
                        "preopen": pre.get(sym),
                        "from_open": r.get("from_open"), "day_pct": r.get("day_pct"),
                        "tover_cr": r.get("tover_cr"), "sym": sym,
                    })
    _cache.update({"day": day, "t": time.time(), "bars": dict(bars),
                   "signals": signals, "pre": pre})
    # RE-ENTRY QUALITY. A later card is only a new signal if it is at least as
    # convincing as the one that first put the stock on screen.
    if REENTRY_NEEDS_STRONGER:
        for _s, _v in signals.items():
            if len(_v) > 1:
                signals[_s] = [_v[0]] + [x for x in _v[1:] if x["rank"] >= _v[0]["rank"]]
    n_sig = sum(len(v) for v in signals.values())
    n_re = sum(1 for v in signals.values() if len(v) > 1)
    log(f"paper: {len(bars)} symbols with bars, {n_sig} signals across "
        f"{len(signals)} stocks ({n_re} re-signalled), {len(pre)} pre-open gap-ups "
        f"({sum(1 for v in signals.values() if v[0]['preopen'])} confirmed)")
    return dict(bars), signals, pre


# ---------------------------------------------------------------- engine
def _exit_scan(b, e_t, e_px, tgt, stp, end_t, timecap):
    """Walk the tape from the bar after entry. Returns (t, px, why)."""
    for (t, o, h, l, c, v) in b:
        if t <= e_t:
            continue
        if l <= stp:
            return t, stp, "stop"          # stop first -- see module docstring
        if h >= tgt:
            return t, tgt, "target"
        if t - e_t >= timecap:
            return t, c, "time"
        if t >= end_t:
            return t, c, "session end"
    return (b[-1][0], b[-1][4], "last print") if b else (e_t, e_px, "no exit data")


def _price_at(b, t):
    """Last recorded close at or before t -- what a market exit would get."""
    last = None
    for row in b:
        if row[0] > t:
            break
        last = row
    return last[4] if last else None


def simulate(capital, leverage, slots, bars, signals, end_t,
             target=TARGET_PCT, stop=STOP_PCT, timecap=TIME_CAP):
    """One full run. Returns the trade list and the counters that explain it."""
    per_slot = capital * leverage / slots
    flat = []
    for sym, lst in signals.items():
        for k, sg in enumerate(lst):
            flat.append((sym, sg, k))
    order = sorted(flat, key=lambda x: (x[1]["t"], -x[1]["rank"]))
    open_pos = {}                 # slot -> dict
    trades = []
    skipped = {"no_price": 0, "too_cheap": 0, "crowded_out": 0, "no_bars": 0,
               "cooldown": 0}
    displaced = 0
    taken = {}                    # sym -> [exit times] for the re-entry rule

    def close(slot, t, px, why):
        p = open_pos.pop(slot)
        bv, sv = p["qty"] * p["in"], p["qty"] * px
        ch = charges(bv, sv)
        taken.setdefault(p["sym"], []).append(t)
        trades.append({**p, "out_t": t, "out": round(px, 2), "why": why,
                       "gross": round(sv - bv, 2), "charges": ch,
                       "chg": ch["total"], "net": round(sv - bv - ch["total"], 2),
                       "held": int(t - p["in_t"]), "value": round(bv, 0)})

    for sym, s, leg in order:
        t_sig = s["t"]
        # Release anything whose exit already happened, BEFORE asking whether we
        # are still holding this stock -- otherwise a leg that closed at 09:19
        # still looks open at 09:40 and blocks its own re-entry. (The second,
        # stricter settle against the ENTRY time still runs below; it is what
        # stops a resting stop being overridden by a displacement.)
        for slot_ in list(open_pos):
            q = open_pos[slot_]
            xt, xpx, why = _exit_scan(bars[q["sym"]], q["in_t"], q["in"],
                                      q["tgt"], q["stp"], end_t, timecap)
            if xt <= t_sig:
                close(slot_, xt, xpx, why)
        # RE-ENTRY GUARD: same stock, but only after it has been out of the book
        # for REENTRY_COOLDOWN, and never more than MAX_ENTRIES_PER_SYM times.
        prior = taken.get(sym) or []
        if prior:
            if len(prior) >= MAX_ENTRIES_PER_SYM:
                skipped["cooldown"] += 1
                continue
            if t_sig - max(prior) < REENTRY_COOLDOWN:
                skipped["cooldown"] += 1
                continue
        if any(q["sym"] == sym for q in open_pos.values()):
            skipped["cooldown"] += 1          # already holding it
            continue
        b = bars.get(sym)
        if not b:
            skipped["no_bars"] += 1
            continue
        entry = next((x for x in b if x[0] > t_sig), None)
        if entry is None or entry[0] - t_sig > FILL_WINDOW:
            # bars30 only carries a symbol while the builder is tracking it, so a
            # signal can be followed by a nine-minute hole. Taking the bar on the
            # far side of that hole is not a fill, it is hindsight.
            skipped["no_price"] += 1
            continue
        if entry[4] < MIN_PRICE:
            skipped["too_cheap"] += 1
            continue
        e_t, e_px = entry[0], entry[4]
        if e_t > end_t:
            continue

        # SETTLE FIRST, against the ENTRY time -- not the signal time. WEL's stop
        # fired at 09:16:00 and KALYANIFRG's entry was also 09:16:00; settling
        # against the 09:15:45 signal left WEL open, so displacement then sold it
        # at that bar's CLOSE (83.63) for -3.5% when a resting stop-loss would
        # have taken 85.79 for -1%. A stop that has already been hit is not
        # available to be displaced.
        for slot_ in list(open_pos):
            q = open_pos[slot_]
            xt, xpx, why = _exit_scan(bars[q["sym"]], q["in_t"], q["in"],
                                      q["tgt"], q["stp"], end_t, timecap)
            if xt <= e_t:
                close(slot_, xt, xpx, why)

        slot = next((i for i in range(slots) if i not in open_pos), None)
        if slot is None:
            # DISPLACEMENT. Every slot is busy -- is this signal materially
            # better than the weakest thing holding the capital?
            weakest = min(open_pos, key=lambda k: open_pos[k]["rank"])
            if s["rank"] > open_pos[weakest]["rank"] + DISPLACE_MARGIN:
                px = _price_at(bars[open_pos[weakest]["sym"]], e_t)
                if px:
                    close(weakest, e_t, px, "displaced")
                    displaced += 1
                    slot = weakest
            if slot is None:
                skipped["crowded_out"] += 1
                continue

        qty = int(per_slot // e_px)
        if qty <= 0:
            skipped["too_cheap"] += 1
            continue
        open_pos[slot] = {
            "sym": sym, "slot": slot + 1, "sig_t": t_sig, "in_t": e_t,
            "in": round(e_px, 2), "qty": qty, "rank": s["rank"], "leg": leg + 1,
            "urgency": s["urgency"], "preopen": s["preopen"],
            "tgt": e_px * (1 + target / 100), "stp": e_px * (1 + stop / 100),
        }

    for slot in list(open_pos):
        p = open_pos[slot]
        xt, xpx, why = _exit_scan(bars[p["sym"]], p["in_t"], p["in"],
                                  p["tgt"], p["stp"], end_t, timecap)
        close(slot, xt, xpx, why)

    trades.sort(key=lambda x: x["in_t"])
    return trades, skipped, displaced


def run(capital=1_00_000.0, leverage=5.0, target_pct=10.0, day=None,
        now_hms=None, log=lambda m: None):
    """The whole thing: sweep the slot count, keep the best, report honestly."""
    capital = max(1000.0, float(capital or 0))
    leverage = max(1.0, min(10.0, float(leverage or 1)))
    target_pct = float(target_pct or 0)
    day = day or now_ist().strftime("%Y%m%d")
    now_hms = now_hms or now_ist().strftime("%H:%M:%S")
    end_t = min(sec(now_hms), sec(SESSION_HARD_END))

    bars, signals, pre = load(day, log=log)
    if not bars or not signals:
        return {"ok": False, "err": ("no signals recorded yet today -- Super Stocks "
                                     "arms at 09:15 and this reads its live log"),
                "trades": [], "summary": {}, "day": day}

    runs = []
    for slots in SLOT_CHOICES:
        tr, sk, dp = simulate(capital, leverage, slots, bars, signals, end_t)
        if not tr:
            continue
        net = round(sum(t["net"] for t in tr), 2)
        nets = sorted((t["net"] for t in tr), reverse=True)
        runs.append({"slots": slots, "trades": tr, "net": net, "skipped": sk,
                     "displaced": dp, "n": len(tr),
                     "wins": sum(1 for t in tr if t["net"] > 0),
                     "pct": round(net / capital * 100, 2),
                     # FRAGILITY. Sri asked for maximum profit, so the engine
                     # picks the biggest number -- but a result that is one lucky
                     # trade must SAY SO rather than being quietly presented as
                     # a strategy. This is that number, on screen, every run.
                     "ex_best": round(sum(nets[1:]), 2) if len(nets) > 1 else 0.0,
                     "best_one": round(nets[0], 2) if nets else 0.0})
    if not runs:
        return {"ok": False, "err": "signals fired but none was fillable on the recorded tape",
                "trades": [], "summary": {}, "day": day}

    # SLOT OBJECTIVE (fix 5). Ranking on raw net collapsed to ONE position at a
    # time -- on 01-Sep that paid Rs 30,615, but Rs 14,759 of it was a single
    # trade, it staked the whole margin on one name, and it structurally could
    # not buy a second good stock (VTL, TBZ and GRAPHITE were all carded, all
    # fillable, and all lost the slot race).
    #
    # Ranking on net-EXCLUDING-THE-BEST-TRADE asks a different question: what
    # did this split earn that did not depend on getting lucky once? A run that
    # is genuinely one lucky trade cannot win it, and a run that spreads across
    # several working trades can. Raw net is still computed and still shown, so
    # the two can be compared rather than one being hidden.
    for r in runs:
        r["score"] = r["ex_best"]
    runs.sort(key=lambda r: (-r["score"], -r["net"]))
    b = runs[0]
    by_net = max(runs, key=lambda r: r["net"])
    tr = b["trades"]
    gross = round(sum(t["gross"] for t in tr), 2)
    chg = round(sum(t["chg"] for t in tr), 2)
    target_rs = round(capital * target_pct / 100, 2)
    wins = [t for t in tr if t["net"] > 0]
    losses = [t for t in tr if t["net"] <= 0]
    dep = round(capital * leverage / b["slots"], 0)

    summary = {
        "capital": capital, "leverage": leverage, "slots": b["slots"],
        "per_slot": dep, "exposure": round(capital * leverage, 0),
        "window": f"{SESSION_START} - {hhmm(end_t)}",
        "trades": b["n"], "wins": len(wins), "losses": len(losses),
        "win_pct": round(len(wins) * 100.0 / b["n"]) if b["n"] else 0,
        "gross": gross, "charges": chg, "net": b["net"], "net_pct": b["pct"],
        "target_pct": target_pct, "target_rs": target_rs,
        "target_hit": bool(b["net"] >= target_rs),
        "target_gap": round(b["net"] - target_rs, 2),
        "best": ({"sym": max(tr, key=lambda t: t["net"])["sym"],
                  "net": b["best_one"]} if tr else None),
        "worst": ({"sym": min(tr, key=lambda t: t["net"])["sym"],
                   "net": round(min(t["net"] for t in tr), 2)} if tr else None),
        "ex_best": b["ex_best"],
        "ex_best_pct": round(b["ex_best"] / capital * 100, 2),
        "displaced": b["displaced"], "skipped": b["skipped"],
        "signals": sum(len(v) for v in signals.values()),
        "signal_stocks": len(signals),
        "resignalled": sum(1 for v in signals.values() if len(v) > 1),
        "preopen_calls": len(pre),
        "preopen_confirmed": sum(1 for v in signals.values() if v[0]["preopen"]),
        "rule": (f"entry = next recorded bar after the signal (within {FILL_WINDOW}s) · "
                 f"target +{TARGET_PCT}% · stop {STOP_PCT}% · {TIME_CAP//60}-min cap · "
                 f"pre-open STRONG GAP-UP confirmations ranked +{PREOPEN_BOOST} urgency · "
                 f"a signal {DISPLACE_MARGIN}+ points stronger displaces the weakest position"),
        "objective": ("most profit that does NOT depend on one lucky trade "
                      "(net excluding each run's single best trade)"),
        "max_net_slots": by_net["slots"], "max_net": by_net["net"],
        "max_net_ex_best": by_net["ex_best"],
        "cooldown_skips": b["skipped"].get("cooldown", 0),
        "alternatives": sorted([{"slots": r["slots"], "n": r["n"], "net": r["net"],
                          "pct": r["pct"], "ex_best": r["ex_best"]} for r in runs],
                          key=lambda r: r["slots"]),
    }
    for t in tr:
        t["in_hms"], t["out_hms"], t["sig_hms"] = hhmm(t["in_t"]), hhmm(t["out_t"]), hhmm(t["sig_t"])
    return {"ok": True, "day": day, "trades": tr, "summary": summary, "err": None}


if __name__ == "__main__":
    import sys
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 1_00_000
    lev = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    tgt = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    r = run(cap, lev, tgt, log=print)
    if not r["ok"]:
        print("ERR:", r["err"]); raise SystemExit(1)
    s = r["summary"]
    print(f"\n{'Stock':<12}{'Signal':<10}{'Entry':<10}{'Buy':>9} {'Exit':<10}{'Sell':>9}"
          f"{'Qty':>6}{'Value':>10}{'Gross':>9}{'Chg':>7}{'Net':>9}  Why")
    for t in r["trades"]:
        print(f"{t['sym']:<12}{t['sig_hms']:<10}{t['in_hms']:<10}{t['in']:>9.2f} "
              f"{t['out_hms']:<10}{t['out']:>9.2f}{t['qty']:>6}{t['value']:>10,.0f}"
              f"{t['gross']:>9,.0f}{t['chg']:>7,.0f}{t['net']:>9,.0f}  {t['why']}"
              + ("  [pre-open]" if t["preopen"] else ""))
    print(f"\nslots {s['slots']} · Rs {s['per_slot']:,.0f}/slot · {s['window']}")
    print(f"gross {s['gross']:,.0f}  charges {s['charges']:,.0f}  NET {s['net']:,.0f} "
          f"= {s['net_pct']}%   target {s['target_pct']}% (Rs {s['target_rs']:,.0f}) "
          f"-> {'HIT' if s['target_hit'] else 'MISSED'} by {abs(s['target_gap']):,.0f}")
    print(f"ex-best-trade {s['ex_best']:,.0f} ({s['ex_best_pct']}%)  displaced {s['displaced']}  "
          f"skipped {s['skipped']}")
    print("alternatives:", [(a["slots"], a["net"]) for a in s["alternatives"]])
