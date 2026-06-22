async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "요청 실패");
  return payload;
}

const CANDIDATE_PAGE_SIZE = 50;
const candidatePaging = {
  needs_review: 0,
  verified: 0,
  rejected: 0,
};
const candidateOpen = {
  needs_review: true,
  verified: false,
  rejected: false,
};
let candidateSearch = "";
let candidateSort = "id_desc";
let currentCollectionPlanId = null;
let collectionProgressData = {
  by_institution: [],
  by_priority: [],
};

const candidateSortOptions = [
  ["id_desc", "ID 최신순"],
  ["id_asc", "ID 오래된순"],
  ["updated_desc", "최근 수정순"],
  ["used_date_desc", "사용일 최신순"],
  ["amount_desc", "금액 높은순"],
  ["name_asc", "상호명순"],
];

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function statusLabel(status) {
  return {
    crawl_target_ready: "수집 대상 확정",
    adapter_pending: "어댑터 예정",
    unknown: "상태 미확인",
  }[status] || status;
}

function formatAmount(value) {
  const amount = Number(value || 0);
  return amount ? `${amount.toLocaleString("ko-KR")}원` : "금액 없음";
}

function compactText(...values) {
  return values.filter((value) => String(value || "").trim()).join(" · ");
}

function candidateStatusLabel(status) {
  return {
    needs_review: "수동검토",
    verified: "승인",
    rejected: "반려",
  }[status] || status;
}

function providerCandidateList(item) {
  const candidates = item.provider_candidates || [];
  if (!candidates.length) return "";
  const groupId = item.candidate_id;
  return `
    <div class="provider-candidates">
      <strong>네이버 후보</strong>
      <div class="provider-candidate-strip">
        <label>
          <input
            type="radio"
            name="provider-${groupId}"
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
              name="provider-${groupId}"
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

function candidateDbEditor(item) {
  const isVerified = item.candidate_status === "verified";
  const effectiveName = item.effective_place_name || item.original_place_name || "";
  const effectiveAddress = item.effective_address || item.original_address || "";
  const effectiveCategory = item.effective_major_category || item.place_major_category || "other";
  const originalName = item.original_place_name || "";
  const originalAddress = item.original_address || "원문 주소 없음";
  return `
    <div class="candidate-db-row candidate-sheet" data-candidate-id="${item.candidate_id}">
      <div class="candidate-db-meta">
        <span title="${escapeHtml(item.used_date || "")}">${escapeHtml(item.used_date || "날짜 없음")}</span>
        <span title="${escapeHtml(formatAmount(item.amount))}">${escapeHtml(formatAmount(item.amount))}</span>
        <span title="${escapeHtml(compactText(item.institution_name, item.department_name))}">
          ${escapeHtml(compactText(item.institution_name, item.department_name))}
        </span>
        <span title="${escapeHtml(item.purpose || "")}">
          ${escapeHtml(item.purpose || "목적 정보 없음")}
        </span>
        <span title="${escapeHtml(compactText(item.source_title, item.source_published_at))}">
          ${escapeHtml(compactText(item.source_title, item.source_published_at))}
        </span>
      </div>
      <label class="readonly cell-id">
        <span>ID</span>
        <input value="${item.candidate_id}" readonly>
      </label>
      <label class="cell-status">
        <span>상태</span>
        <select data-db-field="target_status">
          ${[
            ["needs_review", "수동검토"],
            ["verified", "승인"],
            ["rejected", "반려"],
          ].map(([value, label]) => `
            <option value="${value}" ${item.candidate_status === value ? "selected" : ""}>${label}</option>
          `).join("")}
        </select>
      </label>
      <label class="wide readonly cell-original-name">
        <span>원문 상호명</span>
        <input value="${escapeHtml(originalName)}" readonly>
      </label>
      <label class="wide">
        <span>보정 상호명</span>
        <input data-db-field="review_place_name" value="${escapeHtml(effectiveName)}" placeholder="${escapeHtml(originalName)}">
      </label>
      <label class="readonly cell-original-address">
        <span>원문 주소</span>
        <input value="${escapeHtml(originalAddress)}" readonly>
      </label>
      <label class="wide cell-address">
        <span>보정 주소</span>
        <input data-db-field="review_address" value="${escapeHtml(effectiveAddress)}" placeholder="${escapeHtml(originalAddress)}">
      </label>
      <label class="cell-category">
        <span>보정 분류</span>
        <select data-db-field="review_major_category">
          ${[
            ["restaurant", "음식점"],
            ["cafe", "카페"],
            ["bar", "주점"],
            ["other", "기타"],
          ].map(([value, label]) => `
            <option value="${value}" ${effectiveCategory === value ? "selected" : ""}>${label}</option>
          `).join("")}
        </select>
      </label>
      <label class="cell-reason">
        <span>반려 사유</span>
        <select data-db-field="rejection_reason" ${isVerified ? "disabled" : ""}>
          ${[
            ["manual_reject", "수동 반려"],
            ["LEGAL_ENTITY_INSUFFICIENT_INFO", "법인명/정보부족"],
            ["PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME", "결제대행/카드명"],
            ["CLOSED_OR_NOT_OPERATING", "폐업/운영중단"],
            ["ADDRESS_MISMATCH", "주소 불일치"],
            ["AMBIGUOUS_BRANCH", "지점 불명확"],
            ["TOO_MANY_SIMILAR_NAMES", "동일상호 과다"],
          ].map(([value, label]) => `
            <option value="${value}" ${(item.rejection_reason || item.review_reason) === value ? "selected" : ""}>${label}</option>
          `).join("")}
        </select>
      </label>
      <label class="wide cell-note">
        <span>검토 의견</span>
        <input data-db-field="reviewer_note" value="${escapeHtml(item.review_note || "")}" placeholder="검토 의견" ${isVerified ? "disabled" : ""}>
      </label>
      <button class="cell-action secondary" type="button" data-action="geocode-candidate" data-id="${item.candidate_id}">지오코딩</button>
      <button class="cell-action" type="button" data-action="update-candidate" data-id="${item.candidate_id}">DB 업데이트</button>
    </div>
  `;
}

function candidateEditorPayload(editor) {
  const item = editor.closest(".admin-item");
  const selectedProvider = item?.querySelector(".provider-candidates input[type='radio']:checked");
  return {
    actor_id: "local-admin",
    review_place_name: editor.querySelector("[data-db-field='review_place_name']")?.value || "",
    review_address: editor.querySelector("[data-db-field='review_address']")?.value || "",
    review_major_category: editor.querySelector("[data-db-field='review_major_category']")?.value || "restaurant",
    target_status: editor.querySelector("[data-db-field='target_status']")?.value || "needs_review",
    rejection_reason: editor.querySelector("[data-db-field='rejection_reason']")?.value || "manual_reject",
    reviewer_note: editor.querySelector("[data-db-field='reviewer_note']")?.value || "",
    selected_verification_id: selectedProvider?.value || "",
  };
}

function applyProviderCandidateToEditor(input) {
  const item = input.closest(".admin-item");
  const editor = item?.querySelector(".candidate-db-row");
  if (!editor) return;
  const name = editor.querySelector("[data-db-field='review_place_name']");
  const address = editor.querySelector("[data-db-field='review_address']");
  const category = editor.querySelector("[data-db-field='review_major_category']");
  if (name) name.value = input.dataset.providerName || "";
  if (address) address.value = input.dataset.providerAddress || "";
  if (category) category.value = input.dataset.providerCategory || "other";
}

function useDirectInputForEditor(editor) {
  const item = editor.closest(".admin-item");
  const directInput = item?.querySelector(".provider-candidates input[type='radio'][value='']");
  if (directInput) directInput.checked = true;
}

function integrationLabel(key) {
  return {
    naver_map_js: "네이버 지도 JS",
    naver_search: "네이버 검색",
    naver_maps_geocoding: "네이버 Geocoding",
    data_go_kr_permit: "공공 인허가",
  }[key] || key;
}

async function loadSources() {
  const payload = await fetchJson("/ops/sources");
  const summary = document.querySelector("#source-summary");
  const groups = document.querySelector("#source-groups");
  summary.textContent = `${payload.summary.active_count}/${payload.summary.source_count}`;
  groups.innerHTML = payload.groups.map((group) => {
    const readyCount = group.sources.filter((item) => item.status === "crawl_target_ready").length;
    const sources = group.sources.slice(0, 8).map((item) => `
      <li>
        <span>${escapeHtml(item.institution_name)}</span>
        <small>${escapeHtml(statusLabel(item.status))}</small>
      </li>
    `).join("");
    const hiddenCount = group.sources.length - 8;
    const more = hiddenCount > 0 ? `<li class="source-more">외 ${hiddenCount}개</li>` : "";
    return `
      <article class="source-group">
        <div>
          <strong>${group.priority}순위 · ${escapeHtml(group.group_label)}</strong>
          <small>${readyCount}/${group.source_count} 준비 · 문서 ${group.documents_collected}건</small>
        </div>
        <ul>${sources}${more}</ul>
      </article>
    `;
  }).join("");
}

async function loadVerificationStatus() {
  const payload = await fetchJson("/ops/verification-status");
  const integrations = payload.integrations || {};
  const missingEnv = payload.missing_env || {};
  const overview = payload.overview || {};
  const counts = overview.counts || {};
  const configuredCount = Object.values(integrations).filter(Boolean).length;
  const integrationCount = Object.keys(integrations).length;
  document.querySelector("#verification-summary").textContent = `${configuredCount}/${integrationCount}`;

  const integrationItems = Object.entries(integrations).map(([key, configured]) => `
    <li>
      <span>${escapeHtml(integrationLabel(key))}</span>
      <strong class="${configured ? "ok" : "warn"}" title="${escapeHtml((missingEnv[key] || []).join(", "))}">
        ${configured ? "설정됨" : `미설정${missingEnv[key]?.length ? ` · ${missingEnv[key].join(", ")}` : ""}`}
      </strong>
    </li>
  `).join("");
  const apiItems = (overview.api_summary || []).map((item) => `
    <li>
      <span>${escapeHtml(item.provider)}</span>
      <strong>${item.success_count || 0}/${item.call_count || 0}</strong>
    </li>
  `).join("") || "<li><span>API 호출</span><strong>0</strong></li>";
  const latestBatch = (overview.latest_batches || [])[0];
  const latestSummary = latestBatch ? latestBatch.summary || {} : {};
  const batchText = latestBatch
    ? `#${latestBatch.id} ${escapeHtml(latestBatch.job_name)} · ${escapeHtml(latestBatch.status)}`
    : "배치 없음";

  document.querySelector("#verification-status").innerHTML = `
    <article>
      <strong>연동 설정</strong>
      <ul>${integrationItems}</ul>
    </article>
    <article>
      <strong>검증 데이터</strong>
      <ul>
        <li><span>음식점</span><strong>${counts.restaurants || 0}</strong></li>
        <li><span>후보</span><strong>${counts.candidates || 0}</strong></li>
        <li><span>수동검토</span><strong>${counts.pending_reviews || 0}</strong></li>
        <li><span>인허가 캐시</span><strong>${counts.permit_snapshots || 0}</strong></li>
      </ul>
    </article>
    <article>
      <strong>API 로그</strong>
      <ul>${apiItems}</ul>
    </article>
    <article>
      <strong>최근 배치</strong>
      <p>${batchText}</p>
      <small>승인 ${latestSummary.approved || 0} · 수동검토 ${latestSummary.needs_review || 0} · 반려 ${latestSummary.rejected || 0} · DLQ ${latestSummary.dlq || 0}</small>
    </article>
  `;
}

async function loadReviewQueue() {
  const params = new URLSearchParams({
    limit: String(CANDIDATE_PAGE_SIZE),
    needs_review_offset: String(candidatePaging.needs_review),
    verified_offset: String(candidatePaging.verified),
    rejected_offset: String(candidatePaging.rejected),
    q: candidateSearch,
    sort: candidateSort,
  });
  const payload = await fetchJson(`/admin/candidates?${params.toString()}`);
  const summary = document.querySelector("#review-queue-summary");
  const groups = payload.groups || {};
  const total = Object.values(groups).reduce((sum, group) => sum + Number(group.total || 0), 0);
  summary.textContent = `${total}건`;
  const node = document.querySelector("#review-queue");
  const groupOrder = ["needs_review", "verified", "rejected"];
  node.innerHTML = `
    <form id="candidate-search-form" class="candidate-search">
      <input id="candidate-search-input" type="search" placeholder="상호명, 주소, 기관, 목적, 문서 검색" value="${escapeHtml(candidateSearch)}">
      <button type="submit">검색</button>
      <button type="button" id="candidate-search-clear" ${candidateSearch ? "" : "disabled"}>초기화</button>
    </form>
    ${groupOrder.map((status) => {
    const group = groups[status] || { label: candidateStatusLabel(status), total: 0, items: [] };
    const items = group.items || [];
    const offset = Number(group.offset || 0);
    const start = group.total ? offset + 1 : 0;
    const end = offset + items.length;
    return `
      <details class="candidate-group" data-status="${status}" ${candidateOpen[status] ? "open" : ""}>
        <summary>
          <strong>${escapeHtml(group.label || candidateStatusLabel(status))}</strong>
          <span class="candidate-summary-tools">
            <span>${start}-${end}/${group.total || items.length}건</span>
            <select class="candidate-sort-select" data-action="candidate-sort" aria-label="후보 정렬">
              ${candidateSortOptions.map(([value, label]) => `
                <option value="${value}" ${candidateSort === value ? "selected" : ""}>${label}</option>
              `).join("")}
            </select>
          </span>
        </summary>
        <div class="candidate-group-body">
          <div class="candidate-pager">
            <button type="button" data-action="candidate-page" data-status="${status}" data-offset="${Math.max(0, offset - CANDIDATE_PAGE_SIZE)}" ${group.has_prev ? "" : "disabled"}>이전</button>
            <span>${Math.floor(offset / CANDIDATE_PAGE_SIZE) + 1}페이지</span>
            <button type="button" data-action="candidate-page" data-status="${status}" data-offset="${offset + CANDIDATE_PAGE_SIZE}" ${group.has_next ? "" : "disabled"}>다음</button>
          </div>
          ${items.map((item) => `
            <article class="admin-item candidate-row">
              <div class="candidate-row-head">
                <strong>${escapeHtml(item.effective_place_name || item.original_place_name)}</strong>
                <small>#${item.candidate_id} · ${escapeHtml(candidateStatusLabel(item.candidate_status))} · ${escapeHtml(item.review_reason || item.rejection_reason || item.review_note || "")}</small>
              </div>
              ${item.provider_place_name ? `
                <p class="candidate-evidence">
                  최신 후보: ${escapeHtml(item.provider_place_name)}
                  · ${escapeHtml(item.provider_category)}
                  · 이름 ${(Number(item.name_similarity || 0) * 100).toFixed(0)}%
                  · 주소 ${(Number(item.address_similarity || 0) * 100).toFixed(0)}%
                  <br>${escapeHtml(item.provider_road_address || item.provider_address || "")}
                </p>
              ` : ""}
              ${providerCandidateList(item)}
              ${candidateDbEditor(item)}
            </article>
          `).join("") || "<p class=\"empty\">항목이 없습니다.</p>"}
        </div>
      </details>
    `;
  }).join("")}`;
  const searchForm = node.querySelector("#candidate-search-form");
  searchForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    candidateSearch = node.querySelector("#candidate-search-input")?.value.trim() || "";
    candidatePaging.needs_review = 0;
    candidatePaging.verified = 0;
    candidatePaging.rejected = 0;
    await loadReviewQueue();
  });
  node.querySelector("#candidate-search-clear")?.addEventListener("click", async () => {
    candidateSearch = "";
    candidatePaging.needs_review = 0;
    candidatePaging.verified = 0;
    candidatePaging.rejected = 0;
    await loadReviewQueue();
  });
  node.querySelectorAll(".candidate-group").forEach((group) => {
    group.addEventListener("toggle", () => {
      candidateOpen[group.dataset.status] = group.open;
    });
  });
  node.querySelectorAll("[data-action='candidate-sort']").forEach((select) => {
    select.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    select.addEventListener("change", async (event) => {
      event.stopPropagation();
      candidateSort = event.currentTarget.value || "id_desc";
      candidatePaging.needs_review = 0;
      candidatePaging.verified = 0;
      candidatePaging.rejected = 0;
      await loadReviewQueue();
    });
  });
  node.querySelectorAll("[data-db-field='target_status']").forEach((select) => {
    const editor = select.closest(".candidate-db-row");
    const syncDecisionFields = () => {
      const disabled = select.value === "verified";
      const reason = editor.querySelector("[data-db-field='rejection_reason']");
      const note = editor.querySelector("[data-db-field='reviewer_note']");
      if (reason) reason.disabled = disabled;
      if (note) note.disabled = disabled;
    };
    select.addEventListener("change", syncDecisionFields);
    syncDecisionFields();
  });
  node.querySelectorAll(".provider-candidates input[type='radio']").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked && input.value) applyProviderCandidateToEditor(input);
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
      if (button.dataset.action === "candidate-page") {
        candidatePaging[button.dataset.status] = Number(button.dataset.offset || 0);
        candidateOpen[button.dataset.status] = true;
        await loadReviewQueue();
        return;
      }
      if (button.dataset.action === "update-candidate") {
        const id = button.dataset.id;
        const editor = item.querySelector(".candidate-db-row");
        await fetchJson(`/admin/candidates/${id}`, {
          method: "POST",
          body: JSON.stringify(candidateEditorPayload(editor)),
        });
        await loadReviewQueue();
        await loadVerificationStatus();
        return;
      }
      if (button.dataset.action === "geocode-candidate") {
        const id = button.dataset.id;
        const editor = item.querySelector(".candidate-db-row");
        await fetchJson(`/admin/candidates/${id}/geocode`, {
          method: "POST",
          body: JSON.stringify(candidateEditorPayload(editor)),
        });
        await loadReviewQueue();
        await loadVerificationStatus();
        return;
      }
    });
  });
}

async function loadReports() {
  const payload = await fetchJson("/admin/review-reports");
  const node = document.querySelector("#review-reports");
  node.innerHTML = payload.reports.map((item) => `
    <article class="admin-item">
      <strong>리뷰 #${item.review_id}</strong>
      <p>${escapeHtml(item.reason)} · ${escapeHtml(item.body)}</p>
      <small>${escapeHtml(item.status)}</small>
    </article>
  `).join("") || "<p class=\"empty\">신고가 없습니다.</p>";
}

function renderBatchResult(payload, label) {
  const node = document.querySelector("#batch-status");
  const summary = payload.summary || {};
  const isVerification = summary.rows_processed !== undefined;
  const metrics = isVerification
    ? [
      ["검증 대기", `${summary.pending_before ?? "-"}→${summary.pending_after ?? "-"}`],
      ["처리", summary.rows_processed || 0],
      ["승인", summary.approved || 0],
      ["수동검토", summary.needs_review || 0],
      ["반려", summary.rejected || 0],
      ["DLQ", summary.dlq || 0],
    ]
    : [
      ["문서", `${summary.documents_inserted || 0}/${summary.documents_seen || 0}`],
      ["신규 행", summary.rows_inserted || 0],
      ["확인 행", summary.rows_seen || 0],
      ["검증 대기 추가", summary.needs_review || 0],
      ["DLQ", summary.dlq || 0],
    ];
  node.innerHTML = `
    <div>
      <strong>${escapeHtml(label)} 완료</strong>
      <small>batch #${payload.batch_id || "-"} · ${escapeHtml(payload.status || "unknown")}</small>
    </div>
    <ul>
      ${metrics.map(([name, value]) => `<li><span>${name}</span><strong>${value}</strong></li>`).join("")}
    </ul>
  `;
}

function renderBatchError(error, label) {
  const node = document.querySelector("#batch-status");
  node.innerHTML = `
    <div>
      <strong>${escapeHtml(label)} 실패</strong>
      <small>${escapeHtml(error.message)}</small>
    </div>
  `;
}

function liveCollectionPayload() {
  return {
    start_date: document.querySelector("#live-start-date")?.value || "",
    end_date: document.querySelector("#live-end-date")?.value || "",
    batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
  };
}

function renderCollectionProgress() {
  const mode = document.querySelector("#collection-progress-mode")?.value || "by_institution";
  const items = collectionProgressData[mode] || [];
  const node = document.querySelector("#collection-progress");
  if (!items.length) {
    node.innerHTML = '<p class="empty">선택한 기간에 수집 계획 문서가 없습니다.</p>';
    return;
  }
  node.innerHTML = items.map((item) => {
    const percent = Math.max(0, Math.min(100, Number(item.percent || 0)));
    return `
      <article class="collection-progress-row">
        <div class="collection-progress-label">
          <strong title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</strong>
          <small>
            처리 ${item.processed || 0}/${item.total || 0}
            · 수집 ${item.collected || 0}
            · 중복 ${item.duplicate || 0}
            · 실패 ${item.failed || 0}
          </small>
        </div>
        <div class="collection-progress-track" aria-label="${escapeHtml(item.label)} ${percent}%">
          <span style="width: ${percent}%"></span>
        </div>
        <strong class="collection-progress-percent">${percent.toFixed(1)}%</strong>
      </article>
    `;
  }).join("");
}

function renderStoredCollectionPlan(plan) {
  if (!plan) return;
  const node = document.querySelector("#collection-plan-status");
  node.innerHTML = `
    <div>
      <strong>${escapeHtml(plan.start_date)} ~ ${escapeHtml(plan.end_date)}</strong>
      <small>plan #${plan.id} · ${escapeHtml(plan.status)}</small>
    </div>
    <ul>
      <li><span>대상 문서</span><strong>${plan.discovered_count || 0}</strong></li>
      <li><span>대기</span><strong>${plan.pending_count || 0}</strong></li>
      <li><span>수집</span><strong>${plan.collected_count || 0}</strong></li>
      <li><span>중복</span><strong>${plan.duplicate_count || 0}</strong></li>
      <li><span>실패</span><strong>${plan.failed_count || 0}</strong></li>
      <li><span>신규 행</span><strong>${plan.rows_inserted || 0}</strong></li>
    </ul>
  `;
}

async function loadCollectionProgress() {
  const startDate = document.querySelector("#progress-start-date")?.value || "";
  const endDate = document.querySelector("#progress-end-date")?.value || "";
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  });
  const [logs, progress] = await Promise.all([
    fetchJson("/ops/logs?limit=1"),
    fetchJson(`/ops/collection-progress?${params.toString()}`),
  ]);
  currentCollectionPlanId = logs.selected_plan_id || currentCollectionPlanId;
  collectionProgressData = progress || {
    by_institution: [],
    by_priority: [],
  };
  const selectedPlan = (logs.plans || []).find(
    (plan) => Number(plan.id) === Number(currentCollectionPlanId)
  );
  renderStoredCollectionPlan(selectedPlan);
  const period = document.querySelector("#collection-progress-period");
  if (period) {
    period.textContent = `${startDate} ~ ${endDate} · 문서 ${progress.document_count || 0}건 · 수집 계획 ${progress.plan_count || 0}개`;
  }
  renderCollectionProgress();
}

function renderCollectionPlanStatus(payload, label) {
  const node = document.querySelector("#collection-plan-status");
  const summary = payload.summary || {};
  if (payload.plan_id) currentCollectionPlanId = payload.plan_id;
  node.innerHTML = `
    <div>
      <strong>${escapeHtml(label)} 완료</strong>
      <small>plan #${payload.plan_id || currentCollectionPlanId || "-"} · batch #${payload.batch_id || "-"}</small>
    </div>
    <ul>
      <li><span>실행 배치</span><strong>${summary.batches_run || (summary.documents_seen ? 1 : 0)}</strong></li>
      <li><span>대기 변화</span><strong>${summary.pending_before !== undefined ? `${summary.pending_before}→${summary.pending_after}` : "-"}</strong></li>
      <li><span>재처리 실패문서</span><strong>${summary.retried_failed || 0}</strong></li>
      <li><span>대상 문서</span><strong>${summary.documents_inserted ?? summary.documents_seen ?? 0}/${summary.documents_seen ?? 0}</strong></li>
      <li><span>배치 크기</span><strong>${summary.batch_size || document.querySelector("#collection-batch-size")?.value || "-"}</strong></li>
      <li><span>신규 문서</span><strong>${summary.documents_inserted || 0}</strong></li>
      <li><span>중복 문서</span><strong>${summary.documents_duplicate || 0}</strong></li>
      <li><span>신규 행</span><strong>${summary.rows_inserted || 0}</strong></li>
      <li><span>검증 대기 추가</span><strong>${summary.needs_review || 0}</strong></li>
      <li><span>DLQ</span><strong>${summary.dlq || 0}</strong></li>
    </ul>
  `;
}

async function runCollectionPlan(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    const result = await fetchJson("/ops/collection-plans", {
      method: "POST",
      body: JSON.stringify(liveCollectionPayload()),
    });
    renderCollectionPlanStatus(result, original);
    await loadCollectionProgress();
  } catch (error) {
    document.querySelector("#collection-plan-status").innerHTML = `
      <div><strong>${escapeHtml(original)} 실패</strong><small>${escapeHtml(error.message)}</small></div>
    `;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function runCollectionPlanBatch(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    if (!currentCollectionPlanId) {
      const logs = await fetchJson("/ops/logs?limit=1");
      currentCollectionPlanId = logs.selected_plan_id;
    }
    if (!currentCollectionPlanId) throw new Error("먼저 수집 대상을 확정해야 합니다.");
    const result = await fetchJson(`/ops/collection-plans/${currentCollectionPlanId}/run`, {
      method: "POST",
      body: JSON.stringify({
        batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
        repeat: true,
        max_batches: 100,
      }),
    });
    renderCollectionPlanStatus(result, original);
    await Promise.all([
      loadSources(),
      loadVerificationStatus(),
      loadReviewQueue(),
      loadReports(),
      loadCollectionProgress(),
    ]);
  } catch (error) {
    document.querySelector("#collection-plan-status").innerHTML = `
      <div><strong>${escapeHtml(original)} 실패</strong><small>${escapeHtml(error.message)}</small></div>
    `;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function retryCollectionPlanFailures(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    if (!currentCollectionPlanId) {
      const logs = await fetchJson("/ops/logs?limit=1");
      currentCollectionPlanId = logs.selected_plan_id;
    }
    if (!currentCollectionPlanId) throw new Error("먼저 수집 대상을 확정해야 합니다.");
    const result = await fetchJson(`/ops/collection-plans/${currentCollectionPlanId}/retry-failed`, {
      method: "POST",
      body: JSON.stringify({
        batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
        max_batches: 100,
      }),
    });
    renderCollectionPlanStatus(result, original);
    await Promise.all([
      loadSources(),
      loadVerificationStatus(),
      loadReviewQueue(),
      loadReports(),
      loadCollectionProgress(),
    ]);
  } catch (error) {
    document.querySelector("#collection-plan-status").innerHTML = `
      <div><strong>${escapeHtml(original)} 실패</strong><small>${escapeHtml(error.message)}</small></div>
    `;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function runBatch(url, button, payload = {}) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    const result = await fetchJson(url, { method: "POST", body: JSON.stringify(payload) });
    renderBatchResult(result, original);
    await Promise.all([loadSources(), loadVerificationStatus(), loadReviewQueue(), loadReports()]);
  } catch (error) {
    renderBatchError(error, original);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.querySelector("#run-daily")?.addEventListener("click", async (event) => {
  await runBatch("/ops/run-daily", event.currentTarget);
});

document.querySelector("#collection-progress-mode")?.addEventListener("change", renderCollectionProgress);
document.querySelector("#load-collection-progress")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "조회 중";
  try {
    await loadCollectionProgress();
  } catch (error) {
    document.querySelector("#collection-progress").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

document.querySelector("#create-collection-plan")?.addEventListener("click", async (event) => {
  await runCollectionPlan(event.currentTarget);
});

document.querySelector("#run-plan-batch")?.addEventListener("click", async (event) => {
  await runCollectionPlanBatch(event.currentTarget);
});

document.querySelector("#retry-plan-failed")?.addEventListener("click", async (event) => {
  await retryCollectionPlanFailures(event.currentTarget);
});

document.querySelector("#run-live")?.addEventListener("click", async (event) => {
  await runBatch("/ops/run-busan-live", event.currentTarget, liveCollectionPayload());
});

document.querySelector("#verify-collected")?.addEventListener("click", async (event) => {
  await runBatch("/ops/verify-collected", event.currentTarget);
});

document.querySelector("#verify-pending")?.addEventListener("click", async (event) => {
  await runBatch("/ops/verify-pending", event.currentTarget);
});

const initialLoads = [];
if (document.querySelector("#source-groups")) initialLoads.push(loadSources());
if (document.querySelector("#verification-status")) initialLoads.push(loadVerificationStatus());
if (document.querySelector("#review-queue")) initialLoads.push(loadReviewQueue());
if (document.querySelector("#review-reports")) initialLoads.push(loadReports());
if (document.querySelector("#collection-progress")) initialLoads.push(loadCollectionProgress());
Promise.all(initialLoads).catch((error) => {
  const node = document.querySelector("#review-queue");
  if (node) node.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
