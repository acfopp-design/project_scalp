"""funnel.py -- Sri's own stock-selection funnel, replayed forward-only.

Sri, 07-Sep: "dhan price shocker, volume shockers + my watchlist stocks play a
key role in identifying the list." Until now my replay ignored all of it and used
every stock with a tape, which is not what he actually trades.

THE FUNNEL, as drawn:
    Dhan VOLUME shockers   -> the Board's "By Volumes" panel      (no penny stocks)
    Dhan PRICE  shockers   -> "Price Movers" + "Intraday Movers"  (no thin stocks)
    MyWatchlist            -> logs/mywatchlist.json, all 4 lists
    -> combined into "list of stocks, highly bullish probability"

FORWARD-ONLY. Each panel row carries the timestamp of the poll that saw it, so a
stock becomes tradeable at the MOMENT IT FIRST APPEARED and not a second before.
The watchlist is Sri's own and is known from the open.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
MIN_PRICE = 20.0            # "except penny stocks"
MIN_TURNOVER_CR = 0.5       # "except low volume stocks"

VOL_PANELS = ("By Volumes",)
PRICE_PANELS = ("Price Movers", "Intraday Movers", "Movers (Intraday+Price)")


def watchlist():
    try:
        wl = json.loads((HERE / "logs" / "mywatchlist.json").read_text(encoding="utf-8"))
        return {r["sym"] for l in wl["lists"].values() for r in l}
    except Exception:
        return set()


def shockers(day):
    """{sym: (first_seen_hhmm, source)} from the Board's own live panels."""
    out = {}
    f = HERE / "logs" / "movers_board" / f"board_{day}.jsonl"
    if not f.exists():
        return out
    for line in f.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        hm = (d.get("ts") or "")[11:19]
        if len(hm) != 8:
            continue
        hm = max(hm, "09:15:00")
        for pname, rows in (d.get("panels") or {}).items():
            for r in rows or []:
                s = r.get("sym")
                if not s:
                    continue
                px = r.get("price") or 0
                tv = (r.get("sess_val") or r.get("sess_vol", 0) * px) / 1e7
                if pname in VOL_PANELS:
                    if px and px < MIN_PRICE:      # no penny stocks
                        continue
                    src = "volume shocker"
                elif pname in PRICE_PANELS:
                    if tv and tv < MIN_TURNOVER_CR:  # no thin stocks
                        continue
                    src = "price shocker"
                else:
                    continue
                if s not in out or hm < out[s][0]:
                    out[s] = (hm, src)
    return out


def superstocks(day):
    """{sym: first_seen_hhmm} from the Super Stocks tab's own log.

    Sri, 08-Sep: "Are you not using your super stocks, board stocks for stocks?
    if not, why? I would recommend you take those."

    He was right that they were missing. His earlier instruction -- "ensure this
    is not impacted by Board and super stocks" -- I read as "do not touch their
    state", so the engine went to the raw Dhan panels and ignored the Super
    Stocks cards entirely. Reading their log is not touching their state, and
    those cards are the board's own best judgement of what is moving.
    """
    out = {}
    f = HERE / "logs" / "movers_board" / f"super_{day}.jsonl"
    if not f.exists():
        return out
    for line in f.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        hm = (d.get("ts") or "")[:8]
        if len(hm) != 8:
            continue
        for r in d.get("rows") or []:
            sym = r.get("sym")
            if not sym:
                continue
            t = max(r.get("first_seen") or hm, "09:15:00")
            if sym not in out or t < out[sym]:
                out[sym] = t
    return out


FIRST_SEEN = "logs/FIRST_SEEN_{}.json"


def _fs_path(day):
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        FIRST_SEEN.format(day))


def first_seen(day, syms=None, now=None):
    """When did THIS ENGINE first actually hold bars for each symbol today?

    FIX 1, 30-Sep. The defect this exists to kill:

        for s in wl:
            av[s] = min(av.get(s, "99:99:99"), "09:15:00")

    That min() overwrote every watchlist name's real discovery time with a
    flat 09:15:00. run_book() gates entries on `t < avail[s]`, so the moment a
    name's bars arrived -- often minutes late, because Dhan flags a stock only
    AFTER it moves -- the replay was told it could have traded that name since
    the open, and entered at a price from before it knew the name existed.

    Measured on 30-Sep, four of six trades:

        NAZARA      flagged 09:19:56, "entered" 09:15:30   (4m26s of hindsight)
        GANDHAR     flagged 09:22:18, "entered" 09:15:30   (6m48s)
        WELSPUNLIV  flagged 09:28:29, "entered" 09:17:00   (11m29s)
        ASTEC       flagged 09:33:02, "entered" 09:16:00   (17m02s)

    Corroborated by the funnel holding ZERO names until 09:18:06 while trades
    were booked at 09:15:30. The bias is not noise: it captures precisely the
    move that caused the flag. NAZARA booked +Rs 4,655 that way.

    This is the same family of defect as the MIN_LEG look-ahead found on
    28-Sep, and it is the "residual contamination" the state doc suspected but
    had not located.

    The record is written once per symbol per day and NEVER revised -- a
    restart must not be able to grant the engine an earlier memory than it
    had. Clock time, not bar time.
    """
    import json
    from datetime import datetime
    p = _fs_path(day)
    try:
        with open(p, encoding="utf-8") as f:
            fs = json.load(f)
    except (OSError, ValueError):
        fs = {}
    # ONLY EVER RECORD FOR TODAY. 30-Sep: replaying an older day called this
    # with that day's symbols and stamped them with tonight's wall clock, so
    # every name became "not known until 18:20" and the replay booked zero
    # trades. A first-seen record is a fact about a live session; a replay must
    # read it, never create it.
    if syms and day == datetime.now().strftime("%Y%m%d"):
        now = now or datetime.now().strftime("%H:%M:%S")
        new = [s for s in syms if s not in fs]
        if new:
            for s in new:
                fs[s] = now
            try:
                import os
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(fs, f)
            except OSError:
                pass
    return fs


def build(day, tape):
    """{sym: available_from} for everything in the funnel that we have bars for."""
    sh = shockers(day)
    wl = watchlist()
    su = superstocks(day)
    av = {}
    for s, (hm, src) in sh.items():
        if s in tape:
            av[s] = hm
    for s, hm in su.items():
        if s in tape:
            av[s] = min(av.get(s, "99:99:99"), hm)
    for s in wl:
        if s in tape:
            av[s] = min(av.get(s, "99:99:99"), "09:15:00")

    # CAUSALITY GATE. Nothing may be entered before the wall-clock moment this
    # engine actually held bars for it. See first_seen() above for the evidence.
    fs = first_seen(day, list(tape.keys()))
    for s in list(av):
        seen = fs.get(s)
        if seen:
            av[s] = max(av[s], seen)
    return av, sh, wl
