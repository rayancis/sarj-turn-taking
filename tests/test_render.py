import numpy as np

from tests.fixtures import tiny_split
from ttbench.render import heard


def test_agent_muted_between_stop_and_resume():
    label, audio = next((l, a) for l, a in tiny_split() if l["scenario_id"] == "GI-D90")
    audio = audio.copy()
    audio[:, 1] = 0                                              # caller silent: output = agent only
    out = heard(audio, label, [(1000.0, "STOP"), (3000.0, "RESUME")]).astype(float)
    sr = 16000
    before = np.abs(out[int(0.5 * sr):int(0.9 * sr)]).mean()
    during = np.abs(out[int(1.2 * sr):int(2.9 * sr)]).mean()
    after = np.abs(out[int(3.2 * sr):int(4.0 * sr)]).mean()
    assert before > 100 and after > 100 and during < 1
