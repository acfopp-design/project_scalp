"""morning_check.py -- the one thing to run before the market opens.

Read-only. Places nothing, changes nothing, starts nothing. It answers the
only question that matters at 09:00: is this machine in a state where the
engine will actually work today, and if not, what exactly do I fix?

Written 30-Sep for Sri, who asked for morning steps rather than commands.
MORNING_CHECK.bat wraps this, so the output has to tell a non-technical
reader what to DO, not just what is wrong.
"""
import os, sys
from datetime import datetime
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(HERE))

FIX = []


def line(n, label, value, ok=None, fix=None):
    dots = "." * max(3, 26 - len(label))
    mark = "" if ok is None else ("   OK" if ok else "   <-- FIX THIS")
    print("  %s %s %s %s%s" % (n, label, dots, value, mark))
    if ok is False and fix:
        FIX.append(fix)


print("=" * 66)
print("  MORNING CHECK      %s" % datetime.now().strftime("%a %d-%b-%Y  %H:%M:%S"))
print("=" * 66)

# --- 1/2. the IP whitelist, the thing that silently breaks order placement --
try:
    import ip_check
    mine = ip_check.current_ipv4()
    want = ip_check.whitelisted()
    if not mine:
        line("1.", "Internet", "NO REPLY", False,
             "No internet. Check your connection before anything else.")
    else:
        line("1.", "Internet", "OK", True)
        line("2.", "This machine's IP", mine)
        line("  ", "Whitelisted in env.txt", ", ".join(want) or "(nothing set)")
        if not want:
            line("  ", "Match", "NOT SET", False,
                 "env.txt has no DHAN_WHITELIST_IP. Tell Claude your IP is %s." % mine)
        elif mine in want:
            line("  ", "Match", "YES", True)
        else:
            line("  ", "Match", "NO", False,
                 "Your IP changed to %s (env.txt expects %s). Dhan will REJECT "
                 "orders. Add %s on the Dhan portal under Static IP Setting, "
                 "then tell Claude. NOTE: Dhan only allows one change every 7 "
                 "days." % (mine, ", ".join(want), mine))
except Exception as e:
    line("1.", "IP check", "unavailable (%s)" % str(e)[:30], False,
         "ip_check.py could not run. Tell Claude.")

# --- 3. the token, which expires every single day --------------------------
try:
    import broker
    th = broker.token_hours_left()
    if th is None:
        line("3.", "Dhan token", "CANNOT READ", False,
             "Could not read the Dhan access token. Paste a fresh one into "
             "env.txt.")
    else:
        line("3.", "Dhan token", "%.1f hours left" % th, th > 7,
             "Token has only %.1f hours left -- it will die mid-session. "
             "Generate a fresh one on the Dhan portal and paste it into "
             "env.txt now." % th)

    # --- 4. which mode today ----------------------------------------------
    want_m = broker.configured_mode()
    armed = broker.armed()
    line("4.", "env.txt BROKER_MODE", want_m)
    line("  ", "env.txt LIVE_ARMED", "YES" if armed else "NO")

    if want_m == "LIVE" and armed:
        import live_exec
        c = live_exec.cfg()
        line("5.", "Shares per position", c["max_qty"], c["max_qty"] <= 5,
             "LIVE_MAX_QTY is %s. Set it to 1 in env.txt for a test day."
             % c["max_qty"])
        line("  ", "Skip shares dearer than", "Rs %.0f" % c["max_entry_value"])
        line("  ", "Broker stop", "%.2f%%" % c["sl_pct"])
        line("  ", "New entries", "BLOCKED" if c["no_new"] else "allowed")
        RUN = "LIVE_START.bat      *** REAL MONEY ***"
    else:
        line("5.", "Trading with", "paper money", True)
        RUN = "EYE_START.bat"
except Exception as e:
    line("3.", "Broker check", "unavailable (%s)" % str(e)[:30], False,
         "broker.py could not run. Tell Claude.")
    RUN = "nothing -- fix the errors above first"

# --- 6. warm-up tape. Without it the board is blind until ~09:40 -----------
try:
    today = datetime.now().strftime("%Y%m%d")
    days = sorted(d.name for d in (HERE / "logs" / "tape_live").iterdir()
                  if d.is_dir() and d.name != today)
    if days:
        line("6.", "Warm-up tape", "%s found" % days[-1], True)
    else:
        line("6.", "Warm-up tape", "MISSING", False,
             "No previous session's tape. The board will be blind until about "
             "09:40. Not dangerous, but expect no signals early.")
except Exception:
    line("6.", "Warm-up tape", "could not check")

print()
if FIX:
    print("  " + "!" * 62)
    print("  !!  NOT READY -- %d thing(s) to fix:" % len(FIX))
    print("  " + "!" * 62)
    for i, f in enumerate(FIX, 1):
        print()
        print("   %d) %s" % (i, f))
    print()
    print("  Fix these, then run MORNING_CHECK.bat again.")
else:
    print("  " + "=" * 62)
    print("   READY.   Now run:  %s" % RUN)
    print("  " + "=" * 62)
print()
sys.exit(1 if FIX else 0)
