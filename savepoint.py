"""
savepoint.py -- a way back, for every change made to the :5005 board.

WHY
    This board is used with real money every morning. Changes to it have gone
    wrong before -- a NameError took the live app down mid-session, an NSE/BSE
    filter blanked two whole tabs, and a badge rule was retuned four times in
    one day until it fired zero times. In each case the fix was known within
    minutes but getting BACK was the slow part.

    A savepoint is a dated copy of every source file, taken before a change.
    Restoring is one command and does not depend on remembering what changed.

WHAT IS SAVED
    Every .py, .html and .bat in the project root. NOT logs (gigabytes, and
    they are evidence rather than code), NOT the AI_Board replica (it has its
    own life), NOT env.txt -- credentials should not be copied around, and the
    tokens in it expire daily anyway.

LAYOUT
    C:\\Project_Scalp\\savepoints\\savepoint_20260824_204500\\
        MANIFEST.json      what changed, why, and the hash of every file
        Movers_app.py
        Movers_alarm.py
        ...

USAGE
    python savepoint.py create "why I am changing this"
    python savepoint.py list
    python savepoint.py diff  savepoint_20260824_204500
    python savepoint.py restore savepoint_20260824_204500
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "savepoints"
STORE.mkdir(exist_ok=True)

PATTERNS = ("*.py", "*.html", "*.bat")
# Never copied. AI_Board is a separate project; env.txt holds live credentials
# and daily-expiring tokens; savepoints must not nest inside each other.
SKIP_DIRS = {"savepoints", "AI_Board", "logs", "__pycache__", ".git"}
SKIP_FILES = {"env.txt"}
KEEP = 40


def _files():
    out = []
    for pat in PATTERNS:
        for p in sorted(ROOT.glob(pat)):
            if p.name in SKIP_FILES or p.parent.name in SKIP_DIRS:
                continue
            out.append(p)
    return out


def _hash(p):
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def create(reason="", log=print):
    """Copy the current source into a dated folder. Returns the folder."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = STORE / f"savepoint_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    man = {"created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "reason": reason or "(no reason given)",
           "files": {}}
    for p in _files():
        shutil.copy2(p, dest / p.name)
        man["files"][p.name] = {"sha": _hash(p), "bytes": p.stat().st_size}
    (dest / "MANIFEST.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    log(f"savepoint: {dest.name}  ({len(man['files'])} files)  -- {man['reason']}")
    _prune(log)
    return dest


def _prune(log=print):
    """Keep the most recent KEEP savepoints. Old source is cheap but not free."""
    try:
        sps = sorted(STORE.glob("savepoint_*"))
        for old in sps[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def listing(log=print):
    sps = sorted(STORE.glob("savepoint_*"))
    if not sps:
        log("  no savepoints yet")
        return []
    log(f"  {len(sps)} savepoints:\n")
    for p in sps:
        try:
            m = json.loads((p / "MANIFEST.json").read_text(encoding="utf-8"))
            log(f"  {p.name}   {m['created']}   {len(m['files'])} files")
            log(f"      {m['reason']}")
        except Exception:
            log(f"  {p.name}   (manifest unreadable)")
    return sps


def _resolve(name):
    p = STORE / name if not name.startswith(str(STORE)) else Path(name)
    if not p.exists():
        cand = sorted(STORE.glob(f"*{name}*"))
        if len(cand) == 1:
            return cand[0]
        return None
    return p


def diff(name, log=print):
    """What differs between a savepoint and the files on disk right now."""
    sp = _resolve(name)
    if not sp:
        log(f"  no savepoint matching '{name}'")
        return None
    m = json.loads((sp / "MANIFEST.json").read_text(encoding="utf-8"))
    now = {p.name: _hash(p) for p in _files()}
    changed, added, removed = [], [], []
    for fn, meta in m["files"].items():
        if fn not in now:
            removed.append(fn)
        elif now[fn] != meta["sha"]:
            changed.append(fn)
    for fn in now:
        if fn not in m["files"]:
            added.append(fn)
    log(f"  vs {sp.name} ({m['created']})")
    for fn in changed:
        log(f"    CHANGED  {fn}")
    for fn in added:
        log(f"    ADDED    {fn}")
    for fn in removed:
        log(f"    DELETED  {fn}")
    if not (changed or added or removed):
        log("    identical")
    return {"changed": changed, "added": added, "removed": removed}


def restore(name, log=print):
    """Put a savepoint back.

    Takes a savepoint of the CURRENT state first. Restoring is itself a change,
    and undoing an undo has been needed before.
    """
    sp = _resolve(name)
    if not sp:
        log(f"  no savepoint matching '{name}'")
        return False
    create(reason=f"auto-taken before restoring {sp.name}", log=log)
    n = 0
    for p in sp.glob("*"):
        if p.name == "MANIFEST.json":
            continue
        shutil.copy2(p, ROOT / p.name)
        n += 1
    log(f"  restored {n} files from {sp.name}")
    log("  RESTART the board for this to take effect.")
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    arg = " ".join(sys.argv[2:])
    if cmd == "create":
        create(arg)
    elif cmd == "list":
        listing()
    elif cmd == "diff":
        diff(arg or "")
    elif cmd == "restore":
        if not arg:
            print("  usage: python savepoint.py restore savepoint_20260824_204500")
        else:
            restore(arg)
    else:
        print(__doc__)
