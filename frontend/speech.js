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

/* ---------------------------------------------------------------------------
   Gemini Live — the real-time voice layer.

   Audio flows: user mic -> Gemini Live -> Lifeline backend -> LifeLine speaks.

   Gemini never decides anything financial. When it needs to know anything
   about the assessment, insurance education, or a calculation, it calls the
   `ask_lifeline` tool, which goes through the EXISTING /api/turn flow. The
   same orchestrator, the same deterministic calculator, and the same Bedrock
   Knowledge Base therefore serve both voice and text — and because both use
   the shared backend client, they share one session.
   --------------------------------------------------------------------------- */
const LifelineVoice = (() => {
  "use strict";

  // Gemini is the voice. It is explicitly forbidden from doing LifeLine's job.
  const SYSTEM_INSTRUCTION = `You are the voice interface for LifeLine.

Speak naturally, clearly, and concisely.

Use the LifeLine backend for assessment information, insurance education, and calculations.
Call the ask_lifeline tool for anything about the user's own situation rather than answering from memory.

Never independently calculate life-insurance coverage amounts.
Never invent insurance product information.
Do not provide financial advice or tell users what policy they should buy.
Do not say that a user has enough insurance or needs more insurance.

Describe calculated results as illustrative estimates based on the information and assumptions provided.
When discussing broad life-insurance categories, distinguish between term insurance and permanent insurance.
When the LifeLine backend provides an amount or calculation, preserve it accurately.

Keep spoken responses conversational and concise.`;

  const ASK_TOOL = {
    name: "ask_lifeline",
    description: "Ask the LifeLine backend about the user's own assessment, " +
      "insurance education, or a calculated needs figure. Use this for " +
      "anything user-specific instead of answering from your own knowledge.",
    parameters: {
      type: "object",
      properties: {
        message: { type: "string", description: "What the user asked, in natural language." },
      },
      required: ["message"],
    },
  };

  // Gemini Live wants 16-bit little-endian mono PCM at 16 kHz for microphone input.
  const INPUT_RATE = 16000;
  const OUTPUT_RATE = 24000;
  const SDK_URL = "https://cdn.jsdelivr.net/npm/@google/genai@latest/dist/index.umd.js";
  // Mic RMS above this counts as the user starting to speak (barge-in).
  const SPEECH_ONSET_RMS = 0.02;

  let session = null;    // { socket, stream, audioCtx, ... }
  let sdkPromise = null;

  function loadSdk() {
    if (window.GoogleGenAI) return Promise.resolve(window.GoogleGenAI);
    if (sdkPromise) return sdkPromise;
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = SDK_URL;
      script.async = true;
      script.onload = () => (window.GoogleGenAI
        ? resolve(window.GoogleGenAI)
        : reject(new Error("Gemini SDK failed to load.")));
      script.onerror = () => reject(new Error("Could not load the Gemini SDK. Check your connection."));
      document.head.appendChild(script);
    });
    return sdkPromise;
  }

  function floatToPcm16(float32) {
    const buffer = new ArrayBuffer(float32.length * 2);
    const view = new DataView(buffer);
    for (let i = 0; i < float32.length; i += 1) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  }

  /* The Live API expects audio as a base64 string, not raw bytes. */
  function pcm16ToBase64(float32) {
    const bytes = new Uint8Array(floatToPcm16(float32));
    let binary = "";
    const CHUNK = 0x8000;  // avoid blowing the argument limit on big buffers
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary);
  }

  function isActive() {
    return Boolean(session);
  }

  /* Is the assistant's audio currently playing locally? */
  function isSpeaking() {
    return Boolean(session && session.sources && session.sources.size > 0);
  }

  /* Mute/unmute the MICROPHONE only. The Live session stays open, so Gemini
     keeps the conversation and can still be unmuted into. */
  function setMuted(muted) {
    if (!session) return false;
    session.muted = Boolean(muted);
    return session.muted;
  }

  function isMuted() {
    return Boolean(session && session.muted);
  }

  /* Stop and empty any assistant audio queued locally. This is the playback
     half of barge-in; the server tells us to do it via serverContent.interrupted,
     and we also do it locally the moment the user starts talking so the
     interruption feels immediate rather than round-tripped. */
  function interruptPlayback() {
    if (!session || !session.sources) return;
    for (const source of session.sources) {
      try { source.onended = null; source.stop(); } catch (_) { /* already ended */ }
    }
    session.sources.clear();
  }

  /* Start a voice session. Fails cleanly if no key is configured server-side. */
  async function start({ onState, onEvent } = {}) {
    if (session) return session;
    if (!window.LifelineBackend) throw new Error("The Lifeline backend client is not loaded.");
    onState?.("connecting");

    // The permanent API key never reaches the browser: ask the backend for a
    // short-lived, single-use Live token instead.
    const credentials = await LifelineBackend.requestGeminiToken();
    const GoogleGenAI = await loadSdk();
    const ai = new GoogleGenAI({ apiKey: credentials.token });

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    /* `callbacks` is a sibling of `config` on LiveConnectParameters — the
       handlers are NOT assignable properties on the returned session.
       `socket` is declared first because the handlers below close over it. */
    let socket = null;

    /* The ONLY channel between Gemini and LifeLine's logic. Everything the
       assistant knows about this user arrives through this tool call, which
       runs the normal /api/turn orchestrator (extractor -> calculator / Bedrock). */
    const askLifeline = async (call) => {
      // The JS SDK wants functionResponses as an ARRAY of {id, name, response}
      // objects (not a map keyed by id, which is what the Python SDK uses).
      const responses = [];
      for (const fn of call.functionCalls || []) {
        if (fn.name !== "ask_lifeline") continue;
        try {
          const args = fn.args || {};
          // Shared client => same session_id as the text chat.
          const data = await LifelineBackend.getSharedClient().turn({
            message: String(args.message || ""),
          });
          responses.push({
            id: fn.id,
            name: fn.name,
            // The LifeLine reply verbatim, so amounts and wording survive intact.
            response: {
              reply: data.assistant_message,
              status: data.assessment.status,
              missing_fields: data.assessment.missing_fields,
              next_question: data.assessment.next_field_question,
              illustrative_gap: data.needs_assessment.illustrative_gap,
              gross_need: data.needs_assessment.breakdown.gross_need ?? null,
              disclaimer: data.disclaimer,
            },
          });
        } catch (error) {
          responses.push({
            id: fn.id,
            name: fn.name,
            response: { reply: "I could not reach the Lifeline service just now." },
          });
        }
      }
      if (responses.length) socket.sendToolResponse({ functionResponses: responses });
    };

    // Gemini's audio, transcriptions, and tool calls all arrive on onmessage.
    // There is no separate onToolCall handler in this SDK, so ask_lifeline is
    // dispatched from here.
    const handleMessage = (event) => {
      // The typings and the SDK example disagree about whether the handler gets
      // the LiveServerMessage directly or a MessageEvent wrapping it.
      const msg = (event && event.data !== undefined && event.serverContent === undefined
        && event.toolCall === undefined) ? event.data : event;
      if (!msg) return;

      // Tool call -> LifeLine /api/turn -> response back to Gemini.
      if (msg.toolCall?.functionCalls?.length) askLifeline(msg.toolCall);

      // Audio output, played through a separate context so streaming never stutters.
      const serverContent = msg.serverContent;
      if (serverContent?.modelTurn?.parts) {
        for (const part of serverContent.modelTurn.parts) {
          if (part.inlineData?.data) playChunk(part.inlineData.data);
        }
      }
      const inputText = msg.inputTranscription?.text;
      if (inputText) onEvent?.({ type: "user-speech", text: inputText });
      const outputText = msg.outputTranscription?.text;
      if (outputText) onEvent?.({ type: "assistant-speech", text: outputText });
      // The Live API's supported interruption signal: the server says the turn was
      // cut off, so drop whatever assistant audio we still have queued.
      if (serverContent?.interrupted) {
        interruptPlayback();
        onEvent?.({ type: "interrupted" });
      }
    };

    socket = await ai.live.connect({
      model: credentials.model,
      config: {
        systemInstruction: SYSTEM_INSTRUCTION,
        tools: [{ functionDeclarations: [ASK_TOOL] }],
        responseModalities: ["AUDIO"],
        inputAudioTranscription: {},
        outputAudioTranscription: {},
        speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: "Puck" } } },
      },
      callbacks: {
        onopen: () => onState?.("connecting"),
        onmessage: (event) => handleMessage(event),
        onerror: (event) => onEvent?.({
          type: "error",
          message: event?.error?.message || "The voice connection reported an error.",
        }),
        onclose: () => onState?.("idle"),
      },
    });

    // Microphone -> Gemini Live.
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const audioCtx = new AudioCtx();
    const source = audioCtx.createMediaStreamSource(stream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => {
      if (!session || !socket) return;
      const samples = event.inputBuffer.getChannelData(0);

      // Local barge-in: if the assistant is talking and the user starts
      // speaking, silence the assistant now rather than waiting for the
      // server round-trip. RMS is rate-independent, so this works whatever
      // rate the AudioContext chose.
      if (session.sources.size > 0) {
        let sum = 0;
        for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
        if (Math.sqrt(sum / samples.length) > SPEECH_ONSET_RMS) {
          session.voiceFrames = (session.voiceFrames || 0) + 1;
          // Require a couple of consecutive frames so a single click or
          // keyboard noise does not cut the assistant off.
          if (session.voiceFrames >= 2) {
            interruptPlayback();
            session.voiceFrames = 0;
          }
        } else {
          session.voiceFrames = 0;
        }
      }

      // Muted: keep the session alive, just stop sending microphone audio.
      if (session.muted) return;

      socket.sendRealtimeInput({
        audio: {
          // The Live API takes audio as base64, not raw bytes.
          data: pcm16ToBase64(samples),
          mimeType: `audio/pcm;rate=${INPUT_RATE}`,
        },
      });
    };
    source.connect(processor);
    // ScriptProcessor only pumps while connected, but the mic must never be
    // echoed to the speakers, so route it through a muted gain node.
    const silent = audioCtx.createGain();
    silent.gain.value = 0;
    processor.connect(silent);
    silent.connect(audioCtx.destination);

    session = { socket, stream, audioCtx, source, processor, silent, output: null, sources: new Set(), muted: false, voiceFrames: 0 };
    onState?.("listening");
    return session;
  }

  /* Play one chunk of audio Gemini sent back. */
  function playChunk(data) {
    if (!session || !data) return;
    if (!session.output) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      session.output = new Ctx({ sampleRate: OUTPUT_RATE });
    }
    const raw = atob(data);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    // Gemini emits 16-bit signed PCM.
    const samples = new Int16Array(bytes.buffer);
    const floats = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) floats[i] = samples[i] / 32768;
    const buffer = session.output.createBuffer(1, floats.length, OUTPUT_RATE);
    buffer.copyToChannel(floats, 0);
    const source = session.output.createBufferSource();
    source.buffer = buffer;
    source.connect(session.output.destination);
    // Track it so barge-in and End can silence audio that is already playing.
    session.sources.add(source);
    source.onended = () => session?.sources.delete(source);
    source.start();
  }

  async function stop({ onState } = {}) {
    if (!session) return;
    const current = session;
    session = null;
    // Silence any assistant audio before releasing the contexts, otherwise the
    // tail of the reply keeps playing over the closed session.
    interruptPlayback();
    const quiet = (fn) => { try { fn(); } catch (_) { /* already torn down */ } };
    quiet(() => current.processor.onaudioprocess = null);
    quiet(() => current.socket.close?.());
    quiet(() => current.processor.disconnect());
    quiet(() => current.source.disconnect());
    quiet(() => current.silent.disconnect());
    quiet(() => current.output?.close?.());
    try { await current.audioCtx.close(); } catch (_) { /* noop */ }
    // Release the microphone so the browser's recording indicator goes out.
    current.stream.getTracks().forEach((track) => track.stop());
    current.sources.clear();
    onState?.("idle");
  }

  return {
    start, stop, isActive, playChunk, setMuted, isMuted,
    interruptPlayback, isSpeaking,
    SYSTEM_INSTRUCTION, ASK_TOOL, INPUT_RATE,
  };
})();
