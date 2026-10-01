"""version_snap.py -- freeze the logic that traded a day, with its result.

    python version_snap.py snap [YYYYMMDD]   # freeze today's logic + P&L
    python version_snap.py list              # every frozen version
    python version_snap.py diff vA vB        # what changed in the settings
    python version_snap.py restore vX        # put a version's files back live

Sri, 24-Sep: "Take todays logic version as backup if you are changing that.
We will have to compare the profits with future changes logic version."
Without this every comparison is against a moving target -- which is exactly how
the shorts decision got made on a stale baseline twice.
"""
import sys, os, json, shutil, hashlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
VDIR = HERE / "versions"
# the files that actually decide a trade
SRC = ["eye_strategy.py", "eye_cfg.py", "paper_live.py", "tune_v6.py",
       "live_shadow.py", "paper_engine.py", "funnel.py", "leverage.py"]
# the settings worth showing in a diff, as (module, attribute)
KNOBS = [("paper_live", "SLOTS"), ("paper_live", "RANK"),
         ("paper_live", "COOLDOWN_MIN"), ("paper_live", "STOP_PCT"),
         ("paper_live", "TRAIL_PCT"), ("paper_live", "MIN_HOLD"),
         ("eye_strategy", "MIN_LEG"), ("eye_strategy", "SELECT_MIN"),
         ("eye_strategy", "EXITMODE"), ("eye_strategy", "SHORTS"),
         ("eye_strategy", "LAST_ENTRY"), ("eye_strategy", "MIN_UP"),
         ("eye_strategy", "MAX_OFF"), ("eye_strategy", "MAX_UP"),
         ("eye_strategy", "OPEN_FREE"), ("eye_strategy", "VOLX_MIN")]


def _knobs():
    import importlib
    out = {}
    for mod, name in KNOBS:
        try:
            m = importlib.import_module(mod)
            out[f"{mod}.{name}"] = getattr(m, name, None)
        except Exception as e:
            out[f"{mod}.{name}"] = f"<{type(e).__name__}>"
    return out


def _result(day):
    f = HERE / "logs" / f"paper_live_{day}.json"
    if not f.exists():
        return None
    s = json.loads(f.read_text(encoding="utf-8")).get("summary", {})
    return {k: s.get(k) for k in
            ("net", "net_pct", "trades", "done_n", "wins", "losses",
             "win_pct", "gross", "charges", "last_tick")}


def snap(day=None):
    day = day or datetime.now().strftime("%Y%m%d")
    tag = f"v{day[:4]}-{day[4:6]}-{day[6:]}"
    d = VDIR / tag
    if d.exists():                       # never silently overwrite a version
        n = 2
        while (VDIR / f"{tag}-{n}").exists():
            n += 1
        tag, d = f"{tag}-{n}", VDIR / f"{tag}-{n}"
    (d / "src").mkdir(parents=True, exist_ok=True)
    files = {}
    for f in SRC:
        p = HERE / f
        if not p.exists():
            continue
        shutil.copy2(p, d / "src" / f)
        files[f] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    man = {"tag": tag, "day": day, "frozen_at": datetime.now().isoformat(timespec="seconds"),
           "knobs": _knobs(), "result": _result(day), "sha256": files}
    (d / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    r = man["result"]
    print(f"frozen {tag}")
    if r:
        print(f"  net Rs {r['net']:,.0f} ({r['net_pct']}%) | {r['trades']} trades | win {r['win_pct']}%")
    else:
        print("  no P&L for that day yet")
    return tag


def _load(tag):
    f = VDIR / tag / "manifest.json"
    if not f.exists():
        sys.exit(f"no such version: {tag}")
    return json.loads(f.read_text(encoding="utf-8"))


def lst():
    if not VDIR.exists():
        print("no versions yet"); return
    rows = sorted(p.name for p in VDIR.iterdir() if (p / "manifest.json").exists())
    print(f"{'version':<14}{'net Rs':>11}{'%':>8}{'trades':>8}{'win%':>6}  knobs")
    for t in rows:
        m = _load(t); r = m.get("result") or {}
        k = m.get("knobs", {})
        brief = (f"slots={k.get('paper_live.SLOTS')} rank={k.get('paper_live.RANK')} "
                 f"leg={k.get('eye_strategy.MIN_LEG')} shorts={k.get('eye_strategy.SHORTS')} "
                 f"last={k.get('eye_strategy.LAST_ENTRY')}")
        print(f"{t:<14}{(r.get('net') or 0):>11,.0f}{(r.get('net_pct') or 0):>8}"
              f"{(r.get('trades') or 0):>8}{(r.get('win_pct') or 0):>6}  {brief}")


def diff(a, b):
    ka, kb = _load(a)["knobs"], _load(b)["knobs"]
    print(f"{'setting':<28}{a:>16}{b:>16}")
    for k in sorted(set(ka) | set(kb)):
        va, vb = ka.get(k), kb.get(k)
        if va != vb:
            print(f"{k:<28}{str(va):>16}{str(vb):>16}")
    ra, rb = _load(a).get("result") or {}, _load(b).get("result") or {}
    print()
    print(f"{'net Rs':<28}{(ra.get('net') or 0):>16,.0f}{(rb.get('net') or 0):>16,.0f}")
    print(f"{'trades':<28}{(ra.get('trades') or 0):>16}{(rb.get('trades') or 0):>16}")
    print(f"{'win %':<28}{(ra.get('win_pct') or 0):>16}{(rb.get('win_pct') or 0):>16}")


def restore(tag):
    d = VDIR / tag / "src"
    if not d.exists():
        sys.exit(f"no source in {tag}")
    for f in sorted(os.listdir(d)):
        shutil.copy2(d / f, HERE / f)
        print(f"  restored {f}")
    print(f"{tag} is live again -- the engine reloads within ~45s")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "snap":    snap(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "list":  lst()
    elif cmd == "diff":  diff(sys.argv[2], sys.argv[3])
    elif cmd == "restore": restore(sys.argv[2])
    else: sys.exit(__doc__)
