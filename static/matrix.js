const button = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const events = document.querySelector("#events");
const packagePanel = document.querySelector("#package");
const errorPanel = document.querySelector("#error");
const composeForm = document.querySelector("#composeForm");
const replyForm = document.querySelector("#replyForm");

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

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    scenario = tab.dataset.scenario;
    document.querySelectorAll(".tab").forEach(item => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    composeForm.hidden = scenario !== "compose";
    replyForm.hidden = scenario !== "reply";
  });
});

function addEvent(type, payload) {
  const item = document.createElement("li");
  const extras = [];
  if (payload.data?.stage) extras.push(payload.data.stage);
  if (payload.data?.work_item_id) extras.push(payload.data.work_item_id);
  if (payload.data?.draft_key) extras.push(payload.data.draft_key);
  if (payload.data?.degrade_op) extras.push(degradeLabels[payload.data.degrade_op] || payload.data.degrade_op);
  item.textContent = `${labels[type] || type}${extras.length ? `：${extras.join(" · ")}` : ""}`;
  events.appendChild(item);
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

function renderDraft(draft) {
  const card = document.createElement("article");
  card.className = `draft-card status-${draft.status}`;
  const header = document.createElement("header");
  const title = document.createElement("h3");
  title.textContent = draft.platform_key;
  const chips = document.createElement("div");
  chips.className = "chips";
  chips.append(
    chip(decisionLabels[draft.decision] || draft.decision, draft.decision === "skip" ? "skip" : "ok"),
    chip(degradeLabels[draft.degrade_op] || draft.degrade_op, draft.degrade_op),
    chip(statusLabels[draft.status] || draft.status, draft.status)
  );
  if (draft.source_comment_key) {
    chips.append(chip(draft.source_comment_key, "meta"));
  }
  header.append(title, chips);
  const text = document.createElement("p");
  text.className = "draft-text";
  text.textContent = draft.text || "（正文已清空）";
  const rationale = document.createElement("p");
  rationale.className = "rationale";
  rationale.textContent = draft.rationale || "";
  const meta = document.createElement("p");
  meta.className = "meta";
  const evidence = (draft.evidence_ids || []).join("、") || "无引用";
  const issues = (draft.issues || []).join("、") || "无触线";
  meta.textContent = `${draft.draft_key} · 证据 ${evidence} · 触线 ${issues}`;
  card.append(header, text, rationale, meta);
  return card;
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
    row.textContent = value;
    return row;
  });
}

async function renderResult(taskUrl) {
  const response = await fetch(taskUrl);
  const snapshot = await response.json();
  if (snapshot.status === "failed") throw new Error(snapshot.error || "任务失败");
  const result = snapshot.result;
  document.querySelector("#summary").textContent = result.summary;
  document.querySelector("#packageMeta").textContent =
    `状态 ${result.status} · 类型 ${result.task_type === "compose_post" ? "创作" : "回复"} · ${result.drafts.length} 条草稿`;
  document.querySelector("#drafts").replaceChildren(...result.drafts.map(renderDraft));
  document.querySelector("#evidence").replaceChildren(
    ...listItems(
      (result.evidence || []).map(item => `${item.ref_id} · ${item.title}：${item.ruling}`),
      "本次没有召回案例卡片。"
    )
  );
  document.querySelector("#limitations").replaceChildren(
    ...listItems(result.limitations || [], "没有额外限制说明。")
  );
  document.querySelector("#snapshot").textContent =
    `快照：${result.snapshot_id} · Trace：${result.trace_ref}`;
  packagePanel.hidden = false;
}

function payloadForSubmit() {
  if (scenario === "reply") {
    return {
      url: "/api/reply",
      body: {
        text: document.querySelector("#replyText").value.trim(),
        thread_key: document.querySelector("#threadKey").value.trim(),
        channel: "web"
      }
    };
  }
  const platformKeys = [...document.querySelectorAll("input[name=platform]:checked")]
    .map(item => item.value);
  return {
    url: "/api/create",
    body: {
      text: document.querySelector("#composeText").value.trim(),
      platform_keys: platformKeys,
      need_trends: document.querySelector("#needTrends").checked,
      channel: "web"
    }
  };
}

button.addEventListener("click", async () => {
  button.disabled = true;
  progress.hidden = false;
  packagePanel.hidden = true;
  errorPanel.hidden = true;
  events.replaceChildren();
  try {
    const {url, body} = payloadForSubmit();
    if (!body.text) throw new Error("请先填写主题或指令");
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const accepted = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(accepted, "请求未被服务接受"));
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
  } catch (error) {
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
  } finally {
    button.disabled = false;
  }
});
