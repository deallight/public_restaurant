const restaurantImageState = {
  selectedRestaurantId: null,
  detail: null,
};

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

async function restaurantImageFetchJson(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "요청을 처리하지 못했습니다.");
  return payload;
}

function showRestaurantImageToast(message, isError = false) {
  const toast = document.querySelector("#restaurant-image-toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  window.clearTimeout(showRestaurantImageToast.timer);
  showRestaurantImageToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

async function loadRestaurantImageRestaurants() {
  const query = document.querySelector("#restaurant-image-search")?.value.trim() || "";
  const payload = await restaurantImageFetchJson(
    `/admin/photos/restaurants?q=${encodeURIComponent(query)}&limit=100`,
  );
  const restaurants = payload.restaurants || [];
  document.querySelector("#restaurant-image-search-summary").textContent =
    `${restaurants.length}개 음식점 · 관리자 사진이 있는 가게부터 표시`;
  const list = document.querySelector("#restaurant-image-restaurant-list");
  if (!restaurants.length) {
    list.innerHTML = '<p class="restaurant-image-admin-empty">검색 결과가 없습니다.</p>';
    return;
  }
  list.innerHTML = restaurants.map((restaurant) => `
    <button
      type="button"
      class="restaurant-image-restaurant-row ${Number(restaurant.id) === Number(restaurantImageState.selectedRestaurantId) ? "active" : ""}"
      data-restaurant-id="${restaurant.id}"
    >
      <span>
        <strong>${escapeHtml(restaurant.name)}</strong>
        <small>${escapeHtml(restaurant.road_address || restaurant.address)}</small>
      </span>
      <em>${Number(restaurant.admin_image_count || 0)}/4</em>
    </button>
  `).join("");
}

function restaurantImageEditorMarkup(detail) {
  const images = detail.images || [];
  const remaining = Math.max(0, Number(detail.image_limit || 4) - images.length);
  return `
    <header class="restaurant-image-editor-head">
      <div>
        <span class="dashboard-eyebrow">선택된 음식점</span>
        <h2>${escapeHtml(detail.name)}</h2>
        <p>${escapeHtml(detail.road_address || detail.address)}</p>
      </div>
      <a href="${escapeHtml(detail.naver_map_url)}" target="_blank" rel="noopener">네이버 지도에서 보기</a>
    </header>

    <form id="restaurant-image-upload-form" class="restaurant-image-upload-form">
      <div>
        <strong>사진 추가</strong>
        <span>최대 8MB · PNG, JPEG, WebP · 남은 자리 ${remaining}장</span>
      </div>
      <label>
        <span>사진 설명</span>
        <input name="alt_text" maxlength="120" placeholder="예: 매장 외관 또는 대표 메뉴">
      </label>
      <label class="restaurant-image-file-field">
        <span>사진 파일</span>
        <input name="images" type="file" accept="image/png,image/jpeg,image/webp" multiple ${remaining ? "" : "disabled"}>
      </label>
      <button type="submit" ${remaining ? "" : "disabled"}>사진 등록</button>
    </form>

    <section class="restaurant-image-admin-grid" data-image-count="${images.length}">
      ${images.length ? images.map((image, index) => `
        <article class="restaurant-image-admin-item" data-image-id="${image.id}">
          <div class="restaurant-image-admin-preview">
            <img src="${escapeHtml(image.thumbnail_url)}" alt="${escapeHtml(image.title || detail.name)}">
            <span>${index + 1}번째 노출</span>
          </div>
          <div class="restaurant-image-admin-fields">
            <label>
              <span>사진 설명</span>
              <input data-field="alt-text" maxlength="120" value="${escapeHtml(image.alt_text || "")}" placeholder="사진 설명">
            </label>
            <label>
              <span>노출 순서</span>
              <input data-field="sort-order" type="number" min="0" max="999" value="${Number(image.sort_order || index + 1)}">
            </label>
          </div>
          <small class="restaurant-image-file-name">${escapeHtml(image.original_filename)}</small>
          <div class="restaurant-image-admin-actions">
            <button type="button" data-action="save-image">설명·순서 저장</button>
            <label class="button-link secondary">
              사진 교체
              <input data-action="replace-image" type="file" accept="image/png,image/jpeg,image/webp" hidden>
            </label>
            <button class="danger" type="button" data-action="delete-image">삭제</button>
          </div>
        </article>
      `).join("") : `
        <div class="restaurant-image-editor-empty compact">
          <strong>관리자가 등록한 사진이 없습니다.</strong>
          <span>현재는 네이버 이미지 검색 결과만 사용하며, 등록 사진은 그보다 먼저 노출됩니다.</span>
        </div>
      `}
    </section>
  `;
}

async function loadRestaurantImageDetail(restaurantId) {
  restaurantImageState.selectedRestaurantId = Number(restaurantId);
  const detail = await restaurantImageFetchJson(`/admin/photos/restaurants/${restaurantId}`);
  restaurantImageState.detail = detail;
  document.querySelector("#restaurant-image-editor").innerHTML = restaurantImageEditorMarkup(detail);
  await loadRestaurantImageRestaurants();
}

async function uploadRestaurantImage(file, altText, imageId = null, sortOrder = null) {
  const restaurantId = restaurantImageState.selectedRestaurantId;
  const formData = new FormData();
  formData.append("image", file);
  formData.append("alt_text", altText || "");
  if (imageId) formData.append("image_id", String(imageId));
  if (sortOrder !== null) formData.append("sort_order", String(sortOrder));
  return restaurantImageFetchJson(
    `/admin/photos/restaurants/${restaurantId}/images`,
    { method: "POST", body: formData },
  );
}

document.querySelector("#restaurant-image-search-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await loadRestaurantImageRestaurants();
  } catch (error) {
    showRestaurantImageToast(error.message, true);
  }
});

document.querySelector("#restaurant-image-restaurant-list")?.addEventListener("click", async (event) => {
  const row = event.target.closest("[data-restaurant-id]");
  if (!row) return;
  try {
    await loadRestaurantImageDetail(row.dataset.restaurantId);
  } catch (error) {
    showRestaurantImageToast(error.message, true);
  }
});

document.querySelector("#restaurant-image-editor")?.addEventListener("submit", async (event) => {
  if (event.target.id !== "restaurant-image-upload-form") return;
  event.preventDefault();
  const form = event.target;
  const files = [...(form.elements.images.files || [])];
  const remaining = Math.max(
    0,
    Number(restaurantImageState.detail?.image_limit || 4) - Number(restaurantImageState.detail?.images?.length || 0),
  );
  if (!files.length) {
    showRestaurantImageToast("등록할 사진을 선택해주세요.", true);
    return;
  }
  if (files.length > remaining) {
    showRestaurantImageToast(`현재 ${remaining}장까지 추가할 수 있습니다.`, true);
    return;
  }
  const submitButton = form.querySelector("button[type='submit']");
  submitButton.disabled = true;
  try {
    let detail = null;
    for (const file of files) {
      detail = await uploadRestaurantImage(file, form.elements.alt_text.value);
    }
    restaurantImageState.detail = detail;
    document.querySelector("#restaurant-image-editor").innerHTML = restaurantImageEditorMarkup(detail);
    await loadRestaurantImageRestaurants();
    showRestaurantImageToast(`${files.length}장의 사진을 등록했습니다.`);
  } catch (error) {
    showRestaurantImageToast(error.message, true);
    submitButton.disabled = false;
  }
});

document.querySelector("#restaurant-image-editor")?.addEventListener("click", async (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action || action === "replace-image") return;
  const item = event.target.closest("[data-image-id]");
  if (!item) return;
  const restaurantId = restaurantImageState.selectedRestaurantId;
  const imageId = Number(item.dataset.imageId);
  const altText = item.querySelector("[data-field='alt-text']")?.value || "";
  const sortOrder = Number(item.querySelector("[data-field='sort-order']")?.value || 0);
  try {
    let detail;
    if (action === "save-image") {
      detail = await restaurantImageFetchJson(
        `/admin/photos/restaurants/${restaurantId}/images/${imageId}`,
        {
          method: "POST",
          body: JSON.stringify({ alt_text: altText, sort_order: sortOrder }),
        },
      );
      showRestaurantImageToast("사진 설명과 순서를 저장했습니다.");
    } else if (action === "delete-image") {
      if (!window.confirm("이 사진을 삭제할까요? 삭제한 파일은 복구할 수 없습니다.")) return;
      detail = await restaurantImageFetchJson(
        `/admin/photos/restaurants/${restaurantId}/images/${imageId}/delete`,
        { method: "POST", body: JSON.stringify({}) },
      );
      showRestaurantImageToast("사진을 삭제했습니다.");
    } else {
      return;
    }
    restaurantImageState.detail = detail;
    document.querySelector("#restaurant-image-editor").innerHTML = restaurantImageEditorMarkup(detail);
    await loadRestaurantImageRestaurants();
  } catch (error) {
    showRestaurantImageToast(error.message, true);
  }
});

document.querySelector("#restaurant-image-editor")?.addEventListener("change", async (event) => {
  if (event.target.dataset.action !== "replace-image") return;
  const file = event.target.files?.[0];
  if (!file) return;
  const item = event.target.closest("[data-image-id]");
  const imageId = Number(item.dataset.imageId);
  const altText = item.querySelector("[data-field='alt-text']")?.value || "";
  const sortOrder = Number(item.querySelector("[data-field='sort-order']")?.value || 0);
  try {
    const detail = await uploadRestaurantImage(file, altText, imageId, sortOrder);
    restaurantImageState.detail = detail;
    document.querySelector("#restaurant-image-editor").innerHTML = restaurantImageEditorMarkup(detail);
    await loadRestaurantImageRestaurants();
    showRestaurantImageToast("사진을 교체했습니다.");
  } catch (error) {
    showRestaurantImageToast(error.message, true);
  }
});

loadRestaurantImageRestaurants().catch((error) => {
  showRestaurantImageToast(error.message, true);
});
