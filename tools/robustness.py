"""Matched robustness matrix for any system file (same signals for every candidate).

    python tools/robustness.py my_system.py --dev
    git show 8f08311:my_system.py > /tmp/e01.py && python tools/robustness.py /tmp/e01.py --dev

Part A: generated signals (agent talks from 300 ms; scenario_start before the first frame).
Part B (--dev): official scorer on the dev set under amplitude, timing and added-noise perturbations
(in memory; files untouched). Diagnostic only; nothing here selects parameters.
"""
import importlib.util, sys, json
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(REPO), str(REPO / "tools")]
SR = 16000

def load(path):
    spec = importlib.util.spec_from_file_location("cand_" + Path(path).stem, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def amp(db): return 10 ** (db / 20) * 32768
def norm(x, db): return x / np.sqrt(np.mean(x ** 2)) * amp(db)
def white(ms, db, seed=0): return norm(np.random.default_rng(seed).standard_normal(SR * ms // 1000), db)
def rumble(ms, db, seed=1):
    x = np.cumsum(np.random.default_rng(seed).standard_normal(SR * ms // 1000)); x -= np.convolve(x, np.ones(400) / 400, "same")
    return norm(x, db)
def harmonic(ms, f0, n=None):
    t = np.arange(SR * ms // 1000 if n is None else n) / SR
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * 3 * t))                  # gentle intonation
    ph = 2 * np.pi * np.cumsum(f) / SR
    return sum(np.sin(k * ph) / k for k in range(1, int(3400 // f0) + 1))
def syllabic(ms, db, f0=140.0, rate=4.0, depth=0.4):
    t = np.arange(SR * ms // 1000) / SR
    return norm(harmonic(ms, f0) * ((1 - depth) + depth * np.sin(2 * np.pi * rate * t)), db)
def speechlike(ms, db, f0=130.0, seed=3):
    """Syllables of 120-220 ms voiced nuclei separated by 30-90 ms low-level unvoiced gaps (fricative noise)."""
    rng = np.random.default_rng(seed); out = []
    while sum(map(len, out)) < SR * ms // 1000:
        v = int(SR * rng.uniform(0.12, 0.22)); g = int(SR * rng.uniform(0.03, 0.09))
        out += [harmonic(0, f0 * rng.uniform(0.9, 1.15), n=v) * np.hanning(v) ** 0.3, 0.05 * rng.standard_normal(g)]
    return norm(np.concatenate(out)[:SR * ms // 1000], db)
def steady(ms, db, f0=140.0): return norm(harmonic(ms, f0), db)
def laugh(db, calls=6, voiced=100, breath=100, f0=260.0, seed=4):
    rng = np.random.default_rng(seed); out = []
    for _ in range(calls):
        v = harmonic(voiced, f0 * rng.uniform(0.9, 1.1)) * np.hanning(SR * voiced // 1000)
        out += [v, 0.25 * rng.standard_normal(SR * breath // 1000) * np.hanning(SR * breath // 1000)]
    return norm(np.concatenate(out), db)
def cough(db, seed=5):
    rng = np.random.default_rng(seed); n = SR * 350 // 1000
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (SR * 0.08)); x[:80] *= np.linspace(0, 1, 80)
    return norm(x, db)
def engine(ms, db, f0=110.0, seed=8):
    """Car-engine-like drone: steady periodic harmonics (random amplitudes) plus aperiodic road noise 6 dB down."""
    rng = np.random.default_rng(seed); t = np.arange(SR * ms // 1000) / SR
    h = sum(rng.uniform(0.3, 1.0) / k * np.sin(2 * np.pi * k * f0 * t + rng.uniform(0, 6.3)) for k in range(1, 30))
    return norm(norm(h, 0) + norm(rumble(ms, 0, seed + 1), -6) + norm(white(ms, 0, seed + 2), -20), db)
def mix(total, *parts):
    out = np.zeros(SR * total // 1000)
    for start, sig in parts:
        a = SR * start // 1000; out[a:a + len(sig)] += sig[:max(0, len(out) - a)]
    return np.clip(out, -32768, 32767).astype(np.int16)

def stops(mod, caller, agent=(300, None)):
    s = mod.MySystem(); s.on_scenario_start({"use_case": "t", "language": "ar"})
    a0, a1 = agent[0], agent[1] or len(caller) * 1000 // SR
    out = []; z = np.zeros(320, np.int16)
    for n in range(len(caller) // 320):
        t = n * 20
        if t == a0: s.on_agent_start("x", a1 - a0)
        if t == a1: s.on_agent_end()
        d = s.on_frame(z, caller[n * 320:(n + 1) * 320])
        if d == "STOP" or (isinstance(d, dict) and d.get("decision") == "STOP"): out.append(t)
    return out

def part_a(mod):
    rows = []
    def want_stop(name, caller, onset, budget=600):
        s = stops(mod, caller); hit = [x for x in s if x >= onset - 20]
        early = [x for x in s if x < onset - 20]
        ok = bool(hit) and not early and hit[0] - onset <= budget
        rows.append((name, "STOP", ok, ("early@%d" % early[0]) if early else (f"+{hit[0]-onset} ms" if hit else "miss")))
    def want_keep(name, caller):
        s = stops(mod, caller); rows.append((name, "KEEP", not s, f"stop@{s[0]}" if s else "kept"))
    for nz_name, nz in (("white", white), ("rumble", rumble)):
        for snr in (0, 3, 6, 10, 15, 20):
            for v_name, v in (("syllabic", syllabic), ("speechlike", speechlike)):
                want_stop(f"{v_name} {snr:>2} dB SNR in {nz_name} -40", mix(8000, (0, nz(8000, -40)), (4000, v(2000, -40 + snr))), 4000)
    for g in (-12, -6, 0, 6):
        want_stop(f"speechlike gain {g:+d} dB (voice {-22+g}, noise {-50+g})", mix(7000, (0, white(7000, -50 + g)), (3000, speechlike(2000, -22 + g))), 3000)
    for off in (0, 7, 13):
        want_stop(f"onset sub-frame offset {off} ms", mix(7000, (0, white(7000, -50)), (3000 + off, speechlike(2000, -22))), 3000 + off)
    for v_name, v in (("steady", steady), ("syllabic", syllabic), ("speechlike", speechlike)):
        want_stop(f"cold start: {v_name} voice from frame 0", mix(6000, (0, white(6000, -65)), (0, v(6000, -25))), 300)
        want_stop(f"cold start: {v_name} voice in -40 noise", mix(6000, (0, white(6000, -40)), (0, v(6000, -22))), 300)
    for d in ((100, 100), (80, 120), (120, 80)):
        want_keep(f"laughter {d[0]} voiced/{d[1]} breath ms", mix(6000, (0, white(6000, -65)), (2000, laugh(-22, voiced=d[0], breath=d[1]))))
    want_keep("laughter in -45 noise", mix(6000, (0, white(6000, -45)), (2000, laugh(-25))))
    want_keep("two coughs", mix(6000, (0, white(6000, -65)), (2000, cough(-20)), (2700, cough(-20, seed=6))))
    for on, off in ((40, 100), (60, 100), (80, 80)):
        want_keep(f"sparse voiced bursts {on}/{off} ms", mix(6000, (0, white(6000, -65)), *[(2000 + k * (on + off), syllabic(on, -20)) for k in range(10)]))
    for nz_name, nz in (("white", white), ("rumble", rumble)):
        for lvl in (-45, -35, -25):
            want_keep(f"noise step {nz_name} -65 -> {lvl}", mix(8000, (0, np.concatenate([nz(3000, -65), nz(5000, lvl, seed=7)]))))
        for lvl in (-40, -30):
            want_keep(f"steady {nz_name} {lvl} from frame 0", mix(8000, (0, nz(8000, lvl))))
        want_stop(f"voice 15 dB over new level after {nz_name} step", mix(10000, (0, np.concatenate([nz(3000, -65), nz(7000, -35, seed=7)])), (6500, speechlike(2000, -20))), 6500)
    want_keep("engine drone -35 from frame 0", mix(8000, (0, engine(8000, -35))))
    want_keep("engine step -65 -> -35", mix(8000, (0, np.concatenate([white(3000, -65), engine(5000, -35)]))))
    want_stop("speechlike 12 dB over engine -38", mix(8000, (0, engine(8000, -38)), (4000, speechlike(2000, -26))), 4000)
    want_stop("cold start: speechlike voice over engine -38", mix(6000, (0, engine(6000, -38)), (0, speechlike(6000, -24))), 300)
    return rows

def part_b(mod):
    import replay
    from ttbench.dataset import load_split, lay_out_stream
    from ttbench.score import score, load_labels
    labels, man = load_labels(REPO / "data/dev")
    res = []
    def run(name, fn):
        sc = load_split(REPO / "data/dev")
        for s in sc: s.audio = fn(s.audio.copy())
        log, meta = replay.replay(mod.MySystem, lay_out_stream(sc))
        r = score(labels, meta, log, man["thresholds"]); c = r["core"]
        res.append((name, c["core_score"], c["R"], c["S"], r["rates"]["misses"], r["rates"]["false_stops"], len(meta["errors"])))
    def gain(db): return lambda a: np.clip(a.astype(float) * 10 ** (db / 20), -32768, 32767).astype(np.int16)
    def shift(ms): return lambda a: np.vstack([a[:SR * ms // 1000], a])[:len(a)] if ms else a
    def addnoise(db, seed=11):
        def f(a):
            a = a.astype(float); a[:, 1] += white(len(a) * 1000 // SR + 1, db, seed)[:len(a)]
            return np.clip(a, -32768, 32767).astype(np.int16)
        return f
    run("dev as is", lambda a: a)
    for g in (-12, -6, 6): run(f"gain {g:+d} dB", gain(g))
    for ms in (3, 7, 10, 13, 17): run(f"shift +{ms} ms", shift(ms))
    for db in (-50, -40): run(f"+white noise {db} dBFS on caller", addnoise(db))
    return res

if __name__ == "__main__":
    mod = load(sys.argv[1]); rows = part_a(mod)
    ok = sum(r[2] for r in rows)
    print(f"== {sys.argv[1]}: synthetic matrix {ok}/{len(rows)} as intended")
    for r in rows: print(f"  {'ok ' if r[2] else 'BAD'} {r[1]:4s} {r[0]:52s} {r[3]}")
    if "--dev" in sys.argv:
        for name, core, R, S, miss, fs, err in part_b(mod):
            print(f"  dev {name:34s} Core {core:5.1f}  R {R:.3f}  S {S:.3f}  miss {miss}  FS {fs:2d}  err {err}")
    if "--json" in sys.argv:
        print(json.dumps({"rows": rows}))
