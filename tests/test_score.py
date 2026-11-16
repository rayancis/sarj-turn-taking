"""Scorer behaviour on hand-made decision logs (no network, no audio)."""
import pytest

from tests.fixtures import tiny_split
from ttbench.dataset import GAP_MS
from ttbench.score import score, stop_credit, DEFAULT_THRESHOLDS as TH

LABELS = {l["scenario_id"]: l for l, _ in tiny_split()}
ORDER = ["GI-D90", "BC-D90", "HT-D90", "AM-D90"]


def layout():
    t, out = 0, []
    for sid in ORDER:
        d = LABELS[sid]["audio"]["duration_ms"]
        out.append({"scenario_id": sid, "start_ms": t, "duration_ms": d})
        t += d + GAP_MS
    return out


LAYOUT = layout()
START = {s["scenario_id"]: s["start_ms"] for s in LAYOUT}


def run(decisions):
    """decisions: list of (scenario_id, local_ms, decision)."""
    log = [{"dir": "rx", "stream_ms": START[sid] + t, "msg": {"type": "decision", "decision": d}} for sid, t, d in decisions]
    return score(LABELS, {"scenarios": LAYOUT, "official": True, "speed": 1.0}, log)


def ev(r, eid):
    return next(e for e in r["events"] if e["event_id"] == eid)


def test_never_stop_scores_near_zero():
    r = run([])
    assert r["core"]["R"] == 0 and r["core"]["F"] == 0
    assert r["core"]["core_score"] <= 10
    assert r["eot"]["attempted"] is False and r["eot"]["eot_score"] == 0


def test_always_stop_scores_near_zero():
    r = run([(sid, 10, "STOP") for sid in ORDER])
    assert r["core"]["R"] == 0.0                          # a stop seconds before the caller spoke is not a yield
    assert r["core"]["S"] == 0.0
    assert r["core"]["F"] == 0.0
    assert ev(r, "BC-D90.e1")["credit"] == 0.0          # already silent during the backchannel


def test_oracle_is_perfect():
    r = run([("GI-D90", 2400, "STOP"), ("HT-D90", 5300, "TAKE_TURN")])
    assert r["core"]["R"] == 1 and r["core"]["S"] == 1 and r["core"]["core_score"] == pytest.approx(100)
    assert r["eot"]["eot_score"] == pytest.approx(100)


def test_latency_credit_curve():
    assert stop_credit(0, TH) == 1 and stop_credit(300, TH) == 1
    assert stop_credit(900, TH) == pytest.approx(0.7)
    assert stop_credit(1500, TH) == pytest.approx(0.4)
    r = run([("GI-D90", 2300 + 900, "STOP")])
    assert ev(r, "GI-D90.e1")["credit"] == pytest.approx(0.7)


def test_stop_between_onset_and_evident_is_full_credit():
    r = run([("GI-D90", 2050, "STOP")])
    assert ev(r, "GI-D90.e1")["credit"] == 1.0 and ev(r, "GI-D90.e1")["latency_ms"] == 0


def test_too_late_is_a_miss():
    r = run([("GI-D90", 2300 + 1600, "STOP")])
    assert ev(r, "GI-D90.e1")["outcome"] == "miss"


def test_resume_while_caller_still_talking_is_a_miss():
    r = run([("GI-D90", 2400, "STOP"), ("GI-D90", 3000, "RESUME")])
    assert ev(r, "GI-D90.e1")["outcome"] == "miss"


def test_hiccup_on_backchannel_is_half_credit_and_full_stop_is_zero():
    assert ev(run([("BC-D90", 2100, "STOP"), ("BC-D90", 2400, "RESUME")]), "BC-D90.e1")["credit"] == 0.5
    assert ev(run([("BC-D90", 2100, "STOP")]), "BC-D90.e1")["credit"] == 0.0


def test_spurious_stop_in_clean_agent_speech_hurts_restraint():
    r = run([("GI-D90", 500, "STOP"), ("GI-D90", 600, "RESUME"), ("GI-D90", 2400, "STOP")])
    gi = next(s for s in r["scenarios"] if s["scenario_id"] == "GI-D90")
    assert gi["spurious_weight"] == 0.5 and gi["credit"] == 0.5
    assert ev(r, "GI-D90.e1")["credit"] == 1.0
    assert r["categories"]["GI"]["S"] == 0.5


def test_either_preferred_and_other():
    assert ev(run([]), "AM-D90.e1")["credit"] == 1.0                       # kept = preferred
    assert ev(run([("AM-D90", 2100, "STOP")]), "AM-D90.e1")["credit"] == pytest.approx(0.6)


def test_end_of_turn_premature_and_late():
    r = run([("HT-D90", 3600, "TAKE_TURN"), ("HT-D90", 5000 + 1300, "TAKE_TURN")])
    e = ev(r, "HT-D90.e1")
    assert [i["passed"] for i in e["patience_items"]] == [False, True]
    assert e["credit"] == pytest.approx(1 - 0.6 * 700 / 1400)
    assert r["eot"]["P"] == 0.5


def test_decisions_outside_agent_speech_are_ignored():
    r = run([("HT-D90", 6000, "STOP")])                                   # agent silent at 6 s
    assert r["rates"]["ignored_decisions"] == 1 and r["core"]["S"] == 1


def test_slices_present():
    r = run([("GI-D90", 2400, "STOP")])
    assert r["slices"]["other_dialects"]["n_scenarios"] == 1
    assert r["slices"]["dialect_gap"] is not None


def test_restraint_is_keep_share_times_clean_share():
    r = run([("BC-D90", 2100, "STOP")])                       # fails the only BC backchannel
    assert r["categories"]["BC"]["S"] == 0.0


def test_silence_does_not_leak_into_the_next_utterance():
    two = {**LABELS["GI-D90"], "agent_utterances": [
        {"utt_id": "A0", "start_ms": 0, "end_ms": 1000, "text": "x"},
        {"utt_id": "A1", "start_ms": 1500, "end_ms": 6500, "text": "y"}]}
    from ttbench.score import score_scenario, DEFAULT_THRESHOLDS
    res = score_scenario(two, [(500.0, "STOP")], DEFAULT_THRESHOLDS)
    assert res["events"][0]["outcome"] == "miss"             # a stop in A0 is not a yield in A1


def test_early_stray_stop_is_not_a_free_yield():
    r = run([("GI-D90", 500, "STOP")])                    # onset is 2000: stopped 1.5 s too early, never resumed
    assert ev(r, "GI-D90.e1")["outcome"] == "miss"
    r = run([("GI-D90", 1800, "STOP")])                   # 200 ms before onset: anticipation allowed
    assert ev(r, "GI-D90.e1")["outcome"] == "yield"
