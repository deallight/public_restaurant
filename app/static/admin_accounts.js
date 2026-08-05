const accountAdminState = {
  limit: 25,
  offset: 0,
  total: 0,
  currentUserId: null,
};

function accountEscapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function accountFormatDate(value) {
  if (!value) return "기록 없음";
  const normalized = String(value).replace(" ", "T");
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16);
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function accountRoleLabel(role) {
  return role === "admin" ? "관리자" : "일반 사용자";
}

function accountStatusLabel(status) {
  return ({
    active: "활성",
    suspended: "이용 정지",
    deleted: "탈퇴",
    merged: "통합됨",
  })[status] || status;
}

function accountProviderLabel(provider) {
  return ({
    naver: "네이버",
    google: "Google",
    none: "연결 없음",
  })[provider] || "연결 없음";
}

function accountShowToast(message, isError = false) {
  const toast = document.querySelector("#account-admin-toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  window.clearTimeout(accountShowToast.timer);
  accountShowToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

async function accountFetchJson(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "요청을 처리하지 못했습니다.");
  return payload;
}

function accountRowMarkup(account) {
  const isClosed = ["deleted", "merged"].includes(account.status);
  const isCurrent = Number(account.id) === Number(accountAdminState.currentUserId);
  const controlsDisabled = isClosed || isCurrent;
  const statusOptions = isClosed
    ? `<option value="${accountEscapeHtml(account.status)}">${accountEscapeHtml(accountStatusLabel(account.status))}</option>`
    : `
      <option value="active" ${account.status === "active" ? "selected" : ""}>활성</option>
      <option value="suspended" ${account.status === "suspended" ? "selected" : ""}>이용 정지</option>
    `;
  return `
    <article class="account-row" data-account-id="${Number(account.id)}">
      <div class="account-row-identity">
        <div>
          <strong>${accountEscapeHtml(account.display_name)}</strong>
          ${isCurrent ? '<span class="account-current-badge">현재 로그인</span>' : ""}
        </div>
        <span>${accountEscapeHtml(accountProviderLabel(account.provider))} 가입</span>
      </div>
      <dl class="account-row-metadata">
        <div><dt>가입일</dt><dd>${accountEscapeHtml(accountFormatDate(account.created_at))}</dd></div>
        <div><dt>최근 로그인</dt><dd>${accountEscapeHtml(accountFormatDate(account.last_login_at))}</dd></div>
        <div><dt>활동</dt><dd>리뷰 ${Number(account.review_count || 0)} · 저장 ${Number(account.saved_restaurant_count || 0)}</dd></div>
      </dl>
      <div class="account-row-controls">
        <label>
          <span>역할</span>
          <select data-field="role" ${controlsDisabled ? "disabled" : ""}>
            <option value="user" ${account.role === "user" ? "selected" : ""}>일반 사용자</option>
            <option value="admin" ${account.role === "admin" ? "selected" : ""}>관리자</option>
          </select>
        </label>
        <label>
          <span>상태</span>
          <select data-field="status" ${controlsDisabled ? "disabled" : ""}>
            ${statusOptions}
          </select>
        </label>
        <div class="account-row-action-buttons">
          <button type="button" data-action="save-account" ${controlsDisabled ? "disabled" : ""}>변경 저장</button>
          <button class="danger" type="button" data-action="delete-account" ${controlsDisabled ? "disabled" : ""}>계정 삭제</button>
        </div>
      </div>
      ${isCurrent ? '<p class="account-row-note">현재 로그인한 관리자 계정의 권한은 이 화면에서 변경할 수 없습니다.</p>' : ""}
      ${isClosed ? '<p class="account-row-note">탈퇴 또는 통합된 계정은 이 화면에서 다시 활성화할 수 없습니다.</p>' : ""}
    </article>
  `;
}

function accountRender(payload) {
  accountAdminState.total = Number(payload.total || 0);
  accountAdminState.currentUserId = payload.current_user_id;
  const summary = payload.summary || {};
  document.querySelector("#account-summary-total").textContent = Number(summary.total || 0).toLocaleString("ko-KR");
  document.querySelector("#account-summary-active").textContent = Number(summary.active || 0).toLocaleString("ko-KR");
  document.querySelector("#account-summary-suspended").textContent = Number(summary.suspended || 0).toLocaleString("ko-KR");
  document.querySelector("#account-summary-admins").textContent = Number(summary.active_admins || 0).toLocaleString("ko-KR");

  const accounts = payload.accounts || [];
  const list = document.querySelector("#account-list");
  list.innerHTML = accounts.length
    ? accounts.map(accountRowMarkup).join("")
    : '<p class="account-admin-empty">조건에 맞는 계정이 없습니다.</p>';

  const start = accountAdminState.total ? accountAdminState.offset + 1 : 0;
  const end = Math.min(accountAdminState.offset + accounts.length, accountAdminState.total);
  document.querySelector("#account-list-summary").textContent =
    `검색 결과 ${accountAdminState.total.toLocaleString("ko-KR")}개 · ${start}-${end} 표시`;
  const pageCount = Math.max(1, Math.ceil(accountAdminState.total / accountAdminState.limit));
  const currentPage = Math.floor(accountAdminState.offset / accountAdminState.limit) + 1;
  document.querySelector("#account-page-label").textContent = `${currentPage} / ${pageCount}`;
  document.querySelector("#account-page-prev").disabled = accountAdminState.offset === 0;
  document.querySelector("#account-page-next").disabled = end >= accountAdminState.total;
}

async function accountLoad() {
  const params = new URLSearchParams({
    q: document.querySelector("#account-search").value.trim(),
    role: document.querySelector("#account-role-filter").value,
    status: document.querySelector("#account-status-filter").value,
    limit: String(accountAdminState.limit),
    offset: String(accountAdminState.offset),
  });
  const list = document.querySelector("#account-list");
  list.innerHTML = '<p class="empty">계정을 불러오는 중입니다.</p>';
  try {
    accountRender(await accountFetchJson(`/admin/accounts/data?${params}`));
  } catch (error) {
    list.innerHTML = `<p class="account-admin-empty error">${accountEscapeHtml(error.message)}</p>`;
    accountShowToast(error.message, true);
  }
}

async function accountSave(row) {
  const userId = Number(row.dataset.accountId);
  const role = row.querySelector('[data-field="role"]').value;
  const status = row.querySelector('[data-field="status"]').value;
  const name = row.querySelector(".account-row-identity strong").textContent;
  const message = `${name} 계정을 ${accountRoleLabel(role)} · ${accountStatusLabel(status)} 상태로 변경할까요?`;
  if (!window.confirm(message)) return;

  const button = row.querySelector('[data-action="save-account"]');
  button.disabled = true;
  try {
    await accountFetchJson(`/admin/accounts/${userId}`, {
      method: "POST",
      body: JSON.stringify({
        role,
        status,
        action_token: document.querySelector("#admin-account-app").dataset.actionToken,
      }),
    });
    accountShowToast("계정 권한과 상태를 저장했습니다.");
    await accountLoad();
  } catch (error) {
    accountShowToast(error.message, true);
    button.disabled = false;
  }
}

async function accountDelete(row) {
  const userId = Number(row.dataset.accountId);
  const name = row.querySelector(".account-row-identity strong").textContent;
  const confirmation = window.prompt(
    `${name} 계정의 OAuth 연결과 저장 목록을 삭제하고 리뷰 작성자를 익명화합니다.\n복구할 수 없습니다. 계속하려면 "계정 삭제"를 입력하세요.`,
  );
  if (confirmation !== "계정 삭제") {
    if (confirmation !== null) accountShowToast('확인 문구 "계정 삭제"가 일치하지 않습니다.', true);
    return;
  }

  const buttons = row.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await accountFetchJson(`/admin/accounts/${userId}/delete`, {
      method: "POST",
      body: JSON.stringify({
        confirmation,
        action_token: document.querySelector("#admin-account-app").dataset.actionToken,
      }),
    });
    accountShowToast("계정 연결 정보가 삭제되고 사용자 활동이 익명화되었습니다.");
    await accountLoad();
  } catch (error) {
    accountShowToast(error.message, true);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

document.querySelector("#account-filter-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  accountAdminState.offset = 0;
  accountLoad();
});

document.querySelector("#account-list-refresh")?.addEventListener("click", () => accountLoad());

document.querySelector("#account-page-prev")?.addEventListener("click", () => {
  accountAdminState.offset = Math.max(0, accountAdminState.offset - accountAdminState.limit);
  accountLoad();
});

document.querySelector("#account-page-next")?.addEventListener("click", () => {
  accountAdminState.offset += accountAdminState.limit;
  accountLoad();
});

document.querySelector("#account-list")?.addEventListener("click", (event) => {
  const button = event.target.closest('[data-action="save-account"]');
  if (button) {
    accountSave(button.closest(".account-row"));
    return;
  }
  const deleteButton = event.target.closest('[data-action="delete-account"]');
  if (deleteButton) accountDelete(deleteButton.closest(".account-row"));
});

accountLoad();
