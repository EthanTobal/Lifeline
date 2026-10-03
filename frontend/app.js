/* =========================================================
   Lifeline — front end
   Real Gemini calls when an API key is set (gear button);
   otherwise a local mock so the demo still works.
   ========================================================= */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const el = {
    home: $("#home"),
    heroOrb: $(".orb-hero"),
    chat: $("#chat"),
    messages: $("#messages"),
    form: $("#composer"),
    input: $("#message-input"),
    send: $("#send-btn"),
    newChat: $("#new-chat-btn"),
    brand: $("#brand-home"),
    voice: $("#voice"),
    vOrb: $("#voice-orb"),
    vStatus: $("#voice-status"),
    vText: $("#voice-transcript"),
    mute: $("#mute-btn"),
    endVoice: $("#end-voice-btn"),
  };

  const ICONS = {
    speak: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>',
    up: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.3 20H7"/></svg>',
  };

  /* ---------- Greeting & text size ---------- */
  const hr = new Date().getHours();
  $("#greeting").textContent = hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";

  function setTextSize(size) {
    size === "normal" ? document.documentElement.removeAttribute("data-size")
                      : document.documentElement.setAttribute("data-size", size);
    $$(".ts").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.size === size)));
    localStorage.setItem("lifeline-size", size);
  }
  setTextSize(localStorage.getItem("lifeline-size") || "normal");
  $$(".ts").forEach((b) => b.addEventListener("click", () => setTextSize(b.dataset.size)));

  /* ---------- Mock replies ---------- */
  const REPLIES = [
    {
      match: /quote|cost|price|how much/i,
      blocks: [
        { p: "I'd be happy to help you get a quote. It only takes a few minutes, and there's no obligation." },
        { p: "I'll just need a few details:" },
        { ul: ["Your date of birth", "Whether you smoke", "How much cover you'd like", "How long you'd like it to last"] },
        { p: "Shall we start with your date of birth?" },
      ],
      suggestions: ["Yes, let's start", "How much cover do I need?"],
    },
    {
      match: /policy|understand|document/i,
      blocks: [
        { p: "Of course. Insurance documents can be confusing, so let's go through yours together." },
        { p: "You can tell me your policy number, or just describe what you'd like explained — what's covered, what you pay, or who receives the money." },
      ],
      suggestions: ["What does my policy cover?", "Who is my beneficiary?"],
    },
    {
      match: /claim|passed|died|death/i,
      blocks: [
        { p: "I'm so sorry if you're going through a difficult time. I'll guide you gently, one step at a time." },
        { p: "To start a claim, it helps to have:" },
        { ul: ["The policy number, if you have it", "A copy of the death certificate", "Your contact details"] },
        { p: "Don't worry if you don't have everything yet — we can begin now and add things later." },
      ],
      suggestions: ["Start a claim", "I don't have the policy number"],
    },
    {
      match: /term|whole|difference|basics/i,
      blocks: [
        { p: "Here's the simple version:" },
        { ul: [
          "Term life covers you for a set number of years, such as 10 or 20. It usually costs less.",
          "Whole life covers you for your entire life and can build cash value. It usually costs more.",
        ] },
      ],
      suggestions: ["Which is right for me?", "Get a quote"],
    },
    {
      match: /person|human|agent|call|real/i,
      blocks: [
        { p: "Absolutely. One of our advisers can give you a call." },
        { p: "Our team is available Monday to Friday, 8am to 8pm. When would suit you best?" },
      ],
      suggestions: ["This morning", "This afternoon", "Tomorrow"],
    },
  ];

  const DEFAULT_REPLY = {
    blocks: [
      { p: "Thank you for your question. I can help with quotes, understanding your policy, making a claim, or explaining how life insurance works." },
      { p: "Could you tell me a little more about what you'd like to know?" },
    ],
    suggestions: ["Get a quote", "Explain the basics"],
  };

  const LONG_REPLY = {
    blocks: [
      { p: "Here's an overview of how life insurance works." },
      { p: "Life insurance is an agreement between you and an insurance company. You pay a regular amount, called a premium, and in return the company pays a sum of money to the people you choose if you pass away while the policy is active." },
      { ul: [
        "The people who receive the money are called beneficiaries.",
        "The amount they receive is called the death benefit.",
        "Premiums can be paid monthly or yearly.",
      ] },
      { p: "Most people use it to help their family with a mortgage, everyday bills, or funeral costs." },
    ],
    suggestions: ["What's a premium?", "How much cover do I need?"],
  };

  const THINKING_LINES = [
    "Thinking",
    "Reading your question",
    "Looking into this for you",
    "Putting together a clear answer",
  ];

  const getMockReply = (text) => REPLIES.find((r) => r.match.test(text)) || DEFAULT_REPLY;

  /* ---------- Views ---------- */
  function showChat() {
    if (!el.chat.hidden) return;
    el.home.hidden = true;
    el.chat.hidden = false;
    el.newChat.hidden = false;
  }
  function showHome() {
    stopThinking();
    lastInteractionId = null;
    el.messages.innerHTML = "";
    el.chat.hidden = true;
    el.newChat.hidden = true;
    el.home.hidden = false;
    el.home.style.animation = "none"; void el.home.offsetWidth; el.home.style.animation = "";
    scrollTo({ top: 0 });
    el.input.focus();
  }
  const scrollDown = () => scrollTo({ top: document.body.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });

  function toast(msg) {
    $(".toast")?.remove();
    const t = Object.assign(document.createElement("div"), { className: "toast", textContent: msg });
    t.setAttribute("role", "status");
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2700);
  }

  function smallOrb(state = "idle") {
    const o = document.createElement("div");
    o.className = "orb orb-sm";
    o.dataset.state = state;
    o.setAttribute("aria-hidden", "true");
    o.innerHTML = '<span class="orb-core"></span>';
    return o;
  }

  function makeMsg(role) {
    const li = document.createElement("li");
    li.className = `msg msg-${role}`;
    const body = document.createElement("div");
    body.className = "msg-body";
    const text = document.createElement("div");
    text.className = "text";
    body.appendChild(text);
    if (role !== "user") li.appendChild(smallOrb());
    li.appendChild(body);
    el.messages.appendChild(li);
    return { li, body, text };
  }

  function addUserMessage(str) {
    showChat();
    const { text } = makeMsg("user");
    text.innerHTML = "";
    const p = document.createElement("p");
    p.textContent = str;
    text.appendChild(p);
    scrollDown();
  }

  /* ---------- Thinking indicator ---------- */
  let thinking = null;
  function startThinking() {
    stopThinking();
    showChat();
    const li = document.createElement("li");
    li.className = "msg msg-ai thinking";
    li.appendChild(smallOrb("thinking"));
    const label = document.createElement("span");
    label.className = "shimmer";
    label.textContent = THINKING_LINES[0];
    li.appendChild(label);
    li.setAttribute("aria-label", "Lifeline is thinking");
    el.messages.appendChild(li);

    let i = 0;
    const timer = setInterval(() => {
      if (i >= THINKING_LINES.length - 1) return;
      i++;
      label.classList.add("fade");
      setTimeout(() => { label.textContent = THINKING_LINES[i]; label.classList.remove("fade"); }, 300);
    }, 1700);
    thinking = { li, timer };
    scrollDown();
  }
  function stopThinking() {
    if (!thinking) return;
    clearInterval(thinking.timer);
    thinking.li.remove();
    thinking = null;
  }

  /* ---------- Streaming reply ---------- */
  let busy = false;
  let lastInteractionId = null;

  async function respond(userText, override) {
    if (busy) return;
    busy = true; updateSend();

    if (Gemini.getKey() && !override) {
      startThinking();
      let created = false;
      let aiMsg = null;
      const shownTools = new Set();
      try {
        await Gemini.chatTurn({
          input: userText,
          previousId: lastInteractionId,
          onTool(name, r) {
            toast(name === "save_memory" ? "Saved to memory" :
                  name === "forget_memory" ? "Memory removed" : "Memories loaded");
            renderMemories();
          },
          onText(full) {
            if (!created) {
              stopThinking();
              showChat();
              aiMsg = makeMsg("ai");
              $(".orb", aiMsg.li).dataset.state = "speaking";
              created = true;
            }
            const p = aiMsg.text.querySelector("p") || aiMsg.text.appendChild(document.createElement("p"));
            p.textContent = full;
            scrollDown();
          },
        }).then((res) => {
          lastInteractionId = res.interactionId || lastInteractionId;
          if (aiMsg) {
            $(".orb", aiMsg.li).dataset.state = "idle";
            addActions(aiMsg.body, aiMsg.text);
            scrollDown();
          }
        });
      } catch (err) {
        stopThinking();
        addError(err.message);
      }
      if (!created) stopThinking();
      busy = false; updateSend();
      return;
    }

    /* ---- local mock fallback ---- */
    startThinking();
    await sleep(1500 + Math.random() * 1300);
    stopThinking();
    await streamReply(override || getMockReply(userText));
    busy = false; updateSend();
  }

  function addError(msg) {
    showChat();
    const { li, text } = makeMsg("ai");
    li.classList.add("msg-error");
    text.innerHTML = msg
      ? `<p>${msg}</p><p>Please check your API key and try again.</p>`
      : "<p>Sorry, I'm having trouble connecting right now. Please try again in a moment — your message hasn't been lost.</p>";
    scrollDown();
  }

  async function streamReply(reply) {
    showChat();
    const { li, body, text } = makeMsg("ai");
    const orb = $(".orb", li);
    orb.dataset.state = "speaking";

    for (const block of reply.blocks) {
      if (block.p) {
        await typeInto(text.appendChild(document.createElement("p")), block.p);
      } else if (block.ul) {
        const ul = text.appendChild(document.createElement("ul"));
        for (const item of block.ul) await typeInto(ul.appendChild(document.createElement("li")), item);
      }
      await sleep(reduceMotion ? 0 : 160);
    }
    orb.dataset.state = "idle";

    addActions(body, text);
    if (reply.suggestions) addSuggestions(body, reply.suggestions);
    scrollDown();
  }

  async function typeInto(node, str) {
    if (reduceMotion) { node.textContent = str; return; }
    const words = str.split(" ");
    for (let i = 0; i < words.length; i++) {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = (i ? " " : "") + words[i];
      node.appendChild(w);
      if (i % 5 === 0) scrollDown();
      await sleep(/[.!?]$/.test(words[i]) ? 200 : /[,;:—]$/.test(words[i]) ? 110 : 30 + Math.random() * 35);
    }
    node.textContent = str; // flatten spans
  }

  function addActions(body, text) {
    const row = document.createElement("div");
    row.className = "msg-actions";
    row.innerHTML =
      `<button class="chip" data-act="read">${ICONS.speak}Read aloud</button>` +
      `<button class="chip" data-act="helpful" aria-pressed="false">${ICONS.up}Helpful</button>`;
    row.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.act === "read") toast("Read aloud isn't connected yet");
      else {
        const on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        if (on) toast("Thanks for your feedback");
      }
    });
    body.appendChild(row);
  }

  function addSuggestions(body, list) {
    $$(".suggestions", el.messages).forEach((s) => s.remove());
    const row = document.createElement("div");
    row.className = "suggestions";
    for (const s of list) {
      const b = document.createElement("button");
      b.className = "suggestion";
      b.textContent = s;
      b.addEventListener("click", () => submit(s));
      row.appendChild(b);
    }
    body.appendChild(row);
  }

  /* ---------- Composer ---------- */
  function updateSend() { el.send.disabled = busy || !el.input.value.trim(); }
  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
    updateSend();
  }
  function submit(str) {
    str = (str ?? el.input.value).trim();
    if (!str || busy) return;
    $$(".suggestions", el.messages).forEach((s) => s.remove());
    addUserMessage(str);
    el.input.value = "";
    autoGrow();
    respond(str);
  }

  el.input.addEventListener("input", autoGrow);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  el.form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  $$(".topic").forEach((t) => t.addEventListener("click", () => submit(t.dataset.prompt)));
  el.newChat.addEventListener("click", showHome);
  el.brand.addEventListener("click", (e) => { e.preventDefault(); showHome(); });

  /* =========================================================
     Voice mode (simulated)
     A single orb reacts to a fake audio level via --level.
     Later: feed real mic / TTS amplitude into setLevel().
     ========================================================= */
  const VOICE_COPY = {
    listening: ["Listening", "Go ahead, I'm listening."],
    thinking:  ["Thinking", ""],
    speaking:  ["Speaking", ""],
    muted:     ["Microphone off", "Press Unmute when you're ready."],
  };
  let voiceRun = 0;
  let levelRAF = 0;
  let lastFocus = null;
  let liveSession = null;

  function setLevel(v) { el.vOrb.style.setProperty("--level", v.toFixed(3)); }

  /* Fake amplitude for the no-key demo */
  function animateLevel() {
    cancelAnimationFrame(levelRAF);
    let cur = 0;
    const tick = (t) => {
      const s = el.vOrb.dataset.state;
      let target = 0;
      if (s === "speaking") target = .35 + .35 * Math.abs(Math.sin(t / 140) * Math.sin(t / 370 + 1));
      else if (s === "listening" && el.vOrb.dataset.hearing === "1") target = .2 + .3 * Math.abs(Math.sin(t / 180) * Math.cos(t / 410));
      else if (s === "listening") target = .04 + .03 * Math.sin(t / 900);
      cur += (target - cur) * .18;
      setLevel(reduceMotion ? 0 : cur);
      levelRAF = requestAnimationFrame(tick);
    };
    levelRAF = requestAnimationFrame(tick);
  }

  function setVoiceState(state, text) {
    el.vOrb.dataset.state = state;
    el.vOrb.dataset.hearing = "0";
    el.vStatus.textContent = VOICE_COPY[state][0];
    el.vText.textContent = text ?? VOICE_COPY[state][1];
  }

  function openVoice() {
    lastFocus = document.activeElement;
    el.voice.hidden = false;
    document.body.style.overflow = "hidden";
    el.mute.setAttribute("aria-pressed", "false");
    $("span", el.mute).textContent = "Mute";
    el.endVoice.focus();

    if (Gemini.getKey()) {
      el.vOrb.dataset.state = "listening";
      el.vStatus.textContent = "Connecting";
      el.vText.textContent = "";
      Gemini.startLive({
        onState(s) {
          if (s === "speaking") { el.vOrb.dataset.state = "speaking"; el.vStatus.textContent = "Speaking"; }
          else if (s === "listening") { el.vOrb.dataset.state = "listening"; el.vStatus.textContent = "Listening"; }
        },
        onUserText(t) { el.vText.textContent = "“" + t + "”"; },
        onModelText(t) { el.vText.textContent = t; },
        onTurnDone(user, model) {
          if (user || model) el.vText.textContent = (user ? `You: “${user}”\n` : "") + (model || "");
        },
        onTool(name) { renderMemories(); toast(name === "save_memory" ? "Saved to memory" : name === "forget_memory" ? "Memory removed" : "Memories loaded"); },
        onLevel(v) { el.vOrb.style.setProperty("--level", reduceMotion ? 0 : v.toFixed(3)); },
        onClose(msg) { if (msg) toast(msg); },
      }).then((s) => { liveSession = s; el.vStatus.textContent = "Listening"; })
        .catch((err) => { el.voice.hidden = true; document.body.style.overflow = ""; addError(err.message); toast(err.message); });
      return;
    }

    // mock demo when no key
    setVoiceState("listening");
    animateLevel();
    runVoiceDemo();
  }

  function closeVoice() {
    voiceRun++;
    cancelAnimationFrame(levelRAF);
    liveSession?.close();
    liveSession = null;
    el.voice.hidden = true;
    document.body.style.overflow = "";
    lastFocus?.focus?.();
  }

  async function runVoiceDemo() {
    const id = ++voiceRun;
    const alive = () => id === voiceRun && !el.voice.hidden;
    const question = "How much would life insurance cost for someone my age?";
    const answer = "It depends on a few things, like your age and health. I can work out a personal quote for you in just a few minutes.";

    setVoiceState("listening");
    await sleep(1400); if (!alive()) return;

    el.vOrb.dataset.hearing = "1";
    let said = "";
    for (const w of question.split(" ")) {
      if (!alive()) return;
      said += (said ? " " : "") + w;
      el.vText.textContent = `“${said}”`;
      await sleep(240);
    }
    el.vOrb.dataset.hearing = "0";
    await sleep(600); if (!alive()) return;

    setVoiceState("thinking", `“${question}”`);
    await sleep(2200); if (!alive()) return;

    setVoiceState("speaking", "");
    let spoken = "";
    for (const w of answer.split(" ")) {
      if (!alive()) return;
      spoken += (spoken ? " " : "") + w;
      el.vText.textContent = spoken;
      await sleep(210);
    }
    await sleep(1200); if (!alive()) return;
    setVoiceState("listening");
  }

  $("#voice-btn").addEventListener("click", openVoice);
  $("#voice-cta").addEventListener("click", openVoice);
  el.endVoice.addEventListener("click", closeVoice);
  el.mute.addEventListener("click", () => {
    const muted = el.mute.getAttribute("aria-pressed") !== "true";
    el.mute.setAttribute("aria-pressed", String(muted));
    $("span", el.mute).textContent = muted ? "Unmute" : "Mute";
    liveSession?.setMuted(muted);
    if (liveSession) {
      el.vOrb.dataset.state = muted ? "muted" : "listening";
      el.vStatus.textContent = muted ? "Microphone off" : "Listening";
      if (muted) el.vText.textContent = "Press Unmute when you're ready.";
    } else {
      voiceRun++;
      setVoiceState(muted ? "muted" : "listening");
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !el.voice.hidden) closeVoice(); });
  el.voice.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const f = $$("button", el.voice), first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* ---------- Test panel ---------- */
  const devToggle = $("#devpanel-toggle"), devBody = $("#devpanel-body");
  devToggle.addEventListener("click", () => {
    devBody.hidden = !devBody.hidden;
    devToggle.setAttribute("aria-expanded", String(!devBody.hidden));
  });

  const SAMPLES = ["What's the difference between term and whole life?", "How do I make a claim?", "Can I speak to a real person?"];

  devBody.addEventListener("click", async (e) => {
    const a = e.target.closest("button")?.dataset.dev;
    if (!a) return;
    const voiceMap = { "v-listen": "listening", "v-think": "thinking", "v-speak": "speaking" };
    if (voiceMap[a]) {
      if (el.voice.hidden) openVoice();
      voiceRun++;
      setVoiceState(voiceMap[a], a === "v-speak" ? "Here's what I found for you." : undefined);
      return;
    }
    switch (a) {
      case "user": addUserMessage(SAMPLES[Math.floor(Math.random() * SAMPLES.length)]); break;
      case "reply": if (!busy) { busy = true; await streamReply(DEFAULT_REPLY); busy = false; updateSend(); } break;
      case "thinking": startThinking(); break;
      case "stop": stopThinking(); break;
      case "long": respond("", LONG_REPLY); break;
      case "error": stopThinking(); addError(); break;
      case "clear": showHome(); break;
      case "v-auto": el.voice.hidden ? openVoice() : runVoiceDemo(); break;
    }
  });

  /* ---------- Settings & memories dialogs ---------- */
  const settingsDlg = $("#settings-dlg"), memoriesDlg = $("#memories-dlg");
  const keyInput = $("#key-input");

  function openDlg(d) { d.hidden = false; }
  function closeDlg(d) { d.hidden = true; }

  $("#settings-btn").addEventListener("click", () => {
    keyInput.value = Gemini.getKey();
    openDlg(settingsDlg);
    keyInput.focus();
  });
  $("#key-save").addEventListener("click", () => {
    const k = keyInput.value.trim();
    if (k) { Gemini.setKey(k); toast("Key saved in this browser"); }
    closeDlg(settingsDlg);
  });
  $("#memories-btn").addEventListener("click", () => { renderMemories(); openDlg(memoriesDlg); });
  $$("[data-close]").forEach((b) => b.addEventListener("click", () => closeDlg(b.closest(".dlg"))));
  [settingsDlg, memoriesDlg].forEach((d) =>
    d.addEventListener("click", (e) => { if (e.target === d) closeDlg(d); }));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") [settingsDlg, memoriesDlg].forEach(closeDlg);
  });

  function renderMemories() {
    const mems = Gemini.getMemories();
    const list = $("#mem-list");
    list.innerHTML = "";
    $("#mem-empty").style.display = mems.length ? "none" : "";
    for (const m of mems) {
      const li = document.createElement("li");
      li.innerHTML = `<span></span><button class="chip" aria-label="Delete this memory">Delete</button>`;
      li.querySelector("span").textContent = m.text;
      li.querySelector("button").addEventListener("click", () => {
        Gemini.setMemories(Gemini.getMemories().filter((x) => x.id !== m.id));
        renderMemories();
      });
      list.appendChild(li);
    }
  }
  $("#mem-clear").addEventListener("click", () => { Gemini.setMemories([]); renderMemories(); });
  document.addEventListener("memories-changed", renderMemories);

  /* ---------- Coverage result card ----------
     gemini.js dispatches "coverage-result" when the calculate_coverage
     tool runs. We render a clear, printable card into the chat so the
     user sees the real breakdown (not just the AI's prose) and can keep
     a copy. The actual math lives in calculator.js (Life). */
  document.addEventListener("coverage-result", (e) => {
    const { rec, summary } = e.detail || {};
    if (!rec || typeof Life === "undefined") return;
    showChat();

    const li = document.createElement("li");
    li.className = "msg msg-ai";
    li.appendChild(smallOrb("idle"));

    const card = document.createElement("div");
    card.className = "coverage-card";

    const need = rec.primary;
    const rows = need.breakdown
      .map((b) => `<div class="cc-row"><span>${b.label}<small>${b.detail}</small></span><b>${Life.USD(b.amount)}</b></div>`)
      .join("");
    const offsets = need.offsetLines
      .filter((o) => o.amount > 0)
      .map((o) => `<div class="cc-row cc-sub"><span>Less: ${o.label}</span><b>−${Life.USD(o.amount)}</b></div>`)
      .join("");
    const premium = summary.estimatedMonthlyPremiumText
      ? `<p class="cc-premium">Rough cost: about <b>${summary.estimatedMonthlyPremiumText}</b> for term coverage (an estimate, not a quote).</p>`
      : "";
    const flags = (rec.flags || [])
      .map((f) => `<p class="cc-flag">${f}</p>`).join("");

    card.innerHTML =
      `<p class="cc-label">Estimated coverage you may need</p>` +
      `<p class="cc-total">${Life.USD(rec.recommendedCoverage)}</p>` +
      `<div class="cc-breakdown">${rows}${offsets}</div>` +
      premium + flags +
      `<p class="cc-cross">Quick cross-check (10–15× income): ${Life.USD(rec.sanityCheck.low)} – ${Life.USD(rec.sanityCheck.high)}</p>` +
      `<div class="cc-actions">` +
      `<button class="btn btn-primary cc-print">Save / print my summary</button>` +
      `</div>` +
      `<p class="cc-fine">${rec.disclaimer}</p>`;

    card.querySelector(".cc-print").addEventListener("click", () => {
      const ok = Life.printSummary(summary);
      if (!ok) toast("Please allow pop-ups to print your summary");
    });

    const body = document.createElement("div");
    body.className = "msg-body";
    body.appendChild(card);
    li.appendChild(body);
    el.messages.appendChild(li);
    scrollDown();
  });
})();
