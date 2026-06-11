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

function providerCandidateList(item) {
  const candidates = item.provider_candidates || [];
  if (!candidates.length) return "";
  return `
    <div class="provider-candidates">
      <strong>네이버 후보</strong>
      ${candidates.map((candidate, index) => `
        <label class="${candidate.is_approvable ? "" : "disabled"}">
          <input
            type="radio"
            name="provider-${item.review_id}"
            value="${candidate.verification_id}"
            ${index === 0 && candidate.is_approvable ? "checked" : ""}
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
  `;
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
  const payload = await fetchJson("/review?limit=50");
  const summary = document.querySelector("#review-queue-summary");
  const shown = payload.reviews.length;
  const total = payload.total ?? shown;
  summary.textContent = total > shown ? `${shown}/${total}건 표시` : `${total}건`;
  const node = document.querySelector("#review-queue");
  node.innerHTML = payload.reviews.map((item) => `
    <article class="admin-item">
      <strong>${escapeHtml(item.original_place_name)}</strong>
      <dl class="review-meta">
        <div><dt>후보</dt><dd>#${item.candidate_id} · ${escapeHtml(item.reason || "")}</dd></div>
        <div><dt>기관</dt><dd>${escapeHtml(compactText(item.institution_name, item.department_name))}</dd></div>
        <div><dt>문서</dt><dd>${escapeHtml(compactText(item.source_title, item.source_published_at))}</dd></div>
        <div><dt>집행</dt><dd>${escapeHtml(compactText(item.used_date, formatAmount(item.amount), item.payment_method, item.participants))}</dd></div>
        <div><dt>주소</dt><dd>${escapeHtml(item.original_address || "원문 주소 없음")}</dd></div>
      </dl>
      <p class="review-purpose">${escapeHtml(item.purpose || "목적 정보 없음")}</p>
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
      <div class="review-feedback">
        <label>
          <span>반려 사유</span>
          <select data-field="reason">
            <option value="manual_reject">수동 반려</option>
            <option value="LEGAL_ENTITY_INSUFFICIENT_INFO">법인명/정보부족</option>
            <option value="PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME">결제대행/카드명</option>
            <option value="CLOSED_OR_NOT_OPERATING">폐업/운영중단</option>
            <option value="ADDRESS_MISMATCH">주소 불일치</option>
            <option value="AMBIGUOUS_BRANCH">지점 불명확</option>
            <option value="TOO_MANY_SIMILAR_NAMES">동일상호 과다</option>
          </select>
        </label>
        <textarea data-field="reviewer_note" rows="2" placeholder="검토 의견"></textarea>
      </div>
      <div>
        <button data-action="approve" data-id="${item.review_id}">${item.provider_place_name ? "후보 승인" : "신규 승인"}</button>
        <button data-action="reject" data-id="${item.review_id}">반려</button>
      </div>
    </article>
  `).join("") || "<p class=\"empty\">대기 항목이 없습니다.</p>";
  node.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.id;
      const url = button.dataset.action === "approve" ? `/review/${id}/approve-new` : `/review/${id}/reject`;
      const item = button.closest(".admin-item");
      const payload = {
        actor_id: "local-admin",
        reviewer_note: item.querySelector("[data-field='reviewer_note']")?.value || "",
      };
      if (button.dataset.action === "approve") {
        const selected = item.querySelector("input[type='radio'][name^='provider-']:checked");
        if (selected) payload.verification_id = selected.value;
      }
      if (button.dataset.action === "reject") {
        payload.reason = item.querySelector("[data-field='reason']")?.value || "manual_reject";
      }
      await fetchJson(url, { method: "POST", body: JSON.stringify(payload) });
      await loadReviewQueue();
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
  const rowMetric = summary.rows_processed ?? `${summary.rows_inserted || 0}/${summary.rows_seen || 0}`;
  const metrics = [
    ["문서", `${summary.documents_inserted || 0}/${summary.documents_seen || 0}`],
    [summary.rows_processed === undefined ? "행" : "처리", rowMetric],
    ["승인", summary.approved || 0],
    ["수동검토", summary.needs_review || 0],
    ["반려", summary.rejected || 0],
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

async function runBatch(url, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    const payload = await fetchJson(url, { method: "POST", body: "{}" });
    renderBatchResult(payload, original);
    await Promise.all([loadSources(), loadVerificationStatus(), loadReviewQueue(), loadReports()]);
  } catch (error) {
    renderBatchError(error, original);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.querySelector("#run-daily").addEventListener("click", async (event) => {
  await runBatch("/ops/run-daily", event.currentTarget);
});

document.querySelector("#run-live").addEventListener("click", async (event) => {
  await runBatch("/ops/run-busan-live", event.currentTarget);
});

document.querySelector("#verify-pending").addEventListener("click", async (event) => {
  await runBatch("/ops/verify-pending", event.currentTarget);
});

Promise.all([loadSources(), loadVerificationStatus(), loadReviewQueue(), loadReports()]).catch((error) => {
  document.querySelector("#review-queue").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
