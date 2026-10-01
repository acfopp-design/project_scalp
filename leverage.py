"""leverage.py - per-stock intraday leverage, Rule A.

The problem this solves
-----------------------
Dhan does not give every stock 5x intraday leverage. Some are 1x. Sizing a
Rs 2,50,000 position on a 1x stock needs Rs 2,50,000 of real capital, and a
live order would simply be rejected.

Rule A
------
Dhan's /v2/margincalculator returns a `leverage` string. Verified by hand
against the Dhan web UI Margin tab on 26-Sep-2026:

    ATHERENERG  api 5.00X  UI 5.00 X   ok
    WHIRLPOOL   api 5.00X  UI 5.00 X   ok
    HEGAM       api 4.57X  UI 1.00 X   api too high
    CLAYCRAFT   api 1.87X  UI 1.00 X   api too high
    KANOHAR     api 4.00X  UI 1.00 X   api too high

So: EXACTLY 5.00X means a genuine 5x. Any other value - and any error,
timeout or missing field - means treat the stock as 1x.

This is deliberately asymmetric. Being wrong here can only ever UNDER-size a
position, which costs some profit. It can never over-size one, which is what
gets a live order rejected. Never "fix" this by deriving leverage from
totalMargin: that is what produced the bogus 4.57x for HEGAM.

Leverage can change day to day (exchange/SEBI margin revisions), so the cache
is per-day and is never carried over to the next session.
"""
import json, os, re, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://api.dhan.co/v2/margincalculator"

# Dhan's own published MIS (intraday) list. The website table at
# dhan.co/margin-intraday-squareoff-stocks-list/ is a rendering of this sheet.
# Of 4,904 scrips on 26-Sep: 3,075 are 1x, 1,727 are 5x, 85 are 2x, a handful
# 3.03x / 2.22x / 4x. A scrip ABSENT from the sheet has no MIS facility at all,
# i.e. 1x. The sheet's own header warns it lags the app ("we update this sheet
# multiple times to reflect near live status"), which is why it is used as a
# CAP and never on its own - it showed HEGAM as 5x while the UI said 1.00X.
SHEET = ("https://docs.google.com/spreadsheets/d/"
         "1zqhM3geRNW_ZzEx62y0W5U2ZlaXxG-NDn0V8sJk5TQ4/gviz/tq"
         "?tqx=out:csv&gid=1663719548")
_ROW = re.compile(r'^"(\d+)","([A-Z0-9&.\-]+)","(INE[0-9A-Z]{9})","([^"]*)"')
FULL_LEV = 5.0          # what "exactly 5.00X" is worth
SAFE_LEV = 1.0          # everything else, including unknown

_sheet = None           # symbol -> float, or {} when unavailable
_sheet_day = None
_lock = threading.Lock()
_mem = {}               # sym -> float, this process
_day = None
_stats = {"hits": 0, "api": 0, "unknown": 0, "err": 0}


def _cache_path(day):
    return os.path.join(HERE, "logs", f"LEVERAGE_{day}.json")


def _load(day):
    global _day, _mem
    if _day == day:
        return
    _day, _mem = day, {}
    p = _cache_path(day)
    if os.path.exists(p):
        try:
            _mem = {k: float(v) for k, v in json.load(open(p, encoding="utf-8")).items()}
        except Exception:
            _mem = {}


def _save():
    if not _day:
        return
    try:
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        tmp = _cache_path(_day) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_mem, f)
        os.replace(tmp, _cache_path(_day))
    except OSError:
        pass


def _creds():
    import Opus_env_loader as EL
    e = EL.load_env()
    return e["client_id"], e["token"]


def _ask(sid, price, qty, cid, tok):
    """Dhan's own leverage string, or None. NEVER derived from totalMargin."""
    body = json.dumps({"dhanClientId": cid, "exchangeSegment": "NSE_EQ",
                       "transactionType": "BUY", "quantity": int(max(1, qty)),
                       "productType": "INTRADAY", "securityId": str(sid),
                       "price": float(price)}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "access-token": tok, "client-id": cid})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    d = d.get("data") if isinstance(d.get("data"), dict) else d
    v = d.get("leverage")
    if v in (None, "", 0, "0"):
        return None
    try:
        return float(str(v).upper().replace("X", "").strip())
    except ValueError:
        return None


def load_sheet(day, log=None, force=False):
    """Dhan's published MIS list for `day`. {} if it cannot be fetched."""
    global _sheet, _sheet_day
    if _sheet is not None and _sheet_day == day and not force:
        return _sheet
    path = os.path.join(HERE, "logs", f"MIS_SHEET_{day}.csv")
    txt = None
    if os.path.exists(path) and not force:
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            txt = None
    if not txt:
        try:
            req = urllib.request.Request(SHEET, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "ignore")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w", encoding="utf-8").write(txt)
        except Exception as e:
            if log:
                log(f"leverage: MIS sheet unavailable ({type(e).__name__}) - "
                    f"falling back to Rule A alone")
            _sheet, _sheet_day = {}, day
            return _sheet
    m = {}
    for line in txt.split("\n"):
        g = _ROW.match(line)
        if not g:
            continue
        try:
            m[g.group(2)] = float(g.group(4).lower().replace("x", "").strip())
        except ValueError:
            pass
    _sheet, _sheet_day = m, day
    if log:
        n1 = sum(1 for v in m.values() if v <= 1.0)
        log(f"leverage: MIS sheet {len(m)} scrips ({n1} at 1x)")
    return m


def sheet_lev(sym):
    """Sheet leverage, or None when the sheet is not loaded.

    Absent from a LOADED sheet means no MIS facility -> 1x.
    """
    if not _sheet:
        return None
    return float(_sheet.get(sym, SAFE_LEV))


def combine(api_lev, sht):
    """The shipped rule: the LOWER of the two sources, 1x if neither answers.

    Checked against the five stocks read by eye off the Dhan Margin tab on
    26-Sep - ATHERENERG 5, WHIRLPOOL 5, HEGAM 1, CLAYCRAFT 1, KANOHAR 1 - and
    correct on all five. Neither source alone is: the API rated HEGAM 4.57X
    and the sheet rated it 5x, while Dhan actually allows 1x.
    """
    a = rule_a(api_lev) if api_lev is not None else None
    vals = [v for v in (a, sht) if v is not None]
    return min(vals) if vals else SAFE_LEV


def rule_a(api_lev):
    """The whole rule, in one place so it can be tested."""
    return FULL_LEV if api_lev is not None and abs(api_lev - 5.0) < 0.005 else SAFE_LEV


_SEC = None


def sec_lookup(sym):
    """(security_id, lot) for an NSE cash symbol, from the scrip list."""
    global _SEC
    if _SEC is None:
        import csv
        _SEC = {}
        try:
            with open(os.path.join(HERE, "security_id_list.csv"),
                      encoding="utf-8", errors="ignore") as f:
                for row in csv.DictReader(f):
                    if (row.get("SEM_EXM_EXCH_ID") == "NSE"
                            and row.get("SEM_SEGMENT") == "E"):
                        k = (row.get("SEM_TRADING_SYMBOL") or "").strip()
                        if k and k not in _SEC:
                            try:
                                lot = max(1, int(float(row.get("SEM_LOT_UNITS") or 1)))
                            except (TypeError, ValueError):
                                lot = 1
                            _SEC[k] = (row.get("SEM_SMST_SECURITY_ID"), lot)
        except OSError:
            _SEC = {}
    return _SEC.get(sym, (None, 1))


def prefetch_symbols(day, prices, log=None):
    """prices: {symbol: last_price}. Resolves ids itself."""
    wants = []
    for sym, px in prices.items():
        sid, lot = sec_lookup(sym)
        if sid and px:
            wants.append((sym, sid, float(px), lot))
    return prefetch(day, wants, log=log)


def prefetch(day, wants, log=None):
    """wants: iterable of (symbol, security_id, price, lot). Fills the day cache.

    Runs once during warm-up. Anything it cannot resolve stays out of the
    cache and will be treated as 1x by lev_for().
    """
    _load(day)
    load_sheet(day, log=log)
    todo = [(s, sid, px, lot) for (s, sid, px, lot) in wants
            if s not in _mem and sid]
    if not todo:
        return _stats
    try:
        cid, tok = _creds()
    except Exception as e:
        if log:
            log(f"leverage: no credentials ({type(e).__name__}) - every stock will size at 1x")
        return _stats
    got = bad = 0
    for s, sid, px, lot in todo:
        try:
            lv = _ask(sid, px, lot, cid, tok)
            with _lock:
                _mem[s] = combine(lv, sheet_lev(s))
            _stats["api"] += 1
            if lv is None:
                _stats["unknown"] += 1
            got += 1
        except Exception:
            _stats["err"] += 1
            bad += 1
            sv = sheet_lev(s)
            if sv is not None:
                with _lock:
                    _mem[s] = sv
        time.sleep(0.25)
    _save()
    if log:
        n1 = sum(1 for v in _mem.values() if v <= 1.0)
        log(f"leverage: {got} fetched, {bad} failed, {n1} of {len(_mem)} are 1x (Rule A)")
    return _stats


def lev_for(sym, day=None):
    """Effective leverage for sizing. Unknown -> 1x. Never raises."""
    if day:
        _load(day)
    v = _mem.get(sym)
    if v is None:
        sv = sheet_lev(sym)          # never fetched, but the sheet may know
        if sv is not None:
            _stats["sheet_only"] = _stats.get("sheet_only", 0) + 1
            return sv
        _stats["unknown"] += 1
        return SAFE_LEV
    _stats["hits"] += 1
    return float(v)


def notional_cap(sym, own_capital_per_slot, hard_cap, day=None):
    """Biggest position this slot may actually take in `sym`."""
    return min(float(hard_cap), float(own_capital_per_slot) * lev_for(sym, day))


def summary():
    n1 = sum(1 for v in _mem.values() if v <= 1.0)
    return {"day": _day, "cached": len(_mem), "one_x": n1,
            "five_x": len(_mem) - n1,
            "sheet_rows": len(_sheet or {}), **_stats}
