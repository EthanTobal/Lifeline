/* Shared speech playback for voice replies, previews and Read aloud. */
const LifelineSpeech = (() => {
  "use strict";

  const DEFAULT_MODEL = "microsoft/mai-voice-2.1-flash";
  const DEFAULT_VOICE = "en-US-Harper:MAI-Voice-2.1-Flash";
  let context = null;
  let active = null;

  function getSettings() {
    return {
      output: localStorage.getItem("lifeline-speech-output") === "browser" ? "browser" : "auto",
      model: localStorage.getItem("openrouter-tts-model")?.trim() || DEFAULT_MODEL,
      voice: localStorage.getItem("openrouter-tts-voice")?.trim() || DEFAULT_VOICE,
      key: localStorage.getItem("openrouter-api-key")?.trim() || "",
    };
  }
  function setSettings({ output, model, voice }) {
    localStorage.setItem("lifeline-speech-output", output === "browser" ? "browser" : "auto");
    localStorage.setItem("openrouter-tts-model", model.trim());
    localStorage.setItem("openrouter-tts-voice", voice.trim());
  }
  const settingsFor = (options) => ({ ...getSettings(), ...options });
  const usesAPI = (settings) => settings.output !== "browser" && !!settings.key;

  function cancellable(promise, job) {
    return new Promise((resolve, reject) => {
      const signal = job.controller.signal;
      const abort = () => active === job ? reject(signal.reason) : resolve();
      signal.addEventListener("abort", abort, { once: true });
      Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
      if (signal.aborted) abort();
    });
  }

  // Call during the button click so mobile browsers unlock audio before fetching speech.
  function prepare(options = {}) {
    const settings = settingsFor(options);
    if (!usesAPI(settings)) {
      if (!window.speechSynthesis || !window.SpeechSynthesisUtterance)
        throw new Error("Browser speech is unavailable. Add an OpenRouter API key and select Automatic spoken replies in settings.");
      return Promise.resolve();
    }
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) throw new Error("Audio playback is unavailable in this browser.");
    if (!context || context.state === "closed") context = new AudioContext();
    return context.resume();
  }

  function cancel() {
    if (!active) return;
    const job = active;
    active = null;
    job.controller.abort();
    if (job.source) {
      job.source.onended = null;
      try { job.source.stop(); } catch { /* Already finished. */ }
      job.source.disconnect();
    }
    if (job.utterance) {
      job.utterance.onend = job.utterance.onerror = job.utterance.onstart = null;
      window.speechSynthesis?.cancel();
    }
    job.finish?.();
  }

  // Keep requests within provider input limits, including long Read aloud responses.
  function chunks(text) {
    const result = [];
    while (text.length > 1800) {
      const part = text.slice(0, 1800);
      const boundary = Math.max(part.lastIndexOf(". "), part.lastIndexOf("? "), part.lastIndexOf("! "), part.lastIndexOf("\n"));
      const space = part.lastIndexOf(" ");
      const end = boundary > 900 ? boundary + 1 : space > 0 ? space : 1800;
      result.push(text.slice(0, end));
      text = text.slice(end).trimStart();
    }
    if (text) result.push(text);
    return result;
  }

  async function apiSpeech(text, settings, job, onStart) {
    const timeout = setTimeout(() => job.controller.abort(new Error("Speech generation timed out. Please try again.")), 35000);
    let bytes;
    try {
      const response = await cancellable(fetch("https://openrouter.ai/api/v1/audio/speech", {
        method: "POST", signal: job.controller.signal,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.key}`, "X-OpenRouter-Title": "Lifeline" },
        body: JSON.stringify({ model: settings.model, input: text, voice: settings.voice, response_format: "mp3" }),
      }), job);
      if (active !== job) return;
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(error?.error?.message || `Speech generation failed (${response.status}). Check your speech model and voice in settings.`);
      }
      if (!response.headers.get("content-type")?.toLowerCase().startsWith("audio/mpeg")) {
        await response.body?.cancel();
        throw new Error("The speech service did not return MP3 audio. Check your speech model in settings.");
      }
      bytes = await cancellable(response.arrayBuffer(), job);
    } finally { clearTimeout(timeout); }
    if (active !== job) return;
    if (!bytes.byteLength) throw new Error("The speech service returned empty audio. Please try again.");
    const buffer = await cancellable(context.decodeAudioData(bytes), job);
    if (active !== job) return;
    if (context.state === "suspended") await cancellable(context.resume(), job);
    if (active !== job) return;
    if (context.state !== "running") throw new Error("Audio is paused by your browser. Tap Test voice in settings to enable playback.");
    await new Promise((resolve, reject) => {
      const source = context.createBufferSource();
      job.source = source;
      source.buffer = buffer;
      source.connect(context.destination);
      job.finish = resolve;
      source.onended = () => { source.disconnect(); job.source = null; job.finish = null; resolve(); };
      try { source.start(); onStart?.(); }
      catch (error) { source.disconnect(); job.source = null; job.finish = null; reject(error); }
    });
  }

  function browserSpeech(text, job, onStart) {
    const synth = window.speechSynthesis;
    if (synth.getVoices && !synth.getVoices().length)
      throw new Error("No browser voices are installed. Add an OpenRouter API key and use Automatic spoken replies in settings.");
    return new Promise((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(text);
      job.utterance = utterance;
      utterance.lang = document.documentElement.lang || "en-US";
      const finish = (error) => {
        if (active !== job) return;
        clearTimeout(timer);
        job.finish = null;
        utterance.onend = utterance.onerror = utterance.onstart = null;
        job.utterance = null;
        if (error) synth.cancel();
        error ? reject(new Error(`Browser speech failed (${error}). Use Automatic spoken replies with your OpenRouter key.`)) : resolve();
      };
      const timer = setTimeout(() => finish("playback timed out"), Math.max(15000, text.length * 150));
      job.finish = () => { clearTimeout(timer); resolve(); };
      utterance.onend = () => finish();
      utterance.onerror = (event) => finish(event.error || "synthesis-failed");
      try { synth.resume?.(); synth.speak(utterance); onStart?.(); }
      catch (error) { finish(error.message); }
    });
  }

  async function play(text, options = {}) {
    cancel();
    if (!text?.trim()) return true;
    const settings = settingsFor(options);
    const job = { controller: new AbortController(), source: null, utterance: null, finish: null };
    active = job;
    try {
      await cancellable(prepare(settings), job);
      if (active !== job) return false;
      options.onStatus?.(usesAPI(settings) ? "Generating speech…" : "Reading aloud…");
      for (const part of chunks(text.trim())) {
        if (active !== job) return false;
        const onStart = () => { options.onStatus?.("Speaking"); options.onStart?.(); };
        if (usesAPI(settings)) await apiSpeech(part, settings, job, onStart);
        else await browserSpeech(part, job, onStart);
      }
      return active === job;
    } catch (error) {
      if (active !== job) return false;
      throw error;
    } finally {
      if (active === job) cancel();
    }
  }

  return { getSettings, setSettings, prepare, play, cancel };
})();
