"""Serve my_system.py with some options overridden, for real-time comparison runs of a non-default configuration.

    python tools/serve_config.py --set '{"CALLER_REF": false, "EVIDENCE_WINDOW_MS": 300}' --port 8771 --quiet

The official entry point is still `python my_system.py`; this only exists so a comparison row can be measured with
the real harness without editing the detector.
"""
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import my_system  # noqa: E402
from ttbench.server import run  # noqa: E402

if __name__ == "__main__":
    i = sys.argv.index("--set")
    overrides = json.loads(sys.argv[i + 1])
    del sys.argv[i:i + 2]
    for k, v in overrides.items():
        if not hasattr(my_system, k):
            raise SystemExit(f"my_system.py has no option {k}")
        setattr(my_system, k, v)
    my_system.MySystem()
    run(my_system.MySystem, my_system.TEAM, KIT / "my_system.py")
