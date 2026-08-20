const button = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const packagePanel = document.querySelector("#package");
const errorPanel = document.querySelector("#error");
const composeForm = document.querySelector("#composeForm");
const replyForm = document.querySelector("#replyForm");
const thinkingStatus = document.querySelector("#thinkingStatus");
const threadTurns = document.querySelector("#threadTurns");
const liveTurn = document.querySelector("#liveTurn");
const liveSteps = document.querySelector("#liveSteps");

const labels = {
  "task.submitted": "请求已受理",
  "worker.started": "已开始处理",
  "stage.started": "开始执行阶段",
  "stage.completed": "阶段执行完成",
  "work_item.ready": "工作项已拆出",
  "draft.ready": "草稿已过硬门",
  "package.ready": "草稿包已打包",
  "task.completed": "任务完成",
  "task.failed": "任务失败"
};

const stageLabels = {
  analyze_matrix: "分析与写稿",
  analyze_compose: "分析与写稿",
  analyze_reply: "分析与回评",
  publish_package: "打包草稿",
  publish_compose_package: "打包草稿",
  publish_reply_package: "打包草稿"
};

const degradeLabels = {
  pass: "通过",
  rewrite_safe: "安全改写",
  template_fallback: "模板降级",
  skip: "跳过"
};

const decisionLabels = {
  publishable: "可进入 Review",
  reply: "回复",
  acknowledge: "致谢",
  skip: "不回"
};

const statusLabels = {
  ready: "就绪",
  degraded: "已降级",
  skipped: "已跳过",
  failed: "失败"
};

let scenario = "compose";
const accountsByKey = new Map();
const interactionsByKey = new Map();
const threads = [];
let activeThreadId = null;
const lastThreadByScenario = { compose: null, reply: null };
let runStartedAt = 0;
let assistResult = null;
let selectedDraftKeys = new Set();
let focusedDraftKey = null;
let deletedDraftKeys = new Set();
let archiveFolders = [];
let activeFolderId = null;
let archivePeek = null;
const selectedArchiveKeys = new Set();
let pendingReplySources = [];
let lastReplyJob = { comments: [], sourceKeys: [] };

function sessionHeaders() {
  return {
    "Content-Type": "application/json",
    ...(window.matrixAuth?.headers() || {})
  };
}

async function sessionRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...sessionHeaders(),
      ...(options.headers || {})
    }
  });
  const payload = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : "请求失败";
    throw new Error(window.matrixAuth?.errorText?.(message) || message);
  }
  return payload;
}

function sessionToThread(session) {
  const turns = (session.turns || []).map(turn => ({
    text: turn.text,
    taskUrl: turn.task_url || "",
    at: new Date(turn.created_at),
    replySourceKeys: turn.extra?.reply_source_keys || [],
    replyComments: turn.extra?.reply_comments || []
  }));
  const extra = session.turns?.length ? session.turns.at(-1).extra || {} : {};
  return {
    id: session.session_id,
    title: session.title,
    scenario: session.last_scenario || extra.scenario || "compose",
    accountKey: extra.account_key,
    interactionKey: extra.interaction_key,
    postCount: extra.post_count,
    needTrends: extra.need_trends,
    turns,
    at: new Date(session.last_active_at || session.created_at)
  };
}

async function loadSessions() {
  if (!window.matrixAuth?.user()) {
    threads.length = 0;
    activeThreadId = null;
    renderHistory();
    return;
  }
  const payload = await sessionRequest("/api/sessions");
  const current = activeThread();
  threads.length = 0;
  threads.push(...(payload.sessions || []).map(sessionToThread));
  if (current && threads.some(item => item.id === current.id)) {
    const index = threads.findIndex(item => item.id === current.id);
    if (index >= 0 && current.turns.length) {
      threads[index] = { ...threads[index], ...current, title: threads[index].title };
    }
    activeThreadId = current.id;
  } else {
    if (current) {
      for (const key of Object.keys(lastThreadByScenario)) {
        if (lastThreadByScenario[key] === current.id) lastThreadByScenario[key] = null;
      }
    }
    if (!threads.some(item => item.id === activeThreadId)) activeThreadId = null;
  }
  renderHistory();
}

function selectedAccount() {
  const key = document.querySelector("#accountKey").value || "default";
  return accountsByKey.get(key) || { account_key: key };
}

function selectedInteraction() {
  const key = document.querySelector("#interactionKey")?.value || "help-first";
  return interactionsByKey.get(key) || { interaction_key: key };
}

function timeAgo(date) {
  const delta = Math.max(0, Date.now() - date.getTime());
  if (delta < 60000) return "刚刚";
  if (delta < 3600000) return `${Math.floor(delta / 60000)} 分钟前`;
  if (delta < 86400000) return `${Math.floor(delta / 3600000)} 小时前`;
  return `${Math.floor(delta / 86400000)} 天前`;
}

const DOCK_KEY = "matrix.dockHidden";
const SESSION_COL_KEY = "matrix.sessionCol";
const ASSIST_COL_KEY = "matrix.assistCol";

function setDockHidden(hidden) {
  document.body.classList.toggle("dock-hidden", hidden);
  const show = document.querySelector("#dockShowBtn");
  if (show) show.hidden = !hidden;
  localStorage.setItem(DOCK_KEY, hidden ? "1" : "0");
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function readDeskCol(desk, name, fallback) {
  const raw = getComputedStyle(desk).getPropertyValue(name).trim();
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : fallback;
}

function bindDeskSplits() {
  const desk = document.querySelector("#taskStage");
  if (!desk) return;
  const chatMin = 320;
  const gutters = 10;

  const applyCols = (session, assist) => {
    const deskWidth = desk.getBoundingClientRect().width || window.innerWidth;
    let nextSession = clamp(session, 180, 480);
    let nextAssist = clamp(assist, 240, 640);
    const budget = Math.max(chatMin + gutters + 180 + 240, deskWidth);
    const maxSide = Math.max(420, budget - chatMin - gutters);
    if (nextSession + nextAssist > maxSide) {
      const scale = maxSide / (nextSession + nextAssist);
      nextSession = clamp(Math.round(nextSession * scale), 180, 480);
      nextAssist = clamp(maxSide - nextSession, 240, 640);
    }
    desk.style.setProperty("--session-col", `${nextSession}px`);
    desk.style.setProperty("--assist-col", `${nextAssist}px`);
    return { session: nextSession, assist: nextAssist };
  };

  const sessionSaved = Number(localStorage.getItem(SESSION_COL_KEY));
  const assistSaved = Number(localStorage.getItem(ASSIST_COL_KEY));
  applyCols(
    Number.isFinite(sessionSaved) && sessionSaved > 0 ? sessionSaved : 260,
    Number.isFinite(assistSaved) && assistSaved > 0 ? assistSaved : 340
  );

  desk.querySelectorAll("[data-desk-split]").forEach(handle => {
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const kind = handle.dataset.deskSplit;
      const startX = event.clientX;
      const startSession = readDeskCol(desk, "--session-col", 260);
      const startAssist = readDeskCol(desk, "--assist-col", 340);
      desk.classList.add("is-resizing");
      handle.dataset.active = "1";
      handle.setPointerCapture(event.pointerId);

      const onMove = moveEvent => {
        const dx = moveEvent.clientX - startX;
        const deskWidth = desk.getBoundingClientRect().width;
        if (kind === "session") {
          const assist = readDeskCol(desk, "--assist-col", startAssist);
          const maxSession = Math.max(180, deskWidth - assist - gutters - chatMin);
          applyCols(clamp(startSession + dx, 180, Math.min(480, maxSession)), assist);
        } else {
          const session = readDeskCol(desk, "--session-col", startSession);
          const maxAssist = Math.max(240, deskWidth - session - gutters - chatMin);
          applyCols(session, clamp(startAssist - dx, 240, Math.min(640, maxAssist)));
        }
      };

      const onUp = () => {
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
        handle.removeEventListener("pointercancel", onUp);
        desk.classList.remove("is-resizing");
        delete handle.dataset.active;
        localStorage.setItem(
          SESSION_COL_KEY,
          String(Math.round(readDeskCol(desk, "--session-col", startSession)))
        );
        localStorage.setItem(
          ASSIST_COL_KEY,
          String(Math.round(readDeskCol(desk, "--assist-col", startAssist)))
        );
      };

      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
      handle.addEventListener("pointercancel", onUp);
    });
  });

  window.addEventListener("resize", () => {
    applyCols(
      readDeskCol(desk, "--session-col", 260),
      readDeskCol(desk, "--assist-col", 340)
    );
  });
}

function syncNav() {
  const workspace = document.body.dataset.workspace;
  const board = document.body.dataset.board;
  document.querySelectorAll(".nav-item[data-workspace]").forEach(btn => {
    const target = btn.dataset.workspace;
    const onNewTask = workspace === "task" && board === "home" && btn.id === "newTaskBtn";
    const onCatalog = workspace === "catalog" && target === "catalog";
    const onKb = workspace === "kb" && target === "kb";
    btn.classList.toggle("active", onNewTask || onCatalog || onKb);
  });
}

function setBoard(name) {
  document.body.dataset.board = name;
  if (progress) progress.hidden = name === "home";
  syncNav();
}

function syncChatHeader() {
  const title = document.querySelector("#chatTitle");
  const sub = document.querySelector("#chatSub");
  const thread = activeThread();
  if (title) title.textContent = thread?.title || "新任务";
  const play =
    scenario === "reply"
      ? selectedInteraction().display_name || selectedInteraction().interaction_key
      : selectedAccount().display_name || selectedAccount().account_key;
  if (sub) sub.textContent = `${scenario === "reply" ? "回评" : "写帖"} · ${play || "Twitter"}`;
}

function setAssistEmpty(empty) {
  const node = document.querySelector("#assistEmpty");
  if (node) node.hidden = !empty;
  const meta = document.querySelector("#agentMeta");
  if (meta && empty) meta.hidden = true;
  const source = document.querySelector("#draftSource");
  if (empty && source) source.hidden = true;
  if (empty) {
    assistResult = null;
    selectedDraftKeys = new Set();
    focusedDraftKey = null;
    deletedDraftKeys = new Set();
    archivePeek = null;
  }
}

function setAssistTab(name) {
  document.querySelectorAll("[data-assist-tab]").forEach(tab => {
    const on = tab.dataset.assistTab === name;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", String(on));
  });
  const source = document.querySelector("#sourceLeaf");
  const archive = document.querySelector("#archiveLeaf");
  if (source) source.hidden = name !== "source";
  if (archive) archive.hidden = name !== "archive";
}

function setWorkspace(name) {
  document.body.dataset.workspace = name;
  const task = document.querySelector("#taskStage");
  const catalog = document.querySelector("#catalogEditor");
  const kb = document.querySelector("#kbWorkspace");
  if (task) task.hidden = name !== "task";
  if (catalog) catalog.hidden = name !== "catalog";
  if (kb) kb.hidden = name !== "kb";
  syncNav();
  if (name === "catalog") {
    window.matrixCatalog?.loadCatalog?.().catch(error => {
      window.matrixCatalog?.renderCatalogList?.();
      const status = document.querySelector("#catalogStatus");
      if (status) status.textContent = error.message;
    });
    return;
  }
  const drawer = document.querySelector("#catalogDrawer");
  if (drawer) drawer.hidden = true;
  if (name === "kb") {
    window.matrixKb?.open?.();
    return;
  }
  setBoard(document.body.dataset.board || "home");
}

function applyScenarioChrome(next) {
  scenario = next;
  document.querySelectorAll("[data-scenario]").forEach(item => {
    const active = item.dataset.scenario === next;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
  });
  composeForm.hidden = next !== "compose";
  replyForm.hidden = next !== "reply";
  const trendsWrap = document.querySelector("#trendsWrap");
  if (trendsWrap) trendsWrap.hidden = next !== "compose";
  const accountWrap = document.querySelector("#accountSelectWrap");
  const interactionWrap = document.querySelector("#interactionSelectWrap");
  if (accountWrap) accountWrap.hidden = next !== "compose";
  if (interactionWrap) interactionWrap.hidden = next !== "reply";
  renderSkillChips();
  syncChatHeader();
  renderArchiveFolders();
}

function startFreshTask({ forgetLast = false } = {}) {
  bumpViewEpoch();
  persistPicks();
  if (forgetLast) lastThreadByScenario[scenario] = null;
  activeThreadId = null;
  threadTurns.replaceChildren();
  if (packagePanel) packagePanel.hidden = true;
  setAssistEmpty(true);
  errorPanel.hidden = true;
  if (liveTurn) liveTurn.hidden = true;
  resetLiveProcess();
  const composeBox = document.querySelector("#composeText");
  const replyBox = document.querySelector("#replyText");
  if (composeBox) composeBox.value = "";
  if (replyBox) replyBox.value = "";
  pendingReplySources = [];
  lastReplyJob = { comments: [], sourceKeys: [] };
  setWorkspace("task");
  setBoard("home");
  syncChatHeader();
  renderHistory();
}

function setScenario(next, { resetBoard = true } = {}) {
  const previous = scenario;
  if (resetBoard && previous !== next) {
    persistPicks();
    const current = activeThread();
    if (current) lastThreadByScenario[previous] = current.id;
    applyScenarioChrome(next);
    setWorkspace("task");
    const resumeId = lastThreadByScenario[next];
    const resume = threads.find(
      item => item.id === resumeId && (item.scenario || "compose") === next
    );
    if (resume) {
      openThread(resume.id);
      return;
    }
    startFreshTask();
    return;
  }
  applyScenarioChrome(next);
  setWorkspace("task");
  renderHistory();
}

function renderSkillChips() {
  const host = document.querySelector("#skillChips");
  if (!host) return;
  if (scenario === "reply") {
    const selected = document.querySelector("#interactionKey")?.value;
    host.replaceChildren(
      ...[...interactionsByKey.values()].slice(0, 7).map(item => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `skill-chip${item.interaction_key === selected ? " active" : ""}`;
        chip.textContent = (item.display_name || item.interaction_key).split("/")[0].trim();
        chip.addEventListener("click", () => {
          const select = document.querySelector("#interactionKey");
          if (!select) return;
          select.value = item.interaction_key;
          select.dispatchEvent(new Event("change"));
          renderSkillChips();
        });
        return chip;
      })
    );
    return;
  }
  const selected = document.querySelector("#accountKey").value;
  host.replaceChildren(
    ...[...accountsByKey.values()].slice(0, 7).map(account => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `skill-chip${account.account_key === selected ? " active" : ""}`;
      chip.textContent = (account.display_name || account.account_key).split("/")[0].trim();
      chip.addEventListener("click", () => {
        const select = document.querySelector("#accountKey");
        select.value = account.account_key;
        select.dispatchEvent(new Event("change"));
        renderSkillChips();
        renderExpertList();
      });
      return chip;
    })
  );
}

function composerState() {
  return {
    scenario,
    accountKey: document.querySelector("#accountKey").value || "default",
    interactionKey: document.querySelector("#interactionKey")?.value || "help-first",
    postCount: document.querySelector("#postCount").value,
    needTrends: document.querySelector("#needTrends").checked
  };
}

function applyComposerState(state) {
  if (!state) return;
  setScenario(state.scenario || "compose", { resetBoard: false });
  const select = document.querySelector("#accountKey");
  if (state.accountKey && [...select.options].some(item => item.value === state.accountKey)) {
    select.value = state.accountKey;
    select.dispatchEvent(new Event("change"));
  }
  const interactionSelect = document.querySelector("#interactionKey");
  if (
    interactionSelect &&
    state.interactionKey &&
    [...interactionSelect.options].some(item => item.value === state.interactionKey)
  ) {
    interactionSelect.value = state.interactionKey;
    interactionSelect.dispatchEvent(new Event("change"));
  }
  if (state.postCount) document.querySelector("#postCount").value = state.postCount;
  document.querySelector("#needTrends").checked = Boolean(state.needTrends);
}

function userBubble(text, kicker = "") {
  const article = document.createElement("article");
  article.className = "user-turn";
  if (kicker) {
    const tag = document.createElement("p");
    tag.className = "msg-kicker";
    tag.textContent = kicker;
    article.append(tag);
  }
  const body = document.createElement("p");
  body.textContent = text;
  article.append(body);
  return article;
}

function chatPair(userNode, agentNode) {
  const wrap = document.createElement("div");
  wrap.className = "chat-pair";
  wrap.append(userNode, agentNode);
  return wrap;
}

function pendingAgentTurn(status = "正在回评…") {
  const article = document.createElement("article");
  article.className = "agent-turn";
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble agent pending";
  const kicker = document.createElement("p");
  kicker.className = "msg-kicker";
  kicker.textContent = "AI 回评";
  const line = document.createElement("p");
  line.className = "thinking-status";
  line.textContent = status;
  bubble.append(kicker, line);
  article.append(bubble);
  return article;
}

function failedAgentTurn(message) {
  const article = document.createElement("article");
  article.className = "agent-turn";
  const fail = document.createElement("p");
  fail.className = "panel error";
  fail.textContent = message;
  article.append(fail);
  return article;
}

let viewEpoch = 0;

function bumpViewEpoch() {
  viewEpoch += 1;
  return viewEpoch;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function draftKey(draft, index) {
  return draft.draft_key || `draft-${index}`;
}

function persistPicks() {
  const thread = activeThread();
  if (!thread) return;
  thread.selectedKeys = [...selectedDraftKeys];
  thread.focusedKey = focusedDraftKey;
  thread.deletedKeys = [...deletedDraftKeys];
}

function restorePicks(thread, result) {
  assistResult = result;
  const valid = new Set((result.drafts || []).map((item, index) => draftKey(item, index)));
  deletedDraftKeys = new Set((thread.deletedKeys || []).filter(key => valid.has(key)));
  selectedDraftKeys = new Set(
    (thread.selectedKeys || []).filter(key => valid.has(key) && !deletedDraftKeys.has(key))
  );
  focusedDraftKey =
    valid.has(thread.focusedKey) && !deletedDraftKeys.has(thread.focusedKey)
      ? thread.focusedKey
      : null;
}

function visibleDraftRows(result) {
  return (result.drafts || [])
    .map((draft, index) => ({ draft, index, key: draftKey(draft, index) }))
    .filter(row => !deletedDraftKeys.has(row.key));
}

function toggleDraftPick(result, key, selected) {
  assistResult = result;
  archivePeek = null;
  if (selected) {
    selectedDraftKeys.add(key);
    focusedDraftKey = key;
  } else {
    selectedDraftKeys.delete(key);
    if (focusedDraftKey === key) focusedDraftKey = null;
  }
  if (selectedDraftKeys.size === 1) {
    focusedDraftKey = [...selectedDraftKeys][0];
  }
  persistPicks();
  syncDraftPickCards();
  renderAssistPicks();
  setAssistTab("source");
}

function visibleKeys(result) {
  return visibleDraftRows(result).map(row => row.key);
}

function allSelectedFor(result) {
  const keys = visibleKeys(result);
  return keys.length > 0 && keys.every(key => selectedDraftKeys.has(key));
}

const selectAllButtons = new WeakMap();

function syncSelectAllButtons() {
  document.querySelectorAll("[data-role=select-all]").forEach(btn => {
    const bound = selectAllButtons.get(btn);
    if (bound) btn.textContent = allSelectedFor(bound) ? "取消全选" : "全选";
  });
}

function syncDraftPickCards() {
  document.querySelectorAll(".tweet-pick").forEach(card => {
    const on = selectedDraftKeys.has(card.dataset.draftKey);
    card.classList.toggle("selected", on);
    const box = card.querySelector("input[type=checkbox]");
    if (box) box.checked = on;
  });
  syncSelectAllButtons();
}

function evidenceForDraft(result, draft) {
  const ids = new Set(draft.evidence_ids || []);
  if (!ids.size) return result.evidence || [];
  return (result.evidence || []).filter(item => ids.has(item.ref_id));
}

function fillAssistPanel(result, seconds) {
  const empty = document.querySelector("#assistEmpty");
  if (empty) empty.hidden = true;
  assistResult = result;
  archivePeek = null;
  const meta = document.querySelector("#agentMeta");
  if (meta) {
    meta.hidden = false;
    meta.textContent = `已完成 ${seconds}s`;
  }
  renderAssistPicks();
}

function showSourcePanel(item) {
  const source = document.querySelector("#draftSource");
  if (!source) return;
  if (!item) {
    source.hidden = true;
    return;
  }
  source.hidden = false;
  const rationale = document.querySelector("#sourceRationale");
  const evidence = document.querySelector("#sourceEvidence");
  const gate = document.querySelector("#sourceGate");
  if (rationale) rationale.textContent = item.rationale || "这条没有单独的出处说明。";
  if (evidence) {
    evidence.replaceChildren(
      ...listItems(
        (item.evidence || []).map(card => ({
          title: card.title || card.ref_id,
          body: card.ruling
        })),
        "这条没有绑定案例证据。"
      )
    );
  }
  if (gate) {
    const kind = decisionLabels[item.decision] || item.decision || "";
    const state = statusLabels[item.status] || item.status || "";
    const issues = (item.issues || []).join("；");
    gate.textContent = [kind, state, issues].filter(Boolean).join(" · ") || "—";
  }
}

function renderAssistPicks() {
  if (archivePeek) {
    showSourcePanel(archivePeek);
    return;
  }
  if (!assistResult) {
    showSourcePanel(null);
    return;
  }
  const focused = visibleDraftRows(assistResult).find(
    row => row.key === focusedDraftKey && selectedDraftKeys.has(row.key)
  );
  if (!focused) {
    showSourcePanel(null);
    return;
  }
  showSourcePanel({
    rationale: focused.draft.rationale,
    evidence: evidenceForDraft(assistResult, focused.draft),
    decision: focused.draft.decision,
    status: focused.draft.status,
    issues: focused.draft.issues
  });
}

const ARCHIVE_KEY = "matrix.archiveFolders";

function normalizeArchiveItem(item) {
  if (!item || !item.key) return null;
  return {
    ...item,
    key: String(item.key),
    replies: Array.isArray(item.replies)
      ? item.replies.filter(reply => reply && reply.key).map(reply => ({ ...reply, key: String(reply.key) }))
      : []
  };
}

function folderFromApi(collection) {
  return {
    id: collection.collection_id,
    name: collection.name,
    items: (Array.isArray(collection.items) ? collection.items : [])
      .map(normalizeArchiveItem)
      .filter(Boolean)
  };
}

function replaceArchiveFolders(collections, preferId) {
  const current = preferId || activeFolderId;
  archiveFolders = (collections || []).map(folderFromApi);
  if (current && archiveFolders.some(folder => folder.id === current)) {
    activeFolderId = current;
  } else if (activeFolderId && archiveFolders.some(folder => folder.id === activeFolderId)) {
    /* keep */
  } else {
    activeFolderId = archiveFolders[0]?.id || null;
  }
}

async function loadArchive() {
  if (!window.matrixAuth?.user()) {
    archiveFolders = [];
    activeFolderId = null;
    renderArchiveFolders();
    return;
  }
  const payload = await sessionRequest("/api/collections");
  if (!(payload.collections || []).length) {
    await migrateLocalArchive();
    const migrated = await sessionRequest("/api/collections");
    replaceArchiveFolders(migrated.collections);
  } else {
    replaceArchiveFolders(payload.collections);
  }
  renderArchiveFolders();
}

async function migrateLocalArchive() {
  let local = [];
  try {
    const raw = JSON.parse(localStorage.getItem(ARCHIVE_KEY) || "[]");
    local = Array.isArray(raw) ? raw : [];
  } catch {
    return;
  }
  if (!local.length) return;
  for (const folder of local) {
    if (!folder || !folder.name) continue;
    const created = await sessionRequest("/api/collections", {
      method: "POST",
      body: JSON.stringify({ name: String(folder.name) })
    });
    const items = (Array.isArray(folder.items) ? folder.items : [])
      .map(normalizeArchiveItem)
      .filter(Boolean);
    if (!items.length) continue;
    await sessionRequest(`/api/collections/${created.collection_id}/items`, {
      method: "POST",
      body: JSON.stringify({ items, bind_replies: false })
    });
  }
  localStorage.removeItem(ARCHIVE_KEY);
}

function setArchiveStatus(text) {
  const node = document.querySelector("#archiveStatus");
  if (!node) return;
  const message = String(text || "").trim();
  node.hidden = !message;
  node.textContent = message;
}

function hydrateResult(result, snapshot) {
  if (!result) return result;
  if (!result.task_id && snapshot?.task_id) result.task_id = snapshot.task_id;
  return result;
}

function archiveSnapshotKey(result, row) {
  const taskId = String(result?.task_id || "").trim();
  if (taskId) return `${taskId}:${row.key}`;
  return `once:${crypto.randomUUID()}`;
}

function activeFolder() {
  return archiveFolders.find(folder => folder.id === activeFolderId) || null;
}

function personaLabel() {
  if (scenario === "reply") {
    const item = selectedInteraction();
    return item.display_name || item.interaction_key || "回评";
  }
  const item = selectedAccount();
  return item.display_name || item.account_key || "写帖";
}

function locateArchiveItem(key) {
  for (const folder of archiveFolders) {
    const item = folder.items.find(entry => entry.key === key);
    if (item) return { folder, item };
  }
  return null;
}

function archiveItemByKey(key) {
  return locateArchiveItem(key)?.item || null;
}

function findArchiveItemByText(text) {
  const needle = (text || "").trim();
  if (!needle) return null;
  for (const folder of archiveFolders) {
    const item = folder.items.find(entry => (entry.text || "").trim() === needle);
    if (item) return item;
  }
  return null;
}

function pruneArchiveSelection() {
  const folder = activeFolder();
  const valid = new Set((folder?.items || []).map(item => item.key));
  for (const key of [...selectedArchiveKeys]) {
    if (!valid.has(key)) selectedArchiveKeys.delete(key);
  }
}

function selectedArchiveComments() {
  const folder = activeFolder();
  if (!folder) return [];
  return folder.items.filter(
    item => selectedArchiveKeys.has(item.key) && (item.text || "").trim()
  );
}

function loadArchiveItemToReply(item) {
  const text = (item.text || "").trim();
  if (!text) {
    setArchiveStatus("这条没有正文，不能填入。");
    return;
  }
  const box = document.querySelector("#replyText");
  if (!box) return;
  box.value = text;
  box.focus();
  archivePeek = item;
  pendingReplySources = [item.key];
  renderArchiveFolders();
  setArchiveStatus("已填入输入框，生成后可存回这条原推。");
}

function syncArchiveReplyBar() {
  const hint = document.querySelector("#archiveHint");
  if (hint) {
    hint.textContent =
      scenario === "reply"
        ? "在当前收藏夹勾选要回的推文，批量会并发回评。"
        : "先建收藏夹，对话里勾选后点「存入收藏夹」。";
  }
  const bar = document.querySelector("#archiveReplyBar");
  const folder = activeFolder();
  const show = scenario === "reply" && Boolean(folder && folder.items.length);
  if (bar) bar.hidden = !show;
  const count = selectedArchiveComments().length;
  const batch = document.querySelector("#archiveBatchReply");
  if (batch) {
    batch.textContent = count ? `批量回复（${count}）` : "批量回复";
    batch.disabled = count === 0 || button.disabled;
  }
}

function fillFolderSelect(select) {
  const current = select.value;
  select.replaceChildren();
  if (!archiveFolders.length) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "请先创建文件夹";
    select.append(empty);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  archiveFolders.forEach(folder => {
    const option = document.createElement("option");
    option.value = folder.id;
    option.textContent = `${folder.name}（${folder.items.length}）`;
    select.append(option);
  });
  if (archiveFolders.some(folder => folder.id === current)) select.value = current;
  else if (activeFolderId && archiveFolders.some(folder => folder.id === activeFolderId)) {
    select.value = activeFolderId;
  } else {
    select.value = archiveFolders[0].id;
  }
}

function syncArchiveTargets() {
  document.querySelectorAll("select.archive-target").forEach(fillFolderSelect);
}

function renderBoundReplies(item) {
  const replies = item.replies || [];
  if (!replies.length) return null;
  const list = document.createElement("ol");
  list.className = "archive-replies";
  replies.forEach((reply, index) => {
    const row = document.createElement("li");
    row.className = "archive-reply";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pick-item${archivePeek && archivePeek.key === reply.key ? " active" : ""}`;
    const heading = document.createElement("strong");
    heading.textContent = `回复 ${index + 1}`;
    const text = document.createElement("p");
    text.textContent = reply.text || "（正文已清空）";
    button.append(heading, text);
    button.addEventListener("click", () => {
      archivePeek = reply;
      showSourcePanel(reply);
      renderArchiveFolders();
      setAssistTab("source");
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "archive-remove";
    remove.setAttribute("aria-label", "移出这条回复");
    remove.textContent = "×";
    remove.addEventListener("click", event => {
      event.stopPropagation();
      const folder = activeFolder();
      const replyKey = reply.key;
      if (archivePeek && archivePeek.key === replyKey) {
        archivePeek = null;
        renderAssistPicks();
      }
      removeArchiveEntry(folder, replyKey).catch(error => setArchiveStatus(error.message));
    });
    row.append(button, remove);
    list.append(row);
  });
  return list;
}

function renderArchiveFolders() {
  pruneArchiveSelection();
  const list = document.querySelector("#archiveFolders");
  if (list) {
    list.replaceChildren(
      ...archiveFolders.map(folder => {
        const row = document.createElement("li");
        row.className = "archive-folder-row";
        const button = document.createElement("button");
        button.type = "button";
        button.className = `archive-folder${folder.id === activeFolderId ? " active" : ""}`;
        button.textContent = `${folder.name} · ${folder.items.length}`;
        button.addEventListener("click", () => openArchiveFolder(folder.id));
        const download = document.createElement("button");
        download.type = "button";
        download.className = "archive-folder-btn";
        download.textContent = "下载";
        download.disabled = folder.items.length === 0;
        download.addEventListener("click", event => {
          event.stopPropagation();
          downloadArchive(folder).catch(error => setArchiveStatus(error.message || "下载失败"));
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "archive-folder-btn";
        remove.textContent = "删除";
        remove.addEventListener("click", event => {
          event.stopPropagation();
          deleteArchiveFolder(folder).catch(error => setArchiveStatus(error.message));
        });
        row.append(button, download, remove);
        return row;
      })
    );
  }
  const pickEmpty = document.querySelector("#archivePickEmpty");
  if (pickEmpty) pickEmpty.hidden = archiveFolders.length > 0;
  const view = document.querySelector("#archiveFolderView");
  const title = document.querySelector("#archiveFolderTitle");
  const items = document.querySelector("#archiveFolderItems");
  const folder = activeFolder();
  if (view) view.hidden = !folder;
  if (title) title.textContent = folder ? folder.name : "收藏夹内容";
  const contentEmpty = document.querySelector("#archiveContentEmpty");
  if (contentEmpty) {
    contentEmpty.hidden = !folder || folder.items.length > 0;
    contentEmpty.textContent = "这个收藏夹还是空的。";
  }
  if (items) items.hidden = !folder || folder.items.length === 0;
  const foldHint = document.querySelector("#archiveFoldHint");
  if (foldHint) {
    const extra = Boolean(folder && folder.items.length > 5);
    foldHint.hidden = !extra;
    foldHint.textContent = extra
      ? `共 ${folder.items.length} 条，滑动可看全部。`
      : "";
  }
  if (items && folder) {
    items.replaceChildren(
      ...folder.items.map((item, index) => {
        const row = document.createElement("li");
        row.className = `archive-item${scenario === "reply" ? " has-pick" : ""}`;
        row.dataset.archiveKey = item.key;
        if (scenario === "reply") {
          const box = document.createElement("input");
          box.type = "checkbox";
          box.className = "archive-pick";
          box.checked = selectedArchiveKeys.has(item.key);
          box.disabled = !(item.text || "").trim();
          box.setAttribute("aria-label", "选入批量回复");
          box.addEventListener("click", event => event.stopPropagation());
          box.addEventListener("change", () => {
            if (box.checked) selectedArchiveKeys.add(item.key);
            else selectedArchiveKeys.delete(item.key);
            syncArchiveReplyBar();
          });
          row.append(box);
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = `pick-item${archivePeek && archivePeek.key === item.key ? " active" : ""}`;
        const heading = document.createElement("strong");
        heading.textContent = `推文 ${index + 1} · ${item.platform_key || "x-twitter"}`;
        const bound = (item.replies || []).length;
        if (bound) heading.textContent += ` · 已绑 ${bound} 条回复`;
        const text = document.createElement("p");
        text.textContent = item.text || "（正文已清空）";
        button.append(heading, text);
        button.addEventListener("click", () => {
          if (scenario === "reply") {
            loadArchiveItemToReply(item);
            return;
          }
          archivePeek = item;
          showSourcePanel(item);
          renderArchiveFolders();
          setAssistTab("source");
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "archive-remove";
        remove.setAttribute("aria-label", "移出文件夹");
        remove.textContent = "×";
        remove.addEventListener("click", event => {
          event.stopPropagation();
          selectedArchiveKeys.delete(item.key);
          if (
            archivePeek &&
            (archivePeek.key === item.key ||
              (item.replies || []).some(reply => reply.key === archivePeek.key))
          ) {
            archivePeek = null;
            renderAssistPicks();
          }
          removeArchiveEntry(folder, item.key).catch(error => setArchiveStatus(error.message));
        });
        row.append(button, remove);
        const replies = renderBoundReplies(item);
        if (replies) row.append(replies);
        return row;
      })
    );
  }
  syncArchiveTargets();
  syncArchiveReplyBar();
}

function openArchiveFolder(id) {
  activeFolderId = id;
  archivePeek = null;
  renderArchiveFolders();
  renderAssistPicks();
  setArchiveStatus("");
}

async function createArchiveFolder(name) {
  const label = name.trim();
  if (!label) {
    setArchiveStatus("请填写文件夹名称。");
    return;
  }
  if (!window.matrixAuth?.user()) {
    setArchiveStatus("请先登录后再创建收藏夹。");
    return;
  }
  try {
    const created = await sessionRequest("/api/collections", {
      method: "POST",
      body: JSON.stringify({ name: label })
    });
    await loadArchive();
    activeFolderId = created.collection_id;
    renderArchiveFolders();
    setArchiveStatus(`已创建「${label}」。`);
  } catch (error) {
    setArchiveStatus(error.message);
  }
}

function sourceKeysForResult(result) {
  return Array.isArray(result?.replySourceKeys) ? result.replySourceKeys.filter(Boolean) : [];
}

function originalCommentsForResult(result) {
  const take = values =>
    (Array.isArray(values) ? values : [])
      .map(item => String(item || "").trim())
      .filter(Boolean);
  const stored = take(result?.replyComments);
  if (stored.length) return stored;
  const turn = activeThread()?.turns.at(-1);
  const fromTurn = take(turn?.replyComments);
  if (fromTurn.length) return fromTurn;
  const fromJob = take(lastReplyJob.comments);
  if (fromJob.length) return fromJob;
  if (result?.task_type !== "reply_comment") return [];
  const text = String(turn?.text || "").trim();
  if (!text) return [];
  if (/^\d+\.\s/.test(text.split("\n")[0] || "")) {
    return text
      .split(/\n\n/)
      .map(part => part.replace(/^\d+\.\s*/, "").trim())
      .filter(Boolean);
  }
  return [text];
}

function resolveArchiveFolder(folderId) {
  return (
    archiveFolders.find(item => item.id === folderId) ||
    archiveFolders.find(item => item.id === activeFolderId) ||
    archiveFolders[0] ||
    null
  );
}

function parentSpecForSnapshot(result, snapshot) {
  const sources = sourceKeysForResult(result);
  const comments = originalCommentsForResult(result);
  if (sources.length === 1 || comments.length === 1) {
    return { key: sources[0] || null, text: comments[0] || "" };
  }
  return {
    key: sources[snapshot.index] || null,
    text: comments[snapshot.index] || ""
  };
}

function revealArchiveTop() {
  const items = document.querySelector("#archiveFolderItems");
  if (items) items.scrollTop = 0;
}

async function archiveSelectedTo(folderId) {
  const snapshots = snapshotSelectedDrafts();
  if (!snapshots.length) {
    setArchiveStatus("请先勾选要收藏的推文。");
    return;
  }
  const folder = resolveArchiveFolder(folderId);
  const comments = originalCommentsForResult(assistResult);
  const isReplySave =
    assistResult?.task_type === "reply_comment" ||
    comments.length > 0 ||
    sourceKeysForResult(assistResult).length > 0;
  if (!folder) {
    setAssistTab("archive");
    setArchiveStatus("请先在收藏夹里新建一个文件夹。");
    return;
  }
  if (!window.matrixAuth?.user()) {
    setArchiveStatus("请先登录后再收藏。");
    return;
  }
  try {
    const result = await sessionRequest(`/api/collections/${folder.id}/items`, {
      method: "POST",
      body: JSON.stringify({
        bind_replies: isReplySave,
        items: snapshots.map(snapshot => {
          if (!isReplySave) return snapshot;
          const spec = parentSpecForSnapshot(assistResult, snapshot);
          return {
            ...snapshot,
            parent_key: spec.key || null,
            parent_text: spec.text || ""
          };
        })
      })
    });
    await loadArchive();
    activeFolderId = result.collection?.collection_id || folder.id;
    revealArchiveTop();
    setAssistTab("archive");
    const added = result.added || 0;
    const bound = result.bound || 0;
    const created = result.created_parents || 0;
    if (!isReplySave) {
      setArchiveStatus(added ? `已存入「${folder.name}」${added} 条。` : "这些推文已在该收藏夹里。");
      return;
    }
    if (!bound && !created) {
      if (!comments.length) setArchiveStatus("没有记下原评，无法新建原推。请再发一次回评后再存。");
      else setArchiveStatus("这些回复已绑在原推下。");
      return;
    }
    if (created && bound) {
      setArchiveStatus(
        created === 1
          ? `原推不在收藏夹，已新建并绑上 ${bound} 条回复。`
          : `已新建 ${created} 条原推，并绑上 ${bound} 条回复。`
      );
      return;
    }
    if (bound) {
      setArchiveStatus(`已将 ${bound} 条回复绑到原推。`);
      return;
    }
    setArchiveStatus(`已新建 ${created} 条原推。`);
  } catch (error) {
    setAssistTab("archive");
    setArchiveStatus(error.message);
  }
}

function snapshotSelectedDrafts() {
  if (!assistResult) return [];
  return visibleDraftRows(assistResult)
    .filter(row => selectedDraftKeys.has(row.key))
    .map(row => ({
      key: archiveSnapshotKey(assistResult, row),
      index: row.index,
      text: row.draft.text || "",
      platform_key: row.draft.platform_key || "x-twitter",
      rationale: row.draft.rationale || "",
      decision: row.draft.decision || "",
      status: row.draft.status || "",
      issues: row.draft.issues || [],
      evidence: evidenceForDraft(assistResult, row.draft).map(card => ({
        ref_id: card.ref_id,
        title: card.title,
        ruling: card.ruling
      })),
      persona: personaLabel(),
      scenario,
      thread_title: activeThread()?.title || "",
      task_type: assistResult.task_type || "",
      snapshot_id: assistResult.snapshot_id || ""
    }));
}

function gateLine(item) {
  const kind = decisionLabels[item.decision] || item.decision || "";
  const state = statusLabels[item.status] || item.status || "";
  const issues = (item.issues || []).join("；");
  return [kind, state, issues].filter(Boolean).join(" · ");
}

function buildArchiveMarkdown(items, withSource, folderName) {
  const lines = [`# ${folderName || "MatrixCopilot 推文归档"}`, ""];
  items.forEach((item, index) => {
    lines.push(`## 推文 ${index + 1} · ${item.platform_key}`);
    lines.push("");
    lines.push(item.text || "（正文已清空）");
    lines.push("");
    lines.push(`- 人设/规则：${item.persona || "—"}`);
    lines.push(`- 任务：${item.thread_title || "—"}`);
    lines.push(`- 硬门：${gateLine(item) || "—"}`);
    if (withSource) {
      lines.push(`- 出处：${item.rationale || "这条没有单独的出处说明。"}`);
      const evidence = item.evidence || [];
      if (evidence.length) {
        evidence.forEach(card => {
          lines.push(`- 证据：${card.title || card.ref_id} — ${card.ruling || ""}`);
        });
      } else {
        lines.push("- 证据：这条没有绑定案例证据。");
      }
    }
    const replies = item.replies || [];
    replies.forEach((reply, replyIndex) => {
      lines.push("");
      lines.push(`### 回复 ${replyIndex + 1}`);
      lines.push("");
      lines.push(reply.text || "（正文已清空）");
      if (withSource && reply.rationale) {
        lines.push("");
        lines.push(`- 出处：${reply.rationale}`);
      }
    });
    if (index < items.length - 1) lines.push("");
  });
  return lines.join("\n");
}

function buildArchiveManifest(items, withSource, folderName) {
  return JSON.stringify(
    {
      folder: folderName,
      exported_at: new Date().toISOString(),
      include_source: withSource,
      count: items.length,
      items: items.map(item => ({
        draft_key: item.key,
        platform_key: item.platform_key,
        persona: item.persona,
        scenario: item.scenario,
        thread_title: item.thread_title,
        task_type: item.task_type,
        text: item.text,
        decision: item.decision,
        status: item.status,
        issues: item.issues,
        rationale: withSource ? item.rationale : undefined,
        evidence: withSource ? item.evidence : undefined,
        replies: (item.replies || []).map(reply => ({
          draft_key: reply.key,
          text: reply.text,
          decision: reply.decision,
          status: reply.status,
          rationale: withSource ? reply.rationale : undefined
        }))
      }))
    },
    null,
    2
  );
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = ARCHIVE_CRC[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const ARCHIVE_CRC = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let value = i;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[i] = value >>> 0;
  }
  return table;
})();

function u16(value) {
  const bytes = new Uint8Array(2);
  new DataView(bytes.buffer).setUint16(0, value, true);
  return bytes;
}

function u32(value) {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, value, true);
  return bytes;
}

function concatBytes(chunks) {
  const size = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

function zipStore(files) {
  const encoder = new TextEncoder();
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.name);
    const data = typeof file.text === "string" ? encoder.encode(file.text) : file.bytes;
    const crc = crc32(data);
    const local = concatBytes([
      u32(0x04034b50),
      u16(20),
      u16(0x0800),
      u16(0),
      u16(0),
      u16(0),
      u32(crc),
      u32(data.length),
      u32(data.length),
      u16(name.length),
      u16(0),
      name,
      data
    ]);
    const central = concatBytes([
      u32(0x02014b50),
      u16(20),
      u16(20),
      u16(0x0800),
      u16(0),
      u16(0),
      u16(0),
      u32(crc),
      u32(data.length),
      u32(data.length),
      u16(name.length),
      u16(0),
      u16(0),
      u16(0),
      u16(0),
      u32(0),
      u32(offset),
      name
    ]);
    locals.push(local);
    centrals.push(central);
    offset += local.length;
  }
  const centralDir = concatBytes(centrals);
  const eocd = concatBytes([
    u32(0x06054b50),
    u16(0),
    u16(0),
    u16(files.length),
    u16(files.length),
    u32(centralDir.length),
    u32(offset),
    u16(0)
  ]);
  return new Blob([concatBytes([...locals, centralDir, eocd])], { type: "application/zip" });
}

function triggerDownload(filename, blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function writeTextHandle(dir, name, text) {
  const handle = await dir.getFileHandle(name, { create: true });
  const writable = await handle.createWritable();
  await writable.write(text);
  await writable.close();
}

function archiveStamp() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}${month}${day}`;
}

async function downloadArchive(folder = activeFolder()) {
  if (!folder || !folder.items.length) {
    setArchiveStatus("这个文件夹还没有推文。");
    return;
  }
  const markdown = buildArchiveMarkdown(folder.items, true, folder.name);
  const manifest = buildArchiveManifest(folder.items, true, folder.name);
  const dirName = `matrix-archive-${folder.name}-${archiveStamp()}`;
  if (window.showDirectoryPicker) {
    try {
      const root = await window.showDirectoryPicker({ mode: "readwrite" });
      const dir = await root.getDirectoryHandle(dirName, { create: true });
      await writeTextHandle(dir, "archive.md", markdown);
      await writeTextHandle(dir, "manifest.json", manifest);
      setArchiveStatus(`已写入 ${dirName}/`);
      return;
    } catch (error) {
      if (error.name === "AbortError") {
        setArchiveStatus("已取消保存。");
        return;
      }
    }
  }
  triggerDownload(
    `${dirName}.zip`,
    zipStore([
      { name: "archive.md", text: markdown },
      { name: "manifest.json", text: manifest }
    ])
  );
  setArchiveStatus("已开始下载 zip。");
}

async function deleteArchiveFolder(folder) {
  if (!folder) return;
  try {
    await sessionRequest(`/api/collections/${folder.id}`, { method: "DELETE" });
    if (activeFolderId === folder.id) {
      archivePeek = null;
    }
    await loadArchive();
    renderAssistPicks();
    setArchiveStatus(`已删除「${folder.name}」。`);
  } catch (error) {
    setArchiveStatus(error.message);
  }
}

async function removeArchiveEntry(folder, itemId) {
  if (!folder || !itemId) return;
  await sessionRequest(
    `/api/collections/${folder.id}/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE" }
  );
  await loadArchive();
  syncArchiveTargets();
}

function bindArchive() {
  loadArchive().catch(() => {});
  renderArchiveFolders();
  document.querySelectorAll("[data-assist-tab]").forEach(tab => {
    tab.addEventListener("click", () => setAssistTab(tab.dataset.assistTab));
  });
  document.querySelector("#archiveCreate")?.addEventListener("submit", event => {
    event.preventDefault();
    const input = document.querySelector("#archiveName");
    createArchiveFolder(input?.value || "").catch(error => setArchiveStatus(error.message));
    if (input) input.value = "";
  });
  document.querySelector("#archiveSelectAll")?.addEventListener("click", () => {
    const folder = activeFolder();
    if (!folder) return;
    for (const item of folder.items) {
      if ((item.text || "").trim()) selectedArchiveKeys.add(item.key);
    }
    renderArchiveFolders();
  });
  document.querySelector("#archiveClearPick")?.addEventListener("click", () => {
    const folder = activeFolder();
    if (folder) {
      for (const item of folder.items) selectedArchiveKeys.delete(item.key);
    } else {
      selectedArchiveKeys.clear();
    }
    renderArchiveFolders();
  });
  document.querySelector("#archiveBatchReply")?.addEventListener("click", () => {
    submitArchiveBatch();
  });
}

function renderTweetPick(result, row) {
  const card = document.createElement("label");
  card.className = `tweet-pick${selectedDraftKeys.has(row.key) ? " selected" : ""}`;
  card.dataset.draftKey = row.key;
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = selectedDraftKeys.has(row.key);
  box.addEventListener("change", () => toggleDraftPick(result, row.key, box.checked));
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = `${result.task_type === "reply_comment" ? "回复" : "推文"} ${row.index + 1}`;
  const text = document.createElement("p");
  text.className = "draft-text";
  text.textContent = row.draft.text || "（正文已清空）";
  body.append(title, text);
  card.append(box, body);
  return card;
}

function fillTweetList(list, result) {
  const rows = visibleDraftRows(result);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "tweet-empty";
    empty.textContent = "已删除全部推文";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...rows.map(row => renderTweetPick(result, row)));
}

function pruneDeletedCards() {
  document.querySelectorAll(".tweet-list").forEach(list => {
    list.querySelectorAll(".tweet-pick").forEach(card => {
      if (deletedDraftKeys.has(card.dataset.draftKey)) card.remove();
    });
    if (!list.querySelector(".tweet-pick")) {
      const empty = document.createElement("p");
      empty.className = "tweet-empty";
      empty.textContent = "已删除全部推文";
      list.replaceChildren(empty);
    }
  });
}

function deleteSelectedDrafts() {
  if (!selectedDraftKeys.size) return;
  for (const key of selectedDraftKeys) deletedDraftKeys.add(key);
  selectedDraftKeys = new Set();
  if (focusedDraftKey && deletedDraftKeys.has(focusedDraftKey)) focusedDraftKey = null;
  persistPicks();
  pruneDeletedCards();
  renderAssistPicks();
}

function compactAgentBubble(result) {
  const article = document.createElement("article");
  article.className = "agent-turn";
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble agent";
  const kicker = document.createElement("p");
  kicker.className = "msg-kicker";
  const isReply = result.task_type === "reply_comment";
  kicker.textContent = isReply ? "AI 回评" : "AI 回复";
  const summary = document.createElement("p");
  const remaining = visibleDraftRows(result).length;
  summary.textContent =
    result.summary || `已生成 ${remaining} 条${isReply ? "回复" : "推文"}`;
  const list = document.createElement("div");
  list.className = "tweet-list";
  fillTweetList(list, result);
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const selectAll = document.createElement("button");
  selectAll.type = "button";
  selectAll.dataset.role = "select-all";
  selectAll.textContent = allSelectedFor(result) ? "取消全选" : "全选";
  selectAllButtons.set(selectAll, result);
  selectAll.addEventListener("click", () => {
    assistResult = result;
    archivePeek = null;
    if (allSelectedFor(result)) {
      for (const key of visibleKeys(result)) selectedDraftKeys.delete(key);
      if (!selectedDraftKeys.has(focusedDraftKey)) focusedDraftKey = null;
    } else {
      for (const key of visibleKeys(result)) selectedDraftKeys.add(key);
      if (selectedDraftKeys.size === 1) focusedDraftKey = [...selectedDraftKeys][0];
    }
    persistPicks();
    syncDraftPickCards();
    renderAssistPicks();
  });
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "复制已选";
  copy.addEventListener("click", () => {
    const texts = visibleDraftRows(result)
      .filter(row => selectedDraftKeys.has(row.key))
      .map(row => row.draft.text)
      .filter(Boolean);
    if (texts.length) navigator.clipboard.writeText(texts.join("\n\n"));
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "删除已选";
  remove.addEventListener("click", () => deleteSelectedDrafts());
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "清空选择";
  clear.addEventListener("click", () => {
    selectedDraftKeys = new Set();
    focusedDraftKey = null;
    archivePeek = null;
    persistPicks();
    syncDraftPickCards();
    renderAssistPicks();
  });
  actions.append(selectAll, copy, remove, clear);
  const archiveBar = document.createElement("div");
  archiveBar.className = "msg-actions archive-bar";
  const target = document.createElement("select");
  target.className = "archive-target";
  target.setAttribute("aria-label", "收藏夹");
  fillFolderSelect(target);
  const archiveBtn = document.createElement("button");
  archiveBtn.type = "button";
  archiveBtn.textContent = result.task_type === "reply_comment" ? "存回原推" : "存入收藏夹";
  archiveBtn.addEventListener("click", () => {
    assistResult = result;
    if (!result.replyComments?.length) {
      result.replyComments = originalCommentsForResult(result);
    }
    archiveSelectedTo(target.value || activeFolderId || "").catch(error => {
      setArchiveStatus(error.message);
    });
  });
  archiveBar.append(target, archiveBtn);
  bubble.append(kicker, summary, list, actions, archiveBar);
  article.append(bubble);
  return article;
}

function fillResultSection(root, result, seconds) {
  const meta = root.querySelector("[data-role=agent-meta]");
  if (meta) {
    meta.hidden = false;
    meta.textContent = `已完成 ${seconds}s`;
  }
  root.querySelector("[data-role=summary]").textContent = result.summary;
  root.querySelector("[data-role=package-meta]").textContent =
    `${result.task_type === "compose_post" ? "创作" : "回复"} · ${result.drafts.length} 条草稿`;
  root.querySelector("[data-role=drafts]").replaceChildren(
    ...result.drafts.map((draft, index) => renderDraft(draft, index))
  );
  root.querySelector("[data-role=evidence]").replaceChildren(
    ...listItems(
      (result.evidence || []).map(item => ({
        title: item.title || item.ref_id,
        body: item.ruling
      })),
      "本次没有召回案例卡片。"
    )
  );
  root.querySelector("[data-role=limitations]").replaceChildren(
    ...listItems(result.limitations || [], "没有额外限制说明。")
  );
  root.querySelector("[data-role=snapshot]").textContent =
    `快照：${result.snapshot_id} · Trace：${result.trace_ref}`;
}

function agentResultArticle(result, seconds) {
  const article = document.createElement("article");
  article.className = "agent-turn";
  article.innerHTML = `
    <p class="agent-name">MatrixCopilot</p>
    <p class="agent-meta" data-role="agent-meta"></p>
    <section class="final-result">
      <p class="lede" data-role="summary"></p>
      <ol class="result-steps" data-role="drafts"></ol>
      <p class="result-heading">证据</p>
      <ul class="result-bullets" data-role="evidence"></ul>
      <p class="result-heading">限制说明</p>
      <ol class="result-steps" data-role="limitations"></ol>
      <p class="meta" data-role="snapshot"></p>
      <p class="meta" data-role="package-meta"></p>
    </section>
  `;
  fillResultSection(article, result, seconds);
  return article;
}

function activeThread() {
  return threads.find(item => item.id === activeThreadId) || null;
}

function threadsForScenario() {
  return threads.filter(item => (item.scenario || "compose") === scenario);
}

function renderHistory() {
  const host = document.querySelector("#taskHistory");
  const count = document.querySelector("#taskCount");
  const items = threadsForScenario();
  if (count) count.textContent = String(items.length);
  if (!host) return;
  host.replaceChildren(
    ...items.slice(0, 20).map(item => {
      const row = document.createElement("div");
      row.className = "history-row";
      const open = document.createElement("button");
      open.type = "button";
      open.className = `history-item${item.id === activeThreadId ? " active" : ""}`;
      const avatar = document.createElement("span");
      avatar.className = "session-avatar";
      avatar.textContent = (item.title || "任").slice(0, 1);
      const body = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("small");
      const last = item.turns.at(-1);
      meta.textContent = `${item.scenario === "reply" ? "回评" : "写帖"} · ${timeAgo(last?.at || item.at)}`;
      body.append(title, meta);
      open.append(avatar, body);
      open.addEventListener("click", () => {
        openThread(item.id);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "history-remove";
      remove.textContent = "删除";
      remove.setAttribute("aria-label", `删除会话 ${item.title || ""}`);
      remove.addEventListener("click", event => {
        event.stopPropagation();
        deleteThread(item).catch(error => {
          errorPanel.textContent = error.message || "删除失败";
          errorPanel.hidden = false;
        });
      });
      row.append(open, remove);
      return row;
    })
  );
}

async function deleteThread(thread) {
  if (!thread?.id) return;
  bumpViewEpoch();
  await sessionRequest(`/api/sessions/${encodeURIComponent(thread.id)}`, {
    method: "DELETE"
  });
  dropLocalThread(thread.id);
  if (!activeThreadId) {
    startFreshTask({ forgetLast: true });
    return;
  }
  renderHistory();
}

function dropLocalThread(id) {
  const index = threads.findIndex(item => item.id === id);
  if (index >= 0) threads.splice(index, 1);
  for (const key of Object.keys(lastThreadByScenario)) {
    if (lastThreadByScenario[key] === id) lastThreadByScenario[key] = null;
  }
  if (activeThreadId === id) activeThreadId = null;
}

function sessionGone(error) {
  const text = error?.message || "";
  return (
    text === "session not found" ||
    text === "session access denied" ||
    text === "会话不存在" ||
    text === "不能访问别人的会话"
  );
}

async function ensureThread(text) {
  const state = composerState();
  const current = activeThread();
  if (current && (current.scenario || "compose") === state.scenario) {
    current.turns.push({ text, taskUrl: "", at: new Date() });
    Object.assign(current, state);
    current.at = new Date();
    const rest = threads.filter(item => item.id !== current.id);
    threads.length = 0;
    threads.push(current, ...rest);
    lastThreadByScenario[state.scenario] = current.id;
    return current;
  }
  const created = await sessionRequest("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      title: text.slice(0, 28) || "新会话",
      last_scenario: state.scenario
    })
  });
  const thread = sessionToThread(created);
  Object.assign(thread, state);
  thread.turns = [{ text, taskUrl: "", at: new Date() }];
  thread.at = new Date();
  threads.unshift(thread);
  activeThreadId = thread.id;
  lastThreadByScenario[state.scenario] = thread.id;
  return thread;
}

function attachTaskUrl(taskUrl) {
  const last = activeThread()?.turns.at(-1);
  if (last) last.taskUrl = taskUrl;
}

async function openThread(id) {
  let thread = threads.find(item => item.id === id);
  if (!thread) return;
  const epoch = bumpViewEpoch();
  try {
    const detail = await sessionRequest(`/api/sessions/${id}`);
    if (epoch !== viewEpoch) return;
    thread = sessionToThread(detail);
    const index = threads.findIndex(item => item.id === id);
    if (index >= 0) threads[index] = { ...threads[index], ...thread };
    else threads.unshift(thread);
  } catch (error) {
    if (epoch !== viewEpoch) return;
    if (sessionGone(error)) {
      const wasActive = activeThreadId === id;
      dropLocalThread(id);
      if (wasActive && document.body.dataset.workspace === "task") {
        startFreshTask({ forgetLast: true });
        return;
      }
      renderHistory();
      return;
    }
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
    return;
  }
  activeThreadId = id;
  lastThreadByScenario[thread.scenario || "compose"] = id;
  setWorkspace("task");
  setBoard("done");
  applyComposerState(thread);
  errorPanel.hidden = true;
  if (liveTurn) liveTurn.hidden = true;
  setAssistEmpty(true);
  threadTurns.replaceChildren();
  let lastResult = null;
  for (const turn of thread.turns) {
    if (epoch !== viewEpoch) return;
    const isReplyTurn = Boolean(turn.replySourceKeys?.length || turn.replyComments?.length);
    const user = userBubble(turn.text, isReplyTurn ? "原推" : "");
    if (turn.error && !turn.taskUrl) {
      threadTurns.append(chatPair(user, failedAgentTurn(turn.error)));
      continue;
    }
    if (!turn.taskUrl) {
      threadTurns.append(user);
      continue;
    }
    const pending = pendingAgentTurn("正在加载该轮结果…");
    threadTurns.append(chatPair(user, pending));
    try {
      const result = await fetchTaskResult(turn.taskUrl, {
        wait: true,
        isCurrent: () => epoch === viewEpoch
      });
      if (epoch !== viewEpoch) return;
      result.replySourceKeys = turn.replySourceKeys || [];
      result.replyComments = turn.replyComments || [];
      lastResult = result;
      pending.replaceWith(compactAgentBubble(result));
    } catch (error) {
      if (epoch !== viewEpoch) return;
      if (error.message === "已切换会话") return;
      pending.replaceWith(
        failedAgentTurn(error.message || "无法加载该轮结果，服务重启后历史任务会清空。")
      );
    }
  }
  if (epoch !== viewEpoch) return;
  if (lastResult) {
    restorePicks(thread, lastResult);
    fillAssistPanel(lastResult, 1);
    syncDraftPickCards();
  }
  syncChatHeader();
  const box = document.querySelector(thread.scenario === "reply" ? "#replyText" : "#composeText");
  if (box) box.value = "";
  renderHistory();
  threadTurns.lastElementChild?.scrollIntoView({ block: "end" });
}

function renderExpertList() {
  const list = document.querySelector("#expertList");
  if (!list) return;
  const selected = document.querySelector("#accountKey").value;
  list.replaceChildren(
    ...[...accountsByKey.values()].map(account => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `expert-card${account.account_key === selected ? " active" : ""}`;
      const name = document.createElement("strong");
      name.textContent = account.display_name || account.account_key;
      const sub = document.createElement("small");
      sub.textContent = account.one_liner || account.account_key;
      card.append(name, sub);
      card.addEventListener("click", () => {
        const select = document.querySelector("#accountKey");
        select.value = account.account_key;
        select.dispatchEvent(new Event("change"));
        renderExpertList();
        setWorkspace("task");
        renderSkillChips();
      });
      return card;
    })
  );
}

function renderAccountProfile() {
  const node = document.querySelector("#accountProfile");
  if (!node) return;
  const account = selectedAccount();
  const goals = (account.goals || []).slice(0, 2).join("；");
  const lines = [
    account.one_liner || account.voice_summary || "",
    account.audience ? `读者：${account.audience}` : "",
    goals ? `目标：${goals}` : ""
  ].filter(Boolean);
  node.textContent = lines.join("\n") || "未选人设。";
}

async function loadAccounts() {
  const select = document.querySelector("#accountKey");
  try {
    const response = await fetch("/api/accounts");
    const payload = await response.json();
    const accounts = payload.accounts || [];
    if (!response.ok || !accounts.length) throw new Error("empty catalog");
    const previous = select.value;
    accountsByKey.clear();
    accounts.forEach(account => accountsByKey.set(account.account_key, account));
    select.replaceChildren(
      ...accounts.map(account => {
        const option = document.createElement("option");
        option.value = account.account_key;
        option.textContent = account.display_name;
        return option;
      })
    );
    if (accountsByKey.has(previous)) select.value = previous;
    else select.value = accountsByKey.has("default") ? "default" : accounts[0].account_key;
  } catch (error) {
    accountsByKey.set("default", {
      account_key: "default",
      display_name: "Matrix Demo",
      one_liner: "消费品牌增长官号。"
    });
    const option = document.createElement("option");
    option.value = "default";
    option.textContent = "Matrix Demo";
    select.replaceChildren(option);
  }
  renderAccountProfile();
  renderExpertList();
  renderSkillChips();
}

async function loadInteractions() {
  const select = document.querySelector("#interactionKey");
  if (!select) return;
  try {
    const response = await fetch("/api/interactions");
    const payload = await response.json();
    const items = payload.interactions || [];
    if (!response.ok || !items.length) throw new Error("empty catalog");
    const previous = select.value;
    interactionsByKey.clear();
    items.forEach(item => interactionsByKey.set(item.interaction_key, item));
    select.replaceChildren(
      ...items.map(item => {
        const option = document.createElement("option");
        option.value = item.interaction_key;
        option.textContent = item.display_name;
        return option;
      })
    );
    if (interactionsByKey.has(previous)) select.value = previous;
    else {
      select.value = interactionsByKey.has("help-first")
        ? "help-first"
        : items[0].interaction_key;
    }
  } catch (error) {
    interactionsByKey.set("help-first", {
      interaction_key: "help-first",
      display_name: "先答疑",
      one_liner: "能公开答的答清，不打广告。"
    });
    const option = document.createElement("option");
    option.value = "help-first";
    option.textContent = "先答疑";
    select.replaceChildren(option);
  }
  renderSkillChips();
}

document.querySelector("#accountKey").addEventListener("change", () => {
  renderAccountProfile();
  renderSkillChips();
  renderExpertList();
  syncChatHeader();
});
document.querySelector("#accountKey").addEventListener("focus", () => {
  loadAccounts();
});
const interactionSelect = document.querySelector("#interactionKey");
if (interactionSelect) {
  interactionSelect.addEventListener("change", () => {
    renderSkillChips();
    syncChatHeader();
  });
  interactionSelect.addEventListener("focus", () => {
    loadInteractions();
  });
}

document.querySelector("#sessionNewBtn")?.addEventListener("click", () => {
  startFreshTask({ forgetLast: true });
});
document.querySelectorAll(".nav-item[data-workspace]").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.id === "newTaskBtn") {
      startFreshTask({ forgetLast: true });
      return;
    }
    setWorkspace(btn.dataset.workspace);
  });
});

document.querySelectorAll("[data-scenario]").forEach(tab => {
  tab.addEventListener("click", () => setScenario(tab.dataset.scenario));
});

let liveCounts = { workItems: 0, drafts: 0 };

function resetLiveProcess() {
  liveCounts = { workItems: 0, drafts: 0 };
  if (liveSteps) liveSteps.replaceChildren();
  if (thinkingStatus) {
    thinkingStatus.hidden = false;
    thinkingStatus.textContent = "正在准备";
  }
}

function upsertLiveStep(slot, text, live) {
  if (!liveSteps) return;
  if (thinkingStatus) thinkingStatus.hidden = true;
  let item = liveSteps.querySelector(`[data-slot="${slot}"]`);
  if (!item) {
    item = document.createElement("li");
    item.className = "live-step";
    item.dataset.slot = slot;
    const dot = document.createElement("span");
    dot.className = "live-dot";
    const body = document.createElement("span");
    body.className = "live-text";
    item.append(dot, body);
    liveSteps.append(item);
  }
  const body = item.querySelector(".live-text");
  if (body) body.textContent = text;
  item.classList.toggle("live", live);
  item.classList.toggle("done", !live);
  if (live) {
    liveSteps.querySelectorAll(".live-step").forEach(row => {
      if (row !== item && row.classList.contains("live")) {
        row.classList.remove("live");
        row.classList.add("done");
      }
    });
  }
  liveTurn?.scrollIntoView({ block: "end", behavior: "smooth" });
}

function addEvent(type, payload) {
  const data = payload.data || {};
  if (type === "task.submitted") upsertLiveStep("accept", "请求已受理", true);
  else if (type === "worker.started") upsertLiveStep("worker", "已开始处理", true);
  else if (type === "stage.started") {
    const name = stageLabels[data.stage] || data.stage || "阶段";
    upsertLiveStep(`stage-${data.stage || name}`, `正在${name}`, true);
  } else if (type === "work_item.ready") {
    liveCounts.workItems += 1;
    upsertLiveStep("work-items", `已拆出 ${liveCounts.workItems} 条工作项`, true);
  } else if (type === "draft.ready") {
    liveCounts.drafts += 1;
    const op = degradeLabels[data.degrade_op];
    upsertLiveStep(
      "drafts",
      `已过硬门 ${liveCounts.drafts} 条草稿${op ? ` · ${op}` : ""}`,
      true
    );
  } else if (type === "stage.completed") {
    const name = stageLabels[data.stage] || data.stage || "阶段";
    upsertLiveStep(`stage-${data.stage || name}`, `${name}完成`, false);
  } else if (type === "package.ready") upsertLiveStep("package", "草稿包已就绪", true);
  else if (type === "task.completed") upsertLiveStep("done", "处理完成", false);
  else if (type === "task.failed") upsertLiveStep("done", "任务失败", false);
}

function errorMessage(body, fallback) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => item.msg || JSON.stringify(item)).join("；");
  }
  return fallback;
}

function chip(text, kind) {
  const node = document.createElement("span");
  node.className = `chip chip-${kind}`;
  node.textContent = text;
  return node;
}

function renderDraft(draft, index) {
  const item = document.createElement("li");
  item.className = `result-step status-${draft.status}`;
  const head = document.createElement("p");
  const title = document.createElement("strong");
  const kind = decisionLabels[draft.decision] || draft.decision;
  const state = statusLabels[draft.status] || draft.status;
  title.textContent = `草稿 ${index + 1} · ${draft.platform_key}`;
  head.append(title, document.createTextNode(` — ${kind}，${state}`));
  const text = document.createElement("p");
  text.className = "draft-text";
  text.textContent = draft.text || "（正文已清空）";
  item.append(head, text);
  if (draft.rationale) {
    const rationale = document.createElement("p");
    rationale.className = "rationale";
    rationale.textContent = draft.rationale;
    item.append(rationale);
  }
  return item;
}

function listItems(values, emptyText) {
  if (!values.length) {
    const row = document.createElement("li");
    row.className = "meta";
    row.textContent = emptyText;
    return [row];
  }
  return values.map(value => {
    const row = document.createElement("li");
    if (value.title) {
      const title = document.createElement("strong");
      title.textContent = value.title;
      row.append(title, document.createTextNode(` — ${value.body}`));
    } else {
      row.textContent = value;
    }
    return row;
  });
}

async function fetchTaskResult(taskUrl, { wait = false, isCurrent = () => true } = {}) {
  const deadline = Date.now() + 180000;
  while (isCurrent()) {
    const response = await fetch(taskUrl);
    const snapshot = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(snapshot, "无法加载任务"));
    if (snapshot.status === "failed") throw new Error(snapshot.error || "任务失败");
    if (snapshot.result) return hydrateResult(snapshot.result, snapshot);
    const pending =
      snapshot.status === "accepted" || snapshot.status === "running";
    if (!wait || !pending) throw new Error("任务没有返回草稿");
    if (Date.now() > deadline) throw new Error("等待任务超时");
    await sleep(700);
  }
  throw new Error("已切换会话");
}

function watchMatrixTask(accepted, { onEvent } = {}) {
  return new Promise((resolve, reject) => {
    const source = new EventSource(accepted.events_url);
    let settled = false;
    const stop = () => {
      if (settled) return false;
      settled = true;
      source.close();
      return true;
    };
    Object.keys(labels).forEach(type => {
      source.addEventListener(type, async event => {
        const payload = JSON.parse(event.data);
        onEvent?.(type, payload);
        if (type === "task.completed") {
          if (!stop()) return;
          try {
            resolve(await fetchTaskResult(accepted.task_url));
          } catch (error) {
            reject(error);
          }
        }
        if (type === "task.failed") {
          if (!stop()) return;
          reject(new Error(payload.data?.message || "任务失败"));
        }
      });
    });
    source.onerror = () => {
      if (!stop()) return;
      reject(new Error("事件连接中断"));
    };
  });
}

async function postMatrixTask(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: sessionHeaders(),
    body: JSON.stringify(body)
  });
  const accepted = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(accepted, "请求未被服务接受"));
  return accepted;
}

async function renderResult(taskUrl) {
  const result = await fetchTaskResult(taskUrl);
  const seconds = Math.max(1, Math.round((Date.now() - runStartedAt) / 1000));
  selectedDraftKeys = new Set();
  focusedDraftKey = null;
  deletedDraftKeys = new Set();
  persistPicks();
  const lastTurn = activeThread()?.turns.at(-1);
  result.replySourceKeys =
    (lastTurn?.replySourceKeys || []).length
      ? lastTurn.replySourceKeys
      : lastReplyJob.sourceKeys || [];
  result.replyComments =
    (lastTurn?.replyComments || []).length
      ? lastTurn.replyComments
      : lastReplyJob.comments || [];
  threadTurns.append(compactAgentBubble(result));
  fillAssistPanel(result, seconds);
  if (liveTurn) liveTurn.hidden = true;
  setBoard("done");
  threadTurns.lastElementChild?.scrollIntoView({ block: "end" });
}

function draftCount() {
  const raw = Number(document.querySelector("#postCount").value);
  if (!Number.isInteger(raw) || raw < 1 || raw > 10) {
    throw new Error("条数须为 1 到 10 的整数");
  }
  return raw;
}

function currentDraftKbProfile() {
  const select = document.querySelector("#kbEmbedding");
  if (select?.value) return select.value;
  return window.matrixKb?.rememberedProfile?.() || "";
}

function refreshKbDraftHint() {
  const node = document.querySelector("#kbDraftProfile");
  if (!node) return;
  const profile = currentDraftKbProfile();
  node.hidden = !profile;
  node.textContent = profile ? `将按 ${profile} 检索手册` : "";
}
window.refreshKbDraftHint = refreshKbDraftHint;

function payloadForSubmit(replyComments) {
  const profile = currentDraftKbProfile();
  if (scenario === "reply") {
    const comments = (replyComments || []).map(item => ({
      text: item.text.trim(),
      role: "root"
    }));
    const body = {
      text:
        comments.length === 1
          ? comments[0].text
          : "按互动规则逐条回复已签发评论。",
      comments,
      interaction_key: selectedInteraction().interaction_key,
      channel: "web"
    };
    if (comments.length === 1) body.reply_count = draftCount();
    if (activeThreadId) body.session_id = activeThreadId;
    if (profile) body.embedding_profile_id = profile;
    return { url: "/api/reply", body };
  }
  const body = {
    text: document.querySelector("#composeText").value.trim(),
    need_trends: document.querySelector("#needTrends").checked,
    post_count: draftCount(),
    account_key: selectedAccount().account_key,
    channel: "web",
    session_id: activeThreadId
  };
  if (profile) body.embedding_profile_id = profile;
  return { url: "/api/create", body };
}

async function newReplyThread(title) {
  const created = await sessionRequest("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title, last_scenario: "reply" })
  });
  const thread = sessionToThread(created);
  Object.assign(thread, composerState(), { scenario: "reply", turns: [], at: new Date() });
  threads.unshift(thread);
  activeThreadId = thread.id;
  lastThreadByScenario.reply = thread.id;
  return thread;
}

async function submitArchiveBatch() {
  if (button.disabled) return;
  if (scenario !== "reply") {
    setArchiveStatus("请先切换到回评。");
    return;
  }
  const folder = activeFolder();
  const items = selectedArchiveComments();
  if (!folder) {
    setArchiveStatus("请先选择一个收藏夹。");
    return;
  }
  if (!items.length) {
    setArchiveStatus("请在当前收藏夹里勾选要回复的推文。");
    return;
  }
  if (items.length > 10) {
    setArchiveStatus("一次最多回复 10 条，请先减少勾选。");
    return;
  }
  let replyCount;
  try {
    replyCount = draftCount();
  } catch (error) {
    setArchiveStatus(error.message);
    return;
  }
  button.disabled = true;
  persistPicks();
  syncArchiveReplyBar();
  const thread = await newReplyThread(`批量回评 · ${folder.name} · ${items.length} 条`);
  renderHistory();
  syncChatHeader();
  setWorkspace("task");
  setBoard("run");
  progress.hidden = false;
  threadTurns.replaceChildren();
  if (liveTurn) liveTurn.hidden = true;
  if (packagePanel) packagePanel.hidden = true;
  setAssistEmpty(true);
  errorPanel.hidden = true;
  const agentMeta = document.querySelector("#agentMeta");
  if (agentMeta) agentMeta.hidden = true;
  runStartedAt = Date.now();
  const interactionKey = selectedInteraction().interaction_key;
  const slots = items.map((item, index) => {
    const turn = {
      text: item.text.trim(),
      taskUrl: "",
      at: new Date(),
      replyComments: [item.text.trim()],
      replySourceKeys: [item.key]
    };
    thread.turns.push(turn);
    const pending = pendingAgentTurn(`正在回评 ${index + 1}/${items.length}…`);
    const pair = chatPair(userBubble(item.text.trim(), "原推"), pending);
    threadTurns.append(pair);
    return { item, turn, pending, pair };
  });
  threadTurns.lastElementChild?.scrollIntoView({ block: "end" });
  let finished = 0;
  let lastResult = null;
  setArchiveStatus(`正在并发回评 0/${slots.length}…`);
  try {
    await loadAccounts();
    await loadInteractions();
    await Promise.all(
      slots.map(async slot => {
        try {
          const accepted = await postMatrixTask("/api/reply", {
            text: slot.item.text.trim(),
            comments: [{ text: slot.item.text.trim(), role: "root" }],
            interaction_key: interactionKey,
            reply_count: replyCount,
            channel: "web",
            session_id: thread.id
          });
          slot.turn.taskUrl = accepted.task_url;
          const result = await watchMatrixTask(accepted);
          result.replySourceKeys = [slot.item.key];
          result.replyComments = [slot.item.text.trim()];
          lastResult = result;
          slot.pending.replaceWith(compactAgentBubble(result));
        } catch (error) {
          slot.turn.error = error.message || "回评失败";
          slot.pending.replaceWith(failedAgentTurn(slot.turn.error));
        } finally {
          finished += 1;
          setArchiveStatus(`正在并发回评 ${finished}/${slots.length}…`);
        }
      })
    );
    if (lastResult) fillAssistPanel(lastResult, Math.max(1, Math.round((Date.now() - runStartedAt) / 1000)));
    selectedArchiveKeys.clear();
    renderArchiveFolders();
    setBoard("done");
    const failed = slots.filter(slot => slot.turn.error).length;
    const ok = slots.length - failed;
    setArchiveStatus(
      failed
        ? `已完成 ${ok} 条原推，失败 ${failed} 条。`
        : `已并发回评 ${ok} 条原推，每条 ${replyCount} 个回复。`
    );
    threadTurns.lastElementChild?.scrollIntoView({ block: "end" });
  } catch (error) {
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
    setBoard(threadTurns.children.length ? "done" : "home");
  } finally {
    button.disabled = false;
    syncArchiveReplyBar();
  }
}

async function submitTask(event) {
  event.preventDefault();
  if (scenario === "reply") {
    const text = document.querySelector("#replyText").value.trim();
    if (!text) {
      errorPanel.textContent = "请先填写要回的评论，或从收藏夹勾选后批量回复";
      errorPanel.hidden = false;
      return;
    }
    let sources = pendingReplySources.filter(key => archiveItemByKey(key));
    if (sources.length !== 1) {
      const hit = findArchiveItemByText(text);
      sources = hit ? [hit.key] : [];
    }
    await runMatrixJob({
      displayText: text,
      comments: [{ text, role: "root" }],
      replySourceKeys: sources
    });
    return;
  }
  await runMatrixJob({
    displayText: document.querySelector("#composeText").value.trim()
  });
}

async function runMatrixJob({ displayText, comments, replySourceKeys = [] }) {
  if (button.disabled) return;
  const text = (displayText || "").trim();
  if (!text) {
    errorPanel.textContent = "请先填写主题或指令";
    errorPanel.hidden = false;
    return;
  }
  const epoch = viewEpoch;
  button.disabled = true;
  syncArchiveReplyBar();
  await ensureThread(text);
  if (epoch !== viewEpoch) {
    button.disabled = false;
    syncArchiveReplyBar();
    return;
  }
  const lastTurn = activeThread()?.turns.at(-1);
  const commentTexts = (comments || [])
    .map(item => String(item.text || "").trim())
    .filter(Boolean);
  lastReplyJob = {
    comments: commentTexts,
    sourceKeys: [...replySourceKeys]
  };
  if (lastTurn) {
    lastTurn.replySourceKeys = [...replySourceKeys];
    lastTurn.replyComments = commentTexts;
  }
  renderHistory();
  setWorkspace("task");
  setBoard("run");
  progress.hidden = false;
  threadTurns.append(userBubble(text, commentTexts.length ? "原推" : ""));
  if (liveTurn) liveTurn.hidden = false;
  resetLiveProcess();
  if (packagePanel) packagePanel.hidden = true;
  setAssistEmpty(true);
  setAssistTab("source");
  errorPanel.hidden = true;
  const agentMeta = document.querySelector("#agentMeta");
  if (agentMeta) agentMeta.hidden = true;
  runStartedAt = Date.now();
  try {
    await loadAccounts();
    await loadInteractions();
    if (epoch !== viewEpoch) return;
    const { url, body } = payloadForSubmit(comments);
    const accepted = await postMatrixTask(url, body);
    if (epoch !== viewEpoch) return;
    attachTaskUrl(accepted.task_url);
    await watchMatrixTask(accepted, {
      onEvent: (type, payload) => {
        if (epoch !== viewEpoch) return;
        addEvent(type, payload);
      }
    });
    if (epoch !== viewEpoch) return;
    await renderResult(accepted.task_url);
    const box = document.querySelector(scenario === "reply" ? "#replyText" : "#composeText");
    if (box) box.value = "";
  } catch (error) {
    if (epoch !== viewEpoch) return;
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
    if (liveTurn) liveTurn.hidden = true;
    setBoard(threadTurns.children.length ? "done" : "home");
  } finally {
    if (epoch === viewEpoch) {
      button.disabled = false;
      syncArchiveReplyBar();
    }
  }
}

function bindComposerKeys(textarea) {
  textarea.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    document.querySelector("#taskForm").requestSubmit();
  });
  if (textarea.id === "replyText") {
    textarea.addEventListener("input", () => {
      if (!textarea.value.trim()) pendingReplySources = [];
    });
  }
}

document.querySelector("#taskForm").addEventListener("submit", submitTask);
bindComposerKeys(document.querySelector("#composeText"));
bindComposerKeys(document.querySelector("#replyText"));

document.querySelector("#dockHideBtn")?.addEventListener("click", () => setDockHidden(true));
document.querySelector("#dockShowBtn")?.addEventListener("click", () => setDockHidden(false));
setDockHidden(localStorage.getItem(DOCK_KEY) === "1");
bindDeskSplits();

bindArchive();
loadAccounts();
loadInteractions();
syncChatHeader();
window.addEventListener("matrix-auth-changed", () => {
  loadSessions().catch(() => {});
  loadArchive().catch(() => {});
  refreshKbDraftHint();
});
loadSessions().catch(() => {});
refreshKbDraftHint();
document.querySelector("#kbEmbedding")?.addEventListener("change", refreshKbDraftHint);
