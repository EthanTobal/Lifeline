/* =========================================================
   Gemini wiring — DEMO ONLY.
   - Text: gemini-3.8-flash via the Interactions API (SSE)
   - Voice: gemini-3.8-live via the Live API (raw WebSocket)
   - Memory tools run locally (localStorage)
   Your key stays in this browser, but a client-side key can
   be extracted — do not ship this architecture to production.
   ========================================================= */
const Gemini = (() => {
  "use strict";

  const API_BASE = "https://generativelanguage.googleapis.com/v1beta";
  const WS_URL =
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";
  const FLASH_MODEL = "gemini-3.8-flash";
  const LIVE_MODEL = "gemini-3.8-live";

  /* ---------------- Key & memories (local) ---------------- */
  const getKey = () => localStorage.getItem("gemini-api-key")?.trim() || "";
  const setKey = (k) => localStorage.setItem("gemini-api-key", k.trim());

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
      case "calculate_coverage": {
        if (typeof Life === "undefined")
          return { ok: false, error: "Calculator not loaded." };
        const rec = Life.recommend(args);
        const summary = Life.buildSummary(args);
        // Hand the UI a ready-to-render payload so it can show a result
        // card and a print button. The model also receives these numbers
        // (as plain text) so it can explain them without doing math.
        document.dispatchEvent(new CustomEvent("coverage-result", { detail: { rec, summary, input: args } }));
        return {
          ok: true,
          recommendedCoverage: rec.recommendedCoverage,
          recommendedCoverageText: Life.USD(rec.recommendedCoverage),
          breakdown: rec.primary.breakdown.map((b) => ({ label: b.label, detail: b.detail, amount: b.amount })),
          offsets: rec.primary.offsetLines.filter((o) => o.amount > 0).map((o) => ({ label: o.label, amount: o.amount })),
          sanityCheckBand: { low: rec.sanityCheck.low, high: rec.sanityCheck.high },
          humanLifeValue: rec.humanLifeValue ? rec.humanLifeValue.total : null,
          estimatedMonthlyPremiumText: summary.estimatedMonthlyPremiumText,
          flags: rec.flags,
          disclaimer: rec.disclaimer,
          note: "A result card with a printable summary has been shown to the user. Explain these numbers in plain, calm language; do not recompute them.",
        };
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
    {
      name: "calculate_coverage",
      description:
        "Calculate how much life insurance coverage the user needs, using verified math (DIME needs analysis, a 10-15x income cross-check, Human Life Value, and a rough term premium). " +
        "You MUST call this tool to produce any coverage amount, gap, or premium figure — never do the arithmetic yourself. " +
        "Call it as soon as you have at least the annual income; pass every other value the user has given. Omit values you don't have yet (they default sensibly). " +
        "After it returns, explain the result in plain, calm language and offer to save a printable summary.",
      parameters: {
        type: "object",
        properties: {
          annualIncome: { type: "number", description: "Gross annual income in dollars. Required for a meaningful result." },
          age: { type: "number", description: "The user's age in years." },
          sex: { type: "string", description: "'male' or 'female', used only for the cost estimate." },
          smoker: { type: "boolean", description: "True if the user uses tobacco." },
          health: { type: "string", description: "One of: excellent, good, average, poor." },
          numChildren: { type: "number", description: "Number of children or dependents." },
          mortgageBalance: { type: "number", description: "Remaining mortgage balance in dollars." },
          nonMortgageDebt: { type: "number", description: "Other debts (credit cards, car, student loans) in dollars." },
          existingCoverage: { type: "number", description: "Life insurance they already have (employer + personal) in dollars." },
          liquidSavings: { type: "number", description: "Savings and investments available to the family in dollars." },
          incomeReplacementYears: { type: "number", description: "Years of income to replace (default 10)." },
          termYears: { type: "number", description: "Term length for the premium estimate (e.g. 10, 20, 30)." },
        },
        required: ["annualIncome"],
      },
    },
  ];

  const toolsForInteractions = () =>
    MEMORY_DECLARATIONS.map((d) => ({ type: "function", ...d }));
  const toolsForLive = () =>
    MEMORY_DECLARATIONS.map((d) => ({ ...d, behavior: "BLOCKING" }));

  function systemPrompt() {
    const mems = getMemories();
    return [
      "You are Lifeline, a warm and patient helper for life-insurance questions.",
      "Most users are older adults: explain things simply, never use jargon without defining it, and keep answers short and clear.",
      "You are not a licensed agent; remind users to confirm important details with a real adviser and that you can connect them to a person at any time.",
      "When the user shares something important about themselves (name, family situation, health, policy type), call save_memory. If they ask what you remember, call list_memories. If they ask you to forget something, call forget_memory.",
      "",
      "FIGURING OUT HOW MUCH COVERAGE THEY NEED:",
      "When a user wants a quote, a coverage amount, or to know how much insurance they need, gently gather their details ONE question at a time: annual income first, then dependents, mortgage, other debts, existing coverage, savings, and (for a cost estimate) age, whether they use tobacco, and general health. Keep it conversational, not a form.",
      "You MUST NOT calculate any dollar amount, coverage gap, or premium yourself — language models get numbers wrong. The moment you have at least their annual income, call the calculate_coverage tool with everything they've told you so far; leave out anything you don't know yet. It is fine to call it again with more details as the conversation continues.",
      "After the tool returns, explain the number in plain, reassuring language, walk through the breakdown simply, and reassure them this is a planning estimate, not a bill. Then offer to save them a printable summary they can keep or share.",
      mems.length ? `Things to remember about this user:\n- ${mems.map((m) => m.text).join("\n- ")}` : "",
    ].filter(Boolean).join("\n");
  }

  /* ---------------- Text chat: Interactions API ---------------- */
  async function* streamInteraction({ model, input, previousId, signal }) {
    const res = await fetch(`${API_BASE}/interactions?alt=sse`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json", "x-goog-api-key": getKey() },
      body: JSON.stringify({
        model,
        stream: true,
        store: true,
        system_instruction: systemPrompt(),
        generation_config: { thinking_level: "low" },
        tools: toolsForInteractions(),
        input,
        ...(previousId ? { previous_interaction_id: previousId } : {}),
      }),
    });
    if (!res.ok) {
      let msg = `API error (${res.status})`;
      try { msg = (await res.json())?.error?.message || msg; } catch { /* keep */ }
      throw new Error(msg);
    }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const pendingCalls = [];
    let interactionId = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();
      for (const raw of events) {
        const dataLine = raw.split("\n").find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        let ev;
        try { ev = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }

        if (ev.event_type === "interaction.created") interactionId = ev.interaction?.id || null;
        if (ev.event_type === "interaction.completed") interactionId = ev.interaction?.id || interactionId;

        if (ev.event_type === "step.start" && ev.step?.type === "function_call") {
          pendingCalls.push({ id: ev.step.id, name: ev.step.name, args: ev.step.arguments || {} });
        }
        if (ev.event_type === "step.delta" && ev.delta?.type === "text") {
          yield { type: "text", text: ev.delta.text };
        }
        if (ev.event_type === "step.delta" && ev.delta?.type === "arguments_delta" && pendingCalls.length) {
          // arguments stream in as a JSON string; buffer onto the last call
          const call = pendingCalls[pendingCalls.length - 1];
          call.rawArgs = (call.rawArgs || "") + (ev.delta.arguments || "");
        }
      }
    }

    for (const c of pendingCalls) {
      if (c.rawArgs) { try { c.args = JSON.parse(c.rawArgs); } catch { /* keep */ } }
      yield { type: "tool_call", call: c };
    }
    yield { type: "done", interactionId };
  }

  /* One full chat turn, with tool loop. onText(text) is called with the
     accumulated assistant text; return value = { text, interactionId }. */
  async function chatTurn({ input, previousId, onText, onTool, signal }) {
    let text = "";
    let calls = [];
    let id = previousId || null;
    const it = streamInteraction({ model: FLASH_MODEL, input, previousId: id, signal });
    for await (const ev of it) {
      if (ev.type === "text") { text += ev.text; onText?.(text); }
      else if (ev.type === "tool_call") calls.push(ev.call);
      else if (ev.type === "done") id = ev.interactionId || id;
    }

    if (calls.length) {
      const results = calls.map((c) => {
        const r = runMemoryTool(c.name, c.args);
        onTool?.(c.name, r);
        return { type: "function_result", name: c.name, call_id: c.id, result: [{ type: "text", text: JSON.stringify(r) }] };
      });
      text = "";
      const follow = streamInteraction({
        model: FLASH_MODEL,
        input: results,
        previousId: id,
        signal,
      });
      for await (const ev of follow) {
        if (ev.type === "text") { text += ev.text; onText?.(text); }
        else if (ev.type === "done") id = ev.interactionId || id;
      }
    }
    return { text, interactionId: id };
  }

  /* ---------------- Voice: Live API ---------------- */
  function floatToPcm16Base64(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++)
      out[i] = Math.max(-1, Math.min(1, f32[i])) * 0x7fff;
    let bin = "";
    const bytes = new Uint8Array(out.buffer);
    for (let i = 0; i < bytes.length; i += 0x8000)
      bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function base64ToPcm16(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer);
  }

  /* handlers: {onState(state: 'listening'|'thinking'|'speaking'), onUserText, onModelText, onTool, onLevel(v), onDone(text)} */
  async function startLive(handlers) {
    const key = getKey();
    if (!key) throw new Error("No API key set.");
    const ws = new WebSocket(`${WS_URL}?key=${encodeURIComponent(key)}`);
    const state = {
      ws, closed: false, muted: false,
      playCtx: new AudioContext({ sampleRate: 24000 }),
      nextPlay: 0, sources: [],
      userOpen: "", modelOpen: "",
    };

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("Couldn't reach Gemini Live. Check your key and connection."));
    });

    ws.send(JSON.stringify({
      setup: {
        model: `models/${LIVE_MODEL}`,
        generationConfig: {
          responseModalities: ["AUDIO"],
          speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: "Kore" } } },
        },
        systemInstruction: { parts: [{ text: systemPrompt() }] },
        tools: toolsForLive(),
        inputAudioTranscription: {},
        outputAudioTranscription: {},
        contextWindowCompression: { slidingWindow: {} },
      },
    }));

    /* mic capture at 16kHz */
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
    const micCtx = new AudioContext({ sampleRate: 16000 });
    const src = micCtx.createMediaStreamSource(stream);
    const analyser = micCtx.createAnalyser();
    analyser.fftSize = 512;
    src.connect(analyser);
    const proc = micCtx.createScriptProcessor(2048, 1, 1);
    analyser.connect(proc); proc.connect(micCtx.destination);
    proc.onaudioprocess = (e) => {
      if (state.muted || state.closed) return;
      ws.send(JSON.stringify({ realtimeInput: { audio: { data: floatToPcm16Base64(e.inputBuffer.getChannelData(0)), mimeType: "audio/pcm;rate=16000" } } }));
    };

    /* level for the orb */
    const data = new Uint8Array(analyser.fftSize);
    let playing = false, speakingLevel = 0;
    const levelLoop = () => {
      if (state.closed) return;
      if (playing) {
        speakingLevel = 0.4 + Math.random() * 0.45;
        handlers.onLevel?.(speakingLevel);
      } else {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += Math.abs(data[i] - 128) / 128;
        handlers.onLevel?.(Math.min(1, (sum / data.length) * 4));
      }
      requestAnimationFrame(levelLoop);
    };
    requestAnimationFrame(levelLoop);

    ws.onmessage = async (e) => {
      let msg;
      try { msg = JSON.parse(await (typeof e.data === "string" ? e.data : e.data.text())); } catch { return; }

      if (msg.serverContent) {
        const sc = msg.serverContent;
        if (sc.interrupted) {
          state.sources.forEach((s) => { try { s.stop(); } catch { /* done */ } });
          state.sources = []; state.nextPlay = 0; playing = false;
        }
        for (const part of sc.modelTurn?.parts || []) {
          if (part.inlineData?.data) {
            playing = true; handlers.onState?.("speaking");
            const pcm = base64ToPcm16(part.inlineData.data);
            const buf = state.playCtx.createBuffer(1, pcm.length, 24000);
            buf.copyToChannel(Float32Array.from(pcm, (v) => v / 0x8000), 0);
            const s = state.playCtx.createBufferSource();
            s.buffer = buf; s.connect(state.playCtx.destination);
            const startAt = Math.max(state.playCtx.currentTime + 0.03, state.nextPlay);
            s.start(startAt);
            state.nextPlay = startAt + buf.duration;
            state.sources.push(s);
            state.playCtx.resume().catch(() => {});
          }
        }
        if (sc.inputTranscription?.text) {
          state.userOpen += sc.inputTranscription.text;
          handlers.onUserText?.(state.userOpen);
          if (/\w$/.test(sc.inputTranscription.text) === false && sc.inputTranscription.text.includes("\n")) {
            // keep simple: no special casing
          }
        }
        if (sc.outputTranscription?.text) {
          state.modelOpen += sc.outputTranscription.text;
          handlers.onModelText?.(state.modelOpen);
        }
        if (sc.turnComplete) {
          playing = false;
          handlers.onTurnDone?.(state.userOpen.trim(), state.modelOpen.trim());
          state.userOpen = ""; state.modelOpen = "";
          handlers.onState?.("listening");
        }
        if (sc.waitingForInput || (sc.interactionStatus === "IDLE")) handlers.onState?.("listening");
      }

      if (msg.toolCall) {
        const results = [];
        for (const fc of msg.toolCall.functionCalls || []) {
          const r = runMemoryTool(fc.name, fc.args || {});
          handlers.onTool?.(fc.name, r);
          results.push({ id: fc.id, name: fc.name, response: { result: r } });
        }
        ws.send(JSON.stringify({ toolResponse: { functionResponses: results } }));
      }

      if (msg.goAway) handlers.onClose?.("The session timed out. You can start a new chat any time.");
    };

    ws.onclose = () => { state.closed = true; handlers.onClose?.(); };

    return {
      setMuted(m) { state.muted = m; if (!m) state.userOpen = ""; },
      close() {
        state.closed = true;
        try { ws.close(); } catch { /* ignore */ }
        stream.getTracks().forEach((t) => t.stop());
        try { proc.disconnect(); } catch { /* ignore */ }
        try { micCtx.close(); } catch { /* ignore */ }
        state.sources.forEach((s) => { try { s.stop(); } catch { /* ignore */ } });
        try { state.playCtx.close(); } catch { /* ignore */ }
      },
    };
  }

  return {
    FLASH_MODEL, LIVE_MODEL,
    getKey, setKey,
    getMemories, setMemories, runMemoryTool,
    systemPrompt, chatTurn, startLive,
  };
})();
