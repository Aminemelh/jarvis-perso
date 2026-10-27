// Capture du micro dans un thread audio : conversion en PCM 16 bits 16 kHz
// et mesure du volume pour détecter le début et la fin de la parole.

const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 1600; // 100 ms

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pos = 0;
    this.buffer = new Int16Array(CHUNK_SAMPLES);
    this.filled = 0;
    this.energy = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    // Sous-échantillonnage simple (moyenne par fenêtre) vers 16 kHz.
    while (this.pos < input.length) {
      const start = Math.floor(this.pos);
      const end = Math.min(input.length, Math.floor(this.pos + this.ratio));
      let sum = 0;
      for (let i = start; i < end; i++) sum += input[i];
      const sample = end > start ? sum / (end - start) : input[start];
      const clamped = Math.max(-1, Math.min(1, sample));
      this.energy += clamped * clamped;
      this.buffer[this.filled++] = clamped * 0x7fff;
      if (this.filled === CHUNK_SAMPLES) {
        const rms = Math.sqrt(this.energy / CHUNK_SAMPLES);
        this.port.postMessage({ pcm: this.buffer.buffer, rms }, [this.buffer.buffer]);
        this.buffer = new Int16Array(CHUNK_SAMPLES);
        this.filled = 0;
        this.energy = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= input.length;
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
