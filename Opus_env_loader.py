"""
env_loader.py - read Dhan credentials from Project_Scalp/env.txt

Supports either layout:
    Client: 1109798660            |   Client:
    Token: eyJhbGci...            |   1109798660
                                  |   Token:
                                  |   eyJhbGci...

Keys are matched case-insensitively and tolerate 'client', 'client id',
'clientid', 'token', 'access token', 'access-token'.
Returns dict {"client_id": str, "token": str}.
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "env.txt")

_CLIENT_KEYS = ("client", "clientid", "client id", "client-id", "dhanclientid")
_TOKEN_KEYS = ("token", "accesstoken", "access token", "access-token")


def _norm(k):
    return re.sub(r"[^a-z]", "", k.lower())


def load_env(path=ENV_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"env.txt not found at {path}. Create it with two lines:\n"
            "Client: <your client id>\nToken: <your access token>")
    with open(path, encoding="utf-8", errors="ignore") as f:
        raw = [ln.rstrip("\n") for ln in f]

    client = token = None
    lines = [ln.strip() for ln in raw]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln:
            i += 1
            continue
        # "Key: value" on one line?
        if ":" in ln:
            key, _, val = ln.partition(":")
            nk = _norm(key)
            val = val.strip()
            target = "client" if nk.startswith("client") or nk in (_norm(k) for k in _CLIENT_KEYS) else \
                     ("token" if nk.startswith("token") or "token" in nk else None)
            if target:
                if not val:  # value is on the NEXT non-empty line
                    j = i + 1
                    while j < len(lines) and not lines[j]:
                        j += 1
                    val = lines[j].strip() if j < len(lines) else ""
                    i = j
                if target == "client" and not client:
                    client = val
                elif target == "token" and not token:
                    token = val
        i += 1

    if not client or not token:
        raise ValueError(
            f"Could not parse client/token from {path}. "
            f"Got client={'set' if client else 'MISSING'}, "
            f"token={'set' if token else 'MISSING'}.")
    # sanity: token is a long JWT; client id is digits
    client = re.sub(r"\D", "", client) or client
    return {"client_id": client, "token": token}


if __name__ == "__main__":
    e = load_env()
    print("client_id:", e["client_id"])
    print("token:", e["token"][:18] + "..." + e["token"][-6:],
          f"({len(e['token'])} chars)")
