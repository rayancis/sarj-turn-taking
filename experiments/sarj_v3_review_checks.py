"""Review-only safety checks with synthetic frame features and a deferred ASR result."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'demo/models/sarj_v3'))
from tt.config import Config
from tt.decider import Decider

Z = np.zeros(320, dtype=np.int16)

class Deferred:
    busy = True
    result = None
    def poll(self, now):
        result, self.result = self.result, None
        return result

def prepared(words=None, **cfg):
    d = Decider(Config(**cfg), words=words)
    d.fe = SimpleNamespace(process=lambda a, c: SimpleNamespace(
        is_speech=True, snr_db=30.0, excess_db=-20.0, agent_db=-20.0, flux=1.0))
    d.agent_start('agent context', 10000)
    d.run_seq = 1
    d.run_start = 220.0
    d.run_start_abs = 220.0
    d.run_speech = 200.0
    d.run_gap = 0.0
    d.run_levels = [-20.0]
    d.t = 400.0
    return d

@pytest.mark.parametrize('kind', ['scenario', 'utterance', 'run'])
def test_stale_asr_cannot_stop_new_state(kind):
    w = Deferred()
    old = prepared(w)
    req = (id(old), old.utt_seq, old.run_seq)
    if kind == 'scenario':
        current = prepared(w)
    else:
        current = old
        if kind == 'utterance':
            current.agent_start('new agent turn', 10000)
        else:
            current.run_seq += 1
    w.result = (req, 'توقف')
    assert current.frame(Z, Z) is None
    assert current.word_verdict is None

def test_current_word_can_stop_but_no_duplicate_stop():
    w = Deferred()
    d = prepared(w)
    w.result = ((id(d), d.utt_seq, d.run_seq), 'توقف')
    assert d.frame(Z, Z) == 'STOP'
    assert d.frame(Z, Z) is None
    d.agent_end()
    assert d.frame(Z, Z) is None

def test_late_stop_word_ignored_after_silence_limit():
    w = Deferred()
    d = prepared(w)
    d.fe = SimpleNamespace(process=lambda a, c: SimpleNamespace(
        is_speech=False, snr_db=0.0, excess_db=-90.0, agent_db=-20.0, flux=0.0))
    d.run_gap = d.cfg.word_stop_max_gap_ms + 20
    w.result = ((id(d), d.utt_seq, d.run_seq), 'توقف')
    assert d.frame(Z, Z) is None

def test_resume_waits_for_silence_then_rearms_stop():
    d = prepared(resume=True)
    d.stopped, d.stop_t, d.t = True, 400.0, 400.0
    assert d.frame(Z, Z) is None
    d.fe = SimpleNamespace(process=lambda a, c: SimpleNamespace(
        is_speech=False, snr_db=0.0, excess_db=-90.0, agent_db=-20.0, flux=0.0))
    d.run_gap = d.cfg.resume_gap_ms
    assert d.frame(Z, Z) == 'RESUME'
    assert not d.stopped and d.n_resumes == 1
    d.fe = SimpleNamespace(process=lambda a, c: SimpleNamespace(
        is_speech=True, snr_db=30.0, excess_db=-20.0, agent_db=-20.0, flux=1.0))
    subsequent = [d.frame(Z, Z) for _ in range(100)]
    assert [action for action in subsequent if action] == ['STOP']
    assert d.stopped and d.n_resumes == 1

def test_no_history_growth_after_terminal_stop():
    d = prepared()
    while not d.stopped:
        d.frame(Z, Z)
    before = tuple(len(x) for x in (d.run_frames, d.run_levels, d.run_flux))
    for _ in range(10000):
        d.frame(Z, Z)
    assert d.stopped
    after = tuple(len(x) for x in (d.run_frames, d.run_levels, d.run_flux))
    # With RESUME disabled, STOP is terminal until the next agent utterance.
    assert after == before, f'unused history grows after terminal STOP: {before} -> {after}'
