const catalogTabs = document.querySelector("#catalogTabs");
const catalogList = document.querySelector("#catalogList");
const catalogForm = document.querySelector("#catalogForm");
const catalogStatus = document.querySelector("#catalogStatus");

let catalogKind = "accounts";
let catalogDump = null;
let catalogSelected = "";
let catalogCreating = false;

const catalogFields = {
  accounts: [
    ["account_key", "人设 key", "text"],
    ["display_name", "显示名", "text"],
    ["handle", "Handle", "text"],
    ["one_liner", "一句话", "text"],
    ["voice_summary", "声量", "text"],
    ["background", "背景", "textarea"],
    ["audience", "读者", "text"],
    ["goals", "目标（一行一条）", "list"],
    ["content_pillars", "内容支柱", "list"],
    ["must_do", "必做", "list"],
    ["must_not", "禁做", "list"],
    ["reply_stance", "回复立场", "textarea"],
    ["guardrail_keys", "护栏 keys（一行一个）", "list"]
  ],
  guardrails: [
    ["guardrail_key", "护栏 key", "text"],
    ["forbidden_topics", "禁区主题", "list"],
    ["template_keys", "核准模板", "list"]
  ],
  platforms: [
    ["platform_key", "平台 key", "text"],
    ["max_chars", "字数上限", "number"],
    ["max_posts", "单次条数上限", "number"],
    ["mention_rules", "提及规则", "text"]
  ],
  policy: [
    ["term_list_id", "词表 id", "text"],
    ["disclaimer", "声明", "text"],
    ["terms", "硬词（一行一个）", "list"]
  ],
  templates: [
    ["template_key", "模板 key", "text"],
    ["text", "正文", "textarea"],
    ["claim_types", "claim_types", "list"]
  ]
};

const AVATAR_COLORS = ["#3d7eff", "#12b5cb", "#7c5cfc", "#f08c00", "#e85d4c", "#2f9e44", "#d6336c", "#7048e8"];

function avatarColor(key) {
  const text = String(key || "");
  let n = 0;
  for (const ch of text) n = (n + ch.charCodeAt(0)) % AVATAR_COLORS.length;
  return AVATAR_COLORS[n];
}

function avatarGlyph(label) {
  const text = String(label || "?").replace(/^@/, "").trim();
  return text.slice(0, 1) || "?";
}

function avatarNode(label, key) {
  const node = document.createElement("span");
  node.className = "avatar";
  node.textContent = avatarGlyph(label);
  node.style.background = avatarColor(key || label);
  return node;
}

function catalogRecords() {
  if (!catalogDump) return [];
  if (catalogKind === "policy") return [catalogDump.policy];
  return catalogDump[catalogKind] || [];
}

function recordKey(record) {
  return (
    record.account_key ||
    record.guardrail_key ||
    record.platform_key ||
    record.template_key ||
    record.term_list_id ||
    ""
  );
}

function catalogItems() {
  return catalogRecords().map(record => cardModel(record));
}

function cardModel(record) {
  if (catalogKind === "accounts") {
    return {
      key: record.account_key,
      title: record.display_name || record.account_key,
      subtitle: record.handle || record.account_key,
      body: record.one_liner || record.voice_summary || "",
      tags: (record.content_pillars || record.guardrail_keys || []).slice(0, 3)
    };
  }
  if (catalogKind === "guardrails") {
    return {
      key: record.guardrail_key,
      title: record.guardrail_key,
      subtitle: "护栏包",
      body: (record.forbidden_topics || []).join("、") || "无额外禁区",
      tags: (record.template_keys || []).slice(0, 3)
    };
  }
  if (catalogKind === "platforms") {
    return {
      key: record.platform_key,
      title: record.platform_key,
      subtitle: "发布平台",
      body: record.mention_rules || "平台约束",
      tags: [`${record.max_chars} 字`, `最多 ${record.max_posts} 条`]
    };
  }
  if (catalogKind === "templates") {
    return {
      key: record.template_key,
      title: record.template_key,
      subtitle: "核准模板",
      body: record.text || "",
      tags: (record.claim_types || []).slice(0, 3)
    };
  }
  return {
    key: record.term_list_id,
    title: "硬词表",
    subtitle: record.term_list_id,
    body: record.disclaimer || "",
    tags: (record.terms || []).slice(0, 4)
  };
}

function kindLabel() {
  return {
    accounts: "人设",
    guardrails: "护栏",
    platforms: "平台",
    policy: "词表",
    templates: "模板"
  }[catalogKind];
}

function selectedRecord() {
  if (!catalogDump) return {};
  if (catalogKind === "policy") return catalogDump.policy;
  const keyName = {
    accounts: "account_key",
    guardrails: "guardrail_key",
    platforms: "platform_key",
    templates: "template_key"
  }[catalogKind];
  return (catalogDump[catalogKind] || []).find(item => item[keyName] === catalogSelected) || {};
}

function blankRecord() {
  const record = {};
  catalogFields[catalogKind].forEach(([name, , type]) => {
    record[name] = type === "list" ? [] : type === "number" ? 1 : "";
  });
  if (catalogKind === "accounts") record.guardrail_keys = ["default"];
  if (catalogKind === "platforms") {
    record.max_chars = 280;
    record.max_posts = 10;
  }
  return record;
}

function fieldValue(record, name, type) {
  const value = record[name];
  if (type === "list") return (value || []).join("\n");
  return value == null ? "" : String(value);
}

function readForm() {
  const record = {};
  catalogFields[catalogKind].forEach(([name, , type]) => {
    const node = catalogForm.querySelector(`[name="${name}"]`);
    if (!node) return;
    if (type === "list") {
      record[name] = node.value.split("\n").map(item => item.trim()).filter(Boolean);
    } else if (type === "number") {
      record[name] = Number(node.value);
    } else {
      record[name] = node.value.trim();
    }
  });
  return record;
}

function renderCatalogForm() {
  const record = catalogCreating ? blankRecord() : selectedRecord();
  const card = catalogCreating
    ? { title: `新建${kindLabel()}`, subtitle: kindLabel(), key: "new" }
    : cardModel(record);
  const title = document.querySelector("#catalogDrawerTitle");
  const sub = document.querySelector("#catalogModalSub");
  const avatar = document.querySelector("#catalogModalAvatar");
  if (title) title.textContent = card.title || `编辑${kindLabel()}`;
  if (sub) sub.textContent = card.subtitle || kindLabel();
  if (avatar) {
    avatar.textContent = avatarGlyph(card.title);
    avatar.style.background = avatarColor(card.key || card.title);
  }
  catalogForm.replaceChildren(
    ...catalogFields[catalogKind].map(([name, label, type]) => {
      const wrap = document.createElement("label");
      wrap.className = "field";
      const caption = document.createElement("span");
      caption.className = "field-label";
      caption.textContent = label;
      const input = document.createElement(type === "list" || type === "textarea" ? "textarea" : "input");
      input.name = name;
      if (type === "number") input.type = "number";
      if (type === "list") {
        input.rows = Math.max(4, String(fieldValue(record, name, type)).split("\n").length + 1);
        input.placeholder = "一行一条";
      }
      if (type === "textarea") {
        input.rows = 5;
        input.placeholder = "填写正文";
      }
      if (name.endsWith("_key") && !catalogCreating) input.readOnly = true;
      input.value = fieldValue(record, name, type);
      wrap.append(caption, input);
      return wrap;
    })
  );
}

function openDrawer() {
  const drawer = document.querySelector("#catalogDrawer");
  if (drawer) drawer.hidden = false;
  renderCatalogForm();
}

function closeDrawer() {
  const drawer = document.querySelector("#catalogDrawer");
  if (drawer) drawer.hidden = true;
  catalogCreating = false;
  renderCatalogList();
}

function selectCatalogItem(key) {
  catalogCreating = false;
  catalogSelected = key;
  renderCatalogList();
  openDrawer();
}

function renderSceneCard(scene) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = `scene-card${scene.id === {
    accounts: "compose",
    guardrails: "guard",
    platforms: "ship",
    policy: "ship",
    templates: "ship"
  }[catalogKind] ? " active" : ""}`;
  card.dataset.scene = scene.id;
  const cover = document.createElement("div");
  cover.className = "scene-cover";
  cover.textContent = scene.title;
  const list = document.createElement("div");
  list.className = "scene-list";
  scene.items.forEach(item => {
    const row = document.createElement("div");
    row.className = "scene-item";
    row.append(avatarNode(item.label, item.key), document.createTextNode(item.label));
    list.append(row);
  });
  card.append(cover, list);
  card.addEventListener("click", () => setCatalogKind(scene.kind));
  return card;
}

function renderScenarios() {
  const host = document.querySelector("#catalogScenarios");
  if (!host || !catalogDump) return;
  const accounts = catalogDump.accounts || [];
  const guardrails = catalogDump.guardrails || [];
  const platforms = catalogDump.platforms || [];
  const templates = catalogDump.templates || [];
  const policy = catalogDump.policy || {};
  const scenes = [
    {
      id: "compose",
      kind: "accounts",
      title: "写帖获客",
      items: accounts.slice(0, 3).map(item => ({
        key: item.account_key,
        label: item.display_name || item.account_key
      }))
    },
    {
      id: "reply",
      kind: "accounts",
      title: "回评互动",
      items: accounts.slice(3, 6).map(item => ({
        key: item.account_key,
        label: item.display_name || item.account_key
      }))
    },
    {
      id: "guard",
      kind: "guardrails",
      title: "安全护栏",
      items: guardrails.slice(0, 3).map(item => ({
        key: item.guardrail_key,
        label: item.guardrail_key
      }))
    },
    {
      id: "ship",
      kind: "platforms",
      title: "发布约束",
      items: [
        ...platforms.map(item => ({ key: item.platform_key, label: item.platform_key })),
        { key: policy.term_list_id || "policy", label: "硬词表" },
        ...templates.map(item => ({ key: item.template_key, label: item.template_key }))
      ].slice(0, 3)
    }
  ];
  host.replaceChildren(...scenes.map(renderSceneCard));
}

function renderExpertTile(item) {
  const card = document.createElement("button");
  card.type = "button";
  const selected = item.key === catalogSelected && !catalogCreating;
  card.className = `expert-tile${selected ? " active" : ""}`;
  const head = document.createElement("header");
  const names = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = item.title;
  const subtitle = document.createElement("small");
  subtitle.textContent = item.subtitle;
  names.append(title, subtitle);
  head.append(avatarNode(item.title, item.key), names);
  const body = document.createElement("p");
  body.textContent = item.body;
  const tags = document.createElement("div");
  tags.className = "tile-tags";
  (item.tags || []).forEach(tag => {
    const chip = document.createElement("span");
    chip.textContent = tag;
    tags.append(chip);
  });
  card.append(head, body, tags);
  card.addEventListener("click", () => selectCatalogItem(item.key));
  return card;
}

function renderAddTile() {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "catalog-add";
  card.textContent = catalogKind === "policy" ? "插入硬词" : `新建${kindLabel()}`;
  card.addEventListener("click", () => {
    const action = catalogKind === "policy" ? "#catalogInsert" : "#catalogNew";
    document.querySelector(action).click();
  });
  return card;
}

function renderCatalogList() {
  const items = catalogItems();
  if (!catalogCreating && items.length && catalogSelected && !items.some(item => item.key === catalogSelected)) {
    catalogSelected = "";
  }
  const tiles = items.map(renderExpertTile);
  tiles.push(renderAddTile());
  catalogList.replaceChildren(...tiles);
  renderScenarios();
}

function setCatalogKind(kind) {
  catalogKind = kind;
  catalogCreating = false;
  catalogSelected = "";
  catalogTabs.querySelectorAll(".tab").forEach(item => {
    item.classList.toggle("active", item.dataset.kind === kind);
  });
  closeDrawer();
}

async function loadCatalog() {
  const response = await fetch("/api/catalog");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "配置加载失败");
  catalogDump = payload;
  renderCatalogList();
}

function catalogStatusMessage(text) {
  catalogStatus.textContent = text;
}

async function catalogRequest(url, options) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (response.status === 204) return {};
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === "string" ? detail : "配置请求失败");
  }
  return payload;
}

function resourceUrl(record) {
  if (catalogKind === "accounts") return `/api/catalog/accounts/${record.account_key}`;
  if (catalogKind === "guardrails") return `/api/catalog/guardrails/${record.guardrail_key}`;
  if (catalogKind === "platforms") return `/api/catalog/platforms/${record.platform_key}`;
  if (catalogKind === "templates") return `/api/catalog/templates/${record.template_key}`;
  return "/api/catalog/policy";
}

function collectionUrl() {
  if (catalogKind === "accounts") return "/api/catalog/accounts";
  if (catalogKind === "guardrails") return "/api/catalog/guardrails";
  if (catalogKind === "platforms") return "/api/catalog/platforms";
  if (catalogKind === "templates") return "/api/catalog/templates";
  return "/api/catalog/policy";
}

catalogTabs.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => setCatalogKind(tab.dataset.kind));
});

document.querySelector("#catalogDrawerClose").addEventListener("click", () => closeDrawer());
document.querySelector("#catalogBackdrop").addEventListener("click", () => closeDrawer());
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("#catalogDrawer").hidden) closeDrawer();
});

document.querySelector("#catalogNew").addEventListener("click", () => {
  if (catalogKind === "policy") {
    catalogStatusMessage("词表只有一份，用插入添加硬词。");
    return;
  }
  catalogCreating = true;
  catalogSelected = "";
  renderCatalogList();
  openDrawer();
});

document.querySelector("#catalogSave").addEventListener("click", async () => {
  try {
    const record = readForm();
    if (catalogKind === "policy") {
      await catalogRequest("/api/catalog/policy", { method: "PUT", body: JSON.stringify(record) });
    } else if (catalogCreating) {
      await catalogRequest(collectionUrl(), { method: "POST", body: JSON.stringify(record) });
      catalogSelected = record.account_key || record.guardrail_key || record.platform_key || record.template_key;
      catalogCreating = false;
    } else {
      await catalogRequest(resourceUrl(record), { method: "PUT", body: JSON.stringify(record) });
    }
    await loadCatalog();
    await loadAccounts();
    if (catalogKind === "policy" || catalogSelected) openDrawer();
    catalogStatusMessage("已保存，下一单写稿会用新配置。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

document.querySelector("#catalogDelete").addEventListener("click", async () => {
  try {
    if (catalogKind === "policy") {
      const term = (readForm().terms || [])[0];
      if (!term) throw new Error("没有可删的硬词");
      await catalogRequest(`/api/catalog/policy/terms/${encodeURIComponent(term)}`, { method: "DELETE" });
    } else {
      const record = readForm();
      await catalogRequest(resourceUrl(record), { method: "DELETE" });
      catalogSelected = "";
    }
    catalogCreating = false;
    await loadCatalog();
    await loadAccounts();
    if (catalogKind === "policy") openDrawer();
    else closeDrawer();
    catalogStatusMessage("已删除。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

document.querySelector("#catalogInsert").addEventListener("click", async () => {
  try {
    if (catalogKind === "policy") {
      const term = window.prompt("插入硬词");
      if (!term) return;
      await catalogRequest("/api/catalog/policy/terms", {
        method: "POST",
        body: JSON.stringify({ term: term.trim(), index: 0 })
      });
    } else if (catalogKind === "accounts") {
      if (!catalogSelected) {
        catalogStatusMessage("先点开一个人设，再插入护栏。");
        return;
      }
      const pack = window.prompt("插入护栏 key");
      if (!pack) return;
      await catalogRequest(`/api/catalog/accounts/${catalogSelected}/guardrails`, {
        method: "POST",
        body: JSON.stringify({ guardrail_key: pack.trim(), index: 0 })
      });
    } else {
      catalogCreating = true;
      catalogSelected = "";
      renderCatalogList();
      openDrawer();
      catalogStatusMessage("填好转新建，保存时插入到目录末尾。");
      return;
    }
    await loadCatalog();
    await loadAccounts();
    if (catalogKind === "policy" || catalogSelected) openDrawer();
    catalogStatusMessage("已插入。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

loadCatalog().catch(error => catalogStatusMessage(error.message));
