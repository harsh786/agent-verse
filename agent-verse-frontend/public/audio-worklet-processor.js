/**
 * PcmCaptureProcessor — runs in AudioWorklet thread (dedicated DSP thread).
 *
 * Accumulates 100 ms of PCM16 at 16 kHz and posts the ArrayBuffer to the
 * main thread. Avoids main-thread audio processing completely.
 *
 * Usage:
 *   const ctx = new AudioContext({ sampleRate: 16000 });
 *   await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
 *   const node = new AudioWorkletNode(ctx, 'pcm-capture-processor');
 *   node.port.onmessage = ({ data }) => { // data is ArrayBuffer of Int16 PCM };
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._targetFrames = Math.floor((16000 * 100) / 1000); // 1600 frames / chunk
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;

    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      // Float32 → Int16
      this._buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
    }

    if (this._buf.length >= this._targetFrames) {
      const int16 = new Int16Array(this._buf.splice(0, this._targetFrames));
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
