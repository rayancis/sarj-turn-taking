"""One way to build your system: a Python class that sees every 20 ms of the call. Replace everything inside it.

    python -m ttbench.play                 # your system against 8 example calls, then your score

The challenge, while the AGENT is talking:  keep talking (return nothing)  or  "STOP" (the caller wants the floor).
Optional, own leaderboard, while the CALLER is talking:  keep listening (nothing)  or  "TAKE_TURN" (they have finished).

What you get: both sides' audio (16 kHz, 16-bit, 320 samples per frame), when the agent starts and stops, and the full
text of what the agent is about to say. What you use, and how, is up to you: acoustics, speech recognition and
reasoning over the words, an audio model, a mix, something new. Slow components: see "Using slow components" in
README.md (never block on_frame). Prefer your own server or another language? See INTERFACE.md and `--url`.

As delivered, this is the loudness baseline: the simplest thing that runs, kept as a reference to beat.
"""
import numpy as np

from ttbench.server import System, run

TEAM = "my-team"            # your team name, shown on the scoreboard

FRAME_MS = 20               # each on_frame call covers 20 ms of audio
LOUD_DB = -42               # caller louder than this counts as "sound"
STOP_AFTER_MS = 200         # sound for this long while the agent talks  -> STOP
TAKE_TURN_AFTER_MS = 700    # quiet for this long after the caller spoke -> TAKE_TURN


def loudness_db(samples: np.ndarray) -> float:
    """Loudness of 20 ms of 16-bit audio, in dB below full scale (silence is about -90, speech -40 to -15)."""
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768
    return 20 * np.log10(rms + 1e-12)


class MySystem(System):
    def __init__(self):
        self.agent_talking = False
        self.agent_text = ""            # what the agent is saying right now (you get the full sentence up front)
        self.stopped = False
        self.sound_ms = 0               # how long the caller has been making sound
        self.quiet_ms = 0               # how long the caller has been quiet
        self.caller_spoke = False

    # --- what is happening in the call -------------------------------------------------------------------
    def on_agent_start(self, text, duration_ms):
        self.agent_talking, self.agent_text, self.stopped, self.sound_ms = True, text, False, 0

    def on_agent_end(self):
        self.agent_talking, self.caller_spoke, self.quiet_ms = False, False, 0

    # --- every 20 ms: decide ---------------------------------------------------------------------------
    def on_frame(self, agent, caller):
        loud = loudness_db(caller) > LOUD_DB

        if self.agent_talking:
            # Should the agent stop talking? The baseline stops at ANY sound: a cough, a car horn, "mm-hm",
            # the agent's own echo. That is its weakness. A good system stops only when the caller really
            # wants to speak, and does it fast.
            self.sound_ms = self.sound_ms + FRAME_MS if loud else 0
            if self.sound_ms >= STOP_AFTER_MS and not self.stopped:
                self.stopped = True
                return "STOP"
            return None

        # The agent is silent: is the caller done? The baseline waits for a fixed stretch of quiet, so it jumps in
        # when someone pauses to think, and it is slow when they have clearly finished.
        if loud:
            self.caller_spoke, self.quiet_ms = True, 0
        elif self.caller_spoke:
            self.quiet_ms += FRAME_MS
            if self.quiet_ms >= TAKE_TURN_AFTER_MS:
                self.caller_spoke = False
                return "TAKE_TURN"
        return None



if __name__ == "__main__":
    run(MySystem, TEAM, __file__)
