const button = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const events = document.querySelector("#events");
const packagePanel = document.querySelector("#package");
const errorPanel = document.querySelector("#error");
const composeForm = document.querySelector("#composeForm");
const replyForm = document.querySelector("#replyForm");
const thinking = document.querySelector("#thinking");
const thinkingStatus = document.querySelector("#thinkingStatus");
const threadTurns = document.querySelector("#threadTurns");
const liveTurn = document.querySelector("#liveTurn");

const labels = {
  "task.submitted": "请求已受理",
  "worker.started": "Worker 已领取任务",
  "stage.started": "开始执行阶段",
  "stage.completed": "阶段执行完成",
  "work_item.ready": "工作项已拆出",
  "draft.ready": "草稿已过硬门",
  "package.ready": "草稿包已打包",
  "task.completed": "任务完成",
  "task.failed": "任务失败"
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
const threads = [];
let activeThreadId = null;
let runStartedAt = 0;

function selectedAccount() {
  const key = document.querySelector("#accountKey").value || "default";
  return accountsByKey.get(key) || { account_key: key };
}

function timeAgo(date) {
  const delta = Math.max(0, Date.now() - date.getTime());
  if (delta < 60000) return "刚刚";
  if (delta < 3600000) return `${Math.floor(delta / 60000)} 分钟前`;
  if (delta < 86400000) return `${Math.floor(delta / 3600000)} 小时前`;
  return `${Math.floor(delta / 86400000)} 天前`;
}

function syncNav() {
  const workspace = document.body.dataset.workspace;
  const board = document.body.dataset.board;
  document.querySelectorAll(".nav-item[data-workspace]").forEach(btn => {
    const onNewTask = workspace === "task" && board === "home" && btn.id === "newTaskBtn";
    const onCatalog = workspace === "catalog" && btn.dataset.workspace === "catalog";
    btn.classList.toggle("active", onNewTask || onCatalog);
  });
}

function setBoard(name) {
  document.body.dataset.board = name;
  if (progress) progress.hidden = name === "home";
  syncNav();
}

function setWorkspace(name) {
  document.body.dataset.workspace = name;
  const task = name === "task";
  document.querySelector("#taskStage").hidden = !task;
  document.querySelector("#catalogEditor").hidden = task;
  if (name === "catalog") {
    syncNav();
    return;
  }
  const drawer = document.querySelector("#catalogDrawer");
  if (drawer) drawer.hidden = true;
  setBoard(document.body.dataset.board || "home");
}

function setScenario(next) {
  scenario = next;
  document.querySelectorAll("[data-scenario]").forEach(item => {
    const active = item.dataset.scenario === next;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
  });
  composeForm.hidden = next !== "compose";
  replyForm.hidden = next !== "reply";
  setWorkspace("task");
}

function renderSkillChips() {
  const host = document.querySelector("#skillChips");
  if (!host) return;
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
    postCount: document.querySelector("#postCount").value,
    needTrends: document.querySelector("#needTrends").checked,
    threadKey: document.querySelector("#threadKey").value
  };
}

function applyComposerState(state) {
  if (!state) return;
  setScenario(state.scenario || "compose");
  const select = document.querySelector("#accountKey");
  if (state.accountKey && [...select.options].some(item => item.value === state.accountKey)) {
    select.value = state.accountKey;
    select.dispatchEvent(new Event("change"));
  }
  if (state.postCount) document.querySelector("#postCount").value = state.postCount;
  document.querySelector("#needTrends").checked = Boolean(state.needTrends);
  if (state.threadKey) document.querySelector("#threadKey").value = state.threadKey;
}

function userBubble(text) {
  const article = document.createElement("article");
  article.className = "user-turn";
  const body = document.createElement("p");
  body.textContent = text;
  article.append(body);
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

function renderHistory() {
  const host = document.querySelector("#taskHistory");
  const count = document.querySelector("#taskCount");
  if (count) count.textContent = String(threads.length);
  if (!host) return;
  host.replaceChildren(
    ...threads.slice(0, 20).map(item => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `history-item${item.id === activeThreadId ? " active" : ""}`;
      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("small");
      const last = item.turns.at(-1);
      meta.textContent = `${item.scenario === "reply" ? "回评" : "写帖"} · ${timeAgo(last?.at || item.at)}`;
      row.append(title, meta);
      row.addEventListener("click", () => {
        if (button.disabled) return;
        openThread(item.id);
      });
      return row;
    })
  );
}

function ensureThread(text) {
  const state = composerState();
  if (activeThreadId) {
    const thread = activeThread();
    thread.turns.push({ text, taskUrl: "", at: new Date() });
    Object.assign(thread, state);
    thread.at = new Date();
    const rest = threads.filter(item => item.id !== thread.id);
    threads.length = 0;
    threads.push(thread, ...rest);
    return thread;
  }
  const thread = {
    id: crypto.randomUUID(),
    title: text.slice(0, 28) || "未命名任务",
    ...state,
    turns: [{ text, taskUrl: "", at: new Date() }],
    at: new Date()
  };
  threads.unshift(thread);
  activeThreadId = thread.id;
  return thread;
}

function attachTaskUrl(taskUrl) {
  const last = activeThread()?.turns.at(-1);
  if (last) last.taskUrl = taskUrl;
}

async function openThread(id) {
  const thread = threads.find(item => item.id === id);
  if (!thread) return;
  activeThreadId = id;
  setWorkspace("task");
  setBoard("done");
  applyComposerState(thread);
  errorPanel.hidden = true;
  if (liveTurn) liveTurn.hidden = true;
  if (thinking) thinking.hidden = true;
  if (packagePanel) packagePanel.hidden = true;
  threadTurns.replaceChildren();
  for (const turn of thread.turns) {
    threadTurns.append(userBubble(turn.text));
    if (!turn.taskUrl) continue;
    try {
      const response = await fetch(turn.taskUrl);
      const snapshot = await response.json();
      if (snapshot.status === "failed") {
        const fail = document.createElement("p");
        fail.className = "panel error";
        fail.textContent = snapshot.error || "该轮任务失败";
        threadTurns.append(fail);
        continue;
      }
      if (snapshot.result) threadTurns.append(agentResultArticle(snapshot.result, 1));
    } catch (error) {
      const fail = document.createElement("p");
      fail.className = "panel error";
      fail.textContent = "无法加载该轮结果，服务重启后历史任务会清空。";
      threadTurns.append(fail);
    }
  }
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

document.querySelector("#accountKey").addEventListener("change", () => {
  renderAccountProfile();
  renderSkillChips();
  renderExpertList();
});
document.querySelector("#accountKey").addEventListener("focus", () => {
  loadAccounts();
});

document.querySelectorAll(".nav-item[data-workspace]").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.id === "newTaskBtn") {
      activeThreadId = null;
      threadTurns.replaceChildren();
      events.replaceChildren();
      packagePanel.hidden = true;
      errorPanel.hidden = true;
      if (liveTurn) liveTurn.hidden = true;
      if (thinking) thinking.hidden = true;
      if (thinkingStatus) thinkingStatus.textContent = "生成草稿中，请稍候…";
      const composeBox = document.querySelector("#composeText");
      const replyBox = document.querySelector("#replyText");
      if (composeBox) composeBox.value = "";
      if (replyBox) replyBox.value = "";
      setWorkspace("task");
      setBoard("home");
      renderHistory();
      return;
    }
    setWorkspace(btn.dataset.workspace);
  });
});

document.querySelectorAll("[data-scenario]").forEach(tab => {
  tab.addEventListener("click", () => setScenario(tab.dataset.scenario));
});

function addEvent(type, payload) {
  const item = document.createElement("li");
  const extras = [];
  if (payload.data?.stage) extras.push(payload.data.stage);
  if (payload.data?.work_item_id) extras.push(payload.data.work_item_id);
  if (payload.data?.draft_key) extras.push(payload.data.draft_key);
  if (payload.data?.degrade_op) extras.push(degradeLabels[payload.data.degrade_op] || payload.data.degrade_op);
  const text = `${labels[type] || type}${extras.length ? `：${extras.join(" · ")}` : ""}`;
  item.textContent = text;
  events.appendChild(item);
  if (thinkingStatus) thinkingStatus.textContent = text;
  events.parentElement?.scrollIntoView({ block: "end", behavior: "smooth" });
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

async function renderResult(taskUrl) {
  const response = await fetch(taskUrl);
  const snapshot = await response.json();
  if (snapshot.status === "failed") throw new Error(snapshot.error || "任务失败");
  const result = snapshot.result;
  const seconds = Math.max(1, Math.round((Date.now() - runStartedAt) / 1000));
  threadTurns.append(agentResultArticle(result, seconds));
  if (thinking) thinking.hidden = true;
  if (liveTurn) liveTurn.hidden = true;
  packagePanel.hidden = true;
  setBoard("done");
  threadTurns.lastElementChild?.scrollIntoView({ block: "end" });
}

function composePostCount() {
  const raw = Number(document.querySelector("#postCount").value);
  if (!Number.isInteger(raw) || raw < 1 || raw > 10) {
    throw new Error("推文条数须为 1 到 10 的整数");
  }
  return raw;
}

function payloadForSubmit() {
  const account = selectedAccount();
  if (scenario === "reply") {
    return {
      url: "/api/reply",
      body: {
        text: document.querySelector("#replyText").value.trim(),
        thread_key: document.querySelector("#threadKey").value.trim(),
        account_key: account.account_key,
        channel: "web"
      }
    };
  }
  return {
    url: "/api/create",
    body: {
      text: document.querySelector("#composeText").value.trim(),
      need_trends: document.querySelector("#needTrends").checked,
      post_count: composePostCount(),
      account_key: account.account_key,
      channel: "web"
    }
  };
}

async function submitTask(event) {
  event.preventDefault();
  if (button.disabled) return;
  const text = document.querySelector(scenario === "reply" ? "#replyText" : "#composeText").value.trim();
  if (!text) {
    errorPanel.textContent = "请先填写主题或指令";
    errorPanel.hidden = false;
    return;
  }
  button.disabled = true;
  ensureThread(text);
  renderHistory();
  setWorkspace("task");
  setBoard("run");
  progress.hidden = false;
  threadTurns.append(userBubble(text));
  if (liveTurn) liveTurn.hidden = false;
  if (thinking) thinking.hidden = false;
  if (thinkingStatus) thinkingStatus.textContent = "生成草稿中，请稍候…";
  packagePanel.hidden = true;
  errorPanel.hidden = true;
  events.replaceChildren();
  const agentMeta = document.querySelector("#agentMeta");
  if (agentMeta) agentMeta.hidden = true;
  runStartedAt = Date.now();
  try {
    await loadAccounts();
    const {url, body} = payloadForSubmit();
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const accepted = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(accepted, "请求未被服务接受"));
    attachTaskUrl(accepted.task_url);
    await new Promise((resolve, reject) => {
      const source = new EventSource(accepted.events_url);
      Object.keys(labels).forEach(type => {
        source.addEventListener(type, async event => {
          const payload = JSON.parse(event.data);
          addEvent(type, payload);
          if (type === "task.completed") {
            source.close();
            try {
              await renderResult(accepted.task_url);
              resolve();
            } catch (error) {
              reject(error);
            }
          }
          if (type === "task.failed") {
            source.close();
            reject(new Error(payload.data?.message || "任务失败"));
          }
        });
      });
      source.onerror = () => {
        source.close();
        reject(new Error("事件连接中断"));
      };
    });
    const box = document.querySelector(scenario === "reply" ? "#replyText" : "#composeText");
    if (box) box.value = "";
  } catch (error) {
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
    if (liveTurn) liveTurn.hidden = true;
    if (thinking) thinking.hidden = true;
    setBoard(threadTurns.children.length ? "done" : "home");
  } finally {
    button.disabled = false;
  }
}

function bindComposerKeys(textarea) {
  textarea.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    document.querySelector("#taskForm").requestSubmit();
  });
}

document.querySelector("#taskForm").addEventListener("submit", submitTask);
bindComposerKeys(document.querySelector("#composeText"));
bindComposerKeys(document.querySelector("#replyText"));

loadAccounts();
