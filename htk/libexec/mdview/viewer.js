/* global marked, DOMPurify, mermaid */
const article = document.getElementById("document");
const status = document.getElementById("status");
const error = document.getElementById("error");
const base = new URL(".", location.href);
let etag = null;
let initialized = false;
let diagramId = 0;
let refreshTimer;
let refreshing = false;
let refreshAgain = false;
const localImages = new Map();

marked.setOptions({ gfm: true, breaks: false });
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  suppressErrorRendering: true,
  htmlLabels: false,
  theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default",
});

function fingerprint(element) {
  let hash = 2166136261;
  for (const character of element.outerHTML) {
    hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  }
  return String(hash >>> 0);
}

async function renderDiagrams() {
  for (const code of article.querySelectorAll("pre > code.language-mermaid")) {
    const source = code.textContent;
    const pre = code.parentElement;
    const diagram = document.createElement("div");
    diagram.className = "mermaid-diagram";
    diagram.dataset.key = pre.dataset.key || fingerprint(pre);
    diagram.tabIndex = 0;
    diagram.setAttribute("role", "region");
    diagram.setAttribute("aria-label", "Mermaid diagram — scroll horizontally if needed");
    pre.replaceWith(diagram);
    try {
      const { svg } = await mermaid.render(`mdview-mermaid-${++diagramId}`, source, diagram);
      diagram.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
    } catch (failure) {
      const message = document.createElement("p");
      message.className = "mermaid-error";
      message.textContent = `Cannot render Mermaid diagram: ${failure.message || failure}`;
      // Keep the source visible and confine an error to this diagram.
      diagram.replaceChildren(message, pre);
    }
  }
}

async function render(markdown) {
  const position = { x: scrollX, y: scrollY };
  const visible = [...article.children].find(element => element.getBoundingClientRect().bottom > 90);
  const anchor = visible && { key: visible.dataset.key, top: visible.getBoundingClientRect().top };
  const horizontalScrolls = new Map([...article.querySelectorAll(".table-scroll, .mermaid-diagram")].map(element => [element.dataset.key, element.scrollLeft]));
  // Inline styles are reserved for generated SVG, not Markdown-provided HTML.
  article.innerHTML = DOMPurify.sanitize(marked.parse(markdown), { FORBID_TAGS: ["style"], FORBID_ATTR: ["style"] });
  localImages.clear();

  const slugs = new Map();
  article.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach(heading => {
    const slug = heading.textContent.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s+/g, "-") || "section";
    const count = slugs.get(slug) || 0;
    slugs.set(slug, count + 1);
    heading.id = count ? `${slug}-${count}` : slug;
  });

  article.querySelectorAll("table").forEach(table => {
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Markdown table — scroll horizontally for more columns");
    table.replaceWith(wrapper);
    wrapper.append(table);
  });

  article.querySelectorAll("img").forEach(image => {
    const source = image.getAttribute("src");
    if (source && !/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(source)) {
      image.src = new URL(source.replace(/^\//, ""), new URL("local/", base)).href;
      localImages.set(image, { url: image.src, etag: null });
    }
  });
  article.querySelectorAll("a[href]").forEach(link => {
    const href = link.getAttribute("href");
    if (!href.startsWith("#")) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  });

  const occurrences = new Map();
  [...article.children].forEach(element => {
    const key = fingerprint(element);
    const count = occurrences.get(key) || 0;
    occurrences.set(key, count + 1);
    element.dataset.key = `${key}-${count}`;
  });
  // Fingerprint the source before Mermaid adds generated SVG IDs and styles.
  await renderDiagrams();
  article.querySelectorAll(".table-scroll, .mermaid-diagram").forEach(element => {
    element.scrollLeft = horizontalScrolls.get(element.dataset.key) || 0;
  });

  if (!initialized && location.hash) {
    try { document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView(); } catch (_) { /* Ignore malformed hashes. */ }
  } else {
    const replacement = anchor && [...article.children].find(element => element.dataset.key === anchor.key);
    window.scrollTo(position.x, replacement ? position.y + replacement.getBoundingClientRect().top - anchor.top : position.y);
  }
  initialized = true;
}

async function readResponse(url, options, read) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error("Preview request timed out. Retrying…"));
      controller.abort();
    }, 5000);
  });
  try {
    // Include body reads in the deadline. A stalled response must not stop
    // future polls, and a late response must never update the document.
    return await Promise.race([
      fetch(url, { ...options, signal: controller.signal }).then(read),
      deadline,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function refreshImages() {
  // Local diagrams/images can change while the Markdown itself stays unchanged.
  await Promise.allSettled([...localImages].map(async ([image, state]) => {
    const response = await readResponse(state.url, {
      headers: state.etag ? { "If-None-Match": state.etag } : {},
      cache: "no-store",
    }, async response => {
      await response.body?.cancel();
      return response;
    });
    if (!response.ok || response.status === 304) return;
    const next = response.headers.get("ETag");
    if (next && next !== state.etag) {
      const url = new URL(state.url);
      url.searchParams.set("_mdview", next);
      image.src = url.href;
      state.etag = next;
    }
  }));
}

async function refresh() {
  clearTimeout(refreshTimer);
  if (refreshing) {
    refreshAgain = true;
    return;
  }
  refreshing = true;
  try {
    const { response, data } = await readResponse(new URL("content", base), {
      headers: etag ? { "If-None-Match": etag } : {},
      cache: "no-store",
    }, async response => {
      if (response.status === 304) return { response };
      if (!response.ok) throw new Error(await response.text());
      return { response, data: await response.json() };
    });
    if (response.status !== 304) {
      document.title = `${data.name} — Markdown preview`;
      document.getElementById("filename").textContent = data.name;
      document.getElementById("filepath").textContent = data.path;
      document.getElementById("filepath").title = data.path;
      await render(data.markdown);
      etag = response.headers.get("ETag");
    }
    await refreshImages();
    status.textContent = "Live";
    status.className = "live";
    error.hidden = true;
  } catch (failure) {
    status.textContent = "Reconnecting…";
    status.className = "";
    error.textContent = failure instanceof TypeError ? "Preview disconnected. Keep mdview running in your terminal." : failure.message;
    error.hidden = false;
  } finally {
    refreshing = false;
    refreshTimer = setTimeout(refresh, refreshAgain ? 0 : document.hidden ? 2000 : 750);
    refreshAgain = false;
  }
}

window.addEventListener("focus", refresh);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
window.addEventListener("pageshow", event => {
  if (event.persisted) refresh();
});
refresh();
