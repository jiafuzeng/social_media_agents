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
    ["guardrail_keys", "护栏 keys（一行一个）", "list"],
    ["term_list_keys", "硬禁词 keys（一行一个）", "list"]
  ],
  interactions: [
    ["interaction_key", "规则 key", "text"],
    ["display_name", "显示名", "text"],
    ["one_liner", "一句话", "text"],
    ["voice_summary", "声量", "text"],
    ["goals", "目标（一行一条）", "list"],
    ["skip_guidance", "何时 skip", "textarea"],
    ["must_do", "必做", "list"],
    ["must_not", "禁做", "list"],
    ["guardrail_keys", "护栏 keys（一行一个）", "list"],
    ["term_list_keys", "硬禁词 keys（一行一个）", "list"]
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
    ["term_list_id", "清单 id", "text"],
    ["display_name", "显示名", "text"],
    ["summary", "摘要", "text"],
    ["disclaimer", "说明", "text"],
    ["terms", "拦截词（一行一个）", "list"]
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
  return catalogDump[catalogKind] || [];
}

function recordKey(record) {
  return (
    record.account_key ||
    record.interaction_key ||
    record.guardrail_key ||
    record.platform_key ||
    record.template_key ||
    record.term_list_id ||
    ""
  );
}

function playbookTags(record) {
  return [...(record.term_list_keys || []), ...(record.guardrail_keys || [])].slice(0, 4);
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
      tags: playbookTags(record)
    };
  }
  if (catalogKind === "interactions") {
    return {
      key: record.interaction_key,
      title: record.display_name || record.interaction_key,
      subtitle: record.interaction_key,
      body: record.one_liner || record.voice_summary || "",
      tags: playbookTags(record)
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
    title: record.display_name || record.term_list_id || "硬禁词",
    subtitle: record.summary || record.disclaimer || "过门字面拦截",
    body: record.disclaimer || "草稿写完后按这些词做子串扫描。命中会降级成模板或清空正文，不靠模型自觉。和护栏里的语义禁区不是一回事。",
    tags: (record.terms || []).slice(0, 6)
  };
}

function kindLabel() {
  return {
    accounts: "人设",
    interactions: "互动规则",
    guardrails: "护栏",
    platforms: "平台",
    policy: "硬禁词",
    templates: "模板"
  }[catalogKind];
}

function kindHint() {
  return {
    accounts: "人设决定写帖的身份、获客目标和声量。",
    interactions: "互动规则只约束回评：该不该回、怎么回，不带涨粉任务。",
    guardrails: "护栏是给人设/互动规则挂的语义禁区，写进模型提示，不扫正文。",
    platforms: "平台约束字数、条数和提及规则。",
    policy: "硬禁词按分类挂在人设和互动规则上；Gate 扫描该条绑定分类的并集，正文出现这些词就会降级或清空。",
    templates: "核准模板用于硬门降级后的替补正文。"
  }[catalogKind];
}

function selectedRecord() {
  if (!catalogDump) return {};
  const keyName = {
    accounts: "account_key",
    interactions: "interaction_key",
    guardrails: "guardrail_key",
    platforms: "platform_key",
    templates: "template_key",
    policy: "term_list_id"
  }[catalogKind];
  return (catalogDump[catalogKind] || []).find(item => item[keyName] === catalogSelected) || {};
}

function blankRecord() {
  const record = {};
  catalogFields[catalogKind].forEach(([name, , type]) => {
    record[name] = type === "list" ? [] : type === "number" ? 1 : "";
  });
  if (catalogKind === "accounts") {
    record.guardrail_keys = ["default"];
    record.term_list_keys = ["baseline"];
  }
  if (catalogKind === "interactions") {
    record.guardrail_keys = ["default"];
    record.term_list_keys = ["baseline"];
  }
  if (catalogKind === "policy") record.terms = ["示例词"];
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
      if ((name.endsWith("_key") || name === "term_list_id") && !catalogCreating) input.readOnly = true;
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

function previewLabel(kind, item) {
  if (kind === "accounts") {
    const name = item.display_name || item.account_key;
    return name.includes(" / ") ? name.split(" / ").pop() : name;
  }
  if (kind === "interactions") return item.display_name || item.interaction_key;
  if (kind === "guardrails") return item.guardrail_key;
  if (kind === "platforms") return item.platform_key;
  if (kind === "policy") return item.display_name || item.term_list_id;
  return item.template_key;
}

function previewKey(kind, item) {
  if (kind === "accounts") return item.account_key;
  if (kind === "interactions") return item.interaction_key;
  if (kind === "guardrails") return item.guardrail_key;
  if (kind === "platforms") return item.platform_key;
  if (kind === "policy") return item.term_list_id;
  return item.template_key;
}

function renderSceneCard(scene) {
  const card = document.createElement("article");
  card.className = `scene-card${scene.kind === catalogKind ? " active" : ""}`;
  card.dataset.scene = scene.kind;
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  const cover = document.createElement("div");
  cover.className = "scene-cover";
  const title = document.createElement("strong");
  title.className = "scene-title";
  title.textContent = scene.title;
  const list = document.createElement("div");
  list.className = "scene-list";
  scene.items.slice(0, 3).forEach(item => {
    const row = document.createElement("div");
    row.className = "scene-item";
    const label = document.createElement("span");
    label.textContent = item.label;
    row.append(avatarNode(item.label, item.key), label);
    list.append(row);
  });
  card.append(cover, title, list);
  card.addEventListener("click", () => setCatalogKind(scene.kind));
  card.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setCatalogKind(scene.kind);
  });
  return card;
}

function modulePreview(kind, records) {
  return (records || []).map(item => ({
    key: previewKey(kind, item),
    label: previewLabel(kind, item)
  }));
}

function renderScenarios() {
  const host = document.querySelector("#catalogScenarios");
  if (!host || !catalogDump) return;
  const left = host.scrollLeft;
  const scenes = [
    { kind: "accounts", title: "人设", items: modulePreview("accounts", catalogDump.accounts) },
    { kind: "interactions", title: "互动规则", items: modulePreview("interactions", catalogDump.interactions) },
    { kind: "guardrails", title: "护栏", items: modulePreview("guardrails", catalogDump.guardrails) },
    { kind: "platforms", title: "平台", items: modulePreview("platforms", catalogDump.platforms) },
    { kind: "policy", title: "硬禁词", items: modulePreview("policy", catalogDump.policy) },
    { kind: "templates", title: "模板", items: modulePreview("templates", catalogDump.templates) }
  ];
  host.replaceChildren(...scenes.map(renderSceneCard));
  host.scrollLeft = left;
  syncSceneNav();
}

function sceneScrollStep() {
  const track = document.querySelector("#catalogScenarios");
  const card = track && track.querySelector(".scene-card");
  if (!card) return 250;
  const styles = getComputedStyle(track);
  const gap = Number.parseFloat(styles.columnGap || styles.gap) || 16;
  return card.getBoundingClientRect().width + gap;
}

function syncSceneNav() {
  const track = document.querySelector("#catalogScenarios");
  const prev = document.querySelector("#scenePrev");
  const next = document.querySelector("#sceneNext");
  if (!track || !prev || !next) return;
  const max = Math.max(0, track.scrollWidth - track.clientWidth - 1);
  prev.disabled = track.scrollLeft <= 0;
  next.disabled = track.scrollLeft >= max;
}

function bindSceneCarousel() {
  const track = document.querySelector("#catalogScenarios");
  const prev = document.querySelector("#scenePrev");
  const next = document.querySelector("#sceneNext");
  if (!track || !prev || !next) return;
  prev.addEventListener("click", () => {
    track.scrollBy({ left: -sceneScrollStep(), behavior: "smooth" });
  });
  next.addEventListener("click", () => {
    track.scrollBy({ left: sceneScrollStep(), behavior: "smooth" });
  });
  track.addEventListener("scroll", syncSceneNav, { passive: true });
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
  card.textContent = `新建${kindLabel()}`;
  card.addEventListener("click", () => {
    document.querySelector("#catalogNew").click();
  });
  return card;
}

function renderCatalogList() {
  if (!catalogList) return;
  const items = catalogItems();
  if (!catalogCreating && items.length && catalogSelected && !items.some(item => item.key === catalogSelected)) {
    catalogSelected = "";
  }
  const tiles = items.map(renderExpertTile);
  tiles.push(renderAddTile());
  catalogList.replaceChildren(...tiles);
  const hint = document.querySelector("#catalogKindHint");
  if (hint) hint.textContent = kindHint();
  renderScenarios();
}

function setCatalogKind(kind) {
  catalogKind = kind;
  catalogCreating = false;
  catalogSelected = "";
  catalogTabs?.querySelectorAll(".tab").forEach(item => {
    item.classList.toggle("active", item.dataset.kind === kind);
  });
  const drawer = document.querySelector("#catalogDrawer");
  if (drawer) drawer.hidden = true;
  catalogCreating = false;
  renderCatalogList();
}

async function loadCatalog() {
  const response = await fetch("/api/catalog");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "配置加载失败");
  catalogDump = payload;
  renderCatalogList();
}

function catalogStatusMessage(text) {
  if (catalogStatus) catalogStatus.textContent = text;
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
  if (catalogKind === "interactions") return `/api/catalog/interactions/${record.interaction_key}`;
  if (catalogKind === "guardrails") return `/api/catalog/guardrails/${record.guardrail_key}`;
  if (catalogKind === "platforms") return `/api/catalog/platforms/${record.platform_key}`;
  if (catalogKind === "templates") return `/api/catalog/templates/${record.template_key}`;
  return `/api/catalog/policy/${record.term_list_id}`;
}

function collectionUrl() {
  if (catalogKind === "accounts") return "/api/catalog/accounts";
  if (catalogKind === "interactions") return "/api/catalog/interactions";
  if (catalogKind === "guardrails") return "/api/catalog/guardrails";
  if (catalogKind === "platforms") return "/api/catalog/platforms";
  if (catalogKind === "templates") return "/api/catalog/templates";
  return "/api/catalog/policy";
}

function parseAttachPrompt(text) {
  const match = String(text || "").trim().match(/^(g|t|护栏|硬禁词)\s*[:：]?\s*(.+)$/i);
  if (!match) return null;
  const kind = match[1].toLowerCase() === "t" || match[1] === "硬禁词" ? "term" : "guardrail";
  const key = match[2].trim();
  return key ? { kind, key } : null;
}

catalogTabs?.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => setCatalogKind(tab.dataset.kind));
});

document.querySelector("#catalogDrawerClose")?.addEventListener("click", () => closeDrawer());
document.querySelector("#catalogBackdrop")?.addEventListener("click", () => closeDrawer());
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("#catalogDrawer").hidden) closeDrawer();
});

document.querySelector("#catalogNew")?.addEventListener("click", () => {
  catalogCreating = true;
  catalogSelected = "";
  renderCatalogList();
  openDrawer();
});

document.querySelector("#catalogSave")?.addEventListener("click", async () => {
  try {
    const record = readForm();
    if (catalogCreating) {
      await catalogRequest(collectionUrl(), { method: "POST", body: JSON.stringify(record) });
      catalogSelected = recordKey(record);
      catalogCreating = false;
    } else {
      await catalogRequest(resourceUrl(record), { method: "PUT", body: JSON.stringify(record) });
    }
    await loadCatalog();
    await loadAccounts();
    if (typeof loadInteractions === "function") await loadInteractions();
    if (catalogSelected) openDrawer();
    catalogStatusMessage("已保存，下一单写稿会用新配置。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

document.querySelector("#catalogDelete")?.addEventListener("click", async () => {
  try {
    const record = readForm();
    await catalogRequest(resourceUrl(record), { method: "DELETE" });
    catalogSelected = "";
    catalogCreating = false;
    await loadCatalog();
    await loadAccounts();
    if (typeof loadInteractions === "function") await loadInteractions();
    closeDrawer();
    catalogStatusMessage("已删除。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

document.querySelector("#catalogInsert")?.addEventListener("click", async () => {
  try {
    if (catalogKind === "policy") {
      if (!catalogSelected) {
        catalogStatusMessage("先点开一份硬禁词，再插入拦截词。");
        return;
      }
      const term = window.prompt("插入拦截词");
      if (!term) return;
      await catalogRequest(`/api/catalog/policy/${catalogSelected}/terms`, {
        method: "POST",
        body: JSON.stringify({ term: term.trim(), index: 0 })
      });
    } else if (catalogKind === "accounts" || catalogKind === "interactions") {
      if (!catalogSelected) {
        catalogStatusMessage(
          catalogKind === "interactions" ? "先点开一条互动规则，再插入护栏或硬禁词。" : "先点开一个人设，再插入护栏或硬禁词。"
        );
        return;
      }
      const raw = window.prompt("插入护栏填 g:key，硬禁词填 t:key\n例如 g:maker 或 t:finance");
      if (!raw) return;
      const attached = parseAttachPrompt(raw);
      if (!attached) throw new Error("请用 g:护栏key 或 t:硬禁词key");
      const prefix = catalogKind === "interactions" ? "interactions" : "accounts";
      if (attached.kind === "term") {
        await catalogRequest(`/api/catalog/${prefix}/${catalogSelected}/term-lists`, {
          method: "POST",
          body: JSON.stringify({ term_list_id: attached.key, index: 0 })
        });
      } else {
        await catalogRequest(`/api/catalog/${prefix}/${catalogSelected}/guardrails`, {
          method: "POST",
          body: JSON.stringify({ guardrail_key: attached.key, index: 0 })
        });
      }
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
    if (typeof loadInteractions === "function") await loadInteractions();
    if (catalogKind === "policy" || catalogSelected) openDrawer();
    catalogStatusMessage("已插入。");
  } catch (error) {
    catalogStatusMessage(error.message);
  }
});

loadCatalog().catch(error => catalogStatusMessage(error.message));
bindSceneCarousel();
window.addEventListener("matrix-auth-changed", () => {
  loadCatalog().catch(error => catalogStatusMessage(error.message));
});
window.matrixCatalog = { loadCatalog, renderCatalogList, setCatalogKind };
