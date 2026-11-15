/* Star Math — 数学 RAG 智能问答前端逻辑 */
const API = "";
const state = {
  token: localStorage.getItem("rag_token") || "",
  user: JSON.parse(localStorage.getItem("rag_user") || "null"),
  conversationId: null,
  conversations: [],
  questions: [],
  sending: false,
  convEdit: false,
  convSelected: new Set(),
  convExpanded: false,
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

function toast(msg, ms = 2200) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), ms);
}

/** 把后端错误（含 Pydantic 422 数组）翻译成可读中文 */
function readableApiError(data) {
  const detail = (data && data.detail) || data.msg || "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => {
      const loc = (d.loc || []).filter((x) => x !== "body").join(".");
      const type = d.type || "";
      let msg = String(d.msg || "").replace(/^Value error,?\s*/i, "");
      if (type === "string_too_short") msg = "长度不足";
      else if (type === "string_too_long") msg = "长度超出限制";
      if (loc === "question") {
        if (type === "string_too_long") return "问题过长，请控制在 2000 字以内";
        if (/不能为空/.test(msg)) return "问题不能为空";
        return "问题：请控制在 2000 字以内";
      }
      if (loc === "username") return msg.includes("不足") ? "用户名至少 2 个字符" : "用户名：" + msg;
      if (loc === "password") return msg.includes("不足") ? "密码至少 6 个字符" : "密码：" + msg;
      if (loc === "query") return "查询内容不能为空";
      if (loc === "invite_code") return "邀请码：" + (msg || "格式不正确");
      return (loc ? loc + "：" : "") + msg;
    });
    return msgs.join("；") || "请求参数有误";
  }
  return typeof detail === "string" ? detail : (JSON.stringify(detail) || "请求失败");
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(API + path, { ...options, headers });
  // 仅带凭证的请求返回 401 才判定登录过期；登录/注册接口的 401（如“用户名或密码错误”）原样透传
  if (res.status === 401 && state.token && !path.startsWith("/api/auth/")) {
    logout(false);
    throw new Error("登录已过期，请重新登录");
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    throw new Error(readableApiError(typeof data === "string" ? { detail: data } : data));
  }
  return data;
}

function showLogin() {
  $("#login-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
}

function isAdmin() {
  return state.user?.role === "admin";
}

function canDownload() {
  return isAdmin() || !!state.user?.can_download;
}

function showApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  const name = state.user?.username || "用户";
  $("#user-name").textContent = name;
  $("#user-role").textContent = (state.user?.role === "admin" ? "管理员" : "用户") + (canDownload() && !isAdmin() ? " · 可下载" : "");
  $("#user-avatar").textContent = name.slice(0, 1).toUpperCase();
  applyRoleUI();
  loadConversations();
  loadHealth();
}

function applyRoleUI() {
  const admin = isAdmin();
  const dl = canDownload();
  // 知识库管理操作
  const uploadBtn = $("#upload-btn");
  const ingestBtn = $("#ingest-btn");
  if (uploadBtn) {
    if (admin) uploadBtn.style.removeProperty("display");
    else uploadBtn.style.display = "none";
  }
  if (ingestBtn) {
    if (admin) ingestBtn.style.removeProperty("display");
    else ingestBtn.style.display = "none";
  }
  const userCard = $("#settings-users-card");
  if (userCard) {
    if (admin) userCard.classList.remove("hidden");
    else userCard.classList.add("hidden");
  }
  const settingsBtn = $("#settings-btn");
  if (settingsBtn) {
    settingsBtn.title = admin ? "设置（用户与下载权限）" : "设置";
  }
  const roleBar = $("#kb-role-bar");
  if (roleBar) {
    const name = state.user?.username || "";
    roleBar.innerHTML = admin
      ? `<span class="perm-chip admin">管理员 ${escapeHtml(name)}</span>
         <span class="perm-chip on">可上传 / 删除 / 下载 / 授权下载</span>`
      : `<span class="perm-chip off">普通用户 ${escapeHtml(name)}</span>
         <span class="perm-chip ${dl ? "on" : "off"}">${dl ? "可下载" : "无下载权限"}</span>
         <span class="muted small">仅可浏览与预览知识库</span>`;
  }
  // 状态提示
  const note = $(".composer-note");
  if (note) {
    note.textContent = admin
      ? "回答仅依据知识库资料生成。管理员：可管理知识库并授权下载。"
      : dl
        ? "回答仅依据知识库资料生成。你有下载权限。"
        : "回答仅依据知识库资料生成。如需下载知识库文件，请联系管理员。";
  }
}

function logout(show = true) {
  state.token = "";
  state.user = null;
  state.conversationId = null;
  localStorage.removeItem("rag_token");
  localStorage.removeItem("rag_user");
  if (show) showLogin();
}

/* ---------- auth ---------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("#username").value.trim();
  const password = $("#password").value;
  const isRegister = $(".tab.active").dataset.tab === "register";
  const btn = $("#login-btn");
  btn.disabled = true;
  btn.textContent = isRegister ? "注册中…" : "登录中…";
  try {
    const path = isRegister ? "/api/auth/register" : "/api/auth/login";
    const payload = isRegister
      ? { username, password, invite_code: ($("#invite-code").value || "").trim() }
      : { username, password, remember: $("#remember").checked === true };
    const data = await api(path, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.token = data.token;
    state.user = data.user;
    // 同步权限
    try {
      const me = await api("/api/me");
      if (me.user) {
        state.user = {
          id: me.user.uid || me.user.id,
          username: me.user.username,
          role: me.user.role,
          can_download: me.user.can_download,
        };
      }
    } catch {
      /* ignore */
    }
    localStorage.setItem("rag_token", data.token);
    localStorage.setItem("rag_user", JSON.stringify(state.user));
    toast(isRegister ? "注册成功" : "登录成功");
    showApp();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = isRegister ? "注册" : "登录";
  }
});

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const isRegister = tab.dataset.tab === "register";
    $("#login-btn").textContent = isRegister ? "注册" : "登录";
    // 注册显示邀请码，登录显示记住我
    $("#invite-row").classList.toggle("hidden", !isRegister);
    $("#remember-row").classList.toggle("hidden", isRegister);
    // 切换标签时清空输入，避免残留内容误提交
    $("#username").value = "";
    $("#password").value = "";
    $("#invite-code").value = "";
  });
});

$("#logout").addEventListener("click", () => {
  logout(true);
  toast("已退出");
});

/* ---------- 设置弹窗（左下角设置图标） ---------- */
function openSettings() {
  const acc = $("#settings-account");
  if (acc && state.user) {
    acc.innerHTML = `
      <li><span>用户名</span><b>${escapeHtml(state.user.username || "")}</b></li>
      <li><span>角色</span><b>${isAdmin() ? "管理员" : "普通用户"}</b></li>
      <li><span>下载权限</span><b>${canDownload() ? "可下载" : "无下载权限"}</b></li>
      <li><span>登录方式</span><b>${localStorage.getItem("rag_token") ? "已登录（本机记住）" : "未登录"}</b></li>`;
  }
  loadUsers();
  $("#settings-modal").classList.remove("hidden");
}

$("#settings-btn").addEventListener("click", openSettings);

$$("[data-close-modal]").forEach((el) =>
  el.addEventListener("click", () => {
    const map = { settings: "#settings-modal", preview: "#kb-preview-modal" };
    const target = $(map[el.dataset.closeModal]);
    if (target) target.classList.add("hidden");
  })
);

/* ---------- panels ---------- */
$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const panel = btn.dataset.panel;
    $$(".panel").forEach((p) => p.classList.remove("active"));
    $(`#panel-${panel}`).classList.add("active");
    if (panel === "kb") {
      applyRoleUI();
      loadDocs();
    }
    if (panel === "eval") loadEvalConfig();
    if (panel === "practice") loadPracticePanel();
    if (panel === "learn") loadExercisePanel();
  });
});

/* 检索测试折叠卡片 */
$("#debug-toggle").addEventListener("click", () => {
  const panel = $("#debug-panel");
  panel.classList.toggle("open");
  const open = panel.classList.contains("open");
  $("#debug-chevron").textContent = open ? "▴" : "▾";
  if (open) $("#debug-query").focus();
});

/* ---------- conversations ---------- */
async function loadConversations() {
  try {
    const data = await api("/api/conversations");
    state.conversations = data.items || [];
    renderConvs();
  } catch (e) {
    toast(e.message);
  }
}

function renderConvs() {
  const box = $("#conv-list");
  box.innerHTML = "";
  const bar = $("#conv-batch-bar");
  if (bar) bar.classList.toggle("hidden", !state.convEdit);
  const toggleBtn = $("#conv-edit-toggle");
  if (toggleBtn) toggleBtn.textContent = state.convEdit ? "完成" : "管理";

  if (!state.conversations.length) {
    box.innerHTML = '<div class="muted small" style="padding:8px">暂无会话</div>';
    return;
  }
  // 会话列表折叠：默认只显示前 5 条，多于 5 条时提供展开/收起（管理模式下显示全部）
  const CONV_VISIBLE_LIMIT = 5;
  const showAll = state.convEdit || state.convExpanded;
  const visible = showAll ? state.conversations : state.conversations.slice(0, CONV_VISIBLE_LIMIT);
  visible.forEach((c) => {
    const el = document.createElement("div");
    el.className = "conv-item" + (c.id === state.conversationId ? " active" : "");
    el.dataset.id = c.id;
    const checked = state.convSelected.has(c.id) ? "checked" : "";
    if (state.convEdit) {
      el.innerHTML = `
        <input class="conv-check" type="checkbox" ${checked} data-id="${c.id}" />
        <span class="conv-title">${escapeHtml(c.title || "会话")}</span>
        <span class="conv-actions">
          <button class="icon-btn" data-rename="${c.id}" title="重命名">编辑</button>
          <button class="icon-btn danger" data-del="${c.id}" title="删除">删除</button>
        </span>`;
    } else {
      el.innerHTML = `
        <span class="conv-title">${escapeHtml(c.title || "会话")}</span>
        <button class="del" data-del="${c.id}" title="删除">✕</button>`;
    }

    el.addEventListener("click", (e) => {
      const t = e.target;
      if (t.matches(".conv-check")) {
        const id = Number(t.dataset.id);
        if (t.checked) state.convSelected.add(id);
        else state.convSelected.delete(id);
        return;
      }
      if (t.closest("[data-del]")) {
        const id = Number(t.closest("[data-del]").dataset.del);
        deleteConv(id);
        return;
      }
      if (t.closest("[data-rename]")) {
        const id = Number(t.closest("[data-rename]").dataset.rename);
        startRenameConv(el, c);
        return;
      }
      if (state.convEdit) return;
      openConversation(c.id);
    });
    box.appendChild(el);
  });

  const all = $("#conv-check-all");
  if (all) {
    const ids = state.conversations.map((c) => c.id);
    all.checked = ids.length > 0 && ids.every((id) => state.convSelected.has(id));
    all.indeterminate = ids.some((id) => state.convSelected.has(id)) && !all.checked;
  }

  // 超过 5 条时追加展开/收起按钮
  if (!state.convEdit && state.conversations.length > CONV_VISIBLE_LIMIT) {
    const more = document.createElement("div");
    more.className = "conv-more";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conv-more-btn";
    btn.textContent = state.convExpanded
      ? `收起会话（${CONV_VISIBLE_LIMIT}）`
      : `展开更多会话（${state.conversations.length - CONV_VISIBLE_LIMIT}）`;
    btn.addEventListener("click", () => {
      state.convExpanded = !state.convExpanded;
      renderConvs();
    });
    more.appendChild(btn);
    box.appendChild(more);
  }
}

function startRenameConv(el, conv) {
  el.classList.add("editing");
  const titleEl = el.querySelector(".conv-title");
  if (!titleEl) return;
  const input = document.createElement("input");
  input.className = "conv-rename-input";
  input.value = conv.title || "";
  titleEl.replaceWith(input);
  input.focus();
  input.select();
  const save = async () => {
    const title = input.value.trim();
    if (title && title !== conv.title) {
      try {
        await api(`/api/conversations/${conv.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
        if (state.conversationId === conv.id) $("#chat-title").textContent = title;
        toast("已重命名");
      } catch (e) {
        toast(e.message);
      }
    }
    loadConversations();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      loadConversations();
    }
  });
  input.addEventListener("blur", save);
}

function setConvEditMode(on) {
  state.convEdit = on;
  if (!on) state.convSelected.clear();
  renderConvs();
}

async function deleteConv(id) {
  if (!confirm("删除该会话？")) return;
  await api(`/api/conversations/${id}`, { method: "DELETE" });
  state.convSelected.delete(id);
  if (state.conversationId === id) {
    state.conversationId = null;
    resetChatUI();
  }
  loadConversations();
  toast("已删除");
}

async function batchDeleteConvs() {
  const ids = [...state.convSelected];
  if (!ids.length) return toast("请先勾选会话");
  if (!confirm(`删除所选 ${ids.length} 个会话？`)) return;
  try {
    const data = await api("/api/conversations/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    if (state.conversationId && ids.includes(state.conversationId)) {
      state.conversationId = null;
      resetChatUI();
    }
    state.convSelected.clear();
    await loadConversations();
    toast(`已删除 ${data.deleted} 个会话`);
    if (!state.conversations.length) setConvEditMode(false);
  } catch (e) {
    toast(e.message);
  }
}

async function openConversation(id) {
  state.conversationId = id;
  renderConvs();
  const data = await api(`/api/chat/history/${id}`);
  $("#chat-title").textContent = data.conversation?.title || "会话";
  const box = $("#messages");
  box.innerHTML = "";
  (data.items || []).forEach((m) => {
    appendMessage(m.role, m.content, {
      sources: m.sources || [],
      latency: m.latency_ms,
    });
  });
  if (!(data.items || []).length) showWelcome();
}

function resetChatUI() {
  $("#chat-title").textContent = "新会话";
  showWelcome();
  closePreview();
}

function showWelcome() {
  $("#messages").innerHTML = `
    <div class="welcome" id="welcome">
      <div class="welcome-icon">✦</div>
      <h2>有什么可以帮你？</h2>
      <p>提问数学相关问题，系统会检索知识库并给出带来源的回答。</p>
      <div class="suggests">
        <button class="suggest">什么是数列极限？</button>
        <button class="suggest">简述收敛判别法的主要内容。</button>
        <button class="suggest">为什么说微积分基本定理很重要？</button>
        <button class="suggest">定积分与不定积分的关系是什么？</button>
      </div>
    </div>`;
  bindSuggests();
}

function bindSuggests() {
  $$(".suggest").forEach((b) =>
    b.addEventListener("click", () => {
      $("#question").value = b.textContent.trim();
      sendQuestion();
    })
  );
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** F1 公式渲染：把 $...$ / $$...$$ 中的 LaTeX 交给 KaTeX 渲染（renderMarkdown 输出上调用） */
const TEX_ENTITIES = {
  "&gt;": ">", "&lt;": "<", "&amp;": "&", "&quot;": '"', "&#39;": "'",
  "&#x3e;": ">", "&#x3c;": "<", "&#62;": ">", "&#60;": "<", "&#x27;": "'",
};
/** 还原 HTML 实体（LLM/标记解析可能把 < > 转义成实体，KaTeX 不认识） */
function unescapeTex(s) {
  return String(s || "").replace(/&(?:gt|lt|amp|quot|#39|#x3[eEcC]|#62|#60|#x27);/gi, (m) => TEX_ENTITIES[m.toLowerCase()] ?? m);
}
function renderMath(html) {
  if (typeof katex === "undefined" || !html) return html;
  html = html.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => {
    try {
      return katex.renderToString(unescapeTex(tex).trim(), { displayMode: true, throwOnError: false });
    } catch (e) {
      return m;
    }
  });
  html = html.replace(/\$([^$\n]+?)\$/g, (m, tex) => {
    try {
      return katex.renderToString(unescapeTex(tex).trim(), { displayMode: false, throwOnError: false });
    } catch (e) {
      return m;
    }
  });
  return html;
}

/** 裸 LaTeX 兜底：LLM 偶发输出未用 $ 包裹的公式时自动补包。
 *  先在全文层面保护跨行 $$...$$ 块（避免逐行拆碎），再逐行补包裸公式。 */
function autoWrapLatex(src) {
  const text = String(src || "");
  // ① 全文保护跨行块级公式与行内公式（含跨行 $$...$$）
  const held = [];
  const hold = (m) => { held.push(m); return "\u0000H" + (held.length - 1) + "\u0000"; };
  const protectedText = text
    .replace(/\$\$([\s\S]+?)\$\$/g, hold)
    .replace(/\$([^$\n]+?)\$/g, hold);
  // ② 逐行补包裸公式
  return protectedText.split("\n").map((line) => {
    if (!/\\[a-zA-Z]/.test(line)) return line;
    const t = line.trim();
    const hasHan = /[\u4e00-\u9fff]/.test(t);
    const hasHold = line.includes("\u0000H");
    // 块级：行首是 \ 命令，或整行无汉字且含 LaTeX 命令（不设长度上限，超长裸公式行同样块级渲染）；
    // 已含占位片段的行不重复包，避免 $$...$$$ 嵌套
    if (!hasHold && (/^\\/.test(t) || (!hasHan && /\\[a-zA-Z]/.test(t)))) {
      return "$$" + t + "$$";
    }
    // 行内片段：把 LaTeX 命令片段（延伸到汉字 / 中文标点为止）包上行内 $
    const out = line.replace(
      /(\\[a-zA-Z]+(?:[^{}\u4e00-\u9fff。，；：！？、]|\{[^{}]*\})*)/g,
      (m) => "$" + m.trim() + "$"
    );
    return out;
  }).join("\n").replace(/\u0000H(\d+)\u0000/g, (_, i) => held[+i] ?? "");
}

/** 清理知识库原文：去掉 HTML 标签、图片标记，压缩空白，避免来源卡/预览显示原始标签 */
function cleanKbText(s) {
  return String(s ?? "")
    .replace(/<[^>]*>/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "【图片】")
    .replace(/\s+/g, " ")
    .trim();
}

function inlineMd(s) {
  let t = escapeHtml(s);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return t;
}

/** 轻量 Markdown 渲染：标题 / 列表 / 表格 / 粗体 / 代码 / 引用 / 分隔线 */
function renderMarkdown(src) {
  const codes = [];
  // ① 裸 LaTeX 自动补包（防 LLM 未用 $ 包裹导致公式显示为源码乱码）
  let text = autoWrapLatex(src);
  // ② 公式占位保护：先于“去 HTML 标签”，避免公式中的 < > 被当标签吞掉或截断
  const formulas = [];
  text = text
    .replace(/\r\n/g, "\n")
    .replace(/```([\s\S]*?)```/g, (_, c) => {
      codes.push(c.replace(/^\n/, ""));
      return "\n%%CODE" + (codes.length - 1) + "%%\n";
    });
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m) => {
    formulas.push(m);
    return "\n%%F" + (formulas.length - 1) + "%%\n";
  });
  text = text.replace(/\$([^$\n]+?)\$/g, (m) => {
    formulas.push(m);
    return "%%F" + (formulas.length - 1) + "%%";
  });
  // ③ 再去 HTML 标签 / 图片占位
  text = text
    .replace(/<[^>]*>/g, "") // 去 HTML 标签，避免把 </li> 之类原样漏出
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "【图片】"); // 图片标记 → 占位

  const lines = text.split("\n");
  let html = "";
  let inUl = false;
  let inOl = false;
  let inBq = false;
  let tableBuf = [];

  const closeLists = () => {
    if (inUl) { html += "</ul>"; inUl = false; }
    if (inOl) { html += "</ol>"; inOl = false; }
    if (inBq) { html += "</blockquote>"; inBq = false; }
  };
  const flushTable = () => {
    if (!tableBuf.length) return;
    closeLists();
    html += "<table>";
    tableBuf.forEach((r, i) => {
      const cells = r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const sep = /^\|?[\s:|-]+\|?$/.test(r.trim());
      if (sep) return;
      const tag = i === 0 ? "th" : "td";
      html += "<tr>" + cells.map((c) => `<${tag}>${inlineMd(c)}</${tag}>`).join("") + "</tr>";
    });
    html += "</table>";
    tableBuf = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const t = line.trim();
    if (/^%%CODE\d+%%$/.test(t)) {
      flushTable();
      closeLists();
      const idx = Number(t.replace(/%%CODE|%%/g, ""));
      html += `<pre><code>${escapeHtml(codes[idx] || "")}</code></pre>`;
      continue;
    }
    if (t.startsWith("|") && t.endsWith("|")) {
      tableBuf.push(t);
      continue;
    } else {
      flushTable();
    }
    if (!t) {
      closeLists();
      continue;
    }
    if (/^#{1,4}\s+/.test(t)) {
      closeLists();
      const level = t.match(/^#+/)[0].length;
      html += `<h${level}>${inlineMd(t.replace(/^#+\s+/, ""))}</h${level}>`;
      continue;
    }
    if (/^(---|\*\*\*|___)$/.test(t)) {
      closeLists();
      html += "<hr/>";
      continue;
    }
    if (/^>\s?/.test(t)) {
      if (!inBq) {
        closeLists();
        html += "<blockquote>";
        inBq = true;
      }
      html += `<p>${inlineMd(t.replace(/^>\s?/, ""))}</p>`;
      continue;
    }
    if (/^[-*+]\s+/.test(t)) {
      if (inBq) { html += "</blockquote>"; inBq = false; }
      if (!inUl) {
        if (inOl) { html += "</ol>"; inOl = false; }
        html += "<ul>";
        inUl = true;
      }
      html += `<li>${inlineMd(t.replace(/^[-*+]\s+/, ""))}</li>`;
      continue;
    }
    if (/^\d+[.)]\s+/.test(t)) {
      if (inBq) { html += "</blockquote>"; inBq = false; }
      if (!inOl) {
        if (inUl) { html += "</ul>"; inUl = false; }
        html += "<ol>";
        inOl = true;
      }
      html += `<li>${inlineMd(t.replace(/^\d+[.)]\s+/, ""))}</li>`;
      continue;
    }
    closeLists();
    html += `<p>${inlineMd(t)}</p>`;
  }
  flushTable();
  closeLists();
  // ④ 还原公式占位（交给 renderMath 渲染）
  if (formulas.length) {
    html = html.replace(/%%F(\d+)%%/g, (_, i) => formulas[+i] ?? "");
  }
  return html;
}

function setBubbleHtml(bubble, text) {
  bubble.classList.add("md");
  bubble.innerHTML = renderMath(renderMarkdown(text));
  linkifyCitations(bubble);
}

/** 将回答正文中的 [n] 引文转成可点击角标（借鉴 RAGFlow 引文溯源交互） */
function linkifyCitations(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const re = /\[(\d{1,2})\]/g;
  nodes.forEach((node) => {
    const text = node.nodeValue || "";
    re.lastIndex = 0;
    if (!re.test(text)) return;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    let m;
    while ((m = re.exec(text))) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const span = document.createElement("span");
      span.className = "cite";
      span.dataset.cite = m[1];
      span.textContent = m[0];
      frag.appendChild(span);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  });
}

/* ---------- source preview ---------- */
function currentPanel() {
  return document.querySelector(".nav-item.active")?.dataset.panel || "chat";
}

/** 渲染预览内容（分块列表 / 纯文本），bodyEl 为目标容器 */
function renderChunks(chunks) {
  return chunks
    .map(
      (c) => `
    <div class="chunk-block">
      <div class="chunk-head">
        <span class="chunk-idx">#${Number(c.idx) + 1}</span>
        <span>${escapeHtml(c.section || "片段")}</span>
        ${Number(c.parent_idx) >= 0 ? `<span class="pv-chip">父块 #${Number(c.parent_idx) + 1}</span>` : ""}
      </div>
      <div class="chunk-body">${escapeHtml(cleanKbText(c.content))}</div>
    </div>`
    )
    .join("");
}

function renderPreviewBody(bodyEl, src) {
  const metaHtml = `
    <div class="pv-meta">
      ${src.section ? `<span class="pv-chip">${escapeHtml(src.section)}</span>` : ""}
      ${src.page_hint ? `<span class="pv-chip">页码 ${escapeHtml(src.page_hint)}</span>` : ""}
      ${src.doc_id ? `<span class="pv-chip">DOC #${src.doc_id}</span>` : ""}
    </div>`;
  const hasRaw = typeof src.content === "string" && src.content.length > 0;
  const hasChunks = Array.isArray(src.chunks) && src.chunks.length > 0;

  if (hasRaw && hasChunks) {
    // 完整原文默认 + 分块视图切换
    bodyEl.innerHTML =
      metaHtml +
      `<div class="pv-tabs">
         <button type="button" class="pv-tab active" id="pv-tab-raw">完整原文</button>
         <button type="button" class="pv-tab" id="pv-tab-chunks">分块视图（${src.chunks.length}）</button>
       </div>
       <div class="pv-raw-view" id="pv-raw-view">${renderMath(renderMarkdown(src.content))}</div>
       <div class="pv-chunks hidden" id="pv-chunks-view">${renderChunks(src.chunks)}</div>`;
    bodyEl.querySelector("#pv-tab-raw").addEventListener("click", () => {
      bodyEl.querySelector("#pv-tab-raw").classList.add("active");
      bodyEl.querySelector("#pv-tab-chunks").classList.remove("active");
      bodyEl.querySelector("#pv-raw-view").classList.remove("hidden");
      bodyEl.querySelector("#pv-chunks-view").classList.add("hidden");
      bodyEl.querySelector("#pv-raw-view").scrollTop = 0;
    });
    bodyEl.querySelector("#pv-tab-chunks").addEventListener("click", () => {
      bodyEl.querySelector("#pv-tab-raw").classList.remove("active");
      bodyEl.querySelector("#pv-tab-chunks").classList.add("active");
      bodyEl.querySelector("#pv-raw-view").classList.add("hidden");
      bodyEl.querySelector("#pv-chunks-view").classList.remove("hidden");
      bodyEl.querySelector("#pv-chunks-view").scrollTop = 0;
    });
  } else if (hasChunks) {
    bodyEl.innerHTML = metaHtml + `<div class="pv-chunks">${renderChunks(src.chunks)}</div>`;
  } else {
    const content = src.content || src.snippet || "（无正文）";
    bodyEl.innerHTML = metaHtml + `<div class="pv-content">${renderMath(renderMarkdown(content))}</div>`;
  }
}

function previewSubText(src) {
  return [
    src.section,
    src.page_hint ? `p.${src.page_hint}` : "",
    src.score != null ? `相似度 ${Number(src.score).toFixed(3)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function openPreview(src) {
  // 聊天页：右侧来源面板；知识库/评估页：独立弹窗（避免被聊天面板遮挡）
  if (currentPanel() === "chat") {
    const panel = $("#preview-panel");
    const body = $("#preview-body");
    panel.hidden = false;
    $("#preview-title").textContent = src.doc_name || "来源预览";
    $("#preview-sub").textContent = previewSubText(src);
    renderPreviewBody(body, src);
    body.scrollTop = 0;
  } else {
    const modal = $("#kb-preview-modal");
    $("#kb-preview-title").textContent = src.doc_name || "来源预览";
    $("#kb-preview-sub").textContent = previewSubText(src);
    renderPreviewBody($("#kb-preview-body"), src);
    modal.classList.remove("hidden");
  }
}

function closePreview() {
  const panel = $("#preview-panel");
  if (panel) panel.hidden = true;
  document.querySelectorAll(".source-card.active").forEach((x) => x.classList.remove("active"));
}

/** 上传识别后：在习题库中查重，在题目块下方显示相似题列表，点击跳转到学习面板定位 */
async function checkSimilarForBlock(block, stem) {
  const box = block.querySelector(".ex-similar");
  if (!box || !stem || stem.length < 8) return;
  try {
    const resp = await fetch("/api/exercises/similar", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({ stem, top_k: 3 }),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) return;
    const dup = items[0].similarity >= 0.8;
    box.innerHTML = `
      <div class="similar-title ${dup ? "dup" : ""}">
        ${dup ? "⚠ 习题库已有高度相似题，不建议重复存入" : "习题库中相似的题："}
      </div>
      <div class="similar-list">
        ${items.map((it) => `
          <button type="button" class="similar-item" data-item-id="${it.id}">
            <span class="sim-badge">${Math.round(it.similarity * 100)}%</span>
            <span class="sim-src">${escapeHtml(it.source_name || "")}</span>
            <span class="sim-q">${escapeHtml((it.question || "").slice(0, 60))}${(it.question || "").length > 60 ? "…" : ""}</span>
          </button>`).join("")}
      </div>`;
    box.querySelectorAll(".similar-item").forEach((btn) => {
      btn.addEventListener("click", () => jumpToExerciseItem(Number(btn.dataset.itemId)));
    });
  } catch (e) { /* 静默失败 */ }
}

/** 跳转到学习面板并定位到指定习题 */
async function jumpToExerciseItem(itemId) {
  // 切到学习面板：模拟点击导航
  $$(".nav-item").forEach((b) => b.classList.remove("active"));
  const navBtn = document.querySelector('.nav-item[data-panel="learn"]');
  if (navBtn) navBtn.classList.add("active");
  $$(".panel").forEach((p) => p.classList.remove("active"));
  const learnPanel = $("#panel-learn");
  if (learnPanel) learnPanel.classList.add("active");
  await loadExercisePanel();
  const el = document.querySelector(`[data-item-id="${itemId}"]`);
  if (el) {
    const detail = el.querySelector(".ex-book-detail");
    const toggle = el.querySelector("[data-toggle-ex]");
    if (detail) detail.classList.add("open");
    if (toggle) toggle.textContent = "收起";
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("highlight-flash");
    setTimeout(() => el.classList.remove("highlight-flash"), 2500);
  }
}

/** 知识点标签增强：只有知识库里真有对应概念卡才渲染为可点击 chip，否则保持普通文本 */
async function enhanceKnowledgeChips(wrap, topics) {
  if (!wrap || !topics || !topics.length) return;
  try {
    const results = await Promise.all(
      topics.map(async (t) => {
        try {
          const r = await fetch("/api/exercises/knowledge/lookup", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
            body: JSON.stringify({ topic: t }),
          });
          if (!r.ok) return { topic: t, matched: false, doc_id: null };
          const d = await r.json();
          return { topic: t, matched: !!d.matched, doc_id: d.doc_id };
        } catch {
          return { topic: t, matched: false, doc_id: null };
        }
      })
    );
    wrap.innerHTML = results
      .map((it) =>
        it.matched
          ? `<button type="button" class="kb-chip" data-topic="${escapeHtml(it.topic)}" data-doc-id="${it.doc_id || ""}" title="查阅知识库中关于「${escapeHtml(it.topic)}」的原文">${escapeHtml(it.topic)}</button>`
          : `<button type="button" class="kb-chip kb-chip-llm" data-topic="${escapeHtml(it.topic)}" title="知识库里没有，由 AI 即时解释">${escapeHtml(it.topic)}</button>`
      )
      .join('<span class="muted">、</span>');
    wrap.querySelectorAll(".kb-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const isLlm = btn.classList.contains("kb-chip-llm");
        if (isLlm) loadLlmExplain(btn.dataset.topic, btn);
        else loadKbForTopic(btn.dataset.topic, btn, btn.dataset.docId);
      });
    });
  } catch { /* 保持原始文本 */ }
}

/** 知识库里没有该概念：用 LLM 即时生成通俗解释，在右侧面板显示 */
async function loadLlmExplain(topic, btn) {
  if (!topic) return;
  document.querySelectorAll(".kb-chip.active").forEach((x) => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  const panel = $("#preview-panel");
  const body = $("#preview-body");
  panel.hidden = false;
  $("#preview-title").textContent = `AI 解释 · ${topic}`;
  $("#preview-sub").textContent = "知识库未收录，由 DeepSeek 即时生成…";
  body.innerHTML = '<div class="muted" style="padding:16px">正在生成「' + escapeHtml(topic) + '」的通俗解释…</div>';
  try {
    const r = await fetch("/api/exercises/knowledge/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({ topic }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    $("#preview-sub").textContent = "由 DeepSeek 即时生成，未入库";
    body.innerHTML = `<div class="pv-raw-view">${renderMath(renderMarkdown(d.explain || "（无内容）"))}</div>`;
    body.scrollTop = 0;
  } catch (err) {
    $("#preview-sub").textContent = "生成失败";
    body.innerHTML = '<div class="muted" style="padding:16px">生成失败：' + escapeHtml(err.message) + "</div>";
  }
}

/** 点击知识点标签：打开命中文档的完整原文视图（与问答页"查看来源"一致）。docId 由 lookup 接口预先给出，直接用。 */
async function loadKbForTopic(topic, btn, docId) {
  if (!topic) return;
  document.querySelectorAll(".kb-chip.active").forEach((x) => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  const panel = $("#preview-panel");
  const body = $("#preview-body");
  panel.hidden = false;
  $("#preview-title").textContent = `知识库 · ${topic}`;
  $("#preview-sub").textContent = "加载中…";
  body.innerHTML = '<div class="muted" style="padding:16px">正在打开「' + escapeHtml(topic) + '」的知识章节…</div>';
  try {
    if (!docId) {
      body.innerHTML = '<div class="muted" style="padding:16px">知识库里没有关于「' + escapeHtml(topic) + '」的概念卡。</div>';
      $("#preview-sub").textContent = "未找到对应概念";
      return;
    }
    const rawResp = await fetch(`/api/docs/${docId}/raw`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!rawResp.ok) throw new Error("HTTP " + rawResp.status);
    const raw = await rawResp.json();
    $("#preview-title").textContent = raw.name || topic;
    $("#preview-sub").textContent = `知识点：${topic}`;
    openPreview({ doc_name: raw.name, section: topic, content: raw.content, chunks: raw.chunks });
  } catch (err) {
    $("#preview-sub").textContent = "加载失败";
    body.innerHTML = '<div class="muted" style="padding:16px">加载失败：' + escapeHtml(err.message) + "</div>";
  }
}

function initPreviewResizer() {
  const panel = $("#preview-panel");
  const handle = $("#preview-resizer");
  if (!panel || !handle) return;
  let dragging = false;
  handle.addEventListener("mousedown", (e) => {
    dragging = true;
    handle.classList.add("dragging");
    e.preventDefault();
    const onMove = (ev) => {
      if (!dragging) return;
      const rect = panel.getBoundingClientRect();
      let w = rect.right - ev.clientX;
      w = Math.max(280, Math.min(window.innerWidth * 0.55, w));
      panel.style.width = w + "px";
    };
    const onUp = () => {
      dragging = false;
      handle.classList.remove("dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
}

/** 来源列表：1 条全显；超过 2 条只展开前 2，其余折叠可点开 */
function renderSourcesList(sources) {
  const wrap = document.createElement("div");
  wrap.className = "sources";
  if (!sources || !sources.length) return wrap;

  const title = document.createElement("div");
  title.className = "sources-head";
  title.innerHTML = `<span class="sources-label">参考资料</span><span class="muted small">点击卡片可右侧预览原文</span>`;
  wrap.appendChild(title);

  const list = document.createElement("div");
  list.className = "sources-list";
  wrap.appendChild(list);

  const visible = sources.slice(0, 2);
  const rest = sources.slice(2);

  visible.forEach((s) => list.appendChild(makeSourceCard(s)));

  if (rest.length) {
    const moreBox = document.createElement("div");
    moreBox.className = "sources-more";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "sources-toggle";
    toggle.textContent = `展开其余 ${rest.length} 条参考`;
    const extra = document.createElement("div");
    extra.className = "sources-extra hidden";
    rest.forEach((s) => extra.appendChild(makeSourceCard(s)));
    toggle.addEventListener("click", () => {
      const open = extra.classList.toggle("hidden") === false;
      toggle.textContent = open ? "收起参考" : `展开其余 ${rest.length} 条参考`;
      extra.classList.toggle("open", open);
    });
    moreBox.appendChild(toggle);
    moreBox.appendChild(extra);
    wrap.appendChild(moreBox);
  }
  return wrap;
}

function makeSourceCard(s) {
  const card = document.createElement("div");
  card.className = "source-card";
  const snippet = cleanKbText(s.snippet || s.content || "").slice(0, 110);
  card.innerHTML = `
    <div class="s-head">
      <span><i class="src-num">${s.id}</i>${escapeHtml(s.doc_name || "来源")}</span>
      <span class="score">${(s.score ?? 0).toFixed(3)}</span>
    </div>
    <div class="muted">${escapeHtml(s.section || "")} ${s.page_hint ? "· p." + escapeHtml(s.page_hint) : ""}</div>
    <div class="s-snippet">${escapeHtml(snippet)}…</div>`;
  card.addEventListener("click", () => {
    document.querySelectorAll(".source-card.active").forEach((x) => x.classList.remove("active"));
    card.classList.add("active");
    openPreview(s);
  });
  return card;
}

function makeThinking() {
  const el = document.createElement("div");
  el.className = "thinking";
  el.innerHTML = `
    <span class="dots"><i></i><i></i><i></i></span>
    <span class="thinking-label">正在检索知识库…</span>
    <span class="thinking-steps">
      <span class="step active" data-step="retrieve">检索</span>
      <span class="step" data-step="context">组织资料</span>
      <span class="step" data-step="generate">生成回答</span>
    </span>`;
  return el;
}

function setThinkingStep(root, step) {
  const map = {
    retrieve: "正在检索知识库…",
    context: "已找到相关资料，组织上下文…",
    generate: "正在生成回答…",
    done: "",
  };
  const label = root.querySelector(".thinking-label");
  if (label) label.textContent = map[step] || "处理中…";
  const order = ["retrieve", "context", "generate"];
  const cur = order.indexOf(step);
  root.querySelectorAll(".step").forEach((s) => {
    s.classList.remove("active", "done");
    const idx = order.indexOf(s.dataset.step);
    if (step === "done" || idx < cur) s.classList.add("done");
    if (idx === cur && step !== "done") s.classList.add("active");
  });
}

function appendMessage(role, content, opts = {}) {
  const welcome = $("#welcome");
  if (welcome) welcome.remove();
  const box = $("#messages");
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble" + (role === "assistant" ? " md" : "");
  if (role === "assistant") setBubbleHtml(bubble, content || "");
  else bubble.textContent = content;
  wrap.appendChild(bubble);

  if (role === "assistant") {
    wrap._sources = opts.sources || [];
    if (opts.sources && opts.sources.length) {
      wrap.appendChild(renderSourcesList(opts.sources));
    }
  }
  if (role === "assistant" && opts.latency != null) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${opts.latency} ms`;
    wrap.appendChild(meta);
  }
  box.appendChild(wrap);
  scrollChat();
  return bubble;
}

function scrollChat() {
  const el = $("#chat-scroll");
  if (el) el.scrollTop = el.scrollHeight;
}

/* ---------- chat send (SSE) ---------- */
async function sendQuestion() {
  if (state.sending) return;
  const input = $("#question");
  const question = input.value.trim();
  if (!question) return;
  state.sending = true;
  $("#send-btn").disabled = true;
  $("#stream-status").textContent = "处理中…";
  input.value = "";
  appendMessage("user", question, {});

  const msgWrap = document.createElement("div");
  msgWrap.className = "msg assistant";
  const thinking = makeThinking();
  const bubble = document.createElement("div");
  bubble.className = "bubble md";
  bubble.innerHTML = "";
  msgWrap.appendChild(thinking);
  msgWrap.appendChild(bubble);
  $("#messages").appendChild(msgWrap);
  scrollChat();

  const mode = $("#mode-select").value;
  const topK = Number($("#topk-select").value || 5);
  try {
    const res = await fetch(API + "/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${state.token}`,
      },
      body: JSON.stringify({
        question,
        conversation_id: state.conversationId,
        top_k: topK,
        mode,
        part: $("#module-select").value || undefined,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(readableApiError(err));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sources = [];
    let text = "";
    let srcHolder = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (ev.type === "session") {
          state.conversationId = ev.conversation_id;
          loadConversations();
        } else if (ev.type === "sources") {
          sources = ev.data || [];
          setThinkingStep(thinking, sources.length ? "context" : "retrieve");
          $("#stream-status").textContent = sources.length
            ? `已检索 ${sources.length} 条，生成中…`
            : "未命中资料…";
          // 来源放在回答气泡下方；超过 2 条默认折叠
          if (srcHolder && srcHolder.parentElement) srcHolder.remove();
          srcHolder = renderSourcesList(sources);
          msgWrap._sources = sources;
          msgWrap.appendChild(srcHolder);
        } else if (ev.type === "token") {
          setThinkingStep(thinking, "generate");
          if (thinking.parentElement && text === "") thinking.remove();
          text += ev.data;
          setBubbleHtml(bubble, text);
          // 保持「回答在上、参考在下」
          if (srcHolder && srcHolder.parentElement) {
            msgWrap.appendChild(srcHolder);
          }
          scrollChat();
        } else if (ev.type === "meta") {
          setThinkingStep(thinking, "done");
          if (thinking.parentElement) thinking.remove();
          const truncated = !!ev.data.truncated;
          if (truncated) {
            text += "\n\n> ⚠️ 回答内容较长，被输出长度截断；可继续回复「继续」让我补全。";
            setBubbleHtml(bubble, text);
          }
          const meta = document.createElement("div");
          meta.className = "meta";
          meta.textContent = `${ev.data.latency_ms} ms · ${ev.data.mode === "rag" ? "RAG" : "基线"} · ${
            ev.data.llm ? "LLM" : "本地抽取"
          } · 来源 ${ev.data.source_count || 0}${truncated ? " · 已截断" : ""}`;
          msgWrap.appendChild(meta);
          if (srcHolder && srcHolder.parentElement) msgWrap.appendChild(srcHolder);
          $("#stream-status").textContent = "就绪";
        }
      }
    }
    if (thinking.parentElement) thinking.remove();
    if (!text) setBubbleHtml(bubble, "（无输出）");
    else setBubbleHtml(bubble, text);
  } catch (err) {
    if (thinking.parentElement) thinking.remove();
    setBubbleHtml(bubble, "**出错**：" + err.message);
    $("#stream-status").textContent = "出错";
    toast(err.message);
  } finally {
    state.sending = false;
    $("#send-btn").disabled = false;
  }
}

$("#send-btn").addEventListener("click", sendQuestion);
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
});
// 问题输入字数计数（配合 maxlength=2000，避免超长问题撑爆布局）
$("#question").addEventListener("input", () => {
  const counter = $("#q-counter");
  if (counter) counter.textContent = `${$("#question").value.length}/2000`;
});
$("#new-chat").addEventListener("click", () => {
  state.conversationId = null;
  resetChatUI();
  renderConvs();
  $("#question").focus();
});

// 正文内联引文 [n]：点击高亮对应来源卡片并预览原文
$("#messages").addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  const idx = Number(cite.dataset.cite || 0) - 1;
  if (idx < 0) return;
  const wrap = cite.closest(".msg");
  const sources = wrap && wrap._sources;
  // 折叠的参考自动展开
  if (wrap) {
    const extra = wrap.querySelector(".sources-extra.hidden");
    if (extra && idx >= 2) {
      const toggle = wrap.querySelector(".sources-toggle");
      if (toggle) toggle.click();
    }
  }
  const card = wrap && wrap.querySelectorAll(".source-card")[idx];
  if (card) {
    document.querySelectorAll(".source-card.active").forEach((x) => x.classList.remove("active"));
    card.classList.add("active");
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (sources && sources[idx]) openPreview(sources[idx]);
  e.preventDefault();
});

// 会话管理：多选 / 编辑 / 批量删除
const convEditToggle = $("#conv-edit-toggle");
if (convEditToggle) {
  convEditToggle.addEventListener("click", () => setConvEditMode(!state.convEdit));
}
const convEditDone = $("#conv-edit-done");
if (convEditDone) convEditDone.addEventListener("click", () => setConvEditMode(false));
const convCheckAll = $("#conv-check-all");
if (convCheckAll) {
  convCheckAll.addEventListener("change", () => {
    if (convCheckAll.checked) {
      state.conversations.forEach((c) => state.convSelected.add(c.id));
    } else {
      state.convSelected.clear();
    }
    renderConvs();
  });
}
const convBatchDel = $("#conv-batch-del");
if (convBatchDel) convBatchDel.addEventListener("click", batchDeleteConvs);
bindSuggests();
initPreviewResizer();
const previewClose = $("#preview-close");
if (previewClose) previewClose.addEventListener("click", closePreview);

/* ---------- health / KB ---------- */
async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#eval-config").innerHTML = `
      <li><span>向量库</span><b>${h.vector.chunks} 片段 / ${h.vector.docs} 文档</b></li>
      <li><span>Embedder</span><b>${escapeHtml(h.vector.embedder)}</b></li>
      <li><span>LLM</span><b>${h.llm_configured ? "已配置" : "本地抽取模式"}</b></li>
      <li><span>Top-K</span><b>${h.top_k}</b></li>
      <li><span>Chunk</span><b>${h.chunk_size} / overlap ${h.chunk_overlap}</b></li>`;
  } catch {
    /* ignore */
  }
}

async function loadDocs() {
  try {
    const data = await api("/api/docs");
    const items = data.items || [];
    const table = $("#doc-table");
    // 元数据列（课程/页码）仅在存在有效值时显示，避免全空列误导
    const hasPart = items.some((d) => d.part);
    const hasPages = items.some((d) => d.pages);
    const thCourse = $("#th-course");
    const thPages = $("#th-pages");
    if (thCourse) thCourse.style.display = hasPart ? "" : "none";
    if (thPages) thPages.style.display = hasPages ? "" : "none";
    table.innerHTML = items
      .map(
        (d) => `
      <tr>
        <td>${escapeHtml(d.name)}</td>
        ${hasPart ? `<td>${escapeHtml(d.part || "-")}</td>` : ""}
        ${hasPages ? `<td>${escapeHtml(d.pages || "-")}</td>` : ""}
        <td>${d.chunk_count}</td>
        <td>${escapeHtml((d.format || "").toUpperCase())}</td>
        <td>${new Date((d.updated_at || 0) * 1000).toLocaleString()}</td>
        <td>
          <div class="actions">
            <button class="btn ghost" data-view="${d.id}">预览</button>
            ${canDownload() ? `<button class="btn ghost" data-dl="${d.id}">下载</button>` : ""}
            ${isAdmin() ? `<button class="btn ghost" data-re="${d.id}">重建</button>
            <button class="btn danger" data-del="${d.id}">删除</button>` : ""}
          </div>
        </td>
      </tr>`
      )
      .join("");
    const totalChunks = items.reduce((s, d) => s + (d.chunk_count || 0), 0);
    $("#kb-stats").innerHTML = `
      <div class="stat-card"><div class="n">${items.length}</div><div class="l">文档</div></div>
      <div class="stat-card"><div class="n">${totalChunks}</div><div class="l">知识片段</div></div>
      ${hasPart
        ? `<div class="stat-card"><div class="n">${items.filter((d) => d.part === "分析").length}</div><div class="l">分析</div></div>
      <div class="stat-card"><div class="n">${items.filter((d) => d.part === "线性代数").length}</div><div class="l">线性代数</div></div>
      <div class="stat-card"><div class="n">${items.filter((d) => d.part === "概率统计").length}</div><div class="l">概率统计</div></div>`
        : '<div class="stat-card"><div class="n">数学</div><div class="l">模块</div></div>'}`;

    table.querySelectorAll("[data-del]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("删除文档及其向量？")) return;
        await api(`/api/docs/${btn.dataset.del}`, { method: "DELETE" });
        toast("已删除");
        loadDocs();
        loadHealth();
      })
    );
    table.querySelectorAll("[data-re]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await api(`/api/docs/${btn.dataset.re}/reindex`, { method: "POST" });
        toast("已重建索引");
      })
    );
    table.querySelectorAll("[data-view]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          const d = await api(`/api/docs/${btn.dataset.view}/raw`);
          openPreview({
            doc_name: d.name,
            section: `${d.part || ""} ${d.book || ""}`.trim(),
            page_hint: d.pages || "",
            content: d.content || "",
            chunks: (d.chunks || []).map((c) => ({
              idx: c.idx,
              section: c.section || "",
              content: c.content,
              parent_idx: c.parent_idx,
            })),
          });
        } catch (e) {
          toast(e.message);
        }
      })
    );
    table.querySelectorAll("[data-dl]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          const res = await fetch(`${API}/api/docs/${btn.dataset.dl}/download`, {
            headers: { Authorization: `Bearer ${state.token}` },
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "下载失败");
          }
          const blob = await res.blob();
          const cd = res.headers.get("content-disposition") || "";
          let name = `doc-${btn.dataset.dl}`;
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
          if (m) name = decodeURIComponent(m[1]);
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = name;
          a.click();
          URL.revokeObjectURL(a.href);
          toast("已开始下载");
        } catch (e) {
          toast(e.message);
        }
      })
    );
    if (isAdmin()) loadUsers();
  } catch (e) {
    toast(e.message);
  }
}

async function loadUsers() {
  if (!isAdmin()) return;
  try {
    const data = await api("/api/users");
    const box = $("#user-table");
    if (!box) return;
    box.innerHTML = (data.items || [])
      .map(
        (u) => `
      <tr>
        <td>${u.id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td><span class="perm-chip ${u.role === "admin" ? "admin" : "off"}">${u.role === "admin" ? "管理员" : "普通用户"}</span></td>
        <td><span class="perm-chip ${u.can_download ? "on" : "off"}">${u.can_download ? "可下载" : "无下载"}</span></td>
        <td>
          <div class="actions">
            <button class="btn ghost tiny" data-toggle-dl="${u.id}" data-val="${u.can_download ? 0 : 1}">
              ${u.can_download ? "取消下载" : "授予下载"}
            </button>
            ${u.role !== "admin" ? `<button class="btn ghost tiny" data-promote="${u.id}">设为管理员</button>` : ""}
          </div>
        </td>
      </tr>`
      )
      .join("");
    box.querySelectorAll("[data-toggle-dl]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/users/${btn.dataset.toggleDl}/permissions`, {
            method: "PATCH",
            body: JSON.stringify({ can_download: btn.dataset.val === "1" }),
          });
          toast("已更新下载权限");
          loadUsers();
        } catch (e) {
          toast(e.message);
        }
      })
    );
    box.querySelectorAll("[data-promote]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("设为管理员？")) return;
        try {
          await api(`/api/users/${btn.dataset.promote}/permissions`, {
            method: "PATCH",
            body: JSON.stringify({ role: "admin", can_download: true }),
          });
          toast("已提升为管理员");
          loadUsers();
        } catch (e) {
          toast(e.message);
        }
      })
    );
  } catch (e) {
    toast(e.message);
  }
}

$("#upload-btn").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(API + "/api/docs/upload", {
      method: "POST",
      headers: { Authorization: `Bearer ${state.token}` },
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "上传失败");
    toast(`已入库：${data.name}（${data.chunks} 片段）`);
    loadDocs();
  } catch (err) {
    toast(err.message);
  } finally {
    e.target.value = "";
  }
});

$("#ingest-btn").addEventListener("click", async () => {
  toast("导入中…");
  try {
    const data = await api("/api/kb/ingest-folder", { method: "POST" });
    toast(`导入 ${data.imported}，跳过 ${data.skipped}`);
    loadDocs();
  } catch (e) {
    toast(e.message);
  }
});

$("#debug-btn").addEventListener("click", async () => {
  const query = $("#debug-query").value.trim();
  if (!query) return;
  const mode = $("#debug-mode").value;
  const topK = Number($("#debug-topk").value || 5);
  const btn = $("#debug-btn");
  btn.disabled = true;
  try {
    const data = await api("/api/search/debug", {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK, mode, part: $("#debug-part").value || undefined }),
    });
    const items = data.items || [];
    const modeLabel =
      mode === "hybrid" ? "混合检索（向量+BM25，RRF 融合）" : mode === "vector" ? "纯向量检索" : "纯关键词（BM25）";
    const scoreNote =
      mode === "hybrid"
        ? "混合策略返回向量与 BM25 分项分数（RRF 融合后为综合分）"
        : "单策略模式仅返回综合分；向量分/BM25 分项仅在混合策略下展示";
    $("#debug-results").innerHTML =
      `<div class="muted small" style="margin-bottom:6px">策略：${modeLabel} · 命中 ${items.length} 条</div>` +
      `<div class="muted small" style="margin-bottom:6px">${scoreNote}</div>` +
      (items
        .map(
          (s) => `
    <div class="source-card" data-open-preview="1">
      <div class="s-head">
        <span><i class="src-num">${s.id}</i>${escapeHtml(s.doc_name || "")}</span>
        <span class="score">${mode === "hybrid" ? "RRF " + (s.score ?? 0).toFixed(4) : (s.score ?? 0).toFixed(3)}</span>
      </div>
      <div class="muted">${escapeHtml(s.section || "")} ${s.page_hint ? "· p." + escapeHtml(s.page_hint) : ""}${
        Number(s.parent_idx) >= 0 ? " · 父块#" + (Number(s.parent_idx) + 1) : ""
      }</div>
      ${mode === "hybrid" ? `<div class="muted small">向量 ${s.vec_score == null ? "-" : Number(s.vec_score).toFixed(3)} · BM25 ${s.bm25_score == null ? "-" : Number(s.bm25_score).toFixed(2)}</div>` : ""}
      <div class="s-snippet">${escapeHtml(s.snippet || "")}</div>
    </div>`
        )
        .join("") || '<div class="muted small">无结果，请先导入知识库</div>');
    $("#debug-results").querySelectorAll(".source-card").forEach((card, i) => {
      card.addEventListener("click", () => {
        const s = items[i];
        if (s) openPreview(s);
      });
    });
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
});

/* ---------- eval ---------- */
function loadEvalConfig() {
  loadHealth();
  loadEvalMetrics();
}

/* eval 内部 Tab 切换 */
document.querySelectorAll(".eval-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.evalTab;
    document.querySelectorAll(".eval-tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".eval-tab-pane").forEach((p) =>
      p.classList.toggle("active", p.id === "eval-pane-" + tab)
    );
    if (tab === "metrics") loadEvalMetrics();
  });
});

$("#load-questions").addEventListener("click", async () => {
  const data = await api("/api/test-questions?limit=100");
  state.questions = data.items || [];
  $("#q-table").innerHTML = state.questions
    .map((q) => {
      const src = q.expected_source || {};
      return `
      <tr>
        <td>${q.id}</td>
        <td>${escapeHtml(q.type || "")}</td>
        <td>${escapeHtml(q.course || "")}</td>
        <td>${escapeHtml(q.question)}</td>
        <td>${escapeHtml(src.file || "")} p.${escapeHtml(src.pages || "")}</td>
        <td><button class="btn ghost" data-q="${q.id}">试答</button></td>
      </tr>`;
    })
    .join("");
  $("#q-table").querySelectorAll("[data-q]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const q = state.questions.find((x) => String(x.id) === btn.dataset.q);
      if (!q) return;
      $("#eval-q").value = q.question;
      $("#panel-eval").scrollIntoView({ behavior: "smooth" });
    })
  );
  toast(`已加载 ${state.questions.length} 题`);
});

function renderEvalResult(data, outEl, latencyEl, showSources) {
  const srcs = (data.sources || [])
    .map((s) => `[${s.id}] ${s.doc_name} (p.${s.page_hint || "?"})`)
    .join("\n");
  let tail = "";
  if (showSources && srcs) {
    tail = "\n\n---\n\n**来源**\n\n" + srcs.split("\n").map((s) => "- " + s).join("\n");
  } else if (!showSources) {
    tail = "\n\n---\n\n<em class='muted'>基线模式：未检索知识库，答案仅来自模型通用知识。</em>";
  }
  outEl.innerHTML = renderMath(renderMarkdown((data.answer || "") + tail));
  latencyEl.textContent = `${data.latency_ms} ms`;
}

async function callEval(question, mode, topK) {
  return api("/api/eval/run", {
    method: "POST",
    body: JSON.stringify({ question, mode, top_k: topK }),
  });
}

async function runEvalCompare() {
  const question = $("#eval-q").value.trim();
  if (!question) return toast("请填写问题");
  const topK = Number($("#topk-select").value || 5);
  $("#eval-out-rag").textContent = "评测中…";
  $("#eval-out-base").textContent = "评测中…";
  $("#eval-rag-latency").textContent = "";
  $("#eval-base-latency").textContent = "";
  try {
    const [rag, base] = await Promise.all([
      callEval(question, "rag", topK),
      callEval(question, "baseline", topK),
    ]);
    renderEvalResult(rag, $("#eval-out-rag"), $("#eval-rag-latency"), true);
    renderEvalResult(base, $("#eval-out-base"), $("#eval-base-latency"), false);
  } catch (e) {
    $("#eval-out-rag").textContent = "失败：" + e.message;
  }
}

async function runEvalSingle(mode) {
  const question = $("#eval-q").value.trim();
  if (!question) return toast("请填写问题");
  const topK = Number($("#topk-select").value || 5);
  const outEl = mode === "rag" ? $("#eval-out-rag") : $("#eval-out-base");
  const latEl = mode === "rag" ? $("#eval-rag-latency") : $("#eval-base-latency");
  outEl.textContent = "评测中…";
  try {
    const data = await callEval(question, mode, topK);
    renderEvalResult(data, outEl, latEl, mode === "rag");
  } catch (e) {
    outEl.textContent = "失败：" + e.message;
  }
}

$("#eval-compare").addEventListener("click", runEvalCompare);
$("#eval-run").addEventListener("click", () => runEvalSingle("rag"));
$("#eval-run-base").addEventListener("click", () => runEvalSingle("baseline"));

/* 批量检索指标：vector / keyword / hybrid 对比表 + CSS 柱状图 */
async function loadEvalMetrics() {
  const body = $("#metrics-body");
  try {
    const d = await api("/api/eval/metrics");
    if (!d.available) {
      body.innerHTML = `<div class="muted small">${escapeHtml(d.message || "暂无数据")}。请先在 backend 目录运行：<code>.venv/Scripts/python.exe scripts/eval_retrieval.py</code></div>`;
      return;
    }
    $("#metrics-meta").textContent = `· ${d.generated_at} · ${d.questions} 题 · top_k=${d.top_k}`;
    const modes = d.modes || [];
    const modeLabel = { vector: "纯向量", keyword: "关键词 BM25", hybrid: "混合 RRF" };
    const metrics = ["hit@1", "hit@3", "hit@5", "mrr"];
    const metricLabel = { "hit@1": "Hit@1", "hit@3": "Hit@3", "hit@5": "Hit@5", "mrr": "MRR" };

    // 汇总表
    let html = `<table class="table metrics-table"><thead><tr>
      <th>检索策略</th><th>Hit@1</th><th>Hit@3</th><th>Hit@5</th><th>MRR</th><th>未命中</th>
    </tr></thead><tbody>`;
    for (const m of modes) {
      html += `<tr>
        <td><b>${modeLabel[m.mode] || m.mode}</b><span class="muted small"> (${m.mode})</span></td>
        ${metrics.map(k => `<td>${(m[k] * 100).toFixed(1)}%</td>`).join("")}
        <td>${m.miss_count}</td>
      </tr>`;
    }
    html += `</tbody></table>`;

    // CSS 柱状图：每个指标一组，三策略并排
    html += `<div class="bar-charts">`;
    for (const k of metrics) {
      html += `<div class="bar-group"><div class="bar-group-title">${metricLabel[k]}</div>`;
      for (const m of modes) {
        const v = (m[k] * 100).toFixed(1);
        html += `<div class="bar-row">
          <span class="bar-label">${modeLabel[m.mode] || m.mode}</span>
          <div class="bar-track"><div class="bar-fill bar-${m.mode}" style="width:${v}%"></div></div>
          <span class="bar-val">${v}%</span>
        </div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;

    // 分模块（hybrid）
    const hybrid = modes.find(m => m.mode === "hybrid");
    if (hybrid && hybrid.per_module && hybrid.per_module.length) {
      html += `<h4 style="margin:14px 0 6px">分模块表现（hybrid）</h4>
        <table class="table"><thead><tr><th>模块</th><th>题数</th><th>Hit@1</th><th>Hit@3</th><th>Hit@5</th><th>MRR</th></tr></thead><tbody>`;
      for (const p of hybrid.per_module) {
        html += `<tr><td>${p.module}</td><td>${p.n}</td>
          <td>${(p["hit@1"]*100).toFixed(1)}%</td><td>${(p["hit@3"]*100).toFixed(1)}%</td>
          <td>${(p["hit@5"]*100).toFixed(1)}%</td><td>${(p.mrr*100).toFixed(1)}%</td></tr>`;
      }
      html += `</tbody></table>`;
    }
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="muted small">指标加载失败：${escapeHtml(e.message)}</div>`;
  }
}

$("#run-batch").addEventListener("click", async () => {
  const qs = state.questions.slice(0, 10);
  if (!qs.length) return toast("请先加载问题集");
  toast("批量评测前 10 题…");
  let ok = 0;
  for (const q of qs) {
    try {
      await api("/api/eval/run", {
        method: "POST",
        body: JSON.stringify({ question: q.question, mode: "rag", top_k: 5 }),
      });
      ok++;
    } catch {
      /* continue */
    }
  }
  toast(`批量评测完成：${ok}/${qs.length}`);
});

/* ---------- practice（做题 M1–M6） ---------- */
function practiceAnswerInput(q, i) {
  if (q.mode === "choice") {
    return `<div class="pq-options">${(q.options || []).map((op, oi) => `
      <label class="pq-opt"><input type="radio" name="pq-${i}" value="${oi}" data-i="${i}" data-oi="${oi}" />
        <span>${renderMath(inlineMd(String(op)))}</span></label>`).join("")}</div>`;
  }
  return `<textarea class="pq-answer" data-i="${i}" rows="3" placeholder="输入你的答案（支持公式 $...$）"></textarea>`;
}

function renderQuiz(qs) {
  state.practice = { questions: qs, answers: {}, graded: false };
  $("#practice-quiz").classList.remove("muted");
  $("#practice-quiz").innerHTML = qs.map((q, i) => `
    <div class="pq-item" data-i="${i}">
      <div class="pq-q"><span class="pq-no">${i + 1}.</span>
        <span class="pq-qtext">${renderMath(renderMarkdown(q.question))}</span>
        <span class="pq-tags">${escapeHtml(q.module)} · ${escapeHtml(q.type)} · ${escapeHtml(q.difficulty)}${q.knowledge ? ` · ${escapeHtml(q.knowledge)}` : ""}</span></div>
      ${practiceAnswerInput(q, i)}
      <div class="pq-result" data-r="${i}"></div>
      <button class="btn ghost tiny pq-explain" data-i="${i}" type="button">查看解析</button>
    </div>`).join("");
  $("#practice-quiz").querySelectorAll("input[type=radio]").forEach((r) =>
    r.addEventListener("change", () => {
      state.practice.answers[Number(r.dataset.i)] = r.value;
    })
  );
  $("#practice-quiz").querySelectorAll(".pq-answer").forEach((t) =>
    t.addEventListener("input", () => {
      state.practice.answers[Number(t.dataset.i)] = t.value;
    })
  );
  $("#practice-quiz").querySelectorAll(".pq-explain").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const i = Number(btn.dataset.i);
      const q = state.practice.questions[i];
      try {
        const ex = await api("/api/practice/explain", { method: "POST", body: JSON.stringify({ qid: q.qid }) });
        btn.disabled = true;
        btn.textContent = "解析已展开";
        const pts = (ex.answer_points || []).map((p) => `<li>${renderMath(renderMarkdown(p))}</li>`).join("");
        const steps = (ex.calc_steps || []).map((s, si) => `<li>${si + 1}. ${renderMath(renderMarkdown(s))}</li>`).join("");
        const src = ex.expected_source || {};
        btn.insertAdjacentHTML("afterend",
          `<div class="pq-explain-box">${pts ? `<div><b>参考答案要点</b><ol>${pts}</ol></div>` : ""}
           ${steps ? `<div><b>计算步骤</b><ol>${steps}</ol></div>` : ""}
           ${ex.correct_answer ? `<div><b>正确答案</b>：${renderMath(renderMarkdown(ex.correct_answer))}</div>` : ""}
           <div class="muted small">来源：${escapeHtml(src.file || "")} · ${escapeHtml(src.section || "")}</div></div>`);
      } catch (e) {
        toast("解析加载失败：" + e.message);
      }
    })
  );
}

async function submitQuiz() {
  const st = state.practice;
  if (!st || !st.questions.length) return toast("请先开始做题");
  if (st.graded) return toast("本卷已判分，请重新出题");
  st.graded = true;
  $("#practice-submit").disabled = true;
  let right = 0;
  const total = st.questions.length;
  for (let i = 0; i < total; i++) {
    const q = st.questions[i];
    const ans = (st.answers[i] || "").trim();
    let g;
    try {
      g = await api("/api/practice/grade", { method: "POST", body: JSON.stringify({ qid: q.qid, answer: ans, mode: q.mode }) });
    } catch (e) {
      g = { correct: false, score: 0, feedback: e.message };
    }
    const box = document.querySelector(`[data-r="${i}"]`);
    if (box) {
      box.innerHTML = `<span class="pq-${g.correct ? "ok" : "no"}">${g.correct ? "✓ 正确" : "✗ 错误"}</span>
        <span class="muted small">${g.feedback ? escapeHtml(g.feedback) : ""}</span>`;
    }
    if (g.correct) right++;
    try {
      await api("/api/practice/save", {
        method: "POST",
        body: JSON.stringify({ qid: q.qid, question: q.question, answer: ans, correct: !!g.correct, module: q.module, qtype: q.type, difficulty: q.difficulty, mode: q.mode }),
      });
    } catch { /* 保存失败不阻塞判分展示 */ }
  }
  toast(`判分完成：${right}/${total}`);
  loadPracticeRecords();
  loadPracticeMastery();
}

async function loadPracticeRecords() {
  const module = $("#pr-module").value || "";
  const wrong = $("#pr-wrong").checked;
  try {
    const data = await api(`/api/practice/records?module=${encodeURIComponent(module)}&wrong_only=${wrong}&limit=200`);
    const recs = data.records || [];
    const box = $("#practice-records");
    box.classList.remove("muted");
    if (!recs.length) {
      box.classList.add("muted");
      box.innerHTML = "暂无做题记录。";
      return;
    }
    box.innerHTML = recs.map((r) => `
      <div class="pr-item">
        <span class="pr-mark ${r.correct ? "ok" : "no"}">${r.correct ? "✓" : "✗"}</span>
        <span class="pr-q">${escapeHtml((r.question || "").slice(0, 60))}</span>
        <span class="pr-meta">${escapeHtml(r.module)} · ${escapeHtml(r.qtype || r.mode)}</span>
        <span class="muted small">${new Date(r.created_at * 1000).toLocaleString("zh-CN")}</span>
      </div>`).join("");
  } catch (e) {
    $("#practice-records").textContent = "加载失败：" + e.message;
  }
}

async function loadPracticeMastery() {
  try {
    const data = await api("/api/practice/records?limit=500");
    const recs = data.records || [];
    const box = $("#practice-mastery");
    if (!recs.length) {
      box.classList.remove("muted");
      box.innerHTML = "暂无数据，做题后自动更新。";
      return;
    }
    const byMod = {};
    recs.forEach((r) => {
      const m = r.module || "未知";
      byMod[m] = byMod[m] || { total: 0, ok: 0 };
      byMod[m].total++;
      if (r.correct) byMod[m].ok++;
    });
    const order = ["分析", "线性代数", "概率统计"];
    const names = order.filter((m) => byMod[m]);
    const others = Object.keys(byMod).filter((m) => !order.includes(m));
    const all = names.concat(others);
    box.classList.remove("muted");
    box.innerHTML = all.map((m) => {
      const s = byMod[m];
      const pct = Math.round((s.ok / s.total) * 100);
      return `<div class="pm-row"><span class="pm-name">${escapeHtml(m)}</span>
        <div class="pm-bar"><div class="pm-fill" style="width:${pct}%"></div></div>
        <span class="pm-val">${s.ok}/${s.total} · ${pct}%</span></div>`;
    }).join("");
  } catch (e) {
    $("#practice-mastery").textContent = "加载失败：" + e.message;
  }
}

function loadPracticePanel() {
  loadPracticeRecords();
  loadPracticeMastery();
}

$("#practice-start").addEventListener("click", async () => {
  const params = new URLSearchParams({
    module: $("#p-module").value || "",
    qtype: $("#p-qtype").value || "",
    difficulty: $("#p-difficulty").value || "",
    count: $("#p-count").value || "5",
    knowledge: $("#p-knowledge").value || "",
  });
  try {
    const data = await api(`/api/practice/paper?${params.toString()}`);
    renderQuiz(data.questions || []);
    $("#practice-submit").disabled = false;
    toast(`已出题 ${data.count} 道`);
  } catch (e) {
    toast("出题失败：" + e.message);
  }
});

const submitBtn = document.createElement("button");
submitBtn.id = "practice-submit";
submitBtn.className = "btn primary";
submitBtn.textContent = "交卷判分";
submitBtn.style.marginTop = "12px";
submitBtn.addEventListener("click", submitQuiz);
document.querySelector("#practice-quiz-card").appendChild(submitBtn);

$("#pr-module").addEventListener("change", loadPracticeRecords);
$("#pr-wrong").addEventListener("change", loadPracticeRecords);

$("#practice-export").addEventListener("click", async () => {
  const module = $("#pr-module").value || "";
  const wrong = $("#pr-wrong").checked;
  try {
    const res = await fetch(`${API}/api/practice/export?module=${encodeURIComponent(module)}&wrong_only=${wrong}`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (!res.ok) throw new Error((await res.text()) || "导出失败");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `star-math-practice-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("已导出 Markdown");
  } catch (e) {
    toast("导出失败：" + e.message);
  }
});

/* ---------- exercises：上传解题 + 学习·习题册（M 系列新增） ---------- */
const exercise = {
  sourceId: null,
  name: "",
  sourceType: "doc",
  questions: [],
  results: {},
  solving: false,
  solved: false,
};

function exQuestionStem(q) {
  return renderMath(renderMarkdown(q.stem || ""));
}

function exTypeName(t) {
  return { choice: "选择", judge: "判断", short: "简答", calc: "计算" }[t] || t || "题目";
}

async function uploadExerciseFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(API + "/api/exercises/upload", {
    method: "POST",
    headers: { Authorization: `Bearer ${state.token}` },
    body: fd,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "上传失败");
  return data;
}

/** 在 host 容器内渲染「识别结果 → 解题 → 存入习题册」流程卡片（状态挂在卡片上，多卡互不干扰） */
function buildExerciseFlow(host, source, opts = {}) {
  const ctx = {
    sourceId: source.source_id,
    name: source.name,
    sourceType: source.source_type,
    questions: source.questions || [],
    results: {},
    solving: false,
    solved: false,
  };
  host.innerHTML = "";
  const card = document.createElement("div");
  card.className = "ex-flow";
  card._ex = ctx;
  card.innerHTML = `
    <div class="ex-flow-head">
      <div class="ex-flow-meta">
        <span class="perm-chip ${source.source_type === "image" ? "on" : "off"}">${source.source_type === "image" ? "图片" : "文档"}</span>
        <b>${escapeHtml(source.name)}</b>
        <span class="muted small">共 ${ctx.questions.length} 题</span>
      </div>
      <div class="row">
        <button type="button" class="btn primary tiny ex-solve">全部解题（RAG）</button>
        <button type="button" class="btn primary tiny ex-save hidden">存入习题册</button>
        <span class="muted small ex-status"></span>
      </div>
    </div>
    <div class="ex-qlist"></div>`;
  host.appendChild(card);

  const qlist = card.querySelector(".ex-qlist");
  ctx.questions.forEach((q, i) => {
    const no = q.no || i + 1;
    const block = document.createElement("div");
    block.className = "ex-qblock";
    block.dataset.qno = no;
    block.innerHTML = `
      <div class="ex-qtext"><span class="pq-no">${no}.</span>${exQuestionStem(q)}</div>
      ${q.options && q.options.length ? `<div class="ex-qopts">${q.options.map((o, oi) => `<div>${String.fromCharCode(65 + oi)}. ${renderMath(inlineMd(o))}</div>`).join("")}</div>` : ""}
      <div class="ex-qsolve muted small">待解题</div>
      <div class="ex-similar"></div>`;
    qlist.appendChild(block);
    // 识别后自动在习题库查重，显示相似题
    checkSimilarForBlock(block, q.stem || "");
  });

  card.querySelector(".ex-solve").addEventListener("click", () => solveExerciseFlow(card));
  card.querySelector(".ex-save").addEventListener("click", () => saveExerciseFlow(card, opts));
  // 识别完成后自动用 LLM 解题；opts.autoSolve=false 时保持手动触发
  if (opts.autoSolve !== false) setTimeout(() => solveExerciseFlow(card), 200);
  return card;
}

async function solveExerciseFlow(card) {
  const ex = card._ex || exercise;
  if (ex.solving) return;
  ex.solving = true;
  ex.solved = false;
  const solveBtn = card.querySelector(".ex-solve");
  const saveBtn = card.querySelector(".ex-save");
  const statusEl = card.querySelector(".ex-status");
  solveBtn.disabled = true;
  statusEl.textContent = `开始解题（${ex.questions.length} 题）…`;
  try {
    const res = await fetch(API + `/api/exercises/${ex.sourceId}/solve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({
        questions: ex.questions.map((q, i) => ({
          no: q.no || i + 1,
          type: q.type || "short",
          stem: q.stem || "",
          options: q.options || [],
        })),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(readableApiError(err));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (ev.type === "solve_start") {
          statusEl.textContent = `共 ${ev.count} 题，逐题解题中…`;
        } else if (ev.type === "solve_meta") {
          const se = card.querySelector(`.ex-qblock[data-qno="${ev.data.qno}"] .ex-qsolve`);
          if (se) se.innerHTML = `<span class="thinking-label">正在检索知识库并解题…</span>`;
        } else if (ev.type === "solve_done") {
          const d = ev.data;
          ex.results[d.qno] = d;
          const se = card.querySelector(`.ex-qblock[data-qno="${d.qno}"] .ex-qsolve`);
          if (se) {
            // 答案兜底：后端未提取到最终答案时，从解答文本中再提取一次
            let answer = d.answer || "";
            if (!answer && d.solution) {
              const m = d.solution.match(/【最终答案】([\s\S]*?)(?=\n*【|$)/);
              if (m) answer = m[1].trim();
            }
            se.classList.remove("muted");
            const knowledgeRaw = (d.knowledge || "数学").trim();
            const topics = knowledgeRaw.split(/[、,，;；]/).map((t) => t.trim()).filter(Boolean);
            se.innerHTML = `
              <div class="ex-answer"><span class="ex-label">最终答案</span>${answer ? renderMath(renderMarkdown(answer)) : '<span class="muted">（未给出，详见分步解答）</span>'}</div>
              <div class="ex-answer"><span class="ex-label">知识点</span><span class="kb-chip-wrap" data-topics="${escapeHtml(topics.join("|"))}"><span class="muted">${escapeHtml(knowledgeRaw)}</span></span> <span class="muted small">· 难度 ${escapeHtml(d.difficulty || "L2")}${d.sources && d.sources.length ? ` · 参考 ${d.sources.length} 条` : ""}</span></div>
              <div class="ex-solution"><span class="ex-label">分步解答</span>${renderMath(renderMarkdown(d.solution || ""))}</div>`;
            // 异步检查每个知识点：知识库里有对应概念卡才渲染为可点击标签，否则保持普通文本
            enhanceKnowledgeChips(se.querySelector(".kb-chip-wrap"), topics);
          }
          statusEl.textContent = `已完成 ${Object.keys(ex.results).length}/${ex.questions.length} 题`;
        } else if (ev.type === "done") {
          ex.solving = false;
          ex.solved = true;
          statusEl.textContent = "全部完成，可存入习题册";
          saveBtn.classList.remove("hidden");
          loadExercisePanel();
        } else if (ev.type === "error") {
          throw new Error(ev.detail || "解题失败");
        }
      }
    }
    if (!ex.solved) {
      ex.solving = false;
      statusEl.textContent = "解题已结束（结果可能不完整）";
    }
  } catch (err) {
    ex.solving = false;
    statusEl.textContent = "出错";
    toast("解题失败：" + err.message);
  } finally {
    solveBtn.disabled = false;
    solveBtn.textContent = ex.solved ? "重新解题（RAG）" : "重试解题";
  }
}

async function saveExerciseFlow(card, opts = {}) {
  const ex = card._ex || exercise;
  const items = ex.questions
    .map((q, i) => {
      const no = q.no || i + 1;
      const r = ex.results[no] || {};
      return {
        qno: no,
        type: q.type || "short",
        difficulty: r.difficulty || "L2",
        question: q.stem || "",
        options: q.options || [],
        answer: r.answer || "",
        solution: r.solution || "",
        explanation: r.explanation || "",
        knowledge: r.knowledge || "数学",
      };
    })
    .filter((it) => it.question);
  if (!items.length) return toast("没有可保存的题目");
  // 存入前再查一次重：若题目与习题库高度相似（≥80%），提示用户确认
  try {
    const dupCount = [];
    for (const it of items) {
      const r = await fetch("/api/exercises/similar", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
        body: JSON.stringify({ stem: it.question, top_k: 1 }),
      });
      if (!r.ok) continue;
      const d = await r.json();
      if (d.items && d.items[0] && d.items[0].similarity >= 0.8) dupCount.push(d.items[0]);
    }
    if (dupCount.length && !confirm(`检测到 ${dupCount.length} 道题与习题库高度相似（≥80%），建议不重复存入。\n\n仍要存入全部题目吗？`)) {
      return;
    }
  } catch (e) { /* 查重失败不阻断保存 */ }
  try {
    const data = await api("/api/exercises/save", {
      method: "POST",
      body: JSON.stringify({ source_id: ex.sourceId, items }),
    });
    toast(`已存入习题册 ${data.saved} 题`);
    if (opts.onSaved) opts.onSaved();
    loadExercisePanel();
  } catch (e) {
    toast("保存失败：" + e.message);
  }
}

async function handleExerciseFile(file) {
  if (!file) return;
  const name = file.name || "file";
  const isChat = currentPanel() === "chat";
  showAttachChip("uploading", `正在上传识别：${name}…`);
  if (isChat) {
    appendMessage("user", `上传习题：${name}`, {});
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    const bubble = document.createElement("div");
    bubble.className = "bubble md";
    bubble.innerHTML = '<span class="muted small">上传并识别中…</span>';
    wrap.appendChild(bubble);
    $("#messages").appendChild(wrap);
    scrollChat();
    try {
      const src = await uploadExerciseFile(file);
      bubble.innerHTML = "";
      buildExerciseFlow(bubble, src);
      showAttachChip("ok", `已识别 ${(src.questions || []).length} 题，正在自动解题`);
    } catch (e) {
      bubble.innerHTML = renderMath(renderMarkdown("**上传失败**：" + e.message));
      showAttachChip("err", `上传失败：${e.message}`);
    }
    return;
  }
  // 学习面板
  const card = $("#ex-recog-card");
  card.classList.remove("hidden");
  const host = $("#ex-questions");
  host.classList.remove("muted");
  host.innerHTML = '<span class="muted small">上传并识别中…</span>';
  try {
    const src = await uploadExerciseFile(file);
    host.innerHTML = "";
    buildExerciseFlow(host, src);
    showAttachChip("ok", `已识别 ${(src.questions || []).length} 题，正在自动解题`);
  } catch (e) {
    host.innerHTML = "";
    card.classList.add("hidden");
    toast("上传失败：" + e.message);
    showAttachChip("err", `上传失败：${e.message}`);
  }
}

async function loadExercisePanel() {
  try {
    const d = await api("/api/exercises?limit=1");
    const s = d.stats || {};
    $("#ex-stats").innerHTML = `
      <div class="stat-card"><div class="n">${s.total ?? 0}</div><div class="l">习题</div></div>
      <div class="stat-card"><div class="n">${s.sources ?? 0}</div><div class="l">来源</div></div>
      <div class="stat-card"><div class="n">${s.today ?? 0}</div><div class="l">今日新增</div></div>`;
  } catch {
    $("#ex-stats").innerHTML = "";
  }
  await loadExerciseBook();
}

async function loadExerciseBook() {
  const qtype = $("#ex-qtype").value || "";
  try {
    const d = await api(`/api/exercises?qtype=${encodeURIComponent(qtype)}&limit=300`);
    const items = d.items || [];
    const box = $("#ex-book");
    box.classList.remove("muted");
    if (!items.length) {
      box.classList.add("muted");
      box.innerHTML = "暂无习题。";
      return;
    }
    const groups = {};
    items.forEach((it) => {
      const k = it.source_id + "::" + (it.source_name || "未知来源");
      groups[k] = groups[k] || { source_id: it.source_id, name: it.source_name || "未知来源", type: it.source_type, items: [] };
      groups[k].items.push(it);
    });
    box.innerHTML = Object.values(groups)
      .map(
        (g) => `
      <div class="ex-group">
        <div class="ex-group-head">
          <span class="perm-chip ${g.type === "image" ? "on" : "off"}">${g.type === "image" ? "图片" : "文档"}</span>
          <b>${escapeHtml(g.name)}</b>
          <span class="muted small">${g.items.length} 题</span>
        </div>
        ${g.items.map((it) => exBookItem(it)).join("")}
      </div>`
      )
      .join("");
    box.querySelectorAll("[data-toggle-ex]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const item = btn.closest(".ex-book-item");
        const body = item ? item.querySelector(".ex-book-detail") : null;
        if (!body) return;
        const open = body.classList.toggle("open");
        btn.textContent = open ? "收起" : "查看";
      });
    });
    box.querySelectorAll("[data-del-ex]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("删除该习题？")) return;
        try {
          await api(`/api/exercises/${btn.dataset.delEx}`, { method: "DELETE" });
          toast("已删除");
          loadExercisePanel();
        } catch (e) {
          toast("删除失败：" + e.message);
        }
      });
    });
  } catch (e) {
    $("#ex-book").textContent = "加载失败：" + e.message;
  }
}

function exBookItem(it) {
  const opts = (it.options || [])
    .map((o, oi) => `<div>${String.fromCharCode(65 + oi)}. ${renderMath(inlineMd(o))}</div>`)
    .join("");
  return `
  <div class="ex-book-item" data-item-id="${it.id}">
    <div class="ex-book-row">
      <span class="perm-chip">${escapeHtml(exTypeName(it.type))}</span>
      <span class="ex-book-q">${renderMath(renderMarkdown(it.question || ""))}</span>
      <span class="muted small">${escapeHtml(it.knowledge || "")} · ${escapeHtml(it.difficulty || "")}</span>
      <button class="btn ghost tiny" data-toggle-ex="1" type="button">查看</button>
      <button class="btn danger tiny" data-del-ex="${it.id}" type="button">删除</button>
    </div>
    <div class="ex-book-detail">
      ${opts ? `<div class="ex-answer"><span class="ex-label">选项</span>${opts}</div>` : ""}
      <div class="ex-answer"><span class="ex-label">最终答案</span>${it.answer ? renderMath(renderMarkdown(it.answer)) : '<span class="muted">（未给出）</span>'}</div>
      ${it.explanation ? `<div class="ex-solution"><span class="ex-label">思路分析</span>${renderMath(renderMarkdown(it.explanation))}</div>` : ""}
      ${it.solution ? `<div class="ex-solution"><span class="ex-label">分步解答</span>${renderMath(renderMarkdown(it.solution))}</div>` : ""}
      <div class="muted small">来源：${escapeHtml(it.source_name || "")} · ${new Date((it.created_at || 0) * 1000).toLocaleString("zh-CN")}</div>
    </div>
  </div>`;
}

async function exportExerciseBook() {
  try {
    const res = await fetch(API + "/api/exercises/export", {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (!res.ok) throw new Error((await res.text()) || "导出失败");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `star-math-exercise-book-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("已导出 Markdown");
  } catch (e) {
    toast("导出失败：" + e.message);
  }
}

/** 问答页上传状态 chip：上传中 / 成功 / 失败 */
function showAttachChip(kind, text) {
  const box = $("#attach-chips");
  if (!box) return;
  box.innerHTML = `<span class="attach-chip ${kind}">${kind === "uploading" ? '<i class="chip-spinner"></i>' : ""}${escapeHtml(text)}</span>`;
  if (kind === "ok") setTimeout(() => { if (box) box.innerHTML = ""; }, 2600);
  if (kind === "err") setTimeout(() => { if (box) box.innerHTML = ""; }, 6000);
}

function bindAttach(btnId, inputId) {
  const btn = $(btnId);
  const input = $(inputId);
  if (!btn || !input) return;
  btn.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (f) handleExerciseFile(f);
    e.target.value = "";
  });
}
bindAttach("#attach-img-btn", "#attach-img-input");
bindAttach("#attach-file-btn", "#attach-file-input");
const exUploadBtn = $("#ex-upload-btn");
if (exUploadBtn) exUploadBtn.addEventListener("click", () => $("#ex-file-input").click());
const exFileInput = $("#ex-file-input");
if (exFileInput) {
  exFileInput.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (f) handleExerciseFile(f);
    e.target.value = "";
  });
}
const exExportBtn = $("#ex-export-btn");
if (exExportBtn) exExportBtn.addEventListener("click", exportExerciseBook);
const exQtype = $("#ex-qtype");
if (exQtype) exQtype.addEventListener("change", loadExerciseBook);

/* ---------- boot ---------- */
async function boot() {
  if (state.token) {
    try {
      const me = await api("/api/me");
      if (me.user) {
        state.user = {
          id: me.user.uid || me.user.id,
          username: me.user.username,
          role: me.user.role,
          can_download: me.user.can_download === true || me.user.can_download === 1,
        };
        localStorage.setItem("rag_user", JSON.stringify(state.user));
        showApp();
        return;
      }
    } catch {
      /* fallthrough */
    }
  }
  if (state.token && state.user) showApp();
  else showLogin();
}
boot();
