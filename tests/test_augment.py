"""Self-tests for tools/augment_dev.py (independent of the system under test).

Run with `python3 -m pytest tests/test_augment.py` or directly: `python3 tests/test_augment.py`.
A subset of scenarios is generated for every condition in both trees (about 20-40 s); the checks on the
full generated trees in data/aug run only if those exist.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import augment_dev as A  # noqa: E402
from ttbench.dataset import load_split, read_wav_stereo  # noqa: E402

SRC = ROOT / "data" / "dev"
SUBSET = ["GI-D01", "BC-D03", "BG-D02", "HT-D01", "NV-D03", "DV-D02"]
ADDITIVE = ["ac_hum", "car", "street", "cafe_babble", "tv", "household"]
SNR_TARGET = dict(A.SNR_RANGE, combo_phone_street=A.SNR_RANGE["street"], combo_speaker_ac=A.SNR_RANGE["ac_hum"])
_cache: dict = {}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tmp_root():
    if "root" not in _cache:
        _cache["tmp"] = tempfile.TemporaryDirectory()
        _cache["root"] = Path(_cache["tmp"].name)
    return _cache["root"]


def split_dir(tree, cond):
    d = tmp_root() / tree / cond
    if not d.exists():
        A.build_split(SRC, tmp_root(), tree, cond, only=SUBSET, src=src())
    return d


def src():
    if "src" not in _cache:
        _cache["src"] = A.Source(SRC)
    return _cache["src"]


def manifest(tree, cond):
    return json.loads((split_dir(tree, cond) / "manifest.json").read_text(encoding="utf-8"))


def label(tree, cond, sid):
    return json.loads((split_dir(tree, cond) / "labels" / f"{sid}.json").read_text(encoding="utf-8"))


def audio(tree, cond, sid):
    return read_wav_stereo(split_dir(tree, cond) / "audio" / f"{sid}.wav")


def all_cases():
    return [(t, c) for t in ("dev", "holdout") for c in A.CONDITIONS]


def sample_rms(a, mask):
    return float(np.sqrt(np.mean(a[mask].astype(float) ** 2)))


# ------------------------------------------------------------------ structure

def test_loads_with_same_ids_and_counts():
    for tree, cond in all_cases():
        sc = load_split(split_dir(tree, cond))
        assert [s.scenario_id for s in sc] == [i for i in src().ids if i in SUBSET], (tree, cond)


def test_channel0_bit_identical_and_length_identical():
    for tree, cond in all_cases():
        for sid in SUBSET:
            o, n = src().audio[sid], audio(tree, cond, sid)
            assert n.shape == o.shape, (tree, cond, sid)
            assert np.array_equal(n[:, 0], o[:, 0]), (tree, cond, sid)
            assert not np.array_equal(n[:, 1], o[:, 1]) or cond == "nonspeech_events", (tree, cond, sid)
            assert label(tree, cond, sid)["audio"]["duration_ms"] == src().labels[sid]["audio"]["duration_ms"]


def _strip(lab):
    lab = json.loads(json.dumps(lab))
    lab.pop("acoustic")
    lab["audio"].pop("sha256")
    return lab


def test_labels_unchanged_except_acoustic_and_audio_sha():
    for tree, cond in all_cases():
        for sid in SUBSET:
            new = label(tree, cond, sid)
            assert _strip(new) == _strip(src().labels[sid]), (tree, cond, sid)
            orig_c = src().labels[sid]["acoustic"]["conditions"]
            nc = new["acoustic"]["conditions"]
            assert len(nc) == len(set(nc))
            assert [t for t in orig_c if t != "quiet"] == [t for t in nc if t in orig_c and t != "quiet"], "base kept"
            assert set(A.TOKEN[cond]) <= set(nc)
            assert new["acoustic"]["notes"].startswith(f"augmented: {cond}, seed ")


def test_manifest_hashes_and_fields():
    srcm = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    for tree, cond in all_cases():
        d = split_dir(tree, cond)
        m = manifest(tree, cond)
        assert m["thresholds"] == srcm["thresholds"]
        assert m["source_split"] == "dev"
        assert m["augmentation"]["condition"] == cond and m["augmentation"]["tree"] == tree
        assert m["augmentation"]["generator_version"] == A.GEN_VERSION
        for e in m["scenarios"]:
            sid = e["scenario_id"]
            assert sha(d / "audio" / f"{sid}.wav") == e["wav_sha256"]
            assert sha(d / "labels" / f"{sid}.json") == e["label_sha256"]
            assert label(tree, cond, sid)["audio"]["sha256"] == e["wav_sha256"]
            assert f"seed {e['aug']['seed']}" in label(tree, cond, sid)["acoustic"]["notes"]


# ------------------------------------------------------------------ levels, SNR, clipping

def test_additive_snr_in_range_and_matches_audio():
    for tree in ("dev", "holdout"):
        for cond in ADDITIVE + ["combo_phone_street", "combo_speaker_ac"]:
            lo, hi = SNR_TARGET[cond]
            for sid in SUBSET:
                snr = label(tree, cond, sid)["acoustic"]["snr_db"]
                assert lo - 1.5 <= snr <= hi + 1.5, (tree, cond, sid, snr)
                if cond in ADDITIVE:      # independently re-measure from the written audio
                    o, n = src().audio[sid][:, 1].astype(float), audio(tree, cond, sid)[:, 1].astype(float)
                    mask = A.seg_mask(src().labels[sid], len(o))
                    meas = 20 * np.log10(sample_rms(o, mask) / A.rms(n - o))
                    assert abs(meas - snr) < 0.7, (tree, cond, sid, meas, snr)


def test_no_unintended_clipping():
    for tree, cond in all_cases():
        if cond == "level_high":
            continue
        for sid in SUBSET:
            x = audio(tree, cond, sid)[:, 1]
            frac = float(np.mean(np.abs(x.astype(np.int32)) >= 32767))
            assert frac < 0.001, (tree, cond, sid, frac)


def test_level_conditions():
    for tree in ("dev", "holdout"):
        for sid in SUBSET:
            o = src().audio[sid][:, 1].astype(float)
            mask = A.seg_mask(src().labels[sid], len(o))
            lo = 20 * np.log10(sample_rms(audio(tree, "level_low", sid)[:, 1], mask) / sample_rms(o, mask))
            assert -14.6 <= lo <= -9.4, lo
            hi_x = audio(tree, "level_high", sid)[:, 1].astype(float)
            assert np.abs(hi_x).max() <= 32767
            hi = 20 * np.log10(sample_rms(hi_x, mask) / sample_rms(o, mask))
            assert 5 <= hi <= 12.5, hi          # tanh compression eats some of the +8..12 dB gain


def test_speakerphone_echo_and_packet_loss_parameters():
    for tree in ("dev", "holdout"):
        for cond in ("speakerphone", "combo_speaker_ac"):
            for sid in SUBSET:
                e = label(tree, cond, sid)["acoustic"]["echo"]
                assert -25 <= e["level_db"] <= -15 and 150 <= e["delay_ms"] <= 250
        for e in manifest(tree, "packet_loss")["scenarios"]:
            p = e["aug"]["params"]
            assert 0.02 <= p["loss_rate"] <= 0.05
            o = src().audio[e["scenario_id"]][:, 1]
            n = audio(tree, "packet_loss", e["scenario_id"])[:, 1]
            F = 320
            changed = {i // F for i in range(0, len(o), F) if not np.array_equal(o[i:i + F], n[i:i + F])}
            lost = {ms // 20 for ms, _ in p["lost_ms"]}
            assert changed <= lost, "only the frames that were dropped may differ"
            if p["mode"] == "zero":
                assert all(not n[i * F:(i + 1) * F].any() for i in lost)


# ------------------------------------------------------------------ determinism / independence of trees

def test_determinism_and_byte_identical_regeneration():
    for cond in ("street", "cafe_babble", "combo_speaker_ac", "packet_loss", "nonspeech_events"):
        a1, l1, _ = A.augment_scenario(src(), "GI-D01", cond, "dev")
        a2, l2, _ = A.augment_scenario(src(), "GI-D01", cond, "dev")
        assert a1.tobytes() == a2.tobytes() and l1 == l2
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        A.build_split(SRC, t1, "holdout", "street", only=SUBSET[:2], src=src())
        A.build_split(SRC, t2, "holdout", "street", only=SUBSET[:2], src=src())
        for rel in ("manifest.json", "audio/GI-D01.wav", "labels/GI-D01.json", "audio/BC-D03.wav"):
            assert (Path(t1) / "holdout/street" / rel).read_bytes() == (Path(t2) / "holdout/street" / rel).read_bytes()


def test_dev_and_holdout_differ():
    for cond in A.CONDITIONS:
        for sid in SUBSET:
            d, h = audio("dev", cond, sid)[:, 1], audio("holdout", cond, sid)[:, 1]
            assert not np.array_equal(d, h), (cond, sid)
            assert label("dev", cond, sid)["acoustic"]["notes"] != label("holdout", cond, sid)["acoustic"]["notes"]


def test_partner_pairings_never_same_scenario_and_differ_between_trees():
    for cond, key, k in (("cafe_babble", "partners", None), ("tv", "partner", None)):
        for sid in SUBSET:
            ps = {}
            for tree in ("dev", "holdout"):
                e = next(x for x in manifest(tree, cond)["scenarios"] if x["scenario_id"] == sid)
                p = e["aug"]["params"][key]
                ps[tree] = set(p if isinstance(p, list) else [p])
                assert sid not in ps[tree]
                assert len(ps[tree]) == (e["aug"]["params"]["n_voices"] if key == "partners" else 1)
                if key == "partners":
                    assert 3 <= len(ps[tree]) <= 6
            assert not (ps["dev"] & ps["holdout"]), (cond, sid)


# ------------------------------------------------------------------ nonspeech events keep labels valid

def _forbidden_independent(lab):
    """Re-derived separately from the generator: segments and the full span of every event."""
    z = [(s["start_ms"], s["end_ms"]) for s in lab["user_segments"]]
    for e in lab["events"]:
        ts = [v for k, v in e.items() if k.endswith("_ms") and isinstance(v, int)]
        z.append((min(ts), max(ts)))
    return z


def test_nonspeech_bursts_avoid_segments_and_event_windows():
    total = 0
    for tree in ("dev", "holdout"):
        for e in manifest(tree, "nonspeech_events")["scenarios"]:
            sid = e["scenario_id"]
            o, n = src().audio[sid][:, 1].astype(int), audio(tree, "nonspeech_events", sid)[:, 1].astype(int)
            idx = np.flatnonzero(o != n)
            bursts = e["aug"]["params"]["bursts"]
            assert 0 <= len(bursts) <= 3
            total += len(bursts)
            if len(idx):
                changed_ms = idx * 1000 / A.SR
                for a, b in _forbidden_independent(src().labels[sid]):
                    assert not ((changed_ms >= a) & (changed_ms <= b)).any(), (tree, sid, a, b)
            for b in bursts:
                assert b["end_ms"] > b["start_ms"]
    assert total >= 6, "bursts should usually find room"


# ------------------------------------------------------------------ primitives

def test_ulaw_matches_g711_reference_values_and_phone_band():
    x = np.array([0, 1, -1, 100, 1000, 8000, 32767, -32768])
    enc = A.ulaw_encode(x)
    assert enc.tolist() == [0xFF, 0xFF, 0x7F, 0xF2, 0xCE, 0xA0, 0x80, 0x00]
    dec = A.ulaw_decode(enc)
    assert np.all(np.abs(dec[3:6] - x[3:6]) <= 0.04 * np.abs(x[3:6]))
    w = np.random.default_rng(0).standard_normal(64000) * 3000
    y = A.phone_nb(w)
    S = np.abs(np.fft.rfft(y)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / A.SR)
    ref = S[(f > 500) & (f < 3000)].mean()
    assert S[f > 4200].mean() < ref * 1e-5
    assert S[f < 150].mean() < ref * 1e-2


def test_full_split_loads():
    with tempfile.TemporaryDirectory() as t:
        A.build_split(SRC, t, "dev", "level_low", src=src())
        sc = load_split(Path(t) / "dev" / "level_low")
        assert [s.scenario_id for s in sc] == src().ids and len(sc) == 24


# ------------------------------------------------------------------ generated trees on disk (if present)

def test_generated_trees_on_disk():
    root = ROOT / "data" / "aug"
    if not root.exists():
        return
    srcm = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    for tree in ("dev", "holdout"):
        for cond in A.CONDITIONS:
            d = root / tree / cond
            if not d.exists():
                continue
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            assert [e["scenario_id"] for e in m["scenarios"]] == src().ids
            assert m["thresholds"] == srcm["thresholds"]
            for e in m["scenarios"]:
                sid = e["scenario_id"]
                assert sha(d / "audio" / f"{sid}.wav") == e["wav_sha256"]
                assert sha(d / "labels" / f"{sid}.json") == e["label_sha256"]
                a = read_wav_stereo(d / "audio" / f"{sid}.wav")
                assert np.array_equal(a[:, 0], src().audio[sid][:, 0]) and len(a) == len(src().audio[sid])
            assert len(load_split(d)) == 24


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as ex:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(ex))
    sys.exit(1 if fails else 0)
