const state = {
  restaurants: [],
  map: null,
  markers: [],
  selectedId: null,
  fallbackFocus: null,
  fallbackViewport: null,
  regionFocus: null,
  filters: {
    category: "",
    minVisitCount: "",
  },
};

const categoryColors = {
  restaurant: "#e4572e",
  cafe: "#0b7a75",
  bar: "#7851a9",
  other: "#667085",
};

const regionSearches = [
  {
    aliases: ["서울", "서울시", "서울특별시"],
    query: "서울",
    center: { latitude: 37.5665, longitude: 126.978 },
    zoom: 11,
    viewport: { minLat: 37.41, maxLat: 37.72, minLng: 126.76, maxLng: 127.18 },
  },
  {
    aliases: ["부산", "부산시", "부산광역시"],
    query: "부산",
    center: { latitude: 35.1795543, longitude: 129.0756416 },
    zoom: 11,
    viewport: { minLat: 35.04, maxLat: 35.32, minLng: 128.82, maxLng: 129.31 },
  },
  {
    aliases: ["대전", "대전시", "대전광역시"],
    query: "대전",
    center: { latitude: 36.3504119, longitude: 127.3845475 },
    zoom: 11,
    viewport: { minLat: 36.18, maxLat: 36.52, minLng: 127.24, maxLng: 127.55 },
  },
  {
    aliases: ["천안", "천안시"],
    query: "천안",
    center: { latitude: 36.815129, longitude: 127.1138939 },
    zoom: 11,
    viewport: { minLat: 36.68, maxLat: 36.96, minLng: 126.98, maxLng: 127.28 },
  },
  {
    aliases: ["대구", "대구시", "대구광역시"],
    query: "대구",
    center: { latitude: 35.8714354, longitude: 128.601445 },
    zoom: 11,
    viewport: { minLat: 35.75, maxLat: 36.02, minLng: 128.42, maxLng: 128.78 },
  },
  {
    aliases: ["인천", "인천시", "인천광역시"],
    query: "인천",
    center: { latitude: 37.4562557, longitude: 126.7052062 },
    zoom: 11,
    viewport: { minLat: 37.28, maxLat: 37.64, minLng: 126.44, maxLng: 126.95 },
  },
  {
    aliases: ["광주", "광주시", "광주광역시"],
    query: "광주",
    center: { latitude: 35.1595454, longitude: 126.8526012 },
    zoom: 11,
    viewport: { minLat: 35.05, maxLat: 35.27, minLng: 126.7, maxLng: 127.02 },
  },
  {
    aliases: ["울산", "울산시", "울산광역시"],
    query: "울산",
    center: { latitude: 35.5383773, longitude: 129.3113596 },
    zoom: 11,
    viewport: { minLat: 35.38, maxLat: 35.7, minLng: 129.1, maxLng: 129.48 },
  },
  {
    aliases: ["세종", "세종시", "세종특별자치시"],
    query: "세종",
    center: { latitude: 36.4801322, longitude: 127.2890215 },
    zoom: 11,
    viewport: { minLat: 36.38, maxLat: 36.62, minLng: 127.16, maxLng: 127.43 },
  },
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

async function loadRestaurants(options = {}) {
  const params = new URLSearchParams();
  const q = document.querySelector("#q").value.trim();
  const regionFocus = regionSearchFor(q);
  state.regionFocus = regionFocus;
  if (regionFocus) {
    params.set("q", regionFocus.query);
    params.set("search_mode", "address");
  } else if (isRegionLikeQuery(q)) {
    params.set("q", q);
    params.set("search_mode", "address");
  } else if (q) {
    params.set("q", q);
  }
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.minVisitCount) params.set("min_visit_count", state.filters.minVisitCount);
  const payload = await fetchJson(`/api/map/restaurants?${params.toString()}`);
  state.restaurants = payload.restaurants;
  if (options.focus === "results" && state.restaurants.length === 1) {
    state.selectedId = state.restaurants[0].id;
  }
  if (state.selectedId && !state.restaurants.some((restaurant) => restaurant.id === state.selectedId)) {
    state.selectedId = null;
    const panel = document.querySelector("#detail-panel");
    panel.hidden = true;
    panel.innerHTML = "";
  }
  renderRanking();
  await renderMap({ focus: options.focus, regionFocus });
}

function compactSearchText(value) {
  return String(value || "").replace(/\s+/g, "").toLowerCase();
}

function regionSearchFor(value) {
  const query = compactSearchText(value);
  if (!query) return null;
  return regionSearches.find((region) => region.aliases.some((alias) => compactSearchText(alias) === query)) || null;
}

function isRegionLikeQuery(value) {
  const query = compactSearchText(value);
  return /(특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동)$/.test(query);
}

function updateFilterIndex() {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    const filter = button.dataset.filter;
    const value = button.dataset.value || "";
    const selected = (
      (filter === "category" && state.filters.category === value)
      || (filter === "min_visit_count" && state.filters.minVisitCount === value)
    );
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  const visitLabel = document.querySelector("#visit-filter-label");
  if (visitLabel) {
    const selectedVisitButton = Array.from(document.querySelectorAll('[data-filter="min_visit_count"]'))
      .find((button) => (button.dataset.value || "") === state.filters.minVisitCount);
    visitLabel.textContent = selectedVisitButton?.textContent.trim() || "방문 전체";
  }
}

function renderRanking() {
  document.querySelector("#result-count").textContent = state.restaurants.length;
  const list = document.querySelector("#ranking-list");
  list.innerHTML = state.restaurants.map((restaurant) => `
    <li>
      <button type="button" data-id="${restaurant.id}" class="rank-item ${state.selectedId === restaurant.id ? "active" : ""}">
        <span class="rank-main">
          <strong>${escapeHtml(restaurant.name)}</strong>
          <small>${escapeHtml(restaurant.category_label)} · ${restaurant.visit_count}회 방문</small>
        </span>
        <span class="rank-meta">
          <b>${restaurant.average_rating ? restaurant.average_rating.toFixed(1) : "-"}</b>
        </span>
      </button>
    </li>
  `).join("");
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

async function renderMap(options = {}) {
  try {
    await loadNaverScript();
    renderNaverMap();
    if (options.focus === "results" && options.regionFocus) {
      focusNaverMapOnRegion(options.regionFocus);
    } else if (options.focus === "results") {
      focusNaverMapOnRestaurants(state.restaurants);
    }
  } catch {
    clearNaverMarkers();
    state.map = null;
    if (options.focus === "results" && options.regionFocus) {
      setFallbackRegionFocus(options.regionFocus);
    } else if (options.focus === "results") {
      setFallbackFocus(state.restaurants);
    } else if (!state.selectedId) {
      state.fallbackFocus = null;
      state.fallbackViewport = null;
    }
    renderFallbackMap();
  }
}

function clearNaverMarkers() {
  state.markers.forEach((marker) => {
    try {
      marker.setMap(null);
    } catch {
      // Ignore provider cleanup failures and fall back to a fresh marker list.
    }
  });
  state.markers = [];
}

function restaurantPosition(restaurant) {
  const latitude = Number(restaurant?.latitude);
  const longitude = Number(restaurant?.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return { latitude, longitude };
}

function naverPosition(restaurant) {
  const position = restaurantPosition(restaurant);
  if (!position || !window.naver?.maps) return null;
  return new naver.maps.LatLng(position.latitude, position.longitude);
}

function focusNaverMapOnRestaurant(restaurant, zoom = 16) {
  if (!state.map) return;
  const position = naverPosition(restaurant);
  if (!position) return;
  const currentZoom = typeof state.map.getZoom === "function" ? state.map.getZoom() : 0;
  const targetZoom = currentZoom && currentZoom > zoom ? currentZoom : zoom;
  if (typeof state.map.morph === "function") {
    state.map.morph(position, targetZoom);
    return;
  }
  state.map.setCenter(position);
  if (typeof state.map.setZoom === "function") {
    if (!currentZoom || currentZoom < targetZoom) {
      state.map.setZoom(targetZoom, true);
    }
  }
}

function focusNaverMapOnRegion(region) {
  if (!state.map || !window.naver?.maps) return;
  const center = new naver.maps.LatLng(region.center.latitude, region.center.longitude);
  if (typeof state.map.panTo === "function") {
    state.map.panTo(center);
  } else {
    state.map.setCenter(center);
  }
  if (typeof state.map.setZoom === "function") {
    state.map.setZoom(region.zoom, true);
  }
}

function focusNaverMapOnRestaurants(restaurants) {
  if (!state.map || !window.naver?.maps) return;
  const positions = restaurants
    .map((restaurant) => ({ restaurant, position: restaurantPosition(restaurant) }))
    .filter((item) => item.position);
  if (!positions.length) return;
  const uniquePositions = new Set(
    positions.map((item) => `${item.position.latitude.toFixed(6)},${item.position.longitude.toFixed(6)}`),
  );
  if (positions.length === 1 || uniquePositions.size === 1) {
    focusNaverMapOnRestaurant(positions[0].restaurant);
    return;
  }
  const latitudes = positions.map((item) => item.position.latitude);
  const longitudes = positions.map((item) => item.position.longitude);
  const southWest = new naver.maps.LatLng(Math.min(...latitudes), Math.min(...longitudes));
  const northEast = new naver.maps.LatLng(Math.max(...latitudes), Math.max(...longitudes));
  const bounds = new naver.maps.LatLngBounds(southWest, northEast);
  if (typeof state.map.fitBounds === "function") {
    state.map.fitBounds(bounds);
  } else {
    const center = new naver.maps.LatLng(
      (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
      (Math.min(...longitudes) + Math.max(...longitudes)) / 2,
    );
    state.map.setCenter(center);
  }
}

function markerIcon(restaurant) {
  const active = state.selectedId === restaurant.id;
  const color = active ? "#f2b84b" : "#3b82f6";
  const stroke = active ? "#1d2939" : "#fff";
  return {
    content: `
      <svg
        width="30"
        height="42"
        viewBox="0 0 30 42"
        role="img"
        aria-label="${escapeHtml(restaurant.name)}"
        style="display:block; filter:drop-shadow(0 8px 14px rgba(29,41,57,0.28)); pointer-events:none;"
      >
        <path
          d="M15 40.5C12.5 35.7 2.5 25.1 2.5 15.1C2.5 7.7 8.1 2 15 2C21.9 2 27.5 7.7 27.5 15.1C27.5 25.1 17.5 35.7 15 40.5Z"
          fill="${color}"
          stroke="${stroke}"
          stroke-width="2"
        />
        <circle cx="15" cy="15" r="5.25" fill="#fff" opacity="0.96" />
      </svg>
    `,
    size: new naver.maps.Size(30, 42),
    anchor: new naver.maps.Point(15, 40),
  };
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
  clearNaverMarkers();
  state.markers = state.restaurants.map((restaurant) => {
    const marker = new naver.maps.Marker({
      position: new naver.maps.LatLng(restaurant.latitude, restaurant.longitude),
      map: state.map,
      title: restaurant.name,
      icon: markerIcon(restaurant),
      zIndex: state.selectedId === restaurant.id ? 200 : 100,
    });
    naver.maps.Event.addListener(marker, "click", () => selectRestaurant(restaurant.id));
    return marker;
  });
}

function renderFallbackMap() {
  const mapNode = document.querySelector("#map");
  mapNode.innerHTML = `
    <div class="fallback-map"${fallbackMapStyle()}>
      <div class="water-label">BUSAN</div>
      ${state.restaurants.map((restaurant) => {
        const { left, top } = fallbackPoint(restaurant);
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
}

function fallbackPoint(restaurant) {
  const { minLat, maxLat, minLng, maxLng } = state.fallbackViewport || defaultFallbackViewport();
  const position = restaurantPosition(restaurant) || { latitude: minLat, longitude: minLng };
  return {
    left: Math.min(94, Math.max(6, ((position.longitude - minLng) / (maxLng - minLng)) * 100)),
    top: Math.min(88, Math.max(10, 100 - ((position.latitude - minLat) / (maxLat - minLat)) * 100)),
  };
}

function defaultFallbackViewport() {
  return {
    minLat: 35.12,
    maxLat: 35.22,
    minLng: 129.03,
    maxLng: 129.18,
  };
}

function coordinateViewport(restaurants) {
  const positions = restaurants.map(restaurantPosition).filter(Boolean);
  if (!positions.length) return null;
  const latitudes = positions.map((position) => position.latitude);
  const longitudes = positions.map((position) => position.longitude);
  const minLat = Math.min(...latitudes);
  const maxLat = Math.max(...latitudes);
  const minLng = Math.min(...longitudes);
  const maxLng = Math.max(...longitudes);
  const latPadding = Math.max((maxLat - minLat) * 0.12, 0.015);
  const lngPadding = Math.max((maxLng - minLng) * 0.12, 0.015);
  return {
    minLat: minLat - latPadding,
    maxLat: maxLat + latPadding,
    minLng: minLng - lngPadding,
    maxLng: maxLng + lngPadding,
  };
}

function setFallbackRegionFocus(region) {
  state.fallbackViewport = region.viewport;
  const centerPoint = fallbackPoint({
    latitude: region.center.latitude,
    longitude: region.center.longitude,
  });
  state.fallbackFocus = {
    scale: 1.2,
    panX: 50 - centerPoint.left * 1.2,
    panY: 50 - centerPoint.top * 1.2,
  };
}

function setFallbackFocus(restaurants) {
  state.fallbackViewport = coordinateViewport(restaurants);
  const points = restaurants.map(fallbackPoint);
  if (!points.length) {
    state.fallbackFocus = null;
    state.fallbackViewport = null;
    return;
  }
  const leftValues = points.map((point) => point.left);
  const topValues = points.map((point) => point.top);
  const minLeft = Math.min(...leftValues);
  const maxLeft = Math.max(...leftValues);
  const minTop = Math.min(...topValues);
  const maxTop = Math.max(...topValues);
  const width = Math.max(1, maxLeft - minLeft);
  const height = Math.max(1, maxTop - minTop);
  const scale = points.length === 1
    ? 2.6
    : Math.min(2.2, Math.max(1.2, Math.min(82 / (width + 18), 72 / (height + 18))));
  const centerLeft = (minLeft + maxLeft) / 2;
  const centerTop = (minTop + maxTop) / 2;
  state.fallbackFocus = {
    scale,
    panX: 50 - centerLeft * scale,
    panY: 50 - centerTop * scale,
  };
}

function fallbackMapStyle() {
  if (!state.fallbackFocus) return "";
  return ` style="--fallback-scale:${state.fallbackFocus.scale}; --fallback-pan-x:${state.fallbackFocus.panX}%; --fallback-pan-y:${state.fallbackFocus.panY}%;"`;
}

async function selectRestaurant(id) {
  state.selectedId = id;
  renderRanking();
  const restaurant = await fetchJson(`/api/restaurants/${id}`);
  if (window.naver?.maps && state.map) {
    try {
      renderNaverMap();
      focusNaverMapOnRestaurant(restaurant);
    } catch {
      clearNaverMarkers();
      state.map = null;
      setFallbackFocus([restaurant]);
      renderFallbackMap();
    }
  } else {
    setFallbackFocus([restaurant]);
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
    renderRanking();
    if (window.naver?.maps && state.map) {
      renderNaverMap();
    } else {
      state.fallbackFocus = null;
      state.fallbackViewport = null;
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
  loadRestaurants({ focus: "results" }).catch((error) => console.error(error));
});
document.querySelector("#ranking-list").addEventListener("click", (event) => {
  const button = event.target.closest(".rank-item");
  if (!button) return;
  selectRestaurant(Number(button.dataset.id)).catch((error) => console.error(error));
});
document.querySelector("#map").addEventListener("click", (event) => {
  const marker = event.target.closest(".map-marker");
  if (!marker) return;
  selectRestaurant(Number(marker.dataset.id)).catch((error) => console.error(error));
});
document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.filter === "category") {
      state.filters.category = button.dataset.value || "";
    }
    if (button.dataset.filter === "min_visit_count") {
      state.filters.minVisitCount = button.dataset.value || "";
      button.closest("details")?.removeAttribute("open");
    }
    updateFilterIndex();
    loadRestaurants().catch((error) => console.error(error));
  });
});

updateFilterIndex();
loadRestaurants().catch((error) => {
  document.querySelector("#ranking-list").innerHTML = `<li class="empty">${escapeHtml(error.message)}</li>`;
});
