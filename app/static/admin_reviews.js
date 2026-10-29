const reviewAdminState = { view: "open_reports", page: 1, total: 0, selectedId: null, listRequest: 0 };

function reviewEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function reviewStatusLabel(status) {
  return { visible: "공개", hidden: "비공개", deleted: "삭제" }[status] || status;
}

function reportStatusLabel(status) {
  return { open: "대기", resolved: "처리 완료", dismissed: "기각" }[status] || status;
}

function reviewDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? reviewEscape(value) : reviewEscape(date.toLocaleString("ko-KR"));
}

function reviewMessage(message, error = false) {
  const node = document.querySelector("#review-admin-message");
  node.textContent = message;
  node.classList.toggle("error", error);
}

async function reviewFetch(url, options = {}) {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || "요청을 처리하지 못했습니다.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

// Review list and pagination
async function loadAdminReviews() {
  const request = ++reviewAdminState.listRequest;
  const params = new URLSearchParams({
    view: reviewAdminState.view,
    q: document.querySelector("#review-query").value.trim(),
    status: document.querySelector("#review-status").value,
    page: String(reviewAdminState.page),
  });
  const list = document.querySelector("#review-admin-list");
  list.innerHTML = '<p class="review-admin-empty">후기를 불러오는 중입니다.</p>';
  try {
    const payload = await reviewFetch(`/admin/reviews/data?${params}`);
    if (request !== reviewAdminState.listRequest) return;
    reviewAdminState.total = Number(payload.total || 0);
    const pages = Math.max(1, Math.ceil(reviewAdminState.total / 20));
    if (reviewAdminState.page > pages) {
      reviewAdminState.page = pages;
      await loadAdminReviews();
      return;
    }
    const items = payload.items || [];
    document.querySelector("#review-open-count").textContent = Number(payload.counts?.open_reports || 0).toLocaleString("ko-KR");
    document.querySelector("#review-all-count").textContent = Number(payload.counts?.total || 0).toLocaleString("ko-KR");
    document.querySelector("#review-list-summary").textContent = `검색 결과 ${reviewAdminState.total.toLocaleString("ko-KR")}건 · 최신순`;
    list.innerHTML = items.map((item) => `
      <button type="button" class="review-admin-row" data-id="${Number(item.id)}">
        <span class="review-admin-row-title"><strong>${reviewEscape(item.restaurant_name)}</strong>
          <span>${reviewEscape(reviewStatusLabel(item.status))}</span></span>
        <span class="review-admin-row-meta">별점 ${Number(item.rating)} · ${reviewEscape(item.reviewer_label)} · ${reviewDate(item.created_at)}</span>
        <span class="review-admin-row-body">${reviewEscape(item.body_excerpt || (item.status === "deleted" ? "작성자가 삭제한 후기" : ""))}</span>
        <span class="review-admin-row-report">미처리 신고 ${Number(item.open_report_count || 0)}건</span>
      </button>
    `).join("") || '<p class="review-admin-empty">조건에 맞는 후기가 없습니다.</p>';
    document.querySelector("#review-page-label").textContent = `${reviewAdminState.page} / ${pages}`;
    document.querySelector("#review-page-prev").disabled = reviewAdminState.page <= 1;
    document.querySelector("#review-page-next").disabled = reviewAdminState.page >= pages;
  } catch (error) {
    if (request !== reviewAdminState.listRequest) return;
    list.innerHTML = `<p class="review-admin-empty error">${reviewEscape(error.message)}</p>`;
    reviewMessage(error.message, true);
  }
}

// Review detail and moderation history
async function loadAdminReviewDetail(id) {
  reviewAdminState.selectedId = id;
  const panel = document.querySelector("#review-admin-detail");
  panel.hidden = false;
  panel.innerHTML = '<p class="review-admin-empty">후기 상세를 불러오는 중입니다.</p>';
  try {
    const payload = await reviewFetch(`/admin/reviews/${id}`);
    if (reviewAdminState.selectedId !== id) return;
    const review = payload.review;
    const reports = payload.reports || [];
    const openReports = reports.filter((item) => item.status === "open").length;
    const logs = payload.moderation_logs || [];
    const buttons = review.status === "deleted" ? "" : `
      ${review.status === "visible" ? '<button type="button" data-action="hide">후기 숨김</button>' : ""}
      ${review.status === "hidden" ? '<button type="button" data-action="restore">후기 복원</button>' : ""}
      ${openReports ? '<button type="button" data-action="dismiss_reports">신고 기각</button>' : ""}`;
    panel.innerHTML = `
      <header class="review-admin-detail-head">
        <div><span class="dashboard-eyebrow">후기 #${Number(review.id)}</span>
          <h2>${reviewEscape(review.restaurant_name)}</h2>
          <p>별점 ${Number(review.rating)} · ${reviewEscape(review.reviewer_label)} · ${reviewDate(review.created_at)} · ${reviewEscape(reviewStatusLabel(review.status))}</p></div>
        <button type="button" data-action="close">닫기</button>
      </header>
      <p class="review-admin-full-body">${review.status === "deleted" ? "작성자가 삭제한 후기입니다." : reviewEscape(review.body)}</p>
      <section><h3>신고 내역 (${reports.length})</h3>
        <div class="review-admin-history">${reports.map((item) => `
          <article><strong>#${Number(item.id)} · ${reviewEscape(reportStatusLabel(item.status))}</strong>
            <span>${reviewEscape(item.reason === "spam_or_abuse" ? "스팸·악용" : item.reason)} · ${reviewDate(item.created_at)}</span></article>
        `).join("") || "<p>신고가 없습니다.</p>"}</div></section>
      <section><h3>관리 이력 (${logs.length})</h3>
        <div class="review-admin-history">${logs.map((item) => `
          <article><strong>${reviewEscape({ hide: "숨김", restore: "복원", dismiss_reports: "신고 기각" }[item.action] || item.action)}</strong>
            <span>${reviewDate(item.created_at)} · 관리자 #${reviewEscape(item.moderator_id)}</span>
            <p>${reviewEscape(item.before_status || "-")} → ${reviewEscape(item.after_status || "-")} · ${reviewEscape(item.reason || "")}</p></article>
        `).join("") || "<p>관리 이력이 없습니다.</p>"}</div></section>
      ${buttons ? `<section class="review-admin-actions"><h3>관리 조치</h3>
        <label>처리 사유 <textarea id="review-action-reason" maxlength="500" required placeholder="처리 사유를 입력하세요"></textarea></label>
        <div>${buttons}</div></section>` : ""}
    `;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (reviewAdminState.selectedId !== id) return;
    panel.innerHTML = `<p class="review-admin-empty error">${reviewEscape(error.message)}</p>`;
    reviewMessage(error.message, true);
  }
}

// Moderator action
async function moderateAdminReview(action) {
  const reviewId = reviewAdminState.selectedId;
  const reason = document.querySelector("#review-action-reason")?.value.trim() || "";
  if (!reason) {
    reviewMessage("처리 사유를 입력해 주세요.", true);
    return;
  }
  const label = { hide: "숨김", restore: "복원", dismiss_reports: "신고 기각" }[action];
  if (!window.confirm(`${label} 처리하시겠습니까?`)) return;
  const buttons = document.querySelectorAll(".review-admin-actions button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await reviewFetch(`/admin/reviews/${reviewId}/moderate`, {
      method: "POST", body: JSON.stringify({ action, reason }),
    });
    reviewMessage(`${label} 처리했습니다.`);
    await loadAdminReviews();
    if (reviewAdminState.selectedId === reviewId) await loadAdminReviewDetail(reviewId);
  } catch (error) {
    reviewMessage(error.status === 409 ? "상태가 변경됐습니다. 목록을 다시 확인해 주세요." : error.message, true);
    buttons.forEach((button) => { button.disabled = false; });
    if (error.status === 409) {
      await loadAdminReviews();
      if (reviewAdminState.selectedId === reviewId) await loadAdminReviewDetail(reviewId);
    }
  }
}

document.querySelectorAll(".review-admin-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    reviewAdminState.view = button.dataset.view;
    reviewAdminState.page = 1;
    document.querySelectorAll(".review-admin-tabs button").forEach((tab) => {
      tab.setAttribute("aria-pressed", String(tab === button));
    });
    document.querySelector("#review-admin-detail").hidden = true;
    reviewAdminState.selectedId = null;
    loadAdminReviews();
  });
});
document.querySelector("#review-admin-search").addEventListener("submit", (event) => {
  event.preventDefault();
  reviewAdminState.page = 1;
  loadAdminReviews();
});
document.querySelector("#review-status").addEventListener("change", () => {
  reviewAdminState.page = 1;
  loadAdminReviews();
});
document.querySelector("#review-page-prev").addEventListener("click", () => {
  if (reviewAdminState.page > 1) reviewAdminState.page -= 1;
  loadAdminReviews();
});
document.querySelector("#review-page-next").addEventListener("click", () => {
  if (reviewAdminState.page * 20 < reviewAdminState.total) reviewAdminState.page += 1;
  loadAdminReviews();
});
document.querySelector("#review-admin-list").addEventListener("click", (event) => {
  const row = event.target.closest("[data-id]");
  if (row) loadAdminReviewDetail(Number(row.dataset.id));
});
document.querySelector("#review-admin-detail").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "close") {
    reviewAdminState.selectedId = null;
    document.querySelector("#review-admin-detail").hidden = true;
  }
  else if (action) moderateAdminReview(action);
});
loadAdminReviews();
