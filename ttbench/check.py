"""Connection check (the entry gate): stream the 6 public smoke scenarios and print PASS or FAIL.

    python -m ttbench.check --url ws://<team-ip>:8765 [--data data/dev]

PASS = the run completes, 0 protocol errors, a valid hello, and a median round trip under 50 ms.
"""
import argparse
import asyncio
import sys
from pathlib import Path

from .dataset import lay_out_stream, load_split
from .harness import run_session

SMOKE = ["GI-D01", "BC-D01", "HT-D01", "BG-D02", "AM-D02", "OV-D01"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--data", type=Path, default=Path("data/dev"))
    p.add_argument("--out", type=Path, default=Path("runs/connection-check"))
    p.add_argument("--speed", type=float, default=1.0)
    a = p.parse_args(argv)
    scen = lay_out_stream(load_split(a.data, only=SMOKE))
    print(f"Connection check against {a.url}: {len(scen)} scenarios, about {sum(s.duration_ms for s in scen) / 1000 / a.speed:.0f} s")
    try:
        meta = asyncio.run(run_session(a.url, scen, a.out, speed=a.speed, session_id="check"))
    except (OSError, SystemExit) as e:
        print(f"FAIL: could not complete the session ({e})")
        return 1
    rtt = (meta.get("rtt_summary_ms") or {}).get("median")
    checks = {"completed": bool(meta.get("completed")), "valid hello": "hello" in meta,
              "0 protocol errors": not meta["errors"], "RTT median < 50 ms": rtt is not None and rtt < 50}
    for k, ok in checks.items():
        print(f"  {'ok ' if ok else 'NO '} {k}" + (f" ({rtt} ms)" if k.startswith("RTT") else ""))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL: see TROUBLESHOOTING.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
