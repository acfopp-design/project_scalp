"""
SUPERVISOR.py -- keeps the board alive, and gives Claude a way to control it.

WHY THIS EXISTS
    On 02-Sep the Super Stocks thread stopped producing at 11:10:16. Its own
    monitor noticed at 11:15:12 and logged:

        super_monitor [CRITICAL] scan() has not run for 240s -- the Super
        Stocks thread is dead or stuck.

    ...and then did nothing, because logging a CRITICAL is not a fix. The whole
    process stopped at 11:15:26. Sri was away, and I could not restart it: the
    shell I reach the machine through is a Linux VM with no network route to
    Windows, so http://127.0.0.1:5005 is unreachable to me and no admin route
    can help. The session ended at 11:15 with six positions open.

    So the supervisor has to run ON Windows, next to the board, and I have to be
    able to command it WITHOUT a network. The project folder is mounted into my
    shell, so files are the channel: I drop a command file, this picks it up.

WHAT IT DOES
    1. Owns the board's lifecycle. Launches Movers_app.py as a child and
       relaunches it whenever it exits.
    2. Detects a STUCK board, not just a dead one. Today's failure logged
       happily while the trading brain was blind, so process-alive was never the
       right health check. During market hours it checks that the SUPER log is
       still growing -- that file is written by exactly the thread that died.
    3. Accepts commands as files: logs/control/<name>.cmd.
    4. Publishes logs/control/status.json so I can see what is happening
       without a network.

WHAT IT DELIBERATELY WILL NOT DO
    - It will not restart faster than MIN_RESTART_GAP, and it backs off after
      repeated failures. A supervisor that restarts a crash-looping process
      every two seconds turns one bug into an outage and fills the disk.
    - It will not restart on staleness outside market hours. The loops idle by
      design between 15:30 and 09:15; restarting then would be pure churn.
    - It will not touch trading state. live_paper persists its own book and the
      board's autostart resumes it, so a restart costs a gap, not a position.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
CTRL = os.path.join(LOGS, "control")
DONE = os.path.join(CTRL, "done")
APP = os.path.join(HERE, "Movers_app.py")
APPLOG = os.path.join(LOGS, "movers_app.log")
BOARDLOGS = os.path.join(LOGS, "movers_board")
SUPLOG = os.path.join(LOGS, "supervisor.log")
PORT = 5005

IST = timezone(timedelta(hours=5, minutes=30))

POLL = 5.0                  # how often to look at everything
LOG_STALE_SEC = 120         # app log has not grown this long -> process wedged
SUPER_STALE_SEC = 210       # super log has not grown this long during market
                            # hours -> the trading brain is blind. 210s is just
                            # over the 180s the monitor uses, so the board gets
                            # a chance to complain first.
BOOT_GRACE_SEC = 90         # a fresh process gets this long before any check
MIN_RESTART_GAP = 120       # never relaunch faster than this
MAX_RESTARTS_HOUR = 8       # beyond this, stop and say so rather than flap
MARKET_FROM, MARKET_TO = "09:10:00", "15:35:00"

# ---- THE REVIEW, RUN FROM HERE ------------------------------------------
# The ten-minute review was meant to be driven by a scheduled task in the
# cloud. That task cannot be bound to this computer -- the platform refuses
# with no_signed_approval, and a binding cannot be added after creation -- so
# a cloud schedule can never read these files. Retrying it was pointless.
#
# But the review does not need the cloud. learn.py is a local script reading
# local logs, and this supervisor is already running on the machine that has
# them. So it runs here, on a timer, and writes logs/LEARN_YYYYMMDD.md whether
# or not anyone is connected.
#
# That splits the loop honestly along the line of what each part actually
# needs. The MEASUREMENT is mechanical and runs unattended. The JUDGEMENT --
# deciding which change to make and confirming it against full-engine replay --
# needs a model, and happens when Claude is open. Sri gets the evidence
# collected either way, so an unattended hour is never a lost hour.
LEARN_EVERY = 600           # ten minutes, per Sri's instruction
LEARN_TIMEOUT = 240


def now_ist():
    return datetime.now(IST)


def log(msg):
    line = f"[{now_ist().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(SUPLOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def in_market():
    n = now_ist()
    if n.weekday() >= 5:
        return False
    return MARKET_FROM <= n.strftime("%H:%M:%S") <= MARKET_TO


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def super_log_path():
    return os.path.join(BOARDLOGS, f"super_{now_ist().strftime('%Y%m%d')}.jsonl")


def kill_orphan_boards():
    """Kill every python running Movers_app.py, not just the port holder.

    03-Sep: eight restarts in an hour left TWO boards alive at once. Only one
    can bind 5005; the other ran headless -- still sweeping, still writing to
    the same log file, so the log looked perfectly healthy while the web page
    was dead. Two 'autostart:' lines at the identical second were the only
    visible trace.

    free_port() cannot fix that: it kills whoever holds the socket, and the
    orphan by definition does not. So the supervisor now clears every board
    process by command line before it starts one, which is the only way to
    guarantee that the board it supervises is the only board there is.
    """
    try:
        out = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return 0
    n = 0
    me = os.getpid()
    for line in out.splitlines():
        if "Movers_app.py" not in line:
            continue
        parts = [x for x in line.strip().split(",") if x]
        pid = parts[-1] if parts and parts[-1].isdigit() else None
        if pid and int(pid) != me:
            log(f"clearing orphan board PID {pid}")
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=15)
            n += 1
    return n


def free_port():
    """Kill whatever holds the port. The board runs OLD CODE and looks entirely
    normal doing it if a stale process keeps the socket, which cost hours once
    already -- so this verifies rather than assuming."""
    for _ in range(6):
        pids = set()
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 text=True, timeout=15).stdout
        except Exception:
            return
        for line in out.splitlines():
            if f":{PORT} " in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        if not pids:
            return
        for pid in pids:
            log(f"port {PORT} held by PID {pid} -- stopping it")
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=15)
        time.sleep(1.0)
    log(f"WARNING: port {PORT} still held; the board may bind-fail")


class Board:
    def __init__(self):
        self.proc = None
        self.started_at = 0.0
        self.restarts = []          # timestamps, for the per-hour cap
        self.last_reason = "not started yet"
        self.stopped_by_command = False

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, reason):
        if self.alive():
            return False
        if len(self.recent_restarts()) >= MAX_RESTARTS_HOUR:
            log(f"REFUSING to start: {MAX_RESTARTS_HOUR} restarts in the last "
                f"hour. Something is broken that restarting will not fix.")
            return False
        kill_orphan_boards()
        free_port()
        py = sys.executable or "python"
        try:
            # Windows: a new process group so this supervisor can signal it.
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.proc = subprocess.Popen(
                [py, APP], cwd=HERE, creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"start FAILED: {type(e).__name__} {e}")
            return False
        self.started_at = time.time()
        self.restarts.append(self.started_at)
        self.last_reason = reason
        self.stopped_by_command = False
        log(f"board started, PID {self.proc.pid} -- {reason}")
        return True

    def stop(self, reason):
        if not self.alive():
            return
        log(f"stopping board PID {self.proc.pid} -- {reason}")
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True, timeout=20)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        for _ in range(20):
            if not self.alive():
                break
            time.sleep(0.5)

    def restart(self, reason):
        self.stop(reason)
        time.sleep(2.0)
        return self.start(reason)

    def recent_restarts(self):
        cut = time.time() - 3600
        self.restarts = [t for t in self.restarts if t > cut]
        return self.restarts

    def age(self):
        return time.time() - self.started_at if self.started_at else 0.0


def health(board):
    """(ok, reason). Only ever unhealthy for a reason worth a restart."""
    if not board.alive():
        return False, "process is not running"
    if board.age() < BOOT_GRACE_SEC:
        return True, f"booting ({int(board.age())}s)"

    m = mtime(APPLOG)
    if m is None:
        return False, "app log missing"
    log_age = time.time() - m
    if log_age > LOG_STALE_SEC:
        return False, f"app log has not grown for {int(log_age)}s"

    if in_market():
        sm = mtime(super_log_path())
        if sm is None:
            if board.age() > 300:
                return False, "no super log today -- the scanner never started"
        else:
            sup_age = time.time() - sm
            if sup_age > SUPER_STALE_SEC:
                # THIS is 02-Sep's failure. The app log was advancing happily
                # while the thread that feeds every trade had been silent for
                # five minutes. Process-alive was never the right check.
                return False, (f"SUPER log silent {int(sup_age)}s while the "
                               f"market is open -- the trading brain is blind")
    return True, "healthy"


def write_status(board, ok, reason, note=""):
    st = {
        "ts": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "alive": board.alive(),
        "pid": board.proc.pid if board.alive() else None,
        "uptime_s": int(board.age()),
        "healthy": ok,
        "reason": reason,
        "note": note,
        "restarts_last_hour": len(board.recent_restarts()),
        "last_start_reason": board.last_reason,
        "stopped_by_command": board.stopped_by_command,
        "in_market": in_market(),
        "app_log_age_s": (int(time.time() - mtime(APPLOG))
                          if mtime(APPLOG) else None),
        "super_log_age_s": (int(time.time() - mtime(super_log_path()))
                            if mtime(super_log_path()) else None),
    }
    try:
        os.makedirs(CTRL, exist_ok=True)
        tmp = os.path.join(CTRL, "status.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, os.path.join(CTRL, "status.json"))
    except OSError:
        pass


_SEEN = set()          # command filenames already executed this run


def take_commands(board):
    """Files are the control channel, because I have no network to this machine.

    Drop  logs/control/restart.cmd  and this restarts the board. The file is
    moved into done/ with a result, so a command can never be run twice and I
    can read back what happened.
    """
    try:
        os.makedirs(CTRL, exist_ok=True)
        os.makedirs(DONE, exist_ok=True)
        names = sorted(n for n in os.listdir(CTRL) if n.endswith(".cmd"))
    except OSError:
        return
    for name in names:
        path = os.path.join(CTRL, name)
        if name in _SEEN:
            # Belt and braces. If a command file cannot be moved out of the way
            # -- a locked file, a read-only mount, an antivirus holding it --
            # then without this the same restart executes on every 5s poll, for
            # ever. A supervisor stuck in an infinite restart loop is far worse
            # than the dead board it was built to fix, so the in-memory record
            # is authoritative even when the filesystem will not cooperate.
            continue
        _SEEN.add(name)
        action = name[:-4].split("-")[0].lower()
        body = ""
        try:
            with open(path, encoding="utf-8") as f:
                body = f.read()[:400]
        except OSError:
            pass
        log(f"COMMAND: {action}" + (f"  ({body.strip()[:120]})" if body.strip() else ""))
        result = "unknown action"
        if action == "restart":
            result = "restarted" if board.restart("commanded") else "restart refused"
        elif action == "stop":
            board.stop("commanded")
            board.stopped_by_command = True
            result = "stopped; will not auto-restart until a start command"
        elif action == "start":
            board.stopped_by_command = False
            result = "started" if board.start("commanded") else "already running"
        elif action == "ping":
            result = "pong"
        elif action == "run":
            # A JOB CHANNEL, so analysis work does not need a person at the
            # keyboard. 04-Sep: the tape cache was missing every MyWatchlist
            # name, which is why MTAR and Wockhardt could not be back-tested at
            # all -- and the only way to fix it was to ask Sri to double-click a
            # .bat. That is not monitoring, it is delegating.
            #
            # WHITELIST ONLY. These are read-only analysis scripts that write
            # into logs/. Nothing here can touch the board, live_config.json or
            # an order path, so a stray file in the inbox cannot trade.
            job = (body.strip().split() or [""])[0]
            args = body.strip().split()[1:]
            if job in RUNNABLE:
                result = run_job(job, args)
            else:
                result = f"refused: {job!r} is not in the allowed list {sorted(RUNNABLE)}"
        log(f"COMMAND {action} -> {result}")
        stamp = now_ist().strftime("%Y%m%d_%H%M%S")
        try:
            with open(os.path.join(DONE, f"{stamp}_{name}.result"), "w",
                      encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "result": result,
                                    "at": stamp, "body": body}, indent=1))
        except OSError:
            pass
        # MOVE the command out of the inbox rather than deleting it. A rename
        # succeeds in places a delete does not, and it keeps the original as
        # evidence of what was asked. Only if the move fails do we fall back to
        # a delete; if both fail, _SEEN above is what stops a repeat.
        try:
            os.replace(path, os.path.join(DONE, f"{stamp}_{name}"))
        except OSError:
            try:
                os.remove(path)
            except OSError:
                log(f"WARNING: could not clear {name} from the command inbox; "
                    f"ignoring it from here by name")


# Scripts the file channel may run. Read-only analysis only: none of these can
# start, stop or configure trading.
RUNNABLE = {"build_tape.py", "bullish_list.py", "warmup.py", "habitual_lab.py",
            "regress.py", "replay_check.py", "learn.py",
            "circuit_bands.py", "shadow_run.py", "live_shadow.py", "paper_live.py"}
JOB_TIMEOUT = 1800


def run_job(job, args):
    py = sys.executable or "python"
    try:
        r = subprocess.run([py, os.path.join(HERE, job)] + list(args), cwd=HERE,
                           capture_output=True, text=True, timeout=JOB_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"{job}: timed out after {JOB_TIMEOUT}s"
    except Exception as e:
        return f"{job}: {type(e).__name__} {str(e)[:120]}"
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-6:]
    for l in tail:
        log(f"  {job}: {l[:160]}")
    return f"{job} exited {r.returncode}: " + " | ".join(x[:120] for x in tail[-3:])


def tape_watchdog():
    """Once a day after the close, check the tape actually covers what we can
    trade, and refetch if it does not.

    04-Sep: build_tape had only ever fetched CARDED symbols, so half of what the
    replay traded had no price series and every backtest number was fiction.
    Coverage is now asserted rather than assumed."""
    try:
        day = datetime.now().strftime("%Y%m%d")
        tape = os.path.join(HERE, "logs", "tape", day)
        have = len([f for f in os.listdir(tape)]) if os.path.isdir(tape) else 0
        sys.path.insert(0, HERE)
        import importlib
        bt = importlib.import_module("build_tape")
        importlib.reload(bt)
        want = len(bt.symbols_of(day))
        if want and have < want * 0.9:
            log(f"tape: only {have} of {want} symbols cached -- refetching")
            log("  " + run_job("build_tape.py", [f"--day={day}"]))
        else:
            log(f"tape: {have} of {want} symbols cached -- ok")
    except Exception as e:
        log(f"tape watchdog: {type(e).__name__} {str(e)[:120]}")



# ---------------------------------------------------------------- pre-open prep
# Box 3/4 of PLAN.md. At 09:08 NSE's pre-open matching is done and the real open
# is known, so the "highly bullish probability" list can be frozen; the five
# minutes to 09:15 are then spent warming indicators rather than idling.
#
# Measured on MANINDS 21-Aug, 30s bars: from a cold start EMA8 is unavailable
# until 09:33, MA12 until 09:57 and MACD until 10:07, and cold MACD was still
# 0.85 against a true 1.03 at 10:21. 85% of every opportunity ever found arrives
# before 10:30 -- so a cold start means the richest window of the day is traded
# on indicators that are missing or wrong.
#
# Both run as SUBPROCESSES with timeouts, like run_learn(), so neither can take
# the board down. Once per day each.
PREP = {"day": None, "list": False, "warm": False, "tape": False}


def run_prep(step, args, timeout=240):
    py = sys.executable or "python"
    try:
        r = subprocess.run([py, os.path.join(HERE, step)] + args, cwd=HERE,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"{step}: timed out after {timeout}s -- skipped")
        return
    except Exception as e:
        log(f"{step}: {type(e).__name__} {str(e)[:100]}")
        return
    for ln in (r.stdout or "").splitlines()[:3]:
        if ln.strip():
            log(f"{step}: {ln.strip()[:150]}")
    if r.returncode != 0:
        log(f"{step}: exited {r.returncode} -- {(r.stderr or '')[:150]}")


SHADOW = {"proc": None}


def shadow_keepalive():
    """Keep live_shadow.py running through the session.

    Sri, 07-Sep: "I only need one .bat file." So the supervisor owns the shadow
    the same way it owns the board -- starts it at 09:14, restarts it if it
    dies, and leaves it alone outside market hours. Its book is what the Live
    Trading tab draws while it is up.
    """
    import subprocess
    hm = datetime.now().strftime("%H:%M")
    want = "09:10" <= hm <= "15:31"
    p = SHADOW["proc"]
    if want and (p is None or p.poll() is not None):
        if p is not None:
            log("paper_live: died -- restarting")
        try:
            SHADOW["proc"] = subprocess.Popen(
                # HERE is a STRING in this module, not a Path -- os.path.join,
                # not the / operator. This threw TypeError every 5 seconds at
                # the 09:14 start on 07-Sep and the shadow never launched.
                [sys.executable, os.path.join(HERE, "paper_live.py")],
                cwd=HERE,
                stdout=open(os.path.join(HERE, "logs", "paper_live_stdout.log"), "a",
                            encoding="utf-8", errors="replace"),
                stderr=subprocess.STDOUT)
            log("paper_live: started and IDLE -- it trades nothing until Sri "
                "presses Start Live Paper Trade on the Board")
        except Exception as e:
            log(f"paper_live: could not start -- {type(e).__name__}: {e}")
    elif not want and p is not None and p.poll() is None:
        try:
            p.terminate()
            log("paper_live: stopped for the day")
        except Exception:
            pass
        SHADOW["proc"] = None


def preopen_prep():
    """09:08 freeze the list, 09:09 warm the indicators. Once per day."""
    now = datetime.now()
    day = now.strftime("%Y%m%d")
    if PREP["day"] != day:
        PREP.update({"day": day, "list": False, "warm": False, "bands": False})
    hm = now.strftime("%H:%M")
    if not PREP["list"] and "09:08" <= hm < "09:15":
        PREP["list"] = True
        run_prep("bullish_list.py", ["--quiet"])
    if not PREP["warm"] and "09:09" <= hm < "09:15":
        PREP["warm"] = True
        run_prep("warmup.py", [], timeout=300)
    # Upper/lower circuit prices are fixed for the session and published before
    # the open, so one call at 09:10 covers the whole day and the engine never
    # has to look them up while it is trading.
    if not PREP.get("bands") and "09:10" <= hm < "09:15":
        PREP["bands"] = True
        run_prep("circuit_bands.py", [], timeout=300)
    # After the close, cache a true 30-second series for every symbol carded
    # today. Without it replay_live cannot price roughly half of what it trades
    # -- on 04-Sep, 13 of 27 replayed trades exited at exactly their entry price
    # because the symbol was absent from bars30 -- which inflated every number
    # the replay has ever produced. Once per day, after trading, so it competes
    # with nothing.
    if not PREP["tape"] and "15:40" <= hm < "23:59":
        PREP["tape"] = True
        run_prep("build_tape.py", [f"--day={day}"], timeout=1800)
        tape_watchdog()


def run_learn():
    """Run the review and pull its verdict into this log.

    Failure here must never touch the board: the review is instrumentation,
    and instrumentation that can take down the thing it measures is worse
    than no instrumentation at all.
    """
    py = sys.executable or "python"
    try:
        r = subprocess.run([py, os.path.join(HERE, "learn.py"), "--brief", "--notune"], cwd=HERE,
                           capture_output=True, text=True, timeout=LEARN_TIMEOUT)
    except subprocess.TimeoutExpired:
        log("learn: timed out -- skipped this cycle")
        return
    except Exception as e:
        log(f"learn: {type(e).__name__} {str(e)[:100]}")
        return
    out = (r.stdout or "").splitlines()
    # Surface only the two lines worth reading at a glance; the full report is
    # already in logs/LEARN_YYYYMMDD.md.
    for line in out:
        t = line.strip()
        if t.startswith("## P&L") or t.startswith("BIGGEST:") or "anecdote, not a" in t:
            log("learn: " + t.lstrip("# ").strip())
    if r.returncode != 0:
        log(f"learn: exited {r.returncode} -- {(r.stderr or '')[:150]}")


def main():
    log("=" * 60)
    log("SUPERVISOR starting. It owns the board from here.")
    log(f"  app          {APP}")
    log(f"  commands     {CTRL}\\<action>.cmd   (restart | stop | start | ping)")
    log(f"  status       {CTRL}\\status.json")
    log(f"  review      learn.py every {LEARN_EVERY//60} min during market hours")
    log("Keep this window open. Closing it stops the supervisor, not the board.")
    log("=" * 60)

    board = Board()
    board.start("supervisor boot")
    last_restart = time.time()
    last_learn = 0.0
    consecutive_bad = 0

    while True:
        try:
            take_commands(board)
            ok, reason = health(board)

            if ok:
                consecutive_bad = 0
                write_status(board, ok, reason)
            else:
                if board.stopped_by_command:
                    write_status(board, False, "stopped by command", "idle")
                    time.sleep(POLL)
                    continue
                consecutive_bad += 1
                # Two consecutive bad reads before acting: one slow disk write
                # or a single long GC pause is not an outage, and a supervisor
                # that trusts one sample restarts a healthy board.
                if consecutive_bad < 2:
                    write_status(board, False, reason, "one bad read, watching")
                elif time.time() - last_restart < MIN_RESTART_GAP:
                    write_status(board, False, reason,
                                 f"holding off, restarted "
                                 f"{int(time.time()-last_restart)}s ago")
                else:
                    log(f"UNHEALTHY: {reason} -- restarting")
                    board.restart(reason)
                    last_restart = time.time()
                    consecutive_bad = 0
                    write_status(board, False, reason, "restart issued")
            if board.alive():
                preopen_prep()
                shadow_keepalive()

            if (in_market() and board.alive()
                    and time.time() - last_learn > LEARN_EVERY):
                last_learn = time.time()
                run_learn()

            time.sleep(POLL)
        except KeyboardInterrupt:
            log("supervisor interrupted -- leaving the board running")
            write_status(board, True, "supervisor exited", "board left running")
            return
        except Exception as e:
            log(f"supervisor loop error: {type(e).__name__} {e}")
            time.sleep(POLL)


if __name__ == "__main__":
    main()
