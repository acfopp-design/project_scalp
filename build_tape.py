"""
build_tape.py -- give replay_live a price series for EVERY stock it can trade.

THE PROBLEM THIS SOLVES (04-Sep)
  replay_live builds its prices from bars30, which Movers_ticks writes from the
  BOARD's own universe. A symbol outside that set has no series at all, so the
  replayed position freezes at its entry price, never moves, never stops, and
  exits flat on the 30-minute cap paying only charges.

  On 04-Sep, 13 of 27 replayed trades exited at EXACTLY the entry price; ten of
  those symbols were absent from bars30. They cost Rs 1,300 between them while
  the 14 that could be priced made Rs 24,892. Because bars30 contains the names
  that were already moving, the replay prices the winners and turns everything
  else into a free option that cannot lose. Every replay figure this project has
  produced is inflated by that.

WHAT IT DOES
  For a session, take every symbol that appears in that day's super log (the
  cards the engine could have traded), pull TRUE 30-second bars for each from
  ticks.dhan.co via Movers_chartfeed.get_seconds -- the same series Sri's own
  chart is drawn from -- and write logs/tape/<day>/<SYM>.json.

  Run once per session, board-side (this needs the live feed). The cache is then
  permanent: every future replay of that day uses real prices.

COVERAGE IS REPORTED, NOT ASSUMED
  It prints how many symbols were fetched, how many failed, and which. A symbol
  that cannot be priced must be VISIBLE, because the whole bug was an invisible
  gap being silently treated as a flat position.

Usage: python3 build_tape.py [--day=YYYYMMDD] [--all] [--days=5]
"""
import json, sys, time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
from pathlib import Path

HERE = Path(__file__).resolve().parent
BD   = HERE / "logs" / "movers_board"
TAPE = HERE / "logs" / "tape"

DAY  = next((a.split("=")[1] for a in sys.argv if a.startswith("--day=")),
            datetime.now().strftime("%Y%m%d"))
DAYS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--days=")), 5))
ALL  = "--all" in sys.argv


def symbols_of(day, include_watch=True):
    """Every symbol the engine could have traded that day.

    The super log alone is NOT enough. It lists what was CARDED, so fetching
    only those makes the tape downstream of the decision under test -- the same
    circularity that killed card_lab.py, and the reason question 6.5 (does
    widening the 09:16 universe to MyWatchlist help?) could not be answered on
    04-Sep: every watchlist name that was never carded had no price history to
    check, so the answer was structurally guaranteed to be "no".

    So also fetch MyWatchlist and the habitual-mover list. Those are names we
    have decided are worth WATCHING, and the whole point is to find out what
    they did on days the board ignored them.
    """
    out = {}
    f = BD / f"super_{day}.jsonl"
    if not f.exists():
        return out
    with f.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                snap = json.loads(line)
            except Exception:
                continue
            for r in snap.get("rows") or []:
                s = r.get("sym")
                if s and s != "AAA":
                    out.setdefault(s, r.get("sid"))
    if include_watch:
        try:
            wl = json.loads((HERE / "logs" / "mywatchlist.json").read_text(encoding="utf-8"))
            for lst in (wl.get("lists") or {}).values():
                for r in lst:
                    if r.get("sym"):
                        out.setdefault(r["sym"], r.get("sid"))
        except Exception:
            pass
        try:
            for r in json.loads((HERE / "logs" / "habitual_movers.json")
                                .read_text(encoding="utf-8")):
                if r.get("sym"):
                    out.setdefault(r["sym"], None)
        except Exception:
            pass
    return out


def resolve_sids(syms):
    """super rows carry sid: null, so resolve from the scrip master"""
    import csv
    need = {s for s, sid in syms.items() if not sid}
    if not need:
        return syms
    with (HERE / "security_id_list.csv").open(newline="", encoding="utf-8",
                                              errors="ignore") as f:
        for r in csv.DictReader(f):
            s = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
            if (s in need and not syms.get(s)
                    and (r.get("SEM_EXM_EXCH_ID") or "").upper() == "NSE"
                    and (r.get("SEM_SEGMENT") or "").upper() == "E"):
                syms[s] = str(r.get("SEM_SMST_SECURITY_ID")).strip()
    return syms



def _only_day(d, day):
    """Trim a get_seconds payload to bars belonging to `day` (YYYYMMDD).
    Returns None when the feed carries nothing for that date -- which is the
    normal answer for anything older than about three sessions."""
    ts = d.get("t") or []
    keep = [i for i, tv in enumerate(ts)
            if datetime.fromtimestamp(tv, IST).strftime("%Y%m%d") == day]
    if not keep:
        return None
    out = {}
    for k in ("o", "h", "l", "c", "v", "t"):
        arr = d.get(k) or []
        out[k] = [arr[i] for i in keep if i < len(arr)]
    for k in ("sym", "sid"):
        if k in d:
            out[k] = d[k]
    return out


MAX_BACK_DAYS = 5


def _too_old(day):
    """The 30-second feed carries roughly three sessions. Fetching an older date
    still costs one network call PER SYMBOL and returns nothing usable -- with
    559 symbols that is ten wasted minutes a day. Probe once and skip."""
    from datetime import datetime as _dt
    try:
        d = _dt.strptime(day, "%Y%m%d")
    except Exception:
        return False
    return (_dt.now() - d).days > MAX_BACK_DAYS


def fetch_day(day, cf):
    if _too_old(day):
        print(f"{day}: older than the {MAX_BACK_DAYS}-day feed window -- skipped "
              f"(the 30s feed cannot return it, and probing costs one call per symbol)")
        return
    syms = resolve_sids(symbols_of(day))
    if not syms:
        print(f"{day}: no super log -- nothing to fetch")
        return
    out = TAPE / day
    out.mkdir(parents=True, exist_ok=True)
    ok = skip = fail = 0
    failed = []
    t0 = time.time()
    for sym, sid in sorted(syms.items()):
        p = out / f"{sym}.json"
        if p.exists():
            skip += 1
            continue
        if not sid:
            fail += 1; failed.append(f"{sym}(no sid)"); continue
        try:
            # get_seconds returns (data, error) -- NOT the data alone. Unpacked
            # here because treating the tuple as the payload silently "succeeds"
            # (len(tuple)==2 looks like two bars).
            d, ferr = cf.get_seconds(str(sid), interval="30S", days=DAYS)
            if ferr:
                fail += 1; failed.append(f"{sym}({str(ferr)[:24]})"); continue
        except Exception as e:
            fail += 1; failed.append(f"{sym}({type(e).__name__})"); continue
        if not d or not (d.get("c") if isinstance(d, dict) else d):
            fail += 1; failed.append(f"{sym}(empty)"); continue
        # KEEP ONLY THIS DAY. get_seconds returns roughly three sessions, so a
        # fetch run for an older date returns bars that are not that date at
        # all. Writing them unfiltered would let a replay price one session with
        # another session's prices.
        d = _only_day(d, day)
        if not d:
            fail += 1; failed.append(f"{sym}(no bars for {day})"); continue
        p.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
        ok += 1
    print(f"{day}: fetched {ok}, cached-already {skip}, FAILED {fail}, "
          f"of {len(syms)} symbols in {time.time()-t0:.0f}s")
    if failed:
        print(f"   unpriceable: {', '.join(failed[:15])}"
              + (f" ... +{len(failed)-15} more" if len(failed) > 15 else ""))
        print("   ^ these will still freeze at entry price in replay -- known, not hidden")


def main():
    import Movers_chartfeed as cf
    days = ([p.stem.split("_")[1] for p in sorted(BD.glob("super_2*.jsonl"))]
            if ALL else [DAY])
    for d in days:
        fetch_day(d, cf)


if __name__ == "__main__":
    main()
