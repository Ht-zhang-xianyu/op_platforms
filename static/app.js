const state = { comparison: null, operators: [], documents: [], jiras: [], events: [], selected: new Set(), view: "dashboard" };
const $ = id => document.getElementById(id);
const labels = {
  scope: { general_custom_op: "通用算子", tilelang: "TileLang", device_specific: "设备专属", call_site: "调用点", test_only: "测试", other_custom_op: "其他注册" },
  kind: { torch_library_op: "C++ _C", python_custom_op: "Python custom_op", tilelang_kernel: "TileLang Kernel", torch_call_site: "torch.ops 调用" },
  change: { added: "新增", modified: "相关修改", removed: "移除" },
  doc: { draft: "待审核", approved: "已审核" }
};
const viewTitles = { dashboard: ["01", "总览"], operators: ["02", "算子清单"], documents: ["03", "文档中心"], jiras: ["04", "Jira 追踪"], activity: ["05", "全部记录"] };
const esc = value => String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function api(path, options = {}) {
  const response = await fetch(path, { headers: {"Content-Type": "application/json"}, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function toast(message) { $("toast").textContent = message; $("toast").classList.add("show"); setTimeout(() => $("toast").classList.remove("show"), 2800); }
function emptyRow(text, columns) { return `<tr><td colspan="${columns}" class="empty">${esc(text)}</td></tr>`; }

async function refreshData() {
  const data = await api("/api/comparison");
  state.comparison = data.comparison;
  renderSummary();
  if (state.comparison?.status === "completed") {
    const [operators, documents, jiras, events] = await Promise.all([
      api(`/api/operators?comparison_id=${state.comparison.id}`), api("/api/documents"), api("/api/jiras"), api("/api/events/all")
    ]);
    state.operators = operators.operators; state.documents = documents.documents; state.jiras = jiras.jiras; state.events = events.events;
    renderOperators(); renderDocuments(); renderJiras(); renderEvents();
  }
}
function renderSummary() {
  const c = state.comparison;
  if (!c) return;
  $("base-version").textContent = c.base_version; $("target-version").textContent = c.target_version;
  $("commit-info").textContent = `${c.base_sha.slice(0, 12)} → ${c.target_sha.slice(0, 12)} · ${c.extractor_version}`;
  $("analysis-status").className = `status ${c.status}`;
  $("analysis-status").textContent = {running: "分析中", completed: "分析完成", failed: "分析失败"}[c.status] || c.status;
  const s = c.stats || {};
  ["total", "added", "modified", "documents", "approved", "jiras", "excluded"].forEach(key => { $(`stat-${key}`).textContent = s[key] ?? "—"; });
}
function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  $("view-code").textContent = viewTitles[view][0]; $("view-title").textContent = viewTitles[view][1];
}
document.querySelectorAll(".nav-item").forEach(button => button.onclick = () => switchView(button.dataset.view));
document.querySelectorAll("[data-go]").forEach(button => button.onclick = () => switchView(button.dataset.go));

function filteredOperators() {
  const scope = $("scope-filter").value, change = $("change-filter").value, doc = $("doc-filter").value;
  const query = $("op-search").value.trim().toLowerCase();
  return state.operators.filter(item =>
    (scope === "all" || (scope === "default" ? item.is_default_work_item : item.scope === scope)) &&
    (change === "all" || item.change_type === change) &&
    (doc === "all" || (doc === "none" ? !item.document_status : item.document_status === doc)) &&
    (!query || `${item.name} ${item.path}`.toLowerCase().includes(query))
  );
}
function renderOperators() {
  const rows = filteredOperators();
  const selectable = rows.filter(item => item.is_work_item);
  $("select-all").checked = selectable.length > 0 && selectable.every(item => state.selected.has(item.id));
  $("batch-documents").disabled = !state.selected.size;
  $("op-summary").textContent = `显示 ${rows.length} / ${state.operators.length} 项 · 已选择 ${state.selected.size}`;
  $("operator-rows").innerHTML = rows.length ? rows.map(item => `<tr data-id="${item.id}">
    <td>${item.is_work_item ? `<input class="row-check" data-id="${item.id}" type="checkbox" ${state.selected.has(item.id) ? "checked" : ""}>` : ""}</td>
    <td><div class="operator-name">${esc(item.name)}</div><div class="operator-path" title="${esc(item.path)}">${esc(item.path)}</div></td>
    <td><span class="scope-label">${esc(labels.scope[item.scope])}</span><span class="kind">${esc(labels.kind[item.kind] || item.kind)}</span></td>
    <td><span class="change ${item.change_type}">${esc(labels.change[item.change_type])}</span></td>
    <td>${item.document_status ? `<span class="status ${item.document_status}">${esc(labels.doc[item.document_status])}</span>` : '<span class="muted">未生成</span>'}</td>
    <td>${item.issue_key ? `<a class="jira-link" href="${esc(item.jira_url)}" target="_blank">${esc(item.issue_key)}</a>` : '<span class="muted">—</span>'}</td></tr>`).join("") : emptyRow("没有符合过滤条件的结果", 6);
  document.querySelectorAll("#operator-rows tr[data-id]").forEach(row => row.onclick = event => { if (!event.target.classList.contains("row-check") && event.target.tagName !== "A") showOperator(Number(row.dataset.id)); });
  document.querySelectorAll(".row-check").forEach(check => check.onchange = () => { const id = Number(check.dataset.id); check.checked ? state.selected.add(id) : state.selected.delete(id); renderOperators(); });
}
["op-search", "scope-filter", "change-filter", "doc-filter"].forEach(id => $(id).addEventListener(id === "op-search" ? "input" : "change", renderOperators));
$("select-all").onchange = () => { filteredOperators().filter(item => item.is_work_item).forEach(item => $("select-all").checked ? state.selected.add(item.id) : state.selected.delete(item.id)); renderOperators(); };

function openDrawer(html, wide = false) { $("drawer").className = `drawer open${wide ? " wide" : ""}`; $("drawer-content").innerHTML = html; document.querySelectorAll(".close-drawer").forEach(button => button.onclick = closeDrawer); }
function closeDrawer() { $("drawer").className = "drawer"; }
async function showOperator(id) {
  const data = await api(`/api/operators/${id}`), item = data.operator;
  openDrawer(`<div class="drawer-head"><button class="close-drawer">×</button><span class="change ${item.change_type}">${esc(labels.change[item.change_type])}</span><h2>${esc(item.name)}</h2><div class="muted">${esc(item.path)}:${item.line} · 置信度 ${Math.round(item.confidence * 100)}%</div></div>
  <div class="drawer-actions">${item.is_work_item ? `<button id="generate-doc" class="primary" ${item.document_id ? "disabled" : ""}>${item.document_id ? "文档已生成" : "生成 Engram 风格文档"}</button>` : ""}
  ${item.document_id ? `<button id="read-doc" class="secondary">阅读文档</button><button id="approve-doc" class="secondary" ${item.document_status === "approved" ? "disabled" : ""}>${item.document_status === "approved" ? "已审核" : "审核通过"}</button>` : ""}
  ${item.is_work_item ? `<button id="create-jira" class="secondary" ${item.document_status !== "approved" || item.issue_key ? "disabled" : ""}>${item.issue_key ? esc(item.issue_key) : "创建 Mock Jira"}</button>` : ""}
  ${item.issue_key ? `<a class="jira-link" href="${esc(item.jira_url)}" target="_blank">打开 Jira ↗</a>` : ""}</div>
  <section class="detail-section"><h3>范围分类</h3><div class="evidence"><b>${esc(labels.scope[item.scope])}</b><br>${esc(item.scope_reason)}</div></section>
  <section class="detail-section"><h3>识别依据</h3><div class="evidence">${esc(item.evidence)}</div></section>
  <section class="detail-section"><h3>固定版本源码</h3><p><a href="${esc(item.source_url)}" target="_blank">查看 GitHub 固定源码 ↗</a></p><pre>${esc(item.source || "无源码")}</pre></section>
  <details class="detail-section"><summary>Git diff 证据</summary><pre>${esc(item.diff || "无 diff")}</pre></details>
  <section class="detail-section"><h3>追踪记录</h3>${eventHtml(item.events, true)}</section>`);
  if ($("generate-doc")) $("generate-doc").onclick = () => operatorAction(id, "document", "文档已生成");
  if ($("approve-doc")) $("approve-doc").onclick = () => operatorAction(id, "approve", "文档已审核");
  if ($("create-jira")) $("create-jira").onclick = () => operatorAction(id, "jira", "Mock Jira 已创建");
  if ($("read-doc")) $("read-doc").onclick = () => showDocument(item.document_id);
}
async function operatorAction(id, action, message) {
  try { await api(`/api/operators/${id}/${action}`, {method: "POST", body: "{}"}); toast(message); await refreshData(); await showOperator(id); } catch (error) { toast(error.message); }
}
$("batch-documents").onclick = async () => {
  try { const ids = [...state.selected]; const data = await api("/api/documents/batch", {method: "POST", body: JSON.stringify({operator_ids: ids})}); const ok = data.results.filter(item => item.ok).length; toast(`批量生成完成：成功 ${ok}，失败 ${ids.length - ok}`); await refreshData(); } catch (error) { toast(error.message); }
};

function filteredDocuments() {
  const query = $("document-search").value.trim().toLowerCase(), status = $("document-status").value;
  return state.documents.filter(item => (status === "all" || item.status === status) && (!query || `${item.name} ${item.path}`.toLowerCase().includes(query)));
}
function renderDocuments() {
  const rows = filteredDocuments(); $("doc-count").textContent = `${state.documents.length} 份`;
  $("document-rows").innerHTML = rows.length ? rows.map(item => `<tr data-document-id="${item.id}"><td><div class="operator-name">${esc(item.name)}</div><div class="operator-path">DOC-${String(item.id).padStart(4, "0")} · ${esc(item.path)}</div></td><td class="kind">${esc(item.base_version)} → ${esc(item.target_version)}</td><td class="kind">${esc(item.generator)}</td><td><span class="status ${item.status}">${esc(labels.doc[item.status])}</span></td><td class="muted">${esc(item.created_at)}</td><td>${item.issue_key ? `<a class="jira-link" href="${esc(item.jira_url)}" target="_blank">${esc(item.issue_key)}</a>` : "—"}</td></tr>`).join("") : emptyRow("尚未生成文档", 6);
  document.querySelectorAll("[data-document-id]").forEach(row => row.onclick = event => { if (event.target.tagName !== "A") showDocument(Number(row.dataset.documentId)); });
}
["document-search", "document-status"].forEach(id => $(id).addEventListener(id === "document-search" ? "input" : "change", renderDocuments));
async function showDocument(id) {
  const data = await api(`/api/documents/${id}`), doc = data.document;
  openDrawer(`<div class="drawer-head"><button class="close-drawer">×</button><span class="status ${doc.status}">${esc(labels.doc[doc.status])}</span><h2>${esc(doc.name)}</h2><div class="document-meta"><span>DOC-${String(doc.id).padStart(4, "0")}</span><span>${esc(doc.generator)}</span><span>${esc(doc.created_at)}</span></div></div>
  <div class="drawer-actions"><a class="jira-link" href="/api/documents/${doc.id}/download" download="${esc(doc.name.replace(/[^\w.-]/g, "_"))}.md">下载 Markdown ↓</a><button id="document-operator" class="secondary">查看算子证据</button>${doc.issue_key ? `<a class="jira-link" href="${esc(doc.jira_url)}" target="_blank">${esc(doc.issue_key)} ↗</a>` : ""}</div>
  <article class="markdown-body">${renderMarkdown(doc.content)}</article>`, true);
  $("document-operator").onclick = () => { closeDrawer(); switchView("operators"); showOperator(doc.operator_id); };
}

function inlineMarkdown(text) {
  return esc(text).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g, '<a href="$2" target="_blank">$1</a>');
}
function renderMarkdown(markdown) {
  const lines = markdown.split("\n"), output = [];
  let inCode = false, codeLanguage = "", list = null;
  const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const fence = line.match(/^```(.*)/);
    if (fence) { closeList(); if (inCode) { output.push("</code></pre>"); inCode = false; } else { inCode = true; codeLanguage = fence[1]; output.push(`<pre><code class="language-${esc(codeLanguage)}">`); } continue; }
    if (inCode) { output.push(esc(line) + "\n"); continue; }
    if (!line.trim()) { closeList(); continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)/);
    if (heading) { closeList(); const level = heading[1].length; output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`); continue; }
    if (line.startsWith("|") && /^\|?[\s:|-]+\|/.test(lines[index + 1] || "")) {
      closeList(); const tableRows = [];
      while (index < lines.length && lines[index].startsWith("|")) { if (!/^\|?[\s:|-]+\|$/.test(lines[index])) tableRows.push(lines[index]); index++; }
      index--; output.push("<table>");
      tableRows.forEach((row, rowIndex) => { const cells = row.replace(/^\||\|$/g, "").split("|"); output.push(rowIndex === 0 ? "<thead><tr>" : "<tr>"); cells.forEach(cell => output.push(rowIndex === 0 ? `<th>${inlineMarkdown(cell.trim())}</th>` : `<td>${inlineMarkdown(cell.trim())}</td>`)); output.push(rowIndex === 0 ? "</tr></thead><tbody>" : "</tr>"); });
      output.push("</tbody></table>"); continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)/), ordered = line.match(/^\s*\d+\.\s+(.+)/);
    if (unordered || ordered) { const type = unordered ? "ul" : "ol"; if (list !== type) { closeList(); list = type; output.push(`<${type}>`); } output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`); continue; }
    closeList();
    if (line.startsWith(">")) output.push(`<blockquote>${inlineMarkdown(line.replace(/^>\s?/, ""))}</blockquote>`);
    else if (/^---+$/.test(line)) output.push("<hr>");
    else output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  closeList(); if (inCode) output.push("</code></pre>"); return output.join("\n");
}

function renderJiras() {
  $("jira-count").textContent = `${state.jiras.length} 项`;
  $("jira-rows").innerHTML = state.jiras.length ? state.jiras.map(item => `<tr data-jira-op="${item.operator_id}"><td><a class="jira-link" href="${esc(item.url)}" target="_blank">${esc(item.issue_key)} ↗</a></td><td><div class="operator-name">${esc(item.name)}</div><div class="operator-path">${esc(item.path)}</div></td><td class="kind">${esc(item.base_version)} → ${esc(item.target_version)}</td><td><span class="status ${item.document_status}">${esc(labels.doc[item.document_status])}</span></td><td class="kind">${esc(item.status)}</td><td class="muted">${esc(item.created_at)}</td></tr>`).join("") : emptyRow("尚未创建 Jira", 6);
  document.querySelectorAll("[data-jira-op]").forEach(row => row.onclick = event => { if (event.target.tagName !== "A") showOperator(Number(row.dataset.jiraOp)); });
}
function eventHtml(events, compact = false) {
  if (!events?.length) return '<span class="muted">暂无记录</span>';
  return events.map(event => `<div class="event"><span class="event-time">${esc(event.created_at)}</span><span class="event-message">${esc(event.message)}</span>${compact ? "" : `<span class="event-operator">${esc(event.operator_name || `${event.base_version || ""} → ${event.target_version || ""}`)}</span>`}</div>`).join("");
}
function renderEvents() {
  $("recent-activity").innerHTML = eventHtml(state.events.slice(0, 6), true);
  const query = $("event-search").value.trim().toLowerCase(), type = $("event-type").value;
  const rows = state.events.filter(event => (type === "all" || event.event_type.startsWith(type)) && (!query || `${event.message} ${event.operator_name || ""}`.toLowerCase().includes(query)));
  $("event-count").textContent = `${state.events.length} 条`; $("all-events").innerHTML = eventHtml(rows);
}
["event-search", "event-type"].forEach(id => $(id).addEventListener(id === "event-search" ? "input" : "change", renderEvents));
$("reanalyze").onclick = async () => { await api("/api/comparisons", {method: "POST", body: "{}"}); toast("已有分析会直接复用"); await refreshData(); };
document.addEventListener("keydown", event => { if (event.key === "Escape") closeDrawer(); });

refreshData().catch(error => toast(error.message));
setInterval(() => { if (!state.comparison || state.comparison.status === "running") refreshData().catch(console.error); }, 2500);
