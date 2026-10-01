"""Test the 7 capital tiers against past clean tape.

STANDALONE. Only reads bars/logs and calls paper_live.run_book() from a
NEW python process. Never edits paper_live.py, Movers_app.py, or any file the
live board has open -- the live Rs 1,00,000 tab is untouched, on disk and
in memory. Restores every module attribute it changes before exiting.

Days used: 22-Sep (full) and 25-Sep (full). 23-Sep and 24-Sep are excluded --
their tape stops mid-session (12:09 and 14:40), so scaling capital on a
truncated day would misrepresent every tier equally, not just the small ones.
"""
import json, sys
sys.path.insert(0, ".")
import leverage as LV, live_shadow as LS, paper_live as PL, funnel as FN
import paper_engine as PE, eye_strategy as ES
import multi_capital as MC

DAYS = ["20260922", "20260925"]


def day_tape(day):
    # 28-Sep BUG, FIXED: this used to json.dump the 25-Sep audit straight into
    # logs/LEVERAGE_<day>.json -- the SAME file the live board reads at startup.
    # It clobbered today's real 251-name cache with 51 stale entries one minute
    # before a restart. Never write that path from a test harness. Seed the
    # in-memory cache directly instead; nothing touches disk.
    # Mirror EXACTLY what the live board does: resolve every name from Dhan's
    # MIS sheet, no API calls (the board reports api=0, sheet_only=359), and
    # never write logs/LEVERAGE_<day>.json -- that file is the board's.
    LV._load(day)
    try:
        LV.load_sheet(day, log=lambda m: None, force=False)
    except TypeError:
        LV.load_sheet(day)
    warm = LS.load_warm(LS._prev_session_dir(day), want_day=None)
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
        if len(tb) < 3:
            continue
        tape[s], d0[s] = bb + tb, len(bb)
    for _s in tape:
        if _s not in LV._mem:
            LV._mem[_s] = LV.combine(None, LV.sheet_lev(_s))
    avail, _, _ = FN.build(day, tape)
    pins = PL.nodip_watchlist(day)
    if pins:
        avail = {s: t for s, t in avail.items() if s in pins}
    fun = {s: tape[s] for s in avail if s in tape}
    return fun, {s: d0[s] for s in fun}, avail, tape


def bar_volume(tape, sym, hhmm):
    for x in tape.get(sym, []):
        if x.get("hhmm") == hhmm:
            return x.get("v") or 0
    return 0


def cap_and_reprice(trades, tape, cap_frac):
    """Shrink qty so neither the entry nor exit bar is asked for more than
    cap_frac of its own traded volume. Recompute charges/net at the new qty.
    Returns (repriced_trades, n_capped, rupees_shaved)."""
    out, n_capped, shaved = [], 0, 0.0
    for t in trades:
        ve = bar_volume(tape, t["sym"], t["in_t"])
        vx = bar_volume(tape, t["sym"], t["out_t"])
        cap_e = int(ve * cap_frac) if ve else t["qty"]
        cap_x = int(vx * cap_frac) if vx else t["qty"]
        qty = max(0, min(t["qty"], cap_e, cap_x))
        if qty == 0:
            n_capped += 1
            shaved += t["net"]
            continue
        if qty != t["qty"]:
            n_capped += 1
        side = t["side"]
        ev, xv = qty * t["in"], qty * t["out"]
        bv, sv = (ev, xv) if side == 1 else (xv, ev)
        ch = PE.charges(bv, sv)["total"]
        net = sv - bv - ch
        shaved += t["net"] * (qty / t["qty"]) - net if t["qty"] else 0
        out.append({**t, "qty": qty, "chg": ch, "net": net})
    return out, n_capped, shaved


def run_one(day, fun, fd0, avail, tape, capital):
    slots, leg, book = MC.plan(capital)
    # NOTE: PL.BOOK is a module-level value computed ONCE at import time
    # (BOOK = CAPITAL * LEVERAGE) -- run_book's sizing reads PL.BOOK directly,
    # not CAPITAL*LEVERAGE recomputed live. Setting CAPITAL/LEVERAGE alone
    # silently leaves sizing at the Rs 1,00,000 default. Must set BOOK too.
    B_CAP, B_LEV, B_SLOTS, B_BOOK = PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK
    PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK = capital, 5.0, slots, book
    ES._cache.clear()
    try:
        closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
    finally:
        PL.CAPITAL, PL.LEVERAGE, PL.SLOTS, PL.BOOK = B_CAP, B_LEV, B_SLOTS, B_BOOK
        ES._cache.clear()
    allt = closed + live
    gross = sum(t.get("net", 0) or 0 for t in allt)
    capped, n_capped, shaved = cap_and_reprice(allt, tape, MC.PARTICIPATION_CAP)
    realistic = sum(t.get("net", 0) or 0 for t in capped)
    return {"day": day, "capital": capital, "slots": slots, "leg": leg,
            "trades": len(allt), "gross": gross, "realistic": realistic,
            "n_capped": n_capped, "shaved": gross - realistic}


def main():
    rows = []
    for day in DAYS:
        fun, fd0, avail, tape = day_tape(day)
        print("== %s: %d names in funnel ==" % (day, len(fun)), flush=True)
        for capital in MC.CAPITAL_TIERS:
            r = run_one(day, fun, fd0, avail, tape, capital)
            rows.append(r)
            print("  Rs %-10s slots=%d leg=Rs%-9s trades=%3d  gross=Rs%-9s realistic=Rs%-9s (capped %d trades, shaved Rs%s)"
                  % ("{:,.0f}".format(capital), r["slots"], "{:,.0f}".format(r["leg"]),
                     r["trades"], "{:,.0f}".format(r["gross"]), "{:,.0f}".format(r["realistic"]),
                     r["n_capped"], "{:,.0f}".format(r["shaved"])), flush=True)
    json.dump(rows, open("logs/MULTI_CAPITAL_BACKTEST.json", "w"), indent=1, default=str)
    print("\nwrote logs/MULTI_CAPITAL_BACKTEST.json", flush=True)

    print("\n%-12s" % "capital", end="")
    for day in DAYS:
        print("%16s" % ("realistic " + day[-2:] + "-Sep"), end="")
    print()
    for capital in MC.CAPITAL_TIERS:
        print("Rs %-9s" % "{:,.0f}".format(capital), end="")
        for day in DAYS:
            r = next(x for x in rows if x["day"] == day and x["capital"] == capital)
            print("%16s" % "{:,.0f}".format(r["realistic"]), end="")
        print()


if __name__ == "__main__":
    main()
