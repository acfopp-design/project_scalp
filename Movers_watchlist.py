"""
Movers_watchlist.py -- "Morning Watchlist".

WHY THIS EXISTS
    The SINCE LAST CLOSE tab carries ~130 news items across ~70 stocks. That is a
    feed, not a watchlist -- too much to absorb at 8:30 in the morning, so in
    practice none of it gets read. This cuts the same data down to the handful of
    names that are actually worth watching at the open, on three questions only:

        WHAT HAPPENED  ·  HOW BIG  ·  CAN I TRADE IT

    On 11-Aug the chain below reduced 70 stocks to 5, and four of those five ran
    at the open (AARTIPHARM +3.4%, KOLTEPATIL +3.8%, INDSWFTLAB +7.5%, KPL +2.5%).

IT KEEPS UPDATING, AND THAT IS THE POINT
    A morning watchlist built once at 08:30 misses everything published after it.
    LUMAXTECH released Q1FY27 (+92% profit) at 09:41 and ran 3.8% -- it could not
    have been on any pre-open list. So this re-runs through the session, and
    anything appearing AFTER 09:15 is flagged as new so the UI can raise an alarm.

WHAT IT DELIBERATELY DOES NOT DO
    It does not predict which names will hold. On 11-Aug, KSHINTL passed every
    filter and still faded from +5.65% to +0.76% by 09:26. This tells you WHERE
    TO LOOK. It never tells you what will work.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import datetime

import Opus_engine as engine

try:
    import Movers_dhannews as dhannews
except Exception:                                  # pragma: no cover
    dhannews = None
try:
    import Movers_context as ctx      # previous-session turnover, pre-open
except Exception:                     # pragma: no cover
    ctx = None

try:
    import Movers_alarm as alarm
except Exception:                                  # pragma: no cover
    alarm = None

IST = engine.IST
import os as _os_mod
HERE_LOG = _os_mod.path.join(_os_mod.path.dirname(_os_mod.path.abspath(__file__)),
                             "logs", "movers_board")

# ---- the filter chain -----------------------------------------------------
# 1. HARD EVENTS ONLY. Things that actually re-rate a company overnight.
HARD_RE = re.compile(
    r"(q[1-4]\s*fy|quarterly result|net profit|\bpat\b|ebitda|revenue|profit "
    r"(surge|jump|rise|ris|fall|dip|declin|up|doubl)|swings? to profit|turns? profitable"
    r"|order (win|worth|book|bag)|wins? (an? )?(order|contract)|bags\b|contract worth"
    r"|land sale|asset sale|stake sale|acquisi\w*|merger|demerger|open offer"
    r"|stock split|bonus issue|buyback|dividend declar|record date"
    r"|upgrade|downgrade|target price|guidance|fund rais\w*|qip\b|preferential"
    r"|approval|licen[cs]e|usfda|navratna|capacity expansion|new plant)", re.I)

# 2. NOISE. Corporate hygiene that reads "positive" but never moves a price.
NOISE_RE = re.compile(
    r"\b(agm\b|annual general meeting|ratif\w*|re-?appoint\w*|book closure"
    r"|newspaper (publication|advertisement)|intimation under|disclosure under"
    r"|regulation 3\d|trading window|duplicate share|scrutiniz\w*|voting result"
    r"|postal ballot|loss of share certificate|analyst meet|investor meet"
    r"|earnings call (invite|intimation)|schedule[d]? for|to discuss)\b", re.I)

# ---- DIRECTION -----------------------------------------------------------
# Order matters. "Loss narrows" is BULLISH and contains the word "loss", so the
# rescue patterns are checked before the bearish ones. Getting this backwards
# would put a recovering company in the short column, which is worse than
# showing no direction at all.
# Unambiguously bad, whatever else the sentence contains. Checked FIRST, because
# these headlines routinely also contain bullish words and would otherwise be
# read the wrong way round:
#   "USFDA warning letter"          -> "usfda" is in the bullish list
#   "order worth Rs 300 cr cancelled" -> "order worth" is in the bullish list
# Calling either of those bullish would put a collapsing stock in the long
# column, which is the worst mistake this function can make.
HARD_BEAR_RE = re.compile(
    r"(warning letter|import alert|form 483|clinical hold|recall\w*"
    r"|cancel\w*|terminat\w*|revoke[sd]?|withdraw\w*|reject\w*|rejects?\b"
    r"|fraud|default\w*|insolven\w*|\bnclt\b|delist\w*|suspend\w*|debar\w*"
    r"|resign\w*|steps? down|penalt\w*|show cause|\braid\b|search and seiz"
    r"|downgrade[sd]?|rating cut|qualified opinion|going concern)", re.I)

BULL_RESCUE_RE = re.compile(
    r"(loss (narrow|reduc|shrink|declin|contract)\w*|narrow\w*\s+(its\s+)?loss"
    r"|back (in|to) (the )?black|swings? to profit|turns? profitable"
    r"|profit turns positive|returns? to profit|exits? loss)", re.I)

BEAR_RE = re.compile(
    r"(profit\s+(fall|fell|declin|drop|slump|plunge|shrink|moderat|dip|slid|down)\w*"
    r"|net\s+loss|loss\s+(widen|surg|jump|ris|expand|deepen)\w*|slips?\s+into\s+loss"
    r"|revenue\s+(fall|declin|drop|slump|shrink)\w*|sales\s+(fall|declin|drop)\w*"
    r"|margins?\s+(\w+\s+){0,2}(contract|declin|shrink|compress|narrow|fall|drop|erod)\w*"
    r"|downgrade|target price (cut|lower|reduc)|rating (cut|lower|downgrad)"
    r"|guidance (cut|lower|reduc|trim)|cuts? (guidance|outlook|estimate)"
    r"|resign\w*|steps? down|penalt\w*|\bfine[ds]?\b|show cause|adverse"
    r"|order (cancel|terminat)\w*|contract (cancel|terminat)\w*"
    r"|fraud|default\w*|insolven\w*|\bnclt\b|delist\w*|suspend\w*"
    r"|pledge\w*|stake (sale|sold) by promoter|promoter (sells|sold|offload)"
    r"|recall|import alert|form 483|warning letter|clinical hold)", re.I)

BULL_RE = re.compile(
    # allow a couple of words between the noun and the verb -- real headlines
    # write "net profit nearly doubles", not "profit doubles"
    r"(profit\s+(\w+\s+){0,2}(surg|jump|ris|soar|climb|grow|doubl|tripl|quadrupl|up\b|zoom|spike|multipl)\w*"
    r"|\bpat\b\s+(\w+\s+){0,2}(ris|surg|jump|up\b|grow|doubl|tripl|quadrupl)\w*"
    r"|net profit\s+(\w+\s+){0,2}(up\b|ris|surg|jump|doubl|grow)"
    r"|revenue\s+(surg|jump|ris|grow|up|climb)\w*|sales\s+(surg|jump|ris|grow|up)\w*"
    r"|margin\s+(expand|improv|widen)\w*"
    r"|order (win|won|worth|book)|wins?\s+(\w+\s+){0,4}?(order|contract|bid|project|tender)"
    r"|secures?\s+(\w+\s+){0,4}?(order|contract|project)|bags\b"
    r"|\bloi\b|letter of intent|new (order|contract)"
    r"|upgrade[sd]?\b|target price (rais|hik|increas)|rating (upgrad|rais)"
    r"|record\s+(\w+\s+){0,2}(profit|revenue|throughput|sales|production|output)"
    r"|bonus issue|stock split|buyback|dividend declar|interim dividend"
    r"|approval|approve[sd]?\b|usfda|licen[cs]e|clearance|nod\b"
    r"|capacity (expansion|addition)|new plant|commission\w*|navratna"
    r"|acquisi\w*|acquires?\b|merger|stake (buy|purchase|acquisition)"
    r"|land sale|asset (sale|monetis|monetiz)\w*|fund rais\w*|qip\b)", re.I)

# ASPIRATION, NOT EVENT.
#   "Choice International Eyes 800 Branches & 50% Annual Growth" ranked #1 with
#   a 50% "surprise" -- but "eyes" is a plan, not a reported number. A company
#   hoping to grow 50% a year is not a 09:15 trade. These verbs mark the whole
#   headline as forward-looking and it is dropped.
ASPIRATION_RE = re.compile(
    r"\b(eyes?|aims?|aiming|targets?|targeting|plans?|planning|intends?|expects?|"
    r"hopes?|seeks?|seeking|looks? to|set to|on track|to achieve|to reach|to double|"
    r"to invest|to raise|guidance of|outlook|projects?|forecasts?|estimates?|"
    r"may |could |likely to|proposes?|mulls?|explores?|considering|in talks)\b", re.I)

PCT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*%")
RS_RE = re.compile(r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)\s*"
                   r"(crore|cr\b|lakhs?|lacs?|million|mn\b|billion|bn\b)?", re.I)
# a qualitative turn counts as magnitude even with no number attached
TURN_RE = re.compile(r"(swings? to profit|turns? profitable|profit turns positive"
                     r"|loss (to|turns) profit|record (profit|revenue|throughput)"
                     r"|stock split|bonus issue|navratna)", re.I)

# LIQUIDITY FLOOR, sized to a Rs 10,000 position -- not to a fund.
#   The old Rs 25 Cr floor was inherited from a market-cap screener and threw
#   out DIVGIITTS (Rs 4.27 Cr turnover, Q1 profit +183%, stock +10.4%), which is
#   exactly what this tab is for. At Rs 10,000 you are 0.02% of a Rs 4 Cr day --
#   filling is not the problem.
#   The real cost in a thin stock is the SPREAD: at Rs 1,188 a 0.5% spread is
#   five times your brokerage. That is a judgement for Sri to make, not for this
#   filter to make silently -- so the floor is low and anything below
#   THIN_TOVER_CR is shown with a warning rather than hidden.
MIN_TOVER_CR = 2.0
THIN_TOVER_CR = 15.0       # below this: tradeable, but flag the spread risk
MIN_PRICE = 40.0
MAX_ROWS = 12
# Two different clocks on purpose:
#   PRICES are re-read every cycle -- the ranking has to react to the tape.
#   NEWS is re-fetched less often -- headlines arrive in minutes, and the feed
#   is shared with the News tab, so hammering it buys nothing.
REFRESH_SEC = 20           # re-rank this often (prices move, news may not)
NEWS_FORCE_SEC = 60        # force a fresh pull of the news feed this often
_last_news_pull = [0.0]

# ---- SELF-HEALING ---------------------------------------------------------
# This tab has now gone blank twice, for two different reasons, and each time
# the only symptom was "N news items -> 0 qualify". A morning watchlist that
# silently shows nothing is worse than one showing a flagged, imperfect list:
# blank looks identical to "no news today", so the failure hides itself.
#
# So the chain runs at escalating RELAX levels and stops at the first that
# yields rows:
#     0  strict -- every gate as designed
#     1  liquidity floor dropped (turnover unknown or below Rs 2 Cr allowed)
#     2  also drops the price floor
# The universe and text gates are NEVER relaxed: a BSE-only name or a hygiene
# filing is wrong at any level, whereas an unverified turnover is merely
# unproven. Whatever level was needed is reported on the rows and in the UI.
_RELAX = {"n": 0}
_lock = threading.Lock()
_seen = {}                 # sym -> {"first": epoch, "after_open": bool}
_state = {"ts": 0.0, "rows": [], "err": None, "raw": 0, "kept": 0}


def _market_open_epoch(now=None):
    now = now or datetime.now(IST)
    return now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()


def biggest_pct(text):
    best = 0.0
    for m in PCT_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0 < v < 5000 and v > best:
            best = v
    return best


def biggest_cr(text):
    best = 0.0
    for m in RS_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = (m.group(2) or "").lower()
        if u.startswith(("lakh", "lac")):
            v /= 100.0
        elif u.startswith(("million", "mn")):
            v /= 10.0
        elif u.startswith(("billion", "bn")):
            v *= 100.0
        elif not u:
            v /= 1e7
        if v > best:
            best = v
    return best


def direction(title, text, sentiment=None, day_pct=None):
    """('UP'|'DOWN'|'UNCLEAR', why).

    Decided from the WORDS first, because that is what is known before the
    market reacts. Dhan's own sentiment tag and the live day move are only used
    to break a tie -- both are unreliable on their own: the sentiment tag reads
    "positive" for roughly 77% of the feed, and the day move can be dominated by
    something entirely unrelated to this headline.
    """
    blob = (title or "") + " " + (text or "")
    # 1. unambiguously bad wins outright, even alongside bullish words
    if HARD_BEAR_RE.search(blob):
        return "DOWN", "hard-bearish wording"
    # 2. recovery wording ("loss narrows") before the bearish check, or the word
    #    "loss" would drag a recovering company into the short column
    if BULL_RESCUE_RE.search(blob):
        return "UP", "recovery wording"
    bear = bool(BEAR_RE.search(blob))
    bull = bool(BULL_RE.search(blob))
    if bull and not bear:
        return "UP", "bullish wording"
    if bear and not bull:
        return "DOWN", "bearish wording"
    if bull and bear:
        # mixed headline -- fall back to the tag, then to the tape
        if sentiment == "positive":
            return "UP", "mixed wording, positive tag"
        if sentiment == "negative":
            return "DOWN", "mixed wording, negative tag"
        if day_pct is not None:
            return ("UP" if day_pct >= 0 else "DOWN"), "mixed wording, live move"
        return "UNCLEAR", "mixed wording"
    if sentiment == "negative":
        return "DOWN", "sentiment tag"
    if sentiment == "positive":
        return "UP", "sentiment tag"
    return "UNCLEAR", "no directional wording"


def _what_happened(title, text):
    """Five words, not a paragraph. The headline already says it -- we just trim."""
    t = (title or "").strip()
    t = re.sub(r"^[A-Z][\w&\.\- ]{2,28}\s+(Ltd|Limited)?\s*[:\-–]\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t[:70]


def _build_once(log=lambda m: None):
    if dhannews is None:
        with _lock:
            _state["err"] = "Movers_dhannews unavailable"
        return []
    try:
        # force a fresh pull periodically so a headline published DURING the
        # session (LUMAXTECH at 09:41) shows up within a minute, not whenever
        # the shared news cache happens to expire
        now_t = time.time()
        force = (now_t - _last_news_pull[0]) >= NEWS_FORCE_SEC
        if force:
            _last_news_pull[0] = now_t
        items, err = dhannews.fetch(force=force, log=log)
    except Exception as e:
        with _lock:
            _state["err"] = f"{type(e).__name__}: {str(e)[:100]}"
        return []
    try:
        dhannews.attach_moves(items, log)
    except Exception:
        pass

    # live tradeability from the shared sweep -- no extra API cost
    snap = {}
    universe_syms = set()
    if alarm is not None:
        try:
            _, snap = alarm.snapshot()
        except Exception:
            snap = {}
        try:
            # {sid: sym} for NSE EQ series only -- used to reject non-symbols
            universe_syms = {str(s).upper() for s in alarm.universe(log).values()}
        except Exception:
            universe_syms = set()

    # One clock read for the whole pass. The liquidity rule differs before and
    # after 09:15 (see the note at the turnover gate), so this must not be
    # recomputed per row or a build straddling 09:15 would use two rules.
    market_live_now = bool(alarm.market_live()) if alarm is not None else False
    # WHERE ROWS DIE.
    #   "227 news items -> 0 qualify" says nothing about WHY, and this tab has
    #   now gone blank twice for two different reasons. Every rejection is
    #   counted and logged so the next cycle answers the question instead of me
    #   guessing at it from a stub that does not reproduce the live environment.
    rej = {}
    def _no(stage):
        rej[stage] = rej.get(stage, 0) + 1
    best = {}
    raw = len(items)
    for it in items:
        title = it.get("title") or ""
        blob = title + " " + (it.get("text") or "")
        # 1 + 2. hard event, not hygiene
        if NOISE_RE.search(blob) and not HARD_RE.search(title):
            _no('1_noise'); continue
        if not HARD_RE.search(blob):
            _no('2_no_hard_event'); continue
        # 2b. reject forward-looking statements -- a plan is not an event
        if ASPIRATION_RE.search(title):
            _no('3_aspiration'); continue
        # 3. magnitude actually stated -- a number, or an unambiguous turn
        pct, cr = biggest_pct(blob), biggest_cr(blob)
        turn = bool(TURN_RE.search(blob))
        if not (pct >= 10 or cr >= 25 or turn):
            _no('4_magnitude'); continue
        sym = (it.get("sym") or "").upper().strip()
        if not sym:
            _no('5_no_symbol'); continue
        # 4. tradeable
        sid = str(it.get("nse") or "")
        price = it.get("ltp")
        # LIQUIDITY, and where it came from.
        #   Live sweep  -> today's turnover, but the sweep only runs 09:15-15:30.
        #   Fallback    -> the turnover attach_moves already pulled with the
        #                  quote, which pre-open is YESTERDAY's volume.
        # Without this fallback the whole list scores zero on liquidity at 09:08
        # -- exactly when it is being read -- and the ranking would be driven
        # entirely by headline size, ignoring whether you could get out.
        tov = it.get("tover_cr")
        tov_live = False
        tov_prev = False        # turnover came from the last completed session
        tov_unknown = False     # no figure at all -- pre-open only
        t = snap.get(sid)
        if t:
            try:
                lp, vol = float(t[0]), float(t[1])
                price = round(lp, 2)
                tov = round(vol * lp / 1e7, 1)
                tov_live = True
            except (TypeError, ValueError):
                pass
        # SYMBOL MUST BE A REAL NSE EQ-SERIES STOCK.
        #   The news feed carries free-text symbols and some are not symbols at
        #   all -- "THE AUDIT COMMITTEE, SHIV AUM STEELS" was sitting on the
        #   watchlist. Checking against the security master removes that whole
        #   class, and takes BE/SM/ST series out with it since those cannot be
        #   squared off intraday anyway.
        if universe_syms and sym not in universe_syms:
            _no('6_not_nse_eq'); continue
        if _RELAX['n'] < 2 and price is not None and price < MIN_PRICE:
            _no('7_price_below_min'); continue
        # LIQUIDITY -- and the reason this is not a one-liner.
        #
        #   Rejecting unknown liquidity is right DURING the session: obscure
        #   names are exactly the ones with no quote data, and letting them
        #   through admitted junk while correctly rejecting real stocks.
        #
        #   Before 09:15 it is flatly wrong, and it emptied this tab. Dhan's
        #   quote reports volume 0 pre-open -- because nothing has traded yet,
        #   not because the stock is illiquid -- so attach_moves never sets
        #   tover_cr, every stock reads "unknown", and all 375 headlines were
        #   rejected at 08:10 on the morning the list is actually read.
        #
        #   Fix: pre-open, fall back to the LAST COMPLETED SESSION's turnover.
        #   It answers "can I get out of this?" just as well at 08:10 as the
        #   live figure does at 10:00.
        if tov is None and not tov_live and ctx is not None:
            try:
                pt = ctx.prev_turnover(sid)
                if pt:
                    tov, tov_prev = pt, True
            except Exception:
                pass
        if tov is None:
            # Still unknown. During the session that is a reject, as before.
            # Pre-open it means we simply have no history cached yet, so keep
            # the row and let the UI flag it rather than silently hiding it.
            if market_live_now and _RELAX['n'] < 1:
                _no('8_liquidity_unknown'); continue
            tov_unknown = True
        elif tov < (MIN_TOVER_CR if _RELAX['n'] < 1 else 0.0):
            _no('9_turnover_below_min'); continue

        dirn, why = direction(title, it.get("text"), it.get("sentiment"),
                              it.get("day_pct"))
        # A bearish event with a big number is still a big event -- the sign of
        # the move belongs on the badge, not in the magnitude.
        size = (f"{'-' if dirn == 'DOWN' else '+'}{pct:.0f}%" if pct >= 10
                else (f"₹{cr:,.0f} Cr" if cr >= 25 else "turnaround"))
        # ---- RANK ------------------------------------------------------
        # This scores CONVICTION IN THE SETUP. It is not a probability of
        # profit and must never be presented as one -- on 11-Aug KSHINTL
        # would have ranked near the top and still faded from +5.65% to
        # +0.76% by 09:26. Nothing knowable before the open separated it
        # from INDSWFTLAB, which held +7.5%.
        #
        # Four components, each defensible on its own terms:
        # SURPRISE -- log scale, no early cap.
        #   The old linear/60% version scored +81%, +134%, +175% and +183% as an
        #   identical 34/34, so the best news on the board could not outrank the
        #   merely-good. Log keeps growing but with diminishing returns, which is
        #   how a repricing actually behaves.
        surprise = (math.log10(1.0 + pct / 8.0) / math.log10(1.0 + 400 / 8.0)) * 32 if pct >= 10 else 0.0
        if pct < 10 and cr >= 25:
            surprise = (math.log10(1.0 + cr / 20.0) / math.log10(1.0 + 2000 / 20.0)) * 26
        elif turn and pct < 10:
            surprise = 18
        # LIQUIDITY IS A GATE, NOT A RACE.
        #   Previously 30 points ramping to Rs 150 Cr, which made it the only
        #   column still varying once the others saturated -- the ranking became
        #   "biggest turnover wins". For a Rs 10,000 position, Rs 277 Cr is not
        #   four times better than Rs 63 Cr; both are simply enough. So: small
        #   weight, saturating early.
        liq = 0.0 if tov is None else min(tov / 25.0, 1.0) * 10
        clarity = 20 if dirn in ("UP", "DOWN") else 0   # unclear direction is unusable
        cls = 14 if re.search(r"(q[1-4]\s*fy|result|net profit|\bpat\b)", blob, re.I) else 8
        # ---- MARKET REACTION (only once the market is actually open) -----
        # Before 09:15 the headline is all there is. After 09:15 the TAPE is
        # better evidence than any wording: a "bullish" story whose stock is
        # falling has been judged by people with money on it, and that judgement
        # outranks my regex. So a contradicted call is penalised hard enough to
        # drop it out of the top 3 entirely.
        # REACTION -- now the biggest single term once the market is open, and it
        # runs to 15% rather than saturating at 5%. Under the old cap a stock up
        # 16% and one up 6% both scored 26/26, so the name actually running
        # hardest got no credit for it. After the open the tape IS the evidence.
        reaction = 0.0
        dp = it.get("day_pct")
        live = (alarm.market_live() if alarm is not None else False)
        if live and dp is not None and dirn in ("UP", "DOWN"):
            agree = (dp > 0) if dirn == "UP" else (dp < 0)
            mag = min(abs(dp) / 15.0, 1.0)
            reaction = (mag * 38.0) if agree else -(20.0 + mag * 25.0)
        rank = surprise + liq + clarity + cls + reaction
        rank_parts = {"surprise": round(surprise, 1), "liquidity": round(liq, 1),
                      "clarity": clarity, "event": cls,
                      "reaction": round(reaction, 1), "live": bool(live)}
        d = {"sym": sym, "sid": sid,
             "what": _what_happened(title, it.get("text")),
             "size": size, "pct": round(pct, 1), "cr": round(cr, 1),
             "turn": turn, "price": price, "tover_cr": tov, "tov_live": tov_live,
             "thin": bool(tov is not None and tov < THIN_TOVER_CR),
             "dir": dirn, "dir_why": why,
             "day_pct": it.get("day_pct"), "when": it.get("when"),
             "ts": it.get("ts"), "rank": round(rank, 1),
             "rank_parts": rank_parts, "title": title}
        cur = best.get(sym)
        if cur is None or d["rank"] > cur["rank"]:
            best[sym] = d

    rows = list(best.values())
    # ---- NEW-SINCE-OPEN detection: the reason this tab keeps running ----
    now = time.time()
    open_ep = _market_open_epoch()
    with _lock:
        for d in rows:
            s = _seen.get(d["sym"])
            if s is None:
                # first time this stock has ever appeared on the watchlist
                published = (d["ts"] / 1000.0) if d.get("ts") else now
                after = (published >= open_ep) and (now >= open_ep)
                _seen[d["sym"]] = {"first": now, "after_open": after,
                                   "alerted": False}
                s = _seen[d["sym"]]
            d["is_new"] = bool(s["after_open"])
            d["age_min"] = int((now - s["first"]) / 60)
            # "fresh" drives the alarm: new AND only just noticed
            d["alarm"] = bool(s["after_open"] and d["age_min"] < 10)
    # ORDER STRICTLY BY SCORE.
    #   This used to sort new-since-open names to the front, and the rank numbers
    #   were stamped on afterwards -- so anything arriving after 09:15 became
    #   "rank 1" however bad it was. CHOICEIN took rank 1 on a score of 40.4
    #   while TDPOWERSYS (126) sat at 2, purely because CHOICEIN was newer. Being
    #   new is a reason to LOOK, not a claim of being the best trade on screen.
    #   It keeps the NEW badge and the alarm; it now has to earn its rank.
    rows.sort(key=lambda r: -r["rank"])
    rows = rows[:MAX_ROWS]
    # Number only the TOP 3, and only ones with a usable direction. A stock we
    # cannot call bullish or bearish is not something to trade at 09:15.
    n = 0
    for d in rows:
        if d.get("dir") in ("UP", "DOWN") and n < 3:
            n += 1
            d["top"] = n
        else:
            d["top"] = None
    with _lock:
        _state.update({"rows": rows, "ts": now, "err": None,
                       "raw": raw, "kept": len(best)})
    _LAST_REJ.clear(); _LAST_REJ.update(rej)

    if rej:

        log("watchlist rejects: " + ", ".join(f"{k}={v}" for k, v in sorted(rej.items())))

    log(f"watchlist: {raw} news items -> {len(best)} qualify -> showing {len(rows)}"
        + (f" ({sum(1 for r in rows if r['is_new'])} NEW since open)" if rows else ""))
    return rows


def rows():
    with _lock:
        return list(_state["rows"])


def summary():
    with _lock:
        rws = list(_state["rows"])
        return {"ts": datetime.fromtimestamp(_state["ts"], IST).strftime("%H:%M:%S")
                if _state["ts"] else None,
                "raw": _state["raw"], "kept": _state["kept"],
                "shown": len(rws),
                "new_since_open": sum(1 for r in rws if r.get("is_new")),
                "bull": sum(1 for r in rws if r.get("dir") == "UP"),
                "bear": sum(1 for r in rws if r.get("dir") == "DOWN"),
                "alarm": any(r.get("alarm") for r in rws),
                "err": _state["err"],
                "live": (alarm.market_live() if alarm is not None else False),
                "relaxed": _state.get("relaxed", 0), "rejects": _state.get("rejects", {}),
                "rules": {"min_tover_cr": MIN_TOVER_CR, "min_price": MIN_PRICE,
                          "max_rows": MAX_ROWS}}


def loop(log=lambda m: None, stop=None):
    log("watchlist: loop started")
    while not (stop and stop.is_set()):
        try:
            build(log)
        except Exception as e:
            log(f"watchlist: {type(e).__name__} {str(e)[:120]}")
        time.sleep(REFRESH_SEC)


_LAST_REJ = {}
DIAG_DIR = None


def build(log=lambda m: None):
    """Run the chain, relaxing OPTIONAL gates until it yields something.

    The user cannot read logs or paste them back, so this must diagnose and
    correct itself. Two behaviours make that true:

      1. ESCALATION. Level 0 is the chain exactly as designed. If it returns
         nothing while candidates existed, level 1 drops the liquidity floor and
         level 2 drops the price floor. Text and universe gates never relax --
         a hygiene filing or a non-NSE symbol is wrong at any level, whereas an
         unverified turnover is merely unproven. Rows carry `relaxed` so the UI
         can say the list is provisional rather than pretending it is clean.

      2. A DIAGNOSTIC FILE. Every cycle writes logs/movers_board/watchlist_diag_
         YYYYMMDD.json with the rejection counts per gate and the level used.
         That file is readable directly from the project folder, so the next
         session can see exactly which gate fired without anyone copying a log.
    """
    rows = []
    used = 0
    for lvl in (0, 1, 2):
        _RELAX["n"] = lvl
        try:
            rows = _build_once(log)
        except Exception:
            _RELAX["n"] = 0
            raise
        used = lvl
        if rows:
            break
    _RELAX["n"] = 0
    if used:
        log(f"watchlist: SELF-HEALED at relax level {used} "
            f"({'liquidity floor dropped' if used == 1 else 'liquidity + price floors dropped'}) "
            f"-> {len(rows)} rows")
    for r in rows:
        r["relaxed"] = used
    try:
        import json as _json
        import os as _os
        d = _os.path.join(HERE_LOG, f"watchlist_diag_{datetime.now(IST).strftime('%Y%m%d')}.json")
        _os.makedirs(HERE_LOG, exist_ok=True)
        payload = {"ts": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                   "relax_used": used, "rows": len(rows),
                   "rejects": dict(_LAST_REJ),
                   "market_live": bool(alarm.market_live()) if alarm is not None else None,
                   "thresholds": {"MIN_TOVER_CR": MIN_TOVER_CR, "MIN_PRICE": MIN_PRICE}}
        with open(d, "w", encoding="utf-8") as f:
            _json.dump(payload, f, indent=1)
    except Exception:
        pass
    with _lock:
        _state["relaxed"] = used
        _state["rejects"] = dict(_LAST_REJ)
    return rows
