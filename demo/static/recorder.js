// AudioWorklet for "Test your own audio": captures the microphone, sample-aligned with the agent playback.
// It records nothing before `startAt` (the AudioContext time at which the agent starts) and returns mono Float32
// chunks to the page. Nothing is sent anywhere from here.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.startAt = Infinity;
    this.done = false;
    this.parts = [];
    this.count = 0;
    this.port.onmessage = (e) => {
      if (e.data.startAt !== undefined) this.startAt = e.data.startAt;
      if (e.data.stop) { this.flush(); this.port.postMessage({ done: true }); this.done = true; }
    };
  }

  flush() {
    if (!this.count) return;
    const out = new Float32Array(this.count);
    let o = 0;
    for (const p of this.parts) { out.set(p, o); o += p.length; }
    this.parts = [];
    this.count = 0;
    this.port.postMessage({ chunk: out }, [out.buffer]);
  }

  process(inputs) {
    if (this.done) return false;
    const input = inputs[0];
    if (!input || !input.length) return true;
    const n = input[0].length, end = currentTime + n / sampleRate;
    if (end <= this.startAt) return true;
    const skip = Math.max(0, Math.round((this.startAt - currentTime) * sampleRate));
    const mono = new Float32Array(n - skip);
    for (const ch of input) for (let i = skip; i < n; i++) mono[i - skip] += ch[i] / input.length;
    this.parts.push(mono);
    this.count += mono.length;
    if (this.count >= 8192) this.flush();
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
