"""Production safety gates: bounded state, artifact integrity and no legacy fallback."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from .test_v3_safety import prepared, Z, Deferred
from .test_demo import demo, serving, status, post
from tt.assets import verify_assets
from tt import assets

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('resume', [False, True])
def test_thirty_minute_voiced_turn_has_bounded_memory(resume):
    d = prepared(resume=resume)
    for _ in range(90000):
        d.frame(Z, Z)
    assert d.stopped
    assert len(d.run_levels) <= 500 and len(d.run_flux) <= 500 and len(d.run_frames) <= 500
    assert len(d.audio) <= 170 and len(d.log) <= 128
    assert d.run_frame_count <= 501

def test_long_nonterminal_run_and_repeated_turns_are_bounded():
    d = prepared(stop_after_ms=1e9)
    for _ in range(20000): d.frame(Z, Z)
    assert not d.stopped and len(d.run_levels) == 500 and len(d.run_flux) == 500
    assert len(d.run_frames) == 0  # production has no classifier/training sink
    for _ in range(200):
        d.agent_end()
        d.agent_start('new permitted agent text', 2000)
        for _ in range(60): d.frame(Z, Z)
    assert len(d.log) <= 128
    assert d.n_resumes == 0

def test_quiet_stop_word_vs_distant_background_word():
    for relative, expected in ((-4.0, 'STOP'), (-12.0, None)):
        w = Deferred()
        d = prepared(w)
        d.caller_ref_db = -20.0
        d.run_levels = [-20.0 + relative]
        d.fe = SimpleNamespace(process=lambda a,c: SimpleNamespace(is_speech=True,snr_db=25.,
            excess_db=-20.+relative,agent_db=-20.,flux=1.))
        w.result = ((d.request_token,d.utt_seq,d.run_seq),'توقف')
        assert d.frame(Z,Z) == expected

def test_current_model_assets_have_the_pinned_complete_manifest():
    spec = verify_assets()
    assert spec['revision'] == 'd90ca5fe260221311c53c58e660288d3deb8d356'
    assert {'model.bin','config.json','tokenizer.json','vocabulary.txt'} <= spec['files'].keys()

@pytest.mark.parametrize('name', ['model.bin','config.json','tokenizer.json','vocabulary.txt'])
def test_corrupt_or_missing_asset_is_rejected(tmp_path, monkeypatch, name):
    names=['model.bin','config.json','tokenizer.json','vocabulary.txt']
    expected={}
    for n in names:
        (tmp_path/n).write_bytes(b'original')
        expected[n]=hashlib.sha256(b'original').hexdigest()
    lock=tmp_path/'lock.json'
    lock.write_text(json.dumps({'files':expected}))
    monkeypatch.setattr(assets,'LOCK',lock)
    (tmp_path/name).write_bytes(b'corrupted')
    with pytest.raises(RuntimeError, match='integrity'):
        verify_assets(tmp_path)
    (tmp_path/name).unlink()
    with pytest.raises(RuntimeError, match='missing'):
        verify_assets(tmp_path)

def test_production_refuses_acoustics_only_instead_of_falling_back(monkeypatch):
    monkeypatch.setenv('TT_CFG','use_words=0')
    model=demo.Model('v3',demo.DEFAULT_MODELS['v3'])
    assert not model.available and 'no production fallback' in model.error

def test_public_health_and_no_legacy_sessions():
    app=demo.DemoApp(None,demo.DEFAULT_MODELS,demo.Settings.for_public(port=1))
    assert set(app.models)=={'v3'} and app.models['v3'].available
    with serving(app) as base:
        assert status(base+'/healthz')==200
        assert status(base+'/favicon.ico')==204
        for mid in ('p3','baseline'):
            with pytest.raises(KeyError): app.new_session('GI-D01',mid)
        app.models['v3'].available=False
        assert status(base+'/healthz')==503
        with pytest.raises(demo.Refused) as e:
            app.new_session('GI-D01','v3')
        assert e.value.status==503

def test_real_async_worker_does_not_block_frames_and_drops_an_old_scenario():
    import threading
    import time
    from tt.words import WordsWorker
    entered, release = threading.Event(), threading.Event()
    class Slow:
        def transcribe(self, audio):
            entered.set()
            assert release.wait(5)
            return 'توقف'
    worker = WordsWorker(Slow())
    old = prepared(worker)
    assert worker.submit((old.request_token, old.utt_seq, old.run_seq), Z.copy(), 0)
    assert entered.wait(2)
    new = prepared(worker)
    # The backend is still blocked: a received frame must be processed without waiting for it.
    assert new.frame(Z, Z) is None and not release.is_set()
    release.set()
    until = time.monotonic() + 2
    while worker.busy and time.monotonic() < until:
        new._poll_words()
        time.sleep(.001)
    assert not worker.busy and new.word_verdict is None and not new.stopped

def test_scenario_reset_replaces_noise_level_and_stop_state():
    model=demo.Model('v3',demo.DEFAULT_MODELS['v3'])
    system=model.new_system()
    old=system.dec
    old.caller_ref_db=-20
    old.stopped=True
    old.agent_on=True
    system.on_scenario_start({'language':'ar'})
    assert system.dec is not old and system.dec.fe is not old.fe
    assert system.dec.request_token is not old.request_token
    assert system.dec.caller_ref_db is None and not system.dec.stopped and not system.dec.agent_on
