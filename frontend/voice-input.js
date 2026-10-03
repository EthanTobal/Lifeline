/* Microphone fallback: bounded PCM capture, silence detection and WAV for OpenRouter. */
window.LifelineVoiceInput = (() => {
  "use strict";

  function encodeWav(chunks, sampleRate) {
    const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
    const samples = new Float32Array(length);
    let offset = 0;
    for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
    const rate = Math.min(16000, sampleRate);
    const count = Math.floor(length * rate / sampleRate);
    const bytes = new Uint8Array(44 + count * 2);
    const view = new DataView(bytes.buffer);
    const word = (at, value) => { for (let i = 0; i < value.length; i++) bytes[at + i] = value.charCodeAt(i); };
    word(0, "RIFF"); view.setUint32(4, bytes.length - 8, true); word(8, "WAVE");
    word(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    word(36, "data"); view.setUint32(40, count * 2, true);
    for (let i = 0; i < count; i++) {
      const start = Math.floor(i * sampleRate / rate), end = Math.floor((i + 1) * sampleRate / rate);
      let sum = 0;
      for (let j = start; j < end; j++) sum += samples[j];
      const value = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(binary);
  }

  async function open({ onAudio, onLevel, onError }) {
    const Context = window.AudioContext || window.webkitAudioContext;
    // Construct and resume in the button's user gesture before awaiting permissions.
    const context = new Context();
    let stream, source, processor, gain;
    let listening = false, closed = false, chunks = [], preRoll = [], duration = 0, voiced = 0, silence = 0;
    function reset() { chunks = []; preRoll = []; duration = 0; voiced = 0; silence = 0; }
    function close() {
      if (closed) return;
      closed = true; listening = false;
      if (processor) processor.onaudioprocess = null;
      source?.disconnect(); processor?.disconnect(); gain?.disconnect();
      stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
      context.close().catch(() => {});
      reset(); onLevel?.(0);
    }
    try {
      await context.resume();
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      source = context.createMediaStreamSource(stream);
      processor = context.createScriptProcessor(4096, 1, 1);
      gain = context.createGain(); gain.gain.value = 0;
      source.connect(processor); processor.connect(gain); gain.connect(context.destination);
      stream.getAudioTracks().forEach((track) => { track.onended = () => { close(); onError?.("Microphone disconnected. Reconnect it and start voice again."); }; });
      processor.onaudioprocess = (event) => {
        if (closed || !listening) return;
        const samples = event.inputBuffer.getChannelData(0);
        const seconds = samples.length / context.sampleRate;
        const rms = Math.sqrt(samples.reduce((total, value) => total + value * value, 0) / samples.length);
        onLevel?.(Math.min(1, rms * 8));
        const speaking = rms >= 0.014;
        if (!chunks.length && !speaking) {
          preRoll.push(samples.slice());
          // Retain a little context before speech so initial consonants aren't clipped.
          while (preRoll.length * seconds > 0.3) preRoll.shift();
          return;
        }
        if (!chunks.length) { chunks.push(...preRoll); preRoll = []; }
        chunks.push(samples.slice()); duration += seconds;
        if (speaking) { voiced += seconds; silence = 0; } else silence += seconds;
        if (silence >= 0.8 || duration >= 30) {
          const captured = chunks;
          const valid = voiced >= 0.18;
          reset();
          if (valid) {
            listening = false; onLevel?.(0);
            onAudio(encodeWav(captured, context.sampleRate));
          }
        }
      };
      return { setListening(value) { listening = value; reset(); if (!value) onLevel?.(0); }, close };
    } catch (error) { close(); throw error; }
  }

  return { open, encodeWav };
})();
