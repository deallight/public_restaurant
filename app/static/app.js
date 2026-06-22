const state = {
  restaurants: [],
  map: null,
  markers: [],
  selectedId: null,
};

const categoryColors = {
  restaurant: "#e4572e",
  cafe: "#0b7a75",
  bar: "#7851a9",
  other: "#667085",
};

function money(value) {
  return Number(value || 0).toLocaleString("ko-KR");
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

function naverSearchAddress(address) {
  const cleaned = String(address || "")
    .replace(/\s*(?:지하|지상)?\s*\d+\s*층(?:\s*\d+\s*호)?/g, " ")
    .replace(/\s+\d{2,4}\s*호(?=\s|$)/g, " ")
    .replace(/\s+\bB\d+\s*F?\b/gi, " ")
    .replace(/\s+\b\d+\s*F\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  const withoutPostalCode = cleaned.replace(/^\s*\(?\d{5}\)?\s*/, "");
  const road = withoutPostalCode.match(/^(.*?(?:[0-9A-Za-z가-힣]+(?:대로|로|길))\s+\d+(?:-\d+)?)\b/);
  if (road) return road[1].replace(/\s+/g, " ").trim();
  const lot = withoutPostalCode.match(/^(.*?(?:[0-9A-Za-z가-힣]+(?:동|읍|면|리))\s+(?:산\s*)?\d+(?:-\d+)?)\b/);
  return (lot ? lot[1] : withoutPostalCode).replace(/\s+/g, " ").trim();
}

function naverSearchUrl(restaurant) {
  let query = restaurant.naver_map_query || "";
  if (!query) {
    const address = naverSearchAddress(restaurant.road_address || restaurant.address || "");
    const compactName = String(restaurant.name || "").replace(/\s+/g, "");
    const compactAddress = String(address).replace(/\s+/g, "");
    query = compactName && compactAddress.includes(compactName)
      ? address
      : [restaurant.name, address].filter(Boolean).join(" ");
  }
  return `https://map.naver.com/p/search/${encodeURIComponent(query)}`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "요청 실패");
  }
  return payload;
}

async function loadRestaurants() {
  const params = new URLSearchParams();
  const q = document.querySelector("#q").value.trim();
  const category = document.querySelector("#category").value;
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  const payload = await fetchJson(`/api/map/restaurants?${params.toString()}`);
  state.restaurants = payload.restaurants;
  renderRanking();
  renderMap();
}

function renderRanking() {
  document.querySelector("#result-count").textContent = state.restaurants.length;
  const list = document.querySelector("#ranking-list");
  list.innerHTML = state.restaurants.map((restaurant) => `
    <li>
      <button type="button" data-id="${restaurant.id}" class="rank-item">
        <span class="rank-main">
          <strong>${escapeHtml(restaurant.name)}</strong>
          <small>${escapeHtml(restaurant.category_label)} · ${restaurant.visit_count}회 방문</small>
        </span>
        <span class="rank-meta">
          <b>${restaurant.average_rating ? restaurant.average_rating.toFixed(1) : "-"}</b>
          <small>${money(restaurant.total_amount)}원</small>
        </span>
      </button>
    </li>
  `).join("");
  list.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => selectRestaurant(Number(button.dataset.id)));
  });
}

function loadNaverScript() {
  return new Promise((resolve, reject) => {
    if (!window.NAVER_MAP_KEY) {
      reject(new Error("missing naver key"));
      return;
    }
    if (window.naver?.maps) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = `https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId=${encodeURIComponent(window.NAVER_MAP_KEY)}`;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

async function renderMap() {
  try {
    await loadNaverScript();
    renderNaverMap();
  } catch {
    renderFallbackMap();
  }
}

function renderNaverMap() {
  const mapNode = document.querySelector("#map");
  const center = new naver.maps.LatLng(35.1795543, 129.0756416);
  state.map = state.map || new naver.maps.Map(mapNode, {
    center,
    zoom: 12,
    mapDataControl: false,
    scaleControl: false,
  });
  state.markers.forEach((marker) => marker.setMap(null));
  state.markers = state.restaurants.map((restaurant) => {
    const marker = new naver.maps.Marker({
      position: new naver.maps.LatLng(restaurant.latitude, restaurant.longitude),
      map: state.map,
      title: restaurant.name,
    });
    naver.maps.Event.addListener(marker, "click", () => selectRestaurant(restaurant.id));
    return marker;
  });
}

function renderFallbackMap() {
  const mapNode = document.querySelector("#map");
  const minLat = 35.12;
  const maxLat = 35.22;
  const minLng = 129.03;
  const maxLng = 129.18;
  mapNode.innerHTML = `
    <div class="fallback-map">
      <div class="water-label">BUSAN</div>
      ${state.restaurants.map((restaurant) => {
        const left = Math.min(94, Math.max(6, ((restaurant.longitude - minLng) / (maxLng - minLng)) * 100));
        const top = Math.min(88, Math.max(10, 100 - ((restaurant.latitude - minLat) / (maxLat - minLat)) * 100));
        return `
          <button
            type="button"
            class="map-marker ${state.selectedId === restaurant.id ? "active" : ""}"
            style="left:${left}%; top:${top}%; --marker:${categoryColors[restaurant.category] || categoryColors.other}"
            data-id="${restaurant.id}"
            title="${escapeHtml(restaurant.name)}">
            <span>${restaurant.visit_count}</span>
          </button>
        `;
      }).join("")}
    </div>
  `;
  mapNode.querySelectorAll(".map-marker").forEach((marker) => {
    marker.addEventListener("click", () => selectRestaurant(Number(marker.dataset.id)));
  });
}

async function selectRestaurant(id) {
  state.selectedId = id;
  const restaurant = await fetchJson(`/api/restaurants/${id}`);
  if (window.naver?.maps && state.map) {
    renderNaverMap();
  } else {
    renderFallbackMap();
  }
  const panel = document.querySelector("#detail-panel");
  panel.hidden = false;
  panel.innerHTML = `
    <button type="button" class="panel-close" aria-label="닫기">×</button>
    <h2>${escapeHtml(restaurant.name)}</h2>
    <p class="detail-address">${escapeHtml(restaurant.road_address || restaurant.address)}</p>
    <div class="detail-stats">
      <span>${restaurant.category_label}</span>
      <span>${restaurant.visit_count}회</span>
      <span>${restaurant.average_rating ? restaurant.average_rating.toFixed(1) : "-"}점</span>
    </div>
    <a class="naver-map-link" href="${escapeHtml(naverSearchUrl(restaurant))}" target="_blank" rel="noopener">
      네이버 지도에서 보기
    </a>
    <form id="review-form" class="review-form">
      <select name="rating" aria-label="별점">
        <option value="5">5점</option>
        <option value="4">4점</option>
        <option value="3">3점</option>
        <option value="2">2점</option>
        <option value="1">1점</option>
      </select>
      <input name="reviewer_label" placeholder="닉네임" maxlength="40">
      <textarea name="body" placeholder="리뷰" rows="3"></textarea>
      <button type="submit">등록</button>
    </form>
    <div class="reviews">
      ${restaurant.reviews.map((review) => `
        <article>
          <div><b>${review.rating}점</b><span>${escapeHtml(review.reviewer_label)}</span></div>
          <p>${escapeHtml(review.body)}</p>
          <button type="button" data-review="${review.id}" class="report-button">신고</button>
        </article>
      `).join("") || "<p class=\"empty\">등록된 리뷰가 없습니다.</p>"}
    </div>
  `;
  panel.querySelector(".panel-close").addEventListener("click", () => {
    panel.hidden = true;
    state.selectedId = null;
    if (window.naver?.maps && state.map) {
      renderNaverMap();
    } else {
      renderFallbackMap();
    }
  });
  panel.querySelector("#review-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await fetchJson(`/api/restaurants/${id}/reviews`, {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    await loadRestaurants();
    await selectRestaurant(id);
  });
  panel.querySelectorAll(".report-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await fetchJson(`/api/reviews/${button.dataset.review}/report`, {
        method: "POST",
        body: JSON.stringify({ reason: "spam_or_abuse" }),
      });
      button.textContent = "신고됨";
      button.disabled = true;
    });
  });
}

document.querySelector("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadRestaurants().catch((error) => console.error(error));
});
document.querySelector("#category").addEventListener("change", () => {
  loadRestaurants().catch((error) => console.error(error));
});

loadRestaurants().catch((error) => {
  document.querySelector("#ranking-list").innerHTML = `<li class="empty">${escapeHtml(error.message)}</li>`;
});
