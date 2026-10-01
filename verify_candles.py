"""
verify_candles.py -- prove the board fills, before claiming it does.

Re-runs the three Step 0 tests that failed (candles, scorer, build_card) against
the new chart source, then runs the board's REAL cycle() and counts what would
appear on screen. Nothing is asserted here that is not measured.

Read-only: no board is started, port 5005 is not touched, no file is written
except the report.
Output: console + logs\\VERIFY_CANDLES_REPORT.txt
"""
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "logs" / "VERIFY_CANDLES_REPORT.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
_buf = []
RES = []


def p(s=""):
    print(s, flush=True); _buf.append(str(s))


def head(t):
    p(""); p("=" * 74); p(t); p("=" * 74)


def verdict(step, ok, detail=""):
    RES.append((step, "PASS" if ok else "FAIL", detail))
    p(f"  --> {'PASS' if ok else 'FAIL'}  {detail}")


p(f"VERIFY CANDLES   {datetime.now():%d-%b-%Y %H:%M:%S}")

# ------------------------------------------------------------------ 1
head("1. CREDENTIAL THE CHART FEED IS USING")
try:
    import Movers_chartfeed as cf
    jwt, cid, bid, src = cf.creds(force=True)
    p(f"  token source : {cf.status().get('source')}")
    p(f"  token present: {'yes, ' + str(len(jwt)) + ' chars' if jwt else 'NO'}")
    p(f"  Cid/Bid/Src  : {cid} / {bid} / {src}")
    verdict("1 credentials", bool(jwt), cf.status().get("source") or "")
except Exception as e:
    p(traceback.format_exc()); verdict("1 credentials", False, str(e)[:120])

# ------------------------------------------------------------------ 2
head("2. NAME LIST (unchanged, but the rest depends on it)")
names = []
try:
    import Opus2_movers_source as ms
    names, err = ms.cash_names(None, lambda m: None)
    per = {}
    for n in names:
        for l in (n.get("lists") or []):
            per[l] = per.get(l, 0) + 1
    p(f"  {len(names)} unique names  {per}")
    verdict("2 names", bool(names), f"{len(names)} names")
except Exception as e:
    p(traceback.format_exc()); verdict("2 names", False, str(e)[:120])

# ------------------------------------------------------------------ 3
head("3. CANDLES  Opus_candle_v3.fetch  (Step 0 test 4 -- was 0 of 6)")
good = None
try:
    import Opus_candle_v3 as cv3
    test = names[:8] or [{"sym": "HDFCBANK", "sid": "1333"}]
    ok = 0
    for n in test:
        b, e = cv3.fetch(str(n["sid"]))
        if e or not b:
            p(f"    {str(n['sym']):14s} FAIL  {e}")
            continue
        ok += 1
        good = good or (b, str(n["sid"]), n["sym"])
        c = b["candles"]
        p(f"    {str(n['sym']):14s} OK  bars={len(c['close']):5d} today={b.get('today_bars'):4d} "
          f"session={b.get('session')} prev_close={b.get('prev_close')}")
    verdict("3 candles", ok > 0, f"{ok} of {len(test)} symbols")
except Exception as e:
    p(traceback.format_exc()); verdict("3 candles", False, str(e)[:120])

# ------------------------------------------------------------------ 4
head("4. SCORER  CashScorer.compute_row  (Step 0 test 8 -- was blocked)")
try:
    if not good:
        verdict("4 scorer", False, "no candles to score")
    else:
        from Opus_badge_cash import CashScorer
        b, sid, sym = good
        row = CashScorer().compute_row(sym, sid, b)
        ch = (row or {}).get("chart") or {}
        p(f"  {sym}: price={row.get('price')} day%={row.get('day_pct')} "
          f"vwap={row.get('vwap')} score={row.get('score')}")
        p("  chart arrays: " + ", ".join(f"{k}={len(ch.get(k) or [])}" for k in
          ("c", "ema9", "sma12", "macdLine", "macdSignal", "macdHist")))
        verdict("4 scorer", bool(ch.get("c")), f"{len(ch.get('c') or [])} chart bars")
except Exception as e:
    p(traceback.format_exc()); verdict("4 scorer", False, str(e)[:120])

# ------------------------------------------------------------------ 5
head("5. BUILD_CARD  (Step 0 test 9 -- was 0 cards)")
MA = None
try:
    import Movers_app as MA
    made = 0
    for n in names[:6]:
        card = MA.build_card({"sym": n["sym"], "sid": str(n["sid"]),
                              "lists": n.get("lists") or [], "tvol": n.get("tvol"),
                              "day_pct": n.get("day_pct")})
        if not card:
            p(f"    {str(n['sym']):14s} None")
            continue
        made += 1
        ch = card.get("chart") or {}
        p(f"    {str(card['sym']):14s} tf={card.get('tf')} bars={card.get('bars'):4d} "
          f"px={card.get('price')} day%={card.get('day_pct')} "
          f"chart={len(ch.get('c') or [])} sessVal={card.get('sess_val')} "
          f"cross={card.get('crossBuy')} pinned={card.get('pinned')}")
    verdict("5 build_card", made > 0, f"{made} of 6 complete cards")
except Exception as e:
    p(traceback.format_exc()); verdict("5 build_card", False, str(e)[:120])

# ------------------------------------------------------------------ 6
head("6. THE REAL CYCLE  (what the board would actually put on screen)")
try:
    if MA is None:
        verdict("6 cycle", False, "Movers_app did not import")
    else:
        MA.REFRESH_PER_CYCLE = 45          # this run only; fills faster than 12/cycle
        for i in range(4):
            t0 = time.time()
            MA.cycle()
            s = MA.STATE
            p(f"    pass {i+1}: movers={len(s.get('mv') or []):3d} "
              f"byVol={len(s.get('bv') or []):3d} overall={len(s.get('ov') or []):3d} "
              f"buyTriggered={len(s.get('bt') or []):3d}   ({int(time.time()-t0)}s)")
        s = MA.STATE
        tot = len(s.get("ov") or [])
        p("")
        p("  top 10 of OVERALL, as the Board tab would order them:")
        p(f"    {'#':>2} {'SYMBOL':<14}{'PRICE':>9}{'DAY%':>7}  {'BIAS':<20}{'CROSS':>6} {'PINNED':>7}")
        for i, c in enumerate((s.get("ov") or [])[:10], 1):
            p(f"    {i:2d} {str(c.get('sym')):<14}{str(c.get('price')):>9}"
              f"{str(c.get('day_pct')):>7}  {str(c.get('bias')):<20}"
              f"{str(c.get('crossBuy')):>6} {str(c.get('pinned')):>7}")
        verdict("6 cycle", tot > 0, f"{tot} cards would render on the Board tab")
except Exception as e:
    p(traceback.format_exc()); verdict("6 cycle", False, str(e)[:120])

# ------------------------------------------------------------------ 6b
head("6b. TIMEFRAME OF THE CARDS ON SCREEN")
try:
    cards = (MA.STATE.get("ov") or []) if MA else []
    import collections
    tfc = collections.Counter((c.get("tf"), c.get("tfSrc")) for c in cards)
    p(f"  CARD_TF setting = {getattr(MA, 'CARD_TF', '?')}")
    for (tf, src), k in tfc.most_common():
        p(f"    tf={str(tf):5s} source={str(src):6s}  {k} cards")
    bad = [c for c in cards if (c.get("tf") or "1m") == "1m"]
    p("")
    p("  session split sanity -- 'bars' must be TODAY only, not 3 sessions:")
    for c in cards[:6]:
        ch = c.get("chart") or {}
        p(f"    {str(c.get('sym')):14s} tf={str(c.get('tf')):4s} "
          f"barsToday={c.get('bars')} chartBars={len(ch.get('c') or [])} "
          f"nodip={c.get('nodip')} streak%={c.get('streakPct')} "
          f"sessVal={c.get('sess_val')}")
    verdict("6b timeframe", len(cards) > 0 and len(bad) < len(cards),
            f"{len(cards)-len(bad)} of {len(cards)} cards on sub-minute bars")
except Exception as e:
    p(traceback.format_exc()); verdict("6b timeframe", False, str(e)[:120])

# ------------------------------------------------------------------ 7
head("7. CHART FEED HEALTH")
try:
    st = cf.status()
    p(f"  ok={st['ok']}  fail={st['fail']}  token_expired={st['token_expired']}")
    p(f"  last_ok={st['last_ok']}  last_err={st['last_err']}")
except Exception as e:
    p(f"  {e}")

head("SUMMARY")
w = max((len(r[0]) for r in RES), default=10)
for st_, v, d in RES:
    p(f"  {st_.ljust(w)}  {v:4s}  {d}")
bad = [r for r in RES if r[1] == "FAIL"]
p("")
p(f"  {len(RES)-len(bad)} passed, {len(bad)} failed"
  + ("  ->  CANDLES ARE BACK. Board should render." if not bad
     else f"  ->  FIRST FAILURE: {bad[0][0]}"))
OUT.write_text("\n".join(_buf), encoding="utf-8")
print(f"\nReport written to {OUT}")
