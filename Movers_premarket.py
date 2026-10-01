"""
Movers_premarket.py -- PRE-MARKET news severity + forward validation.

THE PROBLEM
    Before 09:00 there is no price, no volume and no order book, so the "impact"
    measure used during the session (how far the stock actually moved) does not
    exist yet. Severity has to be inferred from the news itself, and then
    confirmed by the pre-open auction once it starts at 09:00.

WHAT THIS DOES
    Phase 1  score each overnight headline on features that ARE observable:
               * magnitude stated in the text  ("profit surges 396%", "Rs 500 Cr
                 order") -- the single biggest differentiator between a Savita
                 Oil and a routine "profit up YoY"
               * event class (results beat > order win > approval > AGM filing)
               * Dhan's sentiment
               * company size proxy -- the same news moves a small-cap far more
             then, from 09:00, ENRICH with the pre-open indicative gap and the
             buy/sell order imbalance, which is the market's first real vote.

    Phase 2  every pre-market call is written to disk WITH the actual outcome
             (open gap and the first 15 minutes) so a MEASURED hit-rate builds
             up over sessions. Until enough sessions exist, no probability is
             shown -- a number invented from 2 days of data would be worthless.

HONEST LIMITS
    * Dhan's news API only reaches back ~2 days, so this cannot be back-tested
      historically; validation accumulates forward from today.
    * Before 09:00 the score is an INFERENCE from text. It is not a forecast.
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta

import Opus_engine as engine
import Opus_quotes_v3 as q

try:
    import Movers_context as ctx          # run-up (Dhan) + cached fundamentals
except Exception:                          # pragma: no cover
    ctx = None

try:
    import Movers_dhannews as dhannews    # NSE-only filter lives here
except Exception:                          # pragma: no cover
    dhannews = None

IST = engine.IST
HERE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(HERE, "logs", "movers_board")

# ---- event classes, weighted by how hard they actually gap a stock ----------
# Tuned for SCALPING: what produces a violent first-5-minute move, not what is
# fundamentally important. Results and single-event catalysts (approvals, big
# orders, M&A) gap stocks; governance filings do not.
EVENTS = [
    (("q1", "q2", "q3", "q4", "quarterly", "result", "earnings", "pat ", "net profit",
      "profit", "revenue", "ebitda", "margin", "topline", "bottomline"), 34, "results"),
    (("usfda", "us fda", "anda", "cdsco", "ema approval", "drug approval",
      "tentative approval", "final approval", "orphan drug", "fast track"), 32, "drug approval"),
    (("order", "contract", "bags", "awarded", "wins ", "won ", "loi",
      "letter of intent", "tender", "work order"), 30, "order win"),
    (("acquisition", "acquire", "acquires", "merger", "merges", "amalgamat",
      "takeover", "buys stake", "stake sale", "divest"), 30, "M&A"),
    (("block deal", "bulk deal", "promoter buy", "promoter sell", "pledge"), 24, "block/stake"),
    (("upgrade", "downgrade", "target price", "initiate coverage", "rerate",
      "brokerage"), 24, "rating"),
    (("patent", "licence", "license", "approval", "clearance", "certification"), 22, "approval"),
    (("guidance", "outlook", "forecast", "capex plan"), 20, "guidance"),
    (("fund rais", "qip", "preferential", "warrant", "rights issue", "fpo"), 18, "fund raise"),
    (("capacity", "expansion", "new plant", "commission", "first production",
      "commercial production", "produces first", "greenfield", "brownfield"), 22, "expansion"),
    (("buyback", "bonus", "split", "special dividend"), 18, "capital action"),
    (("dividend", "record date"), 8, "dividend"),
    (("resign", "appoint", "ceo", "cfo", "managing director"), 10, "management"),
    (("agm", "egm", "board meeting", "intimation", "disclosure", "regulation 30",
      "trading window", "newspaper publication", "compliance", "scrutinizer"), 1, "routine filing"),
]

# ---- financial parameters traders actually price in --------------------------
# A results headline is not one signal -- PAT, EBITDA, margin and revenue each
# move the stock, and the DIRECTION of each matters.
_PARAMS = {
    "pat": ("pat", "net profit", "profit after tax", "bottomline"),
    "ebitda": ("ebitda", "operating profit", "oper profit"),
    "margin": ("margin", "opm"),
    "revenue": ("revenue", "topline", "sales", "turnover"),
    "order_book": ("order book", "orderbook"),
}
_UP_WORDS = ("surge", "surges", "jump", "jumps", "soar", "soars", "rise", "rises",
             "up ", "grows", "growth", "doubl", "tripl", "beat", "beats", "record",
             "highest", "multi-fold", "expand", "improves", "turnaround", "profit vs loss")
_DN_WORDS = ("fall", "falls", "drop", "drops", "decline", "declines", "slump", "slumps",
             "plunge", "plunges", "down ", "miss", "misses", "loss", "narrows",
             "contract", "shrink", "weak", "de-growth", "degrowth")

_PCT = re.compile(r"(\d{1,4}(?:\.\d+)?)\s*(?:%|per\s?cent)", re.I)
_AMT = re.compile(r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)\s*(cr|crore|lakh|bn|billion|mn|million)", re.I)
_MULT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:x|fold|times)", re.I)


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def extract_magnitude(text):
    """Biggest percentage / multiple / rupee amount stated in the headline.

    'Net profit surges 396% YoY' -> pct 396. 'Profit up YoY' -> nothing, and that
    absence is itself informative: vague news rarely gaps a stock hard.
    """
    t = text or ""
    pcts = [v for v in (_num(m) for m in _PCT.findall(t)) if v is not None]
    mults = [v for v in (_num(m) for m in _MULT.findall(t)) if v is not None]
    amt_cr = None
    for val, unit in _AMT.findall(t):
        v = _num(val)
        if v is None:
            continue
        u = unit.lower()
        cr = v if u.startswith("cr") else (v / 100.0 if u.startswith("lakh")
                                           else (v * 100.0 if u.startswith(("bn", "bill"))
                                                 else v / 10.0))
        amt_cr = cr if amt_cr is None else max(amt_cr, cr)
    return {"pct": max(pcts) if pcts else None,
            "mult": max(mults) if mults else None,
            "amount_cr": amt_cr}


def classify(text):
    t = (text or "").lower()
    best, label = 0, "other"
    for kws, w, lab in EVENTS:
        if any(k in t for k in kws):
            if w > best:
                best, label = w, lab
    return best, label


def extract_params(text):
    """Which financial parameters are mentioned, and in which direction.

    'PAT surges 396%, EBITDA margin expands' -> two positive parameters. Traders
    price each of these separately, so several agreeing lines is a far stronger
    setup than one vague 'profit up'.
    """
    t = (text or "").lower()
    found = {}
    for key, kws in _PARAMS.items():
        pos = -1
        for k in kws:
            i = t.find(k)
            if i >= 0:
                pos = i
                break
        if pos < 0:
            continue
        window = t[max(0, pos - 45): pos + 90]     # look around the mention
        up = any(w in window for w in _UP_WORDS)
        dn = any(w in window for w in _DN_WORDS)
        found[key] = "up" if (up and not dn) else ("down" if (dn and not up) else "mixed")
    return found


_TITLE_METRIC = re.compile(
    r"(net\s+profit|profit\s+after\s+tax|\bpat\b|\bprofit\b|\bebitda\b|operating\s+profit"
    r"|\brevenue\b|\bsales\b|\btopline\b|\bmargin\b|\bincome\b|\bloss\b)", re.I)
# Verbs that state which way that metric went. Ordered longest-first so that
# "loss narrows" is not shredded into "loss" + "narrows" and mis-signed.
_TITLE_UP = re.compile(
    r"(turns?\s+positive|swings?\s+to\s+profit|back\s+in\s+(the\s+)?black|loss\s+narrow\w*"
    r"|nearly\s+doubl\w*|more\s+than\s+doubl\w*|doubl\w*|tripl\w*|quadrupl\w*|multi-?fold"
    r"|surge\w*|soar\w*|jump\w*|zoom\w*|spike\w*|rocket\w*|climb\w*|rebound\w*|recover\w*"
    r"|rise\w*|rises|grow\w*|gain\w*|improv\w*|expand\w*|beat\w*|record\b|highest\b"
    r"|\bup\b|\bup[s]?\s|\bhigher\b)", re.I)
_TITLE_DOWN = re.compile(
    r"(swings?\s+to\s+(a\s+)?loss|slips?\s+into\s+(the\s+)?red|loss\s+widen\w*|widen\w*\s+loss"
    r"|turns?\s+negative|plunge\w*|slump\w*|tank\w*|crash\w*|slide\w*|slid\b|sink\w*"
    r"|tumbl\w*|drop\w*|fall\w*|fell\b|declin\w*|dip\w*|shrink\w*|contract\w*|erod\w*"
    r"|miss\w*|weak\w*|de-?growth|\bdown\b|\blower\b|halve\w*)", re.I)


_TURN_UP = re.compile(
    r"(swings?\s+to\s+(?:a\s+)?[^,;]{0,25}?profit|turns?\s+profitable|turns?\s+positive"
    r"|back\s+in\s+(?:the\s+)?black|profit\s+vs\s+loss|loss\s+to\s+profit"
    r"|returns?\s+to\s+profit)", re.I)
_TURN_DOWN = re.compile(
    r"(swings?\s+to\s+(?:a\s+)?[^,;]{0,25}?loss|slips?\s+into\s+(?:the\s+)?red"
    r"|turns?\s+negative|profit\s+to\s+loss|posts?\s+(?:a\s+)?loss\s+vs\s+profit)", re.I)
_LOSS_SHRINK = re.compile(r"(narrow\w*|shrink\w*|reduc\w*|declin\w*|lower\w*|halve\w*"
                          r"|turns?\s+positive|swings?\s+to\s+profit|contract\w*)", re.I)
_LOSS_GROW = re.compile(r"(widen\w*|mount\w*|deepen\w*|balloon\w*|surge\w*|jump\w*"
                        r"|rise\w*|grow\w*|expand\w*|doubl\w*|tripl\w*)", re.I)


def title_direction(title):
    """Direction stated EXPLICITLY in the headline, or None.

    WHY THIS EXISTS
        Six calls across the recorded sessions were called DOWN on headlines
        that plainly said the opposite -- "EBITDA surges 70%", "Net Profit Up
        47%", "Revenue up 35%", "profit turns positive", "Net Profit Nearly
        Doubled". All six then went UP: a 100% failure rate on the single
        easiest category there is.

        Two causes, both fixed here:
          1. Dhan's `sentiment` tag was allowed to outvote the title. It is
             wrong often enough that it cannot be the senior signal.
          2. extract_params() scans a +/-45..90 character window around each
             metric, so a negative word ANYWHERE near it flipped the sign --
             "Revenue up 35% YoY, profit down" style bodies poisoned the title.

        This function reads the TITLE ONLY, pairs each metric with the nearest
        following verb, and returns a direction only when the title is
        unambiguous. Ambiguous titles return None and fall through to the older,
        softer evidence rather than guessing.
    """
    t = (title or "").strip()
    if not t:
        return None
    # TURNAROUND PHRASES FIRST.
    #   "swings to Rs 29 cr profit from Rs 68 cr loss" puts the verb several
    #   words from the metric, so the metric-by-metric loop below finds no verb
    #   beside either noun and returns no call. These are the clearest signals
    #   in the whole feed, so they are matched whole and short-circuit.
    if _TURN_UP.search(t):
        return "UP"
    if _TURN_DOWN.search(t):
        return "DOWN"
    ups = dns = 0
    for m in _TITLE_METRIC.finditer(t):
        # look forward from the metric to the next comma/semicolon/end -- the
        # clause that actually describes it
        seg = t[m.end(): m.end() + 60]
        seg = re.split(r"[,;|]", seg)[0]
        u, d = bool(_TITLE_UP.search(seg)), bool(_TITLE_DOWN.search(seg))
        if not (u or d):
            # verb may sit BEFORE the metric: "Profit Slides", "surges to"
            pre = t[max(0, m.start() - 28): m.start()]
            u, d = bool(_TITLE_UP.search(pre)), bool(_TITLE_DOWN.search(pre))
        # LOSS INVERTS. A loss that shrinks is good news and a loss that grows
        # is bad, which is the opposite sign to every other metric here. The
        # first version tried to patch this inline and silently dropped
        # "Loss Narrows to Rs 57M" to no-call.
        if m.group(0).lower().strip() == "loss":
            if _LOSS_SHRINK.search(seg) or _LOSS_SHRINK.search(t[:m.start()]):
                ups += 1
            elif _LOSS_GROW.search(seg) or _LOSS_GROW.search(t[:m.start()]):
                dns += 1
            continue
        if u and not d:
            ups += 1
        elif d and not u:
            dns += 1
    if ups and not dns:
        return "UP"
    if dns and not ups:
        return "DOWN"
    return None


def severity(item, size_bucket="mid"):
    """0-100 severity from the news TEXT alone (pre-09:00). Returns (score, parts)."""
    text = f"{item.get('title','')} {item.get('text','')}"
    ev, ev_label = classify(text)
    mag = extract_magnitude(text)
    params = extract_params(text)
    # magnitude points -- a stated big number is the strongest textual signal
    mp = 0
    if mag["pct"] is not None:
        p = mag["pct"]
        mp = max(mp, 40 if p >= 100 else 32 if p >= 50 else 22 if p >= 25 else
                 14 if p >= 10 else 6)
    if mag["mult"] is not None and mag["mult"] >= 2:
        mp = max(mp, 32)
    if mag["amount_cr"] is not None:
        a = mag["amount_cr"]
        mp = max(mp, 32 if a >= 1000 else 24 if a >= 250 else 15 if a >= 50 else 6)
    # each financial parameter that moves in a consistent direction adds weight
    agree = sum(1 for v in params.values() if v in ("up", "down"))
    pp = min(18, agree * 7)
    sent = item.get("sentiment")
    sp = 8 if sent in ("positive", "negative") else 0
    # the same news moves a small company far more than a large one
    size_bonus = {"small": 14, "mid": 6, "large": 0}.get(size_bucket, 6)
    score = min(100, ev + mp + pp + sp + size_bonus)
    return score, {"event": ev_label, "event_pts": ev, "magnitude": mag,
                   "mag_pts": mp, "params": params, "param_pts": pp,
                   "sent_pts": sp, "size": size_bucket, "size_pts": size_bonus}


def direction_and_chance(item, parts, pre_gap=None, pre_imb=None, pre_trend=None):
    """Directional call + a CHANCE score for the first minutes after the open.

    Built from converging evidence, weighted by how much each source is worth:
        pre-open auction gap   -- the market voting with real orders (strongest)
        pre-open order imbalance
        financial parameters all pointing the same way
        stated magnitude
        event class + sentiment
    Returned as a 0-100 'chance', NOT a historical frequency: it is a confidence
    that the move happens in this direction, and the components are shown so the
    number is never a black box.
    """
    sent = item.get("sentiment")
    params = parts.get("params") or {}
    ups = sum(1 for v in params.values() if v == "up")
    dns = sum(1 for v in params.values() if v == "down")
    # ---- DIRECTION: text only ------------------------------------------
    # The auction no longer votes here. It used to add +/-2.5 and dominate the
    # call, which meant the "prediction" was largely a restatement of where the
    # stock was already going to open -- something you cannot trade.
    # THE TITLE WINS.
    #   When the headline states plainly which way a metric went, that is the
    #   call -- Dhan's sentiment tag and body-window word matching are both
    #   demonstrably worse and were overriding it (6 of 6 such cases wrong).
    td = title_direction(item.get("title"))
    if td:
        direction = td
    else:
        bias = 0.0
        if sent == "positive":
            bias += 1.0
        elif sent == "negative":
            bias -= 1.0
        bias += (ups - dns) * 0.8
        direction = "UP" if bias > 0 else ("DOWN" if bias < 0 else "UNCLEAR")

    # ---- CHANCE: text only, and CALIBRATED TO MEASURED OUTCOMES ---------
    # Everything here is knowable from the headline the night before, so the
    # number is final well before 09:08 and never jumps when the auction opens.
    #
    # THE WEIGHTS ARE MEASURED, NOT INVENTED. Fitted on 123 scored calls across
    # 6 sessions, scoring the TRADEABLE (open->close) move, base rate 54%:
    #
    #     stated magnitude >= 100%   48%   (-6)   <- big headline %, WORSE
    #     stated magnitude 1-29%     62%   (+7)
    #     rupee scale >= Rs 1000cr   61%   (+6)
    #     rupee scale Rs 100-999cr   59%   (+4)
    #     rupee scale < Rs 100cr     48%   (-6)
    #     called UP                  57%   (+3)
    #     called DOWN                47%   (-7)
    #     size = mid                 47%   (-7)
    #     SEVERITY 90-100 / 70-89 / <70 -> 55% / 54% / 55%   NO DISCRIMINATION
    #
    # Two consequences worth stating plainly:
    #   * Severity is deliberately almost absent from chance. It measures how
    #     BIG the news is, and on this data that has no relationship with which
    #     way the stock then trades. Keeping it at 30 points was manufacturing
    #     confidence out of a variable with zero measured signal.
    #   * The range is capped near 35-72, not 0-95. The best-performing bucket
    #     ever observed was 62%. A 95 would be a claim the evidence cannot
    #     support, and the old scale's 92s were exactly that.
    # CHANCE IS A MEASURED FREQUENCY, NOT A MODEL.
    #   Every attempt to find a text feature that predicts the tradeable move
    #   failed on this data (see calibration()). Rather than dress up a fitted
    #   guess as a probability, report the OBSERVED hit-rate for the bucket this
    #   call falls into, from the user's own accumulated outcomes.
    #
    #   Until enough calls have been scored, there is no honest number to give,
    #   so it returns the base rate with a wide interval and says so. That is
    #   the whole point: a 92% that is really 54% is worse than no number.
    cal = calibration()
    b = cal["buckets"].get(direction)
    if b:
        p, lo, hi, nn = b["p"], b["lo"], b["hi"], b["n"]
    else:
        p, lo, hi, nn = cal["base"], cal["lo"], cal["hi"], cal["n"]
    chance = int(round(p * 100)) if nn else 50
    # Clamp to a range the evidence can support. The best bucket ever observed
    # was 62%; printing 90 would be a claim about precision, not probability.
    chance = max(35, min(70, chance))
    return direction, chance, {"n": nn, "lo": int(round(lo * 100)),
                               "hi": int(round(hi * 100)),
                               "basis": ("measured: %s calls" % nn) if nn else "no data yet"}


def confirmation(direction, pre_gap=None, pre_imb=None, pre_trend=None):
    """The pre-open auction's OPINION -- reported separately, never folded into
    chance.

    Measured reason for the split: adding the auction lifted the day-% hit rate
    from 78% to 93%, but the TRADEABLE hit rate only from 53% to 55%. It was
    predicting the opening gap -- the part of the move that happens before you
    can act -- and then taking credit for it.

    Returns (label, detail) for display next to the call.
    """
    if pre_gap is None:
        return None, None
    agrees = (pre_gap > 0) == (direction == "UP")
    strong = abs(pre_gap) >= 2
    if direction == "UNCLEAR":
        return "NO CALL", f"auction {pre_gap:+.2f}%"
    lbl = ("CONFIRMS" if agrees and strong else
           "confirms" if agrees else
           "CONTRADICTS" if strong else "contradicts")
    bits = [f"auction {pre_gap:+.2f}%"]
    if pre_imb is not None:
        try:
            bits.append(f"imb {float(pre_imb):.2f}")
        except (TypeError, ValueError):
            pass
    if pre_trend:
        bits.append(str(pre_trend))
    return lbl, " · ".join(bits)


def size_buckets(sids, log=lambda m: None):
    """Rough company-size proxy from the last session's traded value."""
    out = {}
    sids = [str(s) for s in sids if str(s).isdigit()]
    if not sids:
        return out
    try:
        quotes = q._quote_all(sids, lambda m: None)
    except Exception as e:
        log(f"premarket: size quote failed {e}")
        return out
    for sid, qq in (quotes or {}).items():
        try:
            lp = float((qq or {}).get("last_price") or 0)
            vol = float((qq or {}).get("volume") or 0)
            tov = lp * vol
            out[str(sid)] = ("large" if tov >= 5e8 else
                             "mid" if tov >= 5e7 else "small")
        except (TypeError, ValueError):
            continue
    return out


def build(news_items, preopen_rows=None, log=lambda m: None):
    """Ranked pre-market watchlist. `preopen_rows` (09:00+) enriches each row
    with the indicative gap and order-book imbalance when available."""
    news_items = news_items or []
    # NSE-ONLY FILTER REMOVED -- it blanked this tab. See Movers_dhannews.only_nse.
    pre = {}
    for r in (preopen_rows or []):
        s = (r.get("sym") or "").upper()
        if s:
            pre[s] = r
    sizes = size_buckets([n.get("nse") for n in news_items], log)
    # ---- PRIOR-STATE CONTEXT -------------------------------------------
    # Fetched for the names with the biggest headlines first, because the
    # per-cycle request budget is small and a stock nobody will look at does
    # not deserve one of them. Cached per day, so this converges over the
    # morning rather than blocking any single cycle.
    rup = {}
    if ctx is not None:
        try:
            ranked = sorted(news_items,
                            key=lambda n: -(severity(n, sizes.get(str(n.get("nse") or ""), "mid"))[0]))
            rup = ctx.runup([n.get("nse") for n in ranked if n.get("nse")], log=log)
        except Exception as e:
            log(f"premarket: context unavailable {type(e).__name__} {str(e)[:80]}")
    rows = []
    for n in news_items:
        sid = str(n.get("nse") or "")
        sc, parts = severity(n, sizes.get(sid, "mid"))
        parts["_severity"] = sc
        sym = (n.get("sym") or "").upper()
        p = pre.get(sym)
        direction, chance, cbasis = direction_and_chance(n, parts)
        # The auction is reported ALONGSIDE the call, never inside it, so the
        # chance printed at 08:00 is the same number you see at 09:10.
        conf_lbl, conf_detail = confirmation(
            direction, (p or {}).get("pct"), (p or {}).get("imb"), (p or {}).get("trend"))
        row = {
            "direction": direction, "chance": chance,
            "chance_n": cbasis["n"], "chance_lo": cbasis["lo"],
            "chance_hi": cbasis["hi"], "chance_basis": cbasis["basis"],
            "confirm": conf_lbl, "confirm_detail": conf_detail,
            "params": parts.get("params") or {},
            "sym": sym, "sid": sid, "name": n.get("name"),
            "when": n.get("when"), "ts": n.get("ts"),
            "title": n.get("title"), "sentiment": n.get("sentiment"),
            "severity": sc, "event": parts["event"], "size": parts["size"],
            "mag_pct": parts["magnitude"]["pct"],
            "mag_amount_cr": parts["magnitude"]["amount_cr"],
            "parts": parts,
            # pre-open confirmation (only exists from 09:00)
            "pre_gap": (p or {}).get("pct"),
            "pre_imb": (p or {}).get("imb"),
            "pre_verdict": (p or {}).get("verdict"),
            "pre_trend": (p or {}).get("trend"),
        }
        # ---- context: recorded on every call, NOT folded into chance -------
        # It has never been validated (the board's logs only cover stocks that
        # were already moving, so a backtest had n=8). calibration() registers
        # these as buckets and will measure them forward; they enter the score
        # only once >= 25 outcomes exist. Building it the other way round is
        # what produced the 92% figures that were really 54%.
        r = rup.get(str(sid)) or {}
        row.update({
            "runup_3d": r.get("runup_3d"), "runup_5d": r.get("runup_5d"),
            "runup_10d": r.get("runup_10d"), "atr_pct": r.get("atr_pct"),
            "pos_20d": r.get("pos_20d"), "vol_x": r.get("vol_x"),
        })
        if ctx is not None:
            try:
                mc = ctx.margin_context(sym)
            except Exception:
                mc = None
            if mc:
                row.update({"opm_now": mc.get("opm_now"), "opm_lo": mc.get("opm_lo"),
                            "opm_hi": mc.get("opm_hi"), "opm_pos_pct": mc.get("opm_pos_pct"),
                            "sales_yoy": mc.get("sales_yoy"),
                            "inflection": mc.get("inflection"), "sector": mc.get("sector")})
        # confirmed = text severity AND the auction agreeing in the same direction
        conf = None
        if row["pre_gap"] is not None and n.get("sentiment") in ("positive", "negative"):
            up_news = n.get("sentiment") == "positive"
            up_book = row["pre_gap"] > 0
            conf = bool(up_news == up_book)
        row["confirmed"] = conf
        rows.append(row)
    # ranked by CHANCE -- what is most likely to move in the first minutes
    # SEVERITY LEADS THE SORT NOW, not chance.
    #   Chance is a measured frequency and takes only two or three distinct
    #   values (UP ~57, DOWN ~47, UNCLEAR ~54), so sorting on it first would
    #   group every bullish call together and then order them arbitrarily.
    #   Severity still differentiates one headline from another, so it decides
    #   the reading order; chance tells you how much to trust the call.
    rows.sort(key=lambda r: (-(r.get("severity") or 0), -(r.get("chance") or 0),
                             -abs(r.get("pre_gap") or 0), -(r.get("ts") or 0)))
    return rows


# ---------------- Phase 2: forward validation ----------------
_calls_lock = threading.Lock()


def record_calls(rows, day=None):
    """Persist today's pre-market calls so the outcome can be measured later."""
    day = day or datetime.now(IST).strftime("%Y%m%d")
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        path = os.path.join(LOGDIR, f"premarket_calls_{day}.json")
        payload = {"day": day, "saved": datetime.now(IST).isoformat(),
                   "calls": [{k: r.get(k) for k in
                              ("sym", "sid", "severity", "chance", "direction", "event",
                               "size", "sentiment", "mag_pct", "mag_amount_cr",
                               "pre_gap", "pre_imb", "confirmed", "title", "when",
                               "confirm", "confirm_detail", "chance_n",
                               "chance_lo", "chance_hi", "chance_basis",
                               "runup_3d", "runup_5d", "runup_10d", "atr_pct",
                               "pos_20d", "vol_x", "opm_now", "opm_pos_pct",
                               "sales_yoy", "inflection", "sector")}
                             for r in rows[:60]]}
        with _calls_lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str)
            os.replace(tmp, path)
    except Exception:
        pass


def score_scalp_outcomes(day=None, log=lambda m: None):
    """Measure each call over the SCALPING horizon: the first 2 and 5 minutes
    after the open, plus the best move available inside 5 minutes.

    This is deliberately not an end-of-day measure. A 2-5 minute trade lives or
    dies in the opening minutes, and measuring there also produces ~30 samples
    per session instead of one, so the hit-rate becomes meaningful in days rather
    than weeks.
    """
    import Opus_candle_v3 as candle_v3
    day = day or datetime.now(IST).strftime("%Y%m%d")
    path = os.path.join(LOGDIR, f"premarket_calls_{day}.json")
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    for c in (d.get("calls") or []):
        sid = str(c.get("sid") or "")
        if not sid.isdigit():
            continue
        try:
            bundle, err = candle_v3.fetch(sid)
            if err or not bundle:
                continue
            cd = bundle.get("candles") or {}
            o, cl, ts = cd.get("open") or [], cd.get("close") or [], cd.get("timestamp") or []
            tb = int(bundle.get("today_bars") or 0)
            if tb < 2 or len(cl) < tb:
                continue
            s = len(cl) - tb                       # first bar of today's session
            open_px = o[s] if o[s] else cl[s]
            if not open_px:
                continue
            def mv(i):
                j = s + i
                return round((cl[j] / open_px - 1) * 100, 2) if j < len(cl) else None
            c["open_px"] = round(open_px, 2)
            c["m2"] = mv(1)                        # 2 minutes after the open
            c["m5"] = mv(4)                        # 5 minutes
            hi = max((cl[s + k] for k in range(0, min(5, tb))), default=None)
            lo = min((cl[s + k] for k in range(0, min(5, tb))), default=None)
            if hi and lo:
                c["best_up"] = round((hi / open_px - 1) * 100, 2)
                c["best_dn"] = round((lo / open_px - 1) * 100, 2)
            want_up = (c.get("direction") == "UP")
            if c.get("m5") is not None:
                c["hit5"] = bool((c["m5"] > 0) == want_up)
            if c.get("m2") is not None:
                c["hit2"] = bool((c["m2"] > 0) == want_up)
            # was a scalp actually available? (>=0.5% in the called direction)
            edge = c.get("best_up") if want_up else (-(c.get("best_dn") or 0))
            if edge is not None:
                c["scalpable"] = bool(edge >= 0.5)
        except Exception:
            continue
    d["scalp_scored_at"] = datetime.now(IST).isoformat()
    try:
        with _calls_lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, default=str)
            os.replace(tmp, path)
    except Exception:
        pass
    return d


def gap_and_fix(c):
    """Why a call was wrong, and the ONE change that would most have helped.

    Returns (verdict, gap_text, fix_text). Written to be blunt: the point of the
    Premarket Predictor tab is to find the systematic error, not to award marks.
    """
    d = c.get("direction")
    act = c.get("actual_pct")
    op = c.get("open_pct")           # gap at the open (prev close -> open)
    intr = c.get("intraday_pct")     # what was tradeable AFTER 09:15 (open -> close)
    ch = c.get("chance") or 0
    sev = c.get("severity") or 0
    if act is None or d not in ("UP", "DOWN"):
        return "PENDING", "not scored yet", ""
    want_up = (d == "UP")
    got_up = act > 0
    COST = 0.105                     # Rs 10-11 round trip on Rs 10,000
    # ---- direction wrong -------------------------------------------------
    if want_up != got_up:
        if op is not None and intr is not None and (op > 0) == want_up and abs(op) > 0.3:
            return ("GAP-FADED",
                    f"opened {op:+.2f}% the RIGHT way, then gave back {intr:+.2f}%",
                    "News was already priced into the auction. Needs a "
                    "fade-the-gap rule, not a follow-the-gap one.")
        if c.get("confirmed") is False:
            return ("MISS",
                    f"called {d}, actual {act:+.2f}% — pre-open book DISAGREED",
                    "confirmed=False was already the warning. Drop or "
                    "down-weight calls where the auction contradicts the news.")
        if not c.get("mag_pct") and not c.get("mag_amount_cr"):
            return ("MISS",
                    f"called {d}, actual {act:+.2f}% — headline carried NO number",
                    "No magnitude was extractable, so severity was guesswork. "
                    "Require a % or Rs figure before allowing chance > 60.")
        return ("MISS", f"called {d}, actual {act:+.2f}%",
                "Direction wrong with a quantified headline — check whether "
                "this event type is systematically mis-signed.")
    # ---- day % agrees, but did the TRADEABLE leg agree? --------------------
    #   Caught by the test on LENSKART: day % +2.03 (all of it gap), tradeable
    #   -1.34, and this returned "HIT". The call was right about the news and
    #   wrong about everything you could have acted on. Scoring day % alone is
    #   the exact self-flattery this tab exists to expose, so it cannot be
    #   allowed to pass here.
    if intr is not None and (intr > 0) != want_up and abs(intr) >= COST:
        return ("GAP-FADED",
                f"day% {act:+.2f}% looks right, but after 09:15 it went "
                f"{intr:+.2f}% — the wrong way",
                "The move was gap, not follow-through. Score and trade the "
                "open-to-close leg; this call was unprofitable despite the "
                "headline being correct.")
    # ---- direction right --------------------------------------------------
    move = abs(intr if intr is not None else act)
    if move < COST:
        return ("TOO SMALL",
                f"right way ({act:+.2f}%) but only {move:.2f}% was tradeable "
                f"vs {COST}% cost",
                "Correct but unprofitable. Raise the severity floor so "
                "low-magnitude events do not become calls at all.")
    if op is not None and intr is not None and abs(op) > 2 * max(abs(intr), 0.01):
        return ("HIT (gap only)",
                f"{act:+.2f}% but {op:+.2f}% of it was the OPENING GAP; "
                f"only {intr:+.2f}% was tradeable after 09:15",
                "You could not have captured most of this. Score the "
                "open-to-close leg, and consider pre-open entry.")
    if ch >= 80 and move < 1.0:
        return ("HIT (weak)", f"{act:+.2f}% — right, but chance said {ch}%",
                "Over-confident. Chance is not calibrated to magnitude — "
                "cap chance when magnitude is small.")
    return ("HIT", f"{act:+.2f}% ({intr:+.2f}% after 09:15)"
            if intr is not None else f"{act:+.2f}%", "")


def score_outcomes(day=None, log=lambda m: None):
    """Attach the ACTUAL outcome to a day's calls (run after ~09:30)."""
    day = day or datetime.now(IST).strftime("%Y%m%d")
    path = os.path.join(LOGDIR, f"premarket_calls_{day}.json")
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    calls = d.get("calls") or []
    sids = [c.get("sid") for c in calls if str(c.get("sid") or "").isdigit()]
    if not sids:
        return None
    try:
        quotes = q._quote_all([str(s) for s in sids], lambda m: None)
    except Exception as e:
        log(f"premarket: outcome quote failed {e}")
        return None
    for c in calls:
        qq = (quotes or {}).get(str(c.get("sid"))) or {}
        try:
            lp = float(qq.get("last_price") or 0)
            nc = float(qq.get("net_change") or 0)
            prev = lp - nc
            # OPEN LIVES INSIDE `ohlc`, NOT AT THE TOP LEVEL.
            #   qq.get("open") returned None on every quote, so open_pct and
            #   intraday_pct were never set -- on 19-Aug all 35 scored calls
            #   showed a tradeable move of 0.00% and the TRADEABLE hit-rate read
            #   0%. That is the one number this whole tab exists to produce, and
            #   it was silently absent. Movers_alarm reads d["ohlc"]["open"];
            #   this did not.
            op = float((qq.get("ohlc") or {}).get("open") or qq.get("open") or 0)
            if lp > 0 and prev:
                # THREE numbers, because they answer different questions:
                #   actual_pct   prev close -> now      (the headline day %)
                #   open_pct     prev close -> open     (the gap, already priced
                #                                        in before you could act)
                #   intraday_pct open -> now            (what was TRADEABLE after
                #                                        09:15 -- the honest test)
                # Scoring only on day % would mark a stock that gapped +8% and
                # then bled all session as a clean HIT.
                c["actual_pct"] = round(nc / prev * 100, 2)
                if op > 0:
                    c["open_pct"] = round((op / prev - 1) * 100, 2)
                    c["intraday_pct"] = round((lp / op - 1) * 100, 2)
                # HIT IS JUDGED ON `direction`, NOT `sentiment`.
                #   sentiment reads "positive" for ~77% of Dhan's feed (see
                #   Movers_dhannews), so scoring against it made almost every
                #   call a bullish one and the hit-rate meaningless.
                if c.get("direction") in ("UP", "DOWN"):
                    want_up = c["direction"] == "UP"
                    c["hit"] = bool((c["actual_pct"] > 0) == want_up)
                    if c.get("intraday_pct") is not None:
                        c["hit_intraday"] = bool((c["intraday_pct"] > 0) == want_up)
                v, gap, fix = gap_and_fix(c)
                c["verdict"], c["gap"], c["fix"] = v, gap, fix
        except (TypeError, ValueError):
            pass
    d["scored_at"] = datetime.now(IST).isoformat()
    try:
        with _calls_lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, default=str)
            os.replace(tmp, path)
    except Exception:
        pass
    return d


_CALIB_MIN = 25           # a bucket needs this many scored calls to be trusted
_CALIB_TTL = 600.0
_calib_cache = {"t": 0.0, "d": None}


def _wilson(hits, n):
    """95% Wilson interval for a proportion -- honest at small n, unlike the
    normal approximation which happily reports 43% +/- 0% on 3 samples."""
    if not n:
        return 0.5, 0.0, 1.0
    z = 1.96
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def calibration(force=False):
    """MEASURED hit-rates from every scored call on disk.

    This exists because hand-fitted weights were indefensible. Three separate
    attempts to find an edge in the recorded data all failed:

        * text severity        55% / 54% / 55% across bands -- no discrimination
        * headline direction   fixing the classifier changed 52 calls and
                               FIXED 7 / BROKE 7 -- good news on a small-cap is
                               close to a coin flip on the tradeable leg
        * fade-the-gap         -0.232% per trade over 96 gaps >= 2%, 43% wins

    So Chance is no longer a model. It is the OBSERVED frequency for the bucket
    a call falls into, taken from this user's own accumulated outcomes, with the
    sample size and a Wilson interval carried alongside so the UI can show how
    much to trust it. As sessions accumulate it self-corrects; it never invents
    confidence that the data does not contain.
    """
    now = datetime.now().timestamp()
    if not force and _calib_cache["d"] and now - _calib_cache["t"] < _CALIB_TTL:
        return _calib_cache["d"]
    rows = []
    try:
        files = [f for f in os.listdir(LOGDIR)
                 if f.startswith("premarket_calls_") and f.endswith(".json")
                 and "TEST" not in f]
    except Exception:
        files = []
    for f in files:
        try:
            d = json.load(open(os.path.join(LOGDIR, f), encoding="utf-8"))
        except Exception:
            continue
        for c in (d.get("calls") or []):
            # score on the TRADEABLE leg -- the opening gap is not capturable
            if c.get("hit_intraday") is not None:
                rows.append(c)
    n = len(rows)
    hits = sum(1 for c in rows if c.get("hit_intraday"))
    p, lo, hi = _wilson(hits, n)
    out = {"n": n, "base": p, "lo": lo, "hi": hi, "buckets": {}}
    def add(key, sel):
        g = [c for c in rows if sel(c)]
        if len(g) >= _CALIB_MIN:
            h = sum(1 for c in g if c.get("hit_intraday"))
            pp, l, u = _wilson(h, len(g))
            out["buckets"][key] = {"n": len(g), "p": pp, "lo": l, "hi": u}
    add("UP", lambda c: c.get("direction") == "UP")
    add("DOWN", lambda c: c.get("direction") == "DOWN")
    # ---- CANDIDATE FEATURES, measured but not yet trusted -----------------
    # Registered here so the moment any of them reaches _CALIB_MIN outcomes the
    # system can SEE whether it discriminates -- instead of me deciding in
    # advance that it should, which is exactly how the original weights went
    # wrong. Nothing consumes these until `edges()` reports a real separation.
    add("ran_up_3d", lambda c: (c.get("runup_3d") or 0) > 3)
    add("not_run_3d", lambda c: c.get("runup_3d") is not None and c["runup_3d"] <= 3)
    add("UP_after_runup", lambda c: c.get("direction") == "UP" and (c.get("runup_3d") or 0) > 3)
    add("UP_no_runup", lambda c: (c.get("direction") == "UP"
                                  and c.get("runup_3d") is not None and c["runup_3d"] <= 3))
    add("high_in_range", lambda c: (c.get("pos_20d") or 0) >= 80)
    add("low_in_range", lambda c: c.get("pos_20d") is not None and c["pos_20d"] <= 20)
    add("margin_at_low", lambda c: c.get("opm_pos_pct") is not None and c["opm_pos_pct"] <= 25)
    add("inflection", lambda c: c.get("inflection") is True)
    _calib_cache.update({"t": now, "d": out})
    return out


def edges(min_n=_CALIB_MIN, min_sep=8.0):
    """Which candidate features have EARNED their way into the score.

    A feature qualifies only when it has >= min_n outcomes AND its interval
    does not overlap the base rate -- i.e. the separation is real, not the
    +/-7 point noise that the first set of hand-fitted weights was built on.

    Returns a list of dicts; empty is the expected answer for a long time, and
    an empty list is a finding, not a failure.
    """
    cal = calibration()
    base = cal["base"]
    out = []
    for k, v in (cal.get("buckets") or {}).items():
        if k in ("UP", "DOWN") or v["n"] < min_n:
            continue
        sep = (v["p"] - base) * 100
        clears = v["lo"] > base or v["hi"] < base       # interval excludes base
        if abs(sep) >= min_sep and clears:
            out.append({"feature": k, "n": v["n"], "p": round(v["p"] * 100),
                        "sep": round(sep, 1),
                        "ci": [round(v["lo"] * 100), round(v["hi"] * 100)]})
    return sorted(out, key=lambda x: -abs(x["sep"]))


def predictor(day=None, live_rows=None):
    """Everything the Premarket Predictor tab shows, for ONE session.

    WINDOW: the calls file holds exactly the news published between the previous
    session's close (15:30) and today's open -- that is the window
    Movers_dhannews.session_cutoff() feeds build() with -- so no extra filtering
    is needed here beyond reading the day's file.

    TODAY BEFORE THE FREEZE.
      Calls are only written to disk while hhmm < 09:15, so on a day the board
      was started late -- or any time before the first write -- there is no file
      and the tab would render empty with no explanation. When the requested day
      is TODAY and no file exists, fall back to the LIVE premarket rows so the
      tab still shows the current call set, clearly marked as not yet frozen.
    """
    today = datetime.now(IST).strftime("%Y%m%d")
    day = day or today
    path = os.path.join(LOGDIR, f"premarket_calls_{day}.json")
    if not os.path.exists(path):
        if day == today and live_rows:
            calls = [{k: r.get(k) for k in
                      ("sym", "sid", "severity", "chance", "direction", "event",
                       "size", "sentiment", "mag_pct", "mag_amount_cr",
                       "pre_gap", "pre_imb", "confirmed", "title", "when",
                       "confirm", "confirm_detail", "chance_n",
                       "chance_lo", "chance_hi", "chance_basis",
                       "runup_3d", "runup_5d", "runup_10d", "atr_pct",
                       "pos_20d", "vol_x", "opm_now", "opm_pos_pct",
                       "sales_yoy", "inflection", "sector")}
                     for r in live_rows[:60]]
            for c in calls:
                c["verdict"], c["gap"], c["fix"] = gap_and_fix(c)
            calls.sort(key=lambda c: (-(c.get("severity") or 0), -(c.get("chance") or 0)))
            # WHICH SESSION ARE THESE ACTUALLY FOR?
            #   The news window is always "since the last close", so the answer
            #   depends on the clock, and getting it wrong would mislabel an
            #   entire screen. Before 09:15 the live rows are today's call set.
            #   After 15:30 the close has already happened, so the same feed is
            #   now describing the NEXT session -- calling that "today" would be
            #   flatly wrong.
            hhmm = datetime.now(IST).strftime("%H:%M")
            if hhmm < "09:15":
                note = ("LIVE — today's calls, not frozen yet (they freeze at 09:15). "
                        "Reality figures start filling from 09:20.")
                forsess = "today"
            elif hhmm < "15:30":
                note = ("LIVE — the session is running but no calls were frozen for "
                        "today, so there is nothing to score. This happens when the "
                        "board was started after 09:15.")
                forsess = "today (not frozen)"
            else:
                note = ("LIVE — today's close has passed, so these are the calls for "
                        "the NEXT trading session. They freeze at 09:15 tomorrow and "
                        "are scored through that session.")
                forsess = "next session"
            return {"day": day, "rows": calls, "live": True,
                    "summary": {"total": len(calls), "scored": 0,
                                "hit_pct": None, "hit_intraday_pct": None,
                                "verdicts": {}, "top_fix": None, "top_fix_n": 0,
                                "live": True, "for_session": forsess, "err": note}}
        return {"day": day, "rows": [], "live": False,
                "summary": {"err": ("no calls recorded for today yet — the premarket "
                                    "loop writes them before 09:15"
                                    if day == today else
                                    "no calls recorded for this session")}}
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return {"day": day, "rows": [], "summary": {"err": str(e)[:120]}}
    calls = d.get("calls") or []
    for c in calls:
        if "verdict" not in c:
            v, gap, fix = gap_and_fix(c)
            c["verdict"], c["gap"], c["fix"] = v, gap, fix
    scored = [c for c in calls if c.get("actual_pct") is not None]
    hits = [c for c in scored if c.get("hit")]
    hits_intr = [c for c in scored if c.get("hit_intraday")]
    verdicts = {}
    for c in scored:
        verdicts[c.get("verdict")] = verdicts.get(c.get("verdict"), 0) + 1
    # THE MOST COMMON FIX is the actionable output of this whole tab -- one
    # recurring systematic error is worth more than 60 individual verdicts.
    fixes = {}
    for c in scored:
        if c.get("fix"):
            fixes[c["fix"]] = fixes.get(c["fix"], 0) + 1
    top_fix = max(fixes.items(), key=lambda kv: kv[1]) if fixes else None
    calls.sort(key=lambda c: (-(c.get("severity") or 0), -(c.get("chance") or 0)))
    return {
        "day": day, "rows": calls, "live": False,
        "summary": {
            "saved": d.get("saved"), "scored_at": d.get("scored_at"),
            "total": len(calls), "scored": len(scored),
            "hit_pct": (round(len(hits) / len(scored) * 100) if scored else None),
            "hit_intraday_pct": (round(len(hits_intr) / len(scored) * 100)
                                 if scored else None),
            "verdicts": verdicts,
            "top_fix": (top_fix[0] if top_fix else None),
            "top_fix_n": (top_fix[1] if top_fix else 0),
            # which candidate features have earned their way in, if any
            "edges": edges(),
            "calib_n": calibration().get("n", 0),
            "err": None if calls else "no calls recorded",
        }}


PREDICTOR_HISTORY = 3      # how many completed sessions to keep alongside today


def predictor_days(history=PREDICTOR_HISTORY):
    """TODAY plus the last `history` completed sessions, newest first.

    Today is ALWAYS included even when it has no calls file yet -- that was the
    bug: the picker only listed days that already had data, so on a live morning
    the tab silently opened on the newest PAST session and looked like it was
    ignoring today entirely.
    """
    today = datetime.now(IST).strftime("%Y%m%d")
    try:
        fs = [f for f in os.listdir(LOGDIR)
              if f.startswith("premarket_calls_") and f.endswith(".json")
              and "TEST" not in f]
    except Exception:
        fs = []
    past = sorted((f[16:24] for f in fs), reverse=True)
    past = [d for d in past if d != today][:max(0, history)]
    return [today] + past


MIN_SAMPLES = 40          # calls, not days -- ~30 accrue per session


def track_record():
    """MEASURED 5-minute hit-rate, counted in CALLS not days.

    Each session produces roughly 30 scored calls, so a usable sample builds in
    a couple of sessions rather than the fifteen days an end-of-day measure
    would have needed.
    """
    try:
        files = [f for f in os.listdir(LOGDIR) if f.startswith("premarket_calls_")]
    except Exception:
        return {"samples": 0, "ready": False, "note": "no data yet"}
    bands = {"80+": [0, 0], "60-79": [0, 0], "40-59": [0, 0], "<40": [0, 0]}
    samples = sessions = scalpable = 0
    for fn in files:
        try:
            d = json.load(open(os.path.join(LOGDIR, fn), encoding="utf-8"))
        except Exception:
            continue
        scored = [c for c in (d.get("calls") or []) if "hit5" in c]
        if not scored:
            continue
        sessions += 1
        for c in scored:
            ch = c.get("chance") or 0
            b = "80+" if ch >= 80 else "60-79" if ch >= 60 else "40-59" if ch >= 40 else "<40"
            bands[b][1] += 1
            samples += 1
            if c.get("hit5"):
                bands[b][0] += 1
            if c.get("scalpable"):
                scalpable += 1
    ready = samples >= MIN_SAMPLES
    out = {"samples": samples, "sessions": sessions, "ready": ready,
           "scalpable_pct": (round(scalpable / samples * 100) if samples else None),
           "note": ("measured 5-min hit-rate" if ready else
                    f"{samples}/{MIN_SAMPLES} calls measured "
                    f"(~30 per session) - chance shown is a model score until then")}
    for b, (h, n) in bands.items():
        out[b] = {"n": n, "hits": h, "rate": (round(h / n * 100) if (ready and n >= 8) else None)}
    return out
