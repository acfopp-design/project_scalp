"""
Movers_master.py -- keep security_id_list.csv current, by itself.

WHY THIS EXISTS
    On 01-Sep the master was dated 20-June -- 73 days stale. Movers_alarm builds
    the scanned universe from it, so every stock listed after 20-June simply did
    not exist to Super Stocks, Scanner1 or the alarm.

    LALITHAA is the proof. Lalithaa Jewellery Mart traded 2,073,106 shares worth
    Rs 55.06 crore in its first twelve minutes and ran +2.26% in the 09:31-09:35
    window Sri asked about. It passed every price and liquidity test this project
    has. It was never scanned once, because it is not in that file. It reached
    the Board at all only because the ScanX movers lists return security IDs
    directly and never consult the master.

    A stale master is silent: nothing errors, the tab just never mentions a whole
    class of stock. That is the worst kind of fault this project has had, and it
    is the third time this shape of bug has appeared (first2_val's all-day
    verdict, the session-average liquidity floor, now this).

SAFETY -- THE OLD FILE IS NEVER LOST
    The download goes to a temp file and is VALIDATED before anything is
    replaced: right header, enough rows, and a set of stocks that must be
    present. Only then is the live file swapped, with the previous one kept as
    security_id_list.prev.csv. A failed download leaves the working file exactly
    as it was and says so in the log. There is no state in which this module
    leaves the board without a master.
"""
import csv
import io
import json
import os
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE / "security_id_list.csv"
PREV = HERE / "security_id_list.prev.csv"
TMP = HERE / "security_id_list.download.csv"
STAMP = HERE / "logs" / "master_refresh.json"
STAMP.parent.mkdir(parents=True, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))

# Dhan publish the scrip master as a plain CSV, no auth. The DETAILED file is
# the one this project's code reads -- it is the only one with the SEM_* column
# names engine.py, Movers_alarm and Scanner1 all index by.
# TWO FILES, AND THEY DO NOT SHARE A SCHEMA.
#     The compact master is the one this project's existing 233,345-row file
#     came from -- it carries the SEM_* column names engine.py, Movers_alarm and
#     Scanner1 all index by. The detailed master is a NEWER, differently-named
#     schema; asking for it first on 01-Sep produced
#         "missing columns ['SEM_EXM_EXCH_ID', 'SEM_SMST_SECURITY_ID', ...]"
#     and the download was correctly refused rather than overwriting a working
#     file with one nothing could read. Compact goes first; detailed is a
#     fallback and gets ALIASED into the SEM_* names on the way in.
SOURCES = [
    ("Dhan scrip master (compact, SEM_* schema)",
     "https://images.dhan.co/api-data/api-scrip-master.csv"),
    ("Dhan scrip master (detailed)",
     "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"),
]

# WHATEVER ARRIVES, WHAT IS WRITTEN OUT IS ALWAYS THE SEM_* SCHEMA.
# The rest of the project reads this file by column name, so normalising here is
# what keeps a schema change at Dhan from becoming a code change in six modules.
ALIASES = {
    "SEM_EXM_EXCH_ID":       ("EXCH_ID", "EXCHANGE_ID", "EXCHANGE"),
    "SEM_SEGMENT":           ("SEGMENT",),
    "SEM_SMST_SECURITY_ID":  ("SECURITY_ID", "SECURITYID"),
    "SEM_INSTRUMENT_NAME":   ("INSTRUMENT", "INSTRUMENT_NAME"),
    "SEM_TRADING_SYMBOL":    ("UNDERLYING_SYMBOL", "SYMBOL_NAME", "TRADING_SYMBOL",
                              "SYMBOL"),
    "SEM_SERIES":            ("SERIES", "INSTRUMENT_TYPE"),
    "SM_SYMBOL_NAME":        ("DISPLAY_NAME", "SECURITY_NAME", "NAME"),
    "SEM_LOT_UNITS":         ("LOT_SIZE", "LOT_UNITS"),
    "SEM_TICK_SIZE":         ("TICK_SIZE",),
    "SEM_EXPIRY_DATE":       ("SM_EXPIRY_DATE", "EXPIRY_DATE"),
    "SEM_STRIKE_PRICE":      ("STRIKE_PRICE",),
    "SEM_OPTION_TYPE":       ("OPTION_TYPE",),
    "SEM_CUSTOM_SYMBOL":     ("DISPLAY_NAME", "CUSTOM_SYMBOL"),
    "SEM_EXPIRY_CODE":       ("EXPIRY_CODE",),
    "SEM_EXPIRY_FLAG":       ("EXPIRY_FLAG",),
    "SEM_EXCH_INSTRUMENT_TYPE": ("EXCH_INSTRUMENT_TYPE", "INSTRUMENT_TYPE"),
}
OUT_COLS = ["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID",
            "SEM_INSTRUMENT_NAME", "SEM_EXPIRY_CODE", "SEM_TRADING_SYMBOL",
            "SEM_LOT_UNITS", "SEM_CUSTOM_SYMBOL", "SEM_EXPIRY_DATE",
            "SEM_STRIKE_PRICE", "SEM_OPTION_TYPE", "SEM_TICK_SIZE",
            "SEM_EXPIRY_FLAG", "SEM_EXCH_INSTRUMENT_TYPE", "SEM_SERIES",
            "SM_SYMBOL_NAME"]
PROBE = HERE / "logs" / "master_probe.txt"

# DO NOT RE-DOWNLOAD 30 MB EVERY FIVE MINUTES ON A FAILURE.
# The first version did exactly that, and the log shows it firing twice inside
# six seconds at startup. A failed refresh is not urgent -- the working file is
# still in use -- so it waits.
RETRY_AFTER_FAIL = 3600

HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
       "Accept": "text/csv,*/*"}

# VALIDATION. A truncated or redirected download must never overwrite a working
# master. These are the columns the rest of the project indexes by name.
NEED_COLS = ["SEM_EXM_EXCH_ID", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME",
             "SEM_TRADING_SYMBOL", "SEM_SERIES", "SM_SYMBOL_NAME"]
# Large, permanently listed NSE EQ names. If any is missing the file is wrong,
# whatever its size.
NEED_SYMS = {"RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN", "ITC"}
MIN_ROWS = 50_000
MIN_NSE_EQ = 1_500
TIMEOUT = 180
MAX_AGE_DAYS = 3

_lock = threading.Lock()
_state = {"checked": None, "refreshed": None, "age_days": None, "rows": None,
          "nse_eq": None, "err": None, "source": None, "new_symbols": None,
          "failed_at": 0}


def file_age_days(p=LIVE):
    try:
        return round((time.time() - p.stat().st_mtime) / 86400.0, 1)
    except OSError:
        return None


def _colmap(cols):
    """Map whatever headers arrived onto the SEM_* names, or raise saying which
    could not be resolved -- with the real header, so the next attempt is
    informed rather than another guess."""
    have = {c.strip().upper(): c for c in cols}
    out = {}
    for want, alts in ALIASES.items():
        if want in have:
            out[want] = have[want]
            continue
        for a in alts:
            if a in have:
                out[want] = have[a]
                break
    missing = [c for c in NEED_COLS if c not in out]
    if missing:
        try:
            PROBE.write_text("header actually served:\n" + ", ".join(cols)
                             + "\n\ncould not resolve: " + ", ".join(missing),
                             encoding="utf-8")
        except OSError:
            pass
        raise ValueError(f"cannot map columns {missing}; served header was "
                         f"[{', '.join(list(cols)[:12])}...] "
                         f"(full header written to {PROBE.name})")
    return out


def _nse_eq_symbols(path):
    """(count, set) of NSE EQ-series equities -- the universe the alarm builds."""
    out = set()
    with open(path, encoding="utf-8", errors="ignore", newline="") as f:
        rdr = csv.DictReader(f)
        cols = rdr.fieldnames or []
        cm = _colmap(cols)
        if any(cm[c] != c for c in NEED_COLS):
            return _nse_eq_via_map(path, cm)
        n = 0
        for r in rdr:
            n += 1
            if (r.get("SEM_EXM_EXCH_ID") == "NSE"
                    and r.get("SEM_INSTRUMENT_NAME") == "EQUITY"
                    and r.get("SEM_SERIES") == "EQ"):
                sym = (r.get("SEM_TRADING_SYMBOL") or "").strip().upper()
                if sym:
                    out.add(sym)
    return n, out


def _nse_eq_via_map(path, cm):
    out, n = set(), 0
    with open(path, encoding="utf-8", errors="ignore", newline="") as f:
        for r in csv.DictReader(f):
            n += 1
            if (r.get(cm["SEM_EXM_EXCH_ID"]) == "NSE"
                    and str(r.get(cm["SEM_INSTRUMENT_NAME"]) or "").upper() == "EQUITY"
                    and r.get(cm["SEM_SERIES"]) == "EQ"):
                sym = (r.get(cm["SEM_TRADING_SYMBOL"]) or "").strip().upper()
                if sym:
                    out.add(sym)
    return n, out


def _normalise(src, dst):
    """Rewrite an aliased file into the SEM_* schema the project reads."""
    with open(src, encoding="utf-8", errors="ignore", newline="") as f:
        rdr = csv.DictReader(f)
        cm = _colmap(rdr.fieldnames or [])
        if all(cm.get(c) == c for c in OUT_COLS if c in cm) and \
                all(c in (rdr.fieldnames or []) for c in NEED_COLS):
            return False                      # already the right shape
        with open(dst, "w", encoding="utf-8", newline="") as g:
            w = csv.DictWriter(g, fieldnames=OUT_COLS)
            w.writeheader()
            for r in rdr:
                w.writerow({c: (r.get(cm[c], "") if c in cm else "") for c in OUT_COLS})
    return True


def _validate(path):
    rows, syms = _nse_eq_symbols(path)
    if rows < MIN_ROWS:
        raise ValueError(f"only {rows:,} rows (expected {MIN_ROWS:,}+) -- truncated?")
    if len(syms) < MIN_NSE_EQ:
        raise ValueError(f"only {len(syms):,} NSE EQ stocks (expected {MIN_NSE_EQ:,}+)")
    missing = NEED_SYMS - syms
    if missing:
        raise ValueError(f"blue chips missing: {sorted(missing)}")
    return rows, syms


def refresh(log=lambda m: None, force=False):
    """Download, validate, then swap. Returns True only if the file changed."""
    age = file_age_days()
    with _lock:
        _state["checked"] = datetime.now(IST).strftime("%d-%b %H:%M")
        _state["age_days"] = age
    last_fail = _state.get("failed_at") or 0
    if not force and last_fail and (time.time() - last_fail) < RETRY_AFTER_FAIL:
        log(f"master: last refresh failed {int((time.time()-last_fail)/60)} min ago -- "
            f"not retrying for another "
            f"{int((RETRY_AFTER_FAIL-(time.time()-last_fail))/60)} min")
        return False
    if not force and age is not None and age <= MAX_AGE_DAYS:
        log(f"master: security_id_list.csv is {age} days old -- fresh enough")
        return False

    before = set()
    if LIVE.exists():
        try:
            _r, before = _nse_eq_symbols(LIVE)
        except Exception:
            before = set()

    last_err = None
    for name, url in SOURCES:
        try:
            log(f"master: downloading {name} ...")
            req = urllib.request.Request(url, headers=HDR)
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r, TMP.open("wb") as f:
                shutil.copyfileobj(r, f)
            mb = TMP.stat().st_size / 1e6
            norm = HERE / "security_id_list.normalised.csv"
            if _normalise(TMP, norm):
                log(f"master: {name} uses a different schema -- normalised to SEM_*")
                os.replace(str(norm), str(TMP))
            rows, syms = _validate(TMP)          # raises if anything is off
            added = sorted(syms - before)
            gone = sorted(before - syms)
            # ATOMIC-ish SWAP, previous kept. Nothing is deleted.
            if LIVE.exists():
                try:
                    if PREV.exists():
                        PREV.unlink()
                except OSError:
                    pass
                try:
                    os.replace(str(LIVE), str(PREV))
                except OSError:
                    shutil.copy2(str(LIVE), str(PREV))
            os.replace(str(TMP), str(LIVE))
            with _lock:
                _state.update({"refreshed": datetime.now(IST).strftime("%d-%b %H:%M"),
                               "age_days": 0.0, "rows": rows, "nse_eq": len(syms),
                               "err": None, "source": name,
                               "new_symbols": added[:40]})
            try:
                STAMP.write_text(json.dumps(
                    {"at": _state["refreshed"], "rows": rows, "nse_eq": len(syms),
                     "added": added, "removed": gone, "source": name}, indent=1),
                    encoding="utf-8")
            except OSError:
                pass
            log(f"master: REPLACED in {time.time()-t0:.0f}s -- {mb:.0f} MB, "
                f"{rows:,} rows, {len(syms):,} NSE EQ stocks "
                f"(+{len(added)} new, -{len(gone)} gone). "
                f"previous kept as {PREV.name}")
            if added:
                log(f"master: newly scannable -> {', '.join(added[:20])}"
                    + (f" (+{len(added)-20} more)" if len(added) > 20 else ""))
            return True
        except Exception as e:
            last_err = f"{name}: {type(e).__name__} {str(e)[:120]}"
            log(f"master: {last_err}")
            try:
                if TMP.exists():
                    TMP.unlink()
            except OSError:
                pass
    with _lock:
        _state["err"] = last_err
        _state["failed_at"] = time.time()
    log("master: refresh FAILED -- the existing file is untouched and still in use")
    return False


def summary():
    with _lock:
        s = dict(_state)
    s["age_days"] = file_age_days()
    s["stale"] = bool(s["age_days"] is not None and s["age_days"] > MAX_AGE_DAYS)
    s["path"] = LIVE.name
    return s


def loop(log=lambda m: None, stop=None):
    """Check once at startup, then once a day at 08:15 IST -- an hour before he
    reads the board, and well before 09:15."""
    done = {"day": None}
    first = True
    while not (stop and stop()):
        try:
            now = datetime.now(IST)
            day = now.strftime("%Y%m%d")
            if first or (done["day"] != day and now.strftime("%H:%M") >= "08:15"):
                refresh(log=log)
                done["day"] = day
                first = False
        except Exception as e:
            log(f"master loop: {type(e).__name__} {str(e)[:110]}")
        time.sleep(300)


if __name__ == "__main__":
    import sys
    print("before:", json.dumps(summary(), indent=1))
    refresh(log=print, force=("--force" in sys.argv))
    print("after :", json.dumps(summary(), indent=1))
