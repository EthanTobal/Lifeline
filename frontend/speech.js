/* Browser speech for the Read aloud control and voice mode. */
const LifelineSpeech = (() => {
  "use strict";
  let active = null;
  function cancel() {
    if (active) {
      const utterance = active;
      active = null;
      utterance.onend = utterance.onerror = null;
      utterance.finish?.(false);
    }
    window.speechSynthesis?.cancel();
  }
  function prepare() {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance)
      return Promise.reject(new Error("Browser speech is unavailable on this device."));
    return Promise.resolve();
  }
  async function play(text, options = {}) {
    await prepare();
    cancel();
    return new Promise((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(String(text));
      active = utterance;
      utterance.finish = resolve;
      utterance.lang = document.documentElement.lang || "en-US";
      utterance.onstart = () => options.onStart?.();
      utterance.onend = () => { if (active === utterance) active = null; resolve(true); };
      utterance.onerror = (event) => { if (active === utterance) active = null; reject(new Error(event.error || "Speech playback failed.")); };
      window.speechSynthesis.speak(utterance);
    });
  }
  async function startMeter(onLevel, signal) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context || !navigator.mediaDevices?.getUserMedia) return () => {};
    const context = new Context();
    let stream, frame = 0, stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      signal?.removeEventListener("abort", stop);
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
      context.close().catch(() => {});
      onLevel(0);
    };
    signal?.addEventListener("abort", stop, { once: true });
    if (signal?.aborted) { stop(); return stop; }
    try {
      await context.resume();
      if (stopped) return stop;
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      if (stopped) { stream.getTracks().forEach((track) => track.stop()); return stop; }
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      let level = 0;
      const update = () => {
        if (stopped) return;
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        const target = Math.min(1, Math.max(0, (rms - .008) * 9));
        level += (target - level) * (target > level ? .35 : .1);
        onLevel(level);
        frame = requestAnimationFrame(update);
      };
      update();
      return stop;
    } catch (error) {
      stop();
      throw error;
    }
  }
  return { cancel, prepare, play, startMeter };
})();
