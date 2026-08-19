const PROFILE_KEY = "matrix.kb.embedding_profile_id";
const STRATEGY_KEY = "matrix.kb.chunk_strategy";
const RECALL_HISTORY_KEY = "matrix.kb.recall_history";
const DOC_STATUS = { ready: "就绪", ingesting: "处理中", failed: "失败" };
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
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
let kbSplitTick = 0;
let kbExtractTick = 0;
let kbIngestTick = 0;
let kbReindexTick = 0;
let kbReindexing = false;
let kbAddSegTick = 0;
let kbLastPreview = null;
let kbLastPreviewKey = "";
let kbFileMeta = null;
let kbOpened = false;
let kbBound = false;
let kbView = "docs";
let kbDocuments = [];
let kbOpenDoc = null;
let kbOpenChunks = [];
let kbSegNotice = { chunkId: "", text: "", kind: "" };
let kbUploadFile = null;
let kbDraftDoc = null;
let kbIngestedDoc = null;
let kbProfilesPromise = null;

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
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(kbErrorText(payload, "请求失败"));
  }
  return payload;
}

function setKbStatus(text, kind = "") {
  const message = String(text || "").trim();
  ["#kbStatus", "#kbChunkStatus", "#kbDoneStatus"].forEach(sel => {
    const node = document.querySelector(sel);
    if (!node) return;
    node.hidden = !message;
    node.textContent = message;
    node.dataset.kind = kind;
  });
}

function setNamedStatus(id, text, kind = "") {
  const node = document.querySelector(id);
  if (!node) return;
  const message = String(text || "").trim();
  node.hidden = !message;
  node.textContent = message;
  node.dataset.kind = kind;
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

function fillProfileSelect(select, profiles, preferred, fallback) {
  if (!select) return;
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
  let next = preferred || previous || fallback;
  if (!ids.has(next)) next = fallback;
  if (ids.has(next)) select.value = next;
}

function fillKbProfiles(payload) {
  const profiles = payload.profiles || [];
  const remembered = rememberedKbProfile();
  fillProfileSelect(
    document.querySelector("#kbEmbedding"),
    profiles,
    remembered,
    payload.default
  );
  const workspace = currentKbProfile();
  if (remembered && remembered !== workspace) {
    setNamedStatus("#kbDocsStatus", `当前模型已不在列表，已回到 ${payload.default}。`, "warn");
  }
  persistKbProfile(workspace);
  fillProfileSelect(
    document.querySelector("#kbReindexProfile"),
    profiles,
    kbOpenDoc?.embedding_profile_id || workspace,
    payload.default
  );
  syncKbChrome();
}

function kbPreviewBody() {
  const strategy = currentKbStrategyId();
  kbSelectedStrategy = strategy;
  persistKbStrategy(strategy);
  if (strategy === "semantic") persistKbProfile(currentKbProfile());
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
  card.dataset.index = String(index);
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

function kbSplitBusyHint() {
  if (kbSelectedStrategy === "semantic") {
    return "语义切分要调 embedding，可能要等一会儿。";
  }
  const label = selectedKbStrategy()?.label || "当前策略";
  return `正在按「${label}」切开全文。`;
}

function renderKbSplitBusy() {
  const list = document.querySelector("#kbChunks");
  const notes = document.querySelector("#kbNotes");
  const search = document.querySelector("#kbChunkSearch");
  const meta = document.querySelector("#kbPreviewMeta");
  const title = document.querySelector("#kbPreviewTitle");
  if (title) title.textContent = kbFileMeta?.name || selectedKbStrategy()?.label || "预览";
  if (meta) meta.textContent = "正在切分…";
  if (search) search.hidden = true;
  if (notes) notes.hidden = true;
  if (!list) return;
  const panel = document.createElement("div");
  panel.className = "kb-split-panel";
  panel.id = "kbSplitPanel";
  panel.setAttribute("role", "status");
  panel.setAttribute("aria-live", "polite");
  panel.innerHTML = `
    <span class="kb-split-spinner" aria-hidden="true"></span>
    <strong>正在切分<span class="kb-dots" aria-hidden="true"><i></i><i></i><i></i></span></strong>
    <p class="kb-split-elapsed" id="kbSplitElapsed">开始切分</p>
    <p class="kb-hint" id="kbSplitHint"></p>
  `;
  const hint = panel.querySelector("#kbSplitHint");
  if (hint) hint.textContent = kbSplitBusyHint();
  list.replaceChildren(panel);
  list.setAttribute("aria-busy", "true");
}

function startKbSplitBusy() {
  stopKbSplitBusy();
  const started = Date.now();
  const previewBtn = document.querySelector("#kbPreviewBtn");
  const resetBtn = document.querySelector("#kbResetParams");
  const nextBtn = document.querySelector("#kbNextToDone");
  if (previewBtn) previewBtn.disabled = true;
  if (resetBtn) resetBtn.disabled = true;
  if (nextBtn) nextBtn.disabled = true;
  document.querySelector("#kbStepChunk")?.classList.add("is-splitting");
  document.querySelector(".kb-preview")?.classList.add("is-splitting");
  const status = document.querySelector("#kbChunkStatus");
  if (status) {
    status.hidden = true;
    status.textContent = "";
    status.dataset.kind = "";
  }
  renderKbSplitBusy();
  const tick = () => {
    const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const elapsed = document.querySelector("#kbSplitElapsed");
    if (elapsed) elapsed.textContent = secs ? `已用时 ${secs} 秒` : "开始切分";
    const meta = document.querySelector("#kbPreviewMeta");
    if (meta) meta.textContent = secs ? `正在切分 · ${secs}s` : "正在切分…";
  };
  tick();
  kbSplitTick = setInterval(tick, 250);
}

function stopKbSplitBusy() {
  if (kbSplitTick) {
    clearInterval(kbSplitTick);
    kbSplitTick = 0;
  }
  const previewBtn = document.querySelector("#kbPreviewBtn");
  const resetBtn = document.querySelector("#kbResetParams");
  const nextBtn = document.querySelector("#kbNextToDone");
  if (previewBtn) previewBtn.disabled = false;
  if (resetBtn) resetBtn.disabled = false;
  if (nextBtn) nextBtn.disabled = false;
  document.querySelector("#kbStepChunk")?.classList.remove("is-splitting");
  document.querySelector(".kb-preview")?.classList.remove("is-splitting");
  document.querySelector("#kbChunks")?.removeAttribute("aria-busy");
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

function bringChunkHitsToStart(list) {
  if (!list) return;
  list.scrollTop = 0;
  const preview = list.closest(".kb-preview");
  if (preview) preview.scrollTop = 0;
}

function filterKbChunks() {
  const list = document.querySelector("#kbChunks");
  const query = (document.querySelector("#kbChunkSearch")?.value || "").trim().toLowerCase();
  const cards = [...(list?.querySelectorAll(".kb-chunk") || [])];
  if (!list || !cards.length) return;
  if (!query) {
    cards
      .sort((a, b) => Number(a.dataset.index) - Number(b.dataset.index))
      .forEach(card => {
        card.hidden = false;
        card.classList.remove("is-hit", "is-miss");
        list.append(card);
      });
    return;
  }
  const hits = [];
  const misses = [];
  cards.forEach(card => {
    const hit = (card.dataset.search || "").toLowerCase().includes(query);
    card.hidden = false;
    card.classList.toggle("is-hit", hit);
    card.classList.toggle("is-miss", !hit);
    (hit ? hits : misses).push(card);
  });
  list.append(...hits, ...misses);
  bringChunkHitsToStart(list);
}

async function runKbPreview() {
  if (!kbSourceText()) {
    setKbStatus("请先粘贴文本或上传文件。", "error");
    return false;
  }
  const seq = (kbPreviewSeq += 1);
  startKbSplitBusy();
  const body = kbPreviewBody();
  try {
    const payload = await kbRequest("/api/kb/preview-chunks", {
      method: "POST",
      body: JSON.stringify(body)
    });
    if (seq !== kbPreviewSeq) return false;
    stopKbSplitBusy();
    kbLastPreviewKey = kbRequestSettings(body);
    renderKbChunks(payload);
    setKbStatus(payload.notes ? payload.notes : `已切出 ${payload.chunks.length} 块，全部列出。`);
    return true;
  } catch (error) {
    if (seq !== kbPreviewSeq) return false;
    stopKbSplitBusy();
    if (kbLastPreview) {
      renderKbChunks(kbLastPreview);
    } else {
      const list = document.querySelector("#kbChunks");
      if (list) list.replaceChildren(emptyKbChunks("切分失败。改策略或参数后再点「预览块」。"));
      const meta = document.querySelector("#kbPreviewMeta");
      if (meta) meta.textContent = "切分失败";
    }
    setKbStatus(error.message, "error");
    return false;
  } finally {
    if (seq === kbPreviewSeq) stopKbSplitBusy();
  }
}

async function loadKbCatalog() {
  fillKbStrategies({ default: "sentence", strategies: FALLBACK_STRATEGIES });
  try {
    const strategies = await kbRequest("/api/kb/chunk-strategies");
    fillKbStrategies(strategies);
  } catch (error) {
    setKbStatus(`切分策略列表未拉到，已用六张默认卡。${error.message}`, "warn");
  }
}

function ensureKbProfiles() {
  if (!kbProfilesPromise) {
    kbProfilesPromise = (async () => {
      try {
        const profiles = await kbRequest("/api/kb/embedding-profiles");
        fillKbProfiles(profiles);
      } catch (error) {
        fillKbProfiles(FALLBACK_PROFILES);
        setKbStatus(`模型列表暂不可用，已用本地默认。${error.message}`, "warn");
      }
    })();
  }
  return kbProfilesPromise;
}

function kbProfileNeeded() {
  return kbView === "recall" || (kbView === "wizard" && kbUiStep >= 2);
}

function updateKbHeadNote() {
  const note = document.querySelector("#kbHeadNote");
  if (!note) return;
  const notes = {
    docs: "先导入或打开文档。向量模型在分段入库和召回测试时再选。",
    wizard:
      kbUiStep < 2
        ? "这一步只选数据源，不选向量模型。"
        : kbUiStep === 2
          ? "在左侧选择入库模型。改模型会丢掉未保存预览。"
          : "当前模型只约束本次预览和新建。改模型会丢掉未保存预览。",
    doc: "分段改删锁定文档徽章上的模型，不跟顶栏走。",
    recall: "检索必须带当前模型，不会和其他模型的文档融合。"
  };
  note.textContent = notes[kbView] || notes.docs;
}

function placeKbEmbedding() {
  const select = document.querySelector("#kbEmbedding");
  const head = document.querySelector("#kbEmbedMountHead");
  const step2 = document.querySelector("#kbEmbedMountStep2");
  if (!select) return;
  const inStep2 = kbView === "wizard" && kbUiStep === 2;
  const target = inStep2 ? step2 : head;
  if (target && select.parentElement !== target) {
    target.appendChild(select);
  }
}

function syncKbProfileChrome() {
  const show = kbProfileNeeded();
  const inStep2 = kbView === "wizard" && kbUiStep === 2;
  const wrap = document.querySelector("#kbProfileWrap");
  const chip = document.querySelector("#kbSideProfile");
  if (wrap) wrap.hidden = !show || inStep2;
  if (chip) chip.hidden = !show || inStep2;
  placeKbEmbedding();
  updateKbHeadNote();
  if (show) {
    ensureKbProfiles().then(() => syncKbChrome());
    return;
  }
  syncKbChrome();
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
  if (meta?.file) kbUploadFile = meta.file;
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
  kbUploadFile = null;
  kbDraftDoc = null;
  kbIngestedDoc = null;
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

function startKbExtractBusy(fileName) {
  stopKbExtractBusy();
  const started = Date.now();
  const drop = document.querySelector("#kbDrop");
  const busy = document.querySelector("#kbExtractBusy");
  const nameNode = document.querySelector("#kbExtractName");
  if (drop) {
    drop.hidden = false;
    drop.classList.add("is-extracting");
  }
  if (busy) busy.hidden = false;
  if (nameNode) nameNode.textContent = fileName || "";
  ["#kbNextToChunk", "#kbModeUpload", "#kbModePaste", "#kbPickFile", "#kbSampleBtn"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = true;
  });
  const status = document.querySelector("#kbStatus");
  if (status) {
    status.hidden = true;
    status.textContent = "";
    status.dataset.kind = "";
  }
  const tick = () => {
    const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const elapsed = document.querySelector("#kbExtractElapsed");
    if (elapsed) elapsed.textContent = secs ? `已用时 ${secs} 秒` : "开始抽取";
  };
  tick();
  kbExtractTick = setInterval(tick, 250);
}

function stopKbExtractBusy() {
  if (kbExtractTick) {
    clearInterval(kbExtractTick);
    kbExtractTick = 0;
  }
  document.querySelector("#kbDrop")?.classList.remove("is-extracting");
  const busy = document.querySelector("#kbExtractBusy");
  if (busy) busy.hidden = true;
  ["#kbNextToChunk", "#kbModeUpload", "#kbModePaste", "#kbPickFile", "#kbSampleBtn"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = false;
  });
}

async function extractKbFile(file) {
  if (!file) return;
  if (file.size > MAX_UPLOAD_BYTES) {
    setKbStatus("单个文件不超过 50 MB", "error");
    return;
  }
  kbSourceSuffix = fileSuffix(file.name);
  const body = new FormData();
  body.append("file", file);
  startKbExtractBusy(file.name);
  try {
    const payload = await kbRequest("/api/kb/extract", { method: "POST", body });
    stopKbExtractBusy();
    const source = document.querySelector("#kbSource");
    if (source) source.value = payload.text || "";
    showKbFileCard({
      name: file.name,
      size: file.size,
      badge: BADGE_BY_SUFFIX[kbSourceSuffix] || "FILE",
      file
    });
    const autoApplied = applyAutoStrategy(kbSourceSuffix);
    const label = selectedKbStrategy()?.label || kbSelectedStrategy;
    if (autoApplied) {
      setKbStatus(`已抽出 ${file.name}，按扩展名建议「${label}」。点其他策略卡可改。`);
    } else {
      setKbStatus(`已抽出 ${file.name}，使用当前策略「${label}」。`);
    }
  } catch (error) {
    stopKbExtractBusy();
    setKbStatus(error.message, "error");
  } finally {
    stopKbExtractBusy();
    const input = document.querySelector("#kbFile");
    if (input) input.value = "";
  }
}

function renderKbSummary() {
  const host = document.querySelector("#kbSummary");
  if (!host) return;
  const ingested = Boolean(kbIngestedDoc);
  const specTitle = document.querySelector("#kbSpecTitle");
  if (specTitle) specTitle.textContent = ingested ? "已按以下配置入库" : "将按以下配置入库";
  const strategy = selectedKbStrategy();
  const body = kbPreviewBody();
  const previewCount = kbLastPreview?.chunks?.length ?? 0;
  const storedCount = kbIngestedDoc?.chunk_count ?? previewCount;
  const rows = [
    ["文件", kbFileMeta?.name || (kbSourceMode === "paste" ? "粘贴文本" : "未命名")],
    ["切分策略", strategy?.label || kbSelectedStrategy],
    ["最大分段长度", String(body.chunk_size)],
    ["文本预处理", "已清洗控制符与多余空白"],
    ["索引方式", "高质量"],
    ["检索设置", "向量检索"],
    ["Embedding", kbIngestedDoc?.embedding_profile_id || currentKbProfile() || "—"],
    ingested ? ["已入库块", String(storedCount)] : ["预估块", `${previewCount}（尚未入库）`]
  ];
  host.replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      if (key === "索引方式" || key === "Embedding") dd.className = "kb-dd-gem";
      if (key === "检索设置") dd.className = "kb-dd-search";
      return [dt, dd];
    })
  );
  renderKbNextSteps(ingested);
}

function renderKbNextSteps(ingested) {
  const list = document.querySelector("#kbNextSteps");
  if (!list) return;
  const items = ingested
    ? [
        "打开文档，检查、停用或删除某一分段。",
        "换模型必须确认重切：会删除旧切片记录，再写入新块。",
        "也可以去做召回测试。"
      ]
    : [
        "确认名称，再点「保存并处理」才会写入。",
        "入库完成后，可以管理分段或停用某一块。",
        "也可以去做召回测试。"
      ];
  list.replaceChildren(
    ...items.map(text => {
      const li = document.createElement("li");
      li.textContent = text;
      return li;
    })
  );
}

function setKbStep(step) {
  kbUiStep = step;
  document.querySelector("#kbStepSource").hidden = step !== 1;
  document.querySelector("#kbStepChunk").hidden = step !== 2;
  document.querySelector("#kbStepDone").hidden = step !== 3;
  document.querySelectorAll("#kbSteps li").forEach(item => {
    const n = Number(item.dataset.kbStep);
    item.classList.toggle("active", n === step);
    item.classList.toggle("done", n < step);
  });
  syncKbProfileChrome();
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
  if (step === 3) {
    kbIngestedDoc = null;
    renderKbSummary();
    const title = document.querySelector("#kbIngestTitle");
    if (title && !title.value) {
      title.value = kbFileMeta?.name || "未命名文档";
    }
    const go = document.querySelector("#kbGoDocs");
    const ingest = document.querySelector("#kbIngestBtn");
    if (go) go.hidden = true;
    if (ingest) ingest.hidden = false;
    const hint = document.querySelector("#kbDoneHint");
    if (hint) hint.textContent = "切片仅用于预览，尚未写入知识库。点击「保存并处理」后才会嵌入入库。";
    const doneTitle = document.querySelector("#kbDoneTitle");
    if (doneTitle) doneTitle.textContent = "预览已完成";
    stopKbIngestBusy();
    document.querySelector("#kbStepDone")?.classList.remove("is-ingested", "is-ingesting");
    setNamedStatus("#kbDoneStatus", "");
  }
}

async function openKbWorkspace() {
  if (!window.matrixAuth?.user()) return;
  try {
    await loadKbCatalog();
    if (!kbOpened) {
      kbOpened = true;
      setKbStep(1);
    }
    if (kbView === "docs" || kbView === "recall") {
      await loadKbDocuments();
    }
    showKbView(kbView === "wizard" ? "wizard" : kbView);
  } catch (error) {
    setNamedStatus("#kbDocsStatus", error.message, "error");
  }
}

function syncKbChrome() {
  const profile = currentKbProfile();
  const side = document.querySelector("#kbSideProfile");
  if (side) side.textContent = profile || "—";
  const recallBadge = document.querySelector("#kbRecallProfile");
  if (recallBadge) recallBadge.textContent = profile ? `向量检索 · ${profile}` : "向量检索";
  const stats = document.querySelector("#kbSideStats");
  if (stats) stats.textContent = `${kbDocuments.length} 文档`;
}

function showKbView(name) {
  kbView = name;
  const views = {
    docs: document.querySelector("#kbViewDocs"),
    wizard: document.querySelector("#kbViewWizard"),
    doc: document.querySelector("#kbViewDoc"),
    recall: document.querySelector("#kbViewRecall")
  };
  Object.entries(views).forEach(([key, node]) => {
    if (node) node.hidden = key !== name;
  });
  document.querySelectorAll("#kbSubnav [data-kb-view]").forEach(tab => {
    const on = tab.dataset.kbView === (name === "doc" || name === "wizard" ? "docs" : name);
    tab.classList.toggle("active", on);
  });
  if (name === "recall") renderRecallHistory();
  syncKbProfileChrome();
}

async function loadKbDocuments() {
  const payload = await kbRequest("/api/kb/documents");
  kbDocuments = payload.documents || [];
  renderKbDocuments();
}

function renderKbDocuments() {
  const list = document.querySelector("#kbDocList");
  const empty = document.querySelector("#kbDocEmpty");
  if (!list) return;
  const current = currentKbProfile();
  const filterWrap = document.querySelector("#kbFilterCurrentWrap");
  const onlyCurrent =
    Boolean(filterWrap && !filterWrap.hidden && document.querySelector("#kbFilterCurrent")?.checked);
  const rows = onlyCurrent
    ? kbDocuments.filter(item => item.embedding_profile_id === current)
    : kbDocuments;
  list.replaceChildren(
    ...rows.map(doc => {
      const row = document.createElement("li");
      row.className = "kb-doc-row";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "kb-doc-open";
      const ico = document.createElement("span");
      ico.className = "kb-doc-ico";
      ico.setAttribute("aria-hidden", "true");
      const text = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = doc.title || "未命名文档";
      const meta = document.createElement("small");
      const status = DOC_STATUS[doc.status] || doc.status;
      meta.textContent = `${doc.chunk_count} 分段 · ${doc.source === "upload" ? "上传" : "粘贴"} · ${status}`;
      text.append(title, meta);
      const badge = document.createElement("span");
      badge.className = `kb-badge${doc.embedding_profile_id === current ? "" : " warn"}`;
      badge.textContent = doc.embedding_profile_id;
      const live = document.createElement("span");
      live.className = `kb-live${doc.enabled ? " on" : ""}`;
      live.textContent = doc.enabled ? "可用" : "已停用";
      open.append(ico, text, badge, live);
      open.addEventListener("click", () => {
        openKbDocument(doc.doc_id).catch(error => {
          setNamedStatus("#kbDocsStatus", error.message, "error");
        });
      });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost kb-doc-delete";
      del.textContent = "删除";
      del.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        deleteKbDocument(doc).catch(error => {
          setNamedStatus("#kbDocsStatus", error.message, "error");
        });
      });
      row.append(open, del);
      return row;
    })
  );
  if (empty) empty.hidden = rows.length > 0;
  syncKbChrome();
  const other = kbDocuments.filter(item => item.embedding_profile_id !== current).length;
  if (onlyCurrent && other && !rows.length && kbDocuments.length) {
    setNamedStatus(
      "#kbDocsStatus",
      `当前模型下没有文档，另有 ${other} 篇用了其他模型。取消「只看当前模型」可查看。`
    );
  } else if (!kbDocuments.length) {
    setNamedStatus("#kbDocsStatus", "");
  }
}

function ingestFields() {
  const body = kbPreviewBody();
  return {
    title: document.querySelector("#kbIngestTitle")?.value.trim() || undefined,
    strategy: body.strategy,
    chunk_size: body.chunk_size,
    chunk_overlap: body.chunk_overlap,
    embedding_profile_id: currentKbProfile(),
    breakpoint_percentile_threshold: body.breakpoint_percentile_threshold,
    buffer_size: body.buffer_size,
    window_size: body.window_size,
    source_suffix: body.source_suffix
  };
}

async function ensureDraftDocument() {
  if (kbDraftDoc?.doc_id) return kbDraftDoc;
  const fields = ingestFields();
  kbDraftDoc = await kbRequest("/api/kb/documents", {
    method: "POST",
    body: JSON.stringify({
      title: fields.title || kbFileMeta?.name || "未命名文档",
      embedding_profile_id: fields.embedding_profile_id,
      strategy: fields.strategy,
      chunk_size: fields.chunk_size,
      chunk_overlap: fields.chunk_overlap,
      window_size: fields.window_size,
      breakpoint_percentile_threshold: fields.breakpoint_percentile_threshold,
      buffer_size: fields.buffer_size,
      source_suffix: fields.source_suffix,
      source: kbUploadFile ? "upload" : "paste"
    })
  });
  return kbDraftDoc;
}

async function ingestPreviewChunk(index) {
  const chunk = kbLastPreview?.chunks?.[index];
  if (!chunk?.text) {
    setKbStatus("请先预览再入库此块。", "error");
    return;
  }
  const doc = await ensureDraftDocument();
  await kbRequest(`/api/kb/documents/${doc.doc_id}/chunks`, {
    method: "POST",
    body: JSON.stringify({
      text: chunk.text,
      window: chunk.window,
      header_path: chunk.header_path,
      element_type: chunk.element_type,
      ordinal: index
    })
  });
  setKbStatus(`已入库第 ${index + 1} 块到「${doc.title}」。`);
}

function startKbIngestBusy() {
  stopKbIngestBusy();
  const started = Date.now();
  const box = document.querySelector("#kbIngestProgress");
  if (box) box.hidden = false;
  document.querySelector("#kbStepDone")?.classList.add("is-ingesting");
  document.querySelector("#kbStepDone")?.classList.remove("is-ingested");
  const label = document.querySelector("#kbIngestLabel");
  if (label) label.textContent = "正在写入并嵌入";
  const nameNode = document.querySelector("#kbIngestFileName");
  if (nameNode) {
    nameNode.textContent =
      kbFileMeta?.name || document.querySelector("#kbIngestTitle")?.value || "粘贴文本";
  }
  const status = document.querySelector("#kbDoneStatus");
  if (status) {
    status.hidden = true;
    status.textContent = "";
    status.dataset.kind = "";
  }
  ["#kbIngestBtn", "#kbBackToChunk", "#kbRestart"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = true;
  });
  const tick = () => {
    const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const elapsed = document.querySelector("#kbIngestElapsed");
    if (elapsed) elapsed.textContent = secs ? `已用时 ${secs} 秒` : "开始处理";
  };
  tick();
  kbIngestTick = setInterval(tick, 250);
}

function stopKbIngestBusy({ done = false } = {}) {
  if (kbIngestTick) {
    clearInterval(kbIngestTick);
    kbIngestTick = 0;
  }
  document.querySelector("#kbStepDone")?.classList.remove("is-ingesting");
  ["#kbIngestBtn", "#kbBackToChunk", "#kbRestart"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = false;
  });
  const box = document.querySelector("#kbIngestProgress");
  if (done) {
    document.querySelector("#kbStepDone")?.classList.add("is-ingested");
    const label = document.querySelector("#kbIngestLabel");
    if (label) label.textContent = "处理完成";
    const elapsed = document.querySelector("#kbIngestElapsed");
    if (elapsed) {
      elapsed.textContent =
        kbIngestedDoc?.chunk_count != null
          ? `已写入 ${kbIngestedDoc.chunk_count} 块`
          : "已写入知识库";
    }
    if (box) box.hidden = false;
    return;
  }
  if (box) box.hidden = true;
}

async function ingestFullDocument() {
  if (!kbSourceText()) {
    setKbStatus("没有可入库的正文。", "error");
    return;
  }
  const profile = currentKbProfile();
  if (!profile) {
    setKbStatus("请先选择当前模型。", "error");
    return;
  }
  const fields = ingestFields();
  const ingestBtn = document.querySelector("#kbIngestBtn");
  startKbIngestBusy();
  try {
    let doc;
    if (kbUploadFile) {
      const body = new FormData();
      body.append("file", kbUploadFile);
      Object.entries(fields).forEach(([key, value]) => {
        if (value != null && value !== "") body.append(key, String(value));
      });
      doc = await kbRequest("/api/kb/documents", { method: "POST", body });
    } else {
      doc = await kbRequest("/api/kb/documents", {
        method: "POST",
        body: JSON.stringify({
          ...fields,
          text: kbSourceText(),
          source: "paste"
        })
      });
    }
    kbIngestedDoc = doc;
    kbDraftDoc = doc;
    stopKbIngestBusy({ done: true });
    const doneTitle = document.querySelector("#kbDoneTitle");
    if (doneTitle) doneTitle.textContent = "知识库已创建";
    const hint = document.querySelector("#kbDoneHint");
    if (hint) hint.textContent = "分段已按当前模型写入。打开文档可管理分段；换模型重切会删除旧切片再写入新块。";
    if (ingestBtn) ingestBtn.hidden = true;
    const go = document.querySelector("#kbGoDocs");
    if (go) go.hidden = false;
    setKbStatus(`已入库 ${doc.chunk_count} 块。`);
    renderKbSummary();
  } catch (error) {
    stopKbIngestBusy();
    setKbStatus(error.message, "error");
  }
}

function renderDocMismatch() {
  const banner = document.querySelector("#kbDocMismatch");
  if (!banner || !kbOpenDoc) return;
  const current = currentKbProfile();
  const locked = kbOpenDoc.embedding_profile_id;
  if (current && locked && current !== locked) {
    banner.hidden = false;
    banner.textContent = `检索正使用 ${current}，本文档以 ${locked} 入库，当前检索看不到本文。分段改删仍用 ${locked}。`;
  } else {
    banner.hidden = true;
    banner.textContent = "";
  }
}

function renderKbDocMeta() {
  const host = document.querySelector("#kbDocMeta");
  if (!host || !kbOpenDoc) return;
  const strategyLabel =
    kbStrategies.find(item => item.id === kbOpenDoc.strategy)?.label || kbOpenDoc.strategy;
  const rows = [
    ["原文件", kbOpenDoc.filename || "粘贴文本", "file"],
    ["来源", kbOpenDoc.source === "upload" ? "文件上传" : "粘贴", ""],
    ["分段策略", strategyLabel, ""],
    ["最大分段长度", String(kbOpenDoc.chunk_size), ""],
    ["分段数", String(kbOpenDoc.chunk_count), ""],
    ["入库模型", kbOpenDoc.embedding_profile_id, ""],
    ["状态", DOC_STATUS[kbOpenDoc.status] || kbOpenDoc.status, ""]
  ];
  host.replaceChildren(
    ...rows.flatMap(([key, value, kind]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = key;
      dd.textContent = value;
      if (kind === "file") {
        dt.className = "kb-meta-file";
        dd.className = "kb-meta-file";
        dd.title = value;
      }
      return [dt, dd];
    })
  );
}

function renderOpenSegments() {
  const list = document.querySelector("#kbSegList");
  const count = document.querySelector("#kbSegCount");
  if (!list) return;
  const query = (document.querySelector("#kbSegSearch")?.value || "").trim().toLowerCase();
  const hits = [];
  const misses = [];
  kbOpenChunks.forEach(chunk => {
    const hay = `${chunk.text || ""} ${chunk.header_path || ""}`.toLowerCase();
    if (!query || hay.includes(query)) hits.push(chunk);
    else misses.push(chunk);
  });
  const ordered = query ? [...hits, ...misses] : kbOpenChunks;
  if (count) {
    count.textContent = query
      ? `${hits.length} / ${kbOpenChunks.length} 分段`
      : `${kbOpenChunks.length} 分段`;
  }
  list.replaceChildren(
    ...ordered.map(chunk => {
      const card = renderStoredSegment(chunk);
      if (query) {
        card.classList.toggle("is-hit", hits.includes(chunk));
        card.classList.toggle("is-miss", !hits.includes(chunk));
      }
      return card;
    })
  );
  if (query) bringChunkHitsToStart(list);
}

function setKbSegNotice(chunkId, text, kind = "") {
  kbSegNotice = { chunkId: chunkId || "", text: String(text || "").trim(), kind };
  setNamedStatus("#kbDocStatus", kbSegNotice.text, kind);
}

function renderStoredSegment(chunk) {
  const card = document.createElement("article");
  card.className = `kb-chunk kb-seg${chunk.enabled ? "" : " is-off"}`;
  const head = document.createElement("header");
  const grip = document.createElement("span");
  grip.className = "kb-seg-grip";
  grip.setAttribute("aria-hidden", "true");
  const ord = document.createElement("span");
  ord.className = "kb-ord";
  ord.textContent = `分段-${String((chunk.ordinal || 0) + 1).padStart(2, "0")}`;
  const chars = document.createElement("span");
  chars.className = "kb-tag";
  chars.textContent = `${String(chunk.text || "").length} 字符`;
  head.append(grip, ord, chars);
  if (chunk.header_path) {
    const path = document.createElement("span");
    path.className = "kb-tag";
    path.textContent = chunk.header_path;
    head.append(path);
  }
  if (chunk.diverged) {
    const flag = document.createElement("span");
    flag.className = "kb-tag";
    flag.textContent = "已改正文";
    head.append(flag);
  }
  const live = document.createElement("span");
  live.className = `kb-live${chunk.enabled ? " on" : ""}`;
  live.textContent = chunk.enabled ? "已启用" : "已停用";
  head.append(live);
  const preview = document.createElement("p");
  preview.className = "kb-chunk-text";
  preview.textContent = chunk.text || "";
  const editor = document.createElement("textarea");
  editor.className = "kb-seg-edit";
  editor.rows = 4;
  editor.value = chunk.text || "";
  const note = document.createElement("p");
  note.className = "kb-seg-status";
  if (kbSegNotice.chunkId === chunk.chunk_id && kbSegNotice.text) {
    note.textContent = kbSegNotice.text;
    note.dataset.kind = kbSegNotice.kind || "";
  } else {
    note.hidden = true;
  }
  const actions = document.createElement("div");
  actions.className = "kb-chunk-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "send-btn";
  edit.textContent = "编辑";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "send-btn kb-seg-save";
  save.textContent = "保存";
  save.hidden = true;
  const setEditing = on => {
    card.classList.toggle("is-editing", on);
    edit.textContent = on ? "收起" : "编辑";
    edit.className = on ? "ghost" : "send-btn";
    save.hidden = !on;
    if (!on) editor.value = chunk.text || "";
  };
  edit.addEventListener("click", () => {
    const on = !card.classList.contains("is-editing");
    setEditing(on);
    if (on) editor.focus();
  });
  save.addEventListener("click", () => {
    const next = editor.value;
    if (!String(next || "").trim()) {
      setKbSegNotice(chunk.chunk_id, "分段正文不能为空。", "error");
      note.hidden = false;
      note.textContent = "分段正文不能为空。";
      note.dataset.kind = "error";
      return;
    }
    if (next === (chunk.text || "")) {
      setKbSegNotice(chunk.chunk_id, "正文没有改动，未写入。", "warn");
      note.hidden = false;
      note.textContent = "正文没有改动，未写入。";
      note.dataset.kind = "warn";
      return;
    }
    save.disabled = true;
    save.textContent = "保存中…";
    patchKbChunk(chunk.chunk_id, { text: next }).catch(error => {
      save.disabled = false;
      save.textContent = "保存";
      setKbSegNotice(chunk.chunk_id, error.message, "error");
    });
  });
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "ghost";
  toggle.textContent = chunk.enabled ? "停用" : "启用";
  toggle.addEventListener("click", () => {
    toggle.disabled = true;
    patchKbChunk(chunk.chunk_id, { enabled: !chunk.enabled }).catch(error => {
      toggle.disabled = false;
      setKbSegNotice(chunk.chunk_id, error.message, "error");
    });
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "删除";
  remove.addEventListener("click", () => {
    if (!window.confirm("删除这一分段？检索将立刻看不到它。")) return;
    remove.disabled = true;
    deleteKbChunk(chunk.chunk_id).catch(error => {
      remove.disabled = false;
      setKbSegNotice(chunk.chunk_id, error.message, "error");
    });
  });
  actions.append(edit, save, toggle, remove);
  if (kbReindexing) {
    [edit, save, toggle, remove, editor].forEach(node => {
      node.disabled = true;
    });
  }
  card.append(head, preview, editor, note, actions);
  return card;
}

function patchKbChunkMessage(body) {
  if (body.text != null) return "已保存分段正文。";
  if (body.enabled === false) return "已停用该分段。";
  if (body.enabled === true) return "已启用该分段。";
  return "已保存。";
}

async function patchKbChunk(chunkId, body) {
  if (!kbOpenDoc) throw new Error("没有打开的文档。");
  if (kbReindexing) throw new Error("正在重建切片，请稍后再改。");
  await kbRequest(
    `/api/kb/documents/${kbOpenDoc.doc_id}/chunks/${chunkId}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  const listed = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}/chunks`);
  kbOpenChunks = listed.chunks || [];
  kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`);
  setKbSegNotice(chunkId, patchKbChunkMessage(body));
  renderOpenSegments();
  renderKbDocMeta();
}

async function deleteKbChunk(chunkId) {
  if (!kbOpenDoc) throw new Error("没有打开的文档。");
  if (kbReindexing) throw new Error("正在重建切片，请稍后再改。");
  await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}/chunks/${chunkId}`, {
    method: "DELETE"
  });
  kbOpenChunks = kbOpenChunks.filter(item => item.chunk_id !== chunkId);
  kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`);
  setKbSegNotice("", "已删除该分段。");
  renderOpenSegments();
  renderKbDocMeta();
}

async function openKbDocument(docId) {
  await ensureKbProfiles();
  kbSegNotice = { chunkId: "", text: "", kind: "" };
  kbOpenDoc = await kbRequest(`/api/kb/documents/${docId}`);
  const payload = await kbRequest(`/api/kb/documents/${docId}/chunks`);
  kbOpenChunks = payload.chunks || [];
  const title = document.querySelector("#kbDocTitle");
  if (title) title.value = kbOpenDoc.title || "";
  const badge = document.querySelector("#kbDocProfile");
  if (badge) badge.textContent = kbOpenDoc.embedding_profile_id;
  const enabled = document.querySelector("#kbDocEnabled");
  if (enabled) enabled.checked = Boolean(kbOpenDoc.enabled);
  const download = document.querySelector("#kbDocDownload");
  if (download) download.hidden = !kbOpenDoc.artifact_id;
  const reindex = document.querySelector("#kbReindexProfile");
  if (reindex) reindex.value = kbOpenDoc.embedding_profile_id;
  renderKbDocMeta();
  renderOpenSegments();
  renderDocMismatch();
  showKbView("doc");
}

async function saveKbDocTitle() {
  if (!kbOpenDoc) return;
  const title = document.querySelector("#kbDocTitle")?.value.trim();
  if (!title || title === kbOpenDoc.title) return;
  kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`, {
    method: "PATCH",
    body: JSON.stringify({ title })
  });
  renderKbDocMeta();
}

async function toggleKbDocEnabled() {
  if (!kbOpenDoc) return;
  const enabled = Boolean(document.querySelector("#kbDocEnabled")?.checked);
  kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled })
  });
  renderKbDocMeta();
}

async function deleteKbDocument(doc = kbOpenDoc) {
  if (!doc?.doc_id) return;
  const title = doc.title || "这篇文档";
  if (
    !window.confirm(
      `删除「${title}」？文档、全部分段和向量都会清掉，列表和检索都看不到。`
    )
  ) {
    return;
  }
  await kbRequest(`/api/kb/documents/${doc.doc_id}`, { method: "DELETE" });
  if (kbOpenDoc?.doc_id === doc.doc_id) {
    kbOpenDoc = null;
    kbOpenChunks = [];
  }
  await loadKbDocuments();
  showKbView("docs");
  setNamedStatus("#kbDocsStatus", `已删除「${title}」及其分段。`);
}

async function downloadKbFile() {
  if (!kbOpenDoc?.artifact_id) return;
  const response = await fetch(`/api/kb/documents/${kbOpenDoc.doc_id}/file`, {
    headers: kbAuthHeaders()
  });
  if (!response.ok) {
    setNamedStatus("#kbDocStatus", "没有原件可下载。", "error");
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = kbOpenDoc.filename || "file";
  link.click();
  URL.revokeObjectURL(url);
}

async function addKbSegment() {
  if (!kbOpenDoc || kbReindexing) return;
  const saveBtn = document.querySelector("#kbAddSegSave");
  if (saveBtn?.disabled) return;
  const box = document.querySelector("#kbAddSegText");
  const text = box?.value.trim();
  if (!text) {
    setNamedStatus("#kbDocStatus", "请填写新分段正文。", "error");
    return;
  }
  startKbAddSegBusy();
  try {
    const created = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}/chunks`, {
      method: "POST",
      body: JSON.stringify({
        text,
        embedding_profile_id: kbOpenDoc.embedding_profile_id
      })
    });
    kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`);
    if (box) box.value = "";
    const host = document.querySelector("#kbAddSegBox");
    if (host) host.hidden = true;
    const search = document.querySelector("#kbSegSearch");
    if (search) search.value = "";
    kbOpenChunks = [created, ...kbOpenChunks.filter(item => item.chunk_id !== created.chunk_id)];
    renderOpenSegments();
    const first = document.querySelector("#kbSegList .kb-chunk");
    first?.classList.add("is-hit");
    bringChunkHitsToStart(document.querySelector("#kbSegList"));
    renderKbDocMeta();
    setNamedStatus("#kbDocStatus", "已入库新分段。");
  } catch (error) {
    setNamedStatus("#kbDocStatus", error.message, "error");
  } finally {
    stopKbAddSegBusy();
  }
}

function startKbAddSegBusy() {
  stopKbAddSegBusy();
  const started = Date.now();
  const progress = document.querySelector("#kbAddSegProgress");
  if (progress) progress.hidden = false;
  ["#kbAddSegSave", "#kbAddSegCancel", "#kbAddSeg"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = true;
  });
  const area = document.querySelector("#kbAddSegText");
  if (area) area.disabled = true;
  const tick = () => {
    const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const elapsed = document.querySelector("#kbAddSegElapsed");
    if (elapsed) elapsed.textContent = secs ? `已用时 ${secs} 秒` : "开始处理";
  };
  tick();
  kbAddSegTick = setInterval(tick, 250);
}

function stopKbAddSegBusy() {
  if (kbAddSegTick) {
    clearInterval(kbAddSegTick);
    kbAddSegTick = 0;
  }
  const progress = document.querySelector("#kbAddSegProgress");
  if (progress) progress.hidden = true;
  ["#kbAddSegSave", "#kbAddSegCancel", "#kbAddSeg"].forEach(sel => {
    const node = document.querySelector(sel);
    if (node) node.disabled = false;
  });
  const area = document.querySelector("#kbAddSegText");
  if (area) area.disabled = false;
}

async function reindexKbDocument() {
  if (!kbOpenDoc) return;
  const next = document.querySelector("#kbReindexProfile")?.value;
  if (!next) return;
  if (next === kbOpenDoc.embedding_profile_id) {
    setNamedStatus("#kbDocStatus", "已经是这个模型。", "warn");
    return;
  }
  if (
    !window.confirm(
      `将删除「${kbOpenDoc.title}」的旧切片记录和向量，再用 ${next} 重切并写入新 chunk。旧块不可恢复。确定吗？`
    )
  ) {
    return;
  }
  const payload = {
    rechunk: true,
    embedding_profile_id: next
  };
  if (!kbOpenDoc.artifact_id) {
    payload.text = kbOpenChunks.map(item => item.text).join("\n\n");
  }
  startKbReindexBusy(next);
  try {
    kbOpenDoc = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
    const chunks = await kbRequest(`/api/kb/documents/${kbOpenDoc.doc_id}/chunks`);
    kbOpenChunks = chunks.chunks || [];
    const badge = document.querySelector("#kbDocProfile");
    if (badge) badge.textContent = kbOpenDoc.embedding_profile_id;
    renderKbDocMeta();
    renderOpenSegments();
    renderDocMismatch();
    stopKbReindexBusy({ done: true });
    setNamedStatus("#kbDocStatus", `已删除旧切片，并用 ${next} 写入新块。`);
  } catch (error) {
    stopKbReindexBusy();
    setNamedStatus("#kbDocStatus", error.message, "error");
  }
}

function startKbReindexBusy(profileId) {
  stopKbReindexBusy();
  const started = Date.now();
  const box = document.querySelector("#kbReindexProgress");
  const label = document.querySelector("#kbReindexLabel");
  const wrap = document.querySelector(".kb-reindex");
  if (box) box.hidden = false;
  if (label) label.textContent = `正在删除旧记录，并用 ${profileId} 写入新切片`;
  wrap?.classList.add("is-busy");
  setKbSegActionsLocked(true);
  const button = document.querySelector("#kbReindexBtn");
  const select = document.querySelector("#kbReindexProfile");
  if (button) button.disabled = true;
  if (select) select.disabled = true;
  const status = document.querySelector("#kbDocStatus");
  if (status) {
    status.hidden = true;
    status.textContent = "";
    status.dataset.kind = "";
  }
  const tick = () => {
    const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const elapsed = document.querySelector("#kbReindexElapsed");
    if (elapsed) elapsed.textContent = secs ? `已用时 ${secs} 秒` : "开始处理";
  };
  tick();
  kbReindexTick = setInterval(tick, 250);
}

function stopKbReindexBusy({ done = false } = {}) {
  if (kbReindexTick) {
    clearInterval(kbReindexTick);
    kbReindexTick = 0;
  }
  setKbSegActionsLocked(false);
  const wrap = document.querySelector(".kb-reindex");
  wrap?.classList.remove("is-busy");
  const button = document.querySelector("#kbReindexBtn");
  const select = document.querySelector("#kbReindexProfile");
  if (button) button.disabled = false;
  if (select) select.disabled = false;
  const box = document.querySelector("#kbReindexProgress");
  const label = document.querySelector("#kbReindexLabel");
  const elapsed = document.querySelector("#kbReindexElapsed");
  if (done) {
    wrap?.classList.add("is-done");
    if (label) label.textContent = "已删除旧记录并写入新切片";
    if (elapsed) elapsed.textContent = "旧块已从库中移除";
    if (box) box.hidden = false;
    return;
  }
  wrap?.classList.remove("is-done");
  if (box) box.hidden = true;
}

function setKbSegActionsLocked(locked) {
  kbReindexing = Boolean(locked);
  document.querySelector("#kbViewDoc")?.classList.toggle("is-reindexing", kbReindexing);
  if (kbReindexing) {
    document.querySelectorAll("#kbSegList .kb-seg.is-editing").forEach(card => {
      card.classList.remove("is-editing");
    });
  }
  document.querySelectorAll("#kbSegList .kb-chunk-actions button, #kbSegList .kb-seg-edit").forEach(node => {
    node.disabled = kbReindexing;
  });
  const add = document.querySelector("#kbAddSeg");
  if (add) add.disabled = kbReindexing;
}

function syncRecallButton() {
  const query = document.querySelector("#kbRecallQuery")?.value.trim() || "";
  const count = document.querySelector("#kbRecallCount");
  if (count) count.textContent = `${query.length}/200`;
  const button = document.querySelector("#kbRecallBtn");
  if (button) button.disabled = !query || !currentKbProfile();
}

async function runKbRecall() {
  const query = document.querySelector("#kbRecallQuery")?.value.trim() || "";
  const profile = currentKbProfile();
  if (!query || !profile) {
    setNamedStatus("#kbRecallStatus", "检索必须填写文本并选择当前模型。", "error");
    return;
  }
  const button = document.querySelector("#kbRecallBtn");
  if (button) button.disabled = true;
  setNamedStatus("#kbRecallStatus", "正在检索…");
  try {
    const payload = await kbRequest("/api/kb/search", {
      method: "POST",
      body: JSON.stringify({ query, embedding_profile_id: profile })
    });
    renderKbRecall(payload);
    pushRecallHistory(query);
  } catch (error) {
    setNamedStatus("#kbRecallStatus", error.message, "error");
  } finally {
    syncRecallButton();
  }
}

function recallHistory() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(RECALL_HISTORY_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter(item => typeof item === "string") : [];
  } catch (_) {
    return [];
  }
}

function pushRecallHistory(query) {
  const next = [query, ...recallHistory().filter(item => item !== query)].slice(0, 8);
  try {
    sessionStorage.setItem(RECALL_HISTORY_KEY, JSON.stringify(next));
  } catch (_) {}
  renderRecallHistory();
}

function renderRecallHistory() {
  const list = document.querySelector("#kbRecallHistory");
  const empty = document.querySelector("#kbRecallHistoryEmpty");
  if (!list) return;
  const items = recallHistory();
  list.replaceChildren(
    ...items.map(query => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = query;
      button.addEventListener("click", () => {
        const box = document.querySelector("#kbRecallQuery");
        if (box) box.value = query.slice(0, 200);
        syncRecallButton();
        runKbRecall().catch(error => {
          setNamedStatus("#kbRecallStatus", error.message, "error");
        });
      });
      row.append(button);
      return row;
    })
  );
  if (empty) empty.hidden = items.length > 0;
}

function kbRecallEmpty(text) {
  const wrap = document.createElement("div");
  wrap.className = "kb-empty-hero";
  const p = document.createElement("p");
  p.className = "kb-empty";
  p.textContent = text;
  wrap.append(p);
  return wrap;
}

function renderKbRecall(payload) {
  const host = document.querySelector("#kbRecallHits");
  if (!host) return;
  const hits = payload.hits || [];
  if (hits.length) {
    setNamedStatus("#kbRecallStatus", `命中 ${hits.length} 块。`);
    host.replaceChildren(
      ...hits.map((hit, index) => {
        const card = document.createElement("article");
        card.className = "kb-hit";
        const head = document.createElement("header");
        const ord = document.createElement("strong");
        ord.textContent = `命中 ${index + 1}`;
        const meta = document.createElement("small");
        const docTitle = kbDocuments.find(item => item.doc_id === hit.doc_id)?.title;
        meta.textContent = hit.header_path || docTitle || hit.chunk_id;
        head.append(ord, meta);
        if (hit.score != null) {
          const score = document.createElement("span");
          score.className = "kb-score";
          score.textContent = Number(hit.score).toFixed(3);
          head.append(score);
        }
        const text = document.createElement("p");
        text.className = "kb-chunk-text";
        text.textContent = hit.text;
        card.append(head, text);
        return card;
      })
    );
    return;
  }
  const other = payload.other_profile_doc_count || 0;
  let message = "没有命中。";
  if (payload.empty_reason === "library_empty") {
    message = "知识库还是空的，先导入文档。";
  } else if (payload.empty_reason === "no_docs_for_profile") {
    message = other
      ? `当前模型下没有可检索文档，另有 ${other} 篇用了其他模型。可在顶栏切换模型，而不是融合检索。`
      : "当前模型下没有可检索文档。";
  } else if (payload.profile_doc_count) {
    message = `没有命中。当前模型下有 ${payload.profile_doc_count} 篇文档。`;
  }
  host.replaceChildren(kbRecallEmpty(message));
  setNamedStatus("#kbRecallStatus", message, "warn");
}

function onWorkspaceProfileChange() {
  persistKbProfile(currentKbProfile());
  kbLastPreview = null;
  kbLastPreviewKey = "";
  kbDraftDoc = null;
  const list = document.querySelector("#kbChunks");
  if (list) list.replaceChildren(emptyKbChunks("当前模型已改，请重新点「预览块」。"));
  fillKbProfiles({
    default: currentKbProfile(),
    profiles: [...document.querySelectorAll("#kbEmbedding option")].map(item => item.value)
  });
  renderKbDocuments();
  renderDocMismatch();
  syncRecallButton();
  syncKbChrome();
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
    onWorkspaceProfileChange();
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
    setKbStatus("已恢复默认切分参数，点击「预览块」后生效。");
  });
  document.querySelector("#kbChunkSearch")?.addEventListener("input", filterKbChunks);
  document.querySelector("#kbImportBtn")?.addEventListener("click", () => {
    clearKbSource({ keepMode: false });
    setKbStep(1);
    showKbView("wizard");
  });
  document.querySelector("#kbWizardBack")?.addEventListener("click", () => {
    loadKbDocuments()
      .then(() => showKbView("docs"))
      .catch(error => setNamedStatus("#kbDocsStatus", error.message, "error"));
  });
  document.querySelector("#kbIngestBtn")?.addEventListener("click", () => {
    ingestFullDocument().catch(error => setKbStatus(error.message, "error"));
  });
  document.querySelector("#kbGoDocs")?.addEventListener("click", () => {
    const docId = kbIngestedDoc?.doc_id;
    if (docId) {
      openKbDocument(docId).catch(error => setKbStatus(error.message, "error"));
    } else {
      loadKbDocuments()
        .then(() => showKbView("docs"))
        .catch(error => setNamedStatus("#kbDocsStatus", error.message, "error"));
    }
  });
  document.querySelector("#kbSubnav")?.addEventListener("click", event => {
    const tab = event.target.closest("[data-kb-view]");
    if (!tab) return;
    const view = tab.dataset.kbView;
    if (view === "docs") {
      loadKbDocuments()
        .then(() => showKbView("docs"))
        .catch(error => setNamedStatus("#kbDocsStatus", error.message, "error"));
    } else if (view === "recall") {
      loadKbDocuments()
        .then(() => {
          showKbView("recall");
          syncRecallButton();
        })
        .catch(error => setNamedStatus("#kbRecallStatus", error.message, "error"));
    }
  });
  document.querySelector("#kbFilterCurrent")?.addEventListener("change", renderKbDocuments);
  document.querySelector("#kbDocBack")?.addEventListener("click", () => {
    loadKbDocuments()
      .then(() => showKbView("docs"))
      .catch(error => setNamedStatus("#kbDocsStatus", error.message, "error"));
  });
  document.querySelector("#kbDocTitle")?.addEventListener("change", () => {
    saveKbDocTitle().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbDocEnabled")?.addEventListener("change", () => {
    toggleKbDocEnabled().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbDocDelete")?.addEventListener("click", () => {
    deleteKbDocument().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbDocDownload")?.addEventListener("click", () => {
    downloadKbFile().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbAddSeg")?.addEventListener("click", () => {
    const box = document.querySelector("#kbAddSegBox");
    if (box) box.hidden = !box.hidden;
  });
  document.querySelector("#kbAddSegCancel")?.addEventListener("click", () => {
    const box = document.querySelector("#kbAddSegBox");
    if (box) box.hidden = true;
  });
  document.querySelector("#kbAddSegSave")?.addEventListener("click", () => {
    addKbSegment().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbSegSearch")?.addEventListener("input", renderOpenSegments);
  document.querySelector("#kbReindexBtn")?.addEventListener("click", () => {
    reindexKbDocument().catch(error => setNamedStatus("#kbDocStatus", error.message, "error"));
  });
  document.querySelector("#kbRecallQuery")?.addEventListener("input", syncRecallButton);
  document.querySelector("#kbRecallBtn")?.addEventListener("click", () => {
    runKbRecall().catch(error => setNamedStatus("#kbRecallStatus", error.message, "error"));
  });
  bindDropzone();
  fillKbStrategies({ default: "sentence", strategies: FALLBACK_STRATEGIES });
  renderRecallHistory();
}

bindKbWorkspace();
window.addEventListener("matrix-auth-changed", () => {
  if (document.body.dataset.workspace === "kb") {
    openKbWorkspace().catch(error => setKbStatus(error.message, "error"));
  }
});
window.matrixKb = { open: openKbWorkspace };
