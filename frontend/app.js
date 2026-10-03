/* =========================================================
   Lifeline — front end
   Real OpenRouter calls when an API key is set (gear button);
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
    vCanvas: $("#voice-canvas-content"),
    vCanvasEmpty: $("#voice-canvas-empty"),
    vFollowUp: $("#voice-follow-up"),
    vReply: $("#voice-reply-input"),
    vReplySend: $("#voice-reply-send"),
    documents: $("#documents-panel"),
    documentsToggle: $("#documents-btn"),
    documentsList: $("#documents-list"),
    documentContent: $("#document-content"),
  };
  const workspaceContent = [];
  let currentFollowUp = null;
  let selectedDocument = 0;
  let previewedDocument = null;
  let documentViewerFocus = null;
  let documentViewerDismissed = false;
  let documentModalActive = false;
  let documentViewerOverflow = "";
  let documentViewerOpen = false;
  let documentViewerClosing = false;
  let documentCloseTimer = null;
  const documentsMedia = matchMedia("(max-width: 68rem)");

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

  const MARKDOWN_REPLY = {
    markdown: "## Life insurance, at a glance\n\n**Term life** covers a set period. *Whole life* can cover your lifetime.\n\n- **Premium:** what you pay for cover.\n- **Beneficiary:** the person you choose to receive the payout.\n\n### Next steps\n\n1. Think about who depends on you.\n2. Ask an adviser to confirm your policy details.\n\n> Keep a copy of your policy somewhere easy to find.\n\n| Term | Meaning |\n| --- | --- |\n| Cover | The amount insured |\n| Premium | Your regular payment |\n\nYou can use `policy number` as a reference. ~~Old details~~ can be replaced.\n\n[Learn about insurance](https://www.naic.org/consumer_insurance.htm)",
  };
  const ARTIFACT_REPLY = {
    markdown: "Here is an **example summary** you can open and print. These are fictional demo details.",
    artifacts: [{
      title: "Your information — example summary",
      markdown: "## About you\n\n*Fictional details for testing only.*\n\n| Detail | Information |\n| --- | --- |\n| Name | Margaret |\n| Location | Ohio |\n| Family | Two adult children |\n| Goal | Help family with funeral costs |\n| Current policy | Not provided |\n\n## Questions for your adviser\n\n1. How much cover would meet my needs?\n2. What would the monthly premium be?\n3. Are there any exclusions?\n\n> Confirm policy terms and costs with a licensed adviser.",
    }],
  };
  const EMBED_REPLY = {
    markdown: "Here are example **resource and video embeds**. Select Load video to play the demo video.",
    embeds: [
      { title: "Consumer insurance resources", url: "https://content.naic.org/consumer", description: "Consumer resources from the National Association of Insurance Commissioners." },
      { title: "Example video — Big Buck Bunny", url: "https://www.youtube.com/watch?v=aqz-KE-bpKQ", description: "A demo video to try the embedded player; unrelated to insurance." },
    ],
  };
  const userMessages = [];
  const escapeMarkdown = (value) => String(value).replace(/[\\`*_{}\[\]()<>#+.!|~-]/g, "\\$&");

  function summaryReply() {
    const memories = OpenRouter.getMemories().map((m) => m.text);
    const shared = userMessages.filter((message) => !/summary|summari[sz]e|artifact|my information/i.test(message));
    const facts = [...new Set([...memories, ...shared])];
    return {
      markdown: "Here is a **summary of your information**. Open the document to review it, or select Print to print it or save it as a PDF.",
      artifacts: [{ title: "Your information summary", markdown:
        "## Information you shared\n\n" + (facts.length ? facts.map((fact) => "- " + escapeMarkdown(fact)).join("\n") : "No personal information has been provided yet.") +
        "\n\n## Details to confirm\n\nOnly information shared above is known. Other personal and policy details: **Not provided**.\n\n> This summary reflects your conversation and saved memories. Confirm important policy details with your adviser." }],
    };
  }

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

  const getMockReply = (text) => {
    if (/summary|summari[sz]e|what.*remember|my (?:details|information)|artifact/i.test(text)) return summaryReply();
    if (/embed|video|resource/i.test(text)) {
      const url = text.match(/https:\/\/[^\s<>]+/)?.[0];
      if (url && LifelineContent.validateEmbed({ title: "Shared resource", url }).ok)
        return { markdown: "Here is the resource you shared.", embeds: [{ title: "Shared resource", url }] };
      return { markdown: "Share an **HTTPS link** to the resource or YouTube/Vimeo video you'd like to embed." };
    }
    return REPLIES.find((r) => r.match.test(text)) || DEFAULT_REPLY;
  };

  /* ---------- Views ---------- */
  function showChat() {
    if (!el.chat.hidden) return;
    el.home.hidden = true;
    el.chat.hidden = false;
    el.newChat.hidden = false;
  }
  function showHome() {
    closeDocumentViewer(false, true);
    LifelineSpeech.cancel();
    if (!el.voice.hidden) closeVoice();
    activeResponse?.abort();
    activeResponse = null;
    busy = false;
    updateSend();
    stopThinking();
    conversationHistory = [];
    conversationModel = "";
    currentFollowUp = null;
    workspaceContent.length = 0;
    selectedDocument = 0;
    documentViewerDismissed = false;
    renderDocuments();
    renderVoiceCanvas();
    renderVoiceFollowUp();
    userMessages.length = 0;
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
    userMessages.push(str);
    showChat();
    const { text } = makeMsg("user");
    LifelineContent.renderMarkdown(text, str);
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
  let conversationHistory = [];
  let conversationModel = "";
  let activeResponse = null;

  function historyForModel(model) {
    if (!conversationModel || conversationModel === model) return conversationHistory;
    return conversationHistory.map(({ reasoning_details, ...message }) => message);
  }

  async function respond(userText, override) {
    if (busy) return;
    busy = true; updateSend();
    const controller = new AbortController();
    activeResponse = controller;
    const { signal } = controller;

    if (OpenRouter.getKey() && !override) {
      startThinking();
      let created = false;
      let aiMsg = null;
      const ensureMessage = () => {
        if (!created) {
          stopThinking();
          showChat();
          aiMsg = makeMsg("ai");
          $(".orb", aiMsg.li).dataset.state = "speaking";
          created = true;
        }
        return aiMsg;
      };
      try {
        await OpenRouter.chatTurn({
          input: userText,
          history: historyForModel(OpenRouter.getModel()),
          signal,
          onTool(name, r) {
            if (signal.aborted) return;
            if (r.ok && (r.artifact || r.embed)) appendContent(ensureMessage().body, r);
            else handleMemoryTool(name, r);
          },
          onText(full) {
            if (signal.aborted) return;
            LifelineContent.renderMarkdown(ensureMessage().text, full);
            scrollDown();
          },
        }).then((res) => {
          if (signal.aborted) return;
          conversationHistory = res.history;
          conversationModel = OpenRouter.getModel();
          currentFollowUp = res.followUp || null;
          if (aiMsg) {
            $(".orb", aiMsg.li).dataset.state = "idle";
            addActions(aiMsg.body, aiMsg.text);
            if (res.followUp) addSuggestions(aiMsg.body, res.followUp.options, res.followUp.question);
            scrollDown();
          }
        });
      } catch (err) {
        if (!signal.aborted) {
          stopThinking();
          if (aiMsg) $(".orb", aiMsg.li).dataset.state = "idle";
          addError(err.message);
        }
      }
      if (signal.aborted) return;
      if (!created) stopThinking();
      busy = false; updateSend();
      activeResponse = null;
      return;
    }

    /* ---- local mock fallback ---- */
    try {
      startThinking();
      await sleep(1500 + Math.random() * 1300);
      if (signal.aborted) return;
      stopThinking();
      await streamReply(override || getMockReply(userText), signal);
    } catch (err) {
      if (!signal.aborted) { stopThinking(); addError(err.message); }
    } finally {
      if (activeResponse === controller) {
        busy = false; updateSend();
        activeResponse = null;
      }
    }
  }

  function addError(msg) {
    showChat();
    const { li, text } = makeMsg("ai");
    li.classList.add("msg-error");
    const p = document.createElement("p");
    p.textContent = msg || "Sorry, I'm having trouble connecting right now. Please try again in a moment — your message hasn't been lost.";
    text.appendChild(p);
    if (msg) {
      const retry = document.createElement("p");
      retry.textContent = "Please check your OpenRouter API key and model in settings, then try again.";
      text.appendChild(retry);
    }
    scrollDown();
  }

  function appendContent(body, result) {
    const hadDocuments = sessionDocuments().length > 0;
    if (result.artifact) {
      const index = sessionDocuments().length;
      body.appendChild(LifelineContent.artifactCard(result.artifact, toast, () => openDocumentViewer(index)));
    }
    if (result.embed) body.appendChild(LifelineContent.embedCard(result.embed));
    if (result.artifact || result.embed) {
      workspaceContent.push(result.artifact ? { artifact: result.artifact } : { embed: result.embed });
      renderVoiceCanvas();
      renderDocuments();
      if (result.artifact && !hadDocuments && !documentViewerDismissed && !documentsMedia.matches && el.voice.hidden)
        openDocumentViewer(0, false);
    }
    scrollDown();
  }

  /* ---------- Session document viewer ---------- */
  function sessionDocuments() { return workspaceContent.filter((item) => item.artifact).map((item) => item.artifact); }

  function renderDocuments() {
    const documents = sessionDocuments();
    $("#documents-count").textContent = documents.length;
    el.documentsToggle.setAttribute("aria-label", `View documents (${documents.length})`);
    $("#documents-summary").textContent = documents.length
      ? `${documents.length} document${documents.length === 1 ? "" : "s"} in this chat`
      : "No documents in this chat yet";
    $("#documents-empty").hidden = documents.length > 0;
    $("#documents-nav").hidden = !documents.length;
    $("#document-preview").hidden = !documents.length;
    const focusedIndex = document.activeElement?.closest("#documents-list button")?.dataset.documentIndex;
    el.documentsList.replaceChildren();
    documents.forEach((doc, index) => {
      const li = document.createElement("li"), button = document.createElement("button");
      button.type = "button";
      button.dataset.documentIndex = index;
      button.className = "document-choice";
      button.textContent = doc.title;
      button.setAttribute("aria-current", String(index === selectedDocument));
      li.appendChild(button);
      el.documentsList.appendChild(li);
    });
    if (focusedIndex !== undefined) $(`[data-document-index="${focusedIndex}"]`, el.documentsList)?.focus({ preventScroll: true });
    const selected = documents[selectedDocument];
    if (selected === previewedDocument) return;
    previewedDocument = selected || null;
    $("#document-title").textContent = selected?.title || "";
    $("#document-print").setAttribute("aria-label", selected ? `Print ${selected.title}` : "Print document");
    if (selected) LifelineContent.renderMarkdown(el.documentContent, selected.markdown);
    else el.documentContent.replaceChildren();
    $("#document-preview").scrollTop = 0;
  }

  function syncDocumentMode() {
    const modal = documentViewerOpen && documentsMedia.matches;
    if (modal && !documentModalActive) {
      documentViewerOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    } else if (!modal && documentModalActive) document.body.style.overflow = documentViewerOverflow;
    documentModalActive = modal;
    $("#conversation-pane").inert = modal;
    $(".topbar").inert = modal;
    $(".devpanel").inert = modal;
    $("#documents-backdrop").hidden = !modal && !(documentViewerClosing && documentsMedia.matches);
    el.documents.setAttribute("role", modal ? "dialog" : "region");
    if (modal) {
      el.documents.setAttribute("aria-modal", "true");
      if (!el.documents.contains(document.activeElement)) $("#documents-close").focus();
    } else el.documents.removeAttribute("aria-modal");
  }

  function openDocumentViewer(index = selectedDocument, focus = true) {
    if (!documentViewerOpen) documentViewerFocus = focus ? document.activeElement : null;
    clearTimeout(documentCloseTimer);
    documentViewerClosing = false;
    documentViewerOpen = true;
    selectedDocument = index;
    renderDocuments();
    el.documents.hidden = false;
    el.documents.inert = false;
    el.documents.removeAttribute("aria-hidden");
    $("#documents-backdrop").hidden = !documentsMedia.matches;
    // Establish the closed position before changing the transition target.
    void $("#session-layout").offsetWidth;
    $("#session-layout").classList.add("documents-open");
    el.documentsToggle.setAttribute("aria-expanded", "true");
    syncDocumentMode();
    if (focus) $("#documents-close").focus();
  }

  function finishDocumentClose() {
    if (documentViewerOpen) return;
    clearTimeout(documentCloseTimer);
    documentViewerClosing = false;
    el.documents.hidden = true;
    $("#documents-backdrop").hidden = true;
  }

  function closeDocumentViewer(restoreFocus = true, immediate = false) {
    if (el.documents.hidden) return;
    const hadFocus = el.documents.contains(document.activeElement);
    documentViewerOpen = false;
    documentViewerClosing = !immediate && !matchMedia("(prefers-reduced-motion: reduce)").matches;
    documentViewerDismissed = true;
    $("#session-layout").classList.remove("documents-open");
    el.documentsToggle.setAttribute("aria-expanded", "false");
    syncDocumentMode();
    if (restoreFocus && hadFocus) (documentViewerFocus?.isConnected ? documentViewerFocus : el.documentsToggle).focus();
    else if (hadFocus) document.activeElement.blur();
    el.documents.inert = true;
    el.documents.setAttribute("aria-hidden", "true");
    clearTimeout(documentCloseTimer);
    if (documentViewerClosing) documentCloseTimer = setTimeout(finishDocumentClose, 220);
    else finishDocumentClose();
  }

  el.documentsToggle.addEventListener("click", () => documentViewerOpen ? closeDocumentViewer() : openDocumentViewer());
  const finishTransition = (event) => {
    if (documentViewerClosing && (event.target === el.documents || event.target === $("#session-layout"))) finishDocumentClose();
  };
  el.documents.addEventListener("transitionend", finishTransition);
  $("#session-layout").addEventListener("transitionend", finishTransition);
  $("#documents-close").addEventListener("click", () => closeDocumentViewer());
  $("#documents-backdrop").addEventListener("click", () => closeDocumentViewer());
  el.documentsList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-document-index]");
    if (!button) return;
    selectedDocument = Number(button.dataset.documentIndex);
    renderDocuments();
  });
  $("#document-print").addEventListener("click", () => {
    const doc = sessionDocuments()[selectedDocument];
    if (!doc) return;
    try { LifelineContent.printArtifact(doc); } catch (error) { toast(error.message); }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && documentViewerOpen && el.voice.hidden && !$(".dlg:not([hidden])")) {
      event.preventDefault();
      closeDocumentViewer();
    }
  });
  el.documents.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !documentModalActive) return;
    const controls = $$("button, a[href], [tabindex='0']", el.documents).filter((node) => !node.disabled && !node.closest("[hidden]"));
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  documentsMedia.addEventListener?.("change", syncDocumentMode);
  const updateHeaderHeight = () => document.documentElement.style.setProperty("--topbar-height", `${$(".topbar").getBoundingClientRect().height}px`);
  if (window.ResizeObserver) new ResizeObserver(updateHeaderHeight).observe($(".topbar"));
  else window.addEventListener("resize", updateHeaderHeight);
  updateHeaderHeight();
  renderDocuments();

  function renderVoiceCanvas() {
    el.vCanvasEmpty.hidden = workspaceContent.length > 0;
    if (el.voice.hidden || !workspaceContent.length) { el.vCanvas.replaceChildren(); return; }
    // Append new content without disturbing an open document, player or keyboard focus.
    for (const item of workspaceContent.slice(el.vCanvas.children.length)) {
      if (item.artifact) {
        const card = LifelineContent.artifactCard(item.artifact, toast);
        $("details", card).open = true;
        el.vCanvas.appendChild(card);
      }
      if (item.embed) el.vCanvas.appendChild(LifelineContent.embedCard(item.embed));
    }
  }

  function renderVoiceFollowUp() {
    el.vFollowUp.replaceChildren();
    el.vFollowUp.hidden = !currentFollowUp;
    if (!currentFollowUp) return;
    const question = document.createElement("p");
    question.textContent = currentFollowUp.question;
    el.vFollowUp.appendChild(question);
    const row = document.createElement("div");
    row.className = "suggestions";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", currentFollowUp.question);
    for (const option of currentFollowUp.options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggestion";
      button.textContent = option;
      button.addEventListener("click", () => sendVoiceReply(option));
      row.appendChild(button);
    }
    el.vFollowUp.appendChild(row);
  }

  function handleMemoryTool(name, result) {
    if (!result.ok) { toast(result.error || "This action could not be completed"); return; }
    if (!["save_memory", "forget_memory", "list_memories"].includes(name)) return;
    toast(name === "save_memory" ? "Saved to memory" : name === "forget_memory" ? "Memory removed" : "Memories loaded");
    renderMemories();
  }

  async function streamReply(reply, signal) {
    showChat();
    const { li, body, text } = makeMsg("ai");
    const orb = $(".orb", li);
    orb.dataset.state = "speaking";

    const markdown = reply.markdown ?? reply.blocks.map((block) => block.p || block.ul?.map((item) => "- " + item).join("\n") || "").join("\n\n");
    if (reduceMotion) LifelineContent.renderMarkdown(text, markdown);
    else {
      const chunks = markdown.match(/\S+\s*|\s+/g) || [];
      let full = "";
      for (let i = 0; i < chunks.length; i++) {
        if (signal?.aborted) return;
        full += chunks[i];
        LifelineContent.renderMarkdown(text, full);
        if (i % 5 === 0) scrollDown();
        await sleep(30 + Math.random() * 35);
      }
    }
    if (signal?.aborted) return;
    for (const artifact of reply.artifacts || []) appendContent(body, { artifact });
    for (const embed of reply.embeds || []) appendContent(body, { embed });
    orb.dataset.state = "idle";

    addActions(body, text);
    if (reply.suggestions) addSuggestions(body, reply.suggestions);
    scrollDown();
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
      if (b.dataset.act === "read") {
        if (b.dataset.reading === "true") { LifelineSpeech.cancel(); return; }
        b.dataset.reading = "true";
        b.setAttribute("aria-pressed", "true");
        b.innerHTML = `${ICONS.speak}Stop reading`;
        LifelineSpeech.play(text.textContent.trim()).catch((error) => toast(error.message)).finally(() => {
          delete b.dataset.reading;
          b.setAttribute("aria-pressed", "false");
          b.innerHTML = `${ICONS.speak}Read aloud`;
        });
      }
      else {
        const on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        if (on) toast("Thanks for your feedback");
      }
    });
    body.appendChild(row);
  }

  function addSuggestions(body, list, question = "Suggested replies") {
    $$(".suggestions", el.messages).forEach((s) => s.remove());
    const row = document.createElement("div");
    row.className = "suggestions";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", question);
    for (const s of list) {
      const b = document.createElement("button");
      b.className = "suggestion";
      b.type = "button";
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
    currentFollowUp = null;
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
     Voice mode
     Browser speech recognition and readback with OpenRouter;
     a simulated conversation is available in the local demo.
     ========================================================= */
  const VOICE_COPY = {
    connecting: ["Connecting", "Allow microphone access to start talking."],
    listening: ["Listening", "Go ahead, I'm listening."],
    thinking:  ["Thinking", ""],
    speaking:  ["Speaking", ""],
    muted:     ["Microphone off", "Press Unmute when you're ready."],
  };
  let voiceRun = 0;
  let levelRAF = 0;
  let lastFocus = null;
  let liveSession = null;
  let voiceMessage = null;
  let voicePending = false;

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

  function openVoice(mock = false) {
    if (busy) { toast("Please wait for the current reply to finish"); return; }
    if (!el.voice.hidden) return;
    voiceMessage = null;
    voicePending = false;
    el.vReplySend.disabled = false;
    el.vReply.value = "";
    $("#voice-input-status").textContent = "";
    $("#voice-output-status").textContent = "";
    renderVoiceFollowUp();
    lastFocus = document.activeElement;
    el.voice.hidden = false;
    renderVoiceCanvas();
    document.body.style.overflow = "hidden";
    el.mute.setAttribute("aria-pressed", "false");
    $("span", el.mute).textContent = "Mute";
    el.endVoice.focus();
    LifelineSpeech.cancel();

    if (OpenRouter.getKey() && !mock) {
      el.vOrb.dataset.state = "listening";
      el.vStatus.textContent = "Connecting";
      el.vText.textContent = "";
      try {
        liveSession = OpenRouter.startVoice({
          onSpeechStatus(status) { $("#voice-output-status").textContent = status; },
          onSpeechError(message) { $("#voice-output-status").textContent = message; },
          onInputMode(mode, reason) {
            $("#voice-input-status").textContent = mode === "audio"
              ? (reason ? "Browser speech is unavailable. Using microphone audio instead." : "Using microphone audio.")
              : "Using browser speech recognition.";
          },
          onState(s) {
            voicePending = s === "thinking" || s === "connecting";
            el.vReplySend.disabled = voicePending;
            $$("button", el.vFollowUp).forEach((button) => { button.disabled = voicePending; });
            if (el.mute.getAttribute("aria-pressed") === "true" && s === "listening") { setVoiceState("muted"); return; }
            if (s === "connecting") { setVoiceState("connecting"); return; }
            if (s === "muted") { setVoiceState("muted"); return; }
            if (s === "speaking") { el.vOrb.dataset.state = "speaking"; el.vStatus.textContent = "Speaking"; }
            else if (s === "listening") { el.vOrb.dataset.state = "listening"; el.vStatus.textContent = "Listening"; }
            else if (s === "thinking") { el.vOrb.dataset.state = "thinking"; el.vStatus.textContent = "Thinking"; }
          },
          onLevel(value) { setLevel(value); },
          onUserText(t) { el.vText.textContent = "“" + t + "”"; },
          onUserTurn(t) {
            currentFollowUp = null;
            renderVoiceFollowUp();
            $$(".suggestions", el.messages).forEach((row) => row.remove());
            voiceMessage = null;
            addUserMessage(t);
          },
          onModelText(t) {
            el.vText.textContent = t;
            if (!voiceMessage) voiceMessage = makeMsg("ai");
            LifelineContent.renderMarkdown(voiceMessage.text, t);
          },
          onHistory(history) { conversationHistory = history; conversationModel = OpenRouter.getVoiceModel(); },
          onTurnDone(user, model, followUp) {
            voicePending = false;
            el.vReplySend.disabled = false;
            if (model) {
              const message = voiceMessage || makeMsg("ai");
              LifelineContent.renderMarkdown(message.text, model);
              addActions(message.body, message.text);
              if (followUp) addSuggestions(message.body, followUp.options, followUp.question);
            }
            currentFollowUp = followUp || null;
            renderVoiceFollowUp();
            if (user || model) el.vText.textContent = (user ? `You: “${user}”\n` : "") + (model || "");
          },
          onTool(name, result) {
            if (result.ok && (result.artifact || result.embed)) {
              showChat();
              if (!voiceMessage) voiceMessage = makeMsg("ai");
              appendContent(voiceMessage.body, result);
            } else if (result.ok && result.followUp) {
              currentFollowUp = result.followUp;
              renderVoiceFollowUp();
              $$("button", el.vFollowUp).forEach((button) => { button.disabled = true; });
            } else handleMemoryTool(name, result);
          },
          onClose(msg) { closeVoice(); if (msg) toast(msg); },
        }, historyForModel(OpenRouter.getVoiceModel()));
      } catch (err) { closeVoice(); toast(err.message); }
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
    LifelineSpeech.cancel();
    voicePending = false;
    setLevel(0);
    el.voice.hidden = true;
    el.vCanvas.replaceChildren();
    document.body.style.overflow = "";
    lastFocus?.focus?.();
  }

  function sendVoiceReply(text) {
    text = text.trim();
    if (!text || voicePending) return;
    if (!liveSession) { closeVoice(); submit(text); return; }
    liveSession.sendText(text);
    el.vReply.value = "";
  }
  $("#voice-reply-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendVoiceReply(el.vReply.value);
  });

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

  $("#voice-btn").addEventListener("click", () => openVoice());
  $("#voice-cta").addEventListener("click", () => openVoice());
  el.endVoice.addEventListener("click", closeVoice);
  el.mute.addEventListener("click", () => {
    const muted = el.mute.getAttribute("aria-pressed") !== "true";
    el.mute.setAttribute("aria-pressed", String(muted));
    $("span", el.mute).textContent = muted ? "Unmute" : "Mute";
    liveSession?.setMuted(muted);
    if (liveSession) {
      el.vOrb.dataset.state = voicePending ? "thinking" : muted ? "muted" : "listening";
      el.vStatus.textContent = voicePending ? "Thinking" : muted ? "Microphone off" : "Listening";
      if (muted) el.vText.textContent = "Press Unmute when you're ready.";
    } else {
      voiceRun++;
      setVoiceState(muted ? "muted" : "listening");
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !el.voice.hidden) closeVoice(); });
  el.voice.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const f = $$("button, input, select, summary, a[href], iframe, [tabindex='0']", el.voice)
      .filter((node) => !node.disabled && !node.closest("[hidden]") &&
        (!node.closest("details:not([open])") || node.tagName === "SUMMARY"));
    const first = f[0], last = f[f.length - 1];
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
      if (el.voice.hidden) openVoice(true);
      voiceRun++;
      setVoiceState(voiceMap[a], a === "v-speak" ? "Here's what I found for you." : undefined);
      return;
    }
    switch (a) {
      case "user": addUserMessage(SAMPLES[Math.floor(Math.random() * SAMPLES.length)]); break;
      case "reply": respond("", DEFAULT_REPLY); break;
      case "thinking": startThinking(); break;
      case "stop": stopThinking(); break;
      case "long": respond("", LONG_REPLY); break;
      case "markdown": respond("", MARKDOWN_REPLY); break;
      case "artifact": respond("", ARTIFACT_REPLY); break;
      case "embed": respond("", EMBED_REPLY); break;
      case "error": stopThinking(); addError(); break;
      case "clear": showHome(); break;
      case "v-auto": el.voice.hidden ? openVoice(true) : runVoiceDemo(); break;
    }
  });

  /* ---------- Settings & memories dialogs ---------- */
  const settingsDlg = $("#settings-dlg"), memoriesDlg = $("#memories-dlg");
  const keyInput = $("#key-input"), modelInput = $("#model-input");

  function openDlg(d) { d.hidden = false; }
  function closeDlg(d) {
    d.hidden = true;
    if (d === settingsDlg) { LifelineSpeech.cancel(); $("#speech-test-status").textContent = ""; }
  }

  $("#settings-btn").addEventListener("click", () => {
    keyInput.value = OpenRouter.getKey();
    modelInput.value = OpenRouter.getModel();
    $("#voice-model-input").value = localStorage.getItem("openrouter-voice-model") || "";
    $("#voice-input-mode").value = OpenRouter.getVoiceInput();
    $("#transcription-model-input").value = localStorage.getItem("openrouter-transcription-model") || "";
    const speech = LifelineSpeech.getSettings();
    $("#speech-output").value = speech.output;
    $("#tts-model-input").value = speech.model;
    $("#tts-voice-input").value = speech.voice;
    openDlg(settingsDlg);
    keyInput.focus();
  });
  $("#key-save").addEventListener("click", () => {
    const k = keyInput.value.trim(), model = modelInput.value.trim();
    if (!el.voice.hidden) closeVoice();
    if (model !== OpenRouter.getModel()) conversationHistory = conversationHistory.map(({ reasoning_details, ...message }) => message);
    OpenRouter.setKey(k);
    OpenRouter.setModel(model);
    OpenRouter.setVoiceModel($("#voice-model-input").value);
    OpenRouter.setVoiceInput($("#voice-input-mode").value);
    OpenRouter.setTranscriptionModel($("#transcription-model-input").value);
    LifelineSpeech.setSettings({ output: $("#speech-output").value, model: $("#tts-model-input").value, voice: $("#tts-voice-input").value });
    toast("OpenRouter settings saved in this browser");
    closeDlg(settingsDlg);
  });
  $("#test-voice-btn").addEventListener("click", async () => {
    const status = $("#speech-test-status"), button = $("#test-voice-btn");
    button.disabled = true;
    try {
      const complete = await LifelineSpeech.play("Hello. I'm Lifeline, and my spoken replies are ready.", {
        key: keyInput.value.trim(), output: $("#speech-output").value,
        model: $("#tts-model-input").value.trim() || LifelineSpeech.getSettings().model,
        voice: $("#tts-voice-input").value.trim() || LifelineSpeech.getSettings().voice,
        onStatus: (message) => { status.textContent = message; },
      });
      if (complete) status.textContent = "Voice test complete.";
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  $("#memories-btn").addEventListener("click", () => { renderMemories(); openDlg(memoriesDlg); });
  modelInput.setAttribute("list", "openrouter-models");
  $("#load-models").addEventListener("click", async () => {
    const button = $("#load-models"), status = $("#models-status");
    button.disabled = true;
    status.textContent = "Loading models…";
    try {
      const models = await OpenRouter.listModels();
      const list = $("#openrouter-models");
      list.replaceChildren();
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.id;
        option.label = model.name + (model.architecture.input_modalities?.includes("audio") ? " — audio input + tools" : " — tools");
        list.appendChild(option);
      }
      status.textContent = `${models.length} models with tools loaded. Choose or enter a model ID above.`;
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  $$("[data-close]").forEach((b) => b.addEventListener("click", () => closeDlg(b.closest(".dlg"))));
  [settingsDlg, memoriesDlg].forEach((d) =>
    d.addEventListener("click", (e) => { if (e.target === d) closeDlg(d); }));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") [settingsDlg, memoriesDlg].forEach(closeDlg);
  });

  function renderMemories() {
    const mems = OpenRouter.getMemories();
    const list = $("#mem-list");
    list.innerHTML = "";
    $("#mem-empty").style.display = mems.length ? "none" : "";
    for (const m of mems) {
      const li = document.createElement("li");
      li.innerHTML = `<span></span><button class="chip" aria-label="Delete this memory">Delete</button>`;
      li.querySelector("span").textContent = m.text;
      li.querySelector("button").addEventListener("click", () => {
        OpenRouter.setMemories(OpenRouter.getMemories().filter((x) => x.id !== m.id));
        renderMemories();
      });
      list.appendChild(li);
    }
  }
  $("#mem-clear").addEventListener("click", () => { OpenRouter.setMemories([]); renderMemories(); });
  document.addEventListener("memories-changed", renderMemories);
})();
