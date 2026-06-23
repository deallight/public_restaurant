async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
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
  const text = String(value ?? "").trim();
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function statusLabel(status) {
  return {
    pending: "대기",
    processing: "처리 중",
    collected: "수집",
    duplicate: "기존 문서",
    failed: "실패",
  }[status] || status || "미확인";
}

const state = {
  offset: 0,
  limit: 10,
  total: 0,
  initialized: false,
};

function queryValues() {
  return {
    start_date: document.querySelector("#document-start-date").value,
    end_date: document.querySelector("#document-end-date").value,
    institution: document.querySelector("#document-institution").value,
    status: document.querySelector("#document-status").value,
    sort: document.querySelector("#document-sort").value,
    q: document.querySelector("#document-search").value.trim(),
  };
}

function syncLocation(values) {
  const params = new URLSearchParams(values);
  if (!values.institution) params.delete("institution");
  if (!values.status) params.delete("status");
  if (!values.q) params.delete("q");
  params.set("offset", String(state.offset));
  history.replaceState(null, "", `/admin/documents?${params.toString()}`);
}

function renderInstitutionOptions(items, selected) {
  const select = document.querySelector("#document-institution");
  select.innerHTML = [
    '<option value="">전체 기관</option>',
    ...items.map((item) => (
      `<option value="${escapeHtml(item)}" ${item === selected ? "selected" : ""}>${escapeHtml(item)}</option>`
    )),
  ].join("");
}

function renderSummary(summary) {
  document.querySelector("#document-summary-total").textContent =
    Number(summary.documents || 0).toLocaleString("ko-KR");
  document.querySelector("#document-summary-collected").textContent =
    Number(summary.collected || 0).toLocaleString("ko-KR");
  document.querySelector("#document-summary-failed").textContent =
    Number(summary.failed || 0).toLocaleString("ko-KR");
  document.querySelector("#document-summary-candidates").textContent =
    Number(summary.candidates || 0).toLocaleString("ko-KR");
  document.querySelector("#document-summary-verified").textContent =
    Number(summary.verification_completed || 0).toLocaleString("ko-KR");
}

function renderBoard(payload) {
  const node = document.querySelector("#document-board-list");
  const items = payload.items || [];
  const institution = payload.institution || "";
  document.querySelector("#document-board-title").textContent =
    institution ? `${institution} 수집 문서` : "전체 수집 문서";
  const first = payload.total ? payload.offset + 1 : 0;
  const last = payload.offset + items.length;
  document.querySelector("#document-board-range").textContent =
    `${first}-${last} / ${payload.total}건`;
  if (!items.length) {
    node.innerHTML = '<p class="empty">조건에 맞는 수집 문서가 없습니다.</p>';
    return;
  }
  node.innerHTML = `
    <div class="document-table-wrap">
      <table class="document-table">
        <thead>
          <tr>
            <th>순번</th>
            <th>상태</th>
            <th>수집일</th>
            <th>원문 작성일</th>
            <th>작성 기관</th>
            <th>문서 제목</th>
            <th>집행 내역</th>
            <th>검증 상태</th>
            <th>시도</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((item, index) => {
            const completed = Number(item.verification_completed || 0);
            const candidates = Number(item.candidate_count || 0);
            const detailParams = new URLSearchParams({
              from: window.location.pathname + window.location.search,
            });
            return `
              <tr class="document-row ${item.status === "failed" ? "failed" : ""}">
                <td>${payload.offset + index + 1}</td>
                <td><span class="status-badge ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
                <td>${escapeHtml(item.collected_at || item.updated_at || "-")}</td>
                <td>${escapeHtml(item.published_at || "-")}</td>
                <td>
                  <strong>${escapeHtml(item.institution_label)}</strong>
                  <small title="${escapeHtml(item.department_name || "")}">${escapeHtml(short(item.department_name, 36))}</small>
                </td>
                <td>
                  <a href="/admin/documents/${item.id}?${detailParams.toString()}">${escapeHtml(item.display_title || item.source_title || "제목 없음")}</a>
                  ${item.error_message ? `<small class="document-error" title="${escapeHtml(item.error_message)}">${escapeHtml(short(item.error_message, 70))}</small>` : ""}
                </td>
                <td>${Number(item.rows_seen || 0).toLocaleString("ko-KR")}건</td>
                <td>
                  <span class="document-verification-text">${completed}/${candidates}</span>
                  <span class="mini-progress" aria-label="검증 완료율 ${item.verification_percent}%">
                    <i style="width:${Number(item.verification_percent || 0)}%"></i>
                  </span>
                  <small>승인 ${item.approved_count} · 수동 ${item.review_count} · 반려 ${item.rejected_count}</small>
                </td>
                <td>${Number(item.attempts || 0)}</td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderPagination(payload) {
  state.total = Number(payload.total || 0);
  const page = Math.floor(state.offset / state.limit) + 1;
  const pages = Math.max(1, Math.ceil(state.total / state.limit));
  document.querySelector("#document-page-label").textContent = `${page} / ${pages}`;
  document.querySelector("#document-prev").disabled = !payload.has_prev;
  document.querySelector("#document-next").disabled = !payload.has_next;
}

async function loadDocuments(resetOffset = false) {
  if (resetOffset) state.offset = 0;
  const values = queryValues();
  const params = new URLSearchParams({
    ...values,
    limit: String(state.limit),
    offset: String(state.offset),
  });
  const payload = await fetchJson(`/admin/documents/data?${params.toString()}`);
  const currentInstitution = values.institution || payload.institution || "";
  renderInstitutionOptions(payload.institutions || [], currentInstitution);
  renderSummary(payload.summary || {});
  renderBoard(payload);
  renderPagination(payload);
  syncLocation({ ...values, institution: currentInstitution });
}

function initializeFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const fields = {
    "#document-start-date": "start_date",
    "#document-end-date": "end_date",
    "#document-status": "status",
    "#document-sort": "sort",
    "#document-search": "q",
  };
  Object.entries(fields).forEach(([selector, key]) => {
    if (params.has(key)) document.querySelector(selector).value = params.get(key);
  });
  state.offset = Math.max(0, Number(params.get("offset") || 0));
  return params.get("institution") || "";
}

const initialInstitution = initializeFromLocation();
document.querySelector("#document-institution").innerHTML =
  `<option value="${escapeHtml(initialInstitution)}">${escapeHtml(initialInstitution || "전체 기관")}</option>`;

document.querySelector("#load-documents").addEventListener("click", () => {
  loadDocuments(true).catch(showError);
});
document.querySelector("#refresh-documents").addEventListener("click", () => {
  loadDocuments(false).catch(showError);
});
document.querySelector("#document-prev").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadDocuments(false).catch(showError);
});
document.querySelector("#document-next").addEventListener("click", () => {
  state.offset += state.limit;
  loadDocuments(false).catch(showError);
});
document.querySelector("#document-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadDocuments(true).catch(showError);
});

function showError(error) {
  document.querySelector("#document-board-list").innerHTML =
    `<p class="empty">${escapeHtml(error.message)}</p>`;
}

loadDocuments(false).catch(showError);
