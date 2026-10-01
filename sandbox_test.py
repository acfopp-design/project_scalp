"""Read-only connectivity test against DhanHQ SANDBOX. Places NO orders.

Run from Windows (SANDBOX_TEST.bat) -- the sandbox host is only reachable
from the machine that already talks to Dhan.
"""
import base64, datetime, json, os, ssl, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "env.txt")
BASE = "https://sandbox.dhan.co/v2"


def sandbox_creds(path=ENV):
    """SANDBOX_CLIENT / SANDBOX_JWT from env.txt. Never touches the live keys."""
    out = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in ("SANDBOX_CLIENT", "SANDBOX_JWT") and v and not v.startswith("PASTE_"):
                out[k] = v
    return out.get("SANDBOX_CLIENT"), out.get("SANDBOX_JWT")


def jwt_exp(tok):
    try:
        pl = tok.split(".")[1]
        pl += "=" * (-len(pl) % 4)
        d = json.loads(base64.urlsafe_b64decode(pl))
        ist = datetime.datetime.fromtimestamp(d["exp"], datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        return ist, round((ist - now).total_seconds() / 3600, 1), d.get("dhanClientId")
    except Exception:
        return None, None, None


def get(path, cid, tok):
    req = urllib.request.Request(
        BASE + path,
        headers={"access-token": tok, "client-id": cid,
                 "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


def main():
    cid, tok = sandbox_creds()
    print("=" * 66, flush=True)
    print("DhanHQ SANDBOX connectivity test  (read-only, no orders placed)")
    print("=" * 66)
    if not cid or not tok:
        print("FAIL  SANDBOX_CLIENT / SANDBOX_JWT not set in env.txt")
        return 1
    exp, hrs, jwt_cid = jwt_exp(tok)
    print("client-id in env.txt :", cid)
    print("client-id in token   :", jwt_cid, "  <-- must match the line above"
          if str(jwt_cid) != str(cid) else "")
    print("token expires IST    :", exp, "(%s hours left)" % hrs)
    print("-" * 66)
    ok = 0
    for path in ("/fundlimit", "/positions", "/holdings", "/orders"):
        print("  -> calling %s ..." % path, flush=True)
        code, body = get(path, cid, tok)
        mark = "OK  " if code == 200 else "FAIL"
        if code == 200:
            ok += 1
        print("%s %-12s HTTP %-4s %s" % (mark, path, code, body[:180].replace("\n", " ")), flush=True)
    print("-" * 66)
    print("%d/4 endpoints answered 200." % ok)
    if ok == 4:
        print("SANDBOX AUTH WORKS -- ready to build the order layer.")
    elif ok == 0:
        print("Nothing answered. Either the token is wrong/expired, or this")
        print("machine cannot reach sandbox.dhan.co.")
    return 0 if ok == 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
