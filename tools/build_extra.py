"""Build the additional evaluation scenarios (data/extra/xdev and data/extra/holdout) from licensed recordings.

    python tools/build_extra.py              # download sources (cached in .local/extra_src), build both splits
    python tools/build_extra.py --verify     # check built audio against manifests and labels (no detector involved)

Why: the kit's 24 dev calls hold 7 genuine interruptions and are fully synthetic. These scenarios add human
Arabic voices, real non-speech sounds and controlled acoustic conditions in the kit's own format (stereo
[agent, caller] WAV, labels, manifest), so the official scorer and tools/replay.py read them unchanged.

Sources (declared in data/extra/SOURCES.md; nothing here is used for training):
  ASC     Arabic Speech Commands Dataset, Ghandoura, Hjabo, Al Dakkak (2021), CC BY 4.0. Human speakers,
          isolated one-second Arabic keywords, 16 kHz.
  ESC-50  Piczak (2015). Only clips whose own Freesound licence is CC0 or CC-BY are used (per the dataset's
          LICENSE file); the compilation as a whole is CC BY-NC, and this use is non-commercial evaluation.
Audio is rebuilt bit-exactly from the cached sources (fixed seeds) and is not committed (*.wav is ignored).

Splits never share a speaker or a noise clip:
  xdev     ASC "train" speakers, ESC-50 folds 1-3    - may be looked at while developing
  holdout  ASC "val"+"test" speakers, ESC-50 folds 4-5 - frozen; scored once after candidate selection

Labels come from the script, never from a detector: a single acknowledgement word (نعم / موافق) while the agent
talks is KEEP; an explicit objection or stop word (لا / توقف / إلغاء) is STOP, evident at the end of the first such
word; background talkers, the agent's own echo and non-speech sounds are KEEP. Event times are the exact sample
positions where the clips were placed. Loudness is a design factor that is varied for BOTH classes (soft
interruptions, normal-level acknowledgements), so no detector cue is built into the labels.

Limits: isolated read keywords are not conversational speech; "interruptions" are concatenated command words
from one speaker; noise is mixed digitally; no human listener has validated the labels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parents[1]
SRC = KIT / ".local" / "extra_src"
OUT = KIT / "data" / "extra"
SR = 16000
RAW = "https://raw.githubusercontent.com"
ASC_URL = f"{RAW}/abdulkaderghandoura/arabic-speech-commands-dataset/master"
ESC_URL = f"{RAW}/karolpiczak/ESC-50/master"
ARABIC = {"zero": "صفر", "one": "واحد", "two": "اثنان", "three": "ثلاثة", "four": "أربعة", "five": "خمسة",
          "six": "ستة", "seven": "سبعة", "eight": "ثمانية", "nine": "تسعة", "yes": "نعم", "no": "لا", "ok": "موافق",
          "stop": "توقف", "cancel": "إلغاء", "open": "افتح", "close": "أغلق", "start": "ابدأ", "send": "أرسل",
          "next": "التالي", "previous": "السابق", "right": "يمين", "left": "يسار", "up": "أعلى", "down": "أسفل",
          "enable": "تفعيل", "record": "تسجيل", "options": "خيارات", "direction": "اتجاه", "digit": "رقم"}
AGENT_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "zero", "open", "close",
               "start", "send", "next", "previous", "right", "left", "up", "down", "enable", "record", "options",
               "direction", "digit"]
SPLITS = {
    "xdev": {"letter": "D", "split": "dev", "asc": ["train"], "folds": {"1", "2", "3"}, "seed": 5101},
    "holdout": {"letter": "H", "split": "reserve", "asc": ["val", "test"], "folds": {"4", "5"}, "seed": 9202},
}
# Scenario templates. words: caller words of the event; rel: event level relative to the caller's own greeting (dB);
# gain: microphone gain applied to the whole caller channel (dB); bed: (ESC category, SNR dB) continuous noise;
# nb: telephone band; cold: the caller says nothing before the event; at0: the event starts with the agent.
TEMPLATES = [
    dict(n=51, cat="GI", sub="interruption.multiword", exp="STOP", words=["no", "no", "stop"]),
    dict(n=52, cat="GI", sub="interruption.single_word", exp="STOP", words=["stop"]),
    dict(n=53, cat="GI", sub="interruption.backchannel_opener", exp="STOP", words=["yes", "no", "cancel"], nb=True,
         bed=("engine", 12)),
    dict(n=54, cat="GI", sub="interruption.soft", exp="STOP", words=["no", "stop", "cancel"], rel=-6),
    dict(n=55, cat="GI", sub="interruption.low_gain", exp="STOP", words=["no", "no", "cancel"], gain=-12),
    dict(n=56, cat="GI", sub="interruption.high_gain", exp="STOP", words=["no", "stop"], gain=6),
    dict(n=57, cat="GI", sub="interruption.low_snr", exp="STOP", words=["no", "no", "stop"], bed=("rain", 5)),
    dict(n=58, cat="GI", sub="interruption.no_prior_caller_speech", exp="STOP", words=["no", "stop"], cold=True),
    dict(n=59, cat="GI", sub="interruption.over_echo", exp="STOP", words=["no", "no", "stop"], echo=True),
    dict(n=51, cat="OV", sub="overlap.simultaneous_start", exp="STOP", words=["no", "cancel"], at0=True),
    dict(n=52, cat="OV", sub="overlap.talk_over", exp="STOP", words=["no", "stop", "no", "cancel", "stop"], rel=3),
    dict(n=51, cat="BC", sub="backchannel.single_soft", exp="KEEP", words=["yes"], rel=-6),
    dict(n=52, cat="BC", sub="backchannel.repeated", exp="KEEP", words=["yes"], rel=-6, nb=True,
         more=[(["ok"], -6), (["yes"], -10)]),
    dict(n=53, cat="BC", sub="backchannel.normal_level", exp="KEEP", words=["yes"], rel=0),
    dict(n=54, cat="BC", sub="backchannel.double", exp="KEEP", words=["yes", "yes"], rel=-6, bed=("rain", 15)),
    dict(n=55, cat="BC", sub="backchannel.low_gain", exp="KEEP", words=["ok"], rel=-6, gain=-12),
    dict(n=56, cat="BC", sub="backchannel.high_gain", exp="KEEP", words=["yes"], rel=-6, gain=6),
    dict(n=51, cat="BG", sub="background.tv", exp="KEEP", tv=-14),
    dict(n=52, cat="BG", sub="background.horn", exp="KEEP", sound="car_horn", rel=0, bed=("engine", 10)),
    dict(n=53, cat="BG", sub="background.side_speech", exp="KEEP", side=["open", "close", "send", "next"], rel=-8),
    dict(n=54, cat="BG", sub="background.abrupt_noise", exp="KEEP", sound="vacuum_cleaner", rel=-6, long=True),
    dict(n=51, cat="NV", sub="nonverbal.cough", exp="KEEP", sound="coughing", rel=0),
    dict(n=52, cat="NV", sub="nonverbal.laugh", exp="KEEP", sound="laughing", rel=-3),
    dict(n=53, cat="NV", sub="nonverbal.echo", exp="KEEP", echo=True),
    dict(n=54, cat="NV", sub="nonverbal.transient", exp="KEEP", sound="door_wood_knock", rel=0),
]


# ---------------------------------------------------------------- sources
def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sS", "-f", "--retry", "3", "-m", "60", "-o", str(dest), url], check=True)
    return dest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_mono(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        rate = w.getframerate()
    if rate != SR:
        from scipy.signal import resample_poly
        g = np.gcd(SR, rate)
        x = resample_poly(x, SR // g, rate // g)
    return x


class Sources:
    """Download-on-demand cache with a record of every file used (url, sha256, licence, attribution)."""

    def __init__(self):
        self.used: dict[str, dict] = {}
        self.asc = {s: sorted({r["file"].split("/")[-1].split("_")[0] for r in csv.DictReader(
            open(fetch(f"{ASC_URL}/{s}.csv", SRC / "asc" / f"{s}.csv"), encoding="utf-8"))}) for s in ("train", "val", "test")}
        self.esc_rows = list(csv.DictReader(open(fetch(f"{ESC_URL}/meta/esc50.csv", SRC / "esc" / "esc50.csv"))))
        self.esc_lic = {}
        for line in open(fetch(f"{ESC_URL}/LICENSE", SRC / "esc" / "LICENSE"), encoding="utf-8"):
            m = re.match(r"- \[(\d)-(\d+)-([A-Z])\.ogg\]: clip derived from (.*) \((http[^)]*)\) by (.*) \[(.*)\]\s*$", line)
            if m:
                self.esc_lic[(m.group(1), m.group(2), m.group(3))] = {"licence": m.group(7), "author": m.group(6),
                                                                      "url": m.group(5), "title": m.group(4)}

    def word(self, speaker: str, word: str, take: int) -> np.ndarray:
        rel = f"dataset/{word}/{speaker}_NO_{take:02d}.wav"
        p = fetch(f"{ASC_URL}/{rel}", SRC / "asc" / rel)
        self.used[f"asc:{rel}"] = {"url": f"{ASC_URL}/{rel}", "sha256": sha256(p), "licence": "CC BY 4.0",
                                   "attribution": "Arabic Speech Commands Dataset (Ghandoura, Hjabo, Al Dakkak, 2021)"}
        return trim(read_mono(p))

    def esc_pick(self, category: str, folds: set, rng, k: int = 1) -> list[dict]:
        ok = [r for r in self.esc_rows if r["category"] == category and r["fold"] in folds
              and self.esc_lic.get((r["fold"], r["src_file"], r["take"]), {}).get("licence") in ("CC0", "CC-BY")]
        ok.sort(key=lambda r: r["filename"])
        return [ok[i] for i in rng.permutation(len(ok))[:k]]

    def esc(self, row: dict) -> np.ndarray:
        p = fetch(f"{ESC_URL}/audio/{row['filename']}", SRC / "esc" / "audio" / row["filename"])
        lic = self.esc_lic[(row["fold"], row["src_file"], row["take"])]
        self.used[f"esc:{row['filename']}"] = {"url": f"{ESC_URL}/audio/{row['filename']}", "sha256": sha256(p),
                                               "licence": lic["licence"], "category": row["category"],
                                               "attribution": f"{lic['title']} by {lic['author']} ({lic['url']}), via ESC-50"}
        return read_mono(p)


# ---------------------------------------------------------------- signal helpers
def frame_db(x: np.ndarray, n: int = 320) -> np.ndarray:
    f = x[:len(x) // n * n].reshape(-1, n)
    return 10 * np.log10(np.mean(f ** 2, axis=1) + 1e-9)


def trim(x: np.ndarray, below_peak_db: float = 30, pad_ms: int = 20) -> np.ndarray:
    """Cut leading/trailing parts more than below_peak_db under the loudest 20 ms frame; 10 ms fades."""
    db = frame_db(x)
    act = np.where(db > db.max() - below_peak_db)[0]
    a = max(0, act[0] * 320 - pad_ms * 16)
    b = min(len(x), (act[-1] + 1) * 320 + pad_ms * 16)
    y = x[a:b].copy()
    f = min(160, len(y) // 2)
    y[:f] *= np.linspace(0, 1, f)
    y[-f:] *= np.linspace(1, 0, f)
    return y


def active_rms_db(x: np.ndarray) -> float:
    db = frame_db(x)
    return float(10 * np.log10(np.mean(10 ** (db[db > db.max() - 20] / 10))) - 20 * np.log10(32768))


def to_level(x: np.ndarray, db: float) -> np.ndarray:
    return x * 10 ** ((db - active_rms_db(x)) / 20)


def loop(x: np.ndarray, n: int) -> np.ndarray:
    """Repeat a clip to n samples with 100 ms cross-fades."""
    x = trim(x, 40)
    f = min(1600, len(x) // 4)
    out = x.copy()
    while len(out) < n:
        nxt = x.copy()
        out[-f:] = out[-f:] * np.linspace(1, 0, f) + nxt[:f] * np.linspace(0, 1, f)
        out = np.concatenate([out, nxt[f:]])
    return out[:n]


def room(x: np.ndarray, rng, rt60: float = 0.35, lowpass_hz: float | None = 3000) -> np.ndarray:
    """Far-field colouring: synthetic exponentially decaying impulse response and an optional low-pass."""
    from scipy.signal import butter, fftconvolve, sosfilt
    n = int(SR * rt60)
    h = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    h[0] = 1.0
    y = fftconvolve(x, h / np.sqrt(np.sum(h ** 2)))[:len(x)]
    if lowpass_hz:
        y = sosfilt(butter(4, lowpass_hz, "low", fs=SR, output="sos"), y)
    return y


def telephone(x: np.ndarray) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, [300, 3400], "band", fs=SR, output="sos"), x)


def say(src: Sources, speaker: str, words: list[str], takes: list[int], gap_ms, rng) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Concatenate one speaker's keyword clips. Returns the signal and each word's (start, end) sample range."""
    parts, spans, pos = [], [], 0
    for w, t in zip(words, takes):
        clip = src.word(speaker, w, t)
        spans.append((pos, pos + len(clip)))
        gap = int(SR * rng.uniform(*gap_ms) / 1000)
        parts += [clip, np.zeros(gap)]
        pos += len(clip) + gap
    return np.concatenate(parts)[:spans[-1][1]], spans


def ms(samples: int) -> int:
    return int(round(samples * 1000 / SR / 10)) * 10


# ---------------------------------------------------------------- one scenario
def build_scenario(src: Sources, cfg: dict, tpl: dict, idx: int, speakers: dict) -> tuple[np.ndarray, dict, dict]:
    rng = np.random.default_rng(cfg["seed"] + 97 * idx)
    sid = f"{tpl['cat']}-{cfg['letter']}{tpl['n']}"
    caller = speakers["callers"][idx % len(speakers["callers"])]
    agent_spk = speakers["agents"][idx % len(speakers["agents"])]
    caller_db, agent_db = -26.0, -23.0
    takes = lambda k, first: [(first + i - 1) % 10 + 1 for i in range(k)]            # noqa: E731

    # agent channel: greeting A0, then the long utterance A1
    a0_words = list(rng.choice(AGENT_WORDS, 3, replace=False))
    a1_words = list(rng.choice(AGENT_WORDS, 14, replace=True))
    a0, _ = say(src, agent_spk, a0_words, takes(3, 1), (90, 160), rng)
    a1, _ = say(src, agent_spk, a1_words, takes(14, 4), (90, 220), rng)
    u0, _ = say(src, caller, ["yes", "ok"], takes(2, 1), (120, 200), rng)
    a0_at = int(0.3 * SR)
    u0_at = a0_at + len(a0) + int(0.35 * SR)
    a1_at = (u0_at + (0 if tpl.get("cold") else len(u0)) + int(0.4 * SR)) // 320 * 320
    total = a1_at + len(a1) + int(1.5 * SR)
    agent = np.zeros(total)
    lvl_a = 10 ** ((agent_db - active_rms_db(np.concatenate([a0, a1]))) / 20)
    agent[a0_at:a0_at + len(a0)] = a0 * lvl_a
    agent[a1_at:a1_at + len(a1)] = a1 * lvl_a

    user = rng.standard_normal(total) * 10 ** (-64 / 20) * 32768                       # room tone
    segments, events, bg, noise_ids = [], [], [], ["SYN-room-tone"]
    lvl_u = 10 ** ((caller_db - active_rms_db(u0)) / 20)                               # this caller's normal level
    if not tpl.get("cold"):
        user[u0_at:u0_at + len(u0)] += u0 * lvl_u
        segments.append({"seg_id": "U0", "speaker_id": "S001", "start_ms": ms(u0_at), "end_ms": ms(u0_at + len(u0)),
                         "kind": "speech", "text": "نعم، موافق."})

    def add_event(eid, onset, offset, expected, seg_ids, rationale, evident=None):
        ev = {"event_id": f"{sid}.{eid}", "kind": "interruption", "expected": expected, "segment_ids": seg_ids,
              "onset_ms": ms(onset), "offset_ms": ms(offset), "rationale": rationale}
        if expected == "STOP":
            ev["evident_ms"] = ms(evident)
        else:
            ev["window_end_ms"] = ms(offset) + 500
        events.append(ev)

    t_ev = a1_at if tpl.get("at0") else a1_at + int(SR * rng.uniform(3.0, 4.2))
    if "words" in tpl:
        todo = [(tpl["words"], tpl.get("rel", 0))] + tpl.get("more", [])
        for j, (words, rel) in enumerate(todo):
            sig, spans = say(src, caller, words, takes(len(words), 3 + 2 * j), (70, 140), rng)
            sig = sig * lvl_u * 10 ** (rel / 20)
            at = t_ev + j * int(2.6 * SR)
            user[at:at + len(sig)] += sig
            seg = f"U{j + 1}"
            text = " ".join(ARABIC[w] for w in words)
            if tpl["exp"] == "STOP":
                first = next(i for i, w in enumerate(words) if w in ("no", "stop", "cancel"))
                segments.append({"seg_id": seg, "speaker_id": "S001", "start_ms": ms(at), "end_ms": ms(at + len(sig)),
                                 "kind": "speech", "text": text})
                add_event(f"e{j + 1}", at, at + len(sig), "STOP", [seg],
                          "Explicit objection / stop word(s) from the caller.", evident=at + spans[first][1])
            else:
                segments.append({"seg_id": seg, "speaker_id": "S001", "start_ms": ms(at), "end_ms": ms(at + len(sig)),
                                 "kind": "backchannel", "text": text})
                add_event(f"e{j + 1}", at, at + len(sig), "KEEP", [seg], "Acknowledgement word(s) only.")
    if "sound" in tpl:
        row = src.esc_pick(tpl["sound"], cfg["folds"], rng)[0]
        clip = src.esc(row)
        clip = loop(clip, int(4.0 * SR)) if tpl.get("long") else trim(clip, 25)[:int(2.5 * SR)]
        clip = to_level(clip, caller_db + tpl.get("rel", 0))
        f = min(160, len(clip) // 2)
        clip[-f:] *= np.linspace(1, 0, f)
        user[t_ev:t_ev + len(clip)] += clip[:total - t_ev]
        noise_ids.append(f"ESC50-{row['filename']}")
        kind = "nonverbal" if tpl["cat"] == "NV" else None
        if kind:
            segments.append({"seg_id": "X1", "speaker_id": "S001", "start_ms": ms(t_ev), "end_ms": ms(t_ev + len(clip)),
                             "kind": "nonverbal", "delivery": f"recorded {tpl['sound']}"})
        else:
            bg.append({"kind": "other", "start_ms": ms(t_ev), "end_ms": ms(t_ev + len(clip)), "level_db": tpl.get("rel", 0)})
        add_event("e1", t_ev, t_ev + len(clip), "KEEP", ["X1"] if kind else [], f"Recorded {tpl['sound']}; nothing said.")
    if "side" in tpl:
        other = speakers["background"][idx % len(speakers["background"])]
        sig, _ = say(src, other, tpl["side"], takes(len(tpl["side"]), 2), (150, 300), rng)
        sig = room(sig, rng)
        sig = to_level(sig, caller_db + tpl["rel"])
        user[t_ev:t_ev + len(sig)] += sig
        segments.append({"seg_id": "X1", "speaker_id": "S002", "start_ms": ms(t_ev), "end_ms": ms(t_ev + len(sig)),
                         "kind": "side_speech", "text": " ".join(ARABIC[w] for w in tpl["side"])})
        add_event("e1", t_ev, t_ev + len(sig), "KEEP", ["X1"], "Another person talking in the room; the caller says nothing.")
    if "tv" in tpl:
        other = speakers["background"]
        parts = []
        while sum(map(len, parts)) < total:
            spk = other[len(parts) % len(other)]
            w = list(rng.choice(AGENT_WORDS, 5, replace=False))
            parts.append(np.concatenate([say(src, spk, w, takes(5, 1 + len(parts) % 6), (60, 180), rng)[0],
                                         np.zeros(int(SR * rng.uniform(0.2, 0.5)))]))
        tv = to_level(room(np.concatenate(parts)[:total], rng, rt60=0.45, lowpass_hz=2800), caller_db + tpl["tv"])
        user += tv
        bg.append({"kind": "tv_speech", "start_ms": 0, "end_ms": ms(total), "level_db": tpl["tv"]})
        add_event("e1", a1_at + int(0.5 * SR), a1_at + len(a1) - int(1.0 * SR), "KEEP", [],
                  "Background broadcast speech from other speakers; not the caller.")
    echo = None
    if tpl.get("echo"):
        delay = int(rng.choice([120, 180, 240, 320]))
        level = float(rng.choice([-15, -18, -21]))
        e = room(np.concatenate([np.zeros(delay * 16), agent])[:total], rng, rt60=0.25, lowpass_hz=3400)
        user += e * 10 ** ((agent_db + level - active_rms_db(e)) / 20) * 10 ** (-(agent_db - active_rms_db(agent)) / 20)
        echo = {"level_db": level, "delay_ms": delay}
        if tpl["exp"] == "KEEP":
            add_event("e1", a1_at + delay * 16, a1_at + len(a1) - int(1.0 * SR), "KEEP", [],
                      "Speakerphone: the agent's own voice comes back; nobody new is talking.")
    snr = 38.0
    if "bed" in tpl:
        cat, snr = tpl["bed"]
        row = src.esc_pick(cat, cfg["folds"], rng)[0]
        user += to_level(loop(src.esc(row), total), caller_db - snr)
        noise_ids.append(f"ESC50-{row['filename']}")
        bg.append({"kind": "traffic" if cat == "engine" else "other", "start_ms": 0, "end_ms": ms(total), "level_db": -snr})
    if tpl.get("nb"):
        user = telephone(user)
    user *= 10 ** (tpl.get("gain", 0) / 20)

    audio = np.stack([agent, user], axis=1)
    audio = np.clip(np.round(audio), -32768, 32767).astype(np.int16)
    cond = (["phone-nb"] if tpl.get("nb") else []) + (["speakerphone"] if echo else []) + (["tv"] if "tv" in tpl else []) \
        + ({"engine": ["car"], "rain": ["street"]}.get(tpl["bed"][0], ["household"]) if "bed" in tpl else [])
    label = {
        "schema_version": "1.0", "scenario_id": sid, "split": cfg["split"], "category": tpl["cat"], "subtype": tpl["sub"],
        "difficulty": 2, "dialect": "msa", "elicitation": "scripted",
        "speakers": [{"speaker_id": "S001", "role": "caller", "source": "human", "gender": "m", "age_band": "25-34",
                      "dialect": "msa", "consent_id": f"ASC-CC-BY-4.0-{caller}"}],
        "agent_voice": "agent-m1",
        "acoustic": {"conditions": cond or ["quiet"], "snr_db": snr, **({"echo": echo} if echo else {}),
                     "noise_source_ids": noise_ids,
                     "notes": "ASC human keyword recordings (gender/age not published; placeholders) mixed digitally"},
        "audio": {"file": f"{sid}.wav", "sample_rate": SR, "channels": ["agent", "user"], "duration_ms": len(audio) * 1000 // SR,
                  "sha256": ""},
        "context": {"use_case": "extra evaluation", "language": "ar"},
        "agent_utterances": [
            {"utt_id": "A0", "start_ms": ms(a0_at), "end_ms": ms(a0_at + len(a0)), "text": " ".join(ARABIC[w] for w in a0_words),
             "is_question": True},
            {"utt_id": "A1", "start_ms": ms(a1_at), "end_ms": ms(a1_at + len(a1)), "text": " ".join(ARABIC[w] for w in a1_words),
             "is_question": False}],
        "user_segments": segments, "background_events": bg, "events": events,
        "provenance": {"script_ref": f"tools/build_extra.py#{tpl['cat']}-{tpl['n']}", "production_mode": "human_keywords_mixed",
                       "rendered_on": "2026-11-17", "reviewed_by": ["unreviewed"], "label_status": "script_derived",
                       "notes": "Built from ASC (CC BY 4.0) keyword recordings and CC0/CC-BY ESC-50 clips. Labels derived "
                                "from the script and exact placement, NOT validated by native listeners."}}
    design = {"scenario_id": sid, "caller": caller, "agent_speaker": agent_spk, "rel_level_db": tpl.get("rel", 0),
              "mic_gain_db": tpl.get("gain", 0), "telephone_band": bool(tpl.get("nb")), "bed": tpl.get("bed"),
              "echo": echo, "no_prior_caller_speech": bool(tpl.get("cold")), "starts_with_agent": bool(tpl.get("at0"))}
    return audio, label, design


# ---------------------------------------------------------------- splits
def write_wav(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(data, dtype="<i2").tobytes())


def build_split(name: str, src: Sources) -> dict:
    cfg = SPLITS[name]
    pool = sorted(s for part in cfg["asc"] for s in src.asc[part])
    speakers = {"agents": pool[:2], "background": pool[2:4], "callers": pool[4:]}
    out = OUT / name
    thresholds = json.loads((KIT / "data" / "dev" / "manifest.json").read_text(encoding="utf-8"))["thresholds"]
    entries, designs = [], []
    for idx, tpl in enumerate(TEMPLATES):
        audio, label, design = build_scenario(src, cfg, tpl, idx, speakers)
        sid = label["scenario_id"]
        write_wav(out / "audio" / f"{sid}.wav", audio)
        label["audio"]["sha256"] = sha256(out / "audio" / f"{sid}.wav")
        lp = out / "labels" / f"{sid}.json"
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps(label, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        entries.append({"scenario_id": sid, "wav_sha256": label["audio"]["sha256"], "label_sha256": sha256(lp)})
        designs.append(design)
    manifest = {"dataset": "sarj-extra-eval", "version": "p3-1", "split": name, "created": "2026-11-17",
                "label_status": "script_derived", "audio_source": "ASC human keywords (CC BY 4.0) + CC0/CC-BY ESC-50 clips, mixed digitally",
                "thresholds": thresholds, "speakers": speakers, "scenarios": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "design.json").write_text(json.dumps(designs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"speakers": speakers, "n": len(entries)}


def verify(name: str) -> list[str]:
    """Checks that need no detector: hashes, layout, and that each labelled sound is really in its window."""
    out, problems = OUT / name, []
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for e in manifest["scenarios"]:
        sid = e["scenario_id"]
        label = json.loads((out / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
        wav = out / "audio" / f"{sid}.wav"
        if sha256(wav) != e["wav_sha256"]:
            problems.append(f"{sid}: audio differs from the manifest (rebuild is not bit-exact)")
            continue
        with wave.open(str(wav)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2).astype(np.float64)
        db = frame_db(x[:, 1])
        agent = [(u["start_ms"], u["end_ms"]) for u in label["agent_utterances"]]
        for ev in label["events"]:
            a, b = ev["onset_ms"] // 20, max(ev["onset_ms"] // 20 + 1, ev["offset_ms"] // 20)
            if not any(s <= ev["onset_ms"] <= t for s, t in agent):
                problems.append(f"{ev['event_id']}: onset is outside agent speech")
            if ev["expected"] == "STOP" and not ev["onset_ms"] < ev["evident_ms"] <= ev["offset_ms"]:
                problems.append(f"{ev['event_id']}: evident is not inside the caller's speech")
            if "tv" in label["subtype"] or "echo" in label["subtype"]:
                continue                                                    # sound present throughout by design
            before = db[max(0, a - 50):a - 2]
            if np.percentile(db[a:b], 90) - np.percentile(before, 50) < 6:
                problems.append(f"{ev['event_id']}: labelled sound is not at least 6 dB above the preceding second")
    return problems


def write_sources(src: Sources, info: dict) -> None:
    (OUT / "sources.json").write_text(json.dumps(src.used, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    if not a.verify:
        src = Sources()
        info = {name: build_split(name, src) for name in SPLITS}
        shared = set(sum(info["xdev"]["speakers"].values(), [])) & set(sum(info["holdout"]["speakers"].values(), []))
        assert not shared, f"speakers shared between splits: {shared}"
        write_sources(src, info)
        print(f"Built {info['xdev']['n']} xdev and {info['holdout']['n']} holdout scenarios from {len(src.used)} source files.")
    problems = [p_ for name in SPLITS for p_ in verify(name)]
    print("\n".join(problems) if problems else "verify: all hashes, layouts and labelled windows are consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
