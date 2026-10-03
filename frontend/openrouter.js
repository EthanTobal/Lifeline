/* OpenRouter chat completions. Demo credentials and memories stay in this browser. */
const OpenRouter = (() => {
  "use strict";

  const API_URL = "https://openrouter.ai/api/v1/chat/completions";
  const getKey = () => localStorage.getItem("openrouter-api-key")?.trim() || "";
  const setKey = (value) => localStorage.setItem("openrouter-api-key", value.trim());
  const getModel = () => localStorage.getItem("openrouter-model")?.trim() || "";
  const setModel = (value) => localStorage.setItem("openrouter-model", value.trim());
  const getVoiceModel = () => localStorage.getItem("openrouter-voice-model")?.trim() || getModel();
  const setVoiceModel = (value) => localStorage.setItem("openrouter-voice-model", value.trim());
  const getVoiceInput = () => localStorage.getItem("lifeline-voice-input") === "audio" ? "audio" : "auto";
  const setVoiceInput = (value) => localStorage.setItem("lifeline-voice-input", value === "audio" ? "audio" : "auto");
  const getTranscriptionModel = () => localStorage.getItem("openrouter-transcription-model")?.trim() || "google/gemini-3.8-flash";
  const setTranscriptionModel = (value) => localStorage.setItem("openrouter-transcription-model", value.trim());
  let browserRecognitionFailed = false;

  async function listModels() {
    const response = await fetch("https://openrouter.ai/api/v1/models");
    if (!response.ok) throw new Error("Could not load OpenRouter models. You can still enter a model ID.");
    const payload = await response.json();
    if (!Array.isArray(payload.data)) throw new Error("OpenRouter returned an invalid model list.");
    return payload.data.filter((model) => model.supported_parameters?.includes("tools") &&
      model.architecture?.output_modalities?.includes("text"));
  }

  const MEM_KEY = "lifeline-memories";
  const getMemories = () => {
    try { return JSON.parse(localStorage.getItem(MEM_KEY) || "[]"); } catch { return []; }
  };
  const setMemories = (list) => {
    localStorage.setItem(MEM_KEY, JSON.stringify(list));
    document.dispatchEvent(new CustomEvent("memories-changed"));
  };
  const runMemoryTool = (name, args) => {
    const list = getMemories();
    switch (name) {
      case "save_memory": {
        const text = String(args.text || "").trim();
        if (!text) return { ok: false, error: "Empty memory." };
        if (list.some((m) => m.text.toLowerCase() === text.toLowerCase()))
          return { ok: true, note: "Already remembered." };
        list.push({ id: "m" + Date.now().toString(36), text, at: new Date().toISOString() });
        setMemories(list);
        return { ok: true, saved: text };
      }
      case "list_memories":
        return list.length ? { ok: true, memories: list.map((m) => m.text) } : { ok: true, memories: [] };
      case "forget_memory": {
        const q = String(args.text || "").toLowerCase();
        const kept = list.filter((m) => !m.text.toLowerCase().includes(q));
        const removed = list.length - kept.length;
        setMemories(kept);
        return { ok: true, removed };
      }
      default:
        return { ok: false, error: "Unknown tool." };
    }
  };

  const MEMORY_DECLARATIONS = [
    {
      name: "save_memory",
      description:
        "Save a short, important fact about the user (their name, family, health, policy details, preferences) so it can be remembered in future chats. Use when the user explicitly shares something personal and important. Do not save sensitive financial account numbers or anything the user said not to keep.",
      parameters: {
        type: "object",
        properties: { text: { type: "string", description: "One short sentence, e.g. 'User's name is Margaret and she lives in Ohio.'" } },
        required: ["text"],
      },
    },
    {
      name: "list_memories",
      description: "List the facts currently remembered about the user.",
      parameters: { type: "object", properties: {} },
    },
    {
      name: "forget_memory",
      description: "Delete remembered facts matching the given text.",
      parameters: {
        type: "object",
        properties: { text: { type: "string", description: "Part of the memory to delete." } },
        required: ["text"],
      },
    },
  ];

  const CONTENT_DECLARATIONS = [
    {
      name: "ask_follow_up",
      description: "Ask one clarifying question and wait for the user to answer. Optional answer choices appear as buttons; the user can always give their own answer. Use when an important detail is missing rather than guessing. This ends the current turn.",
      parameters: {
        type: "object",
        properties: {
          question: { type: "string", description: "One plain-text question, up to 500 characters." },
          options: { type: "array", items: { type: "string" }, maxItems: 5, description: "Optional short answers, up to 160 characters each." },
        },
        required: ["question"],
      },
    },
    {
      name: "create_artifact",
      description: "Create a document card in the conversation with a View document control and Print button. Use for a summary of the user's information, a checklist, a policy comparison, or a plan worth keeping.",
      parameters: {
        type: "object",
        properties: {
          title: { type: "string", description: "Short document title, up to 200 characters." },
          markdown: { type: "string", description: "Complete document in Markdown, up to 60,000 characters. Use only known facts; clearly mark unknown details." },
        },
        required: ["title", "markdown"],
      },
    },
    {
      name: "create_embed",
      description: "Show a resource link card or an embedded YouTube/Vimeo video. Use only a URL supplied by the user or available in trusted context; never invent resource URLs.",
      parameters: {
        type: "object",
        properties: {
          title: { type: "string", description: "Short resource title, up to 200 characters." },
          url: { type: "string", description: "Absolute HTTPS resource URL." },
          description: { type: "string", description: "Optional plain-text explanation, up to 2,000 characters." },
        },
        required: ["title", "url"],
      },
    },
  ];
  const tools = () => [...MEMORY_DECLARATIONS, ...CONTENT_DECLARATIONS]
    .map((declaration) => ({ type: "function", function: declaration }));

  function runTool(name, args) {
    if (!args || typeof args !== "object" || Array.isArray(args)) return { ok: false, error: "Invalid tool arguments." };
    if (name === "create_artifact") return LifelineContent.validateArtifact(args);
    if (name === "create_embed") return LifelineContent.validateEmbed(args);
    if (name === "ask_follow_up") return LifelineContent.validateFollowUp(args);
    return runMemoryTool(name, args);
  }

  function systemPrompt(voice = false) {
    const mems = getMemories();
    return [
      "You are Lifeline, a warm and patient helper for life-insurance questions.",
      "Most users are older adults: explain things simply, never use jargon without defining it, and keep answers short and clear.",
      "When you need missing information, call ask_follow_up with one clear question and a few suitable answer choices when helpful, then wait. Never guess the user's answer. The user may answer by speaking, selecting a button, or typing their own reply. Do not ask several questions at once or call ask_follow_up more than once per turn.",
      "You are not a licensed agent; remind users to confirm important details with a real adviser and that you can connect them to a person at any time.",
      "When the user shares something important about themselves (name, family situation, health, policy type), call save_memory. If they ask what you remember, call list_memories. If they ask you to forget something, call forget_memory.",
      voice ? "Speak naturally without reading Markdown punctuation aloud. Documents can still use Markdown through create_artifact." : "Format replies with Markdown when helpful: **bold** key terms, *emphasis*, headings, lists, links, blockquotes, and tables. Keep formatting simple and readable; do not emit raw HTML.",
      "When asked for a summary of the user's information, first call list_memories, then call create_artifact with a descriptive title and Markdown document. Combine relevant saved memories with details explicitly shared in this conversation. Do not invent personal details, policy terms, prices, or contact information. Label missing facts as 'Not provided'.",
      "Use create_artifact for printable summaries, checklists, comparisons, and plans; avoid duplicating the entire document in the chat reply. Briefly tell the user the document is available in the conversation and can be printed or saved as PDF through Print. Do not claim a card was created if the tool reports an error.",
      "Use create_embed to show a resource or video card only when its HTTPS URL is supplied by the user or trusted context. Never generate arbitrary HTML or iframe markup. In voice mode, documents and resources are visible in the canvas beside the conversation; the user can view and print them without ending the call. Use create_artifact whenever a written checklist or summary would help while talking, and briefly point the user to the canvas instead of reading the whole document aloud.",
      mems.length ? `Things to remember about this user:\n- ${mems.map((m) => m.text).join("\n- ")}` : "",
    ].filter(Boolean).join("\n");
  }

  async function* readEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    function parse(raw) {
      const data = raw.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
      if (!data) return null;
      if (data === "[DONE]") return { done: true };
      try { return JSON.parse(data); } catch { return null; }
    }
    try {
      for (;;) {
        const { done, value } = await reader.read();
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const event = parse(buffer.slice(0, boundary.index));
          buffer = buffer.slice(boundary.index + boundary[0].length);
          if (event) yield event;
        }
        if (done) {
          const event = parse(buffer);
          if (event) yield event;
          break;
        }
      }
    } finally {
      try { await reader.cancel(); } catch { /* already closed */ }
      reader.releaseLock();
    }
  }

  async function streamCompletion({ messages, model, key, onText, signal, voice }) {
    const response = await fetch(API_URL, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}`, "X-OpenRouter-Title": "Lifeline" },
      body: JSON.stringify({ model, stream: true, messages: [{ role: "system", content: systemPrompt(voice) }, ...messages], tools: tools(), provider: { require_parameters: true } }),
    });
    if (!response.ok) {
      let message = `OpenRouter error (${response.status})`;
      try { message = (await response.json())?.error?.message || message; } catch { /* retain status */ }
      throw new Error(message);
    }
    if (!response.body) throw new Error("OpenRouter returned an empty response.");
    const calls = new Map();
    const reasoning = [];
    let content = "", done = false, finishReason = null;
    for await (const event of readEvents(response.body)) {
      signal?.throwIfAborted();
      if (event.error) throw new Error(event.error.message || "OpenRouter could not complete this response.");
      if (event.done) { done = true; break; }
      const choice = event.choices?.find((item) => item.index === 0) || event.choices?.[0];
      if (!choice) continue;
      if (choice.finish_reason) finishReason = choice.finish_reason;
      if (choice.finish_reason === "error") throw new Error("OpenRouter could not complete this response.");
      const delta = choice.delta || {};
      if (typeof delta.content === "string") { content += delta.content; onText?.(content); }
      // Keep provider reasoning blocks for subsequent tool calls, without displaying them.
      if (Array.isArray(delta.reasoning_details)) reasoning.push(...delta.reasoning_details);
      for (const part of delta.tool_calls || []) {
        const index = part.index ?? 0;
        const call = calls.get(index) || { id: "", type: "function", function: { name: "", arguments: "" } };
        if (part.id) call.id = part.id;
        if (part.function?.name) call.function.name += part.function.name;
        if (typeof part.function?.arguments === "string") call.function.arguments += part.function.arguments;
        calls.set(index, call);
      }
    }
    if (!done || !finishReason) throw new Error("The OpenRouter response was interrupted. Please try again.");
    if (finishReason === "length") throw new Error("The model reached its response limit. Please try a shorter request.");
    if (finishReason === "content_filter") throw new Error("The model could not answer this request. Please rephrase it.");
    const toolCalls = [...calls.values()];
    if (toolCalls.some((call) => !call.id || !call.function.name)) throw new Error("OpenRouter returned an incomplete tool call.");
    return {
      role: "assistant", content: content || null,
      ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
      ...(reasoning.length ? { reasoning_details: reasoning } : {}),
    };
  }

  async function chatTurn({ input, history = [], onText, onTool, signal, voice = false, model = voice ? getVoiceModel() : getModel() }) {
    signal?.throwIfAborted();
    const key = getKey();
    if (!key) throw new Error("Enter your OpenRouter API key in settings.");
    if (!model) throw new Error("Enter an OpenRouter model ID in settings.");
    const messages = [...history, { role: "user", content: input }];
    let text = "";
    let createdDocument = false, createdEmbed = false;
    for (let round = 0; round < 8; round++) {
      signal?.throwIfAborted();
      const prefix = text ? text + "\n\n" : "";
      const message = await streamCompletion({ messages, model, key, signal, voice, onText: (value) => onText?.(prefix + value) });
      text = message.content ? prefix + message.content : text;
      messages.push(message);
      if (!message.tool_calls?.length) {
        if (!text.trim() && (createdDocument || createdEmbed)) {
          text = createdDocument ? "Your document is ready in the conversation." : "Your resource is ready in the conversation.";
          message.content = text;
          onText?.(text);
        }
        if (!text.trim()) throw new Error("The model returned no answer. Please try again.");
        return { text, history: messages };
      }
      let followUp = null;
      for (const call of message.tool_calls) {
        signal?.throwIfAborted();
        let args;
        try { args = JSON.parse(call.function.arguments || "{}"); } catch { args = null; }
        const result = call.function.name === "ask_follow_up" && followUp
          ? { ok: false, error: "Only one follow-up question is allowed per turn." }
          : runTool(call.function.name, args);
        if (result.ok && result.followUp) followUp = result.followUp;
        if (result.ok && result.artifact) createdDocument = true;
        if (result.ok && result.embed) createdEmbed = true;
        onTool?.(call.function.name, result);
        messages.push({ role: "tool", tool_call_id: call.id, content: JSON.stringify(result) });
      }
      if (followUp) {
        if (!text.trim()) text = followUp.question;
        else if (!text.includes(followUp.question)) text += "\n\n" + followUp.question;
        messages.push({ role: "assistant", content: followUp.question });
        onText?.(text);
        return { text, history: messages, followUp };
      }
    }
    throw new Error("The AI needed too many steps. Please try your request again.");
  }

  async function transcribeAudio(data, signal, model = getTranscriptionModel()) {
    signal?.throwIfAborted();
    if (!getKey() || !model) throw new Error("Enter your OpenRouter API key and voice model ID in settings.");
    const response = await fetch(API_URL, {
      method: "POST", signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getKey()}`, "X-OpenRouter-Title": "Lifeline" },
      body: JSON.stringify({ model, stream: false, messages: [
        { role: "system", content: "Transcribe the user's speech exactly. Return only the spoken words, without commentary or answering questions. Return an empty string if there is no intelligible speech." },
        { role: "user", content: [{ type: "input_audio", input_audio: { data, format: "wav" } }] },
      ] }),
    });
    const payload = await response.json();
    signal?.throwIfAborted();
    if (!response.ok || payload.error) throw new Error(payload.error?.message || "Audio transcription failed. Choose an audio-input speech transcription model in settings.");
    const transcript = payload.choices?.[0]?.message?.content;
    if (typeof transcript !== "string") throw new Error("The speech transcription model returned no transcript. Choose a model with audio input and text output.");
    return transcript.trim();
  }

  function startVoice(handlers, history = []) {
    const model = getVoiceModel();
    const transcriptionModel = getTranscriptionModel();
    if (!getKey() || !model) throw new Error("Enter your OpenRouter API key and model ID in settings.");
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let useAudio = getVoiceInput() === "audio" || !Recognition || browserRecognitionFailed;
    const audioAvailable = () => window.LifelineVoiceInput && navigator.mediaDevices?.getUserMedia && (window.AudioContext || window.webkitAudioContext);
    if (useAudio && !audioAvailable())
      throw new Error("Voice input is unavailable in this browser. Use a supported browser on HTTPS or localhost, or type your question instead.");
    LifelineSpeech.prepare().catch((error) => handlers.onSpeechError?.(error.message));
    let recognition = useAudio ? null : new Recognition();
    const lang = document.documentElement.lang || "en-US";
    if (recognition) {
      recognition.lang = lang;
      recognition.interimResults = true;
      recognition.continuous = false;
    }
    let closed = false, muted = false, pending = false, listening = false, speaking = false;
    let controller = null, restartTimer = null, recorder = null;
    let openingAudio = false;
    let speechRun = 0;
    let messages = history;

    function listen() {
      if (closed || muted || pending || listening || (useAudio && !recorder)) return;
      try { if (recognition) recognition.start(); else recorder?.setListening(true); listening = true; handlers.onState?.("listening"); }
      catch (error) { if (recognition) switchToAudio(error.message); else fail(error.message || "Could not start voice input."); }
    }
    function resume() {
      clearTimeout(restartTimer);
      if (!closed && !muted && !pending) restartTimer = setTimeout(listen, 200);
    }
    function stop() {
      if (closed) return;
      closed = true;
      clearTimeout(restartTimer);
      controller?.abort();
      recognition?.abort();
      recorder?.close();
      cancelSpeech();
      handlers.onLevel?.(0);
    }
    function cancelSpeech() {
      speechRun++;
      speaking = false;
      LifelineSpeech.cancel();
    }
    function fail(message) {
      if (closed) return;
      stop();
      handlers.onClose?.(message);
    }
    function switchToAudio(reason = "") {
      if (closed || openingAudio || recorder) return;
      if (!audioAvailable()) {
        fail("Microphone audio is unavailable. Open Lifeline on HTTPS or localhost in a browser with microphone support, or type your reply.");
        return;
      }
      useAudio = true;
      openingAudio = true;
      if (recognition) {
        browserRecognitionFailed = true;
        const old = recognition;
        recognition = null;
        old.onresult = old.onend = old.onerror = null;
        try { old.abort(); } catch { /* Recognition may already have stopped. */ }
      }
      listening = false;
      clearTimeout(restartTimer);
      handlers.onInputMode?.("audio", reason);
      if (!pending && !muted) handlers.onState?.("connecting");
      window.LifelineVoiceInput.open({
        onAudio: (data) => sendTurn(data, true),
        onLevel: (value) => { if (!closed) handlers.onLevel?.(value); },
        onError: fail,
      }).then((input) => {
        openingAudio = false;
        if (closed) { input.close(); return; }
        recorder = input;
        if (muted) handlers.onState?.("muted"); else listen();
      }).catch((error) => {
        openingAudio = false;
        if (error.name === "NotAllowedError") fail("Microphone access was denied. Allow it in your browser to use voice.");
        else if (error.name === "NotFoundError") fail("No microphone was found. Connect a microphone and start voice again.");
        else fail(error.message || "Could not open the microphone. Check your browser's microphone settings.");
      });
    }
    function speak(text) {
      if (muted || closed) { pending = false; if (!closed) handlers.onState?.("muted"); resume(); return; }
      const plain = LifelineContent.renderMarkdown(document.createElement("div"), text).textContent.trim();
      const run = ++speechRun;
      const finish = () => {
        if (speechRun !== run || closed) return;
        speaking = false; pending = false; resume();
      };
      speaking = true;
      LifelineSpeech.play(plain, {
        onStatus: (status) => { if (speechRun === run && !closed) handlers.onSpeechStatus?.(status); },
        onStart: () => { if (speechRun === run && !closed) handlers.onState?.("speaking"); },
      }).then(() => {
        if (speechRun !== run || closed) return;
        handlers.onSpeechStatus?.("");
        finish();
      }).catch((error) => {
        if (speechRun !== run || closed) return;
        handlers.onSpeechError?.(error.message);
        finish();
      });
    }
    async function sendTurn(input, audio = false, typed = false) {
      if (closed || (!typed && muted) || (pending && !(typed && speaking)) || !input?.trim()) return;
      cancelSpeech();
      pending = true;
      recognition?.stop();
      recorder?.setListening(false);
      listening = false;
      controller = new AbortController();
      const signal = controller.signal;
      handlers.onState?.("thinking");
      try {
        const transcript = audio ? await transcribeAudio(input, signal, transcriptionModel) : input.trim();
        if (closed || signal.aborted) return;
        if (!transcript) { pending = false; resume(); return; }
        handlers.onUserText?.(transcript);
        handlers.onUserTurn?.(transcript);
        const result = await chatTurn({ input: transcript, history: messages, voice: true, model, signal,
          onText: (text) => { if (!closed && !signal.aborted) handlers.onModelText?.(text); },
          onTool: (name, result) => { if (!closed && !signal.aborted) handlers.onTool?.(name, result); },
        });
        if (closed || signal.aborted) return;
        messages = result.history;
        handlers.onHistory?.(messages);
        handlers.onTurnDone?.(transcript, result.text, result.followUp);
        speak(result.text);
      } catch (error) {
        if (closed || signal.aborted) return;
        stop();
        handlers.onClose?.(error.message);
      }
    }
    if (recognition) {
      const browserRecognition = recognition;
      recognition.onresult = (event) => {
        if (closed || useAudio || muted || pending) return;
        const results = Array.from(event.results);
        const transcript = results.map((result) => result[0]?.transcript || "").join(" ").trim();
        handlers.onUserText?.(transcript);
        // Wait for the entire utterance, including any interim segments.
        if (transcript && results.every((result) => result.isFinal)) return sendTurn(transcript);
      };
      recognition.onend = () => { if (closed || useAudio) return; listening = false; resume(); };
      recognition.onerror = (event) => {
        if (closed || useAudio || recognition !== browserRecognition || event.error === "aborted" || event.error === "no-speech") return;
        if (event.error === "not-allowed") { fail("Microphone access was denied. Allow it in your browser to use voice."); return; }
        switchToAudio(event.error);
      };
      handlers.onInputMode?.("browser");
      listen();
    } else {
      switchToAudio();
    }
    return {
      sendText(input) { return sendTurn(input, false, true); },
      setMuted(value) {
        muted = value;
        if (muted) {
          recognition?.abort();
          recorder?.setListening(false);
          listening = false;
          clearTimeout(restartTimer);
          if (speaking) pending = false;
          cancelSpeech();
        }
        // Finish an in-progress request; speak() skips audio if muted.
        else resume();
      },
      close: stop,
    };
  }

  return { getKey, setKey, getModel, setModel, getVoiceModel, setVoiceModel, getVoiceInput, setVoiceInput, getTranscriptionModel, setTranscriptionModel, listModels, transcribeAudio, getMemories, setMemories, runMemoryTool, runTool, systemPrompt, chatTurn, startVoice };
})();
