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
    if (!el.voice.hidden) closeVoice();
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
    if (!el.voice.hidden) closeVoice();
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
    documentViewerDismissed = false;
    renderDocuments();
    renderVoiceCanvas();
    renderVoiceFollowUp();
    userMessages.length = 0;
    el.messages.innerHTML = "";
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
        renderAssessment(message.body, data);
        addActions(message.body, message.text);
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
        "## Illustrative coverage gap", "",
        `**${currency(needs.illustrative_gap)}**`, "",
        escapeMarkdown(needs.disclaimer || data.disclaimer), "",
        "### What builds this up", "",
        ...breakdown.components.map(componentLine), "",
        `**Gross illustrative need: ${currency(breakdown.gross_need)}**`, "",
        "### What you already have", "",
        ...breakdown.offsets.map((row) => `- **${escapeMarkdown(row.label)}: −${currency(row.amount)}**`),
        "",
        `**Total resources: ${currency(breakdown.total_offsets)}**`, "",
        "### Assumptions behind these figures", "",
        ...assumptionLines,
        "",
        "> These are estimates, not facts. Open “Update your estimate” "
        + "below to change the years of income replaced, the education "
        + "allowance per child, or final expenses, and the backend will "
        + "recalculate.",
      ];
      const result = LifelineContent.validateArtifact({ title: "Your illustrative needs assessment", markdown: lines.join("\n") });
      if (result.ok) {
        appendContent(body, result);
        const card = body.lastElementChild;
        card.classList.add("needs-assessment-card");
        $(".artifact-details", card).open = true;
      }
    }

    // The backend also extracts facts from free text, but a customer editing
    // numbers here means exactly these values. Send only the edited fields and
    // let the backend calculator recompute -- this UI never does the maths.
    const editor = document.createElement("details");
    editor.className = "assessment-editor artifact-card artifact-details";
    editor.open = data.assessment.status === "collecting";
    const summary = document.createElement("summary");
    summary.textContent = data.assessment.status === "collecting" ? "Details for your estimate" : "Update your estimate";
    editor.appendChild(summary);
    const form = document.createElement("form");
    form.className = "dlg-card";
    const fields = [
      ["annual_income", "Annual income ($)", "profile", 0.01],
      ["num_children", "Number of children / dependents", "profile", 1],
      ["mortgage_balance", "Mortgage balance ($)", "profile", 0.01],
      ["non_mortgage_debt", "Other debts ($)", "profile", 0.01],
      ["existing_coverage", "Existing life insurance ($)", "profile", 0.01],
      ["liquid_savings", "Savings and investments ($)", "profile", 0.01],
      ["income_replacement_years", "Years of income to replace", "assumptions", 1],
      ["education_per_child", "Education allowance per child ($)", "assumptions", 0.01],
      ["final_expenses", "Final expenses ($)", "assumptions", 0.01],
    ];
    for (const [key, title, group, step] of fields) {
      const label = document.createElement("label");
      label.className = "model-label";
      label.textContent = title;
      const input = document.createElement("input");
      input.type = "number";
      input.name = key;
      input.min = "0";
      input.step = String(step);
      input.required = key === "annual_income";
      input.value = data.assessment[group][key] ?? "";
      label.appendChild(input);
      form.appendChild(label);
      if (data.assessment.field_help?.[key]) {
        const help = document.createElement("p");
        help.className = "dlg-note";
        help.textContent = data.assessment.field_help[key];
        form.appendChild(help);
      }
    }
    const note = document.createElement("p");
    note.className = "dlg-note";
    note.textContent = "Optional details left blank use the service's assumptions. Review them with your estimate.";
    form.appendChild(note);
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "btn btn-outline assessment-submit";
    button.textContent = "Calculate estimate";
    form.appendChild(button);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      const profileUpdates = {}, assumptionUpdates = {};
      for (const [key, , group] of fields) {
        const value = form.elements.namedItem(key).value;
        const previous = data.assessment[group][key];
        if (value === "" || (previous !== null && previous !== undefined && previous !== "" && Number(value) === Number(previous))) continue;
        const number = Number(value);
        if (!Number.isFinite(number) || number < 0) return;
        (group === "profile" ? profileUpdates : assumptionUpdates)[key] = number;
      }
      submit("Calculate my estimate using these details.", { profileUpdates, assumptionUpdates });
    });
    editor.appendChild(form);
    body.appendChild(editor);
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

  /* ---------- Composer ---------- */
  function updateSend() {
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
  function setVoiceState(state, text) {
    el.vOrb.dataset.state = state;
    el.vStatus.textContent = { listening: "Listening", thinking: "Thinking", speaking: "Speaking", muted: "Microphone off", error: "Voice unavailable" }[state];
    el.vText.textContent = text || "";
  }
  function closeVoice() {
    voiceRun++;
    voiceController?.abort();
    voiceController = null;
    recognition?.abort();
    recognition = null;
    // Gemini Live owns its own mic and playback; tear it down too. This
    // silences queued assistant audio, stops the mic tracks, and releases the
    // audio contexts so a new session can start cleanly.
    if (window.LifelineVoice?.isActive()) LifelineVoice.stop();
    // Reset the controls so the next session opens unmuted.
    el.mute.setAttribute("aria-pressed", "false");
    $("span", el.mute).textContent = "Mute";
    LifelineSpeech.cancel();
    voicePending = false;
    el.voice.hidden = true;
    el.vCanvas.replaceChildren();
    document.body.style.overflow = "";
    lastFocus?.focus?.();
  }
  function listen() {
    if (el.voice.hidden || voicePending || el.mute.getAttribute("aria-pressed") === "true") return;
    try { recognition?.start(); setVoiceState("listening", "Go ahead, I'm listening."); }
    catch (error) { $("#voice-input-status").textContent = error.message; }
  }
  async function voiceTurn(text) {
    text = text.trim();
    if (!text || voicePending || el.voice.hidden) return;
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
      renderAssessment(message.body, data);
      addActions(message.body, message.text);
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
  /* ---------- Gemini Live voice, falling back to browser speech ----------
     Both transports use the SAME backend session, so a user can answer by
     voice and then keep going in the text chat without losing the assessment. */
  async function startGeminiVoice() {
    if (!window.LifelineVoice) return false;
    try {
      await LifelineVoice.start({
        onState: (state) => {
          if (state === "connecting") setVoiceState("listening", "Connecting to the Lifeline voice service...");
          else if (state === "listening") setVoiceState("listening", "Go ahead, I'm listening.");
        },
        onEvent: (event) => {
          if (event.type === "assistant-speech" && event.text) {
            setVoiceState("speaking", event.text);
          } else if (event.type === "error") {
            setVoiceState("error", event.message);
          }
        },
      });
      return true;
    } catch (error) {
      $("#voice-input-status").textContent = error.message;
      setVoiceState("error", "Voice service unavailable. Falling back to browser speech.");
      return false;
    }
  }

  function openVoice() {
    if (!el.voice.hidden) return;
    lastFocus = document.activeElement;
    el.voice.hidden = false;
    renderVoiceCanvas();
    document.body.style.overflow = "hidden";
    el.mute.setAttribute("aria-pressed", "false");
    $("span", el.mute).textContent = "Mute";
    el.endVoice.focus();
    // Prefer Gemini Live; fall back to the browser's own speech recognition.
    startGeminiVoice().then((started) => {
      if (started || el.voice.hidden) return;
      startBrowserVoice();
    });
  }
  function startBrowserVoice() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      setVoiceState("error", "Speech recognition is unavailable here. Type your reply below.");
      return;
    }
    recognition = new Recognition();
    recognition.lang = document.documentElement.lang || "en-US";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      const results = Array.from(event.results);
      const transcript = results.map((result) => result[0]?.transcript || "").join(" ").trim();
      el.vText.textContent = transcript;
      if (transcript && results.every((result) => result.isFinal)) voiceTurn(transcript);
    };
    recognition.onerror = (event) => {
      if (event.error !== "aborted" && event.error !== "no-speech") {
        setVoiceState("error", "Microphone unavailable. Type your reply below.");
        $("#voice-input-status").textContent = event.error || "Speech recognition failed.";
      }
    };
    recognition.onend = () => {
      if (!voicePending && !el.voice.hidden && el.mute.getAttribute("aria-pressed") === "false")
        setTimeout(listen, 250);
    };
    listen();
  }
  $("#voice-btn").addEventListener("click", openVoice);
  $("#voice-cta").addEventListener("click", openVoice);
  el.endVoice.addEventListener("click", closeVoice);
  $("#voice-reply-form").addEventListener("submit", (event) => { event.preventDefault(); voiceTurn(el.vReply.value); });
  el.mute.addEventListener("click", () => {
    const muted = el.mute.getAttribute("aria-pressed") !== "true";
    el.mute.setAttribute("aria-pressed", String(muted));
    $("span", el.mute).textContent = muted ? "Unmute" : "Mute";
    // Gemini Live: stop/resume sending mic audio without dropping the session.
    if (window.LifelineVoice?.isActive()) LifelineVoice.setMuted(muted);
    if (muted) { recognition?.abort(); setVoiceState("muted", "Press Unmute when you're ready."); }
    else listen();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !el.voice.hidden) closeVoice(); });
  el.voice.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = $$("button, input, select, summary, a[href], iframe, [tabindex='0']", el.voice)
      .filter((node) => !node.disabled && !node.closest("[hidden]"));
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
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
