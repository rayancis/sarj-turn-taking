"""Run an unchanged module with a scoped Windows 1 ms scheduler timer request.

Example: python tools/windows_realtime.py pytest -q tests/test_replay.py
This does not alter the harness clock, timestamps, speed, thresholds or tests.
The OS timer request is released even if the target raises or exits.
"""
import ctypes
import runpy
import sys

def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: windows_realtime.py MODULE [ARGS...]')
    module, args = sys.argv[1], sys.argv[2:]
    winmm = None
    if sys.platform == 'win32':
        winmm = ctypes.WinDLL('winmm')
        if winmm.timeBeginPeriod(1) != 0:
            raise SystemExit('Windows rejected the 1 ms scheduler timer request')
    try:
        sys.argv = [module, *args]
        sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
        runpy.run_module(module, run_name='__main__')
    finally:
        if winmm is not None:
            winmm.timeEndPeriod(1)

if __name__ == '__main__':
    main()
