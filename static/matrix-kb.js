const PROFILE_KEY = "matrix.kb.embedding_profile_id";
const STRATEGY_KEY = "matrix.kb.chunk_strategy";
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;
const SAMPLE_MARKDOWN = `# 售后手册

## 退款

七天无理由退款需提供购买凭证和完好包装。超过七天仅支持质量问题退换。

## 发票

电子发票在发货后自动开具，可在订单详情页下载。纸质发票需下单时备注。

### 开票信息

公司抬头以营业执照为准。
`;

const FALLBACK_STRATEGIES = [
  { id: "sentence", label: "按句子", parser: "SentenceSplitter" },
  { id: "token", label: "按 Token", parser: "TokenTextSplitter" },
  { id: "markdown", label: "按 Markdown 标题", parser: "MarkdownNodeParser" },
  { id: "markdown_element", label: "按 Markdown 元素", parser: "MarkdownElementNodeParser" },
  { id: "semantic", label: "按语义", parser: "SemanticSplitterNodeParser" },
  { id: "sentence_window", label: "句子窗口", parser: "SentenceWindowNodeParser" }
];

const FALLBACK_PROFILES = {
  default: "bge-m3",
  profiles: ["openai-small", "bge-m3", "qwen3"]
};

const ELEMENT_LABELS = {
  title: "标题",
  text: "正文",
  table: "表格",
  code: "代码",
  table_text: "表格"
};

const AUTO_STRATEGY_BY_SUFFIX = {
  ".md": "markdown",
  ".markdown": "markdown"
};

const BADGE_BY_SUFFIX = {
  ".md": "MD",
  ".markdown": "MD",
  ".txt": "TXT",
  ".pdf": "PDF",
  ".docx": "DOCX",
  ".pptx": "PPTX",
  ".html": "HTML",
  ".htm": "HTM"
};

const DEFAULT_PARAMS = {
  chunkSize: "512",
  overlap: "64",
  breakpoint: "95",
  buffer: "1",
  windowSize: "3"
};

let kbStrategies = FALLBACK_STRATEGIES.slice();
let kbSelectedStrategy = "sentence";
let kbStrategyUserPicked = false;
let kbSourceSuffix = "";
let kbSourceMode = "upload";
let kbUiStep = 1;
let kbPreviewSeq = 0;
let kbLastPreview = null;
let kbLastPreviewKey = "";
let kbFileMeta = null;
let kbOpened = false;
let kbBound = false;

function kbAuthHeaders() {
  return window.matrixAuth?.headers() || {};
}

function kbErrorText(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") {
    return window.matrixAuth?.errorText?.(detail) || detail;
  }
  if (Array.isArray(detail)) {
    return detail.map(item => item.msg || JSON.stringify(item)).join("；");
  }
  return fallback;
}

async function kbRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...kbAuthHeaders(),
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(kbErrorText(payload, "请求失败"));
  }
  return payload;
}

function setKbStatus(text, kind = "") {
  const message = String(text || "").trim();
  document.querySelectorAll(".kb-status").forEach(node => {
    node.hidden = !message;
    node.textContent = message;
    node.dataset.kind = kind;
  });
}

function currentKbProfile() {
  return document.querySelector("#kbEmbedding")?.value || "";
}

function persistKbProfile(profileId) {
  if (!profileId) return;
  try {
    sessionStorage.setItem(PROFILE_KEY, profileId);
  } catch (_) {}
}

function rememberedKbProfile() {
  try {
    return sessionStorage.getItem(PROFILE_KEY) || "";
  } catch (_) {
    return "";
  }
}

function rememberedKbStrategy() {
  try {
    return sessionStorage.getItem(STRATEGY_KEY) || "";
  } catch (_) {
    return "";
  }
}

function persistKbStrategy(strategy) {
  if (!strategy) return;
  try {
    sessionStorage.setItem(STRATEGY_KEY, strategy);
  } catch (_) {}
}

function selectedKbStrategy() {
  return kbStrategies.find(item => item.id === kbSelectedStrategy) || kbStrategies[0] || null;
}

function kbSourceText() {
  return document.querySelector("#kbSource")?.value.trim() || "";
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function currentKbStrategyId() {
  const fromSelect = document.querySelector("#kbStrategy")?.value;
  if (fromSelect && kbStrategies.some(item => item.id === fromSelect)) return fromSelect;
  const fromCard = document.querySelector(".kb-strategy.active")?.dataset.strategy;
  if (fromCard && kbStrategies.some(item => item.id === fromCard)) return fromCard;
  return kbSelectedStrategy || kbStrategies[0]?.id || "sentence";
}

function applyKbStrategyChrome() {
  const select = document.querySelector("#kbStrategy");
  if (select && select.value !== kbSelectedStrategy) select.value = kbSelectedStrategy;
  document.querySelectorAll(".kb-strategy").forEach(button => {
    button.classList.toggle("active", button.dataset.strategy === kbSelectedStrategy);
  });
  syncKbParamVisibility();
}

function selectKbStrategy(strategyId, { fromUser = false } = {}) {
  if (fromUser) kbStrategyUserPicked = true;
  const next = kbStrategies.some(item => item.id === strategyId)
    ? strategyId
    : kbStrategies[0]?.id || "sentence";
  kbSelectedStrategy = next;
  persistKbStrategy(next);
  applyKbStrategyChrome();
}

function syncKbParamVisibility() {
  const strategy = kbSelectedStrategy;
  document.querySelectorAll("[data-kb-param]").forEach(node => {
    const kind = node.dataset.kbParam;
    const show =
      kind === "length" ||
      (kind === "semantic" && strategy === "semantic") ||
      (kind === "window" && strategy === "sentence_window");
    node.hidden = !show;
  });
  const hint = document.querySelector("#kbParamHint");
  if (!hint) return;
  if (strategy === "semantic") {
    hint.textContent = "语义切分使用当前模型。失败会降级为按句子，并在右侧说明。";
  } else if (strategy === "sentence_window") {
    hint.textContent = "一句一块；右侧同时列出句子和窗口。向量以后打在句子上。";
  } else if (strategy === "markdown") {
    hint.textContent = "按标题成节，超过三级的标题当作正文。长度单位是 token。";
  } else {
    hint.textContent = "长度单位是 token。overlap 必须小于 chunk_size。";
  }
}

function fillKbStrategies(payload) {
  const items = payload?.strategies?.length ? payload.strategies : FALLBACK_STRATEGIES;
  kbStrategies = items;
  const select = document.querySelector("#kbStrategy");
  if (select) {
    select.replaceChildren(
      ...kbStrategies.map(item => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        return option;
      })
    );
  }
  const remembered = rememberedKbStrategy();
  const ids = new Set(kbStrategies.map(item => item.id));
  kbSelectedStrategy = ids.has(remembered)
    ? remembered
    : ids.has(kbSelectedStrategy)
      ? kbSelectedStrategy
      : payload?.default || kbStrategies[0]?.id || "sentence";
  if (remembered && ids.has(remembered)) kbStrategyUserPicked = true;
  persistKbStrategy(kbSelectedStrategy);
  renderKbStrategies();
  applyKbStrategyChrome();
}

function renderKbStrategies() {
  const host = document.querySelector("#kbStrategies");
  if (!host) return;
  host.replaceChildren(
    ...kbStrategies.map(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `kb-strategy${item.id === kbSelectedStrategy ? " active" : ""}`;
      button.dataset.strategy = item.id;
      const name = document.createElement("strong");
      name.textContent = item.label;
      const sub = document.createElement("small");
      sub.textContent = item.id;
      button.append(name, sub);
      button.addEventListener("click", () => {
        selectKbStrategy(item.id, { fromUser: true });
      });
      return button;
    })
  );
}

function fillKbProfiles(payload) {
  const select = document.querySelector("#kbEmbedding");
  if (!select) return;
  const profiles = payload.profiles || [];
  const remembered = rememberedKbProfile();
  const previous = select.value;
  select.replaceChildren(
    ...profiles.map(profileId => {
      const option = document.createElement("option");
      option.value = profileId;
      option.textContent = profileId;
      return option;
    })
  );
  const ids = new Set(profiles);
  let next = remembered || previous || payload.default;
  if (!ids.has(next)) {
    next = payload.default;
    if (remembered && remembered !== next) {
      setKbStatus(`当前模型已不在列表，已回到 ${payload.default}。`, "warn");
    }
  }
  if (ids.has(next)) select.value = next;
  persistKbProfile(select.value);
}

function kbPreviewBody() {
  const strategy = currentKbStrategyId();
  kbSelectedStrategy = strategy;
  persistKbStrategy(strategy);
  persistKbProfile(currentKbProfile());
  applyKbStrategyChrome();
  const chunkSize = Number(document.querySelector("#kbChunkSize")?.value || 512);
  const overlap = Number(document.querySelector("#kbChunkOverlap")?.value || 64);
  const body = {
    text: kbSourceText(),
    strategy,
    chunk_size: chunkSize,
    chunk_overlap: overlap
  };
  if (strategy === "semantic") {
    body.embedding_profile_id = currentKbProfile();
    body.breakpoint_percentile_threshold = Number(
      document.querySelector("#kbBreakpoint")?.value || 95
    );
    body.buffer_size = Number(document.querySelector("#kbBuffer")?.value || 1);
  }
  if (strategy === "sentence_window") {
    body.window_size = Number(document.querySelector("#kbWindowSize")?.value || 3);
  }
  if (kbSourceSuffix) body.source_suffix = kbSourceSuffix;
  return body;
}

function kbRequestSettings(body) {
  return JSON.stringify({
    strategy: body.strategy,
    chunk_size: body.chunk_size,
    chunk_overlap: body.chunk_overlap,
    embedding_profile_id: body.embedding_profile_id || "",
    breakpoint_percentile_threshold: body.breakpoint_percentile_threshold,
    buffer_size: body.buffer_size,
    window_size: body.window_size,
    source_suffix: body.source_suffix || ""
  });
}

function renderKbChunk(chunk, index, strategy) {
  const card = document.createElement("article");
  card.className = "kb-chunk";
  card.dataset.search = `${chunk.text || ""} ${chunk.header_path || ""} ${chunk.element_type || ""}`;
  const head = document.createElement("header");
  const ord = document.createElement("span");
  ord.className = "kb-ord";
  ord.textContent = `分段-${String(index + 1).padStart(2, "0")}`;
  head.append(ord);
  const chars = document.createElement("span");
  chars.className = "kb-tag";
  chars.textContent = `${String(chunk.text || "").length} 字符`;
  head.append(chars);
  if (chunk.header_path) {
    const path = document.createElement("span");
    path.className = "kb-tag";
    path.textContent = chunk.header_path;
    head.append(path);
  }
  if (chunk.element_type) {
    const type = document.createElement("span");
    type.className = "kb-tag";
    type.textContent = ELEMENT_LABELS[chunk.element_type] || chunk.element_type;
    head.append(type);
  }
  if (chunk.char_start != null && chunk.char_end != null) {
    const span = document.createElement("span");
    span.className = "kb-range";
    span.textContent = `${chunk.char_start}–${chunk.char_end}`;
    head.append(span);
  }
  const cols = document.createElement("div");
  cols.className = `kb-chunk-cols${strategy === "sentence_window" && chunk.window ? " two" : ""}`;
  const main = document.createElement("div");
  if (strategy === "sentence_window") {
    const kicker = document.createElement("p");
    kicker.className = "kb-col-kicker";
    kicker.textContent = "句子";
    main.append(kicker);
  }
  const text = document.createElement("p");
  text.className = "kb-chunk-text";
  text.textContent = chunk.text || "";
  main.append(text);
  cols.append(main);
  if (strategy === "sentence_window" && chunk.window) {
    const side = document.createElement("div");
    const kicker = document.createElement("p");
    kicker.className = "kb-col-kicker";
    kicker.textContent = "窗口";
    const windowText = document.createElement("p");
    windowText.className = "kb-chunk-window";
    windowText.textContent = chunk.window;
    side.append(kicker, windowText);
    cols.append(side);
  }
  card.append(head, cols);
  return card;
}

function emptyKbChunks(message) {
  const empty = document.createElement("p");
  empty.className = "kb-empty";
  empty.id = "kbChunkEmpty";
  empty.textContent = message;
  return empty;
}

function renderKbChunks(payload) {
  kbLastPreview = payload;
  const list = document.querySelector("#kbChunks");
  const notes = document.querySelector("#kbNotes");
  const meta = document.querySelector("#kbPreviewMeta");
  const title = document.querySelector("#kbPreviewTitle");
  const search = document.querySelector("#kbChunkSearch");
  const chunks = payload.chunks || [];
  if (title) title.textContent = kbFileMeta?.name || selectedKbStrategy()?.label || "预览";
  if (meta) {
    meta.textContent = chunks.length ? `${chunks.length} 预估块 · 尚未入库` : "0 预估块";
  }
  if (search) {
    search.hidden = !chunks.length;
    search.value = "";
  }
  if (notes) {
    const text = String(payload.notes || "").trim();
    notes.hidden = !text;
    notes.textContent = text;
  }
  if (!list) return;
  if (!chunks.length) {
    list.replaceChildren(emptyKbChunks("没有切出块。改策略后再点「预览块」。"));
    return;
  }
  const fragment = document.createDocumentFragment();
  chunks.forEach((chunk, index) => {
    fragment.append(renderKbChunk(chunk, index, payload.strategy));
  });
  list.replaceChildren(fragment);
}

function filterKbChunks() {
  const query = (document.querySelector("#kbChunkSearch")?.value || "").trim().toLowerCase();
  const cards = document.querySelectorAll("#kbChunks .kb-chunk");
  if (!cards.length) return;
  cards.forEach(card => {
    const hay = (card.dataset.search || "").toLowerCase();
    card.hidden = Boolean(query) && !hay.includes(query);
  });
}

async function runKbPreview() {
  if (!kbSourceText()) {
    setKbStatus("请先粘贴文本或上传文件。", "error");
    return false;
  }
  const seq = (kbPreviewSeq += 1);
  const button = document.querySelector("#kbPreviewBtn");
  if (button) button.disabled = true;
  setKbStatus("正在切分…");
  const body = kbPreviewBody();
  try {
    const payload = await kbRequest("/api/kb/preview-chunks", {
      method: "POST",
      body: JSON.stringify(body)
    });
    if (seq !== kbPreviewSeq) return false;
    kbLastPreviewKey = kbRequestSettings(body);
    renderKbChunks(payload);
    setKbStatus(payload.notes ? payload.notes : `已切出 ${payload.chunks.length} 块，全部列出。`);
    return true;
  } catch (error) {
    if (seq !== kbPreviewSeq) return false;
    setKbStatus(error.message, "error");
    return false;
  } finally {
    if (seq === kbPreviewSeq && button) button.disabled = false;
  }
}

async function loadKbCatalog() {
  fillKbStrategies({ default: "sentence", strategies: FALLBACK_STRATEGIES });
  fillKbProfiles(FALLBACK_PROFILES);
  try {
    const strategies = await kbRequest("/api/kb/chunk-strategies");
    fillKbStrategies(strategies);
  } catch (error) {
    setKbStatus(`切分策略列表未拉到，已用六张默认卡。${error.message}`, "warn");
  }
  try {
    const profiles = await kbRequest("/api/kb/embedding-profiles");
    fillKbProfiles(profiles);
  } catch (error) {
    fillKbProfiles(FALLBACK_PROFILES);
    setKbStatus(`模型列表暂不可用，已用本地默认。${error.message}`, "warn");
  }
}

function fileSuffix(name) {
  const base = String(name || "").split(/[/\\]/).pop() || "";
  const index = base.lastIndexOf(".");
  return index >= 0 ? base.slice(index).toLowerCase() : "";
}

function applyAutoStrategy(suffix) {
  const auto = AUTO_STRATEGY_BY_SUFFIX[suffix];
  if (!auto || kbStrategyUserPicked || auto === kbSelectedStrategy) return false;
  selectKbStrategy(auto);
  return true;
}

function setKbSourceMode(mode) {
  kbSourceMode = mode === "paste" ? "paste" : "upload";
  document.querySelector("#kbModeUpload")?.classList.toggle("active", kbSourceMode === "upload");
  document.querySelector("#kbModePaste")?.classList.toggle("active", kbSourceMode === "paste");
  const upload = document.querySelector("#kbUploadPanel");
  const paste = document.querySelector("#kbPastePanel");
  if (upload) upload.hidden = kbSourceMode !== "upload";
  if (paste) paste.hidden = kbSourceMode !== "paste";
}

function showKbFileCard(meta) {
  kbFileMeta = meta;
  const card = document.querySelector("#kbFileCard");
  const drop = document.querySelector("#kbDrop");
  if (!card) return;
  if (!meta) {
    card.hidden = true;
    if (drop) drop.hidden = false;
    return;
  }
  const badge = document.querySelector("#kbFileBadge");
  const name = document.querySelector("#kbFileName");
  const details = document.querySelector("#kbFileMeta");
  if (badge) badge.textContent = meta.badge || "FILE";
  if (name) name.textContent = meta.name;
  if (details) details.textContent = `${(meta.badge || "FILE")} · ${formatFileSize(meta.size)}`;
  card.hidden = false;
  if (drop) drop.hidden = true;
}

function resetKbParams() {
  const mapping = {
    kbChunkSize: DEFAULT_PARAMS.chunkSize,
    kbChunkOverlap: DEFAULT_PARAMS.overlap,
    kbBreakpoint: DEFAULT_PARAMS.breakpoint,
    kbBuffer: DEFAULT_PARAMS.buffer,
    kbWindowSize: DEFAULT_PARAMS.windowSize
  };
  Object.entries(mapping).forEach(([id, value]) => {
    const node = document.querySelector(`#${id}`);
    if (node) node.value = value;
  });
}

function clearKbSource({ keepMode = true } = {}) {
  const source = document.querySelector("#kbSource");
  if (source) source.value = "";
  kbSourceSuffix = "";
  kbLastPreview = null;
  kbLastPreviewKey = "";
  showKbFileCard(null);
  const input = document.querySelector("#kbFile");
  if (input) input.value = "";
  const list = document.querySelector("#kbChunks");
  if (list) list.replaceChildren(emptyKbChunks("点击左侧的「预览块」按钮来加载预览"));
  const search = document.querySelector("#kbChunkSearch");
  if (search) {
    search.hidden = true;
    search.value = "";
  }
  if (!keepMode) setKbSourceMode("upload");
}

function fillKbSample() {
  setKbSourceMode("paste");
  const source = document.querySelector("#kbSource");
  if (source) source.value = SAMPLE_MARKDOWN;
  kbSourceSuffix = ".md";
  showKbFileCard(null);
  applyAutoStrategy(".md");
}

async function extractKbFile(file) {
  if (!file) return;
  if (file.size > MAX_UPLOAD_BYTES) {
    setKbStatus("单个文件不超过 15 MB", "error");
    return;
  }
  kbSourceSuffix = fileSuffix(file.name);
  const body = new FormData();
  body.append("file", file);
  setKbStatus(`正在抽取 ${file.name}…`);
  try {
    const payload = await kbRequest("/api/kb/extract", { method: "POST", body });
    const source = document.querySelector("#kbSource");
    if (source) source.value = payload.text || "";
    showKbFileCard({
      name: file.name,
      size: file.size,
      badge: BADGE_BY_SUFFIX[kbSourceSuffix] || "FILE"
    });
    const autoApplied = applyAutoStrategy(kbSourceSuffix);
    const label = selectedKbStrategy()?.label || kbSelectedStrategy;
    if (autoApplied) {
      setKbStatus(`已抽出 ${file.name}，按扩展名建议「${label}」。点其他策略卡可改。`);
    } else {
      setKbStatus(`已抽出 ${file.name}，使用当前策略「${label}」。`);
    }
  } catch (error) {
    setKbStatus(error.message, "error");
  } finally {
    const input = document.querySelector("#kbFile");
    if (input) input.value = "";
  }
}

function renderKbSummary() {
  const host = document.querySelector("#kbSummary");
  if (!host) return;
  const strategy = selectedKbStrategy();
  const body = kbPreviewBody();
  const rows = [
    ["文件", kbFileMeta?.name || (kbSourceMode === "paste" ? "粘贴文本" : "未命名")],
    ["切分策略", strategy?.label || kbSelectedStrategy],
    ["Parser", strategy?.parser || "—"],
    ["最大分段长度", String(body.chunk_size)],
    ["分段重叠长度", String(body.chunk_overlap)],
    ["Embedding", currentKbProfile() || "—"],
    ["预估块", `${kbLastPreview?.chunks?.length ?? 0}（尚未入库）`]
  ];
  host.replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      return [dt, dd];
    })
  );
}

function setKbStep(step) {
  kbUiStep = step;
  document.querySelector("#kbStepSource").hidden = step !== 1;
  document.querySelector("#kbStepChunk").hidden = step !== 2;
  document.querySelector("#kbStepDone").hidden = step !== 3;
  document.querySelectorAll("#kbSteps li").forEach(item => {
    item.classList.toggle("active", Number(item.dataset.kbStep) === step);
  });
}

async function goKbStep(step) {
  if (step === 2 && !kbSourceText()) {
    setKbStatus("请先上传文件或粘贴文本。", "error");
    return;
  }
  if (step === 3) {
    const currentKey = kbRequestSettings(kbPreviewBody());
    if (!kbLastPreview || currentKey !== kbLastPreviewKey) {
      setKbStatus("请先点击「预览块」，按当前分段设置请求后再进入下一步。", "error");
      return;
    }
  }
  setKbStep(step);
  if (step === 3) renderKbSummary();
}

async function openKbWorkspace() {
  if (!window.matrixAuth?.user()) return;
  try {
    await loadKbCatalog();
    if (!kbOpened) {
      kbOpened = true;
      setKbStep(1);
    }
  } catch (error) {
    setKbStatus(error.message, "error");
  }
}

function bindDropzone() {
  const drop = document.querySelector("#kbDrop");
  const input = document.querySelector("#kbFile");
  if (!drop || !input) return;
  const pick = () => input.click();
  drop.addEventListener("click", event => {
    if (event.target === input || event.target.closest("#kbPickFile")) return;
    pick();
  });
  document.querySelector("#kbPickFile")?.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    pick();
  });
  ["dragenter", "dragover"].forEach(type => {
    drop.addEventListener(type, event => {
      event.preventDefault();
      drop.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach(type => {
    drop.addEventListener(type, event => {
      event.preventDefault();
      drop.classList.remove("dragover");
    });
  });
  drop.addEventListener("drop", event => {
    const file = event.dataTransfer?.files && event.dataTransfer.files[0];
    extractKbFile(file).catch(error => setKbStatus(error.message, "error"));
  });
}

function bindKbWorkspace() {
  if (kbBound) return;
  kbBound = true;
  document.querySelector("#kbModeUpload")?.addEventListener("click", () => setKbSourceMode("upload"));
  document.querySelector("#kbModePaste")?.addEventListener("click", () => setKbSourceMode("paste"));
  document.querySelector("#kbPreviewBtn")?.addEventListener("click", () => {
    runKbPreview().catch(error => setKbStatus(error.message, "error"));
  });
  document.querySelector("#kbSampleBtn")?.addEventListener("click", () => {
    fillKbSample();
  });
  document.querySelector("#kbFile")?.addEventListener("change", event => {
    const file = event.target.files && event.target.files[0];
    extractKbFile(file).catch(error => setKbStatus(error.message, "error"));
  });
  document.querySelector("#kbFileRemove")?.addEventListener("click", () => {
    clearKbSource();
    setKbStatus("");
  });
  document.querySelector("#kbEmbedding")?.addEventListener("change", () => {
    persistKbProfile(currentKbProfile());
  });
  document.querySelector("#kbStrategy")?.addEventListener("change", event => {
    selectKbStrategy(event.target.value, { fromUser: true });
  });
  document.querySelector("#kbNextToChunk")?.addEventListener("click", () => {
    goKbStep(2).catch(error => setKbStatus(error.message, "error"));
  });
  document.querySelector("#kbBackToSource")?.addEventListener("click", () => setKbStep(1));
  document.querySelector("#kbNextToDone")?.addEventListener("click", () => {
    goKbStep(3).catch(error => setKbStatus(error.message, "error"));
  });
  document.querySelector("#kbBackToChunk")?.addEventListener("click", () => setKbStep(2));
  document.querySelector("#kbRestart")?.addEventListener("click", () => {
    clearKbSource({ keepMode: false });
    setKbStatus("");
    setKbStep(1);
  });
  document.querySelector("#kbResetParams")?.addEventListener("click", () => {
    resetKbParams();
    setKbStatus("已重置参数，点击「预览块」后生效。");
  });
  document.querySelector("#kbChunkSearch")?.addEventListener("input", filterKbChunks);
  bindDropzone();
  fillKbStrategies({ default: "sentence", strategies: FALLBACK_STRATEGIES });
  fillKbProfiles(FALLBACK_PROFILES);
}

bindKbWorkspace();
window.addEventListener("matrix-auth-changed", () => {
  if (document.body.dataset.workspace === "kb") {
    openKbWorkspace().catch(error => setKbStatus(error.message, "error"));
  }
});
window.matrixKb = { open: openKbWorkspace };
