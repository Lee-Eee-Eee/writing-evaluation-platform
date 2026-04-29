const STORAGE_KEY = "essay-benchmark-teachers";

const state = {
  studyConfig: null,
  selectedFile: null,
  lastResult: null,
  lastObjective: null,
  chatHistory: [],
  activeTab: "objective",
};

const nodes = {
  topicInput: document.getElementById("topicInput"),
  essayInput: document.getElementById("essayInput"),
  fileInput: document.getElementById("fileInput"),
  dropzone: document.getElementById("dropzone"),
  teachersContainer: document.getElementById("teachersContainer"),
  teacherTemplate: document.getElementById("teacherTemplate"),
  addTeacherButton: document.getElementById("addTeacherButton"),
  resetTeachersButton: document.getElementById("resetTeachersButton"),
  objectiveButton: document.getElementById("objectiveButton"),
  gradeButton: document.getElementById("gradeButton"),
  exportButton: document.getElementById("exportButton"),
  essayStats: document.getElementById("essayStats"),
  objectiveMeta: document.getElementById("objectiveMeta"),
  objectiveIntro: document.getElementById("objectiveIntro"),
  objectiveResults: document.getElementById("objectiveResults"),
  resultMeta: document.getElementById("resultMeta"),
  aggregateCard: document.getElementById("aggregateCard"),
  teacherResults: document.getElementById("teacherResults"),
  errorBanner: document.getElementById("errorBanner"),
  chatMessages: document.getElementById("chatMessages"),
  chatInput: document.getElementById("chatInput"),
  chatButton: document.getElementById("chatButton"),
};

function safeJsonParse(value, fallback) {
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === `${tabName}Pane`);
  });
}

function showError(message) {
  nodes.errorBanner.textContent = message;
  nodes.errorBanner.classList.remove("hidden");
}

function clearError() {
  nodes.errorBanner.textContent = "";
  nodes.errorBanner.classList.add("hidden");
}

async function readJsonResponse(response) {
  const text = await response.text();
  const contentType = response.headers.get("content-type") || "";
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    const preview = text.replace(/\s+/g, " ").trim().slice(0, 180);
    const kind = contentType.includes("text/html") || preview.startsWith("<")
      ? "接口返回了 HTML，通常意味着后端发生 500 错误、接口路径不匹配，或部署服务还没更新。"
      : "接口返回的内容不是合法 JSON。";
    throw new Error(`${kind} HTTP ${response.status}. ${preview}`);
  }
}

function loadStoredTeachers() {
  return safeJsonParse(localStorage.getItem(STORAGE_KEY), null);
}

function saveTeachers() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(readTeachers()));
}

function readTeachers() {
  return Array.from(nodes.teachersContainer.querySelectorAll(".teacher-card")).map((card) => {
    const teacher = {};
    card.querySelectorAll("[data-key]").forEach((field) => {
      teacher[field.dataset.key] = field.value;
    });
    teacher.temperature = Number(teacher.temperature || 0.2);
    return teacher;
  });
}

function primaryTeacher() {
  return readTeachers()[0] || null;
}

function updateEssayStats() {
  const text = nodes.essayInput.value.trim();
  const words = text ? text.split(/\s+/).filter(Boolean).length : 0;
  const fileInfo = state.selectedFile ? `文件：${state.selectedFile.name}` : "未选择文件";
  nodes.essayStats.textContent = `${fileInfo} | 字数估计：${words}`;
}

function createTeacherCard(teacher = {}) {
  const fragment = nodes.teacherTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".teacher-card");
  const title = fragment.querySelector(".teacher-head h3");
  title.textContent = teacher.name || "老师";

  card.querySelectorAll("[data-key]").forEach((field) => {
    const key = field.dataset.key;
    field.value = teacher[key] ?? "";
    field.addEventListener("input", () => {
      title.textContent = card.querySelector('[data-key="name"]').value || "老师";
      saveTeachers();
    });
  });

  card.querySelector(".remove-teacher").addEventListener("click", () => {
    card.remove();
    saveTeachers();
  });

  nodes.teachersContainer.appendChild(fragment);
}

function restoreTeachers() {
  nodes.teachersContainer.innerHTML = "";
  const stored = loadStoredTeachers();
  const source = Array.isArray(stored) && stored.length ? stored : state.studyConfig.teacher_presets;
  source.forEach((teacher) => createTeacherCard(teacher));
  saveTeachers();
}

function renderObjectiveDefinitions() {
  nodes.objectiveIntro.innerHTML = state.studyConfig.objective_metrics.map((item) => `
    <div class="definition-row">
      <strong>${escapeHtml(item.label_zh)}</strong>
      <span>${escapeHtml(item.label_en)}</span>
    </div>
  `).join("");
}

function metricLevel(metric) {
  if (metric.key === "lexical_diversity_mtld") {
    if (metric.value >= 80) return "高";
    if (metric.value >= 50) return "中";
    return "低";
  }
  if (metric.per_sentence >= 1.2) return "高";
  if (metric.per_sentence >= 0.4) return "中";
  return "低";
}

function renderObjective(payload) {
  state.lastObjective = payload;
  state.lastResult = state.lastResult || {};
  nodes.exportButton.disabled = false;
  const objective = payload.objective;
  nodes.objectiveMeta.textContent = `${objective.word_count} 词 | ${objective.sentence_count} 句 | 平均句长 ${objective.average_sentence_length} | ${objective.parser}`;
  nodes.objectiveResults.innerHTML = objective.metrics.map((metric) => `
    <article class="metric-card">
      <div class="metric-head">
        <div>
          <h3>${escapeHtml(metric.label_zh)}</h3>
          <p>${escapeHtml(metric.label_en)}</p>
        </div>
        <span class="score-pill">${escapeHtml(metricLevel(metric))}</span>
      </div>
      <div class="metric-value">${metric.value}</div>
      <p>${escapeHtml(metric.description_zh)}</p>
      <div class="metric-foot">每句均值：${metric.per_sentence}</div>
    </article>
  `).join("");
}

function criteriaTable(criteria, includeFeedback = false) {
  const rows = criteria.map((item) => `
    <tr>
      <td>${escapeHtml(item.label_zh)}<br><span class="muted">${escapeHtml(item.label_en)}</span></td>
      <td><span class="score-pill">${escapeHtml(item.score)}</span></td>
      ${includeFeedback ? `<td>${escapeHtml(item.reason || "")}</td><td>${escapeHtml(item.improvement || "")}</td>` : ""}
    </tr>
  `).join("");
  return `
    <table class="criteria-table">
      <thead>
        <tr>
          <th>维度</th>
          <th>分数</th>
          ${includeFeedback ? "<th>评价</th><th>建议</th>" : ""}
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function seedGuidance(payload) {
  state.chatHistory = [{
    role: "assistant",
    content: payload.guidance || "已完成评分。你可以继续追问某一段怎么改、某个维度为什么扣分，或让我给出示范改写。",
  }];
  renderChat();
}

function renderResults(payload) {
  state.lastResult = payload;
  if (payload.objective) {
    renderObjective({ essay: payload.essay, objective: payload.objective });
  }
  nodes.exportButton.disabled = false;
  nodes.errorBanner.classList.toggle("hidden", payload.failures.length === 0);
  nodes.errorBanner.innerHTML = payload.failures.map((item) => `${escapeHtml(item.teacher)}: ${escapeHtml(item.error)}`).join("<br>");
  nodes.resultMeta.textContent = `作文约 ${payload.essay.word_count} 词 | 成功评分老师数：${payload.results.length}`;

  nodes.aggregateCard.innerHTML = `
    <div class="aggregate-top">
      <div>
        <div class="muted">聚合总均分</div>
        <div class="aggregate-score">${escapeHtml(payload.aggregate.overall_score)} / 6</div>
      </div>
      <div class="muted">评分维度对齐原论文 7 项标准</div>
    </div>
    ${criteriaTable(payload.aggregate.criteria, false)}
  `;
  nodes.aggregateCard.classList.remove("hidden");

  nodes.teacherResults.innerHTML = payload.results.map((item) => `
    <article class="result-card">
      <div class="result-head">
        <div>
          <h3>${escapeHtml(item.teacher.name)}</h3>
          <div class="muted">${escapeHtml(item.teacher.model)}</div>
        </div>
        <span class="score-pill">${escapeHtml(item.result.overall_score)}</span>
      </div>
      ${criteriaTable(item.result.criteria, false)}
    </article>
  `).join("");

  seedGuidance(payload);
}

async function loadStudyConfig() {
  const response = await fetch("/api/study-config");
  const payload = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(payload.error || "无法加载研究配置。");
  }
  state.studyConfig = payload;
  restoreTeachers();
  renderObjectiveDefinitions();
}

function buildEssayForm() {
  const formData = new FormData();
  formData.append("topic", nodes.topicInput.value.trim());
  formData.append("essay_text", nodes.essayInput.value.trim());
  if (state.selectedFile) {
    formData.append("file", state.selectedFile);
  }
  return formData;
}

async function analyzeObjective() {
  clearError();
  const formData = buildEssayForm();
  nodes.objectiveButton.disabled = true;
  nodes.objectiveButton.textContent = "分析中...";
  try {
    const response = await fetch("/api/objective-analysis", { method: "POST", body: formData });
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error([payload.error, payload.detail].filter(Boolean).join("：") || "客观分析失败");
    }
    renderObjective(payload);
    setTab("objective");
  } catch (error) {
    showError(error.message);
  } finally {
    nodes.objectiveButton.disabled = false;
    nodes.objectiveButton.textContent = "客观特征分析";
  }
}

async function gradeEssay() {
  const teachers = readTeachers();
  if (!teachers.length) {
    showError("请至少配置一位老师。");
    return;
  }

  const formData = buildEssayForm();
  formData.append("teachers", JSON.stringify(teachers));

  clearError();
  nodes.gradeButton.disabled = true;
  nodes.gradeButton.textContent = "评分中...";

  try {
    const response = await fetch("/api/grade", { method: "POST", body: formData });
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      const failures = Array.isArray(payload.failures)
        ? payload.failures.map((item) => `${item.teacher}: ${item.error}`).join("；")
        : "";
      throw new Error([payload.error, payload.detail, failures].filter(Boolean).join("：") || "评分失败");
    }
    renderResults(payload);
    setTab("chat");
  } catch (error) {
    showError(error.message);
  } finally {
    nodes.gradeButton.disabled = false;
    nodes.gradeButton.textContent = "七维度主观评分";
  }
}

function exportJson() {
  const payload = {
    objective: state.lastObjective,
    subjective: state.lastResult,
    chat: state.chatHistory,
  };
  if (!payload.objective && !payload.subjective) {
    return;
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "essay-analysis-result.json";
  link.click();
  URL.revokeObjectURL(url);
}

function renderChat() {
  nodes.chatMessages.innerHTML = state.chatHistory.map((message) => `
    <div class="chat-message ${message.role === "user" ? "user" : "assistant"}">
      ${escapeHtml(message.content)}
    </div>
  `).join("");
  nodes.chatMessages.scrollTop = nodes.chatMessages.scrollHeight;
}

async function sendChat() {
  const message = nodes.chatInput.value.trim();
  const teacher = primaryTeacher();
  const essayText = state.lastResult?.essay?.text || nodes.essayInput.value.trim();
  if (!message) return;
  if (!teacher) {
    showError("请先配置至少一位老师。");
    return;
  }
  if (!essayText) {
    showError("请先输入作文文本。");
    return;
  }

  clearError();
  state.chatHistory.push({ role: "user", content: message });
  nodes.chatInput.value = "";
  renderChat();
  nodes.chatButton.disabled = true;
  nodes.chatButton.textContent = "回复中...";

  try {
    const gradeResult = state.lastResult?.results?.[0]?.result || null;
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic: nodes.topicInput.value.trim(),
        essay_text: essayText,
        message,
        teacher,
        grade_result: gradeResult,
        history: state.chatHistory.slice(0, -1),
      }),
    });
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error([payload.error, payload.detail].filter(Boolean).join("：") || "聊天失败");
    }
    state.chatHistory.push({ role: "assistant", content: payload.reply });
    renderChat();
  } catch (error) {
    showError(error.message);
  } finally {
    nodes.chatButton.disabled = false;
    nodes.chatButton.textContent = "发送";
  }
}

function attachDropzone() {
  const openPicker = () => nodes.fileInput.click();
  nodes.dropzone.addEventListener("click", openPicker);
  nodes.dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") openPicker();
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    nodes.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      nodes.dropzone.classList.add("dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    nodes.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      nodes.dropzone.classList.remove("dragging");
    });
  });

  nodes.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files?.[0];
    if (file) {
      state.selectedFile = file;
      updateEssayStats();
    }
  });

  nodes.fileInput.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (file) {
      state.selectedFile = file;
      updateEssayStats();
    }
  });
}

function attachEvents() {
  nodes.addTeacherButton.addEventListener("click", () => {
    createTeacherCard({
      name: "New Teacher",
      base_url: "https://api.openai.com/v1",
      model: "",
      api_key: "",
      api_key_env: "OPENAI_API_KEY",
      temperature: 0.2,
      extra_body: "",
    });
    saveTeachers();
  });

  nodes.resetTeachersButton.addEventListener("click", restoreTeachers);
  nodes.objectiveButton.addEventListener("click", analyzeObjective);
  nodes.gradeButton.addEventListener("click", gradeEssay);
  nodes.exportButton.addEventListener("click", exportJson);
  nodes.chatButton.addEventListener("click", sendChat);
  nodes.chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) sendChat();
  });
  nodes.essayInput.addEventListener("input", updateEssayStats);
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.tab));
  });
  attachDropzone();
}

async function boot() {
  attachEvents();
  await loadStudyConfig();
  updateEssayStats();
  renderChat();
}

boot().catch((error) => showError(error.message));
