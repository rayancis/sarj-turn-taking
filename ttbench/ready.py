"""Am I ready to submit? Checks everything the organisers will ask for, and runs the connection check.

    python -m ttbench.ready                 # full check, including the 6-call connection check (~2.5 min)
    python -m ttbench.ready --skip-connection
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .play import KIT, free_port, wait_for
from .server import git_version


def team_name(system: Path) -> str | None:
    m = re.search(r'^TEAM\s*=\s*["\']([^"\']+)["\']', system.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def writeup_status(path: Path) -> tuple[bool, bool, bool]:
    if not path.exists():
        return False, False, False
    t = path.read_text(encoding="utf-8")
    filled = t.count("<") < 6 and len(t.split()) > 400
    failure_rows = re.findall(r"^\|\s*\d+\s*\|\s*[A-Z]{2}-D\d{2}\s*\|.*\|\s*[^|\s][^|]*\|\s*$", t, re.M)
    sec8 = t.split("## 8.")[-1] if "## 8." in t else ""
    rows = [l for l in sec8.splitlines() if l.startswith("|") and not re.match(r"^\|\s*(Kind|-{3}|<)", l)]
    services = bool(rows) or bool(re.search(r"^\s*none\.?\s*$", sec8, re.M | re.I))
    return filled, len(failure_rows) >= 5, services


def connection_check(system: Path, url: str | None = None) -> bool:
    if url:
        r = subprocess.run([sys.executable, "-m", "ttbench.check", "--url", url], cwd=KIT, capture_output=True, text=True)
        print("    " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
        return r.returncode == 0
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(system), "--port", str(port), "--quiet"], cwd=KIT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for(port, proc):
            return False
        r = subprocess.run([sys.executable, "-m", "ttbench.check", "--url", f"ws://127.0.0.1:{port}"], cwd=KIT,
                           capture_output=True, text=True)
        print("    " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
        return r.returncode == 0
    finally:
        proc.terminate()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--system", default="my_system.py")
    p.add_argument("--skip-connection", action="store_true")
    p.add_argument("--url", help="your system is already running (any language or framework): check it at this address")
    a = p.parse_args(argv)
    system = (KIT / a.system).resolve()
    team = None if a.url else (team_name(system) if system.exists() else None)
    version = git_version(KIT)
    filled, five, services = writeup_status(KIT / "WRITEUP.md")
    checks = [
        (True, "") if a.url else (bool(team) and team != "my-team", f"team name set in {a.system} (now: {team!r})"),
        (version not in ("dev",) and not version.endswith("-dirty"),
         f"all changes committed to git (commit: {version})" if version != "dev" else "the kit is a git repository (git init; git add -A; git commit)"),
        (filled, "WRITEUP.md written (2–4 pages, no <...> left)"),
        (five, "WRITEUP.md lists at least 5 failure cases from the dev set (python -m ttbench.summary runs/<name> --failures)"),
        (services, "WRITEUP.md section 8 declares external services, models and data (or says none)"),
    ]
    print("Submission check\n")
    for ok, what in checks:
        if what:
            print(f"  {'✓' if ok else '✗'} {what}")
    if a.skip_connection:
        print("  - connection check skipped")
        conn = True
    else:
        print("  … connection check: your system against 6 calls in real time (about 2.5 minutes)")
        conn = connection_check(system, a.url)
        print(f"  {'✓' if conn else '✗'} connection check {'PASS' if conn else 'FAIL'}")
        if a.url:                                       # own server: the team name is the one in its hello
            run = KIT / "runs" / "connection-check" / "run.json"
            team = json.loads(run.read_text(encoding="utf-8")).get("hello", {}).get("team") if run.exists() else None
            ok = bool(team) and team != "my-team"
            checks.append((ok, ""))
            print(f"  {'✓' if ok else '✗'} team name in your hello message (now: {team!r})")
    ready = all(ok for ok, _ in checks) and conn
    print()
    if ready:
        print(f"Ready. Hand in: team {team!r}, commit {version}, WRITEUP.md. See SUBMISSION.md for the final run.")
    else:
        print("Not ready yet: fix the ✗ lines above. SUBMISSION.md explains each one.")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
