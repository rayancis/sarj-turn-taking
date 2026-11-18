// AudioWorklet for the live agent. In one audio clock it:
//  - plays the agent audio received from Gemini (24 kHz PCM16, resampled to the device rate),
//  - captures the microphone,
//  - down-samples BOTH to 16 kHz and packs 20 ms kit frames: 1 flag byte + 320 x [agent, caller] int16 LE.
// The agent channel of each frame is exactly what was played in that frame, so SARJ hears what the caller heard.
// Flags: bit 0 = agent audio played in this frame, bit 1 = playback queue empty at the end of the frame.

class Decimator {
  // 2nd-order low-pass at 7 kHz, then fractional decimation to 16 kHz with linear interpolation.
  constructor(rate) {
    const f = 7000 / rate, w = 2 * Math.PI * Math.min(f, 0.45), q = Math.SQRT1_2;
    const a = Math.sin(w) / (2 * q), c = Math.cos(w), a0 = 1 + a;
    this.b0 = (1 - c) / 2 / a0; this.b1 = (1 - c) / a0; this.b2 = this.b0;
    this.a1 = -2 * c / a0; this.a2 = (1 - a) / a0;
    this.x1 = this.x2 = this.y1 = this.y2 = 0;
    this.step = rate / 16000; this.pos = 0; this.prev = 0;
  }
  push(x, out) {
    const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2;
    this.x2 = this.x1; this.x1 = x; this.y2 = this.y1; this.y1 = y;
    // emit every `step` input samples (pos counts input samples since the last emitted point)
    while (this.pos <= 1) {
      out.push(this.prev + (y - this.prev) * this.pos);
      this.pos += this.step;
    }
    this.pos -= 1;
    this.prev = y;
  }
}

class SarjLive extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];            // Float32Array chunks at 24 kHz
    this.qi = 0;                // index in queue[0]
    this.frac = 0;              // fractional read position between 24 kHz samples
    this.step = 24000 / sampleRate;
    this.gen = 0;
    this.decA = new Decimator(sampleRate);
    this.decC = new Decimator(sampleRate);
    this.bufA = []; this.bufC = [];
    this.played = false;
    this.stopped = false;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.flush !== undefined) { this.queue = []; this.qi = 0; this.frac = 0; this.gen = m.flush; }
      if (m.audio && m.gen >= this.gen) {
        if (m.gen > this.gen) { this.queue = []; this.qi = 0; this.gen = m.gen; }
        const f = new Float32Array(m.audio.length);
        for (let i = 0; i < f.length; i++) f[i] = m.audio[i] / 32768;
        this.queue.push(f);
      }
      if (m.stop) this.stopped = true;
    };
  }

  queued() {
    let n = 0;
    for (const q of this.queue) n += q.length;
    return n - this.qi;
  }

  nextAgent() {
    // linear interpolation through the 24 kHz queue
    while (this.queue.length && this.qi >= this.queue[0].length) { this.queue.shift(); this.qi = 0; }
    if (!this.queue.length) return null;
    const cur = this.queue[0][this.qi];
    let nxt = cur;
    if (this.qi + 1 < this.queue[0].length) nxt = this.queue[0][this.qi + 1];
    else if (this.queue.length > 1) nxt = this.queue[1][0];
    const v = cur + (nxt - cur) * this.frac;
    this.frac += this.step;
    while (this.frac >= 1) { this.frac -= 1; this.qi += 1; }
    return v;
  }

  process(inputs, outputs) {
    if (this.stopped) return false;
    const mic = inputs[0] && inputs[0][0];
    const out = outputs[0][0];
    const n = out.length;
    const ta = [], tc = [];
    for (let i = 0; i < n; i++) {
      const a = this.nextAgent();
      if (a !== null) this.played = true;
      const av = a === null ? 0 : a;
      out[i] = av;
      this.decA.push(av, ta);
      this.decC.push(mic ? mic[i] : 0, tc);
    }
    for (let ch = 1; ch < outputs[0].length; ch++) outputs[0][ch].set(out);
    const m = Math.min(ta.length, tc.length);
    for (let i = 0; i < m; i++) {
      this.bufA.push(ta[i]); this.bufC.push(tc[i]);
      if (this.bufA.length === 320) this.emit();
    }
    return true;
  }

  emit() {
    const buf = new ArrayBuffer(1 + 1280), view = new DataView(buf);
    const empty = this.queued() <= 0;
    view.setUint8(0, (this.played ? 1 : 0) | (empty ? 2 : 0));
    let ea = 0, ec = 0;
    for (let i = 0; i < 320; i++) {
      const a = Math.max(-1, Math.min(1, this.bufA[i])), c = Math.max(-1, Math.min(1, this.bufC[i]));
      ea += a * a; ec += c * c;
      view.setInt16(1 + 4 * i, Math.round(a * 32767), true);
      view.setInt16(3 + 4 * i, Math.round(c * 32767), true);
    }
    this.port.postMessage({ frame: buf, played: this.played, agentDb: 10 * Math.log10(ea / 320 + 1e-12),
      micDb: 10 * Math.log10(ec / 320 + 1e-12) }, [buf]);
    this.bufA = []; this.bufC = []; this.played = false;
  }
}

registerProcessor("sarj-live", SarjLive);
