"""Fails loudly BEFORE the board starts if the human-eye logic cannot trade.
The failure this guards against is the quiet one: a tab that looks perfectly
healthy all day and never places a trade."""
import sys
from pathlib import Path
HERE=Path(__file__).parent
ok=True
def chk(name,cond,detail=""):
    global ok
    print(f"   {'PASS' if cond else 'FAIL'}  {name}{'  -- '+detail if detail and not cond else ''}")
    if not cond: ok=False
try:
    import numpy,pandas; chk("numpy + pandas",True)
except Exception as e: chk("numpy + pandas",False,str(e)[:60])
try:
    import tune_v6,tune_dyn,ind_lib; from eye_cfg import CFG
    chk("v6 engine + locked config",True)
except Exception as e: chk("v6 engine + locked config",False,str(e)[:70])
try:
    import paper_engine as pe
    c=pe.charges(250000,250000)["total"]
    chk("Dhan charge model",130<c<145,f"Rs {c} on a 2.5L round trip looks wrong")
    print(f"         charges Rs {c} per Rs 2,50,000 round trip")
except Exception as e: chk("Dhan charge model",False,str(e)[:70])
try:
    import eye_signals as ES
    chk("eye_signals importable",True)
    u=ES._universe(ES._day())
    chk("tradeable universe non-empty",len(u)>0,"no premarket_calls and no previous tape")
    print(f"         {len(u)} names, selection score >= {ES.SELECT_MIN}")
    pv=ES._prev_tape_day(ES._day())
    chk("previous-session tape for warm-up",pv is not None,
        "indicators will be blind until ~09:40 without it")
    if pv: print(f"         warm-up from {pv}")
except Exception as e: chk("eye_signals importable",False,str(e)[:70])
try:
    import live_paper as LP
    chk("live_paper loads",True)
    chk("EYE_LOGIC is on",LP.EYE_LOGIC is True,"old engine would run instead")
    chk("eye_signals wired into live_paper",LP._eye is not None)
    chk("2 slots",LP.UNITS==2,f"UNITS={LP.UNITS}")
    chk("entry window to 15:05",LP.ENTRY_TO=="15:05:00",f"ENTRY_TO={LP.ENTRY_TO}")
    chk("sid lookup works",bool(LP.pe_sid("AGI")),"security_id_list.csv missing or stale")
except Exception as e: chk("live_paper loads",False,str(e)[:70])
# ---------------------------------------------------------------------------
# THE ENGINE THAT ACTUALLY TRADES.
# Everything above checks live_paper.py, which owns the Board's tab. The
# module that places the paper trades is paper_live.py, launched detached by
# Movers_app. Until 26-Sep nothing here touched it, so the pre-flight could
# print 12 PASS and "OK" with the real engine broken.
# ---------------------------------------------------------------------------
try:
    import paper_live as PL
    chk("paper_live loads (the trading engine)", True)
    chk("2 slots", PL.SLOTS == 2, f"SLOTS={PL.SLOTS}")
    chk("square-off before Dhan's 15:10", PL.SQUARE_OFF <= "15:08:00",
        f"SQUARE_OFF={PL.SQUARE_OFF} -- Dhan force-closes at 15:10")
    chk("circuit guard armed", PL.CIRCUIT_BUF_PCT > 0,
        "CIRCUIT_BUF_PCT=0, a position could get locked at a band")
    chk("leverage wired into sizing", hasattr(PL, "LV"))
except Exception as e:
    chk("paper_live loads (the trading engine)", False, str(e)[:70])

try:
    import leverage as LV
    chk("leverage module", True)
    chk("Rule A: only an exact 5.00X is 5x",
        LV.rule_a(5.0) == 5.0 and LV.rule_a(4.57) == 1.0 and LV.rule_a(None) == 1.0,
        "the rule is wrong -- a restricted stock would be oversized")
    _sid, _lot = LV.sec_lookup("HDFCBANK")
    chk("security-id lookup for leverage", bool(_sid), "security_id_list.csv stale?")
except Exception as e:
    chk("leverage module", False, str(e)[:70])

try:
    import eye_strategy as ES2
    chk("eye_strategy loads", True)
    # 28-Sep: MIN_LEG gated entry on the EXIT bar index -- it could not be known
    # until the trade was over. Any non-zero value reintroduces the look-ahead
    # and makes every P&L figure unachievable. See eye_strategy.py.
    chk("MIN_LEG 0 (no look-ahead entry gate)", ES2.MIN_LEG == 0,
        f"MIN_LEG={ES2.MIN_LEG} -- entry would be gated on the future exit bar")
    chk("last entry 14:00", ES2.LAST_ENTRY == "1400", f"LAST_ENTRY={ES2.LAST_ENTRY}")
    chk("rejected knobs still off",
        ES2.MIN_EXP == 0 and ES2.VOLX_MIN == 0 and ES2.MAX_UP == 0
        and ES2.OPEN_FREE == 0 and not ES2.DEAD_FROM,
        "a rule that tested badly has been left switched on")
    print(f"         shorts {'ON' if ES2.SHORTS else 'off'} | "
          f"MIN_LEG {ES2.MIN_LEG} | last entry {ES2.LAST_ENTRY}")
except Exception as e:
    chk("eye_strategy loads", False, str(e)[:70])

# --------------------------------------------------------------- Dhan IP ---
# api.dhan.co is IPv4-ONLY (no AAAA record, verified 29-Sep). Dhan rejects
# ORDER PLACEMENT from an un-whitelisted IP and allows a re-set only once
# every 7 days, so a rotated IP is a week-long outage. Advisory while we are
# on PAPER/SANDBOX; a hard stop the moment BROKER_MODE is LIVE.
# LIVE-ONLY SANITY, 29-Sep. Nothing here can fire in PAPER mode. It exists so
# the first real-money morning cannot start on a silly setting -- a size cap
# left wide, or a broker stop sitting INSIDE the software stop (which would
# make the broker stop fire first every day instead of only on a crash).
try:
    import broker as _b
    if _b.mode() == "LIVE":
        import live_exec as _lx
        _c = _lx.cfg()
        chk("live_exec order router imports", True,
            "qty cap %s | entry cap Rs %.0f | broker SL %.2f%%"
            % (_c["max_qty"], _c["max_entry_value"], _c["sl_pct"]))
        chk("LIVE_MAX_QTY is a sane first size", 1 <= _c["max_qty"] <= 5,
            "LIVE_MAX_QTY=%s -- the state doc requires 1 share on day one"
            % _c["max_qty"])
        chk("broker SL sits OUTSIDE the software stop",
            _c["sl_pct"] < 0 and abs(_c["sl_pct"]) > abs(PL.STOP_PCT),
            "LIVE_SL_PCT=%s vs software STOP_PCT=%s -- the broker stop must be "
            "the WIDER of the two or it fires first every day"
            % (_c["sl_pct"], PL.STOP_PCT))
        _th = _b.token_hours_left()
        chk("Dhan access token has life left", (_th or 0) > 1,
            "token hours left: %s -- refresh it in env.txt" % _th)
except Exception as _e:
    print("   note  LIVE sanity block unavailable -- %s" % str(_e)[:70])

try:
    import ip_check, broker
    _mode = broker.mode()
    _st, _msg = ip_check.check()
    if _mode == "LIVE":
        chk("Dhan IP whitelist matches this machine", _st == "OK", _msg)
    else:
        print(f"   {'PASS' if _st == 'OK' else 'note'}  Dhan IP whitelist "
              f"[{_st}] -- {_msg}")
        print(f"         BROKER_MODE={_mode}, so this cannot block the run.")
except Exception as e:
    print(f"   note  Dhan IP whitelist check unavailable -- {str(e)[:70]}")

print()
print("   PRE-FLIGHT OK -- paper trading will auto-start at 09:15." if ok
      else "   PRE-FLIGHT FAILED.")
sys.exit(0 if ok else 1)
