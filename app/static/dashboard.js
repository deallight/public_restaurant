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

function statusLabel(status) {
  return {
    crawl_target_ready: "수집 대상 확정",
    adapter_pending: "어댑터 예정",
    collected: "수집됨",
    unknown: "상태 미확인",
  }[status] || status;
}

const dashboardState = {
  payload: null,
  selectedPriority: 1,
  currentPlanId: null,
};

const verificationSegments = [
  { key: "pending", label: "검증 전", color: "#8aa4b8" },
  { key: "approved", label: "승인", color: "#087f5b" },
  { key: "needs_review", label: "수동검토", color: "#f2a93b" },
  { key: "rejected", label: "반려", color: "#d95d4f" },
];

const collectionSegments = [
  { key: "stored", label: "수집 완료", className: "collected" },
  { key: "failed", label: "실패", className: "failed" },
  { key: "processing", label: "처리 중", className: "processing" },
  { key: "pending", label: "대기", className: "pending" },
];

function liveCollectionPayload() {
  return {
    start_date: document.querySelector("#live-start-date")?.value || "",
    end_date: document.querySelector("#live-end-date")?.value || "",
    batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
  };
}

function renderDonut(city) {
  const verification = city.verification || {};
  const total = Math.max(0, Number(city.verification_total || 0));
  let cursor = 0;
  const stops = [];
  verificationSegments.forEach((segment) => {
    const count = Number(verification[segment.key] || 0);
    const share = total ? count / total * 100 : 0;
    stops.push(`${segment.color} ${cursor}% ${cursor + share}%`);
    cursor += share;
  });
  if (!total) stops.push("#d9e1e5 0% 100%");
  const donut = document.querySelector("#verification-donut");
  donut.style.background = `conic-gradient(${stops.join(", ")})`;
  document.querySelector("#verification-donut-total").textContent = total.toLocaleString("ko-KR");
  document.querySelector("#verification-donut-legend").innerHTML = verificationSegments.map((segment) => {
    const count = Number(verification[segment.key] || 0);
    const percent = total ? count / total * 100 : 0;
    return `
      <li>
        <i style="background:${segment.color}"></i>
        <span>${segment.label}</span>
        <strong>${count.toLocaleString("ko-KR")} · ${percent.toFixed(1)}%</strong>
      </li>
    `;
  }).join("");
}

function renderOverview(payload) {
  const city = payload.city || {};
  document.querySelector("#dashboard-period-label").textContent = `${payload.start_date} ~ ${payload.end_date}`;
  document.querySelector("#dashboard-document-count").textContent = Number(city.document_count || 0).toLocaleString("ko-KR");
  document.querySelector("#dashboard-expense-count").textContent = Number(city.expense_count || 0).toLocaleString("ko-KR");
  document.querySelector("#dashboard-candidate-count").textContent = Number(city.candidate_count || 0).toLocaleString("ko-KR");
  renderDonut(city);
}

function progressWidth(value, maximum) {
  if (!maximum) return 0;
  return Math.max(0, Math.min(100, Number(value || 0) / maximum * 100));
}

function collectionStatusTrack(item, maximum) {
  const description = collectionSegments
    .map((segment) => `${segment.label} ${Number(item[segment.key] || 0)}건`)
    .join(", ");
  return `
    <span class="dashboard-bar-track" aria-label="${escapeHtml(description)}">
      ${collectionSegments.map((segment) => {
        const count = Number(item[segment.key] || 0);
        return count > 0
          ? `<i class="${segment.className}" style="width:${progressWidth(count, maximum)}%" title="${segment.label} ${count}건"></i>`
          : "";
      }).join("")}
    </span>
  `;
}

function verificationStatusTrack(verification) {
  const total = Number(verification?.total || 0);
  const description = verificationSegments
    .map((segment) => `${segment.label} ${Number(verification?.[segment.key] || 0)}건`)
    .join(", ");
  return `
    <span class="dashboard-bar-track verification" aria-label="${escapeHtml(description)}">
      ${verificationSegments.map((segment) => {
        const count = Number(verification?.[segment.key] || 0);
        return count > 0
          ? `<i style="width:${progressWidth(count, total)}%;background:${segment.color}" title="${segment.label} ${count}건"></i>`
          : "";
      }).join("")}
    </span>
  `;
}

function metricTracks(item, collectionMaximum) {
  const verification = item.verification || {};
  return `
    <span class="dashboard-metric-tracks">
      <span class="dashboard-track-line">
        <small>수집</small>
        ${collectionStatusTrack(item, collectionMaximum)}
      </span>
      <span class="dashboard-track-line">
        <small>검증</small>
        ${verificationStatusTrack(verification)}
      </span>
    </span>
  `;
}

function renderPriorityChart() {
  const priorities = dashboardState.payload?.priorities || [];
  const maximum = Math.max(1, ...priorities.map((item) => Number(item.total || 0)));
  const node = document.querySelector("#priority-chart");
  node.innerHTML = priorities.map((item) => `
    <button
      class="dashboard-bar-row ${Number(item.priority) === Number(dashboardState.selectedPriority) ? "active" : ""}"
      type="button"
      data-priority="${item.priority}"
    >
      <span class="dashboard-bar-label">
        <strong>${item.priority}순위</strong>
        <small>${escapeHtml(item.label)} · ${item.ready_count}/${item.source_count} 준비</small>
      </span>
      ${metricTracks(item, maximum)}
      <span class="dashboard-bar-value">
        <strong>${Number(item.total || 0).toLocaleString("ko-KR")}건</strong>
        <small>수집 ${item.stored || 0}/${item.total || 0} · 검증완료 ${item.verification?.completed || 0}/${item.verification?.total || 0}</small>
      </span>
    </button>
  `).join("") || '<p class="empty">수집 우선순위가 없습니다.</p>';

  node.querySelectorAll("[data-priority]").forEach((button) => {
    button.addEventListener("click", () => {
      dashboardState.selectedPriority = Number(button.dataset.priority);
      renderPriorityChart();
      renderPriorityDetail();
    });
  });
}

function renderPriorityDetail() {
  const priorities = dashboardState.payload?.priorities || [];
  const selected = priorities.find(
    (item) => Number(item.priority) === Number(dashboardState.selectedPriority)
  ) || priorities[0];
  if (!selected) return;
  dashboardState.selectedPriority = Number(selected.priority);
  document.querySelector("#priority-detail-title").textContent = `${selected.priority}순위 · ${selected.label}`;
  const detailLinkParams = new URLSearchParams({
    start_date: dashboardState.payload?.start_date || "",
    end_date: dashboardState.payload?.end_date || "",
  });
  document.querySelector("#priority-detail-link").href = `/admin/documents?${detailLinkParams.toString()}`;
  const institutions = selected.institutions || [];
  const maximum = Math.max(1, ...institutions.map((item) => Number(item.total || 0)));
  const node = document.querySelector("#priority-detail");
  node.innerHTML = institutions.map((item) => {
    const params = new URLSearchParams({
      institution: item.label,
      start_date: dashboardState.payload?.start_date || "",
      end_date: dashboardState.payload?.end_date || "",
    });
    const verification = item.verification || {};
    const status = item.total
      ? `수집 ${item.stored}/${item.total} · 실패 ${item.failed} · 검증완료 ${verification.completed || 0}/${verification.total || 0}`
      : statusLabel(item.status);
    return `
      <a
        class="dashboard-bar-row detail"
        href="/admin/documents?${params.toString()}"
      >
        <span class="dashboard-bar-label">
          <strong>${escapeHtml(item.label)}</strong>
          <small>${escapeHtml(status)}</small>
        </span>
        ${metricTracks(item, maximum)}
        <span class="dashboard-bar-value">
          <strong>${Number(item.total || 0).toLocaleString("ko-KR")}건</strong>
          <small>수집 ${Number(item.percent || 0).toFixed(1)}% · 검증 ${Number(verification.percent || 0).toFixed(1)}%</small>
        </span>
      </a>
    `;
  }).join("") || '<p class="empty">등록된 기관이 없습니다.</p>';
}

async function loadDashboard() {
  const startDate = document.querySelector("#dashboard-start-date")?.value || "";
  const endDate = document.querySelector("#dashboard-end-date")?.value || "";
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const payload = await fetchJson(`/ops/dashboard?${params.toString()}`);
  dashboardState.payload = payload;
  if (!(payload.priorities || []).some(
    (item) => Number(item.priority) === Number(dashboardState.selectedPriority)
  )) {
    dashboardState.selectedPriority = Number(payload.priorities?.[0]?.priority || 1);
  }
  renderOverview(payload);
  renderPriorityChart();
  renderPriorityDetail();
}

async function loadSources() {
  const payload = await fetchJson("/ops/sources");
  document.querySelector("#source-summary").textContent =
    `${payload.summary.active_count}/${payload.summary.source_count}`;
  document.querySelector("#source-groups").innerHTML = payload.groups.map((group) => {
    const readyCount = group.sources.filter((item) => item.status === "crawl_target_ready").length;
    const sources = group.sources.slice(0, 8).map((item) => `
      <li>
        <span>${escapeHtml(item.institution_name)}</span>
        <small>${escapeHtml(statusLabel(item.status))}</small>
      </li>
    `).join("");
    const hiddenCount = group.sources.length - 8;
    return `
      <article class="source-group">
        <div>
          <strong>${group.priority}순위 · ${escapeHtml(group.group_label)}</strong>
          <small>${readyCount}/${group.source_count} 준비 · 문서 ${group.documents_collected}건</small>
        </div>
        <ul>
          ${sources}
          ${hiddenCount > 0 ? `<li class="source-more">외 ${hiddenCount}개</li>` : ""}
        </ul>
      </article>
    `;
  }).join("");
}

function renderStoredCollectionPlan(plan) {
  if (!plan) return;
  document.querySelector("#collection-plan-status").innerHTML = `
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

async function loadCurrentPlan() {
  const payload = await fetchJson("/ops/logs?limit=1");
  dashboardState.currentPlanId = payload.selected_plan_id || dashboardState.currentPlanId;
  const plan = (payload.plans || []).find(
    (item) => Number(item.id) === Number(dashboardState.currentPlanId)
  );
  renderStoredCollectionPlan(plan);
}

function renderBatchResult(payload, label) {
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
      ["검증 대기", summary.needs_review || 0],
      ["DLQ", summary.dlq || 0],
    ];
  document.querySelector("#batch-status").innerHTML = `
    <div>
      <strong>${escapeHtml(label)} 완료</strong>
      <small>batch #${payload.batch_id || "-"} · ${escapeHtml(payload.status || "unknown")}</small>
    </div>
    <ul>${metrics.map(([name, value]) => `<li><span>${name}</span><strong>${value}</strong></li>`).join("")}</ul>
  `;
}

async function runAction(button, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "실행 중";
  try {
    const payload = await action();
    renderBatchResult(payload, original);
    await Promise.all([loadDashboard(), loadSources(), loadCurrentPlan()]);
  } catch (error) {
    document.querySelector("#batch-status").innerHTML = `
      <div><strong>${escapeHtml(original)} 실패</strong><small>${escapeHtml(error.message)}</small></div>
    `;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.querySelector("#load-dashboard").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "조회 중";
  try {
    await loadDashboard();
  } catch (error) {
    document.querySelector("#priority-chart").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

document.querySelector("#create-collection-plan").addEventListener("click", (event) => {
  runAction(event.currentTarget, async () => {
    const payload = await fetchJson("/ops/collection-plans", {
      method: "POST",
      body: JSON.stringify(liveCollectionPayload()),
    });
    dashboardState.currentPlanId = payload.plan_id;
    return payload;
  });
});

document.querySelector("#run-plan-batch").addEventListener("click", (event) => {
  runAction(event.currentTarget, async () => {
    if (!dashboardState.currentPlanId) await loadCurrentPlan();
    if (!dashboardState.currentPlanId) throw new Error("먼저 수집 대상을 확정해야 합니다.");
    return fetchJson(`/ops/collection-plans/${dashboardState.currentPlanId}/run`, {
      method: "POST",
      body: JSON.stringify({
        batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
        repeat: true,
        max_batches: 100,
      }),
    });
  });
});

document.querySelector("#retry-plan-failed").addEventListener("click", (event) => {
  runAction(event.currentTarget, async () => {
    if (!dashboardState.currentPlanId) await loadCurrentPlan();
    if (!dashboardState.currentPlanId) throw new Error("먼저 수집 대상을 확정해야 합니다.");
    return fetchJson(`/ops/collection-plans/${dashboardState.currentPlanId}/retry-failed`, {
      method: "POST",
      body: JSON.stringify({
        batch_size: Number(document.querySelector("#collection-batch-size")?.value || 20),
        max_batches: 100,
      }),
    });
  });
});

document.querySelector("#run-live").addEventListener("click", (event) => {
  runAction(event.currentTarget, () => fetchJson("/ops/run-busan-live", {
    method: "POST",
    body: JSON.stringify(liveCollectionPayload()),
  }));
});

document.querySelector("#verify-collected").addEventListener("click", (event) => {
  runAction(event.currentTarget, () => fetchJson("/ops/verify-collected", {
    method: "POST",
    body: JSON.stringify({ limit: 100 }),
  }));
});

document.querySelector("#verify-pending").addEventListener("click", (event) => {
  runAction(event.currentTarget, () => fetchJson("/ops/verify-pending", {
    method: "POST",
    body: JSON.stringify({ limit: 100 }),
  }));
});

Promise.all([loadDashboard(), loadSources(), loadCurrentPlan()]).catch((error) => {
  document.querySelector("#priority-chart").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
