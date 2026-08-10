async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "요청 실패");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("ko-KR");
}

function shortText(value, max = 70) {
  const text = String(value ?? "").trim();
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function documentStatusLabel(status) {
  return {
    pending: "대기",
    processing: "처리 중",
    collected: "수집",
    duplicate: "기존 문서",
    failed: "실패",
  }[status] || status || "미확인";
}

function parseStatusLabel(status) {
  return {
    not_requested: "파싱 대기",
    parsing: "파싱 중",
    parsed: "파싱 완료",
    empty: "파싱 결과 없음",
    failed: "파싱 실패",
    unsupported: "지원 불가",
  }[status] || status || "파싱 대기";
}

function candidateStatusMeta(item) {
  if (item.candidate_status === "verified") {
    return { label: "승인", className: "verified", percent: 100 };
  }
  if (item.candidate_status === "rejected") {
    return { label: "반려", className: "rejected", percent: 100 };
  }
  if (item.verification_status === "not_requested") {
    return { label: "검증 대기", className: "pending", percent: 12 };
  }
  return { label: "수동검토", className: "needs_review", percent: 100 };
}

function compactText(...values) {
  return values.filter((value) => String(value || "").trim()).join(" · ");
}

const WORKFLOW_LOCAL_LOG_KEY = "publicRestaurant.workflow.localLogs";

function select(selector) {
  return document.querySelector(selector);
}

function setText(selector, value) {
  const node = select(selector);
  if (node) node.textContent = value;
}

function on(selector, eventName, handler) {
  const node = select(selector);
  if (node) node.addEventListener(eventName, handler);
}

function loadStoredLocalLogs() {
  try {
    const raw = window.sessionStorage?.getItem(WORKFLOW_LOCAL_LOG_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 20) : [];
  } catch {
    return [];
  }
}

function saveStoredLocalLogs() {
  try {
    window.sessionStorage?.setItem(
      WORKFLOW_LOCAL_LOG_KEY,
      JSON.stringify(workflowState.localLogs.slice(0, 20))
    );
  } catch {
    // Ignore storage failures; persisted server logs still render.
  }
}

const workflowState = {
  mode: document.querySelector("[data-workflow-mode]")?.dataset.workflowMode || "collection",
  documents: null,
  candidates: null,
  dashboard: null,
  logs: null,
  currentPlanId: null,
  activeStatus: "needs_review",
  documentOffset: 0,
  documentLimit: 25,
  candidateOffset: 0,
  candidateLimit: 25,
  selectedPriority: "",
  selectedInstitution: "",
  localLogs: loadStoredLocalLogs(),
  verificationProgress: null,
  verificationProgressTimer: null,
  verificationRequestPending: false,
  verificationRequestedTotal: 0,
  verificationRequestedAt: "",
  syncedPlanPeriod: false,
};

function periodPayload() {
  return {
    start_date: select("#workflow-start-date")?.value || "",
    end_date: select("#workflow-end-date")?.value || "",
    batch_size: selectedBatchSize(),
  };
}

function selectedBatchMode() {
  return select("#workflow-batch-mode")?.value === "custom" ? "custom" : "all";
}

function selectedBatchSize() {
  const value = Number(select("#workflow-batch-size")?.value || 100);
  if (!Number.isFinite(value)) return 100;
  return Math.max(1, Math.min(Math.trunc(value), 200));
}

function batchExecutionPayload() {
  const repeat = selectedBatchMode() === "all";
  return {
    batch_size: selectedBatchSize(),
    repeat,
    max_batches: repeat ? 100 : 1,
  };
}

function syncBatchControls() {
  const field = select("#workflow-batch-size-field");
  const input = select("#workflow-batch-size");
  const custom = selectedBatchMode() === "custom";
  if (field) field.hidden = !custom;
  if (input) input.disabled = !custom;
  updateStepsAndSummary();
}

function verifyLimit() {
  return Number(select("#workflow-verify-limit")?.value || 100);
}

function selectedInstitution() {
  return document.querySelector("#workflow-institution")?.value || "";
}

function documentQueryParams() {
  const period = periodPayload();
  const params = new URLSearchParams({
    start_date: period.start_date,
    end_date: period.end_date,
    institution: selectedInstitution(),
    status: select("#workflow-document-status")?.value || "",
    parse_status: select("#workflow-parse-status")?.value || "",
    sort: "published_desc",
    limit: String(workflowState.documentLimit),
    offset: String(workflowState.documentOffset),
  });
  if (workflowState.currentPlanId) params.set("plan_id", String(workflowState.currentPlanId));
  return params;
}

function candidateQueryParams() {
  const period = periodPayload();
  return new URLSearchParams({
    start_date: period.start_date,
    end_date: period.end_date,
    institution: selectedInstitution(),
    status: select("#workflow-candidate-status")?.value || "needs_review",
    q: select("#workflow-candidate-search")?.value.trim() || "",
    sort: select("#workflow-candidate-sort")?.value || "verification_oldest",
    limit: String(workflowState.candidateLimit),
    offset: String(workflowState.candidateOffset),
    needs_review_offset: "0",
    verified_offset: "0",
    rejected_offset: "0",
  });
}

function dashboardQueryParams() {
  const period = periodPayload();
  return new URLSearchParams({
    start_date: period.start_date,
    end_date: period.end_date,
  });
}

function logsQueryParams() {
  const params = new URLSearchParams({ limit: "20" });
  if (workflowState.currentPlanId) params.set("plan_id", String(workflowState.currentPlanId));
  return params;
}

function showToast(message, isError = false) {
  const toast = select("#workflow-toast");
  if (!toast) return;
  toast.hidden = false;
  toast.className = `workflow-toast ${isError ? "error" : "success"}`;
  toast.textContent = message;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4200);
}

function appendLog(message, tone = "info") {
  workflowState.localLogs.unshift({
    message,
    tone,
    time: new Date().toISOString(),
  });
  workflowState.localLogs = workflowState.localLogs.slice(0, 20);
  saveStoredLocalLogs();
  renderLogPanel();
}

function setStepStatus(step, status) {
  const item = document.querySelector(`#workflow-steps [data-step="${step}"]`);
  if (!item) return;
  item.classList.remove("active", "done", "warn");
  if (status) item.classList.add(status);
}

function candidateTotals() {
  const groups = workflowState.candidates?.groups || {};
  const pending = Number(groups.needs_review?.pending_total || 0);
  const manual = Number(groups.needs_review?.manual_total ?? groups.needs_review?.total ?? 0);
  return {
    needs_review: pending + manual,
    pending,
    manual,
    verified: Number(groups.verified?.total || 0),
    rejected: Number(groups.rejected?.total || 0),
  };
}

function updateStepsAndSummary() {
  const period = periodPayload();
  const summary = workflowState.documents?.summary || {};
  const documentTotal = Number(summary.documents || 0);
  const collected = Number(summary.collected || 0);
  const failed = Number(summary.failed || 0);
  const parsed = Number(summary.parsed || 0);
  const parsePending = Number(summary.parse_pending || 0);
  const parseFailed = Number(summary.parse_failed || 0);
  const parseUnsupported = Number(summary.parse_unsupported || 0);
  const parseEmpty = Number(summary.parse_empty || 0);
  const totals = candidateTotals();
  const candidateTotal = totals.needs_review + totals.verified + totals.rejected;
  const batchSize = Number(period.batch_size || 100);
  const batchMode = selectedBatchMode();
  const verifyBatchSize = verifyLimit();

  setText("#workflow-range-label", `${period.start_date} ~ ${period.end_date}`);
  setText("#workflow-step-period", `${period.start_date} ~ ${period.end_date}`);
  setText("#workflow-step-list", `문서 ${formatNumber(documentTotal)}건`);
  setText(
    "#workflow-step-collect-batch",
    batchMode === "all" ? "전체" : `${formatNumber(batchSize)}건`
  );
  setText(
    "#workflow-step-collect",
    failed ? `실패 ${formatNumber(failed)}건` : `수집 ${formatNumber(collected)}건`
  );
  setText(
    "#workflow-step-parse",
    parseFailed
      ? `실패 ${formatNumber(parseFailed)}건`
      : parseUnsupported
      ? `지원 불가 ${formatNumber(parseUnsupported)}건`
      : parsePending
      ? `대기 ${formatNumber(parsePending)}건`
      : parseEmpty
      ? `빈 문서 ${formatNumber(parseEmpty)}건`
      : `완료 ${formatNumber(parsed)}건`
  );
  setText("#workflow-step-verify-batch", `${formatNumber(verifyBatchSize)}건 · 대기 ${formatNumber(totals.pending)}`);
  setText("#workflow-step-result", `승인 ${formatNumber(totals.verified)} · 수동 ${formatNumber(totals.manual)} · 반려 ${formatNumber(totals.rejected)}`);

  setText("#workflow-document-total", formatNumber(documentTotal));
  setText("#workflow-document-collected", formatNumber(collected));
  setText("#workflow-document-failed", formatNumber(failed));
  setText("#workflow-parse-pending", formatNumber(parsePending));
  setText("#workflow-parse-success", formatNumber(parsed));
  setText("#workflow-parse-failed", formatNumber(parseFailed));
  setText("#workflow-candidate-total", formatNumber(candidateTotal));
  setText("#workflow-approved-total", formatNumber(totals.verified));
  setText("#workflow-review-total", formatNumber(totals.manual));
  setText("#workflow-rejected-total", formatNumber(totals.rejected));

  setStepStatus("period", "done");
  setStepStatus("list", documentTotal ? "done" : "active");
  setStepStatus("collect-batch", documentTotal ? "active" : "");
  setStepStatus("collect", failed ? "warn" : collected ? "done" : documentTotal ? "active" : "");
  setStepStatus(
    "parse",
    parseFailed || parseUnsupported ? "warn" : parsePending ? "active" : parsed || parseEmpty ? "done" : ""
  );
  setStepStatus("verify-batch", totals.pending ? "active" : candidateTotal ? "done" : "");
  setStepStatus("result", totals.verified || totals.rejected || totals.manual ? "done" : "");
}

function renderScopeFilters() {
  const prioritySelect = document.querySelector("#workflow-priority");
  const institutionSelect = document.querySelector("#workflow-institution");
  if (!prioritySelect || !institutionSelect) return;
  const priorities = workflowState.dashboard?.priorities || [];
  const previousPriority = workflowState.selectedPriority || prioritySelect.value || "";
  const previousInstitution = workflowState.selectedInstitution || institutionSelect.value || "";

  prioritySelect.innerHTML = [
    '<option value="">전체 순위</option>',
    ...priorities.map((item) => {
      const value = String(item.priority);
      const selected = value === previousPriority ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${selected}>${escapeHtml(value)}순위 · ${escapeHtml(item.label || "미분류")}</option>`;
    }),
  ].join("");
  workflowState.selectedPriority = prioritySelect.value;

  const institutionMap = new Map();
  priorities
    .filter((item) => !workflowState.selectedPriority || String(item.priority) === workflowState.selectedPriority)
    .flatMap((item) => item.institutions || [])
    .forEach((item) => {
      const label = String(item.label || "").trim();
      if (label) institutionMap.set(label, item);
    });
  const institutionOptions = [...institutionMap.values()].sort((a, b) => {
    const totalDiff = Number(b.total || 0) - Number(a.total || 0);
    return totalDiff || String(a.label).localeCompare(String(b.label), "ko-KR");
  });
  const hasPreviousInstitution = institutionOptions.some((item) => item.label === previousInstitution);
  workflowState.selectedInstitution = hasPreviousInstitution ? previousInstitution : "";
  institutionSelect.innerHTML = [
    '<option value="">전체 기관</option>',
    ...institutionOptions.map((item) => {
      const selected = item.label === workflowState.selectedInstitution ? " selected" : "";
      const detail = item.total ? ` · 문서 ${formatNumber(item.total)}` : "";
      return `<option value="${escapeHtml(item.label)}"${selected}>${escapeHtml(item.label)}${detail}</option>`;
    }),
  ].join("");
}

function renderDocumentRows() {
  const node = document.querySelector("#workflow-document-rows");
  if (!node) return;
  const payload = workflowState.documents || {};
  const items = payload.items || [];
  if (!items.length) {
    node.innerHTML = '<tr><td colspan="5">조건에 맞는 수집 문서가 없습니다.</td></tr>';
    return;
  }
  node.innerHTML = items.map((item, index) => {
    const verificationPercent = Number(item.verification_percent || 0);
    const statusPercent = {
      pending: 0,
      processing: 45,
      collected: 100,
      duplicate: 100,
      failed: 100,
    }[item.status] ?? verificationPercent;
    const statusClass = item.status === "failed" ? "failed" : item.status === "processing" ? "processing" : item.status === "pending" ? "pending" : "collected";
    const detailParams = new URLSearchParams({ from: window.location.pathname + window.location.search });
    return `
      <tr>
        <td>${Number(payload.offset || 0) + index + 1}</td>
        <td>${escapeHtml(item.published_at || "-")}</td>
        <td>
          <a href="/admin/documents/${item.id}?${detailParams.toString()}">${escapeHtml(shortText(item.display_title || item.source_title || "제목 없음", 92))}</a>
          <small>${escapeHtml(item.institution_label || item.department_name || "")}</small>
        </td>
        <td>
          <span class="workflow-progress ${statusClass}" aria-label="${escapeHtml(documentStatusLabel(item.status))} ${statusPercent}%">
            <i style="width:${statusPercent}%"></i>
          </span>
          <small>${escapeHtml(parseStatusLabel(item.parse_status))}${item.parse_error_message ? ` · ${escapeHtml(shortText(item.parse_error_message, 42))}` : ""}</small>
        </td>
        <td>
          <span class="workflow-status-pill ${statusClass}">${escapeHtml(documentStatusLabel(item.status))}</span>
          <small>행 ${formatNumber(item.rows_seen || 0)} · 수집 ${formatNumber(item.attempts || 0)} · 파싱 ${formatNumber(item.parse_attempts || 0)}</small>
        </td>
      </tr>
    `;
  }).join("");
}

function evidenceText(item) {
  const name = Number(item.name_similarity || 0);
  const address = Number(item.address_similarity || 0);
  if (!name && !address) return "검증 대기";
  return `이름 ${(name * 100).toFixed(0)}% · 주소 ${(address * 100).toFixed(0)}%`;
}

function evidencePercent(item, meta) {
  const name = Number(item.name_similarity || 0);
  const address = Number(item.address_similarity || 0);
  if (!name && !address) return meta.percent;
  return Math.max(12, Math.round(Math.max(name, address) * 100));
}

function verificationProgressItem(candidateId) {
  const items = workflowState.verificationProgress?.items || [];
  return items.find((item) => Number(item.candidate_id) === Number(candidateId)) || null;
}

function verificationIsRunning() {
  return Boolean(workflowState.verificationRequestPending || workflowState.verificationProgress?.active);
}

function renderVerificationActivity() {
  const running = verificationIsRunning();
  const status = select("#workflow-verification-running");
  const button = select("#workflow-run-verification");
  if (status) status.hidden = !running;
  if (button) {
    button.disabled = running;
    button.setAttribute("aria-busy", running ? "true" : "false");
  }
}

function progressStatusMeta(item, progress) {
  if (!progress) {
    const meta = candidateStatusMeta(item);
    return {
      ...meta,
      percent: evidencePercent(item, meta),
      detail: evidenceText(item),
    };
  }
  if (progress.status === "failed") {
    return {
      label: progress.label || "검증 실패",
      className: "failed",
      percent: 100,
      detail: progress.stage || "failed",
    };
  }
  if (progress.status === "completed") {
    const completedMeta = {
      approved: { label: "승인", className: "verified" },
      needs_review: { label: "수동검토", className: "needs_review" },
      rejected: { label: "반려", className: "rejected" },
    }[progress.decision] || candidateStatusMeta(item);
    return {
      ...completedMeta,
      percent: 100,
      detail: progress.label || "검증 완료",
    };
  }
  return {
    label: progress.label || "검증 중",
    className: progress.status === "queued" ? "queued" : "running",
    percent: Number(progress.percent || 0),
    detail: progress.label || progress.stage || "검증 중",
  };
}

function renderCandidateRows() {
  const node = document.querySelector("#workflow-candidate-rows");
  if (!node) return;
  const selected = workflowState.candidates?.selected || {};
  const items = selected.items || [];
  if (!items.length) {
    node.innerHTML = '<tr><td colspan="7">선택한 조건의 검증 항목이 없습니다.</td></tr>';
    return;
  }
  node.innerHTML = items.map((item, index) => {
    const meta = progressStatusMeta(item, verificationProgressItem(item.candidate_id));
    return `
      <tr>
        <td>${Number(selected.offset || 0) + index + 1}</td>
        <td>${escapeHtml(item.source_published_at || "-")}</td>
        <td>${escapeHtml(item.used_date || "-")}</td>
        <td title="${escapeHtml(item.original_place_name || "")}">${escapeHtml(shortText(item.original_place_name || "-", 38))}</td>
        <td title="${escapeHtml(item.original_address || "")}">${escapeHtml(shortText(item.original_address || "-", 42))}</td>
        <td>
          <span class="workflow-progress ${meta.className}" aria-label="${escapeHtml(meta.label)} ${meta.percent}%">
            <i style="width:${meta.percent}%"></i>
          </span>
          <small>${escapeHtml(meta.detail)}</small>
        </td>
        <td>
          <span class="workflow-status-pill ${meta.className}">${escapeHtml(meta.label)}</span>
          <small>#${item.candidate_id}</small>
        </td>
      </tr>
    `;
  }).join("");
}

function renderPagination(selector, payload, setOffset) {
  const node = document.querySelector(selector);
  if (!node) return;
  const total = Number(payload?.total || 0);
  const offset = Number(payload?.offset || 0);
  const limit = Number(payload?.limit || 25);
  const itemCount = Number(payload?.items?.length || 0);
  const start = total ? offset + 1 : 0;
  const end = total ? Math.min(offset + itemCount, total) : 0;
  node.innerHTML = `
    <span>${formatNumber(start)}-${formatNumber(end)} / ${formatNumber(total)}</span>
    <button type="button" data-page="prev"${offset <= 0 ? " disabled" : ""}>이전</button>
    <button type="button" data-page="next"${offset + itemCount >= total ? " disabled" : ""}>다음</button>
  `;
  node.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      const nextOffset = button.dataset.page === "prev"
        ? Math.max(0, offset - limit)
        : offset + limit;
      setOffset(nextOffset);
      loadWorkflowData().catch((error) => {
        appendLog(`페이지 조회 실패: ${error.message}`, "error");
        showToast(error.message, true);
      });
    });
  });
}

function renderDocumentPagination() {
  renderPagination("#workflow-document-pagination", workflowState.documents || {}, (offset) => {
    workflowState.documentOffset = offset;
  });
}

function renderCandidatePagination() {
  renderPagination("#workflow-candidate-pagination", workflowState.candidates?.selected || {}, (offset) => {
    workflowState.candidateOffset = offset;
  });
}

function renderDbList() {
  const node = document.querySelector("#workflow-db-list");
  if (!node) return;
  const group = workflowState.candidates?.groups?.[workflowState.activeStatus] || { items: [], total: 0 };
  const items = group.items || [];
  if (!items.length) {
    node.innerHTML = '<p class="empty">해당 상태의 항목이 없습니다.</p>';
    return;
  }
  node.innerHTML = items.map((item) => {
    const meta = candidateStatusMeta(item);
    const effectiveName = item.effective_place_name || item.original_place_name || "상호명 없음";
    const effectiveAddress = item.effective_address || item.original_address || "주소 없음";
    return `
      <article class="workflow-db-row ${meta.className}">
        <div class="workflow-db-row-head">
          <strong>${escapeHtml(effectiveName)}</strong>
          <span>#${item.candidate_id} · ${escapeHtml(meta.label)} · ${escapeHtml(item.review_reason || item.rejection_reason || item.review_note || "")}</span>
        </div>
        ${item.provider_place_name ? `
          <p class="workflow-provider-line">
            최신 후보: ${escapeHtml(item.provider_place_name)}
            · ${escapeHtml(item.provider_category || "")}
            · ${escapeHtml(evidenceText(item))}
            · ${escapeHtml(item.provider_road_address || item.provider_address || "")}
          </p>
        ` : ""}
        <dl>
          <div><dt>문서</dt><dd>${escapeHtml(shortText(item.source_title || "-", 54))}</dd></div>
          <div><dt>방문일</dt><dd>${escapeHtml(item.used_date || "-")}</dd></div>
          <div><dt>기관</dt><dd>${escapeHtml(compactText(item.institution_name, item.department_name) || "-")}</dd></div>
          <div><dt>원문 주소</dt><dd>${escapeHtml(item.original_address || "-")}</dd></div>
          <div><dt>보정 주소</dt><dd>${escapeHtml(effectiveAddress)}</dd></div>
          <div><dt>목적</dt><dd>${escapeHtml(shortText(item.purpose || "-", 72))}</dd></div>
        </dl>
      </article>
    `;
  }).join("");
}

function formatLogTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function logToneFromStatus(status) {
  if (status === "success" || status === "completed") return "success";
  if (status === "failed" || status === "error") return "error";
  return "info";
}

function batchLogMessage(batch) {
  const summary = batch.summary || {};
  const jobType = batch.job_type || batch.job_name || "";
  if (jobType === "collection_plan_batch" || jobType === "collection_plan_run") {
    return `수집 배치 ${batch.status}: 대기 ${formatNumber(summary.pending_before)}→${formatNumber(summary.pending_after)} · 문서 ${formatNumber(summary.documents_seen || 0)} · 실패 ${formatNumber(summary.dlq || 0)}`;
  }
  if (jobType === "collection_plan_parse") {
    const retried = Number(summary.retried_parse_failed || 0);
    return [
      `파싱 ${batch.status}: 문서 ${formatNumber(summary.documents_parsed || 0)}/${formatNumber(summary.documents_seen || 0)}`,
      retried ? `재시도 ${formatNumber(retried)}` : "",
      `신규 행 ${formatNumber(summary.rows_inserted || 0)}`,
      `실패 ${formatNumber(summary.dlq || 0)}`,
    ].filter(Boolean).join(" · ");
  }
  if (jobType === "collection_plan_discover") {
    return `목록 가져오기 ${batch.status}: 문서 ${formatNumber(summary.documents_inserted || 0)}/${formatNumber(summary.documents_seen || 0)} · 기존 ${formatNumber(summary.documents_skipped_collected || 0)} · 실패 ${formatNumber(summary.dlq || 0)}`;
  }
  if (jobType === "verify_collected_candidates" || jobType === "verify_collected") {
    return `검증 ${batch.status}: 처리 ${formatNumber(summary.rows_processed || 0)} · 승인 ${formatNumber(summary.approved || 0)} · 수동 ${formatNumber(summary.needs_review || 0)} · 반려 ${formatNumber(summary.rejected || 0)}`;
  }
  return `${jobType || "작업"} ${batch.status || ""}`.trim();
}

function persistedLogEntries() {
  const logs = workflowState.logs || {};
  const batches = (logs.batches || []).map((batch) => ({
    time: batch.completed_at || batch.updated_at || batch.created_at,
    message: batchLogMessage(batch),
    tone: logToneFromStatus(batch.status),
  }));
  const dlq = (logs.dlq || []).map((item) => ({
    time: item.created_at,
    message: `${item.stage || "실패"}: ${item.error_message || "오류 메시지 없음"}`,
    tone: "error",
  }));
  return [...batches, ...dlq];
}

function verificationProgressLogEntry() {
  const progress = workflowState.verificationProgress || {};
  const running = verificationIsRunning();
  if (!running && !progress.batch_id) return null;

  const waitingForStart = workflowState.verificationRequestPending && !progress.active;
  const counts = waitingForStart
    ? { approved: 0, needs_review: 0, rejected: 0, failed: 0 }
    : { approved: 0, needs_review: 0, rejected: 0, failed: 0, ...(progress.classifications || {}) };
  const total = waitingForStart
    ? workflowState.verificationRequestedTotal
    : Number(progress.total || 0);
  const processed = waitingForStart ? 0 : Number(progress.processed || 0);
  const label = running ? "검증 진행 중" : "최근 검증 완료";
  return {
    time: waitingForStart ? workflowState.verificationRequestedAt : progress.updated_at,
    message: `${label}: ${formatNumber(processed)}/${formatNumber(total)}건 완료 · 분류 승인 ${formatNumber(counts.approved)} · 수동검토 ${formatNumber(counts.needs_review)} · 반려 ${formatNumber(counts.rejected)} · 실패 ${formatNumber(counts.failed)}`,
    tone: running ? "info" : "success",
    className: "verification-progress",
  };
}

function renderLogPanel() {
  const node = document.querySelector("#workflow-log-list");
  if (!node) return;
  const progressEntry = verificationProgressLogEntry();
  const entries = [
    ...(progressEntry ? [progressEntry] : []),
    ...workflowState.localLogs,
    ...persistedLogEntries(),
  ]
    .sort((a, b) => String(b.time || "").localeCompare(String(a.time || "")))
    .slice(0, 40);
  if (!entries.length) {
    node.innerHTML = '<p class="workflow-log-entry"><span>-</span>저장된 작업 로그가 없습니다.</p>';
    return;
  }
  node.innerHTML = entries.map((entry) => `
    <p class="workflow-log-entry ${escapeHtml(entry.tone || "info")} ${escapeHtml(entry.className || "")}">
      <span>${escapeHtml(formatLogTime(entry.time))}</span>${escapeHtml(entry.message)}
    </p>
  `).join("");
}

function selectedPlan(logs) {
  const plans = logs?.plans || [];
  return plans.find((item) => Number(item.id) === Number(logs.selected_plan_id)) || plans[0] || null;
}

function currentPlanMatchesPeriod(logs = workflowState.logs) {
  const plan = selectedPlan(logs);
  const period = periodPayload();
  return Boolean(
    plan
    && Number(plan.id) === Number(workflowState.currentPlanId)
    && plan.start_date === period.start_date
    && plan.end_date === period.end_date
  );
}

function syncPeriodFromSelectedPlan(logs) {
  if (workflowState.syncedPlanPeriod) return;
  const plan = selectedPlan(logs);
  if (!plan) {
    workflowState.syncedPlanPeriod = true;
    return;
  }
  if (plan.start_date) document.querySelector("#workflow-start-date").value = plan.start_date;
  if (plan.end_date) document.querySelector("#workflow-end-date").value = plan.end_date;
  const batchSize = document.querySelector("#workflow-batch-size");
  if (plan.batch_size && batchSize) batchSize.value = Number(plan.batch_size);
  workflowState.syncedPlanPeriod = true;
}

function renderAll() {
  renderScopeFilters();
  renderDocumentRows();
  renderDocumentPagination();
  renderCandidateRows();
  renderCandidatePagination();
  renderVerificationActivity();
  renderLogPanel();
  updateStepsAndSummary();
}

async function loadWorkflowData() {
  const logs = await fetchJson(`/ops/logs?${logsQueryParams().toString()}`);
  workflowState.logs = logs;
  workflowState.currentPlanId = logs.selected_plan_id || workflowState.currentPlanId;
  syncPeriodFromSelectedPlan(logs);

  const shouldLoadDocuments = Boolean(select("#workflow-document-rows"));
  const shouldLoadCandidates = Boolean(select("#workflow-candidate-rows"));
  const [dashboard, documents, candidates, progress] = await Promise.all([
    fetchJson(`/ops/dashboard?${dashboardQueryParams().toString()}`),
    shouldLoadDocuments
      ? fetchJson(`/admin/documents/data?${documentQueryParams().toString()}`)
      : Promise.resolve({ summary: {}, items: [], total: 0 }),
    shouldLoadCandidates
      ? fetchJson(`/admin/candidates?${candidateQueryParams().toString()}`)
      : Promise.resolve({ groups: {}, selected: { items: [], total: 0 } }),
    shouldLoadCandidates ? fetchJson("/ops/verification-progress") : Promise.resolve({ items: [] }),
  ]);
  workflowState.dashboard = dashboard;
  workflowState.documents = documents;
  workflowState.candidates = candidates;
  workflowState.verificationProgress = progress;
  renderAll();
  if (progress.active && !workflowState.verificationProgressTimer) {
    startVerificationProgressPolling();
  }
}

async function refreshVerificationProgress() {
  if (!select("#workflow-candidate-rows")) return { items: [] };
  const progress = await fetchJson("/ops/verification-progress");
  workflowState.verificationProgress = progress;
  renderCandidateRows();
  renderVerificationActivity();
  renderLogPanel();
  updateStepsAndSummary();
  if (!verificationIsRunning()) stopVerificationProgressPolling();
  return progress;
}

function startVerificationProgressPolling() {
  stopVerificationProgressPolling();
  refreshVerificationProgress().catch(() => {});
  workflowState.verificationProgressTimer = window.setInterval(() => {
    refreshVerificationProgress().catch((error) => {
      appendLog(`검증 진행 조회 실패: ${error.message}`, "error");
    });
  }, 600);
}

function stopVerificationProgressPolling() {
  if (!workflowState.verificationProgressTimer) return;
  window.clearInterval(workflowState.verificationProgressTimer);
  workflowState.verificationProgressTimer = null;
}

function actionSummary(payload) {
  const summary = payload.summary || {};
  if (summary.rows_processed !== undefined) {
    return `처리 ${formatNumber(summary.rows_processed)} · 승인 ${formatNumber(summary.approved)} · 수동 ${formatNumber(summary.needs_review)} · 반려 ${formatNumber(summary.rejected)}`;
  }
  if (summary.documents_parsed !== undefined) {
    const retried = Number(summary.retried_parse_failed || 0);
    return [
      retried ? `재시도 ${formatNumber(retried)}` : "",
      `파싱 ${formatNumber(summary.documents_parsed)}/${formatNumber(summary.documents_seen || 0)}`,
      `빈 문서 ${formatNumber(summary.documents_empty || 0)}`,
      `신규 행 ${formatNumber(summary.rows_inserted || 0)}`,
      `실패 ${formatNumber(summary.dlq || 0)}`,
    ].filter(Boolean).join(" · ");
  }
  if (summary.documents_seen !== undefined) {
    const skipped = Number(summary.documents_skipped_collected || 0);
    return [
      `문서 ${formatNumber(summary.documents_inserted || 0)}/${formatNumber(summary.documents_seen || 0)}`,
      skipped ? `기존 ${formatNumber(skipped)}` : "",
      `실패 ${formatNumber(summary.dlq || 0)}`,
    ].filter(Boolean).join(" · ");
  }
  if (summary.pending_before !== undefined) {
    return `대기 ${formatNumber(summary.pending_before)}→${formatNumber(summary.pending_after)} · 신규 행 ${formatNumber(summary.rows_inserted || 0)} · DLQ ${formatNumber(summary.dlq || 0)}`;
  }
  return payload.status || "완료";
}

async function runWorkflowAction(button, label, action, options = {}) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  appendLog(`${label} 시작`, "info");
  if (options.trackVerificationProgress) {
    workflowState.verificationRequestPending = true;
    workflowState.verificationRequestedTotal = verifyLimit();
    workflowState.verificationRequestedAt = new Date().toISOString();
    renderVerificationActivity();
    renderLogPanel();
    startVerificationProgressPolling();
  }
  try {
    const payload = await action();
    appendLog(`${label} 완료: ${actionSummary(payload)}`, "success");
    showToast(`${label} 완료`);
    if (options.trackVerificationProgress) {
      await refreshVerificationProgress().catch(() => {});
    }
    await loadWorkflowData();
    if (options.scrollTo) {
      document.querySelector(options.scrollTo)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    appendLog(`${label} 실패: ${error.message}`, "error");
    showToast(`${label} 실패: ${error.message}`, true);
  } finally {
    if (options.trackVerificationProgress) {
      workflowState.verificationRequestPending = false;
      if (!workflowState.verificationProgress?.active) stopVerificationProgressPolling();
    }
    button.disabled = false;
    button.textContent = original;
    if (options.trackVerificationProgress) {
      renderVerificationActivity();
      renderLogPanel();
    }
  }
}

async function ensurePlanId() {
  if (workflowState.currentPlanId && currentPlanMatchesPeriod()) {
    return workflowState.currentPlanId;
  }
  const logs = await fetchJson("/ops/logs?limit=20");
  workflowState.logs = logs;
  workflowState.currentPlanId = logs.selected_plan_id;
  if (!workflowState.currentPlanId) throw new Error("먼저 목록 가져오기를 실행해야 합니다.");
  if (!currentPlanMatchesPeriod(logs)) {
    throw new Error("변경한 기간으로 목록 가져오기를 먼저 실행해야 합니다.");
  }
  return workflowState.currentPlanId;
}

function resetPages() {
  workflowState.documentOffset = 0;
  workflowState.candidateOffset = 0;
}

function applyWorkflowMode() {
  const parseStatus = select("#workflow-parse-status");
  if (parseStatus) {
    parseStatus.hidden = workflowState.mode !== "parsing";
  }
}

on("#workflow-fetch-list", "click", (event) => {
  runWorkflowAction(event.currentTarget, "목록 가져오기", async () => {
    const payload = await fetchJson("/ops/collection-plans", {
      method: "POST",
      body: JSON.stringify(periodPayload()),
    });
    workflowState.currentPlanId = payload.plan_id;
    resetPages();
    return payload;
  });
});

on("#workflow-run-collection", "click", (event) => {
  runWorkflowAction(event.currentTarget, "수집", async () => {
    const planId = await ensurePlanId();
    return fetchJson(`/ops/collection-plans/${planId}/run`, {
      method: "POST",
      body: JSON.stringify(batchExecutionPayload()),
    });
  }, { scrollTo: ".workflow-log-panel" });
});

on("#workflow-run-parse", "click", (event) => {
  runWorkflowAction(event.currentTarget, "파싱", async () => {
    const planId = await ensurePlanId();
    return fetchJson(`/ops/collection-plans/${planId}/parse`, {
      method: "POST",
      body: JSON.stringify(batchExecutionPayload()),
    });
  }, { scrollTo: ".workflow-log-panel" });
});

on("#workflow-retry-parse", "click", (event) => {
  runWorkflowAction(event.currentTarget, "실패 재파싱", async () => {
    const planId = await ensurePlanId();
    return fetchJson(`/ops/collection-plans/${planId}/retry-parse-failed`, {
      method: "POST",
      body: JSON.stringify(batchExecutionPayload()),
    });
  }, { scrollTo: ".workflow-log-panel" });
});

on("#workflow-retry-collection", "click", (event) => {
  runWorkflowAction(event.currentTarget, "재수집", async () => {
    const planId = await ensurePlanId();
    return fetchJson(`/ops/collection-plans/${planId}/retry-failed`, {
      method: "POST",
      body: JSON.stringify(batchExecutionPayload()),
    });
  }, { scrollTo: ".workflow-log-panel" });
});

on("#workflow-run-verification", "click", (event) => {
  runWorkflowAction(event.currentTarget, "검증 대기 검증", () => fetchJson("/ops/verify-collected", {
    method: "POST",
    body: JSON.stringify({
      limit: verifyLimit(),
      sort: select("#workflow-candidate-sort")?.value || "verification_oldest",
    }),
  }), { scrollTo: ".workflow-log-panel", trackVerificationProgress: true });
});

on("#workflow-refresh", "click", (event) => {
  runWorkflowAction(event.currentTarget, "새로고침", async () => {
    await loadWorkflowData();
    return { status: "success" };
  });
});

on("#workflow-priority", "change", (event) => {
  workflowState.selectedPriority = event.currentTarget.value;
  workflowState.selectedInstitution = "";
  resetPages();
  renderScopeFilters();
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-institution", "change", (event) => {
  workflowState.selectedInstitution = event.currentTarget.value;
  resetPages();
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-city", "change", () => {
  resetPages();
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-document-status", "change", () => {
  workflowState.documentOffset = 0;
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-parse-status", "change", () => {
  workflowState.documentOffset = 0;
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-candidate-status", "change", () => {
  workflowState.candidateOffset = 0;
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-candidate-sort", "change", () => {
  workflowState.candidateOffset = 0;
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-candidate-search-button", "click", () => {
  workflowState.candidateOffset = 0;
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

on("#workflow-candidate-search", "keydown", (event) => {
  if (event.key === "Enter") {
    workflowState.candidateOffset = 0;
    loadWorkflowData().catch((error) => showToast(error.message, true));
  }
});

on("#workflow-batch-size", "input", updateStepsAndSummary);
on("#workflow-batch-mode", "change", syncBatchControls);
on("#workflow-verify-limit", "input", updateStepsAndSummary);
on("#workflow-start-date", "change", () => {
  workflowState.currentPlanId = null;
  resetPages();
  loadWorkflowData().catch((error) => showToast(error.message, true));
});
on("#workflow-end-date", "change", () => {
  workflowState.currentPlanId = null;
  resetPages();
  loadWorkflowData().catch((error) => showToast(error.message, true));
});

applyWorkflowMode();
syncBatchControls();
loadWorkflowData().catch((error) => {
  appendLog(`초기 조회 실패: ${error.message}`, "error");
  showToast(error.message, true);
});
