/* Safe Markdown and structured content shared by chat, voice and demos. */
const LifelineContent = (() => {
  "use strict";

  function safeUrl(value, httpsOnly = false) {
    if (typeof value !== "string" || /[\u0000-\u0020\u007f]/.test(value)) return null;
    try {
      const url = new URL(value);
      if (url.username || url.password) return null;
      if (!(httpsOnly ? ["https:"] : ["https:", "http:", "mailto:"]).includes(url.protocol)) return null;
      return url.href;
    } catch { return null; }
  }

  function renderMarkdown(node, markdown) {
    node.classList.add("markdown-content");
    const html = marked.parse(String(markdown || ""), { gfm: true, breaks: true, async: false });
    node.innerHTML = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "blockquote", "pre", "code", "hr", "a", "table", "thead", "tbody", "tr", "th", "td"],
      ALLOWED_ATTR: ["href", "title", "start"],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
    });
    for (const link of node.querySelectorAll("a")) {
      const url = safeUrl(link.getAttribute("href"));
      if (!url) { link.replaceWith(...link.childNodes); continue; }
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    for (const table of node.querySelectorAll("table")) {
      const wrap = document.createElement("div");
      wrap.className = "markdown-table";
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", "Table, scroll horizontally if needed");
      table.replaceWith(wrap);
      wrap.appendChild(table);
    }
    return node;
  }

  function validateArtifact(args) {
    if (!args || typeof args.title !== "string" || !args.title.trim() || args.title.length > 200 ||
        typeof args.markdown !== "string" || !args.markdown.trim() || args.markdown.length > 60000)
      return { ok: false, error: "Provide a title (up to 200 characters) and Markdown content (up to 60,000 characters)." };
    return { ok: true, artifact: { title: args.title.trim(), markdown: args.markdown.trim() } };
  }

  function validateEmbed(args) {
    const url = safeUrl(args?.url, true);
    if (!url || url.length > 2048 || typeof args?.title !== "string" || !args.title.trim() || args.title.length > 200 ||
        (args.description !== undefined && (typeof args.description !== "string" || args.description.length > 2000)))
      return { ok: false, error: "Provide a title and an absolute HTTPS URL, with an optional short description." };
    return { ok: true, embed: { title: args.title.trim(), url, description: args.description?.trim() || "" } };
  }

  function validateFollowUp(args) {
    if (typeof args?.question !== "string" || !args.question.trim() || args.question.length > 500 ||
        (args.options !== undefined && (!Array.isArray(args.options) || args.options.length > 5 ||
          args.options.some((option) => typeof option !== "string" || !option.trim() || option.length > 160))))
      return { ok: false, error: "Provide one question (up to 500 characters) and at most five short answer options (up to 160 characters each)." };
    return { ok: true, followUp: { question: args.question.trim(), options: [...new Set((args.options || []).map((option) => option.trim()))] } };
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function printArtifact(artifact) {
    const validated = validateArtifact(artifact);
    if (!validated.ok) throw new Error(validated.error);
    document.getElementById("lifeline-print")?.remove();
    const page = element("section");
    page.id = "lifeline-print";
    page.append(element("p", "print-brand", "Lifeline"), element("h1", "", validated.artifact.title));
    page.append(renderMarkdown(element("div"), validated.artifact.markdown));
    document.body.appendChild(page);
    const cleanup = () => { page.remove(); window.removeEventListener("afterprint", cleanup); };
    window.addEventListener("afterprint", cleanup);
    try { window.print(); } catch (err) { cleanup(); throw err; }
  }

  function artifactCard(artifact, onError, onOpen) {
    const result = validateArtifact(artifact);
    if (!result.ok) throw new Error(result.error);
    const card = element("article", "artifact-card");
    const header = element("div", "artifact-header");
    const heading = element("div", "artifact-heading");
    heading.append(element("span", "content-label", "Document"), element("h3", "", result.artifact.title));
    const print = element("button", "btn btn-outline artifact-print", "Print");
    print.type = "button";
    print.setAttribute("aria-label", `Print ${result.artifact.title}`);
    print.addEventListener("click", () => {
      try { printArtifact(result.artifact); } catch (err) { onError?.(err.message); }
    });
    const actions = element("div", "artifact-card-actions");
    if (onOpen) {
      const open = element("button", "btn btn-outline artifact-open", "Open in viewer");
      open.type = "button";
      open.setAttribute("aria-label", `Open ${result.artifact.title} in document viewer`);
      open.addEventListener("click", onOpen);
      actions.append(open);
    }
    actions.append(print);
    header.append(heading, actions);
    const details = element("details", "artifact-details");
    details.append(element("summary", "", "View document"), renderMarkdown(element("div", "artifact-content"), result.artifact.markdown));
    card.append(header, details);
    return card;
  }

  // Only recognised video providers receive an iframe, and only after a click.
  function videoUrl(value) {
    const safe = safeUrl(value, true);
    if (!safe) return null;
    const url = new URL(safe);
    let id;
    if (["www.youtube.com", "youtube.com", "m.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"].includes(url.hostname)) {
      if (url.pathname === "/watch") id = url.searchParams.get("v");
      else id = url.pathname.match(/^\/(?:embed|shorts)\/([\w-]+)\/?$/)?.[1];
    } else if (url.hostname === "youtu.be") id = url.pathname.slice(1);
    if (id && /^[\w-]{11}$/.test(id)) return `https://www.youtube-nocookie.com/embed/${id}`;
    if (["vimeo.com", "www.vimeo.com", "player.vimeo.com"].includes(url.hostname)) {
      const vimeoId = url.pathname.match(/^\/(?:video\/)?(\d+)\/?$/)?.[1];
      if (vimeoId) return `https://player.vimeo.com/video/${vimeoId}`;
    }
    return null;
  }

  function embedCard(embed) {
    const result = validateEmbed(embed);
    if (!result.ok) throw new Error(result.error);
    const data = result.embed;
    const video = videoUrl(data.url);
    const card = element("article", "embed-card");
    card.append(element("span", "content-label", video ? "Video" : "Resource"));
    const link = element("a", "embed-link", data.title);
    link.href = data.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.append(link, element("span", "embed-host", new URL(data.url).hostname));
    if (data.description) card.append(element("p", "embed-description", data.description));
    if (video) {
      const load = element("button", "btn btn-outline", "Load video");
      load.type = "button";
      load.addEventListener("click", () => {
        const frame = element("iframe", "embed-video");
        frame.src = video;
        frame.title = data.title;
        frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
        frame.setAttribute("allow", "fullscreen; picture-in-picture");
        frame.referrerPolicy = "no-referrer";
        frame.allowFullscreen = true;
        load.replaceWith(frame);
      }, { once: true });
      card.append(load);
    }
    return card;
  }

  return { renderMarkdown, validateArtifact, validateEmbed, validateFollowUp, artifactCard, embedCard, printArtifact, videoUrl };
})();
