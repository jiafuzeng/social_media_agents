const TOKEN_KEY = "matrix_user_token";
let currentUser = null;
let userMenuOpen = false;
let userAccounts = [];
let selectedUserId = null;
let creatingUser = false;

const AUTH_ERRORS = {
  "missing token": "请先登录",
  "invalid token": "登录已失效，请重新登录",
  "invalid username or password": "用户名或密码不正确",
  "username already exists": "用户名已存在",
  "username must be 2-32 letters, digits or underscore": "用户名为 2–32 位字母、数字或下划线",
  "password must be at least 6 characters": "密码至少 6 位",
  "current password is required": "改自己的账号必须填写当前登录密码，不是新密码",
  "no user fields to update": "没有要保存的更改",
  "cannot delete the last user": "不能删除最后一个用户",
  "cannot delete the last admin": "不能删除最后一个管理员",
  "cannot demote the last admin": "不能取消最后一个管理员",
  "admin role required": "需要管理员权限",
  "role must be admin or user": "角色只能是管理员或普通用户",
  "user not found": "用户不存在",
  "session not found": "会话不存在",
  "session access denied": "不能访问别人的会话",
  "collection not found": "收藏夹不存在",
  "collection access denied": "不能访问别人的收藏夹",
  "collection item not found": "收藏条目不存在",
  "folder name already exists": "已有同名文件夹",
  "folder name must not be empty": "请填写文件夹名称",
  "unknown embedding_profile_id": "未知的 embedding 模型",
  "cannot change embedding_profile_id without rechunk": "更换向量模型必须重新切分",
  "embedding_profile_id does not match document": "本文档已锁定该向量模型",
  "too many chunks": "分段超过 2000 块上限",
  "document not found": "文档不存在",
  "chunk not found": "分段不存在",
  "forbidden": "不能访问别人的知识库文档",
  "document has no file": "这篇没有原件",
  "file is required": "请上传文件",
  "text is required to rechunk": "重新切分需要正文或原件",
  "embedding_profile_id is required": "检索必须选择 embedding 模型",
  "kb_chat_failed": "生成回答失败，召回结果仍可用",
  "kb_analyze_failed": "召回要点分析失败，已按切片原文作答",
  "collapsed_cite": "回答未覆盖全部切片，已按要点重写引用",
  "embedding_profile_id is required for semantic": "按语义切分需要当前 embedding 模型",
  "text is empty": "请先填写文本",
  "unsupported file type": "只支持 txt / markdown / pdf / docx / pptx / html",
  "file too large": "单个文件不超过 50 MB",
  "extracted text is empty": "没抽出文本",
  "text file is not valid utf-8": "文本不是合法 UTF-8",
  "invalid pdf": "无法读取这个 PDF",
  "invalid docx": "无法读取这个 Word",
  "invalid pptx": "无法读取这个 PPT",
  "chunk_overlap must be less than chunk_size": "overlap 必须小于 chunk_size",
  "status must be active or archived": "会话状态无效",
  "title must not be empty": "会话标题不能为空"
};

function isAdmin() {
  return currentUser?.role === "admin";
}

function roleLabel(role) {
  return role === "admin" ? "管理员" : "普通用户";
}

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function authDetail(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return AUTH_ERRORS[detail] || detail;
  return fallback;
}

async function authRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {})
    }
  });
  const payload = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(authDetail(payload, "请求失败"));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setAuthError(message) {
  const panel = document.querySelector("#authError");
  if (!panel) return;
  panel.hidden = !message;
  panel.textContent = message || "";
}

function setUserFormError(message) {
  const panel = document.querySelector("#userFormError");
  if (!panel) return;
  panel.hidden = !message;
  panel.textContent = message || "";
}

function closeUserMenu() {
  userMenuOpen = false;
  const menu = document.querySelector("#userMenu");
  const button = document.querySelector("#userMenuBtn");
  if (menu) menu.hidden = true;
  if (button) button.setAttribute("aria-expanded", "false");
}

function placeUserMenu() {
  const button = document.querySelector("#userMenuBtn");
  const menu = document.querySelector("#userMenu");
  if (!button || !menu || menu.hidden) return;
  const rect = button.getBoundingClientRect();
  menu.style.left = `${Math.round(rect.right + 12)}px`;
  menu.style.top = `${Math.round(rect.top + rect.height / 2)}px`;
  menu.style.transform = "translateY(-50%)";
}

function toggleUserMenu() {
  if (!currentUser) return;
  userMenuOpen = !userMenuOpen;
  const menu = document.querySelector("#userMenu");
  const button = document.querySelector("#userMenuBtn");
  if (menu) menu.hidden = !userMenuOpen;
  if (button) button.setAttribute("aria-expanded", String(userMenuOpen));
  if (userMenuOpen) placeUserMenu();
}

function showAuthedUser(user) {
  currentUser = user;
  document.documentElement.classList.remove("matrix-auth-pending");
  document.body.classList.remove("auth-locked");
  const avatar = document.querySelector("#userMenuBtn");
  const name = document.querySelector("#userMenuName");
  const role = document.querySelector("#userMenuRole");
  const glyph = (user.username || "?").slice(0, 1).toUpperCase();
  if (avatar) {
    avatar.textContent = glyph;
    avatar.title = `${user.username} · ${roleLabel(user.role)}`;
  }
  if (name) name.textContent = user.username;
  if (role) role.textContent = roleLabel(user.role);
  const drawerAvatar = document.querySelector("#userDrawerAvatar");
  if (drawerAvatar) drawerAvatar.textContent = glyph;
  window.dispatchEvent(new CustomEvent("matrix-auth-changed", { detail: { user } }));
}

function showAuthGate() {
  currentUser = null;
  userAccounts = [];
  selectedUserId = null;
  creatingUser = false;
  closeUserMenu();
  closeUserDrawer();
  document.documentElement.classList.remove("matrix-auth-pending");
  document.body.classList.add("auth-locked");
  const avatar = document.querySelector("#userMenuBtn");
  const name = document.querySelector("#userMenuName");
  const role = document.querySelector("#userMenuRole");
  if (avatar) {
    avatar.textContent = "?";
    avatar.title = "未登录";
  }
  if (name) name.textContent = "未登录";
  if (role) role.textContent = "本机 SQLite 账号";
  window.dispatchEvent(new CustomEvent("matrix-auth-changed", { detail: { user: null } }));
}

async function restoreAuth() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    showAuthGate();
    return;
  }
  try {
    showAuthedUser(await authRequest("/api/users/me"));
  } catch {
    localStorage.removeItem(TOKEN_KEY);
    showAuthGate();
  }
}

async function submitAuth(kind) {
  const username = document.querySelector("#authUsername")?.value.trim() || "";
  const password = document.querySelector("#authPassword")?.value || "";
  setAuthError("");
  try {
    const payload = await authRequest(
      kind === "register" ? "/api/users/register" : "/api/users/login",
      { method: "POST", body: JSON.stringify({ username, password }) }
    );
    localStorage.setItem(TOKEN_KEY, payload.token);
    showAuthedUser(payload.user);
  } catch (error) {
    setAuthError(error.message);
  }
}

async function logoutUser() {
  closeUserMenu();
  try {
    await authRequest("/api/users/logout", { method: "POST" });
  } catch {
    // 本地凭证仍要清掉，避免失效 token 残留。
  }
  localStorage.removeItem(TOKEN_KEY);
  showAuthGate();
}

function closeUserDrawer() {
  const drawer = document.querySelector("#userDrawer");
  if (drawer) drawer.hidden = true;
}

function syncAdminControls() {
  const admin = isAdmin();
  const createBtn = document.querySelector("#userCreateBtn");
  const roleWrap = document.querySelector("#userRoleWrap");
  const deleteBtn = document.querySelector("#userDeleteBtn");
  if (createBtn) createBtn.hidden = !admin;
  if (roleWrap) roleWrap.hidden = !admin;
  if (deleteBtn) deleteBtn.hidden = creatingUser || !admin;
}

function fillUserForm(user) {
  creatingUser = !user;
  selectedUserId = user?.user_id || null;
  const username = document.querySelector("#userFormUsername");
  const password = document.querySelector("#userFormPassword");
  const password2 = document.querySelector("#userFormPassword2");
  const current = document.querySelector("#userFormCurrentPassword");
  const roleSelect = document.querySelector("#userFormRole");
  const passwordLabel = document.querySelector("#userPasswordLabel");
  const password2Label = document.querySelector("#userPassword2Label");
  const hint = document.querySelector("#userFormHint");
  const wrap = document.querySelector("#userCurrentPasswordWrap");
  if (username) username.value = user?.username || "";
  if (passwordLabel) passwordLabel.textContent = creatingUser ? "登录密码" : "新密码";
  if (password2Label) password2Label.textContent = creatingUser ? "确认登录密码" : "确认新密码";
  if (password) {
    password.value = "";
    password.required = creatingUser;
    password.placeholder = creatingUser ? "设置登录密码，至少 6 位" : "不修改则留空";
  }
  if (password2) {
    password2.value = "";
    password2.required = creatingUser;
    password2.placeholder = creatingUser ? "再输入一次登录密码" : "再输入一次新密码";
  }
  if (current) current.value = "";
  if (roleSelect) roleSelect.value = user?.role || "user";
  const editingSelf = Boolean(user && currentUser && user.user_id === currentUser.user_id);
  if (wrap) wrap.hidden = creatingUser || !editingSelf;
  syncAdminControls();
  if (hint) {
    hint.textContent = creatingUser
      ? "这里设置的是该账号的登录密码。新建后不会切换当前登录。"
      : editingSelf
        ? "改用户名或设新密码时，必须填写当前登录密码。新密码留空表示不改。"
        : "可重置该账号的登录密码；新密码留空表示只改用户名。";
  }
  setUserFormError("");
  renderUserList();
}

function renderUserList() {
  const list = document.querySelector("#userList");
  if (!list) return;
  list.replaceChildren();
  if (creatingUser) {
    const draft = document.createElement("button");
    draft.type = "button";
    draft.className = "user-list-item active";
    draft.textContent = "新用户";
    list.append(draft);
  }
  for (const user of userAccounts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "user-list-item";
    if (!creatingUser && user.user_id === selectedUserId) item.classList.add("active");
    item.innerHTML = `<span>${user.username}</span>`;
    const mark = document.createElement("small");
    const parts = [roleLabel(user.role)];
    if (currentUser && user.user_id === currentUser.user_id) parts.push("当前");
    mark.textContent = parts.join(" · ");
    item.append(mark);
    item.addEventListener("click", () => fillUserForm(user));
    list.append(item);
  }
}

async function loadUsers() {
  const payload = await authRequest("/api/users");
  userAccounts = payload.users || [];
  const selected = userAccounts.find(item => item.user_id === (selectedUserId || currentUser?.user_id));
  fillUserForm(selected || userAccounts[0] || null);
}

async function openUserDrawer() {
  closeUserMenu();
  const drawer = document.querySelector("#userDrawer");
  if (drawer) drawer.hidden = false;
  setUserFormError("");
  syncAdminControls();
  try {
    await loadUsers();
  } catch (error) {
    setUserFormError(error.message);
  }
}

async function saveUser(event) {
  event.preventDefault();
  const username = document.querySelector("#userFormUsername")?.value.trim() || "";
  const password = document.querySelector("#userFormPassword")?.value || "";
  const password2 = document.querySelector("#userFormPassword2")?.value || "";
  const currentPassword = document.querySelector("#userFormCurrentPassword")?.value || "";
  setUserFormError("");
  if (password || password2) {
    if (password !== password2) {
      setUserFormError(creatingUser ? "两次输入的登录密码不一致" : "两次输入的新密码不一致");
      return;
    }
  }
  try {
    if (creatingUser) {
      if (!password) {
        setUserFormError("新建用户必须设置登录密码");
        return;
      }
      const created = await authRequest("/api/users", {
        method: "POST",
        body: JSON.stringify({
          username,
          password,
          role: document.querySelector("#userFormRole")?.value || "user"
        })
      });
      selectedUserId = created.user_id;
      await loadUsers();
      return;
    }
    const body = { username };
    if (password) body.new_password = password;
    if (currentPassword) body.current_password = currentPassword;
    if (isAdmin()) body.role = document.querySelector("#userFormRole")?.value || "user";
    const updated = await authRequest(`/api/users/${selectedUserId}`, {
      method: "PATCH",
      body: JSON.stringify(body)
    });
    if (currentUser && updated.user_id === currentUser.user_id) {
      showAuthedUser(updated);
    }
    selectedUserId = updated.user_id;
    await loadUsers();
  } catch (error) {
    setUserFormError(error.message);
  }
}

async function deleteSelectedUser() {
  if (creatingUser || !selectedUserId) return;
  const target = userAccounts.find(item => item.user_id === selectedUserId);
  const label = target?.username || "该用户";
  if (!window.confirm(`确定删除用户 ${label}？此操作不可恢复。`)) return;
  setUserFormError("");
  const deletingSelf = currentUser && selectedUserId === currentUser.user_id;
  try {
    await authRequest(`/api/users/${selectedUserId}`, { method: "DELETE" });
    if (deletingSelf) {
      localStorage.removeItem(TOKEN_KEY);
      showAuthGate();
      return;
    }
    selectedUserId = currentUser?.user_id || null;
    await loadUsers();
  } catch (error) {
    setUserFormError(error.message);
  }
}

function bindAuth() {
  document.querySelector("#authForm")?.addEventListener("submit", event => {
    event.preventDefault();
    submitAuth("login");
  });
  document.querySelector("#authRegister")?.addEventListener("click", () => submitAuth("register"));
  document.querySelector("#userMenuBtn")?.addEventListener("click", event => {
    event.stopPropagation();
    toggleUserMenu();
  });
  document.querySelector("#userManageBtn")?.addEventListener("click", openUserDrawer);
  document.querySelector("#userLogoutBtn")?.addEventListener("click", logoutUser);
  document.querySelector("#userDrawerClose")?.addEventListener("click", closeUserDrawer);
  document.querySelector("#userBackdrop")?.addEventListener("click", closeUserDrawer);
  document.querySelector("#userCreateBtn")?.addEventListener("click", () => fillUserForm(null));
  document.querySelector("#userForm")?.addEventListener("submit", saveUser);
  document.querySelector("#userDeleteBtn")?.addEventListener("click", deleteSelectedUser);
  document.addEventListener("click", event => {
    if (!userMenuOpen) return;
    if (event.target.closest("#userSlot, #userMenu")) return;
    closeUserMenu();
  });
  window.addEventListener("resize", () => {
    if (userMenuOpen) placeUserMenu();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    closeUserMenu();
    closeUserDrawer();
  });
  restoreAuth();
}

bindAuth();
window.matrixAuth = {
  headers: authHeaders,
  user: () => currentUser,
  errorText: message => {
    const text = String(message || "");
    if (AUTH_ERRORS[text]) return AUTH_ERRORS[text];
    const key = Object.keys(AUTH_ERRORS).find(
      item => text === item || text.startsWith(`${item}:`) || text.startsWith(`${item} `)
    );
    return key ? AUTH_ERRORS[key] : text;
  }
};
