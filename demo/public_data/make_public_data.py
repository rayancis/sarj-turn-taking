"""Build the PUBLIC demonstration scenarios: original material, safe to serve on the public demo site.

    python demo/public_data/make_public_data.py        # needs espeak-ng and ffmpeg on PATH; rewrites audio/ labels/

Why this exists: the competition dev set (data/dev) has no public redistribution licence, so the public site must
not serve it. Everything here is created from scratch:
- Scripts: written for this demo (below), not taken from the kit's scripts or dev labels.
- Voices: eSpeak NG's built-in Arabic voice ("ar", rule-based formant synthesis, no recorded speech samples). eSpeak
  NG is GPL-3.0 software; the GPL covers the program, not the audio it generates. MBROLA voices are NOT used (their
  diphone databases carry separate licences).
- Noise, cough and door sounds: generated here from seeded random numbers.
- Labels: written by us from the scripts and the exact placement below (script-derived, not listener-validated).
The voices are robotic: these scenarios demonstrate the mechanics, they are not a benchmark.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SR = 16000
LICENSE = "CC-BY-4.0"
DATE = "2026-11-17"

AGENT_VOICE = ("ar+f3", 170)     # eSpeak NG voice variant, words per minute
CALLER_VOICE = ("ar+m3", 170)
TV_VOICE = ("ar+m6", 175)


# ------------------------------------------------------------------------------------------------ signal helpers
def db(x: float) -> float:
    return 10 ** (x / 20)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2) + 1e-20))


def tts(text: str, voice: tuple[str, int], tmp: Path) -> np.ndarray:
    """Synthesize with eSpeak NG and resample to 16 kHz mono float with ffmpeg."""
    raw, out = tmp / "raw.wav", tmp / "out.wav"
    subprocess.run(["espeak-ng", "-v", voice[0], "-s", str(voice[1]), "-w", str(raw), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", str(SR), str(out)], check=True)
    with wave.open(str(out), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    nz = np.flatnonzero(np.abs(x) > 1e-3)                         # trim leading/trailing silence
    return x[nz[0]:nz[-1] + 1] if len(nz) else x


def at_level(x: np.ndarray, level_db: float) -> np.ndarray:
    """Scale so the active (non-silent) part has this RMS in dBFS."""
    active = x[np.abs(x) > 0.02 * np.max(np.abs(x))] if len(x) else x
    return x * db(level_db) / rms(active)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    y = np.fft.irfft(spec, n)
    return y / rms(y)


def cough(rng: np.random.Generator) -> np.ndarray:
    """Two cough bursts: broadband noise, sharp attack, ~200 ms decay (aperiodic, like a real cough)."""
    out = []
    for dur in (0.24, 0.20):
        n = int(SR * dur)
        t = np.arange(n) / SR
        env = (1 - np.exp(-t / 0.008)) * np.exp(-t / 0.07)
        burst = np.convolve(rng.standard_normal(n), np.ones(6) / 6, "same") * env
        out += [burst, np.zeros(int(SR * 0.12))]
    return np.concatenate(out)


def door(rng: np.random.Generator) -> np.ndarray:
    """A door slam: low thump plus a short broadband crack."""
    n = int(SR * 0.6)
    t = np.arange(n) / SR
    thump = np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.09)
    crack = rng.standard_normal(n) * np.exp(-t / 0.02)
    return thump + 0.6 * crack


def telephone(x: np.ndarray) -> np.ndarray:
    """300-3400 Hz band limit (narrowband telephone)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec[(f < 300) | (f > 3400)] = 0
    return np.fft.irfft(spec, len(x))


# ------------------------------------------------------------------------------------------------ scenarios
# Each: agent says A0 (greeting), caller answers U0, agent says A1 (long), and during A1 the event happens.
SCENARIOS = [
    {
        "id": "GI-P01", "category": "GI", "subtype": "interruption.correction", "use_case": "delivery scheduling",
        "conditions": ["quiet"], "noise": None,
        "a0": "السلام عليكم، معك سارة من خدمة التوصيل. أكلم الأستاذ خالد؟",
        "u0": "إيه نعم، معك خالد.",
        "a1": "أهلا أستاذ خالد. طلبك جاهز وبيوصلك بكرة الصبح بين الساعة تسع وإحدى عشر، والمندوب بيتصل عليك قبل ما يوصل، "
              "وإذا حاب تغير العنوان أو الوقت تقدر ترد على الرسالة اللي بتوصلك اليوم.",
        "event": {"at": 0.35, "kind": "speech", "text": "لا لا، لحظة لو سمحت، أنا بكرة مسافر.", "expected": "STOP",
                  "rationale": "Caller rejects the time and takes the floor: a real interruption."},
    },
    {
        "id": "GI-P02", "category": "GI", "subtype": "interruption.question", "use_case": "bill payment",
        "conditions": ["street", "phone-nb"], "noise": ("street", -40),
        "a0": "مساء الخير، معك نورة من خدمة العملاء. معي الأستاذة ريم؟",
        "u0": "أيوه أنا ريم، مساء النور.",
        "a1": "أهلين أستاذة ريم. حبيت أذكرك إن فاتورة هذا الشهر مستحقة يوم الخميس، وتقدرين تسددينها من التطبيق، "
              "أو من أي فرع قريب منك، وإذا عندك أي استفسار عن المبلغ أنا موجودة.",
        "event": {"at": 0.4, "kind": "speech", "text": "طيب لحظة، كم المبلغ بالضبط؟", "expected": "STOP",
                  "rationale": "Caller asks a question over the agent in street noise: a real interruption."},
    },
    {
        "id": "BC-P01", "category": "BC", "subtype": "backchannel.short", "use_case": "appointment reminder",
        "conditions": ["quiet"], "noise": None,
        "a0": "صباح الخير، معك هند من العيادة. أكلم الأستاذ فهد؟",
        "u0": "صباح النور، إيه نعم.",
        "a1": "حياك الله. موعدك مع الدكتور يوم الأحد الساعة عشر الصبح، ونرجو الحضور قبل الموعد بربع ساعة، "
              "وإذا عندك تأمين طبي لا تنسى تجيب البطاقة معك.",
        "event": {"at": 0.45, "kind": "backchannel", "text": "إيه.", "expected": "KEEP",
                  "rationale": "A short acknowledgement: the caller is listening, not taking the floor."},
    },
    {
        "id": "NV-P01", "category": "NV", "subtype": "nonverbal.cough", "use_case": "order confirmation",
        "conditions": ["quiet"], "noise": None,
        "a0": "السلام عليكم، معك سارة من المتجر. معي الأستاذ ماجد؟",
        "u0": "وعليكم السلام، إيه معك.",
        "a1": "أهلا أستاذ ماجد. حبيت أأكد طلبك، ثلاث قطع، والمجموع مية وعشرين ريال، والدفع عند الاستلام، "
              "والتوصيل خلال يومين عمل إن شاء الله.",
        "event": {"at": 0.45, "kind": "cough", "expected": "KEEP",
                  "rationale": "A cough is not a request for the floor."},
    },
    {
        "id": "BG-P01", "category": "BG", "subtype": "background.door", "use_case": "service follow-up",
        "conditions": ["household"], "noise": ("room", -58),
        "a0": "مساء الخير، معك نورة من الصيانة. أكلم الأستاذة منى؟",
        "u0": "إيه نعم، تفضلي.",
        "a1": "أهلين أستاذة منى. الفني بيزورك يوم الثلاثاء العصر لفحص المكيف، والزيارة مجانية لأن الجهاز تحت الضمان، "
              "وبيتصل عليك الفني قبل ما يوصل بنص ساعة.",
        "event": {"at": 0.4, "kind": "door", "expected": "KEEP",
                  "rationale": "A door slam in the caller's room: background sound, not the caller."},
    },
    {
        "id": "BG-P02", "category": "BG", "subtype": "background.tv_voice", "use_case": "delivery scheduling",
        "conditions": ["tv"], "noise": ("tv", -36),
        "a0": "السلام عليكم، معك سارة من خدمة التوصيل. معي الأستاذ سعد؟",
        "u0": "وعليكم السلام، إيه نعم.",
        "a1": "أهلا أستاذ سعد. شحنتك وصلت المستودع، وبتوصلك بعد بكرة إن شاء الله، ولو حاب تستلمها من الفرع "
              "بدل التوصيل للبيت تقدر تختار هذا من التطبيق.",
        "event": {"at": 0.0, "kind": "tv", "expected": "KEEP",
                  "rationale": "A television voice runs in the background through the whole call; the caller says "
                               "nothing during the agent's message."},
    },
]
TV_TEXT = ("وفي أخبار الطقس اليوم، تتوقع الأرصاد أجواء معتدلة في أغلب المناطق، مع رياح نشطة على الأجزاء الشمالية، "
           "وفرصة لهطول أمطار خفيفة على المرتفعات الجنوبية، ودرجات الحرارة العظمى بين خمس وعشرين وثلاثين درجة.")


def build(spec: dict, tmp: Path, rng: np.random.Generator) -> tuple[dict, np.ndarray]:
    a0 = at_level(tts(spec["a0"], AGENT_VOICE, tmp), -20)
    a1 = at_level(tts(spec["a1"], AGENT_VOICE, tmp), -20)
    u0 = at_level(tts(spec["u0"], CALLER_VOICE, tmp), -22)
    ev = spec["event"]
    ms = lambda samples: int(round(len(samples) * 1000 / SR))  # noqa: E731

    t_a0 = 300
    t_u0 = t_a0 + ms(a0) + 450
    t_a1 = t_u0 + ms(u0) + 500
    total = t_a1 + ms(a1) + 1500
    n = total * SR // 1000
    agent, caller = np.zeros(n), np.zeros(n)

    def put(buf, start_ms, sig):
        a = start_ms * SR // 1000
        buf[a:a + len(sig)] += sig[:max(0, n - a)]

    put(agent, t_a0, a0)
    put(agent, t_a1, a1)
    put(caller, t_u0, u0)
    segments = [{"seg_id": "U0", "speaker_id": "S901", "start_ms": t_u0, "end_ms": t_u0 + ms(u0), "kind": "speech",
                 "text": spec["u0"]}]
    background = []

    # the event, placed `at` of the way into A1
    t_ev = t_a1 + int(ev["at"] * ms(a1))
    if ev["kind"] in ("speech", "backchannel"):
        sig = at_level(tts(ev["text"], CALLER_VOICE, tmp), -22)
        segments.append({"seg_id": "U1", "speaker_id": "S901", "start_ms": t_ev, "end_ms": t_ev + ms(sig),
                         "kind": ev["kind"], "text": ev["text"]})
    elif ev["kind"] == "cough":
        sig = at_level(cough(rng), -24)
        segments.append({"seg_id": "U1", "speaker_id": "S901", "start_ms": t_ev, "end_ms": t_ev + ms(sig),
                         "kind": "nonverbal", "delivery": "two coughs"})
    elif ev["kind"] == "door":
        sig = at_level(door(rng), -18)
        background.append({"kind": "door", "start_ms": t_ev, "end_ms": t_ev + ms(sig), "level_db": -18})
    else:
        sig = None
    if sig is not None:
        put(caller, t_ev, sig)

    # background
    if spec["noise"]:
        kind, level = spec["noise"]
        if kind == "tv":
            tv = at_level(tts(TV_TEXT, TV_VOICE, tmp), level)
            tv = np.tile(tv, int(np.ceil(n / len(tv))))[:n]
            caller += telephone(tv * 0.9)
            background.append({"kind": "tv_speech", "start_ms": 0, "end_ms": total, "level_db": level})
        else:
            caller += pink(n, rng) * db(level)
            background.append({"kind": "other" if kind == "room" else "traffic", "start_ms": 0, "end_ms": total,
                               "level_db": level})
    caller += rng.standard_normal(n) * db(-68)                    # microphone self-noise
    if "phone-nb" in spec["conditions"]:
        caller = telephone(caller)

    audio = np.stack([agent, caller], axis=1)
    peak = np.max(np.abs(audio))
    if peak > 0.95:
        audio *= 0.95 / peak
    pcm = np.round(audio * 32767).astype("<i2")

    # labels
    events = []
    if ev["kind"] == "tv":
        # one KEEP window over the whole agent message: any stop during it is unjustified
        on, off = t_a1, t_a1 + ms(a1)
        events.append({"event_id": f"{spec['id']}.e1", "kind": "interruption", "expected": "KEEP", "onset_ms": on,
                       "offset_ms": off, "window_end_ms": off + 500, "rationale": ev["rationale"]})
    else:
        seg_on, seg_off = t_ev, t_ev + ms(sig)
        e = {"event_id": f"{spec['id']}.e1", "kind": "interruption", "expected": ev["expected"], "onset_ms": seg_on,
             "offset_ms": seg_off, "rationale": ev["rationale"]}
        if ev["kind"] in ("speech", "backchannel", "cough"):
            e["segment_ids"] = ["U1"]
        if ev["expected"] == "STOP":
            e["evident_ms"] = seg_on + min(500, (seg_off - seg_on) // 3)    # after the first word
            e["deadline_ms"] = e["evident_ms"] + 1500
        else:
            e["window_end_ms"] = seg_off + 500
        events.append(e)

    label = {
        "schema_version": "1.0", "scenario_id": spec["id"], "split": "dev", "category": spec["category"],
        "subtype": spec["subtype"], "difficulty": 1, "dialect": "sa-najdi", "elicitation": "scripted",
        "speakers": [{"speaker_id": "S901", "role": "caller", "source": "synthetic", "gender": "m",
                      "age_band": "25-34", "dialect": "sa-najdi"}],
        "agent_voice": "agent-f1",                                 # schema enum; the voice is eSpeak NG ar+f3
        "acoustic": {"conditions": spec["conditions"], "notes": "procedural noise; voices: eSpeak NG ar+f3 (agent), ar+m3 (caller)"},
        "audio": {"file": f"{spec['id']}.wav", "sample_rate": SR, "channels": ["agent", "user"], "duration_ms": total,
                  "sha256": ""},
        "context": {"use_case": spec["use_case"], "language": "ar"},
        "agent_utterances": [
            {"utt_id": "A0", "start_ms": t_a0, "end_ms": t_a0 + ms(a0), "text": spec["a0"], "is_question": True},
            {"utt_id": "A1", "start_ms": t_a1, "end_ms": t_a1 + ms(a1), "text": spec["a1"], "is_question": False}],
        "user_segments": segments,
        "background_events": background,
        "events": events,
        "provenance": {"script_ref": "demo/public_data/make_public_data.py", "production_mode": "synthetic",
                       "rendered_on": DATE, "reviewed_by": ["unreviewed"], "label_status": "script_derived",
                       "notes": "Original demo material (scripts, eSpeak NG voices, procedural noise). Not competition data."},
    }
    return label, pcm


def main() -> int:
    for tool in ("espeak-ng", "ffmpeg"):
        if not shutil.which(tool):
            print(f"{tool} not found on PATH", file=sys.stderr)
            return 1
    import jsonschema
    schema = json.loads((KIT / "schema" / "scenario.schema.json").read_text(encoding="utf-8"))
    # the kit's schema, with one deviation: "-P" (public demo) ids, so they can never be mistaken for dev calls
    schema["properties"]["scenario_id"]["pattern"] = "^(GI|BC|HT|BG|NV|AM|OV|DV)-P[0-9]{2}$"
    (HERE / "audio").mkdir(exist_ok=True)
    (HERE / "labels").mkdir(exist_ok=True)
    entries = []
    with tempfile.TemporaryDirectory() as t:
        for i, spec in enumerate(SCENARIOS):
            label, pcm = build(spec, Path(t), np.random.default_rng(1000 + i))
            wav = HERE / "audio" / label["audio"]["file"]
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm.tobytes())
            sha = hashlib.sha256(wav.read_bytes()).hexdigest()
            label["audio"]["sha256"] = sha
            jsonschema.validate(label, schema)
            path = HERE / "labels" / f"{spec['id']}.json"
            path.write_text(json.dumps(label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entries.append({"scenario_id": spec["id"], "wav_sha256": sha,
                            "label_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            print(f"{spec['id']}: {label['audio']['duration_ms'] / 1000:.1f} s, event {label['events'][0]['expected']}")
    manifest = {
        "dataset": "sarj-demo-public", "version": "1.0.0", "split": "demo", "created": DATE,
        "distribution": "public", "license": LICENSE,
        "license_note": "Original synthetic demo material created for this project (scripts written for the demo, "
                        "eSpeak NG rule-based Arabic voices, procedural noise). Contains no competition data and no "
                        "recordings of people.",
        "label_status": "script_derived",
        "thresholds": {"T_full_ms": 300, "T_dead_ms": 1500, "E_full_ms": 600, "E_dead_ms": 2000, "E_miss_ms": 3000},
        "scenarios": entries,
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
