"""
engine.py - Agent 1 brain.

  * Dhan 1-min feed (live candles; token/clientid from env.txt)
  * name -> NSE ticker -> Dhan securityId resolver (Moneycontrol gives names)
  * indicator stack (indicators.py) + Ultimate-Master-Scalper labels (master_scalper.py)
  * Agent-1 score, the +17%/upper-circuit knockout, and freeze/shuffle ordering
  * a compact chart payload (last 30 min) for the UI

No Flask here so it stays unit-testable. app.py imports run_cycle().
"""
import csv, json, os, time, math, threading
from datetime import datetime, timezone, timedelta
import urllib.request, urllib.error

import Opus_indicators as I
from Opus_env_loader import load_env

try:
    import pandas as pd
    from master_scalper import compute_signals
    _HAVE_MS = True
except Exception:
    _HAVE_MS = False

HERE = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://api.dhan.co/v2"

WINDOW = 30            # bars shown in the UI preview (last 30 min)
PCT_CAP = 17.0         # drop names that have run >= this (or upper circuit)
DATA_PER_SEC = 4.5     # stay under Dhan Data 5/s
WIN_BARS = 15          # "recent activity" window = last 15 one-min bars
MIN_WIN_TURNOVER = 1_500_000   # >= ₹15L actually traded in the last 15 min (tradeable now)
MIN_WIN_RANGE    = 0.3         # last-15-min high-low range must be >= 0.3% (real movement)

# ---------------- credentials / headers ----------------
def _headers():
    e = load_env()
    return {"access-token": e["token"], "client-id": e["client_id"],
            "Content-Type": "application/json", "Accept": "application/json"}, e["client_id"]


_HEAD = None
def head():
    global _HEAD
    if _HEAD is None:
        _HEAD, _ = _headers()
    return _HEAD


# ---------------- rate-limited POST (with 429 back-off) ----------------
_last_call = [0.0]
_lock = threading.Lock()
# Request accounting. Dhan has warned this account about volume before, and
# nobody could say what the actual rate WAS -- only that it was "probably fine".
# Counted per endpoint so the answer is a number, not an opinion.
REQ_LOG = {"n": 0, "by_path": {}, "429": 0, "since": time.time()}


def req_stats(reset=False):
    import copy
    out = copy.deepcopy(REQ_LOG)
    out["elapsed"] = max(0.001, time.time() - REQ_LOG["since"])
    out["per_sec"] = round(out["n"] / out["elapsed"], 2)
    if reset:
        REQ_LOG.update({"n": 0, "by_path": {}, "429": 0, "since": time.time()})
    return out
_cooldown_until = [0.0]        # process-wide pause after a Dhan 429 (shared by all threads)
_MAX_429_TRIES = 3


def post(path, body):
    """Rate-limited Dhan POST. On HTTP 429 ("Too many requests") it does NOT hammer:
    it sets a process-wide cooldown so EVERY thread pauses, then retries with growing
    back-off. This is what stops a single 429 from spiralling into a sustained block
    (which blanks the scanner). Normal responses return immediately as before."""
    for attempt in range(_MAX_429_TRIES):
        with _lock:
            now = time.time()
            if now < _cooldown_until[0]:          # honour a recent 429 cooldown
                time.sleep(_cooldown_until[0] - now)
            dt = time.time() - _last_call[0]
            if dt < 1.0 / DATA_PER_SEC:
                time.sleep(1.0 / DATA_PER_SEC - dt)
            _last_call[0] = time.time()
            REQ_LOG["n"] += 1
            REQ_LOG["by_path"][path] = REQ_LOG["by_path"].get(path, 0) + 1
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers=head(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                REQ_LOG["429"] += 1
            if e.code == 429 and attempt < _MAX_429_TRIES - 1:
                wait = 3.0 * (attempt + 1)        # 3s, 6s back-off
                with _lock:
                    _cooldown_until[0] = max(_cooldown_until[0], time.time() + wait)
                time.sleep(wait)
                continue
            body_txt = ""
            try:
                body_txt = e.read().decode()[:160]
            except Exception:
                pass
            return {"_error": f"HTTP {e.code}", "_body": body_txt}
        except Exception as e:
            return {"_error": str(e)}
    return {"_error": "HTTP 429", "_body": "rate-limited after retries"}


# ================= resolver: name -> ticker -> securityId =================
class Resolver:
    def __init__(self):
        self.sym2sid = {}      # NSE ticker -> securityId
        self.name2sym = {}     # upper company name -> ticker
        self._load_universe()
        self._load_names()
        self.cache = {}
        self.unmapped = set()

    def _load_universe(self):
        p = os.path.join(HERE, "universe.csv")
        with open(p, newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                seg = (r.get("segment") or "").upper()
                if "NSE" in seg:
                    self.sym2sid[(r.get("symbol") or "").strip().upper()] = str(r.get("securityId") or "").strip()

    def _load_names(self):
        """Map company display-names -> NSE ticker via the Dhan instrument master."""
        p = os.path.join(HERE, "security_id_list.csv")
        if not os.path.exists(p):
            return
        with open(p, newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                if (r.get("SEM_EXM_EXCH_ID") or "").upper() != "NSE":
                    continue
                if (r.get("SEM_SERIES") or "").upper() not in ("EQ", "BE", ""):
                    continue
                tk = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
                if not tk:
                    continue
                for key in (r.get("SM_SYMBOL_NAME"), r.get("SEM_CUSTOM_SYMBOL")):
                    if key:
                        self.name2sym[_norm_name(key)] = tk

    def resolve(self, name):
        """Best-effort Moneycontrol display-name -> securityId."""
        if name in self.cache:
            return self.cache[name]
        sid = self._resolve(name)
        self.cache[name] = sid
        if not sid:
            self.unmapped.add(name)
        return sid

    def _resolve(self, name):
        up = name.strip().upper()
        # 1. maybe it's already a ticker
        if up in self.sym2sid:
            return self.sym2sid[up]
        nn = _norm_name(name)
        # 2. exact normalised company-name match
        tk = self.name2sym.get(nn)
        if tk and tk in self.sym2sid:
            return self.sym2sid[tk]
        # 3. prefix match (MC truncates names, e.g. "New India Assur")
        cands = [t for k, t in self.name2sym.items() if k.startswith(nn) or nn.startswith(k)]
        for t in cands:
            if t in self.sym2sid:
                return self.sym2sid[t]
        return None


def _norm_name(s):
    s = (s or "").upper()
    for junk in (" LTD", " LIMITED", " INDIA", "(INDIA)", " CORP", " CO.", "&", ".", ",", "'", "-"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


# ================= Dhan candle fetch =================
_daily_cache = {}
def _daily(sid):
    if sid in _daily_cache:
        return _daily_cache[sid]
    today = datetime.now(IST).strftime("%Y-%m-%d")
    frm = (datetime.now(IST) - timedelta(days=10)).strftime("%Y-%m-%d")
    d = post("/charts/historical", {"securityId": sid, "exchangeSegment": "NSE_EQ",
             "instrument": "EQUITY", "expiryCode": 0, "fromDate": frm, "toDate": today})
    prev = None
    if "_error" not in d:
        cl = [c for c in (d.get("close") or []) if c]
        if len(cl) >= 2:
            prev = cl[-2]
    _daily_cache[sid] = prev
    return prev


_KEYS = ("open", "high", "low", "close", "volume", "timestamp")


def fetch_candles(sid, lookback_days=7):
    """1-min candles for the MOST RECENT trading session.

    During market hours that session is today; on weekends/holidays/after-hours
    it's the last day that actually traded. We request a multi-day window and
    keep only the latest IST date present, so the board (and the 30-min preview)
    populate even when the market is closed.
    """
    end = datetime.now(IST)
    start = end - timedelta(days=lookback_days)
    d = post("/charts/intraday", {"securityId": sid, "exchangeSegment": "NSE_EQ",
             "instrument": "EQUITY", "interval": "1",
             "fromDate": start.strftime("%Y-%m-%d"), "toDate": end.strftime("%Y-%m-%d")})
    if "_error" in d:
        return None, d["_error"]
    full = {k: (d.get(k) or []) for k in _KEYS}
    ts = full["timestamp"]
    if len(full["close"]) < 20 or not ts:
        return None, f"few_bars({len(full['close'])})"
    # keep only the latest trading day's bars so indicators don't span sessions
    days = [datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d") for t in ts]
    last_day = days[-1]
    idx = [i for i, dd in enumerate(days) if dd == last_day]
    sess = {k: [full[k][i] for i in idx] for k in _KEYS}
    if len(sess["close"]) < 20:        # last day too short -> use the whole window
        sess = full
    return sess, None


# ================= per-symbol compute =================
def compute(name, sid, mc):
    """mc = {'pct','price','volume','lists'} from Moneycontrol. Returns a full row."""
    candles, err = fetch_candles(sid)
    if err:
        return {"name": name, "sid": sid, "error": err}
    o, h, l, c, v = (candles["open"], candles["high"], candles["low"],
                     candles["close"], candles["volume"])
    ts = candles["timestamp"]
    px = c[-1]
    a = I.atr(h, l, c) or 1e-9

    ema9 = I.ema_series(c, 9)
    sma12 = I.sma_series(c, 12)
    hull = I.hma_series(c, 55)
    sar = I.psar_series(h, l)
    mac = I.macd_series(c)
    ctop, cbot = I.ichimoku_series(h, l)
    rsi = I.rsi(c)
    vsma = I.sma(v, 12)
    vsurge = (v[-1] / vsma) if vsma else None
    ema_ang = I.angle_deg(ema9, a, 3)

    prev = _daily(sid)
    day_pct = mc.get("pct")
    if day_pct is None and prev:
        day_pct = round((px / prev - 1) * 100, 2)

    ema9_gt_sma = bool(ema9[-1] and sma12[-1] and ema9[-1] > sma12[-1])
    ema9_cross = bool(ema9[-2] and sma12[-2] and ema9[-2] <= sma12[-2] and ema9_gt_sma)
    macd_cross = bool(mac["line"][-1] is not None and mac["signal"][-1] is not None
                      and mac["line"][-2] is not None and mac["signal"][-2] is not None
                      and mac["line"][-2] <= mac["signal"][-2] and mac["line"][-1] > mac["signal"][-1])
    sar_below = bool(sar[-1] is not None and px > sar[-1])
    above_cloud = bool(ctop[-1] is not None and px > ctop[-1])
    hull_green = bool(hull[-1] is not None and hull[-2] is not None and hull[-1] > hull[-2])

    # angle sustainability: angle stayed >= 20 deg over the last 5 bars
    ang_hist = [I.angle_deg(ema9[:k + 1], a, 3) for k in range(len(ema9) - 5, len(ema9))]
    ang_sustained = all((x is not None and x >= 20) for x in ang_hist)

    # angle trend over the last 5 bars: rising = fresh ignition, falling = fading
    _va = [x for x in ang_hist if x is not None]
    ang_rising  = bool(len(_va) >= 2 and _va[-1] > _va[0])
    ang_falling = bool(len(_va) >= 2 and _va[-1] < _va[0])

    # sustaining: EMA-9 angle held > 30 deg over the last 3 bars (Trend bonus + freeze)
    _a3 = [x for x in ang_hist[-3:] if x is not None]
    ang_sust30  = bool(len(_a3) >= 3 and all(x > 30 for x in _a3))
    ang_nondecl = bool(len(_a3) >= 2 and _a3[-1] >= _a3[0])
    macd_line_pos = bool(mac["line"][-1] is not None and mac["line"][-1] > 0)

    # ---- recent tradeability: judge the LAST ~15 min, not the whole day ----
    # kills "dummy" names that printed a burst early then died, or are flat.
    _rv = v[-WIN_BARS:] if len(v) >= WIN_BARS else v
    _rh = h[-WIN_BARS:] if len(h) >= WIN_BARS else h
    _rl = l[-WIN_BARS:] if len(l) >= WIN_BARS else l
    active_ratio  = (sum(1 for x in _rv if x and x > 0) / len(_rv)) if _rv else 0.0
    win_vol       = sum(x for x in _rv if x)
    win_turnover  = win_vol * px                       # ₹ traded in the window
    win_range_pct = ((max(_rh) - min(_rl)) / px * 100) if (px and _rh and _rl) else 0.0
    stagnant = bool(active_ratio < 0.6                 # traded in <60% of recent minutes (sporadic)
                    or win_turnover < MIN_WIN_TURNOVER # too little ₹ changing hands now
                    or win_range_pct < MIN_WIN_RANGE)  # price essentially flat
    # "real activity now" — used to gate volume-RATIO signals so a tiny absolute
    # burst on a near-dead stock can't fake a surge / ignition / freeze-rescue.
    liquid_now = bool(active_ratio >= 0.6 and win_turnover >= MIN_WIN_TURNOVER)

    # ---- EARLY ignition: LEADING signals that precede the confirmed cross ----
    # The goal is to catch the surge as it *starts* (volume building while price
    # is still coiling), not after EMA/MACD confirm (which is already too late).
    _N = 6
    if len(h) > _N and len(l) > _N:
        base_hi   = max(h[-_N - 1:-1])                  # high of the small base (excl. current bar)
        base_lo   = min(l[-_N - 1:-1])
        breakout  = bool(px > base_hi)                  # current bar clears the base
        squeeze   = bool(px and (base_hi - base_lo) / px * 100 < 1.2)  # base was tight (coiled)
    else:
        breakout = squeeze = False
    vol_ig     = bool(vsurge is not None and vsurge >= 1.8)            # volume leading price
    slope_turn = bool(ema_ang is not None and 5 <= ema_ang <= 20 and ang_rising)  # slope just turning up
    # igniting = REAL activity AND volume surging AND (base-break OR slope turning up).
    # liquid_now guard kills the "6.88x on 203 shares" illusion from dead stocks.
    igniting   = bool(liquid_now and vol_ig and (breakout or slope_turn or squeeze))

    # ---- Ultimate Master Scalper labels ----
    labels = {"buy": [], "sell": [], "masterLong": [], "upTrend": [], "strongBuy": []}
    buy_active = master_long = False
    if _HAVE_MS:
        try:
            df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v})
            sig = compute_signals(df, window=WINDOW)
            if sig.get("ok"):
                buy_active = bool(sig["buy_active"])
                master_long = bool(sig["long_now"])
                # mark bar indices (in full series) where events fired
                be, ul, lt = sig["buy_event"], sig["ualgo_buy"], sig["long_trigger"]
                base = len(c) - len(be)  # compute_signals drops the forming bar
                for i in range(len(be)):
                    gi = base + i
                    # 'Buy' = the UAlgo trend flip (-1 -> +1). THIS is the green 'Buy'
                    # label on the TradingView chart. It was previously drawn from
                    # buy_event (= ualgo_buy AND long_trigger on the SAME bar), which
                    # almost never coincides -> the Buy marker never appeared.
                    if ul[i]:
                        labels["buy"].append(gi)
                    # buy_event kept as a rarer, stronger 'both aligned' marker.
                    if be[i]:
                        labels["strongBuy"].append(gi)
                    if lt[i]:
                        labels["masterLong"].append(gi)
        except Exception as e:
            labels["_err"] = str(e)

    sess_date = (datetime.fromtimestamp(ts[-1], IST).strftime("%Y-%m-%d")
                 if ts and ts[-1] else None)
    row = {
        "name": name, "sid": sid, "error": None,
        "time": datetime.now(IST).strftime("%H:%M:%S"), "session": sess_date,
        "price": round(px, 2), "day_pct": day_pct,
        "ema_angle": round(ema_ang, 1) if ema_ang is not None else None,
        "ema9_gt_sma12": ema9_gt_sma, "ema9_cross_up": ema9_cross,
        "macd_cross_up": macd_cross, "macd_hist": round(mac["hist"][-1], 4) if mac["hist"][-1] is not None else None,
        "sar_below": sar_below, "above_cloud": above_cloud, "hull_green": hull_green,
        "rsi": round(rsi, 1) if rsi else None,
        "vol": v[-1], "day_vol": mc.get("volume"),
        "vol_surge_x": round(vsurge, 2) if vsurge else None,
        "ang_sustained": ang_sustained,
        "ang_rising": ang_rising, "ang_falling": ang_falling,
        "ang_sust30": ang_sust30, "ang_nondecl": ang_nondecl, "macd_line_pos": macd_line_pos,
        "igniting": igniting, "breakout": breakout, "liquid_now": liquid_now,
        "active_ratio": round(active_ratio, 2), "stagnant": stagnant,
        "win_turnover": round(win_turnover), "win_range_pct": round(win_range_pct, 2),
        "buy_active": buy_active, "master_long": master_long,
        "lists": sorted(mc.get("lists", [])),
        "chart": _chart_payload(ts, o, h, l, c, v, ema9, sma12, hull, sar, ctop, cbot, mac, labels),
    }

    # ---- stage detection: FRESH (ideal entry) vs RUNNING vs EXTENDED (late) ----
    ang = row["ema_angle"]
    rsi_v = row["rsi"]
    # fresh = an ignition event (EMA or MACD just crossed up) while the angle is
    # still building (not yet parabolic) and not already overbought
    row["fresh"] = bool((ema9_cross or macd_cross)
                        and ang is not None and 10 <= ang <= 50
                        and not (rsi_v is not None and rsi_v > 75))
    # watch = gapped up / ran hard and now stalling: steep angle that's flattening,
    # or overbought — momentum may be fading, monitor before entry
    row["watch"] = bool((ang is not None and ang >= 50 and ang_falling)
                        or (rsi_v is not None and rsi_v > 78))
    if row["igniting"] and not row["watch"]:
        row["stage"] = "igniting"          # EARLY heads-up: surge just starting
    elif row["fresh"]:
        row["stage"] = "fresh"
    elif row["watch"] or (ang is not None and ang > 65):
        row["stage"] = "extended"
    else:
        row["stage"] = "running"

    row["sa"], row["sb"], row["sc"] = score_abc(row)
    row["score"] = round(row["sa"] + row["sb"] + row["sc"], 1)   # total (reference)
    return row


def _chart_payload(ts, o, h, l, c, v, ema9, sma12, hull, sar, ctop, cbot, mac, labels):
    n = len(c)
    s = max(0, n - WINDOW)

    def w(arr):
        return [None if (i >= len(arr) or arr[i] is None) else round(arr[i], 2) for i in range(s, n)]

    tlabels = []
    tsec = []          # epoch seconds -- Lightweight Charts needs a NUMBER, not "HH:MM"
    for i in range(s, n):
        if i < len(ts) and ts[i]:
            tlabels.append(datetime.fromtimestamp(ts[i], IST).strftime("%H:%M"))
            tsec.append(int(ts[i]))
        else:
            tlabels.append("")
            tsec.append(None)
    # guarantee strictly-ascending unique timestamps (the charting lib rejects dupes/out-of-order)
    _last = None
    for i, v_ in enumerate(tsec):
        if v_ is None:
            continue
        if _last is not None and v_ <= _last:
            v_ = _last + 60
            tsec[i] = v_
        _last = v_
    mk = {k: [gi - s for gi in idxs if isinstance(gi, int) and s <= gi < n]
          for k, idxs in labels.items() if isinstance(idxs, list)}
    return {
        "t": tlabels, "tsec": tsec,
        "o": w(o), "h": w(h), "l": w(l), "c": w(c), "v": [v[i] for i in range(s, n)],
        "ema9": w(ema9), "sma12": w(sma12), "hull": w(hull), "sar": w(sar),
        "cloudTop": w(ctop), "cloudBot": w(cbot),
        "macdLine": [None if mac["line"][i] is None else round(mac["line"][i], 4) for i in range(s, n)],
        "macdSignal": [None if mac["signal"][i] is None else round(mac["signal"][i], 4) for i in range(s, n)],
        "macdHist": [None if mac["hist"][i] is None else round(mac["hist"][i], 4) for i in range(s, n)],
        "markers": mk,
    }


# ================= Agent-1 three scores (A=Trend, B=Trigger, C=Momentum) =======
def score_abc(r):
    """Return (A, B, C) each on a 0-10 scale.
       A = Trend (angle quality + sustaining), B = Trigger (fresh ignition),
       C = Momentum (confirmed strength)."""
    ang = r.get("ema_angle")

    # ---- A: Trend ----
    a = 0.0
    if ang is not None:
        if 30 <= ang <= 45:   a += 6     # ideal launch zone
        elif 20 <= ang < 30:  a += 4     # building
        elif 45 < ang <= 60:  a += 4     # strong but hotter
        elif 10 <= ang < 20:  a += 2     # early
    if r.get("ang_sust30"):   a += 4     # angle held > 30 deg over 3 bars
    a = min(10.0, a)

    # ---- B: Trigger (just igniting → entry timing) ----
    b = 0.0
    if r.get("igniting"):      b += 5    # EARLY leading signal (volume + base-break/slope-turn)
    if r.get("fresh"):         b += 5
    if r.get("ema9_cross_up"): b += 2    # EMA-9 just crossed above SMA-12
    if r.get("macd_cross_up"): b += 2    # MACD just crossed signal
    if r.get("ang_rising"):    b += 1
    b = min(10.0, b)

    # ---- C: Momentum (confirmed strength) ----
    c = 0.0
    if r.get("macd_cross_up") and r.get("macd_line_pos"):
        c += 4                            # priority combo: MACD crossed AND line > 0
    elif r.get("macd_line_pos"):
        c += 2
    if r.get("ema9_gt_sma12"): c += 2     # EMA-9 holding above SMA-12
    vs = r.get("vol_surge_x") or 0
    if r.get("liquid_now"):                # surge only counts if activity is real
        if vs >= 1.5:   c += 3             # strong minute-volume surge
        elif vs >= 1.0: c += 1
    if (r.get("macd_hist") or 0) > 0: c += 1
    c = min(10.0, c)

    return round(a, 1), round(b, 1), round(c, 1)


def passes_filters(r):
    if r.get("error"):
        return False
    if r.get("stagnant"):                      # dashy / illiquid 1-min chart
        return False
    p = r.get("day_pct")
    if p is not None and p >= PCT_CAP:        # ran too far / upper circuit
        return False
    ang = r.get("ema_angle")
    if ang is not None and ang < 0:           # EMA-9 sloping DOWN → not a long setup
        return False
    return True


# ================= freeze / shuffle manager =================
class Agent1:
    """Rolling state so sustaining leaders FREEZE (pin on top) while the rest
    shuffle by Trigger→Trend→Momentum. A stock freezes once its EMA-9 angle has
    stayed positive & non-declining (or day % kept rising) for >= 2 cycles, and
    un-freezes the moment the angle turns negative."""
    FREEZE_MIN_CYCLES = 2
    FROZEN_MIN_TURNOVER = 30_000_000  # frozen names need >= ₹3 Cr day TURNOVER (price*vol,
                                      # price-neutral)...
    FROZEN_VOL_RESCUE_X = 3.0         # ...unless the minute-volume surge is >= 3x (relative)

    def __init__(self):
        self.state = {}    # name -> {"frozen":bool,"cycles":int,"last_pct":float}

    def rank(self, rows, keep_all=False):
        if keep_all:
            live = [r for r in rows if not r.get("error") and not r.get("stagnant")]
        else:
            live = [r for r in rows if passes_filters(r)]
        if keep_all:
            # preview / after-hours: static snapshot → freezing is meaningless.
            for r in live:
                r["frozen"] = False
            # total quality first (A+B+C), Trigger as tiebreaker
            live.sort(key=lambda r: (-(r.get("score") or 0), -(r.get("sb") or 0)))
            return live
        now = set()
        for r in live:
            nm = r["name"]; now.add(nm)
            st = self.state.setdefault(nm, {"frozen": False, "cycles": 0, "last_pct": None})
            ang = r.get("ema_angle")
            pct = r.get("day_pct")
            ang_ok = (ang is not None and ang > 0 and r.get("ang_nondecl"))
            pct_rising = (pct is not None and st["last_pct"] is not None and pct > st["last_pct"])
            pct_dropping = (pct is not None and st["last_pct"] is not None and pct < st["last_pct"])
            # "down" = losing ground this cycle (angle gone/negative, rolling over, or % slipping)
            down = bool(ang is None or ang <= 0 or r.get("ang_falling") or pct_dropping)
            sustaining = ang_ok or pct_rising
            # require TWO consecutive down cycles before releasing — a single-bar
            # wobble no longer un-freezes a sustained leader (stops the flip-flop).
            st["down"] = st.get("down", 0) + 1 if down else 0
            if st["down"] >= 2:
                st["frozen"] = False; st["cycles"] = 0
            elif sustaining:
                st["cycles"] += 1
                if st["cycles"] >= self.FREEZE_MIN_CYCLES:
                    st["frozen"] = True
            # else: a single down/flat cycle → hold the current frozen state
            st["last_pct"] = pct
            r["frozen"] = st["frozen"]
            # frozen list must be liquid: drop a frozen name whose DAY TURNOVER
            # (price*vol — price-neutral) is under the floor, but rescue it back
            # if its minute-volume surge is >= 3x (relative, also price-neutral).
            if r["frozen"]:
                dv = r.get("day_vol"); px = r.get("price")
                turn = (dv * px) if (dv is not None and px is not None) else None
                vsx = r.get("vol_surge_x") or 0
                # rescue a low-turnover frozen name ONLY if its surge is real
                # (>=3x AND backed by genuine recent activity) — not a dead-stock blip.
                rescued = (vsx >= self.FROZEN_VOL_RESCUE_X and r.get("liquid_now"))
                if turn is not None and turn < self.FROZEN_MIN_TURNOVER and not rescued:
                    r["frozen"] = False
                    st["frozen"] = False; st["cycles"] = 0
        for k in [k for k in self.state if k not in now]:
            self.state.pop(k, None)
        # FROZEN first, then by TOTAL quality (A+B+C), Trigger(B) as tiebreaker,
        # then volume — so strong steady names hold their place instead of being
        # buried when a one-bar Trigger lapses.
        live.sort(key=lambda r: (not r.get("frozen"),
                                 -(r.get("score") or 0),
                                 -(r.get("sb") or 0),
                                 -(r.get("vol") or 0)))
        return live
