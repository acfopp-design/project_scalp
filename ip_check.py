"""ip_check.py -- is this machine's public IP still the one Dhan has whitelisted?

WHY THIS EXISTS (29-Sep)
    Dhan requires a whitelisted IP for ORDER PLACEMENT (market data is fine
    without one). Two facts make a silent mismatch expensive:

      1. api.dhan.co publishes ONLY A records -- no AAAA. Verified 29-Sep:
             api.dhan.co     -> 18.238.136.6 / .45 / .52 / .87   (IPv4 only)
             sandbox.dhan.co -> 13.200.62.216 / 13.206.148.228 / 15.252.178.117
         So the connection to Dhan ALWAYS egresses over IPv4. Whitelisting an
         IPv6 address can never match, however stable that address is.

      2. Dhan allows a re-set only once every 7 days. If the IP rotates on a
         Tuesday you cannot fix it until the following Tuesday.

    Therefore: check the public IPv4 before arming live, every single day.

CONFIG (env.txt)
    DHAN_WHITELIST_IP = 49.43.246.104          # or "ip1,ip2" for both slots

BEHAVIOUR
    PAPER / SANDBOX ... advisory only, never blocks the daily paper run.
    LIVE ............. a mismatch is a hard pre-flight failure.
"""
import ipaddress, json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV  = os.path.join(HERE, "env.txt")
TIMEOUT = 6

# IPv4-only endpoints on purpose. api64.* would hand back an IPv6 address and
# hide the very mismatch this module is here to catch.
SOURCES = ("https://api.ipify.org?format=json",
           "https://ipv4.icanhazip.com")


def _env(path=ENV):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                v = v.strip()
                if v and not v.startswith("PASTE_"):
                    out[k.strip().upper()] = v
    except OSError:
        pass
    return out


def current_ipv4():
    """Public IPv4 as the outside world sees it, or None if unreachable."""
    for url in SOURCES:
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                body = r.read().decode("utf-8", "ignore").strip()
            ip = json.loads(body)["ip"] if body.startswith("{") else body
            ip = ip.strip()
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address):
                return ip
        except Exception:
            continue
    return None


def whitelisted():
    """The IPs configured in env.txt, in order."""
    raw = _env().get("DHAN_WHITELIST_IP", "")
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def check():
    """-> (status, message). status in OK MISMATCH UNSET BADCONFIG UNKNOWN."""
    want = whitelisted()
    if not want:
        return "UNSET", ("DHAN_WHITELIST_IP is not set in env.txt -- "
                         "cannot verify Dhan will accept an order")

    bad6 = [w for w in want if ":" in w]
    if bad6:
        return "BADCONFIG", (f"{bad6[0]} is IPv6. api.dhan.co has no AAAA "
                             f"record, so an IPv6 entry can NEVER match. "
                             f"Whitelist the IPv4 instead.")

    have = current_ipv4()
    if have is None:
        return "UNKNOWN", "could not reach any IP-echo service"
    if have in want:
        return "OK", f"public IPv4 {have} matches Dhan whitelist"
    return "MISMATCH", (f"public IPv4 is {have}; Dhan has {', '.join(want)}. "
                        f"Live orders WILL be rejected. Dhan allows a re-set "
                        f"only once every 7 days.")


if __name__ == "__main__":
    import sys
    st, msg = check()
    print(f"[{st}] {msg}")
    sys.exit(0 if st == "OK" else 1)
