"""One command: start your system, play calls to it through the real harness, score it, tell you how it went.

    python -m ttbench.play                      # my_system.py against 8 example calls, one per situation (~3.5 min)
    python -m ttbench.play --fast               # the same 4x faster (~1 min): for quick iteration, timing not official
    python -m ttbench.play --calls all          # all 24 development calls (~10 min real time)
    python -m ttbench.play --calls GI-D01,HT-D01
    python -m ttbench.play --system other_idea.py --name idea2

It is exactly what the harness does at the final, on your own machine: your system runs as its own process and
receives the audio over a WebSocket, in real time. Every run is kept in runs/ and added to runs/history.csv.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import harness
from .summary import record, text

KIT = Path(__file__).resolve().parents[1]
# One call per situation, chosen so its scores track the full development set (baseline 48.6 here vs 41.0 on all 24;
# the order of the reference systems is the same). The full set is the one to report.
QUICK = ["GI-D02", "BC-D03", "HT-D02", "BG-D01", "NV-D01", "AM-D01", "OV-D01", "DV-D01"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int, proc: subprocess.Popen, timeout: float = 30) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--system", default="my_system.py", help="your system file (default: my_system.py)")
    p.add_argument("--url", help="test a system that is already running, in any language or framework: ws://127.0.0.1:8765")
    p.add_argument("--calls", default="quick", help="quick (8 calls, one per situation), all (24), or ids: GI-D01,HT-D01")
    p.add_argument("--fast", action="store_true", help="4x faster than real time; latency then does not count")
    p.add_argument("--name", help="name for this run (default: time stamp)")
    p.add_argument("--data", default=str(KIT / "data" / "dev"))
    a = p.parse_args(argv)

    if a.url:
        return played(a, None, a.url)
    system = Path(a.system).resolve()
    if not system.exists():
        print(f"Cannot find {a.system}. Run this from the starter kit folder.", file=sys.stderr)
        return 2
    name = a.name or time.strftime("%m%d-%H%M%S") + ("-fast" if a.fast else "")
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(KIT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    (KIT / "runs").mkdir(exist_ok=True)
    log = open(KIT / "runs" / f".{name}.system.log", "w")
    proc = subprocess.Popen([sys.executable, str(system), "--port", str(port), "--quiet"], cwd=KIT, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_for(port, proc):
            log.flush()
            print(f"\nYour system did not start. Its output:\n{(KIT / 'runs' / f'.{name}.system.log').read_text()[-2000:]}",
                  file=sys.stderr)
            return 2
        print(f"Your system ({system.name}) is running.")
        return played(a, name, f"ws://127.0.0.1:{port}", f"runs/.{name}.system.log")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def played(a, name: str | None, url: str, system_log: str | None = None) -> int:
    """Stream the calls to the system at `url`, score the run and print the summary."""
    calls = {"quick": ",".join(QUICK), "all": None}.get(a.calls, a.calls)
    name = name or a.name or time.strftime("%m%d-%H%M%S") + ("-fast" if a.fast else "")
    out = KIT / "runs" / name
    print(f"Playing calls to {url}{' at 4x speed' if a.fast else ' in real time'}...\n")
    args = ["--data", a.data, "--url", url, "--out", str(out)]
    args += ["--scenarios", calls] if calls else []
    args += ["--speed", "4"] if a.fast else []
    code = harness.main(args)
    if code != 0 or not (out / "score.json").exists():
        print("\nThe run did not finish." + (f" Your system's output: {system_log}" if system_log else ""), file=sys.stderr)
        return code or 2
    r = json.loads((out / "score.json").read_text(encoding="utf-8"))
    print("\n" + "=" * 78)
    print(text(r, out.relative_to(KIT)))
    print(record(out, r))
    if a.fast:
        print("Fast run: timing is approximate. Check with a real-time run before you trust a latency change.")
    worst = next((e["scenario_id"] for e in sorted(r["events"], key=lambda e: e["credit"]) if e["credit"] < 1), None)
    if worst:
        print(f"Hear what the caller heard in {worst}:  python -m ttbench.render --run {out.relative_to(KIT)} --scenario {worst}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
