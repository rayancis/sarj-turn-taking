"""The additional evaluation scenarios: split isolation and label consistency (no detector involved)."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / "data" / "extra"


def _manifest(split):
    return json.loads((EXTRA / split / "manifest.json").read_text(encoding="utf-8"))


def _labels(split):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((EXTRA / split / "labels").glob("*.json"))]


def test_splits_share_no_speaker_and_no_noise_clip():
    people = {s: set(sum(_manifest(s)["speakers"].values(), [])) for s in ("xdev", "holdout")}
    assert people["xdev"] and people["holdout"] and not people["xdev"] & people["holdout"]
    noise = {s: {n for l in _labels(s) for n in l["acoustic"]["noise_source_ids"] if n.startswith("ESC50-")}
             for s in ("xdev", "holdout")}
    assert noise["xdev"] and noise["holdout"] and not noise["xdev"] & noise["holdout"]


def test_every_source_has_a_permissive_licence_and_a_hash():
    sources = json.loads((EXTRA / "sources.json").read_text(encoding="utf-8"))
    assert len(sources) > 100
    for key, s in sources.items():
        assert s["licence"] in ("CC BY 4.0", "CC0", "CC-BY"), key
        assert len(s["sha256"]) == 64 and s["attribution"], key


def test_loudness_is_not_a_proxy_for_the_label():
    """Both classes contain soft and normal-level events, so a level cue cannot be read off the labels."""
    for split in ("xdev", "holdout"):
        design = {d["scenario_id"]: d for d in json.loads((EXTRA / split / "design.json").read_text(encoding="utf-8"))}
        rel = {"STOP": set(), "KEEP": set()}
        for l in _labels(split):
            if l["category"] in ("GI", "OV", "BC"):
                rel[l["events"][0]["expected"]].add(design[l["scenario_id"]]["rel_level_db"] < 0)
        assert rel["STOP"] == {True, False} and rel["KEEP"] == {True, False}


def test_labels_score_with_the_official_scorer_and_match_the_built_audio():
    if not (EXTRA / "xdev" / "audio").exists():
        pytest.skip("audio not built; run: python tools/build_extra.py")
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import build_extra
    from ttbench.score import load_labels, score
    assert build_extra.verify("xdev") == [] and build_extra.verify("holdout") == []
    labels, manifest = load_labels(EXTRA / "xdev")
    layout = [{"scenario_id": sid, "start_ms": i * 100000, "duration_ms": l["audio"]["duration_ms"]}
              for i, (sid, l) in enumerate(labels.items())]
    r = score(labels, {"scenarios": layout, "hello": {}, "errors": []}, [], manifest["thresholds"])
    assert r["rates"]["n_stop_events"] == 11 and r["core"]["R"] == 0.0          # never stopping: R = 0, S = 1
    assert r["core"]["S"] == 1.0
