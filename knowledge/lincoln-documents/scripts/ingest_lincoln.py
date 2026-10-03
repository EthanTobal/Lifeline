#!/usr/bin/env python3
"""
LifeLine RAG ingestion -- Lincoln Financial public educational content.

Retrieves approved, PUBLIC, consumer-facing pages from lincolnfinancial.com via
the same public JSON content endpoint the site's own Angular frontend uses
(/pbl-api/getcontent), then converts them into raw JSON, cleaned Markdown, and
provenance metadata suitable for a later RAG chunking step.

Request format verified by reading Lincoln's own public frontend bundle:
    GET {base_url}/pbl-api/getcontent?vanityUrl=<path>&audience=global
That code path carries no reCAPTCHA token and no Authorization header.

Design constraints (deliberate):
  * Standard library only. No third-party packages required.
  * robots.txt is parsed and obeyed before every request.
  * Conservative fixed delay between requests.
  * Identifies itself honestly in the User-Agent header.
  * Only ever requests the exact vanityUrl values in the config file. No link
    following, no path guessing, no enumeration.
  * No LLM summarisation, paraphrasing, or invented content. The cleaned
    Markdown contains only text present in the source response.

Usage:
    python ingest_lincoln.py [--config lincoln_sources.json] [--dry-run]

Outputs, relative to the configured output_root:
    raw/<name>.json        original API response, unmodified
    cleaned/<name>.md      cleaned Markdown
    metadata/<name>.json   provenance and extraction metadata
    discovered_links.json  candidate links for human review (never followed)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import requests

# Quality thresholds. HTTP 200 alone is NOT success.
MIN_BODY_CHARACTERS = 400   # below this the page is effectively empty
MIN_HEADINGS = 1
WARN_BODY_CHARACTERS = 1200

# ---------------------------------------------------------------------------
# HTML -> tree (for converting the HTML fragments inside CMS "Copy" fields)
# ---------------------------------------------------------------------------

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

BOILERPLATE_TAGS = {
    "script", "style", "noscript", "template", "svg", "canvas", "iframe",
    "object", "embed", "form", "button", "select", "input", "textarea",
    "label", "fieldset", "legend",
}

BOILERPLATE_ATTR_PATTERN = re.compile(
    r"(cookie|consent|gdpr|banner-overlay|popup|modal|social-share|"
    r"newsletter|subscribe|skip-link|breadcrumb|pagination|back-to-top)",
    re.IGNORECASE,
)

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
INLINE_BOLD = {"strong", "b"}
INLINE_ITALIC = {"em", "i"}
INLINE_SKIP = {"script", "style", "noscript", "template", "svg", "iframe"}

# "[Asset Included(LFGComponent:123456)]" placeholders that CMS authors use to
# inline a referenced component (often a footnote source).
ASSET_PLACEHOLDER = re.compile(
    r"\[Asset Included\s*\(\s*LFGComponent\s*:\s*(\d+)\s*\)\s*\]", re.IGNORECASE)

# Media/asset placeholders carry no consumer prose; drop them entirely.
ASSET_PLACEHOLDER_ANY = re.compile(
    r"\[Asset Included\s*\([^)]*\)\s*\]", re.IGNORECASE)

# Marker left behind when an asset reference cannot be resolved from the
# source. Kept through rendering so the extractor can tell "safe to strip"
# apart from "this sentence was left incomplete".
ASSET_SENTINEL = "\ue000"

# A sentence ending in a dangling connective once an asset marker is removed,
# e.g. "Permanent Life insurance includes* and ." -> incomplete, so drop it.
DANGLING_CONNECTIVE = re.compile(
    r"\b(?:and|or|plus|as well as|including|includes|such as)\s*[\*\u2022]*\s*[.,;:]*\s*$",
    re.IGNORECASE)


class Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.text = ""

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)


class TreeBuilder(HTMLParser):
    """Builds a small DOM tree from an HTML fragment, tolerating bad markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack: list[Node] = [self.root]

    def _current(self) -> Node:
        return self._stack[-1]

    def handle_starttag(self, tag, attrs):
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        node = Node(tag, attr_map)
        self._current().children.append(node)
        if tag not in VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        self._current().children.append(Node(tag, attr_map))

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        if data.strip():
            node = Node("#text")
            node.text = data
            self._current().children.append(node)


# ---------------------------------------------------------------------------
# HTML fragment -> Markdown
# ---------------------------------------------------------------------------

def is_boilerplate(node: Node) -> bool:
    if node.tag in BOILERPLATE_TAGS:
        return True
    if node.attr("role").lower() in {"navigation", "banner", "contentinfo",
                                     "search", "dialog", "alert"}:
        return True
    if node.attr("aria-hidden", "").lower() == "true":
        return True
    marker = f"{node.attr('class')} {node.attr('id')}".strip()
    return bool(marker and BOILERPLATE_ATTR_PATTERN.search(marker))


def strip_boilerplate(node: Node) -> None:
    kept = []
    for child in node.children:
        if child.tag != "#text" and is_boilerplate(child):
            continue
        kept.append(child)
        if child.tag != "#text":
            strip_boilerplate(child)
    node.children = kept


def collect_text(node: Node) -> str:
    parts = []
    for child in node.children:
        if child.tag == "#text":
            parts.append(child.text)
        else:
            parts.append(collect_text(child))
    return " ".join(parts)


def render_inline(node: Node, base_url: str) -> str:
    parts: list[str] = []
    for child in node.children:
        if child.tag == "#text":
            parts.append(child.text)
        elif child.tag in INLINE_SKIP:
            continue
        elif child.tag == "br":
            parts.append(" ")
        elif child.tag == "a":
            label = re.sub(r"\s+", " ", render_inline(child, base_url)).strip()
            href = child.attr("href")
            if not label:
                continue
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                parts.append(label)
            else:
                parts.append(f"[{label}]({absolutize(href, base_url)})")
        elif child.tag in INLINE_BOLD or child.tag in INLINE_ITALIC:
            raw = collect_text(child)
            text = render_inline(child, base_url).strip()
            if not text:
                continue
            marker = "**" if child.tag in INLINE_BOLD else "*"
            # Preserve the original word boundaries around the emphasised run,
            # e.g. "<strong>Coverage: </strong>Lifetime" must not become
            # "**Coverage:**Lifetime".
            prefix = " " if raw[:1].isspace() else ""
            suffix = " " if raw[-1:].isspace() else ""
            parts.append(f"{prefix}{marker}{text}{marker}{suffix}")
        elif child.tag in ("sup", "sub"):
            text = render_inline(child, base_url).strip()
            if not text:
                continue
            # A footnote marker directly after a word needs separating space.
            needs_gap = bool(parts and re.search(r"[A-Za-z0-9]$", parts[-1]))
            parts.append(f"{' ' if needs_gap else ''}{text} ")
        else:
            # Generic inline container: preserve a word boundary on each side so
            # adjacent markup (e.g. "Coverage:<span>Varies</span>") does not glue.
            text = render_inline(child, base_url).strip()
            parts.append(f" {text} " if text else "")
    return re.sub(r"[ \t\r\f\v]+", " ", "".join(parts)).strip()


def block_text(node: Node, base_url: str) -> str:
    return re.sub(r"\s+", " ", render_inline(node, base_url)).strip()


def render_list(node: Node, base_url: str, depth: int = 0) -> str:
    ordered = node.tag == "ol"
    indent = "  " * depth
    lines: list[str] = []
    for child in node.children:
        if child.tag != "li":
            continue
        parts: list[str] = []
        nested: list[Node] = []
        wrapper = Node("span")
        for sub in child.children:
            if sub.tag in ("ul", "ol"):
                nested.append(sub)
            else:
                # Render the whole item in one pass so inline context (spacing
                # around <strong>, superscript markers) is preserved.
                wrapper.children.append(sub)
        text = render_inline(wrapper, base_url).strip()
        if text:
            lines.append(f"{indent}{'1.' if ordered else '-'} {text}")
        for sub in nested:
            lines.append(render_list(sub, base_url, depth + 1))
    return "\n".join(lines)


def render_table(node: Node, base_url: str) -> str:
    rows: list[list[str]] = []
    for section in node.children:
        if section.tag not in ("thead", "tbody", "tfoot"):
            continue
        for row in section.children:
            if row.tag != "tr":
                continue
            cells = [block_text(c, base_url).replace("|", "\\|")
                     for c in row.children if c.tag in ("td", "th")]
            if cells:
                rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def render_blocks(node: Node, base_url: str) -> str:
    blocks: list[str] = []
    for child in node.children:
        if child.tag == "#text":
            text = re.sub(r"\s+", " ", child.text).strip()
            if text:
                blocks.append(text)
            continue
        if child.tag in HEADING_TAGS:
            text = block_text(child, base_url)
            if text:
                blocks.append(f"{'#' * int(child.tag[1])} {text}")
        elif child.tag == "p":
            text = block_text(child, base_url)
            if text:
                blocks.append(text)
        elif child.tag in ("ul", "ol"):
            rendered = render_list(child, base_url)
            if rendered:
                blocks.append(rendered)
        elif child.tag == "table":
            rendered = render_table(child, base_url)
            if rendered:
                blocks.append(rendered)
        elif child.tag == "blockquote":
            inner = render_blocks(child, base_url)
            if inner:
                blocks.append("\n".join(f"> {ln}" for ln in inner.splitlines()))
        elif child.tag == "img":
            alt = child.attr("alt")
            if alt.strip():
                blocks.append(alt.strip())
        elif child.tag == "hr":
            blocks.append("---")
        elif child.tag in ("div", "section", "article", "span", "li", "dl",
                           "dt", "dd", "figure", "figcaption", "tr", "td",
                           "th", "a", "strong", "b", "em", "i"):
            blocks.append(render_blocks(child, base_url))
        else:
            text = block_text(child, base_url)
            if text:
                blocks.append(text)
    return clean_spacing("\n\n".join(b for b in blocks if b and b.strip()))


def clean_spacing(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def absolutize(href: str, base_url: str) -> str:
    """Resolve a CMS link. CMS urls are site-relative like 'public/individuals/x'."""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        return urljoin(base_url, href)
    return urljoin(base_url, "/" + href)


def html_to_markdown(fragment: str, base_url: str,
                     footnotes: dict[str, dict[str, str]] | None = None) -> str:
    """Convert a CMS HTML field to Markdown without altering its wording."""
    if not fragment or not fragment.strip():
        return ""

    # Resolve inline [Asset Included(LFGComponent:id)] references to their text.
    def _resolve(match: re.Match[str]) -> str:
        if not footnotes:
            return match.group(0)
        entry = footnotes.get(match.group(1))
        return entry["text"] if entry else ""

    fragment = ASSET_PLACEHOLDER.sub(_resolve, fragment)
    # Any placeholder still left is a reference the source never resolved to
    # text. Mark it so the caller can drop sentences it would otherwise break.
    fragment = ASSET_PLACEHOLDER_ANY.sub(ASSET_SENTINEL, fragment)

    builder = TreeBuilder()
    try:
        builder.feed(fragment)
        builder.close()
    except Exception:
        return ""
    strip_boilerplate(builder.root)
    return render_blocks(builder.root, base_url)


def resolve_asset_sentences(markdown: str) -> tuple[str, list[str]]:
    """Remove unresolvable asset markers, dropping sentences left incomplete.

    Returns the cleaned Markdown plus a warning for each dropped sentence.
    A trailing marker in an otherwise complete sentence (a table cell ending
    "...you choose where your premiums are invested. <marker>") is simply
    stripped. A marker that leaves a dangling connective ("...includes* and .")
    means the source never supplied the missing label, so the sentence is
    dropped rather than completed with guessed text.
    """
    warnings: list[str] = []
    if ASSET_SENTINEL not in markdown:
        return markdown, warnings

    output: list[str] = []
    stripped = 0
    for line in markdown.splitlines():
        if ASSET_SENTINEL not in line:
            output.append(line)
            continue

        # Table rows: the marker is trailing punctuation inside a cell.
        if line.lstrip().startswith("|"):
            output.append(line.replace(ASSET_SENTINEL, "").rstrip())
            continue

        kept: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if ASSET_SENTINEL not in sentence:
                kept.append(sentence)
                continue
            cleaned = sentence.replace(ASSET_SENTINEL, "").rstrip()
            if not cleaned.strip():
                # The marker was the whole "sentence" (a lone footnote or
                # media reference). Nothing readable is lost, so do not report
                # it as a dropped sentence.
                stripped += 1
                continue
            if (cleaned.endswith((".", "!", "?"))
                    and not DANGLING_CONNECTIVE.search(cleaned)):
                kept.append(cleaned)
            else:
                warnings.append(
                    "dropped sentence left incomplete by an asset reference "
                    "whose label is absent from the source: "
                    f"{cleaned.strip()[:120]!r}")
        if kept:
            output.append(" ".join(kept))

    return "\n".join(output), warnings


# ---------------------------------------------------------------------------
# JSON traversal helpers
# ---------------------------------------------------------------------------

def iter_component_lists(node: Any) -> Iterable[list]:
    """Yield every list of component-like dicts found anywhere under `node`.

    Handles ComponentAssoc being a bare list, a dict wrapping ComponentArray,
    a dict wrapping any other list, or nested several levels down.
    """
    if isinstance(node, list):
        if any(isinstance(i, dict) for i in node):
            yield node
        for item in node:
            yield from iter_component_lists(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_component_lists(value)


def looks_like_component(value: Any) -> bool:
    """A component is a dict carrying CMS component identity fields."""
    if not isinstance(value, dict):
        return False
    return any(k in value for k in ("template", "subtype", "assetType"))


def resolve_components(value: Any) -> list[dict]:
    """Normalise any ComponentAssoc-ish shape to a flat list of components."""
    out: list[dict] = []
    if isinstance(value, list):
        for item in value:
            if looks_like_component(item):
                out.append(item)
            else:
                out.extend(resolve_components(item))
    elif isinstance(value, dict):
        if looks_like_component(value):
            return [value]
        for nested in value.values():
            out.extend(resolve_components(nested))
    return out


# Fields that carry consumer-facing text, in editorial order.
HEADING_FIELDS = ("Heading", "SubHeading", "Subheading", "HeadingText",
                  "Title", "NavTitle", "CardTitle")
BODY_FIELDS = ("Copy", "BannerText", "Description", "BodyCopy", "Text",
               "ContentText")
LINK_LABEL_FIELDS = ("LinkText", "AriaLabel")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

class Extractor:
    """Walks a Page asset graph and emits ordered Markdown blocks."""

    def __init__(self, base_url: str, allowed_domains: set[str]) -> None:
        self.base_url = base_url
        self.allowed_domains = allowed_domains
        self.blocks: list[str] = []
        self.headings: list[str] = []
        self._seen_headings: set[str] = set()
        self.links: dict[str, dict[str, str]] = {}
        self._seen_links: set[str] = set()
        self.footnotes: dict[str, dict[str, str]] = {}
        self.warnings: list[str] = []

    # -- helpers ----------------------------------------------------------
    def add_heading(self, text: str, level: int = 2) -> None:
        text = re.sub(r"\s+", " ", unescape(text)).strip()
        if not text:
            return
        key = text.lower()
        if key in self._seen_headings:
            return
        self._seen_headings.add(key)
        self.headings.append(text)
        # Structural heading authored by an editor for this component.
        self.blocks.append(("section", max(2, min(level, 5)), text))

    def add_body(self, markdown: str) -> None:
        if markdown and markdown.strip():
            # Prose block; may itself contain headings authored in the rich
            # text editor, which are normalised relative to their section.
            self.blocks.append(("body", 0, markdown.strip()))

    def add_link(self, label: str, href: str, context: str = "") -> None:
        label = re.sub(r"\s+", " ", unescape(label)).strip()
        if not label or not href:
            return
        absolute = absolutize(href, self.base_url)
        parsed = urlparse(absolute)
        if parsed.netloc.lower() not in self.allowed_domains:
            return
        clean = urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path,
                            parsed.params, parsed.query, ""))
        if clean in self._seen_links:
            return
        self._seen_links.add(clean)
        self.links[clean] = {"url": clean, "title": label,
                             "context": context or self.base_url}

    # -- passes -----------------------------------------------------------
    def collect_footnotes(self, node: Any) -> None:
        """Pre-pass: map component id -> footnote/link text.

        CMS authors reference these with [Asset Included(LFGComponent:id)].
        """
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                ident = value.get("id")
                label = ""
                for field in LINK_LABEL_FIELDS:
                    candidate = value.get(field)
                    if isinstance(candidate, str) and candidate.strip():
                        label = candidate.strip()
                        break
                if not label:
                    for field in HEADING_FIELDS + BODY_FIELDS:
                        candidate = value.get(field)
                        if isinstance(candidate, str) and candidate.strip():
                            plain = html_to_markdown(candidate, self.base_url)
                            label = plain.strip()
                            break
                target = ""
                assoc = value.get("PageAssoc")
                if isinstance(assoc, dict):
                    target = assoc.get("url", "") or ""
                if ident is not None and label:
                    self.footnotes[str(ident)] = {"text": label, "url": target}
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(node)

    def emit_node(self, node: Any, level: int = 2) -> None:
        """Emit one component (or plain dict) in editorial order."""
        if isinstance(node, list):
            for item in node:
                self.emit_node(item, level)
            return
        if not isinstance(node, dict):
            return

        # 1. Headings.
        for field in HEADING_FIELDS:
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                # NavTitle on a link node is link text, handled in step 4.
                if field == "NavTitle":
                    continue
                self.add_heading(value, level)

        # 2. Body copy (HTML fragments preserved verbatim).
        for field in BODY_FIELDS:
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                self.add_body(html_to_markdown(value, self.base_url,
                                               self.footnotes))

        # 3. Nested components, in declared order.
        # Peer regions of the page layout share the parent level; only true
        # sub-components (grids, cards) nest one level deeper.
        for field in ("Banner", "BannerOverlay", "MainContent"):
            value = node.get(field)
            if value is None:
                continue
            for component in resolve_components(value):
                if component is not node:
                    self.emit_node(component, level)
        for field in ("ComponentAssoc", "ComponentArray", "ColumnSplit",
                      "ComponentList", "Widgets"):
            value = node.get(field)
            if value is None:
                continue
            for component in resolve_components(value):
                if component is not node:
                    self.emit_node(component, min(level + 1, 6))

        # 4. Link lists attached to this component.
        for field in ("Links", "LinksArray"):
            value = node.get(field)
            for component in resolve_components(value):
                label = ""
                for name in LINK_LABEL_FIELDS:
                    if isinstance(component.get(name), str) and component[name].strip():
                        label = component[name].strip()
                        break
                if not label:
                    nav = component.get("NavTitle")
                    label = nav.strip() if isinstance(nav, str) else ""
                assoc = component.get("PageAssoc")
                href = ""
                if isinstance(assoc, dict):
                    href = assoc.get("url", "") or component.get("href", "") or ""
                elif isinstance(assoc, str):
                    href = assoc
                href = href or component.get("url", "") or component.get("href", "")
                if label and href:
                    self.add_link(label, href, context=self.base_url)

        # 5. Disclaimer text is meaningful legal content; keep it.
        disclaimer = node.get("Disclaimer")
        if isinstance(disclaimer, str) and disclaimer.strip():
            self.add_body(html_to_markdown(disclaimer, self.base_url,
                                           self.footnotes))

    def run(self, page: dict, page_title: str = "") -> str:
        self.collect_footnotes(page)
        self.emit_node(page, level=2)

        # A page whose first content heading simply repeats the page title
        # adds nothing, so drop the duplicate.
        if (self.blocks and page_title
                and self.blocks[0][0] == "section"
                and self.blocks[0][2].strip().lower() == page_title.strip().lower()):
            dropped = self.blocks.pop(0)[2]
            if dropped in self.headings:
                self.headings.remove(dropped)
            self.warnings.append(
                f"removed duplicate heading identical to the page title: "
                f"{dropped!r}")

        # Renumber headings into one coherent hierarchy. The document title is
        # added separately as "#", so authored sections start at "##" and
        # headings inside prose are demoted one level below their section.
        rendered: list[str] = []
        section_level = 2
        for kind, level, value in self.blocks:
            if kind == "section":
                section_level = level
                rendered.append(f"{'#' * section_level} {value}")
                continue

            lines: list[str] = []
            for line in value.splitlines():
                match = re.match(r"^(#{1,6})\s+(.*)$", line)
                if match:
                    demoted = min(6, section_level + 1)
                    lines.append(f"{'#' * demoted} {match.group(2).strip()}")
                else:
                    lines.append(line)
            rendered.append("\n".join(lines).strip())

        markdown = clean_spacing("\n\n".join(r for r in rendered if r.strip()))
        markdown, asset_warnings = resolve_asset_sentences(markdown)
        self.warnings.extend(asset_warnings)
        return clean_spacing(markdown)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Fetcher:
    """robots.txt-aware, rate-limited, self-identifying JSON fetcher."""

    def __init__(self, config: dict, allowed_domains: list[str]) -> None:
        self.user_agent = config["user_agent"]
        self.delay = float(config.get("crawl_delay_seconds", 10))
        self.timeout = int(config.get("request_timeout_seconds", 30))
        self.respect_robots = bool(config.get("respect_robots_txt", True))
        self.allowed_domains = {d.lower() for d in allowed_domains}
        api = config["content_api"]
        self.api_url = api["base_url"].rstrip("/") + api["path"]
        self.audience = api.get("audience", "global")
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if origin not in self.robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            try:
                parser.read()
            except Exception as exc:
                print(f"    ! robots.txt unreadable for {origin}: {exc}")
                parser.disallow_all = True
            self.robots[origin] = parser
        return self.robots[origin]

    def allowed(self, url: str) -> tuple[bool, str]:
        host = urlparse(url).netloc.lower()
        if host not in self.allowed_domains:
            return False, f"host '{host}' is not an allowed Lincoln domain"
        if self.respect_robots and not self._robots_for(url).can_fetch(self.user_agent, url):
            return False, "disallowed by robots.txt"
        return True, ""

    def fetch_page(self, resource: dict) -> tuple[dict | None, dict | None, str]:
        """Fetch one approved page. Returns (raw_json, response_meta, reason)."""
        vanity = resource["vanityUrl"]
        if not resource.get("approved_public", False):
            return None, None, "resource is not marked approved_public"
        if "/secure/" in vanity or vanity.startswith("secure/"):
            return None, None, f"refusing non-public path '{vanity}'"

        url = f"{self.api_url}?vanityUrl={vanity}&audience={self.audience}"
        ok, reason = self.allowed(url)
        if not ok:
            return None, None, reason

        try:
            response = self.session.get(url, timeout=self.timeout,
                                        allow_redirects=True)
        except requests.RequestException as exc:
            return None, None, f"request failed: {exc}"

        meta = {"request_url": url, "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
                "response_bytes": len(response.content)}

        if response.status_code in (401, 403):
            return None, meta, (f"blocked by the site (HTTP "
                                f"{response.status_code}); not bypassing")
        if response.status_code == 429:
            return None, meta, "rate limited by the site (HTTP 429)"
        if response.status_code >= 400:
            return None, meta, f"HTTP {response.status_code}"
        if "json" not in meta["content_type"].lower():
            return None, meta, f"unexpected content type '{meta['content_type']}'"

        try:
            payload = response.json()
        except ValueError as exc:
            return None, meta, f"response was not valid JSON: {exc}"

        time.sleep(self.delay)
        return payload, meta, ""

    def pace(self) -> None:
        time.sleep(self.delay)


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------

def find_repo_root(start: str) -> str:
    """Walk up to the repository root, identified by a .git entry."""
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(f"could not locate repository root (.git) above {start}")
        current = parent


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def rel(path: str, repo_root: str) -> str:
    return os.path.relpath(path, repo_root).replace("\\", "/")


# ---------------------------------------------------------------------------
# Per-resource processing
# ---------------------------------------------------------------------------

def process_resource(fetcher: Fetcher, resource: dict, repo_root: str,
                     root: str, from_raw: bool = False) -> dict:
    name = resource.get("filename") or resource["id"]
    url = resource["url"]
    retrieved_at = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "status": "failed",
        "title": "",
        "source_url": url,
        "canonical_url": "",
        "vanity_url": resource.get("vanityUrl", ""),
        "retrieved_at": retrieved_at,
        "resource_category": resource.get("resource_category", ""),
        "headings": [],
        "reason": "",
        "http_status": None,
        "content_type": "",
        "raw_bytes": 0,
        "body_characters": 0,
        "word_count": 0,
        "warnings": [],
        "output_files": [],
        "excluded_from_rag": bool(resource.get("excluded_from_rag", False)),
        "exclusion_reason": resource.get("exclusion_reason", ""),
    }

    print(f"\n==> {resource.get('title_hint') or name}")
    print(f"    vanityUrl={record['vanity_url']}")

    raw_path = os.path.join(root, "raw", f"{name}.json")

    if from_raw:
        # Offline re-extraction: reuse the already-downloaded response.
        if not os.path.exists(raw_path):
            record["reason"] = f"no raw response at {rel(raw_path, repo_root)}"
            print(f"    FAILED: {record['reason']}")
            write_json(os.path.join(root, "metadata", f"{name}.json"), record)
            return {**record, "_links": []}
        with open(raw_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        meta = {"http_status": None, "content_type": "application/json",
                "response_bytes": os.path.getsize(raw_path)}
        print("    (local re-extract from raw, no network request)")
    else:
        payload, meta, reason = fetcher.fetch_page(resource)
        if payload is None:
            if meta:
                record["http_status"] = meta.get("http_status")
                record["content_type"] = meta.get("content_type", "")
            record["reason"] = reason
            print(f"    FAILED: {reason}")
            write_json(os.path.join(root, "metadata", f"{name}.json"), record)
            return {**record, "_links": []}

    record["raw_bytes"] = meta.get("response_bytes", 0)
    if meta.get("http_status"):
        record["http_status"] = meta.get("http_status")

    # 1. Preserve the original API response (skipped when re-extracting).
    if not from_raw:
        write_text(raw_path, json.dumps(payload, indent=2, ensure_ascii=False))
    record["output_files"].append(rel(raw_path, repo_root))

    page = payload.get("Page")
    if not isinstance(page, dict):
        record["reason"] = "response contained no Page object"
        print(f"    FAILED: {record['reason']}")
        write_json(os.path.join(root, "metadata", f"{name}.json"), record)
        return {**record, "_links": []}

    raw_status = payload.get("StatusCode")
    try:
        status_code = int(raw_status)
    except (TypeError, ValueError):
        status_code = raw_status
    title = str(page.get("MetaTitle") or page.get("name") or "").strip()
    record["title"] = title
    record["canonical_url"] = url

    # 2. Extract consumer-facing content.
    extractor = Extractor(fetcher.api_url, fetcher.allowed_domains)
    markdown = extractor.run(page, page_title=title)

    body_chars = len(markdown)
    record["headings"] = extractor.headings
    record["body_characters"] = body_chars
    record["word_count"] = len(markdown.split())
    record["warnings"] = extractor.warnings
    record["audience"] = (payload.get("Navigation") or {}).get("Audience", "")
    record["page_asset_name"] = str(page.get("name", ""))
    record["page_asset_id"] = str(page.get("id", ""))

    # 3. Quality gate -- HTTP 200 is NOT success.
    problems = []
    if status_code != 200:
        problems.append(f"API StatusCode was {status_code}, not 200")
    if body_chars < MIN_BODY_CHARACTERS:
        problems.append(
            f"only {body_chars} characters of content extracted "
            f"(minimum {MIN_BODY_CHARACTERS})")
    if len(extractor.headings) < MIN_HEADINGS:
        problems.append(f"only {len(extractor.headings)} headings extracted")
    if body_chars < WARN_BODY_CHARACTERS:
        record["warnings"].append(
            f"thin page: {body_chars} characters is below the "
            f"{WARN_BODY_CHARACTERS}-character review threshold")

    if problems:
        record["reason"] = "; ".join(problems)
        print(f"    FAILED (quality gate): {record['reason']}")
        write_json(os.path.join(root, "metadata", f"{name}.json"), record)
        return {**record, "_links": list(extractor.links.values())}

    # 4. Emit Markdown with provenance for later chunking.
    front = [
        "---",
        f"title: {json.dumps(title)}",
        f"source_url: {json.dumps(url)}",
        f"canonical_url: {json.dumps(url)}",
        f"vanity_url: {json.dumps(record['vanity_url'])}",
        f"retrieved_at: {json.dumps(retrieved_at)}",
        f"resource_category: {json.dumps(record['resource_category'])}",
        'source: "Lincoln Financial (public consumer education content)"',
        'retrieval_method: "public JSON content endpoint /pbl-api/getcontent"',
        "---",
        "",
        f"# {title or name}",
        "",
    ]
    cleaned = "\n".join(front) + markdown + "\n"
    cleaned_path = os.path.join(root, "cleaned", f"{name}.md")
    write_text(cleaned_path, cleaned)
    record["output_files"].append(rel(cleaned_path, repo_root))
    record["status"] = "success"

    write_json(os.path.join(root, "metadata", f"{name}.json"), record)

    print(f"    OK  {record['http_status']}  title={title!r}")
    print(f"        chars={body_chars} words={record['word_count']} "
          f"headings={len(extractor.headings)} links={len(extractor.links)}")
    for warning in record["warnings"]:
        print(f"        warning: {warning}")

    return {**record, "_links": list(extractor.links.values())}


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = find_repo_root(here)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.path.join(here, "lincoln_sources.json"))
    parser.add_argument("--dry-run", action="store_true",
                        help="List approved resources and exit without fetching.")
    parser.add_argument("--from-raw", action="store_true",
                        help="Re-extract from the already-downloaded raw JSON "
                             "files. Makes no network requests at all.")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)

    root = os.path.abspath(os.path.join(repo_root, config.get(
        "output_root", "knowledge/lincoln-documents")))

    # Safety rail: never write outside the repository.
    if os.path.commonpath([root, repo_root]).lower() != repo_root.lower():
        print(f"REFUSING TO RUN: output_root '{root}' is outside '{repo_root}'")
        return 2

    resources = config.get("resources", [])
    print(f"Approved resources: {len(resources)}")
    print(f"Content endpoint : {config['content_api']['base_url']}"
          f"{config['content_api']['path']}")
    for item in resources:
        print(f"  - {item.get('title_hint', item['id'])}")
        print(f"    vanityUrl={item['vanityUrl']}")

    if args.dry_run:
        print("\nDry run: no requests made.")
        return 0

    fetcher = Fetcher(config, config["allowed_domains"])
    print(f"\nOutput root: {root}")
    print(f"Delay between requests: {fetcher.delay}s")

    results: list[dict] = []
    all_links: dict[str, dict[str, Any]] = {}

    for index, resource in enumerate(resources):
        if index:
            fetcher.pace()
        try:
            result = process_resource(fetcher, resource, repo_root, root,
                                    from_raw=args.from_raw)
        except Exception as exc:
            print(f"    UNEXPECTED ERROR: {exc}")
            record = {
                "status": "failed", "source_url": resource["url"],
                "vanity_url": resource.get("vanityUrl", ""),
                "canonical_url": "", "retrieved_at":
                    datetime.now(timezone.utc).isoformat(),
                "title": "", "headings": [],
                "resource_category": resource.get("resource_category", ""),
                "reason": f"unexpected error: {exc}",
                "warnings": [], "output_files": [], "_links": [],
            }
            try:
                write_json(os.path.join(root, "metadata",
                                        f"{resource.get('filename', resource['id'])}.json"),
                           {k: v for k, v in record.items() if not k.startswith("_")})
            except OSError as write_exc:
                print(f"    ! could not write failure metadata: {write_exc}")
            result = record
        results.append(result)
        for link in result.get("_links", []) or []:
            if not isinstance(link, dict) or not link.get("url"):
                continue
            entry = all_links.setdefault(link["url"], {**link, "found_on_pages": []})
            if result["source_url"] not in entry["found_on_pages"]:
                entry["found_on_pages"].append(result["source_url"])

    seen = {r["url"] for r in resources}
    discovered = {
        "_note": ("Links discovered inside the approved seed pages. Recorded for human "
                  "review only -- the crawler does not follow them."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "allowed_domains": config["allowed_domains"],
        "already_collected": sorted(seen),
        "candidates": sorted((link for url, link in all_links.items()
                              if url not in seen),
                             key=lambda item: item["url"]),
    }
    write_json(os.path.join(root, "discovered_links.json"), discovered)

    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] != "success"]

    print(f"\n{'=' * 62}")
    print(f"Collected : {len(successes)}/{len(results)}")
    print(f"Discovered : {len(discovered['candidates'])} candidate links for review")
    for item in failures:
        print(f"  FAILED: {item['source_url']} -> {item.get('reason', '')}")
    for item in successes:
        for warning in item.get("warnings", []):
            print(f"  WARNING: {item['source_url']} -> {warning}")
    print(f"Output root: {root}")

    return 0


if __name__ == "__main__":
    sys.exit(main())