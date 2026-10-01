"""broker.py -- the ONLY place the engine talks to a broker.

Three modes, chosen by BROKER_MODE in env.txt (default PAPER):

    PAPER     no network at all. Fills are simulated inside our own engine,
              exactly as paper_live.py has always done. This is the default
              and nothing changes for the daily paper run.
    SANDBOX   real HTTP calls to sandbox.dhan.co. Proves auth, payload shape
              and the order lifecycle. Fills are FAKE (~Rs 100) and capital
              resets to Rs 10,00,000 daily -- never read P&L from here.
    LIVE      real HTTP calls to api.dhan.co. REAL MONEY. Refuses to place
              anything unless LIVE_ARMED = YES is also present in env.txt.

Nothing in this module is imported by paper_live.py yet.
"""
import base64, json, os, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "env.txt")

HOSTS = {"SANDBOX": "https://sandbox.dhan.co/v2",
         "LIVE":    "https://api.dhan.co/v2"}

SEGMENT  = "NSE_EQ"
PRODUCT  = "INTRADAY"
VALIDITY = "DAY"
LIMIT_BUF = 0.60      # how far the stop-limit sits beyond its trigger, %
TIMEOUT  = 8          # seconds per call. /orders can hang on Dhan's side.


# ---------------------------------------------------------------- env ------
def _plain(path=ENV):
    """Every KEY = VALUE line. Ignores the 'Client:'/'Token:' live block."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                v = v.strip()
                if v and not v.startswith("PASTE_"):
                    out[k.strip().upper()] = v
    except OSError:
        pass
    return out


def configured_mode():
    """What env.txt ASKS for. Not necessarily what you get -- see mode()."""
    m = _plain().get("BROKER_MODE", "PAPER").upper()
    return m if m in ("PAPER", "SANDBOX", "LIVE") else "PAPER"


def mode():
    """The mode that is actually in force.

    THE LAUNCHER LOCK, 29-Sep. Sri: "my understanding is, eye_start.bat will
    continue to work as-is and has no effect on live trading?"

    Before this function existed that was FALSE, and dangerously so. Live
    trading was decided entirely by BROKER_MODE in env.txt, which every
    launcher shares. A BROKER_MODE=LIVE left in env.txt overnight meant that
    double-clicking EYE_START.bat out of habit the next morning would place
    real orders -- the paper launcher, trading real money, looking completely
    normal while doing it. env.txt was found in exactly that state on 29-Sep.

    So LIVE now needs TWO independent things that live in different places:

        BROKER_MODE=LIVE in env.txt     (the intent)
        SCALP_LIVE_OK=1 in the process  (the launcher)

    Only LIVE_START.bat sets SCALP_LIVE_OK. EYE_START.bat explicitly clears
    it. Nothing else sets it, so a bare `python paper_live.py`, the tiers
    worker, a scheduled task or an IDE run can never go live either --
    whatever env.txt happens to say. Downgrading to PAPER is always safe;
    that is why the failure direction is one-way.

    SANDBOX is left alone: it spends no money, so it needs no lock.
    """
    m = configured_mode()
    if m == "LIVE" and os.environ.get("SCALP_LIVE_OK") != "1":
        return "PAPER"
    return m


def live_blocked_by_launcher():
    """True when env.txt wants LIVE but this process was not started by
    LIVE_START.bat. Used for messaging, never for a trading decision."""
    return configured_mode() == "LIVE" and os.environ.get("SCALP_LIVE_OK") != "1"


def _creds(m):
    e = _plain()
    if m == "SANDBOX":
        return e.get("SANDBOX_CLIENT"), e.get("SANDBOX_JWT")
    from Opus_env_loader import load_env          # live keys live in their own block
    d = load_env()
    return d["client_id"], d["token"]


def armed():
    return _plain().get("LIVE_ARMED", "").upper() in ("YES", "TRUE", "1")


def token_hours_left(m=None):
    """Hours until the access token for this mode expires, or None."""
    try:
        _, tok = _creds(m or mode())
        pl = tok.split(".")[1]
        pl += "=" * (-len(pl) % 4)
        return round((json.loads(base64.urlsafe_b64decode(pl))["exp"] - time.time()) / 3600, 1)
    except Exception:
        return None


# --------------------------------------------------------------- http ------
def _call(method, path, body=None, m=None):
    """(http_status, parsed_json_or_text). Never raises."""
    m = m or mode()
    if m == "PAPER":
        return 0, {"error": "PAPER mode makes no network calls"}
    cid, tok = _creds(m)
    if not cid or not tok:
        return 0, {"error": "no credentials for %s mode" % m}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        HOSTS[m] + path, data=data, method=method,
        headers={"access-token": tok, "client-id": cid,
                 "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


# -------------------------------------------------------------- orders -----
def _sid(sym):
    from leverage import sec_lookup            # already resolves NSE security ids
    sid, _lot = sec_lookup(sym)
    return str(sid) if sid else None


def _corr(tag):
    """Dhan's correlationId accepts ALPHANUMERIC ONLY, max 25 chars.

    30-Sep, first live order ever attempted: the tag was built as
    "E" + "UJJIVANSFB|09:24:00", and Dhan rejected the order outright with
        DH-905  Input_Exception  'Invalid correlationId'
    The "|" and ":" came straight from live_exec's ledger key. A real,
    routable, in-the-money signal was lost to a punctuation character.
    Strip to alphanumerics rather than trusting any caller's key format.
    """
    s = "".join(ch for ch in str(tag) if ch.isalnum())
    return s[:20] or "SCALP"


def place(sym, side, qty, price, tag=None, m=None):
    """side: 1 = BUY, -1 = SELL. LIMIT only -- sandbox rejects MARKET.

    Returns {ok, order_id, status, mode, detail}. Never raises.
    """
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "order_id": "PAPER-%s-%d" % (sym, time.time() * 1000),
                "status": "TRADED", "mode": "PAPER", "detail": "simulated in-engine"}
    if m == "LIVE" and not armed():
        return {"ok": False, "order_id": None, "status": "BLOCKED", "mode": m,
                "detail": "LIVE mode but LIVE_ARMED is not YES in env.txt"}
    sid = _sid(sym)
    if not sid:
        return {"ok": False, "order_id": None, "status": "NO_SECURITY_ID", "mode": m,
                "detail": "%s not found in security_id_list.csv" % sym}
    cid, _ = _creds(m)
    body = {"dhanClientId": cid,
            "transactionType": "BUY" if side == 1 else "SELL",
            "exchangeSegment": SEGMENT, "productType": PRODUCT,
            "orderType": "LIMIT", "validity": VALIDITY,
            "securityId": sid, "quantity": int(qty),
            "disclosedQuantity": 0, "price": round(float(price), 2),
            "triggerPrice": 0, "afterMarketOrder": False}
    if tag:
        body["correlationId"] = _corr(tag)
    st, r = _call("POST", "/orders", body, m)
    ok = st in (200, 202) and isinstance(r, dict) and bool(r.get("orderId"))
    return {"ok": ok, "order_id": (r or {}).get("orderId") if isinstance(r, dict) else None,
            "status": (r or {}).get("orderStatus") if isinstance(r, dict) else None,
            "mode": m, "http": st, "detail": r}


def status(order_id, m=None):
    """Single-order lookup. Avoids GET /orders, which hangs on Dhan's side."""
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "status": "TRADED", "detail": "simulated"}
    st, r = _call("GET", "/orders/%s" % order_id, None, m)
    d = r[0] if isinstance(r, list) and r else r
    return {"ok": st == 200, "http": st,
            "status": (d or {}).get("orderStatus") if isinstance(d, dict) else None,
            "traded_qty": (d or {}).get("filled_qty") or (d or {}).get("filledQty"),
            "avg_price": (d or {}).get("averageTradedPrice"), "detail": d}


def cancel(order_id, m=None):
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "detail": "simulated"}
    st, r = _call("DELETE", "/orders/%s" % order_id, None, m)
    return {"ok": st in (200, 202), "http": st, "detail": r}


def positions(m=None):
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "rows": [], "detail": "simulated"}
    st, r = _call("GET", "/positions", None, m)
    return {"ok": st == 200, "http": st, "rows": r if isinstance(r, list) else [], "detail": r}


def funds(m=None):
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "balance": None, "detail": "simulated"}
    st, r = _call("GET", "/fundlimit", None, m)
    bal = None
    if isinstance(r, dict):
        bal = r.get("availabelBalance", r.get("availableBalance"))
    return {"ok": st == 200, "http": st, "balance": bal, "detail": r}


def place_sl(sym, side, qty, trigger, tag=None, m=None):
    """A RESTING STOP_LOSS_MARKET order that lives at Dhan, not in this process.

    side: 1 = BUY (protects a short), -1 = SELL (protects a long).

    WHY THIS EXISTS -- Sri, 29-Sep:
        "if i lost internet connect and I am away from the laptop ... I cant
         wait till 3:15PM IST as the loss will be huge"

    He is right. STOP_PCT = -1.0 in paper_live.py is a SOFTWARE stop: it is
    recomputed each cycle inside run_book() and dies the moment the process
    dies. Before this function existed, a crashed laptop or a dropped line
    meant a naked position until Dhan's own ~15:15 auto-square-off.

    This order is the opposite: once accepted it sits in Dhan's book and
    fires whether or not our engine, our laptop or our internet still exist.
    It is insurance, never strategy -- it is set deliberately WIDER than the
    software stop so that in normal running the software stop always wins and
    this one is cancelled untouched.

    orderType STOP_LOSS_MARKET takes triggerPrice and price = 0: once the
    trigger is touched it becomes a market order, so it fills rather than
    resting unfilled through a fast move -- which is the whole point.
    """
    m = m or mode()
    if m == "PAPER":
        return {"ok": True, "order_id": "PAPERSL-%s-%d" % (sym, time.time() * 1000),
                "status": "PENDING", "mode": "PAPER", "detail": "simulated"}
    if m == "LIVE" and not armed():
        return {"ok": False, "order_id": None, "status": "BLOCKED", "mode": m,
                "detail": "LIVE mode but LIVE_ARMED is not YES in env.txt"}
    sid = _sid(sym)
    if not sid:
        return {"ok": False, "order_id": None, "status": "NO_SECURITY_ID", "mode": m,
                "detail": "%s not found in security_id_list.csv" % sym}
    cid, _ = _creds(m)
    body = {"dhanClientId": cid,
            "transactionType": "BUY" if side == 1 else "SELL",
            "exchangeSegment": SEGMENT, "productType": PRODUCT,
            "orderType": "STOP_LOSS_MARKET", "validity": VALIDITY,
            "securityId": sid, "quantity": int(qty),
            "disclosedQuantity": 0, "price": 0,
            "triggerPrice": round(float(trigger), 2), "afterMarketOrder": False}
    if tag:
        body["correlationId"] = _corr(tag)
    st, r = _call("POST", "/orders", body, m)
    ok = st in (200, 202) and isinstance(r, dict) and bool(r.get("orderId"))

    # FALLBACK TO A STOP-LIMIT, 30-Sep. The first live protective stop ever
    # sent -- DELTACORP, SELL SL-M, trigger 78.63, price 0 -- came back
    #     DH-906  Order_Error  'Trigger Price should be greater than Price'
    # Numerically 78.63 IS greater than 0, so Dhan is not accepting price = 0
    # on this order type; it wants a real limit price with the trigger on the
    # correct side of it (for a SELL stop: trigger ABOVE the limit).
    # A stop-limit can in principle miss a violent gap, so the limit is placed
    # a full LIMIT_BUF% beyond the trigger -- deep enough to fill in anything
    # short of a circuit lock, which Dhan's own square-off then catches. An
    # imperfect stop that Dhan accepts beats a perfect one it rejects.
    if not ok:
        lim = float(trigger) * (1 - LIMIT_BUF / 100.0) if side == -1 \
            else float(trigger) * (1 + LIMIT_BUF / 100.0)
        body2 = dict(body, orderType="STOP_LOSS", price=round(lim, 2))
        st2, r2 = _call("POST", "/orders", body2, m)
        ok2 = st2 in (200, 202) and isinstance(r2, dict) and bool(r2.get("orderId"))
        if ok2:
            return {"ok": True, "order_id": r2.get("orderId"),
                    "status": r2.get("orderStatus"), "mode": m, "http": st2,
                    "kind": "STOP_LOSS", "limit": round(lim, 2), "detail": r2}
        return {"ok": False, "order_id": None, "status": None, "mode": m,
                "http": st, "detail": {"sl_market": r, "sl_limit": r2}}

    return {"ok": ok, "order_id": (r or {}).get("orderId") if isinstance(r, dict) else None,
            "status": (r or {}).get("orderStatus") if isinstance(r, dict) else None,
            "mode": m, "http": st, "kind": "STOP_LOSS_MARKET", "detail": r}


if __name__ == "__main__":
    print("BROKER_MODE :", mode())
    print("LIVE_ARMED  :", armed())
    print("token hours :", token_hours_left())
