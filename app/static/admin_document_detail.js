async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", Accept: "application/json" },
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

function formatAmount(value) {
  return Number(value || 0).toLocaleString("ko-KR");
}

function statusLabel(status) {
  return {
    pending: "대기",
    processing: "처리 중",
    collected: "수집",
    duplicate: "기존 문서",
    failed: "실패",
    needs_review: "수동검토",
    verified: "승인",
    rejected: "반려",
  }[status] || status || "미확인";
}

const documentId = Number(window.location.pathname.split("/").filter(Boolean).pop());
const state = {
  offset: 0,
  limit: 25,
  total: 0,
  dirty: new Set(),
};

function rowQuery() {
  return {
    q: document.querySelector("#document-row-search").value.trim(),
    status: document.querySelector("#document-row-status").value,
    sort: document.querySelector("#document-row-sort").value,
  };
}

function renderDocument(documentData) {
  document.querySelector("#document-detail-title").textContent =
    documentData.display_title || documentData.source_title || "제목 없는 수집 문서";
  document.querySelector("#document-detail-subtitle").textContent =
    `${documentData.institution_label || "기관 미상"} · ${documentData.department_name || "부서 미상"}`;
  const badge = document.querySelector("#document-detail-status");
  badge.textContent = statusLabel(documentData.status);
  badge.className = `status-badge ${documentData.status || ""}`;
  const sourceLink = document.querySelector("#document-source-link");
  sourceLink.href = documentData.source_url || "#";
  sourceLink.hidden = !documentData.source_url;
  const from = new URLSearchParams(window.location.search).get("from");
  if (from && from.startsWith("/admin/documents")) {
    document.querySelector("#document-back-link").href = from;
  }
  const verification = documentData.verification || {};
  document.querySelector("#document-detail-summary").innerHTML = `
    <div><span>원문 작성일</span><strong>${escapeHtml(documentData.published_at || "-")}</strong></div>
    <div><span>수집일</span><strong>${escapeHtml(documentData.collected_at || documentData.updated_at || "-")}</strong></div>
    <div><span>확인 행</span><strong>${Number(documentData.rows_seen || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>신규 행</span><strong>${Number(documentData.rows_inserted || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>승인</span><strong>${Number(verification.approved || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>수동검토</span><strong>${Number(verification.needs_review || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>반려</span><strong>${Number(verification.rejected || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>검증 전</span><strong>${Number(verification.pending || 0).toLocaleString("ko-KR")}</strong></div>
    <div><span>수집 시도</span><strong>${Number(documentData.attempts || 0)}</strong></div>
    <div class="wide"><span>오류</span><strong>${escapeHtml(documentData.error_message || documentData.raw_error_message || "없음")}</strong></div>
  `;
}

function statusOptions(selected) {
  return [
    ["needs_review", "수동검토"],
    ["verified", "승인"],
    ["rejected", "반려"],
  ].map(([value, label]) => (
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
  )).join("");
}

function categoryOptions(selected) {
  return [
    ["restaurant", "음식점"],
    ["cafe", "카페"],
    ["bar", "주점"],
    ["other", "기타"],
  ].map(([value, label]) => (
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
  )).join("");
}

function rejectionOptions(selected) {
  return [
    ["manual_reject", "수동 반려"],
    ["LEGAL_ENTITY_INSUFFICIENT_INFO", "법인명/정보부족"],
    ["PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME", "결제대행/카드명"],
    ["CLOSED_OR_NOT_OPERATING", "폐업/운영중단"],
    ["ADDRESS_MISMATCH", "주소 불일치"],
    ["AMBIGUOUS_BRANCH", "지점 불명확"],
    ["TOO_MANY_SIMILAR_NAMES", "동일상호 과다"],
  ].map(([value, label]) => (
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
  )).join("");
}

function renderRows(payload) {
  const node = document.querySelector("#document-row-list");
  const items = payload.items || [];
  if (!items.length) {
    node.innerHTML = '<p class="empty">표시할 집행 내역이 없습니다.</p>';
    return;
  }
  node.innerHTML = `
    <div class="document-edit-table-wrap">
      <table class="document-edit-table">
        <thead>
          <tr>
            <th><input id="select-all-document-rows" type="checkbox" aria-label="현재 페이지 전체 선택"></th>
            <th>순번</th>
            <th>ID</th>
            <th>상태</th>
            <th>원문 상호명</th>
            <th>보정 상호명</th>
            <th>원문 주소</th>
            <th>보정 주소</th>
            <th>분류</th>
            <th>사용일</th>
            <th>금액</th>
            <th>목적</th>
            <th>반려 사유</th>
            <th>검토 의견</th>
            <th>도구</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((item, index) => {
            const effectiveName = item.effective_place_name || item.original_place_name || "";
            const effectiveAddress = item.effective_address || item.original_address || "";
            const effectiveCategory = item.effective_major_category || item.place_major_category || "other";
            const rowNumber = item.source_row_number || payload.offset + index + 1;
            return `
              <tr data-candidate-id="${item.candidate_id}">
                <td><input data-row-select type="checkbox" aria-label="${item.candidate_id} 선택"></td>
                <td>${rowNumber}</td>
                <td>${item.candidate_id}</td>
                <td>
                  <select data-field="target_status">${statusOptions(item.candidate_status)}</select>
                  <small>${escapeHtml(item.verification_status || "")}</small>
                </td>
                <td class="readonly-cell" title="${escapeHtml(item.original_place_name || "")}">${escapeHtml(item.original_place_name || "-")}</td>
                <td><input data-field="review_place_name" value="${escapeHtml(effectiveName)}"></td>
                <td class="readonly-cell" title="${escapeHtml(item.original_address || "")}">${escapeHtml(item.original_address || "-")}</td>
                <td><input data-field="review_address" value="${escapeHtml(effectiveAddress)}"></td>
                <td><select data-field="review_major_category">${categoryOptions(effectiveCategory)}</select></td>
                <td>${escapeHtml(item.used_date || "-")}</td>
                <td class="number-cell">${formatAmount(item.amount)}원</td>
                <td title="${escapeHtml(item.purpose || "")}">${escapeHtml(item.purpose || "-")}</td>
                <td><select data-field="rejection_reason">${rejectionOptions(item.rejection_reason || "")}</select></td>
                <td><input data-field="reviewer_note" value="${escapeHtml(item.review_note || "")}" placeholder="검토 의견"></td>
                <td>
                  <button class="icon-text-button secondary" data-action="geocode" type="button">지오코딩</button>
                </td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
  node.querySelectorAll("input[data-field], select[data-field]").forEach((control) => {
    control.addEventListener("change", () => markDirty(control.closest("tr")));
    if (control.tagName === "INPUT") {
      control.addEventListener("input", () => markDirty(control.closest("tr")));
    }
  });
  node.querySelectorAll("[data-action='geocode']").forEach((button) => {
    button.addEventListener("click", () => geocodeRow(button.closest("tr"), button));
  });
  document.querySelector("#select-all-document-rows").addEventListener("change", (event) => {
    node.querySelectorAll("[data-row-select]").forEach((checkbox) => {
      checkbox.checked = event.currentTarget.checked;
    });
  });
}

function markDirty(row) {
  if (!row) return;
  state.dirty.add(Number(row.dataset.candidateId));
  row.classList.add("dirty");
  document.querySelector("#document-dirty-count").textContent = `변경 ${state.dirty.size}건`;
}

function rowPayload(row) {
  return {
    actor_id: "local-admin",
    review_place_name: row.querySelector("[data-field='review_place_name']").value,
    review_address: row.querySelector("[data-field='review_address']").value,
    review_major_category: row.querySelector("[data-field='review_major_category']").value,
    target_status: row.querySelector("[data-field='target_status']").value,
    rejection_reason: row.querySelector("[data-field='rejection_reason']").value,
    reviewer_note: row.querySelector("[data-field='reviewer_note']").value,
  };
}

async function geocodeRow(row, button) {
  const candidateId = Number(row.dataset.candidateId);
  const payload = rowPayload(row);
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "처리 중";
  try {
    const result = await fetchJson(`/admin/candidates/${candidateId}/geocode`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage(
      result.geocoding?.status === "success"
        ? `#${candidateId} 지오코딩 완료: ${result.geocoding.road_address || result.geocoding.address || ""}`
        : `#${candidateId} 지오코딩 결과: ${result.geocoding?.reason || "확인 필요"}`,
      result.geocoding?.status !== "success",
    );
    markDirty(row);
  } catch (error) {
    showMessage(`#${candidateId} 지오코딩 실패: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function saveDirtyRows() {
  const rows = [...document.querySelectorAll("#document-row-list tr[data-candidate-id]")]
    .filter((row) => state.dirty.has(Number(row.dataset.candidateId)));
  if (!rows.length) {
    showMessage("변경된 행이 없습니다.", false);
    return;
  }
  const button = document.querySelector("#save-document-rows");
  button.disabled = true;
  button.textContent = "저장 중";
  let completed = 0;
  const failures = [];
  for (const row of rows) {
    const candidateId = Number(row.dataset.candidateId);
    try {
      await fetchJson(`/admin/candidates/${candidateId}`, {
        method: "POST",
        body: JSON.stringify(rowPayload(row)),
      });
      completed += 1;
      state.dirty.delete(candidateId);
      row.classList.remove("dirty");
    } catch (error) {
      failures.push(`#${candidateId} ${error.message}`);
    }
  }
  document.querySelector("#document-dirty-count").textContent = `변경 ${state.dirty.size}건`;
  showMessage(
    failures.length
      ? `${completed}건 저장, ${failures.length}건 실패: ${failures.join(" / ")}`
      : `${completed}건을 DB에 저장했습니다.`,
    failures.length > 0,
  );
  button.disabled = false;
  button.textContent = "DB 업데이트";
  if (!failures.length) await loadDocument(false);
}

function applyBulkStatus() {
  const value = document.querySelector("#document-bulk-status").value;
  if (!value) return;
  const selected = document.querySelectorAll("#document-row-list [data-row-select]:checked");
  selected.forEach((checkbox) => {
    const row = checkbox.closest("tr");
    row.querySelector("[data-field='target_status']").value = value;
    markDirty(row);
  });
}

function renderPagination(payload) {
  state.total = Number(payload.total || 0);
  const page = Math.floor(state.offset / state.limit) + 1;
  const pages = Math.max(1, Math.ceil(state.total / state.limit));
  document.querySelector("#document-row-page-label").textContent = `${page} / ${pages}`;
  document.querySelector("#document-row-prev").disabled = !payload.has_prev;
  document.querySelector("#document-row-next").disabled = !payload.has_next;
}

async function loadDocument(resetOffset = false) {
  if (resetOffset) state.offset = 0;
  const params = new URLSearchParams({
    ...rowQuery(),
    limit: String(state.limit),
    offset: String(state.offset),
  });
  const payload = await fetchJson(`/admin/documents/${documentId}/data?${params.toString()}`);
  renderDocument(payload.document);
  renderRows(payload);
  renderPagination(payload);
  state.dirty.clear();
  document.querySelector("#document-dirty-count").textContent = "변경 0건";
}

function showMessage(message, isError) {
  const node = document.querySelector("#document-save-message");
  node.hidden = false;
  node.className = `document-save-message ${isError ? "error" : "success"}`;
  node.textContent = message;
}

document.querySelector("#load-document-rows").addEventListener("click", () => {
  loadDocument(true).catch((error) => showMessage(error.message, true));
});
document.querySelector("#apply-document-bulk-status").addEventListener("click", applyBulkStatus);
document.querySelector("#save-document-rows").addEventListener("click", () => {
  saveDirtyRows().catch((error) => showMessage(error.message, true));
});
document.querySelector("#document-row-prev").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadDocument(false).catch((error) => showMessage(error.message, true));
});
document.querySelector("#document-row-next").addEventListener("click", () => {
  state.offset += state.limit;
  loadDocument(false).catch((error) => showMessage(error.message, true));
});
document.querySelector("#document-row-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadDocument(true).catch((error) => showMessage(error.message, true));
});

loadDocument(false).catch((error) => showMessage(error.message, true));
