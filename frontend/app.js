/* =========================================================
   Lifeline — front end
   Lifeline backend for chat, voice transcripts, and needs assessments.
   Scripted replies are available only in the explicit Test panel.
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
    stop: $("#stop-response-btn"),
    latest: $("#jump-latest-btn"),
    continueChat: $("#continue-chat-btn"),
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
    composerSuggestions: $("#composer-suggestions"),
  };
  const workspaceContent = [];
  let currentFollowUp = null;
  let selectedDocument = 0;
  let lastDocumentCount = 0;
  let voiceWorkspaceOpen = false;
  let previewedDocument = null;
  let documentViewerFocus = null;
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
    $(".text-size").style.setProperty("--size-index", Math.max(0, ["normal", "large", "xlarge"].indexOf(size)));
    localStorage.setItem("lifeline-size", size);
  }
  setTextSize(localStorage.getItem("lifeline-size") || "normal");
  $$(".ts").forEach((b) => b.addEventListener("click", () => setTextSize(b.dataset.size)));

  // Smoothly follow the pointer so the highlight feels like light inside a body.
  $$(".orb").forEach((orb) => {
    let frame = 0;
    let targetX = -10, targetY = -15;
    let lightX = targetX, lightY = targetY;
    const moveLight = () => {
      lightX += (targetX - lightX) * .14;
      lightY += (targetY - lightY) * .14;
      orb.style.setProperty("--light-x", `${lightX}%`);
      orb.style.setProperty("--light-y", `${lightY}%`);
      if (Math.abs(targetX - lightX) + Math.abs(targetY - lightY) > .08) {
        frame = requestAnimationFrame(moveLight);
      } else {
        frame = 0;
      }
    };
    orb.addEventListener("pointermove", (event) => {
      if (event.pointerType === "touch") return;
      const bounds = orb.getBoundingClientRect();
      const x = ((event.clientX - bounds.left) / bounds.width) * 100;
      const y = ((event.clientY - bounds.top) / bounds.height) * 100;
      targetX = Math.max(-20, Math.min(40, x - 47.5));
      targetY = Math.max(-35, Math.min(40, y - 47.5));
      if (!frame) frame = requestAnimationFrame(moveLight);
    });
    orb.addEventListener("pointerleave", () => {
      targetX = -10;
      targetY = -15;
      if (!frame) frame = requestAnimationFrame(moveLight);
    });
  });

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
    const shared = userMessages.filter((message) => !/summary|summari[sz]e|artifact|my information/i.test(message));
    const facts = [...new Set(shared)];
    return {
      markdown: "Here is a **summary of your information**. Open the document to review it, or select Print to print it or save it as a PDF.",
      artifacts: [{ title: "Your information summary", markdown:
        "## Information you shared\n\n" + (facts.length ? facts.map((fact) => "- " + escapeMarkdown(fact)).join("\n") : "No personal information has been provided yet.") +
        "\n\n## Details to confirm\n\nOnly information shared above is known. Other personal and policy details: **Not provided**.\n\n> This summary reflects your conversation. Confirm important policy details with your adviser." }],
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
    el.continueChat.hidden = true;
  }
  function showHome() {
    closeDocumentViewer(false, true);
    LifelineSpeech.cancel();
    if (!el.voice.hidden) closeVoice(true);
    if (busy) stopResponse();
    el.chat.hidden = true;
    el.home.hidden = false;
    el.newChat.hidden = !el.messages.children.length;
    el.continueChat.hidden = !el.messages.children.length;
    el.latest.hidden = true;
    scrollTo({ top: 0, behavior: "instant" });
    el.continueChat.hidden ? el.input.focus() : el.continueChat.focus();
  }
  function newChat() {
    closeDocumentViewer(false, true);
    if (!el.voice.hidden) closeVoice(true);
    activeResponse?.abort();
    activeResponse = null;
    activeTurn = null;
    busy = false;
    updateSend();
    stopThinking();
    backend.reset();
    currentFollowUp = null;
    workspaceContent.length = 0;
    selectedDocument = 0;
    renderDocuments();
    renderVoiceCanvas();
    renderVoiceFollowUp();
    userMessages.length = 0;
    el.messages.innerHTML = "";
    clearComposerSuggestions();
    el.input.value = "";
    autoGrow();
    followingLatest = true;
    showHome();
    el.home.style.animation = "none"; void el.home.offsetWidth; el.home.style.animation = "";
  }
  let followingLatest = true;
  const nearLatest = () => {
    const page = document.scrollingElement || document.documentElement;
    return page.scrollHeight - window.innerHeight - window.scrollY <= 96;
  };
  function updateLatest() {
    el.latest.hidden = el.chat.hidden || followingLatest || nearLatest();
  }
  function scrollDown(force = false) {
    if (el.chat.hidden || !el.voice.hidden) return;
    if (force) followingLatest = true;
    if (followingLatest) scrollTo({ top: (document.scrollingElement || document.documentElement).scrollHeight, behavior: "instant" });
    updateLatest();
  }
  window.addEventListener("scroll", () => {
    if (el.chat.hidden || !el.voice.hidden) return;
    followingLatest = nearLatest();
    updateLatest();
  }, { passive: true });
  window.addEventListener("wheel", (event) => {
    if (event.deltaY < 0 && !el.chat.hidden && el.voice.hidden &&
        !event.target.closest?.(".documents-panel, .dlg")) followingLatest = false;
  }, { passive: true });
  el.latest.addEventListener("click", () => scrollDown(true));

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

  function revealResponse(text) {
    if (reduceMotion) return;
    [...text.children].forEach((block, index) => {
      block.classList.add("response-reveal");
      block.style.setProperty("--reveal-delay", `${Math.min(index * 55, 330)}ms`);
    });
  }

  function addUserMessage(str) {
    userMessages.push(str);
    showChat();
    const { text } = makeMsg("user");
    LifelineContent.renderMarkdown(text, str);
    scrollDown(true);
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
  const backend = LifelineBackend.getSharedClient();
  let busy = false;
  let activeResponse = null;
  let activeTurn = null;
  // The latest coverage estimate shown in the chat. Save/Print act on this.
  // A document is only created when the user explicitly saves/prints — a
  // recalculation just updates this value and the inline chat card.
  let currentEstimate = null;

  function markResponseEnded(message, label) {
    if (!message) message = makeMsg("ai");
    $(".orb", message.li).dataset.state = "idle";
    const status = document.createElement("p");
    status.className = "response-status";
    status.setAttribute("role", "status");
    status.textContent = label;
    message.body.appendChild(status);
    if (message.text.textContent.trim() && !$(".msg-actions", message.body)) addActions(message.body, message.text);
    return message;
  }

  function stopResponse() {
    if (!activeResponse) return;
    const turn = activeTurn;
    activeResponse.abort();
    activeResponse = null;
    activeTurn = null;
    stopThinking();
    busy = false;
    const message = markResponseEnded(turn?.message, "Response stopped. You can ask another question.");
    updateSend();
    scrollDown();
    el.input.focus({ preventScroll: true });
  }
  el.stop.addEventListener("click", stopResponse);

  async function respond(userText, override, updates = {}) {
    if (busy) return;
    busy = true; updateSend();
    const controller = new AbortController();
    activeResponse = controller;
    activeTurn = { input: userText, override, message: null };
    const { signal } = controller;

    if (!override) {
      startThinking();
      try {
        const data = await backend.turn({ message: userText, ...updates, signal });
        if (signal.aborted) return;
        stopThinking();
        const message = makeMsg("ai");
        activeTurn.message = message;
        LifelineContent.renderMarkdown(message.text, data.assistant_message);
        revealResponse(message.text);
        renderAssessment(message.body, data);
        addActions(message.body, message.text);
        renderComposerSuggestions(suggestionsForTurn(data));
        scrollDown();
      } catch (error) {
        if (!signal.aborted) {
          stopThinking();
          addError(error.message, () => respond(userText, override, updates));
        }
      } finally {
        if (activeResponse === controller) {
          busy = false; updateSend();
          activeResponse = null;
          activeTurn = null;
        }
      }
      return;
    }

    /* ---- explicitly selected Test panel examples ---- */
    try {
      startThinking();
      await sleep(1500 + Math.random() * 1300);
      if (signal.aborted) return;
      stopThinking();
      await streamReply(override, signal);
    } catch (err) {
      if (!signal.aborted) {
        stopThinking();
        addError(err.message, () => respond(userText, override));
      }
    } finally {
      if (activeResponse === controller) {
        busy = false; updateSend();
        activeResponse = null;
        activeTurn = null;
      }
    }
  }

  function renderAssessment(body, data) {
    $$(".assessment-editor", el.messages).forEach((form) => form.remove());
    const currency = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
    // Labels for the three assumptions the customer actually sees. HLV and
    // sanity-band parameters stay in the backend response but are never
    // rendered, so they have no label here.
    const assumptionLabels = {
      income_replacement_years: "Years of income to replace",
      education_per_child: "Education allowance per child",
      final_expenses: "Final expenses",
    };
    // The backend returns every assumption for transparency, but only these three
// drive the published DIME result. HLV and sanity-band parameters belong to
// internal calculations that are not part of the customer-facing experience,
// so they are not shown.
const CUSTOMER_ASSUMPTIONS = [
  "income_replacement_years", "education_per_child", "final_expenses",
];
const assumptionLines = Object.entries(data.assessment.assumptions)
      .filter(([key]) => CUSTOMER_ASSUMPTIONS.includes(key))
      .map(([key, value]) => {
        const display = ["education_per_child", "final_expenses"].includes(key) && Number.isFinite(value)
          ? currency(value) : value;
        return `- ${escapeMarkdown(assumptionLabels[key] || key.replaceAll("_", " "))}: ${escapeMarkdown(display)}`;
      });

    if (data.assessment.status === "ready") {
      const needs = data.needs_assessment, breakdown = needs.breakdown;
      // Each line shows its own derivation ("$80,000 per year x 10 years") so
      // the customer can see exactly where every dollar came from. The amounts
      // and the detail text both come straight from the backend calculator --
      // nothing here recomputes the maths.
      const componentLine = (row) =>
        `- **${escapeMarkdown(row.label)}: ${currency(row.amount)}**` +
        (row.detail ? `\n  - ${escapeMarkdown(row.detail)}` : "");
      const lines = [
        "## Your coverage estimate", "",
        "**Estimated additional coverage**", "",
        `**${currency(needs.illustrative_gap)}**`, "",
        "A planning estimate based on your details and the assumptions shown.", "",
        "### What builds this up", "",
        ...breakdown.components.map(componentLine), "",
        `**Total needs: ${currency(breakdown.gross_need)}**`, "",
        "### What you already have", "",
        ...breakdown.offsets.map((row) => `- **${escapeMarkdown(row.label)}: −${currency(row.amount)}**`),
        "",
        `**Resources you already have: ${currency(breakdown.total_offsets)}**`, "",
        "### Assumptions used", "",
        ...assumptionLines,
        "",
        "> These are estimates, not facts. Just tell me in the chat if "
        + "anything changes — your income, dependents, debts, coverage, or "
        + "savings — and I'll recalculate. You can save or print this estimate "
        + "any time with the buttons below.",
      ];
      const markdown = lines.join("\n");
      // Remember the current estimate so Save / Print act on exactly this one.
      currentEstimate = { title: "Your coverage estimate", markdown };

      // Render the estimate INLINE in the chat. It is NOT a document: a
      // recalculation just shows the new numbers here. A document is only
      // created when the user explicitly chooses Save or Print below.
      const card = document.createElement("article");
      card.className = "artifact-card needs-assessment-card";
      LifelineContent.renderMarkdown(card, markdown);
      card.appendChild(buildEstimateActions(currentEstimate));
      body.appendChild(card);
      // Human-in-the-loop: securely send details to a licensed advisor.
      body.appendChild(buildReviewAction());
      scrollDown();
    }

    // Collection is CONVERSATION-ONLY: the backend extracts every fact from
    // what the customer says, so no data-entry form is shown while collecting.
    // The chat itself asks for anything still missing, one thing at a time.
    //
    // Once an estimate is ready we offer a small, OPTIONAL refinement for just
    // the three assumptions the estimate card references (years of income to
    // replace, education allowance, final expenses). The six profile inputs
    // are intentionally gone — those come from the conversation.
    if (data.assessment.status !== "ready") return;

    const editor = document.createElement("details");
    editor.className = "assessment-editor artifact-card artifact-details";
    editor.open = false;  // tucked away; the conversation is the main path
    const summary = document.createElement("summary");
    summary.textContent = "Adjust the assumptions (optional)";
    editor.appendChild(summary);
    const form = document.createElement("form");
    form.className = "dlg-card";
    const fields = [
      ["income_replacement_years", "Years of income to replace", "assumptions", 1],
      ["education_per_child", "Education allowance per child ($)", "assumptions", 0.01],
      ["final_expenses", "Final expenses ($)", "assumptions", 0.01],
    ];
    for (const [key, title, group, step] of fields) {
      const field = document.createElement("div");
      field.className = "assessment-field";
      const label = document.createElement("label");
      label.className = "model-label";
      label.textContent = title;
      const input = document.createElement("input");
      input.type = "number";
      input.name = key;
      input.min = "0";
      input.step = String(step);
      input.value = data.assessment[group][key] ?? "";
      label.appendChild(input);
      field.appendChild(label);
      if (data.assessment.field_help?.[key]) {
        const help = document.createElement("p");
        help.className = "dlg-note";
        help.textContent = data.assessment.field_help[key];
        field.appendChild(help);
      }
      form.appendChild(field);
    }
    const note = document.createElement("p");
    note.className = "dlg-note";
    note.textContent = "Change any of these and I'll recalculate. To change your "
      + "income, dependents, debts, coverage, or savings, just tell me in the chat.";
    form.appendChild(note);
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "btn btn-outline assessment-submit";
    button.textContent = "Recalculate";
    form.appendChild(button);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      const assumptionUpdates = {};
      for (const [key, , group] of fields) {
        const value = form.elements.namedItem(key).value;
        const previous = data.assessment[group][key];
        if (value === "" || (previous !== null && previous !== undefined && previous !== "" && Number(value) === Number(previous))) continue;
        const number = Number(value);
        if (!Number.isFinite(number) || number < 0) return;
        assumptionUpdates[key] = number;
      }
      submit("Please recalculate with these assumptions.", { assumptionUpdates });
    });
    editor.appendChild(form);
    body.appendChild(editor);
  }

  // Save / Print buttons for the inline coverage estimate. These are the ONLY
  // way a document gets created — a recalculation never creates one.
  function buildEstimateActions(estimate) {
    const row = document.createElement("div");
    row.className = "estimate-actions";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", "Estimate actions");

    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn btn-outline estimate-save";
    save.textContent = "Save to documents";
    save.addEventListener("click", () => saveCurrentEstimate(estimate, { open: true }));

    const print = document.createElement("button");
    print.type = "button";
    print.className = "btn btn-outline estimate-print";
    print.textContent = "Print / download";
    print.addEventListener("click", () => {
      // Printing also saves a copy so there's a durable record, then prints.
      saveCurrentEstimate(estimate, { open: false });
      try { LifelineContent.printArtifact(estimate); } catch (error) { toast(error.message); }
    });

    row.append(save, print);
    return row;
  }

  // Create or UPDATE the single saved coverage-estimate document. Saving the
  // same assessment again replaces the existing entry rather than adding a
  // duplicate; genuinely different documents (different titles) stay separate.
  function saveCurrentEstimate(estimate, { open = true } = {}) {
    const validated = LifelineContent.validateArtifact(estimate);
    if (!validated.ok) { toast(validated.error); return; }
    const existing = workspaceContent.findIndex(
      (item) => item.artifact && item.artifact.title === validated.artifact.title);
    if (existing >= 0) {
      workspaceContent[existing] = { artifact: validated.artifact };
      toast("Updated your saved estimate");
    } else {
      workspaceContent.push({ artifact: validated.artifact });
      toast("Saved to documents");
    }
    renderVoiceCanvas();
    renderDocuments();
    if (open && !documentsMedia.matches && el.voice.hidden) {
      const index = workspaceContent.filter((i) => i.artifact).findIndex(
        (i) => i.artifact.title === validated.artifact.title);
      openDocumentViewer(index < 0 ? 0 : index, false);
    }
  }

  // "Send to an advisor" — securely stores the collected assessment and
  // notifies a licensed advisor for a precise, reviewed estimate. The details
  // are already saved server-side; this flags them for human review and shows
  // the customer a reference number.
  function buildReviewAction() {
    const wrap = document.createElement("div");
    wrap.className = "review-action artifact-card";

    const blurb = document.createElement("p");
    blurb.className = "review-blurb";
    blurb.textContent = "Want a precise figure? Send your details securely to a "
      + "licensed advisor for review. Your information is stored securely, and "
      + "you'll get a reference number to quote.";
    wrap.appendChild(blurb);

    const row = document.createElement("div");
    row.className = "review-row";
    const email = document.createElement("input");
    email.type = "email";
    email.placeholder = "Email for the advisor to reach you (optional)";
    email.className = "review-email";
    email.autocomplete = "email";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary review-submit";
    btn.textContent = "Send to an advisor for review";
    row.appendChild(email);
    row.appendChild(btn);
    wrap.appendChild(row);

    const result = document.createElement("p");
    result.className = "review-result";
    result.hidden = true;
    wrap.appendChild(result);

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const original = btn.textContent;
      btn.textContent = "Sending…";
      try {
        const data = await backend.submitForReview({ contact: email.value.trim() });
        result.hidden = false;
        result.textContent = `${data.message} (Reference: ${data.reference})`;
        row.hidden = true;
        toast("Sent to an advisor for review");
      } catch (error) {
        btn.disabled = false;
        btn.textContent = original;
        toast(error.message || "Could not send for review. Please try again.");
      }
    });

    return wrap;
  }

  // Calculator UI can submit collected fields without owning chat transport.
  document.addEventListener("lifeline:assessment-update", (event) => {
    const { message = "Update my estimate.", profile_updates, assumption_updates } = event.detail || {};
    if (typeof message !== "string") return;
    submit(message, { profileUpdates: profile_updates, assumptionUpdates: assumption_updates });
  });

  function addError(msg, onRetry) {
    showChat();
    const { li, body, text } = makeMsg("ai");
    li.classList.add("msg-error");
    $(".orb", li).dataset.state = "error";
    const p = document.createElement("p");
    p.textContent = msg || "Sorry, I'm having trouble connecting right now. Please try again in a moment — your message hasn't been lost.";
    text.appendChild(p);
    if (msg) {
      const retry = document.createElement("p");
      retry.textContent = "Your message is still here. Retry when the service is available.";
      text.appendChild(retry);
    }
    if (onRetry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-outline retry-response";
      button.textContent = "Retry response";
      button.disabled = busy;
      button.addEventListener("click", () => {
        if (busy) return;
        li.remove();
        showChat();
        scrollDown(true);
        onRetry();
      });
      body.appendChild(button);
    }
    scrollDown();
  }

  function appendContent(body, result) {
    if (result.artifact) {
      const index = sessionDocuments().length;
      body.appendChild(LifelineContent.artifactCard(result.artifact, toast, () => openDocumentViewer(index)));
    }
    if (result.embed) body.appendChild(LifelineContent.embedCard(result.embed));
    if (result.artifact || result.embed) {
      workspaceContent.push(result.artifact ? { artifact: result.artifact } : { embed: result.embed });
      renderVoiceCanvas();
      renderDocuments();
    }
    scrollDown();
  }

  /* ---------- Session document viewer ---------- */
  function sessionDocuments() { return workspaceContent.filter((item) => item.artifact).map((item) => item.artifact); }

  function renderDocuments() {
    const documents = sessionDocuments();
    const badge = $("#documents-count");
    badge.textContent = documents.length;
    badge.hidden = documents.length === 0;
    if (documents.length > lastDocumentCount) {
      badge.classList.remove("badge-pop");
      void badge.offsetWidth;
      badge.classList.add("badge-pop");
    }
    lastDocumentCount = documents.length;
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
      const title = document.createElement("span");
      title.className = "document-choice-title";
      title.textContent = doc.title;
      button.append(LifelineContent.documentThumbnail(), title);
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
    el.documentsToggle.inert = modal;
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
    $(".voice-canvas").hidden = !voiceWorkspaceOpen;
    const count = $("#voice-documents-count");
    count.textContent = workspaceContent.length;
    count.hidden = !workspaceContent.length;
    $("#voice-documents-btn").setAttribute("aria-label", `Open documents and resources (${workspaceContent.length})`);
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

  function setVoiceWorkspace(open) {
    voiceWorkspaceOpen = open;
    $("#voice-documents-btn").setAttribute("aria-expanded", String(open));
    $("#voice-workspace-backdrop").hidden = !open;
    el.voice.classList.toggle("workspace-open", open);
    $(".voice-inner").inert = open;
    $("#voice-documents-btn").inert = open;
    renderVoiceCanvas();
    (open ? $("#voice-workspace-close") : $("#voice-documents-btn")).focus({ preventScroll: true });
  }
  $("#voice-documents-btn").addEventListener("click", () => setVoiceWorkspace(!voiceWorkspaceOpen));
  $("#voice-workspace-close").addEventListener("click", () => setVoiceWorkspace(false));
  $("#voice-workspace-backdrop").addEventListener("click", () => setVoiceWorkspace(false));

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

  async function streamReply(reply, signal) {
    showChat();
    const { li, body, text } = makeMsg("ai");
    if (activeResponse?.signal === signal) activeTurn.message = { li, body, text };
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

  /* ---------- Contextual composer suggestions ----------
     A short row of quick replies sits above the composer and updates after
     every turn based on the backend's own state (status + which field it is
     asking about next). The buttons never invent a dollar amount — they offer
     intents like "I'm not sure" or "No savings set aside" and let the backend
     extractor interpret them. Selecting one goes through submit(), the exact
     same path as typing, so there is one pipeline for all input. */

  // Per-field quick answers for the question currently being asked. Phrasing is
  // plain-language and amount-free; the backend binds it to the asked field.
  const FIELD_SUGGESTIONS = {
    annual_income: ["I'm not sure", "Why do you need this?"],
    num_children: ["No dependents", "Just my partner", "Why does this matter?"],
    mortgage_balance: ["No mortgage", "I'm not sure", "Why do you need this?"],
    non_mortgage_debt: ["No other debts", "I'm not sure"],
    existing_coverage: ["No coverage yet", "Only through work", "Why does this matter?"],
    liquid_savings: ["No savings set aside", "I'm not sure", "Why does this matter?"],
  };

  // Decide the suggestion row for a turn, straight from the response contract.
  function suggestionsForTurn(data) {
    const assessment = data?.assessment || {};
    const status = assessment.status;

    if (status === "collecting") {
      const field = assessment.next_field;
      const perField = FIELD_SUGGESTIONS[field] || [];
      // Lead with the field-specific answers, then a general escape hatch.
      return dedupeSuggestions([...perField, "Talk to a real person"]);
    }

    if (status === "ready") {
      // An estimate is on screen: offer ways to understand or refine it,
      // never a fabricated number.
      return dedupeSuggestions([
        "Explain this estimate",
        "Change an assumption",
        "Something has changed",
      ]);
    }

    // Idle / general question with no assessment under way.
    return dedupeSuggestions([
      "Estimate my coverage",
      "How does life insurance work?",
      "Talk to a real person",
    ]);
  }

  function dedupeSuggestions(list) {
    return [...new Set(list.filter(Boolean))].slice(0, 4);
  }

  // Render (or clear) the contextual row above the composer.
  function renderComposerSuggestions(list) {
    const row = el.composerSuggestions;
    row.replaceChildren();
    if (!list || !list.length || el.chat.hidden) { row.hidden = true; return; }
    for (const text of list) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggestion";
      button.textContent = text;
      button.addEventListener("click", () => submit(text));
      row.appendChild(button);
    }
    row.hidden = false;
  }

  function clearComposerSuggestions() {
    el.composerSuggestions.replaceChildren();
    el.composerSuggestions.hidden = true;
  }

  /* ---------- Composer ---------- */
  function updateSend() {
    el.form.classList.toggle("is-ready", !busy && !!el.input.value.trim());
    el.send.disabled = busy || !el.input.value.trim();
    el.send.hidden = busy;
    el.stop.hidden = !busy;
    $$(".retry-response").forEach((button) => { button.disabled = busy; });
    $$(".assessment-submit").forEach((button) => { button.disabled = busy; });
  }
  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
    updateSend();
  }
  function submit(str, updates) {
    str = (str ?? el.input.value).trim();
    if (!str || busy) return;
    currentFollowUp = null;
    $$(".suggestions", el.messages).forEach((s) => s.remove());
    clearComposerSuggestions();
    addUserMessage(str);
    el.input.value = "";
    autoGrow();
    respond(str, undefined, updates);
  }

  el.input.addEventListener("input", autoGrow);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  el.form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  $$(".topic").forEach((t) => t.addEventListener("click", () => submit(t.dataset.prompt)));
  el.newChat.addEventListener("click", newChat);
  el.brand.addEventListener("click", (e) => { e.preventDefault(); showHome(); });
  el.continueChat.addEventListener("click", () => {
    showChat();
    scrollDown(true);
    el.input.focus({ preventScroll: true });
  });

  /* ---------- Voice uses browser speech and the same backend session ---------- */
  let recognition = null;
  let voiceController = null;
  let voicePending = false;
  let voiceRun = 0;
  let lastFocus = null;
  let voiceClosing = false;
  let voiceCloseTimer = null;
  let voiceMeterController = null;
  let voiceInputFailed = false;
  let voiceReturnTarget = null;
  function stopVoiceMeter() {
    voiceMeterController?.abort();
    voiceMeterController = null;
    el.vOrb.style.setProperty("--level", "0");
  }
  function startVoiceMeter() {
    stopVoiceMeter();
    if (reduceMotion || voiceClosing || el.voice.hidden || voiceInputFailed || el.mute.getAttribute("aria-pressed") === "true") return;
    const controller = new AbortController();
    voiceMeterController = controller;
    LifelineSpeech.startMeter((level) => {
      if (voiceMeterController !== controller) return;
      el.vOrb.style.setProperty("--level", el.vOrb.dataset.state === "listening" ? String(level) : "0");
    }, controller.signal).catch(() => {
      // Speech recognition remains usable if separate volume monitoring fails.
      if (voiceMeterController === controller) stopVoiceMeter();
    });
  }
  function setVoiceState(state, text) {
    el.vOrb.dataset.state = state;
    el.vStatus.textContent = { listening: "Listening", thinking: "Thinking", speaking: "Speaking", muted: "Microphone off", error: "Voice unavailable" }[state];
    el.vText.textContent = text || "";
    if (state !== "listening") el.vOrb.style.setProperty("--level", "0");
  }
  function finishVoiceClose() {
    clearTimeout(voiceCloseTimer);
    el.voice.hidden = true;
    el.voice.classList.remove("is-opening", "is-closing");
    voiceWorkspaceOpen = false;
    $("#voice-workspace").hidden = true;
    $("#voice-workspace-backdrop").hidden = true;
    $("#voice-documents-btn").setAttribute("aria-expanded", "false");
    el.voice.classList.remove("workspace-open");
    $(".voice-inner").inert = false;
    $("#voice-documents-btn").inert = false;
    el.vCanvas.replaceChildren();
    document.body.style.overflow = "";
    voiceClosing = false;
    const focus = lastFocus?.isConnected && !lastFocus.closest("[hidden]") ? lastFocus : $("#voice-btn");
    focus?.focus({ preventScroll: true });
  }
  function closeVoice(immediate = false) {
    // Click events are not requests for an immediate close.
    immediate = immediate === true;
    if (el.voice.hidden) return;
    if (voiceClosing) { if (immediate) finishVoiceClose(); return; }
    voiceClosing = true;
    voiceRun++;
    stopVoiceMeter();
    voiceController?.abort();
    voiceController = null;
    recognition?.abort();
    recognition = null;
    // Gemini Live owns its own mic and playback; tear it down too.
    if (window.LifelineVoice?.isActive()) LifelineVoice.stop();
    LifelineSpeech.cancel();
    voicePending = false;
    el.voice.classList.remove("is-opening");
    if (immediate || reduceMotion) { finishVoiceClose(); return; }
    const target = voiceReturnTarget?.isConnected && !voiceReturnTarget.closest("[hidden]") ? voiceReturnTarget : $("#voice-btn");
    animateVoiceOpening(target.getBoundingClientRect(), true);
    voiceCloseTimer = setTimeout(finishVoiceClose, 800);
  }
  function listen() {
    if (el.voice.hidden || voiceClosing || voiceInputFailed || voicePending || el.mute.getAttribute("aria-pressed") === "true") return;
    try { recognition?.start(); setVoiceState("listening", "Go ahead, I'm listening."); }
    catch (error) {
      voiceInputFailed = true;
      stopVoiceMeter();
      $("#retry-voice-btn").hidden = false;
      setVoiceState("error", "Microphone unavailable. Type your reply below.");
      $("#voice-input-status").textContent = error.message;
    }
  }
  async function voiceTurn(text) {
    text = text.trim();
    if (!text || voicePending || voiceClosing || el.voice.hidden) return;
    const run = voiceRun;
    voicePending = true;
    el.vReplySend.disabled = true;
    recognition?.abort();
    voiceController = new AbortController();
    el.vReply.value = "";
    addUserMessage(text);
    setVoiceState("thinking", `You: “${text}”`);
    try {
      const data = await backend.turn({ message: text, signal: voiceController.signal });
      if (run !== voiceRun || el.voice.hidden) return;
      const message = makeMsg("ai");
      LifelineContent.renderMarkdown(message.text, data.assistant_message);
      revealResponse(message.text);
      renderAssessment(message.body, data);
      addActions(message.body, message.text);
      renderComposerSuggestions(suggestionsForTurn(data));
      renderVoiceCanvas();
      setVoiceState("speaking", data.assistant_message);
      await LifelineSpeech.play(data.assistant_message);
    } catch (error) {
      if (error.name !== "AbortError" && run === voiceRun) {
        setVoiceState("error", error.message);
        $("#voice-input-status").textContent = error.message;
      }
    } finally {
      if (run === voiceRun) { voicePending = false; el.vReplySend.disabled = false; listen(); }
    }
  }
  function animateVoiceOpening(source, closing = false) {
    if (reduceMotion) return;
    const target = el.vOrb.getBoundingClientRect();
    const x = source.left + source.width / 2;
    const y = source.top + source.height / 2;
    const reach = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    el.voice.style.setProperty("--voice-origin-x", `${x}px`);
    el.voice.style.setProperty("--voice-origin-y", `${y}px`);
    el.voice.style.setProperty("--voice-start-radius", `${source.width / 2}px`);
    el.voice.style.setProperty("--voice-reveal-radius", `${reach + 24}px`);
    el.voice.style.setProperty("--orb-enter-x", `${x - target.left - target.width / 2}px`);
    el.voice.style.setProperty("--orb-enter-y", `${y - target.top - target.height / 2}px`);
    el.voice.style.setProperty("--orb-enter-scale", String(source.width / target.width));
    // Measure the final layout before moving its orb back to the clicked position.
    void el.voice.offsetWidth;
    el.voice.classList.add(closing ? "is-closing" : "is-opening");
  }
  /* ---------- Gemini Live voice, falling back to browser speech ----------
     Both transports use the SAME backend session, so a user can answer by
     voice and then keep going in the text chat without losing the assessment. */
  async function startGeminiVoice() {
    if (!window.LifelineVoice) return false;
    const run = voiceRun;
    try {
      await LifelineVoice.start({
        onState: (state) => {
          if (run !== voiceRun || voiceClosing || el.voice.hidden) return;
          if (state === "connecting") setVoiceState("listening", "Connecting to the Lifeline voice service...");
          else if (state === "listening") setVoiceState("listening", "Go ahead, I'm listening.");
        },
        onEvent: (event) => {
          if (run !== voiceRun || voiceClosing || el.voice.hidden) return;
          if (event.type === "assistant-speech" && event.text) {
            setVoiceState("speaking", event.text);
          } else if (event.type === "error") {
            setVoiceState("error", event.message);
          }
        },
      });
      if (run !== voiceRun || voiceClosing || el.voice.hidden) {
        await LifelineVoice.stop();
        return false;
      }
      if (el.mute.getAttribute("aria-pressed") === "true") LifelineVoice.setMuted(true);
      return true;
    } catch (error) {
      if (run !== voiceRun || voiceClosing || el.voice.hidden) return false;
      $("#voice-input-status").textContent = error.message;
      setVoiceState("error", "Voice service unavailable. Falling back to browser speech.");
      return false;
    }
  }

  function openVoice(event) {
    if (!el.voice.hidden) return;
    const trigger = event?.currentTarget;
    const sourceOrb = trigger?.querySelector(".orb") || (!el.home.hidden ? el.heroOrb : trigger) || el.vOrb;
    const source = sourceOrb.getBoundingClientRect();
    voiceReturnTarget = sourceOrb;
    voiceInputFailed = false;
    $("#retry-voice-btn").hidden = true;
    $("#voice-input-status").textContent = "";
    el.vReplySend.disabled = false;
    lastFocus = document.activeElement;
    el.voice.hidden = false;
    el.voice.scrollTop = 0;
    el.voice.classList.remove("is-opening");
    renderVoiceCanvas();
    animateVoiceOpening(source);
    document.body.style.overflow = "hidden";
    el.mute.setAttribute("aria-pressed", "false");
    $("span", el.mute).textContent = "Mute";
    el.endVoice.focus({ preventScroll: true });
    // Prefer Gemini Live; fall back to the browser's own speech recognition.
    const run = voiceRun;
    startGeminiVoice().then((started) => {
      if (started || run !== voiceRun || el.voice.hidden) return;
      startBrowserVoice(run);
    });
  }
  function startBrowserVoice(run) {
    if (el.voice.hidden || voiceClosing || run !== voiceRun) return;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      setVoiceState("error", "Speech recognition is unavailable here. Type your reply below.");
      return;
    }
    try { recognition = new Recognition(); }
    catch (error) {
      setVoiceState("error", "Speech recognition is unavailable here. Type your reply below.");
      return;
    }
    recognition.lang = document.documentElement.lang || "en-US";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      if (run !== voiceRun || voiceClosing || el.voice.hidden || voiceInputFailed || el.mute.getAttribute("aria-pressed") === "true") return;
      const results = Array.from(event.results);
      const transcript = results.map((result) => result[0]?.transcript || "").join(" ").trim();
      el.vText.textContent = transcript;
      if (transcript && results.every((result) => result.isFinal)) voiceTurn(transcript);
    };
    recognition.onerror = (event) => {
      if (run !== voiceRun || voiceClosing || el.voice.hidden) return;
      if (event.error !== "aborted" && event.error !== "no-speech") {
        voiceInputFailed = true;
        stopVoiceMeter();
        $("#retry-voice-btn").hidden = false;
        setVoiceState("error", "Microphone unavailable. Type your reply below.");
        $("#voice-input-status").textContent = event.error || "Speech recognition failed.";
      }
    };
    recognition.onend = () => {
      if (run === voiceRun && !voiceInputFailed && !voiceClosing && !voicePending && !el.voice.hidden && el.mute.getAttribute("aria-pressed") === "false")
        setTimeout(() => { if (run === voiceRun) listen(); }, 250);
    };
    listen();
    startVoiceMeter();
  }
  el.voice.addEventListener("animationend", (event) => {
    if (event.target === el.voice && event.animationName === "voice-expand")
      el.voice.classList.remove("is-opening");
    if (event.target === el.voice && event.animationName === "voice-contract") finishVoiceClose();
  });
  $("#voice-btn").addEventListener("click", openVoice);
  $("#hero-voice-btn").addEventListener("click", openVoice);
  $("#voice-cta").addEventListener("click", openVoice);
  window.addEventListener("pagehide", () => closeVoice(true));
  el.endVoice.addEventListener("click", closeVoice);
  $("#voice-reply-form").addEventListener("submit", (event) => { event.preventDefault(); voiceTurn(el.vReply.value); });
  el.mute.addEventListener("click", () => {
    const muted = el.mute.getAttribute("aria-pressed") !== "true";
    el.mute.setAttribute("aria-pressed", String(muted));
    $("span", el.mute).textContent = muted ? "Unmute" : "Mute";
    if (window.LifelineVoice?.isActive()) {
      LifelineVoice.setMuted(muted);
      setVoiceState(muted ? "muted" : "listening", muted ? "Press Unmute when you're ready." : "Go ahead, I'm listening.");
    } else if (muted) { stopVoiceMeter(); recognition?.abort(); setVoiceState("muted", "Press Unmute when you're ready."); }
    else { listen(); startVoiceMeter(); }
  });
  $("#retry-voice-btn").addEventListener("click", () => {
    voiceInputFailed = false;
    $("#retry-voice-btn").hidden = true;
    $("#voice-input-status").textContent = "";
    listen();
    startVoiceMeter();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || el.voice.hidden) return;
    event.preventDefault();
    if (voiceWorkspaceOpen) setVoiceWorkspace(false);
    else closeVoice();
  });
  el.voice.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = $$("button, input, select, summary, a[href], iframe, [tabindex='0']", voiceWorkspaceOpen ? $("#voice-workspace") : el.voice)
      .filter((node) => !node.disabled && !node.closest("[hidden]"));
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  /* ---------- Test panel ---------- */
  $(".devpanel").hidden = new URLSearchParams(location.search).get("dev") !== "1";
  const devToggle = $("#devpanel-toggle"), devBody = $("#devpanel-body");
  devToggle.addEventListener("click", () => {
    devBody.hidden = !devBody.hidden;
    devToggle.setAttribute("aria-expanded", String(!devBody.hidden));
  });

  const SAMPLES = ["What's the difference between term and whole life?", "How do I make a claim?", "Can I speak to a real person?"];

  devBody.addEventListener("click", async (e) => {
    const a = e.target.closest("button")?.dataset.dev;
    if (!a) return;
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
      case "clear": newChat(); break;

    }
  });

})();
