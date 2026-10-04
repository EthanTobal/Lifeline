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
  return { cancel, prepare, play };
})();
