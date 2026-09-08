#!/usr/bin/env python3
"""keep_awake.py - stop macOS from ever going to sleep, and stay running.

Holds IOKit power assertions (the same mechanism /usr/bin/caffeinate uses) for as
long as the process lives. Nothing is installed system-wide and nothing needs
sudo; the moment the process exits, macOS goes back to its normal power policy.

    ./keep_awake.py                 # hold awake in the foreground (Ctrl-C to stop)
    ./keep_awake.py start           # hold awake in the background
    ./keep_awake.py status          # is it running? which assertions?
    ./keep_awake.py stop            # release and exit
    ./keep_awake.py run -d 90m      # foreground, auto-release after 90 minutes
    ./keep_awake.py run --pid 4242  # release when that process exits
    ./keep_awake.py install         # LaunchAgent: start at login, restart if killed
    ./keep_awake.py uninstall       # remove the LaunchAgent

Caveats worth knowing:
  * Power assertions block *idle* sleep. Closing the lid on a laptop still
    sleeps it unless an external display/power is attached. To defeat that you
    need `sudo pmset -a disablesleep 1`, which this script deliberately does not
    do for you (it is a persistent system-wide change).
  * Display sleep is prevented only with --display (the default). The screen
    still locks if you have a screensaver/lock policy - assertions do not
    disable the lock screen.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "keep_awake"
LABEL = "com.local.keepawake"
STATE_DIR = Path.home() / ".local" / "state" / APP_NAME
PID_FILE = STATE_DIR / "keep_awake.pid"
LOG_FILE = STATE_DIR / "keep_awake.log"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# IOKit assertion types (IOPMLib.h).
ASSERT_SYSTEM = "PreventUserIdleSystemSleep"   # -i : no idle system sleep
ASSERT_DISPLAY = "PreventUserIdleDisplaySleep"  # -d : screen stays on
ASSERT_DISK = "PreventDiskIdle"                 # -m : disks stay spun up
ASSERT_AWAKE = "PreventSystemSleep"             # -s : awake while on AC power

ASSERTION_LEVEL_ON = 255
KCF_STRING_ENCODING_UTF8 = 0x08000100


# --------------------------------------------------------------------------- #
# IOKit backend
# --------------------------------------------------------------------------- #
class PowerAssertions:
    """Holds a set of IOPMAssertions for the lifetime of this object."""

    def __init__(self, reason: str = "keep_awake: user requested no sleep"):
        self.reason = reason
        self._ids: dict[str, ctypes.c_uint32] = {}
        self._cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

        self._cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self._cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]

        self._iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int
        self._iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self._iokit.IOPMAssertionRelease.restype = ctypes.c_int
        self._iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]

    def _cfstr(self, value: str) -> ctypes.c_void_p:
        ref = self._cf.CFStringCreateWithCString(
            None, value.encode("utf-8"), KCF_STRING_ENCODING_UTF8
        )
        if not ref:
            raise RuntimeError(f"CFStringCreateWithCString failed for {value!r}")
        return ctypes.c_void_p(ref)

    def acquire(self, kinds: list[str]) -> None:
        for kind in kinds:
            if kind in self._ids:
                continue
            type_ref = self._cfstr(kind)
            name_ref = self._cfstr(self.reason)
            handle = ctypes.c_uint32(0)
            try:
                rc = self._iokit.IOPMAssertionCreateWithName(
                    type_ref, ASSERTION_LEVEL_ON, name_ref, ctypes.byref(handle)
                )
            finally:
                self._cf.CFRelease(type_ref)
                self._cf.CFRelease(name_ref)
            if rc != 0:
                raise OSError(f"IOPMAssertionCreateWithName({kind}) failed: rc={rc}")
            self._ids[kind] = handle

    def release(self) -> None:
        for kind, handle in list(self._ids.items()):
            self._iokit.IOPMAssertionRelease(handle)
            del self._ids[kind]

    @property
    def held(self) -> list[str]:
        return list(self._ids)

    def __enter__(self) -> PowerAssertions:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class CaffeinateFallback:
    """Backup backend: supervise /usr/bin/caffeinate if IOKit is unavailable."""

    FLAGS = {
        ASSERT_SYSTEM: "-i",
        ASSERT_DISPLAY: "-d",
        ASSERT_DISK: "-m",
        ASSERT_AWAKE: "-s",
    }

    def __init__(self, reason: str = ""):
        self.reason = reason
        self._proc: subprocess.Popen[bytes] | None = None
        self._kinds: list[str] = []

    def acquire(self, kinds: list[str]) -> None:
        self._kinds = kinds
        flags = [self.FLAGS[k] for k in kinds if k in self.FLAGS] or ["-i"]
        self._proc = subprocess.Popen(
            ["/usr/bin/caffeinate", *flags],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def ensure_alive(self) -> bool:
        """Restart caffeinate if it died. Returns True if it had to restart."""
        if self._proc is not None and self._proc.poll() is not None:
            self.acquire(self._kinds)
            return True
        return False

    def release(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    @property
    def held(self) -> list[str]:
        return list(self._kinds)

    def __enter__(self) -> CaffeinateFallback:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def make_backend(reason: str) -> PowerAssertions | CaffeinateFallback:
    try:
        return PowerAssertions(reason)
    except OSError as exc:  # framework missing / not macOS
        log(f"IOKit unavailable ({exc}); falling back to caffeinate")
        return CaffeinateFallback(reason)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
DURATION_RE = re.compile(r"(?i)^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    m = DURATION_RE.match(text)
    if not m:
        raise argparse.ArgumentTypeError(
            f"bad duration {text!r} (use e.g. 30, 45s, 90m, 8h, 2d)"
        )
    return float(m.group(1)) * _UNITS[m.group(2).lower()]


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def read_pid_file() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if not pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def write_pid_file() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n")


def selected_kinds(args: argparse.Namespace) -> list[str]:
    kinds = [ASSERT_SYSTEM]
    if args.display:
        kinds.append(ASSERT_DISPLAY)
    if args.disk:
        kinds.append(ASSERT_DISK)
    if args.on_ac:
        kinds.append(ASSERT_AWAKE)
    return kinds


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    existing = read_pid_file()
    if existing and existing != os.getpid() and not args.force:
        log(f"already running as pid {existing} (use --force to run anyway)")
        return 1

    kinds = selected_kinds(args)
    deadline = time.monotonic() + args.duration if args.duration else None
    stopping = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        log(f"received {signal.Signals(signum).name}, releasing assertions")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    write_pid_file()
    try:
        with make_backend(args.reason) as backend:
            backend.acquire(kinds)
            log(f"pid {os.getpid()} holding: {', '.join(backend.held)}")
            if deadline:
                log(f"auto-release in {args.duration:.0f}s")
            if args.pid:
                log(f"auto-release when pid {args.pid} exits")

            while not stopping:
                time.sleep(1)
                if isinstance(backend, CaffeinateFallback) and backend.ensure_alive():
                    log("caffeinate died; restarted it")
                if deadline and time.monotonic() >= deadline:
                    log("duration elapsed")
                    break
                if args.pid and not pid_alive(args.pid):
                    log(f"watched pid {args.pid} exited")
                    break
    finally:
        if read_pid_file() == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    log("sleep re-enabled")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    existing = read_pid_file()
    if existing:
        log(f"already running as pid {existing}")
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    forwarded = ["run"]
    if not args.display:
        forwarded.append("--no-display")
    if args.disk:
        forwarded.append("--disk")
    if args.on_ac:
        forwarded.append("--on-ac")
    if args.duration:
        forwarded += ["--duration", str(int(args.duration))]

    with LOG_FILE.open("ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *forwarded],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.7)
    if proc.poll() is not None:
        log(f"failed to start; see {LOG_FILE}")
        return 1
    log(f"started in background as pid {proc.pid}; log: {LOG_FILE}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    pid = read_pid_file()
    if not pid:
        log("not running")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not pid_alive(pid):
            log(f"stopped pid {pid}")
            return 0
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    log(f"force-killed pid {pid}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    pid = read_pid_file()
    if pid:
        log(f"running (pid {pid})")
    else:
        log("not running")

    if PLIST_PATH.exists():
        log(f"LaunchAgent installed: {PLIST_PATH}")

    try:
        out = subprocess.run(
            ["/usr/bin/pmset", "-g", "assertions"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return 0 if pid else 1

    print("\n--- pmset -g assertions ---")
    for line in out.splitlines():
        if re.search(r"Prevent(UserIdle)?(System|Display)Sleep|PreventDiskIdle", line):
            print(line)
    return 0 if pid else 1


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def cmd_install(args: argparse.Namespace) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    argv = [sys.executable, os.path.abspath(__file__), "run", "--force"]
    if not args.display:
        argv.append("--no-display")
    if args.disk:
        argv.append("--disk")
    if args.on_ac:
        argv.append("--on-ac")

    PLIST_PATH.write_text(
        PLIST_TEMPLATE.format(
            label=LABEL,
            args="\n".join(f"        <string>{a}</string>" for a in argv),
            log=LOG_FILE,
        )
    )
    subprocess.run(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
    )
    result = subprocess.run(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"launchctl bootstrap failed: {result.stderr.strip()}")
        return 1
    log(f"installed and loaded {LABEL}")
    log("it now starts at login and is restarted automatically if it dies")
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    subprocess.run(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
    )
    PLIST_PATH.unlink(missing_ok=True)
    log(f"removed {LABEL}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keep_awake.py",
        description="Keep macOS awake (no idle sleep) for as long as this runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Caveats", 1)[-1] and "See the module docstring for caveats.",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--no-display",
            dest="display",
            action="store_false",
            help="allow the display to sleep (system still stays awake)",
        )
        p.add_argument(
            "--disk", action="store_true", help="also prevent disk idle sleep"
        )
        p.add_argument(
            "--on-ac",
            action="store_true",
            help="also assert PreventSystemSleep (only effective on AC power)",
        )
        p.set_defaults(display=True)

    run = sub.add_parser("run", help="hold awake in the foreground (default)")
    add_common(run)
    run.add_argument(
        "-d", "--duration", type=parse_duration, help="auto-release after e.g. 90m, 8h"
    )
    run.add_argument("--pid", type=int, help="auto-release when this pid exits")
    run.add_argument(
        "--reason", default="keep_awake: user requested no sleep", help="assertion name"
    )
    run.add_argument("--force", action="store_true", help="run even if one is running")
    run.set_defaults(func=cmd_run)

    start = sub.add_parser("start", help="hold awake in a detached background process")
    add_common(start)
    start.add_argument("-d", "--duration", type=parse_duration)
    start.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="stop the running instance").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="show state and live pmset assertions").set_defaults(
        func=cmd_status
    )

    install = sub.add_parser(
        "install", help="install a LaunchAgent (start at login, auto-restart)"
    )
    add_common(install)
    install.set_defaults(func=cmd_install)

    sub.add_parser("uninstall", help="remove the LaunchAgent").set_defaults(
        func=cmd_uninstall
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("keep_awake.py only supports macOS", file=sys.stderr)
        return 2

    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"run", "start", "stop", "status", "install", "uninstall"}
    if not argv or argv[0] not in known and not argv[0] in {"-h", "--help"}:
        argv.insert(0, "run")  # bare invocation / bare flags default to `run`

    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
