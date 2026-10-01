"""live_exec.py -- turns the engine's DESIRED positions into real Dhan orders,
and guards every one of them with a stop that lives at the broker.

Inert unless env.txt says BROKER_MODE = LIVE and LIVE_ARMED = YES. In PAPER
mode reconcile() returns immediately, so importing this from paper_live.py
changes nothing about the daily paper run.


WHY A RECONCILER AND NOT JUST "place an order when we enter"
------------------------------------------------------------
paper_live.run_book() is a STATELESS deterministic replay. Every 20 seconds it
re-reads the whole tape from 09:15 and rebuilds `closed` and `live` from
scratch. It has no memory between cycles -- that is deliberate, and it is why
the engine is reproducible.

It also means a broker.place() call inside run_book() would re-fire every
order, every 20 seconds, all day.

So execution is a separate step that compares two pictures:

    run_book()  ->  what SHOULD be open now  (rebuilt fresh each cycle)
    the ledger  ->  what IS open at Dhan     (logs/LIVE_LEDGER_<day>.json)
    reconcile() ->  issues only the difference

That one design choice answers three of Sri's worries at once:

  1. No duplicate orders, however often the cycle runs.
  2. CRASH RECOVERY. Restart the engine at 13:00 and run_book() replays the
     morning and again asks for the position it entered at 10:40. The ledger
     already holds it, so nothing is re-sent -- the engine simply resumes
     managing a position it now remembers.
  3. MANUAL EXIT. Sri clicks Exit in the Dhan app. Next cycle the SL poll
     shows the protective order gone or the exit rejected, the ledger is
     marked closed, and the engine moves on instead of fighting the broker.


THE PROTECTIVE STOP -- the real answer to "what if I am away from the laptop"
----------------------------------------------------------------------------
Sri, 29-Sep: "if i lost internet connect and I am away from the laptop, then
in that case what to do? I cant wait till 3:15PM IST as the loss will be huge
or tremendous (Ex: what if it falls below par)."

The honest position before today: STOP_PCT = -1.0 is a SOFTWARE stop computed
inside run_book(). It dies with the process. A dead laptop meant a naked
position until Dhan's own ~15:15 auto-square-off.

It cannot go below zero -- NSE circuit limits cap the daily fall and MIS
positions are squared off the same day -- but the whole of a small account
can go, which is the same thing in practice. At CAPITAL = 10,000, SLOTS = 2,
roughly 25,000 of notional behind each slot at 5x:

    engine alive, software stop -1.0%        ->    -250 per position
    engine DEAD, broker SL at -2.5%          ->    -625 per position
    engine DEAD, no broker SL, 20% circuit   ->  -5,000 per position
                                                 both slots = the entire 10,000

So every entry now gets a STOP_LOSS_MARKET order sent to Dhan the moment the
entry is confirmed filled. It is set WIDER than the software stop on purpose:
in normal running the -1% software stop fires first and this order is
cancelled untouched. It only ever does anything on the day the laptop dies.

Three layers, outermost last:
    -1.0%   software stop      needs the engine alive
    -2.5%   broker SL-M        needs only Dhan  <-- new today
    ~15:15  Dhan auto-square   needs nothing


THE SL IS NEVER SENT BEFORE THE ENTRY IS CONFIRMED FILLED
---------------------------------------------------------
A stop order placed against a position that does not exist is not protection,
it is a naked entry pointing the wrong way: if it triggers, it OPENS a
reversed position. So an entry sits in state ENTRY_SENT until Dhan reports it
traded, and only then does the SL go in, for the quantity actually filled.
Worst case that costs one 20-second cycle of unprotected exposure.


CANCEL BEFORE EXIT, ALWAYS
--------------------------
When the engine wants out, the resting SL is cancelled FIRST. Exit first and
the SL is left behind with nothing to close -- if it later triggers it opens a
brand-new reversed position. If the cancel fails we check whether the SL has
in fact already executed (the broker beat us to it, which is a valid outcome),
and if we still cannot tell, we send NO exit and say so loudly. Doing nothing
is recoverable; a double order is not.


KILL SWITCHES -- env.txt is re-read every cycle, so both work without a restart
------------------------------------------------------------------------------
    LIVE_ARMED = NO             hard stop. No orders of any kind. Positions
                                already open stay protected by their resting
                                broker SL and Dhan's auto-square-off, but the
                                engine will not manage them -- exit by hand in
                                the Dhan app if you pull this mid-position.
    LIVE_NO_NEW_ENTRIES = YES   soft stop. Manages and exits what is open,
                                takes nothing new. This is the one to use
                                intraday.


TUNABLES, all from env.txt so none of this needs a code change
--------------------------------------------------------------
    LIVE_MAX_QTY           hard cap on shares per position. Default 1.
    LIVE_MAX_ENTRY_VALUE   skip any name whose 1-share price exceeds this, in
                           rupees. Default 500, matching Sri's first live test.
                           0 = no cap.
    LIVE_SL_PCT            broker stop distance, negative. Default -2.5.
    LIVE_CROSS_PCT         how far to cross the book so a LIMIT behaves like a
                           market order. Default 0.30.
"""
import json, os, time
from datetime import datetime
from pathlib import Path

import broker

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
LEDGER = HERE / "logs" / "LIVE_LEDGER_{}.json"

_ANNOUNCED = {}

DONE = ("CLOSED", "STOPPED_BY_BROKER", "ENTRY_FAILED", "GONE_AT_BROKER")
FILLED_WORDS = ("TRADED", "EXECUTED", "FILLED", "COMPLETE")
DEAD_WORDS = ("REJECTED", "CANCELLED", "CANCELED", "EXPIRED")


# ------------------------------------------------------------------ env ----
def _num(key, default):
    try:
        return float(broker._plain().get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _flag(key):
    return broker._plain().get(key, "").upper() in ("YES", "TRUE", "1")


def cfg():
    return {"max_qty": int(_num("LIVE_MAX_QTY", 1)),
            "max_entry_value": _num("LIVE_MAX_ENTRY_VALUE", 500),
            "sl_pct": _num("LIVE_SL_PCT", -2.5),
            "cross_pct": _num("LIVE_CROSS_PCT", 0.30),
            "no_new": _flag("LIVE_NO_NEW_ENTRIES")}


# --------------------------------------------------------------- ledger ----
def _path(day):
    return Path(str(LEDGER).format(day))


def load(day):
    try:
        return json.loads(_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(day, led):
    p = _path(day)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(led, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


def _key(sym, in_t):
    """One position = one symbol at one entry time. run_book() is
    deterministic, so in_t is stable across cycles -- and a legitimate
    re-entry into the same name later in the day gets its own row rather
    than colliding with the first."""
    return "%s|%s" % (sym, in_t)


def _said(st):
    return (st.get("status") or "").upper()


# ---------------------------------------------------------------- moves ----
# How hard to cross the book on each successive exit attempt. 30-Sep: the
# first live exit went out at 0.30% and rested UNFILLED while price fell away
# from it -- the market moved through the limit before it could trade. An exit
# that will not fill is not an exit, so each retry reaches further.
CROSS_LADDER = (0.30, 1.00, 2.50)
EXIT_COOLDOWN = 3     # cycles to hold, protected, after the ladder fails


def _send_sl(key, e, c, log):
    """Protective stop, sent only once the entry is confirmed filled."""
    side, qty = e["side"], int(e.get("filled_qty") or e["qty"])
    trig = float(e["in"]) * (1 + side * c["sl_pct"] / 100.0)
    r = broker.place_sl(e["sym"], -side, qty, trig, tag="S" + key)
    if r.get("ok"):
        e["sl_id"], e["sl_trigger"] = r["order_id"], round(trig, 2)
        e.pop("sl_error", None)
        log("LIVE %s: protective stop %s trigger %.2f (order %s)"
            % (e["sym"], r.get("kind", "SL"), trig, r["order_id"]))
        return "%s:SL_PLACED" % key
    e["sl_error"] = str(r.get("detail"))[:200]
    log("LIVE %s: *** PROTECTIVE STOP FAILED *** %s -- position NOT protected"
        % (e["sym"], e["sl_error"]))
    return "%s:SL_FAIL" % key


def _drop_sl(e, log):
    """Cancel the resting stop. Returns True when it is certainly gone.

    False means the stop may still be live -- and in that case the caller must
    NOT send an exit, because two live sell orders on one share becomes a short
    position the moment both trade.
    """
    if not e.get("sl_id"):
        return True
    if broker.cancel(e["sl_id"]).get("ok"):
        e["sl_id"] = None
        return True
    st = _said(broker.status(e["sl_id"]))
    if st in FILLED_WORDS:
        e["state"] = "STOPPED_BY_BROKER"
        log("LIVE %s: stop already executed -- broker closed it" % e["sym"])
        return False
    if st in DEAD_WORDS:
        e["sl_id"] = None
        return True
    log("LIVE %s: *** STOP CANCEL FAILED and stop still live (%s) -- sending "
        "NO exit. Two live orders would be worse than none." % (e["sym"], st or "?"))
    return False


def _send_exit(key, e, ref, c, log):
    """Fire one exit attempt and move to EXIT_SENT. Never claims CLOSED."""
    side = e["side"]
    qty = int(e.get("filled_qty") or e["qty"])
    n = int(e.get("exit_tries") or 0)
    cross = CROSS_LADDER[min(n, len(CROSS_LADDER) - 1)]
    px = float(ref or e.get("fill_px") or e["in"]) * (1 - side * cross / 100.0)
    r = broker.place(e["sym"], -side, qty, px, tag="X" + key)
    if not r.get("ok"):
        e["exit_error"] = str(r.get("detail"))[:200]
        log("LIVE %s: EXIT REJECTED -- %s" % (e["sym"], e["exit_error"]))
        _send_sl(key, e, c, log)          # rejected: put the stop straight back
        return "%s:EXIT_REJECTED" % key
    e["state"] = "EXIT_SENT"
    e["exit_id"] = r["order_id"]
    e["exit_tries"] = n + 1
    e["exit_px_sent"] = round(px, 2)
    e["exit_sent_at"] = datetime.now().strftime("%H:%M:%S")
    log("LIVE %s: exit attempt %d, %d @ ~%.2f (cross %.2f%%, order %s)"
        % (e["sym"], n + 1, qty, px, cross, r["order_id"]))
    return "%s:EXIT_SENT" % key


def _poll_exit(key, e, ref, c, log):
    """The heart of FIX 3. An ACCEPTED exit is not a CLOSED position.

    30-Sep: UJJIVANSFB's exit rested unfilled at 67.53 while price fell to
    67.16. The old code had marked it CLOSED on acceptance, cancelled its
    stop and stopped managing it -- a real share left on the account with no
    stop, no manager and no exit. Sri found it on his own screen.

    So: poll every cycle. Filled -> CLOSED, with the REAL fill price recorded
    (FIX 2). Not filled -> cancel the stale order and either retry harder or,
    once the ladder is exhausted, put the protective stop BACK and return to
    OPEN. Being protected and still holding beats being unprotected and
    believing otherwise.
    """
    st = broker.status(e["exit_id"])
    said = _said(st)
    if said in FILLED_WORDS:
        e["state"] = "CLOSED"
        e["exit_fill"] = st.get("avg_price")
        e["exit_qty"] = st.get("traded_qty")
        e["closed"] = datetime.now().strftime("%H:%M:%S")
        log("LIVE %s: exit FILLED %s @ %s"
            % (e["sym"], e.get("exit_qty"), e.get("exit_fill") or "?"))
        return "%s:CLOSED" % key

    if said in DEAD_WORDS:                       # cancelled or rejected
        e["state"] = "OPEN"
        return _send_sl(key, e, c, log)

    # Still resting. Pull it and try again harder, or give up and re-protect.
    broker.cancel(e["exit_id"])
    e["exit_id"] = None
    if int(e.get("exit_tries") or 0) >= len(CROSS_LADDER):
        # Ladder exhausted. Stop hammering the book: hold the position, put
        # the protective stop BACK, and sit out a few cycles before trying
        # again. Without the cooldown, step 5 would cancel that stop and fire
        # another exit in the very same cycle -- the position would ratchet
        # between naked and protected forever and never actually be protected.
        e["state"] = "OPEN"
        e["exit_cooldown"] = EXIT_COOLDOWN
        log("LIVE %s: exit unfilled after %d attempts -- holding and "
            "re-protecting, will retry in %d cycles"
            % (e["sym"], e.get("exit_tries"), EXIT_COOLDOWN))
        return _send_sl(key, e, c, log)
    e["state"] = "OPEN"
    log("LIVE %s: exit did not fill at %.2f -- retrying harder"
        % (e["sym"], e.get("exit_px_sent") or 0))
    return "%s:EXIT_RETRY" % key


def _open(key, p, led, c, log):
    sym, side = p["sym"], int(p.get("side", 1))
    px_ref = float(p["in"])
    if c["max_entry_value"] and px_ref > c["max_entry_value"]:
        led[key] = {"sym": sym, "state": "ENTRY_FAILED",
                    "detail": "1 share costs %.2f, over LIVE_MAX_ENTRY_VALUE %.0f"
                              % (px_ref, c["max_entry_value"])}
        return "%s:TOO_DEAR" % key
    qty = min(int(p["qty"]), c["max_qty"])
    if qty < 1:
        led[key] = {"sym": sym, "state": "ENTRY_FAILED", "detail": "qty 0"}
        return "%s:QTY0" % key
    px = px_ref * (1 + side * c["cross_pct"] / 100.0)
    r = broker.place(sym, side, qty, px, tag="E" + key)
    if not r.get("ok"):
        led[key] = {"sym": sym, "side": side, "qty": qty, "state": "ENTRY_FAILED",
                    "detail": str(r.get("detail"))[:200]}
        log("LIVE %s: ENTRY REJECTED -- %s" % (sym, led[key]["detail"]))
        return "%s:ENTRY_FAIL" % key
    led[key] = {"sym": sym, "side": side, "qty": qty, "in": px_ref,
                "entry_id": r["order_id"], "sl_id": None, "state": "ENTRY_SENT",
                "sent": datetime.now().strftime("%H:%M:%S")}
    log("LIVE %s: entry %s %d @ ~%.2f (order %s)"
        % (sym, "BUY" if side == 1 else "SELL", qty, px, r["order_id"]))
    return "%s:ENTRY_SENT" % key


# ------------------------------------------------------------ reconcile ----
def _dhan_open_syms(log):
    """Symbols Dhan says we actually hold, or None if we could not ask.

    None is NOT 'flat'. 30-Sep: broker.positions() failed from a sandboxed
    host with a proxy 403 and returned {ok: False, rows: []}; read as an empty
    list that looked exactly like a flat account while two positions were
    open. Never infer 'flat' from a call that did not succeed.
    """
    p = broker.positions()
    if not p.get("ok"):
        log("LIVE: /positions unavailable (%s) -- skipping broker reconcile "
            "rather than assuming flat" % str(p.get("detail"))[:70])
        return None
    out = set()
    for r in (p.get("rows") or []):
        try:
            if int(r.get("netQty") or 0) != 0:
                out.add(str(r.get("tradingSymbol") or "").upper())
        except (TypeError, ValueError):
            continue
    return out


def reconcile(day, desired, closed, log=print):
    """Called once per cycle from paper_live.py, straight after run_book().
    Never raises: a broker problem must not take the paper engine down."""
    m = broker.mode()
    if not _ANNOUNCED.get(day):
        _ANNOUNCED[day] = True
        if m == "LIVE" and broker.armed():
            _c = cfg()
            log("=" * 62)
            log("LIVE EXEC ACTIVE -- REAL ORDERS WILL BE SENT TO DHAN")
            log("  max %s share(s) per position | skip shares over Rs %.0f"
                % (_c["max_qty"], _c["max_entry_value"]))
            log("  broker stop %.2f%% | new entries %s"
                % (_c["sl_pct"], "BLOCKED" if _c["no_new"] else "allowed"))
            log("=" * 62)
        else:
            log("live_exec: PAPER routing in force (mode=%s, armed=%s)"
                % (m, broker.armed()))

    if m != "LIVE":
        return {"mode": m, "note": "live_exec idle -- BROKER_MODE is not LIVE"}
    if not broker.armed():
        return {"mode": m, "note": "LIVE_ARMED is not YES -- no orders sent"}

    c = cfg()
    led = load(day)
    want = {_key(p["sym"], p["in_t"]): p for p in (desired or [])}
    refs = {_key(x["sym"], x["in_t"]): x.get("out") for x in (closed or [])}
    acts = []

    # 1. confirm entry fills, and protect anything not yet protected
    for key, e in led.items():
        if e.get("state") == "ENTRY_SENT":
            st = broker.status(e["entry_id"]); said = _said(st)
            if said in FILLED_WORDS:
                e["state"] = "OPEN"
                e["filled_qty"] = int(st.get("traded_qty") or e["qty"])
                if st.get("avg_price"):
                    e["fill_px"] = st["avg_price"]
                log("LIVE %s: entry FILLED %s @ %s"
                    % (e["sym"], e["filled_qty"], e.get("fill_px") or "?"))
                acts.append(_send_sl(key, e, c, log))
            elif said in DEAD_WORDS:
                e["state"] = "ENTRY_FAILED"
                e["detail"] = "entry %s at Dhan" % said
                acts.append("%s:ENTRY_%s" % (key, said))
        elif e.get("state") == "OPEN" and not e.get("sl_id") and not e.get("sl_error"):
            acts.append(_send_sl(key, e, c, log))

    # 2. FIX 3 -- chase every exit to an actual fill
    for key, e in led.items():
        if e.get("state") == "EXIT_SENT":
            acts.append(_poll_exit(key, e, refs.get(key), c, log))

    # 3. did the broker stop take us out while we were not looking?
    for key, e in led.items():
        if e.get("state") == "OPEN" and e.get("sl_id") \
                and _said(broker.status(e["sl_id"])) in FILLED_WORDS:
            e["state"] = "STOPPED_BY_BROKER"
            log("LIVE %s: protective stop EXECUTED at Dhan" % e["sym"])
            acts.append("%s:SL_HIT" % key)

    # 4. DHAN IS THE TRUTH -- reconcile belief against the real account
    real = _dhan_open_syms(log)
    if real is not None:
        held = {e["sym"].upper() for e in led.values()
                if e.get("state") in ("OPEN", "EXIT_SENT")}
        for key, e in led.items():
            if e.get("state") in ("OPEN", "EXIT_SENT") and e["sym"].upper() not in real:
                e["state"] = "GONE_AT_BROKER"
                e["closed"] = datetime.now().strftime("%H:%M:%S")
                log("LIVE %s: Dhan shows no position -- closed outside the "
                    "engine. Slot released." % e["sym"])
                acts.append("%s:GONE" % key)
        for sym in real - held:
            log("LIVE *** UNTRACKED POSITION AT DHAN: %s -- the engine is NOT "
                "managing it. Exit by hand in the Dhan app." % sym)
            acts.append("UNTRACKED:%s" % sym)

    # 5. anything open the engine no longer wants -> begin exiting
    for key, e in led.items():
        if e.get("state") != "OPEN" or key in want:
            continue
        if int(e.get("exit_cooldown") or 0) > 0:
            e["exit_cooldown"] = int(e["exit_cooldown"]) - 1
            if e["exit_cooldown"] == 0:
                e["exit_tries"] = 0          # ladder resets, try again fresh
            continue
        if True:
            if _drop_sl(e, log):
                acts.append(_send_exit(key, e, refs.get(key), c, log))
            else:
                acts.append("%s:SL_CANCEL_FAIL" % key)

    # 6. anything the engine wants that we do not hold
    if c["no_new"]:
        n = sum(1 for k in want if k not in led)
        if n:
            log("LIVE: LIVE_NO_NEW_ENTRIES=YES -- %d new signal(s) not taken" % n)
    else:
        for key, p in want.items():
            if key not in led:
                acts.append(_open(key, p, led, c, log))

    save(day, led)
    out = {"mode": m, "armed": True, "cfg": c,
           "open": [e["sym"] for e in led.values()
                    if e.get("state") in ("OPEN", "EXIT_SENT")],
           "unprotected": [e["sym"] for e in led.values()
                           if e.get("state") == "OPEN" and not e.get("sl_id")],
           "rows": len(led)}
    if acts:
        out["actions"] = acts
    return out


# ------------------------------------------------------------ standalone ---
# Plain-English states, so the status window needs no knowledge of this file.
SAYS = {
    "ENTRY_SENT":        "entry sent, waiting for Dhan to fill it",
    "OPEN":              "OPEN at Dhan",
    "CLOSED":            "closed by the engine",
    "STOPPED_BY_BROKER": "closed by the BROKER STOP (engine did not do this)",
    "GONE_AT_BROKER":    "closed outside the engine (manual exit in the app)",
    "ENTRY_FAILED":      "never opened",
}


def show(day=None):
    """Human-readable status. LIVE_STATUS.bat is a wrapper around this, so the
    output has to make sense to someone who has never opened this file."""
    day = day or datetime.now().strftime("%Y%m%d")
    m, a = broker.mode(), broker.armed()
    want = broker.configured_mode()
    c = cfg()

    print("=" * 68)
    print("  LIVE TRADING STATUS        %s" % datetime.now().strftime("%d-%b-%Y %H:%M:%S"))
    print("=" * 68)
    print("  env.txt asks for     : BROKER_MODE=%s" % want)
    print("  ACTUALLY IN FORCE    : %s" % m)
    if broker.live_blocked_by_launcher():
        print("      ^ env.txt says LIVE, but this window was not started by")
        print("        LIVE_START.bat, so the launcher lock has forced PAPER.")
        print("        Real orders are impossible here. This is by design.")
    print("  LIVE_ARMED           : %s" % ("YES" if a else "NO"))
    print("  new entries          : %s" % ("BLOCKED (LIVE_NO_NEW_ENTRIES=YES)"
                                           if c["no_new"] else "allowed"))
    print("  shares per position  : %s" % c["max_qty"])
    print("  skip shares dearer   : Rs %.0f" % c["max_entry_value"])
    print("  broker stop distance : %.2f%%" % c["sl_pct"])
    if m == "LIVE":
        th = broker.token_hours_left()
        print("  Dhan token hours left: %s%s"
              % (th, "   <-- REFRESH IT" if (th or 0) <= 1 else ""))
    print()

    if m != "LIVE":
        print("  Nothing has been sent to Dhan, and nothing can be from this")
        print("  window. This is the normal, safe state.")
        if want == "LIVE":
            print()
            print("  To trade live, run LIVE_STATUS.bat from inside a live")
            print("  session, or just run LIVE_START.bat -- it sets the flag.")
        print("=" * 68)
        return

    led = load(day)
    if not led:
        print("  No live orders recorded for %s yet." % day)
        print("  Ledger file: %s" % _path(day))
        print("=" * 68)
        return

    print("  OUR LEDGER -- what this engine believes it did today")
    print("  %-12s %-9s %-4s %-34s %s" % ("STOCK", "ENTRY", "QTY", "STATE", "STOP"))
    print("  " + "-" * 64)
    unprot = []
    for k, e in led.items():
        sym = e.get("sym", k.split("|")[0])
        st = e.get("state", "?")
        say = SAYS.get(st, st)
        stop = ("trigger %.2f" % e["sl_trigger"]) if e.get("sl_trigger") else "-"
        if st == "OPEN" and not e.get("sl_id"):
            say, stop = "OPEN -- *** NOT PROTECTED ***", "NONE"
            unprot.append(sym)
        print("  %-12s %-9s %-4s %-34s %s"
              % (sym, k.split("|")[1][:8], e.get("filled_qty") or e.get("qty") or "-",
                 say, stop))
        for why in ("detail", "sl_error", "exit_error"):
            if e.get(why):
                print("               ^ %s: %s" % (why, str(e[why])[:100]))
    print()

    if unprot:
        print("  " + "!" * 64)
        print("  !!  %s has NO broker stop behind it." % ", ".join(unprot))
        print("  !!  If this laptop dies, that position is unprotected until")
        print("  !!  Dhan squares it off at ~15:15. Exit it by hand in the")
        print("  !!  Dhan app if you are leaving the desk.")
        print("  " + "!" * 64)
        print()

    # Dhan's own view. Ours can be wrong; Dhan's cannot.
    p = broker.positions()
    print("  DHAN'S OWN VIEW -- the truth, if these two ever disagree")
    if not p.get("ok"):
        print("    could not read /positions -- %s" % str(p.get("detail"))[:90])
    else:
        rows = [r for r in (p.get("rows") or []) if int(r.get("netQty") or 0) != 0]
        if not rows:
            print("    flat -- no open positions at Dhan.")
        for r in rows:
            print("    %-14s netQty %-6s avg %-10s P&L %s"
                  % (r.get("tradingSymbol"), r.get("netQty"),
                     r.get("buyAvg") or r.get("costPrice"), r.get("realizedProfit")))
    f = broker.funds()
    if f.get("ok"):
        print("    available balance : Rs %s" % f.get("balance"))
    print("=" * 68)


def open_count(day=None):
    """How many positions this engine believes are live at Dhan right now."""
    day = day or datetime.now().strftime("%Y%m%d")
    return sum(1 for e in load(day).values()
               if e.get("state") in ("OPEN", "ENTRY_SENT"))


if __name__ == "__main__":
    import sys
    if "--open-count" in sys.argv:
        # Used by EYE_START.bat to refuse to start on top of a live session.
        # Exit 2 means "there is real money open right now". 30-Sep: Sri has
        # launched the paper board every morning for weeks, so the likeliest
        # live-trading mistake is not a bad signal -- it is double-clicking
        # the familiar icon at 11:00 and silently killing the live board,
        # leaving a real position with nothing managing it.
        n = open_count()
        print(n)
        sys.exit(2 if n else 0)
    show(sys.argv[1] if len(sys.argv) > 1 else None)
