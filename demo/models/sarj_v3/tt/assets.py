"""Pinned public Whisper snapshot. Downloads occur only through the explicit build command."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'model-assets.json'
MODEL_DIR = ROOT / 'models' / 'faster-whisper-tiny'


def verify_assets(model_dir=MODEL_DIR):
    spec = json.loads(LOCK.read_text(encoding='utf-8'))
    root = Path(model_dir).resolve()
    for name, expected in spec['files'].items():
        path = root / name
        if Path(name).name != name or path.is_symlink() or not path.is_file():
            raise RuntimeError('Whisper asset missing or unsafe: ' + name)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != expected:
            raise RuntimeError('Whisper asset integrity failure: ' + name)
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        from huggingface_hub import snapshot_download
        spec = json.loads(LOCK.read_text(encoding='utf-8'))
        snapshot_download(spec['repository'], revision=spec['revision'],
                          allow_patterns=list(spec['files']), local_dir=str(MODEL_DIR))
    spec = verify_assets()
    print('Whisper assets verified: ' + spec['revision'])


if __name__ == '__main__':
    main()
