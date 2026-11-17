"""Fetch the pinned neural VAD packages into .local/models (not committed) and verify their hashes.

    python tools/get_models.py

Silero VAD 6.2.3 (MIT): only the ONNX file is used, run with onnxruntime; torch is NOT installed.
TEN VAD 1.0.6.9 (Apache-2.0 with additional conditions, see experiments/P3 report): native library + ctypes binding.
Both come from PyPI wheels (`pip download --no-deps`), so no other host is needed.
"""
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / ".local" / "models"
WHEELS = {
    "silero-vad==6.2.3": ("silero_vad-6.2.3-py3-none-any.whl",
                          {"silero_vad/data/silero_vad.onnx": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"}),
    "ten-vad==1.0.6.9": ("ten_vad-1.0.6.9-py3-none-any.whl", {}),
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for spec, (wheel, hashes) in WHEELS.items():
        if not (DEST / wheel).exists():
            subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "-d", str(DEST), spec], check=True)
        with zipfile.ZipFile(DEST / wheel) as z:
            z.extractall(DEST / wheel.split("-")[0])
        for rel, want in hashes.items():
            got = hashlib.sha256((DEST / wheel.split("-")[0] / rel).read_bytes()).hexdigest()
            assert got == want, f"{rel}: sha256 {got} != pinned {want}"
        print(f"ok  {spec}  ->  {DEST / wheel.split('-')[0]}  (wheel sha256 "
              f"{hashlib.sha256((DEST / wheel).read_bytes()).hexdigest()[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
