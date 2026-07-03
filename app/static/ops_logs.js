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

function short(value, max = 80) {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function summaryText(summary) {
  const pairs = Object.entries(summary || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== "" && typeof value !== "object")
    .slice(0, 8);
  return pairs.map(([key, value]) => `${key}: ${value}`).join(" · ");
}

function metric(row, key, fallback = "") {
  const value = row.summary?.[key];
  return escapeHtml(value ?? fallback);
}

function pendingMetric(row) {
  const before = row.summary?.pending_before;
  const after = row.summary?.pending_after;
  return before === undefined && after === undefined ? "" : `${escapeHtml(before ?? "-")}→${escapeHtml(after ?? "-")}`;
}

function renderTable(nodeId, columns, rows, emptyText) {
  const node = document.querySelector(nodeId);
  if (!rows.length) {
    node.innerHTML = `<p class="empty">${escapeHtml(emptyText)}</p>`;
    return;
  }
  node.innerHTML = `
    <div class="ops-table-wrap">
      <table class="ops-table">
        <thead>
          <tr>${columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              ${columns.map((column) => `<td>${column.render ? column.render(row) : escapeHtml(row[column.key])}</td>`).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderPlanSelector(plans, selectedPlanId) {
  const select = document.querySelector("#ops-log-plan");
  select.innerHTML = plans.map((plan) => `
    <option value="${plan.id}" ${Number(plan.id) === Number(selectedPlanId) ? "selected" : ""}>
      #${plan.id} · ${escapeHtml(plan.start_date)}~${escapeHtml(plan.end_date)} · ${escapeHtml(plan.status)}
    </option>
  `).join("") || "<option value=\"\">수집 계획 없음</option>";
}

async function loadOpsLogs() {
  const select = document.querySelector("#ops-log-plan");
  const limit = Number(document.querySelector("#ops-log-limit")?.value || 100);
  const selected = select?.value || "";
  const params = new URLSearchParams({ limit: String(limit) });
  if (selected) params.set("plan_id", selected);
  const payload = await fetchJson(`/ops/logs?${params.toString()}`);
  renderPlanSelector(payload.plans || [], payload.selected_plan_id);
  const institutionFilter = new URLSearchParams(window.location.search).get("institution") || "";
  const documents = institutionFilter
    ? (payload.documents || []).filter((row) =>
      `${row.department_name || ""} ${row.source_title || ""}`.includes(institutionFilter)
    )
    : (payload.documents || []);
  const documentHeading = document.querySelector("#ops-documents-title");
  if (documentHeading) {
    documentHeading.textContent = institutionFilter
      ? `문서별 수집 상태 · ${institutionFilter}`
      : "문서별 수집 상태";
  }

  renderTable("#ops-log-plans", [
    { key: "id", label: "ID" },
    { key: "source_key", label: "소스" },
    { key: "start_date", label: "시작일" },
    { key: "end_date", label: "종료일" },
    { key: "status", label: "상태" },
    { key: "discovered_count", label: "대상" },
    { key: "pending_count", label: "대기" },
    { key: "collected_count", label: "수집" },
    { key: "duplicate_count", label: "중복" },
    { key: "failed_count", label: "실패" },
    { key: "rows_inserted", label: "신규 행" },
    { key: "created_at", label: "생성" },
    { key: "updated_at", label: "수정" },
  ], payload.plans || [], "수집 계획이 없습니다.");

  renderTable("#ops-log-documents", [
    { key: "id", label: "ID" },
    { key: "status", label: "상태" },
    { key: "published_at", label: "공표일" },
    { key: "department_name", label: "부서", render: (row) => escapeHtml(short(row.department_name, 32)) },
    { key: "source_title", label: "제목", render: (row) => `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(short(row.source_title, 70))}</a>` },
    { key: "rows_seen", label: "확인 행" },
    { key: "rows_inserted", label: "신규 행" },
    { key: "attempts", label: "수집 시도" },
    { key: "parse_status", label: "파싱" },
    { key: "parse_attempts", label: "파싱 시도" },
    { key: "batch_job_id", label: "배치" },
    { key: "failure_type", label: "실패 유형", render: (row) => escapeHtml(row.failure_type || "") },
    { key: "error_message", label: "수집 오류", render: (row) => escapeHtml(short(row.error_message, 80)) },
    { key: "parse_error_message", label: "파싱 오류", render: (row) => escapeHtml(short(row.parse_error_message, 80)) },
    { key: "updated_at", label: "수정" },
  ], documents, institutionFilter ? "선택한 기관의 문서가 없습니다." : "선택된 계획의 문서가 없습니다.");

  renderTable("#ops-log-batches", [
    { key: "id", label: "ID" },
    { key: "job_name", label: "작업" },
    { key: "status", label: "상태" },
    { key: "plan_id", label: "계획", render: (row) => metric(row, "plan_id") },
    { key: "batches_run", label: "실행배치", render: (row) => metric(row, "batches_run") },
    { key: "batch_size", label: "크기", render: (row) => metric(row, "batch_size") },
    { key: "documents_seen", label: "문서", render: (row) => metric(row, "documents_seen") },
    { key: "documents_inserted", label: "신규문서", render: (row) => metric(row, "documents_inserted") },
    { key: "documents_duplicate", label: "중복문서", render: (row) => metric(row, "documents_duplicate") },
    { key: "rows_seen", label: "확인행", render: (row) => metric(row, row.summary?.rows_processed !== undefined ? "rows_processed" : "rows_seen") },
    { key: "rows_inserted", label: "신규행", render: (row) => metric(row, "rows_inserted") },
    { key: "approved", label: "승인", render: (row) => metric(row, "approved") },
    { key: "needs_review", label: "수동", render: (row) => metric(row, "needs_review") },
    { key: "rejected", label: "반려", render: (row) => metric(row, "rejected") },
    { key: "dlq", label: "DLQ", render: (row) => metric(row, "dlq") },
    { key: "permit_advisory_checked", label: "인허가 확인", render: (row) => metric(row, "permit_advisory_checked") },
    { key: "permit_advisory_unavailable", label: "인허가 실패", render: (row) => metric(row, "permit_advisory_unavailable") },
    { key: "pending", label: "대기", render: pendingMetric },
    { key: "started_at", label: "시작" },
    { key: "finished_at", label: "종료" },
    { key: "summary", label: "기타", render: (row) => escapeHtml(short(summaryText(row.summary), 80)) },
    { key: "error_message", label: "오류", render: (row) => escapeHtml(short(row.error_message, 80)) },
  ], payload.batches || [], "배치 로그가 없습니다.");

  renderTable("#ops-log-api", [
    { key: "provider", label: "Provider" },
    { key: "endpoint", label: "Endpoint", render: (row) => escapeHtml(short(row.endpoint, 50)) },
    { key: "status_code", label: "HTTP" },
    { key: "duration_ms", label: "ms" },
    { key: "success", label: "성공", render: (row) => row.success ? "성공" : "실패" },
    { key: "error_message", label: "오류", render: (row) => escapeHtml(short(row.error_message, 80)) },
    { key: "called_at", label: "호출" },
  ], payload.api_calls || [], "API 호출 로그가 없습니다.");

  renderTable("#ops-log-dlq", [
    { key: "id", label: "ID" },
    { key: "batch_job_id", label: "배치" },
    { key: "stage", label: "단계" },
    { key: "status", label: "상태" },
    { key: "retry_count", label: "재시도" },
    { key: "error_message", label: "오류", render: (row) => escapeHtml(short(row.error_message, 100)) },
    { key: "created_at", label: "생성" },
    { key: "resolved_at", label: "해결" },
  ], payload.dlq || [], "DLQ가 없습니다.");
}

document.querySelector("#refresh-ops-logs").addEventListener("click", () => {
  loadOpsLogs().catch((error) => {
    document.querySelector("#ops-log-plans").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

document.querySelector("#ops-log-plan").addEventListener("change", () => {
  loadOpsLogs().catch((error) => {
    document.querySelector("#ops-log-documents").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

document.querySelector("#ops-log-limit").addEventListener("change", () => {
  loadOpsLogs().catch((error) => {
    document.querySelector("#ops-log-plans").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

loadOpsLogs().catch((error) => {
  document.querySelector("#ops-log-plans").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
