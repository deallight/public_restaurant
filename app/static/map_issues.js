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
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function formatAmount(value) {
  const amount = Number(value || 0);
  return amount ? `${amount.toLocaleString("ko-KR")}원` : "금액 없음";
}

function compactText(...values) {
  return values.filter((value) => String(value || "").trim()).join(" · ");
}

function providerCandidateList(item) {
  const candidates = item.provider_candidates || [];
  if (!candidates.length) return "";
  return `
    <div class="provider-candidates">
      <strong>네이버 후보</strong>
      <div class="provider-candidate-strip">
        <label>
          <input
            type="radio"
            name="provider-${item.candidate_id}"
            value=""
            checked
          >
          <span>
            직접 입력값 사용
            <small>보정 상호명/주소를 네이버 후보로 덮어쓰지 않음</small>
          </span>
        </label>
        ${candidates.map((candidate) => `
          <label class="${candidate.is_approvable ? "" : "disabled"}">
            <input
              type="radio"
              name="provider-${item.candidate_id}"
              value="${candidate.verification_id}"
              data-provider-name="${escapeHtml(candidate.provider_place_name)}"
              data-provider-address="${escapeHtml(candidate.provider_road_address || candidate.provider_address || "")}"
              data-provider-category="${escapeHtml(candidate.provider_category || "other")}"
              ${candidate.is_approvable ? "" : "disabled"}
            >
            <span>
              ${escapeHtml(candidate.provider_place_name)}
              · ${escapeHtml(candidate.provider_category)}
              · 이름 ${(Number(candidate.name_similarity || 0) * 100).toFixed(0)}%
              · 주소 ${(Number(candidate.address_similarity || 0) * 100).toFixed(0)}%
              <small>${escapeHtml(candidate.provider_road_address || candidate.provider_address || "")}</small>
            </span>
          </label>
        `).join("")}
      </div>
    </div>
  `;
}

function candidateEditor(item) {
  const name = item.effective_place_name || item.original_place_name || "";
  const address = item.effective_address || item.original_address || "";
  const category = item.effective_major_category || item.place_major_category || "other";
  return `
    <div class="candidate-db-row candidate-sheet" data-candidate-id="${item.candidate_id}">
      <div class="candidate-db-meta">
        <span>${escapeHtml(item.used_date || "날짜 없음")}</span>
        <span>${escapeHtml(formatAmount(item.amount))}</span>
        <span>${escapeHtml(compactText(item.institution_name, item.department_name))}</span>
        <span>${escapeHtml(item.purpose || "목적 정보 없음")}</span>
        <span>${escapeHtml(compactText(item.source_title, item.source_published_at))}</span>
      </div>
      <label class="readonly cell-id">
        <span>ID</span>
        <input value="${item.candidate_id}" readonly>
      </label>
      <label class="cell-status">
        <span>상태</span>
        <select data-db-field="target_status">
          <option value="needs_review">수동검토</option>
          <option value="verified" selected>승인</option>
          <option value="rejected">반려</option>
        </select>
      </label>
      <label class="wide readonly">
        <span>원문 상호명</span>
        <input value="${escapeHtml(item.original_place_name || "")}" readonly>
      </label>
      <label class="wide">
        <span>보정 상호명</span>
        <input data-db-field="review_place_name" value="${escapeHtml(name)}">
      </label>
      <label class="readonly">
        <span>원문 주소</span>
        <input value="${escapeHtml(item.original_address || "원문 주소 없음")}" readonly>
      </label>
      <label class="wide cell-address">
        <span>보정 주소</span>
        <input data-db-field="review_address" value="${escapeHtml(address)}">
      </label>
      <label class="cell-category">
        <span>보정 분류</span>
        <select data-db-field="review_major_category">
          ${[
            ["restaurant", "음식점"],
            ["cafe", "카페"],
            ["bar", "주점"],
            ["other", "기타"],
          ].map(([value, label]) => `<option value="${value}" ${category === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <label class="cell-reason">
        <span>반려 사유</span>
        <select data-db-field="rejection_reason" disabled>
          <option value="manual_reject">수동 반려</option>
          <option value="ADDRESS_MISMATCH">주소 불일치</option>
          <option value="AMBIGUOUS_BRANCH">지점 불명확</option>
        </select>
      </label>
      <label class="wide cell-note">
        <span>검토 의견</span>
        <input data-db-field="reviewer_note" value="${escapeHtml(item.review_note || "")}" disabled>
      </label>
      <button class="cell-action secondary" type="button" data-action="geocode-candidate" data-id="${item.candidate_id}">지오코딩</button>
      <button class="cell-action" type="button" data-action="update-candidate" data-id="${item.candidate_id}">DB 업데이트</button>
    </div>
  `;
}

function editorPayload(editor) {
  const item = editor.closest(".admin-item");
  const selectedProvider = item?.querySelector(".provider-candidates input[type='radio']:checked");
  return {
    actor_id: "local-admin",
    review_place_name: editor.querySelector("[data-db-field='review_place_name']")?.value || "",
    review_address: editor.querySelector("[data-db-field='review_address']")?.value || "",
    review_major_category: editor.querySelector("[data-db-field='review_major_category']")?.value || "restaurant",
    target_status: editor.querySelector("[data-db-field='target_status']")?.value || "verified",
    rejection_reason: editor.querySelector("[data-db-field='rejection_reason']")?.value || "manual_reject",
    reviewer_note: editor.querySelector("[data-db-field='reviewer_note']")?.value || "",
    selected_verification_id: selectedProvider?.value || "",
  };
}

function applyProvider(input) {
  const item = input.closest(".admin-item");
  const editor = item?.querySelector(".candidate-db-row");
  if (!editor) return;
  editor.querySelector("[data-db-field='review_place_name']").value = input.dataset.providerName || "";
  editor.querySelector("[data-db-field='review_address']").value = input.dataset.providerAddress || "";
  editor.querySelector("[data-db-field='review_major_category']").value = input.dataset.providerCategory || "other";
}

function useDirectInputForEditor(editor) {
  const item = editor.closest(".admin-item");
  const directInput = item?.querySelector(".provider-candidates input[type='radio'][value='']");
  if (directInput) directInput.checked = true;
}

async function loadMapIssues() {
  const payload = await fetchJson("/admin/map-issues/data?limit=200");
  const issues = payload.items || [];
  document.querySelector("#map-issues-summary").textContent = `${payload.total ?? issues.length}건`;
  const node = document.querySelector("#map-issues-list");
  node.innerHTML = issues.map((item) => `
    <article class="admin-item candidate-row">
      <div class="candidate-row-head">
        <strong>${escapeHtml(item.effective_place_name || item.original_place_name)}</strong>
        <small>#${item.candidate_id} · restaurant #${item.restaurant_id || "-"} · ${escapeHtml(item.issue_reason || "")}</small>
      </div>
      <p class="candidate-evidence">
        현재 지도: ${escapeHtml(item.restaurant_name || "없음")}
        · ${escapeHtml(item.restaurant_road_address || item.restaurant_address || "주소 미확인")}
        · ${escapeHtml([item.restaurant_longitude, item.restaurant_latitude].filter(Boolean).join(", ") || "좌표 없음")}
      </p>
      ${providerCandidateList(item)}
      ${candidateEditor(item)}
    </article>
  `).join("") || "<p class=\"empty\">지도 문제 항목이 없습니다.</p>";

  node.querySelectorAll(".provider-candidates input[type='radio']").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked && input.value) applyProvider(input);
    });
  });
  node.querySelectorAll(
    "[data-db-field='review_place_name'], [data-db-field='review_address'], [data-db-field='review_major_category']"
  ).forEach((field) => {
    field.addEventListener("input", () => {
      const editor = field.closest(".candidate-db-row");
      if (editor) useDirectInputForEditor(editor);
    });
    field.addEventListener("change", () => {
      const editor = field.closest(".candidate-db-row");
      if (editor) useDirectInputForEditor(editor);
    });
  });
  node.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = button.closest(".admin-item");
      const editor = item.querySelector(".candidate-db-row");
      const id = button.dataset.id;
      if (button.dataset.action === "geocode-candidate") {
        await fetchJson(`/admin/candidates/${id}/geocode`, {
          method: "POST",
          body: JSON.stringify(editorPayload(editor)),
        });
      }
      if (button.dataset.action === "update-candidate") {
        await fetchJson(`/admin/candidates/${id}`, {
          method: "POST",
          body: JSON.stringify(editorPayload(editor)),
        });
      }
      await loadMapIssues();
    });
  });
}

document.querySelector("#refresh-map-issues").addEventListener("click", loadMapIssues);
loadMapIssues().catch((error) => {
  document.querySelector("#map-issues-list").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
