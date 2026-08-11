const state = {
  restaurants: [],
  map: null,
  markers: [],
  markerViewportKey: null,
  mapEventsBound: false,
  clusterEntries: [],
  clusterInfoWindow: null,
  openClusterMarker: null,
  fallbackClusters: [],
  fallbackClusterIndex: null,
  selectedId: null,
  fallbackFocus: null,
  fallbackViewport: null,
  regionFocus: null,
  detailSelectionRequest: 0,
  rankingRenderKey: null,
  filters: {
    category: "",
    minVisitCount: "",
    savedOnly: new URLSearchParams(window.location.search).get("saved_only") === "1",
  },
};

const markerClusterRadius = 56;
const markerViewportPaddingRatio = 0.3;
const numericClusterMaxZoom = 14;

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
    aliases: ["중구"],
    query: "부산광역시 중구",
    center: { latitude: 35.106214, longitude: 129.032352 },
    zoom: 14,
    viewport: { minLat: 35.085, maxLat: 35.115, minLng: 129.02, maxLng: 129.05 },
    useBounds: true,
  },
  {
    aliases: ["서구"],
    query: "부산광역시 서구",
    center: { latitude: 35.097955, longitude: 129.024356 },
    zoom: 13,
    viewport: { minLat: 35.06, maxLat: 35.13, minLng: 128.98, maxLng: 129.04 },
    useBounds: true,
  },
  {
    aliases: ["동구"],
    query: "부산광역시 동구",
    center: { latitude: 35.129274, longitude: 129.045325 },
    zoom: 13,
    viewport: { minLat: 35.1, maxLat: 35.15, minLng: 129.025, maxLng: 129.07 },
    useBounds: true,
  },
  {
    aliases: ["영도", "영도구"],
    query: "부산광역시 영도구",
    center: { latitude: 35.091212, longitude: 129.067875 },
    zoom: 13,
    viewport: { minLat: 35.05, maxLat: 35.105, minLng: 129.02, maxLng: 129.09 },
    useBounds: true,
  },
  {
    aliases: ["부산진", "부산진구"],
    query: "부산광역시 부산진구",
    center: { latitude: 35.163087, longitude: 129.053174 },
    zoom: 13,
    viewport: { minLat: 35.135, maxLat: 35.19, minLng: 129.03, maxLng: 129.08 },
    useBounds: true,
  },
  {
    aliases: ["남구"],
    query: "부산광역시 남구",
    center: { latitude: 35.136578, longitude: 129.084163 },
    zoom: 13,
    viewport: { minLat: 35.095, maxLat: 35.155, minLng: 129.055, maxLng: 129.13 },
    useBounds: true,
  },
  {
    aliases: ["북구"],
    query: "부산광역시 북구",
    center: { latitude: 35.197185, longitude: 128.990438 },
    zoom: 12,
    viewport: { minLat: 35.18, maxLat: 35.27, minLng: 128.98, maxLng: 129.07 },
    useBounds: true,
  },
  {
    aliases: ["사하", "사하구"],
    query: "부산광역시 사하구",
    center: { latitude: 35.104585, longitude: 128.974817 },
    zoom: 12,
    viewport: { minLat: 35.05, maxLat: 35.13, minLng: 128.94, maxLng: 129.03 },
    useBounds: true,
  },
  {
    aliases: ["금정", "금정구"],
    query: "부산광역시 금정구",
    center: { latitude: 35.242992, longitude: 129.092074 },
    zoom: 12,
    viewport: { minLat: 35.2, maxLat: 35.31, minLng: 129.03, maxLng: 129.12 },
    useBounds: true,
  },
  {
    aliases: ["강서", "강서구"],
    query: "부산광역시 강서구",
    center: { latitude: 35.212217, longitude: 128.980387 },
    zoom: 12,
    viewport: { minLat: 35.05, maxLat: 35.24, minLng: 128.8, maxLng: 128.99 },
    useBounds: true,
  },
  {
    aliases: ["연제", "연제구"],
    query: "부산광역시 연제구",
    center: { latitude: 35.176193, longitude: 129.079915 },
    zoom: 13,
    viewport: { minLat: 35.165, maxLat: 35.205, minLng: 129.055, maxLng: 129.105 },
    useBounds: true,
  },
  {
    aliases: ["수영", "수영구"],
    query: "부산광역시 수영구",
    center: { latitude: 35.145703, longitude: 129.113222 },
    zoom: 13,
    viewport: { minLat: 35.13, maxLat: 35.18, minLng: 129.09, maxLng: 129.14 },
    useBounds: true,
  },
  {
    aliases: ["사상", "사상구"],
    query: "부산광역시 사상구",
    center: { latitude: 35.152624, longitude: 128.991248 },
    zoom: 13,
    viewport: { minLat: 35.12, maxLat: 35.19, minLng: 128.95, maxLng: 129.03 },
    useBounds: true,
  },
  {
    aliases: ["기장", "기장군"],
    query: "부산광역시 기장군",
    center: { latitude: 35.244498, longitude: 129.222312 },
    zoom: 12,
    viewport: { minLat: 35.14, maxLat: 35.39, minLng: 129.11, maxLng: 129.35 },
    useBounds: true,
  },
  {
    aliases: ["정관", "정관읍"],
    query: "부산광역시 기장군 정관읍",
    center: { latitude: 35.321865, longitude: 129.17669 },
    zoom: 14,
    viewport: { minLat: 35.295, maxLat: 35.34, minLng: 129.15, maxLng: 129.205 },
    useBounds: true,
  },
  {
    aliases: ["일광", "일광읍"],
    query: "부산광역시 기장군 일광읍",
    center: { latitude: 35.264365, longitude: 129.233195 },
    zoom: 14,
    viewport: { minLat: 35.24, maxLat: 35.29, minLng: 129.2, maxLng: 129.27 },
    useBounds: true,
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
  {
    aliases: ["해운대", "해운대구"],
    query: "부산광역시 해운대구",
    center: { latitude: 35.163132, longitude: 129.163543 },
    zoom: 13,
    viewport: { minLat: 35.145, maxLat: 35.185, minLng: 129.135, maxLng: 129.19 },
    useBounds: true,
  },
  {
    aliases: ["서면"],
    query: "부산광역시 부산진구",
    center: { latitude: 35.157662, longitude: 129.05903 },
    zoom: 15,
    viewport: { minLat: 35.145, maxLat: 35.171, minLng: 129.043, maxLng: 129.075 },
    useBounds: true,
  },
  {
    aliases: ["전포", "전포동", "전포카페거리"],
    query: "부산광역시 부산진구",
    center: { latitude: 35.15433, longitude: 129.06372 },
    zoom: 15,
    viewport: { minLat: 35.145, maxLat: 35.164, minLng: 129.055, maxLng: 129.074 },
    useBounds: true,
  },
  {
    aliases: ["광안리"],
    query: "부산광역시 수영구",
    center: { latitude: 35.15317, longitude: 129.11867 },
    zoom: 15,
    viewport: { minLat: 35.145, maxLat: 35.162, minLng: 129.108, maxLng: 129.13 },
    useBounds: true,
  },
  {
    aliases: ["센텀", "센텀시티"],
    query: "부산광역시 해운대구",
    center: { latitude: 35.16887, longitude: 129.13134 },
    zoom: 15,
    viewport: { minLat: 35.158, maxLat: 35.178, minLng: 129.118, maxLng: 129.144 },
    useBounds: true,
  },
  {
    aliases: ["부산대", "부산대학교", "부대앞"],
    query: "부산광역시 금정구",
    center: { latitude: 35.23121, longitude: 129.08449 },
    zoom: 15,
    viewport: { minLat: 35.221, maxLat: 35.24, minLng: 129.073, maxLng: 129.096 },
    useBounds: true,
  },
  {
    aliases: ["경성대", "부경대", "경성대부경대"],
    query: "부산광역시 남구",
    center: { latitude: 35.13754, longitude: 129.10053 },
    zoom: 15,
    viewport: { minLat: 35.128, maxLat: 35.147, minLng: 129.089, maxLng: 129.112 },
    useBounds: true,
  },
  {
    aliases: ["남포", "남포동", "자갈치"],
    query: "부산광역시 중구",
    center: { latitude: 35.09796, longitude: 129.03473 },
    zoom: 15,
    viewport: { minLat: 35.088, maxLat: 35.108, minLng: 129.023, maxLng: 129.046 },
    useBounds: true,
  },
  {
    aliases: ["동래", "동래구"],
    query: "부산광역시 동래구",
    center: { latitude: 35.20554, longitude: 129.08367 },
    zoom: 14,
    viewport: { minLat: 35.186, maxLat: 35.224, minLng: 129.062, maxLng: 129.105 },
    useBounds: true,
  },
  {
    aliases: ["연산", "연산동"],
    query: "부산광역시 연제구",
    center: { latitude: 35.18605, longitude: 129.08124 },
    zoom: 15,
    viewport: { minLat: 35.174, maxLat: 35.198, minLng: 129.066, maxLng: 129.096 },
    useBounds: true,
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

function reviewStars(value) {
  const rating = Math.max(0, Math.min(5, Math.round(Number(value) || 0)));
  return `
    <span class="review-stars" aria-label="${rating}점">
      <span class="review-stars-filled" aria-hidden="true">${"★".repeat(rating)}</span><span class="review-stars-empty" aria-hidden="true">${"☆".repeat(5 - rating)}</span>
    </span>
  `;
}

function formatVisitDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const matched = text.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?/,
  );
  if (!matched) return text;
  const [, year, month, day, hour, minute] = matched;
  return `${year}.${month}.${day}${hour && minute ? ` ${hour}:${minute}` : ""}`;
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
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
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
    if (regionFocus.useBounds && regionFocus.viewport) {
      params.set("bounds", viewportBounds(regionFocus.viewport));
    }
  } else if (isAddressLikeQuery(q)) {
    params.set("q", q);
    params.set("search_mode", "address");
  } else if (q) {
    params.set("q", q);
  }
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.minVisitCount) params.set("min_visit_count", state.filters.minVisitCount);
  if (state.filters.savedOnly) params.set("saved_only", "1");
  const payload = await fetchJson(`/api/map/restaurants?${params.toString()}`);
  state.restaurants = payload.restaurants;
  state.fallbackClusterIndex = null;
  closeClusterInfoWindow();
  if (options.focus === "results" && state.restaurants.length === 1) {
    state.selectedId = state.restaurants[0].id;
  }
  if (state.selectedId && !state.restaurants.some((restaurant) => restaurant.id === state.selectedId)) {
    state.selectedId = null;
    const panel = document.querySelector("#detail-panel");
    panel.hidden = true;
    panel.innerHTML = "";
    panel.classList.remove("detail-expanded");
    panel.closest(".map-stage")?.classList.remove("detail-open", "detail-expanded");
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

function viewportBounds(viewport) {
  return [viewport.minLat, viewport.minLng, viewport.maxLat, viewport.maxLng].join(",");
}

function isAddressLikeQuery(value) {
  const query = String(value || "").replace(/\s+/g, " ").trim();
  const compact = compactSearchText(query);
  if (!compact) return false;
  return (
    /(특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)$/.test(compact)
    || /(특별시|광역시|특별자치시|특별자치도)/.test(compact)
    || /[가-힣0-9]+(?:시|군|구|읍|면|동|리)\s+[가-힣0-9]/.test(query)
    || /[가-힣0-9]+(?:대로|로|길)\s*\d/.test(query)
    || /(?:^|\s)(?:산\s*)?\d{1,5}(?:-\d{1,5})?(?:\s|$)/.test(query)
    || /^\d{5}$/.test(compact)
  );
}

function updateFilterIndex() {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    const filter = button.dataset.filter;
    const value = button.dataset.value || "";
    const selected = (
      (filter === "category" && !state.filters.savedOnly && state.filters.category === value)
      || (filter === "min_visit_count" && state.filters.minVisitCount === value)
      || (filter === "saved" && state.filters.savedOnly)
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

function restaurantsVisibleInMap() {
  if (!state.map || !window.naver?.maps || typeof state.map.getBounds !== "function") {
    return state.restaurants;
  }
  let bounds;
  try {
    bounds = state.map.getBounds();
  } catch {
    return state.restaurants;
  }
  if (!bounds || typeof bounds.hasLatLng !== "function") return state.restaurants;
  return state.restaurants.filter((restaurant) => {
    const position = naverPosition(restaurant);
    if (!position) return false;
    try {
      return bounds.hasLatLng(position);
    } catch {
      return false;
    }
  });
}

function renderRanking(options = {}) {
  const visibleRestaurants = restaurantsVisibleInMap();
  const renderKey = [
    state.selectedId || "",
    ...visibleRestaurants.map((restaurant) => restaurant.id),
  ].join(":");
  if (options.skipIfUnchanged && renderKey === state.rankingRenderKey) return;
  state.rankingRenderKey = renderKey;
  document.querySelector("#result-count").textContent = visibleRestaurants.length;
  const list = document.querySelector("#ranking-list");
  if (!visibleRestaurants.length) {
    list.innerHTML = '<li class="empty">현재 지도 영역에 음식점이 없습니다.</li>';
    return;
  }
  list.innerHTML = visibleRestaurants.map((restaurant) => `
    <li>
      <button type="button" data-id="${restaurant.id}" class="rank-item ${state.selectedId === restaurant.id ? "active" : ""}">
        <span class="rank-main">
          <strong>${escapeHtml(restaurant.name)}</strong>
          <small>${escapeHtml(restaurant.category_detail_label || restaurant.category_label)} · ${restaurant.visit_count}회 방문</small>
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
    state.mapEventsBound = false;
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

function closeClusterInfoWindow() {
  try {
    state.clusterInfoWindow?.close();
  } catch {
    // The provider may already have disposed the info window with the map.
  }
  state.clusterInfoWindow = null;
  state.openClusterMarker = null;
}

function clearNaverMarkers() {
  closeClusterInfoWindow();
  const markers = state.markers;
  state.markers = [];
  state.clusterEntries = [];
  state.markerViewportKey = null;
  markers.forEach((marker) => {
    try {
      marker.setMap(null);
    } catch {
      // Continue removing the remaining markers if one provider marker fails.
    }
    try {
      window.naver?.maps?.Event?.clearInstanceListeners?.(marker);
    } catch {
      // Listener cleanup must never prevent the visual marker from being removed.
    }
  });
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

function clusterGridKey(x, y, cellSize) {
  return `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}`;
}

function addClusterToGrid(grid, cluster, cellSize) {
  const key = clusterGridKey(cluster.x, cluster.y, cellSize);
  if (!grid.has(key)) grid.set(key, new Set());
  grid.get(key).add(cluster);
  cluster.gridKey = key;
}

function buildRestaurantClusters(restaurants, pointForRestaurant, radius = markerClusterRadius) {
  const grid = new Map();
  const clusters = [];
  const orderedRestaurants = [...restaurants].sort((left, right) => {
    const idDifference = Number(left.id) - Number(right.id);
    return Number.isFinite(idDifference) && idDifference !== 0
      ? idDifference
      : String(left.name || "").localeCompare(String(right.name || ""), "ko");
  });

  orderedRestaurants.forEach((restaurant) => {
    const position = restaurantPosition(restaurant);
    if (!position) return;
    const point = pointForRestaurant(restaurant, position);
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return;

    const cellX = Math.floor(point.x / radius);
    const cellY = Math.floor(point.y / radius);
    let nearestCluster = null;
    let nearestDistanceSquared = radius * radius;
    for (let xOffset = -1; xOffset <= 1; xOffset += 1) {
      for (let yOffset = -1; yOffset <= 1; yOffset += 1) {
        const candidates = grid.get(`${cellX + xOffset}:${cellY + yOffset}`);
        if (!candidates) continue;
        candidates.forEach((cluster) => {
          const distanceSquared = ((cluster.x - point.x) ** 2) + ((cluster.y - point.y) ** 2);
          if (distanceSquared <= nearestDistanceSquared) {
            nearestCluster = cluster;
            nearestDistanceSquared = distanceSquared;
          }
        });
      }
    }

    if (!nearestCluster) {
      const cluster = {
        x: point.x,
        y: point.y,
        latitude: position.latitude,
        longitude: position.longitude,
        restaurants: [restaurant],
        gridKey: "",
      };
      clusters.push(cluster);
      addClusterToGrid(grid, cluster, radius);
      return;
    }

    const previousSize = nearestCluster.restaurants.length;
    const previousBucket = grid.get(nearestCluster.gridKey);
    previousBucket?.delete(nearestCluster);
    if (previousBucket?.size === 0) grid.delete(nearestCluster.gridKey);
    nearestCluster.x = ((nearestCluster.x * previousSize) + point.x) / (previousSize + 1);
    nearestCluster.y = ((nearestCluster.y * previousSize) + point.y) / (previousSize + 1);
    nearestCluster.latitude = (
      (nearestCluster.latitude * previousSize) + position.latitude
    ) / (previousSize + 1);
    nearestCluster.longitude = (
      (nearestCluster.longitude * previousSize) + position.longitude
    ) / (previousSize + 1);
    nearestCluster.restaurants.push(restaurant);
    addClusterToGrid(grid, nearestCluster, radius);
  });

  return clusters;
}

function webMercatorPoint(position, zoom) {
  const latitude = Math.max(-85.05112878, Math.min(85.05112878, position.latitude));
  const latitudeRadians = latitude * Math.PI / 180;
  const worldSize = 256 * (2 ** Number(zoom || 0));
  const sine = Math.sin(latitudeRadians);
  return {
    x: ((position.longitude + 180) / 360) * worldSize,
    y: (
      0.5 - (Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI))
    ) * worldSize,
  };
}

function naverMapPoint(position, zoom) {
  if (state.map && window.naver?.maps) {
    try {
      const projection = state.map.getProjection();
      const offset = projection.fromCoordToOffset(
        new naver.maps.LatLng(position.latitude, position.longitude),
      );
      return {
        x: Number(offset.x),
        y: Number(offset.y),
      };
    } catch {
      // Keep clustering available for fallback map providers and test environments.
    }
  }
  return webMercatorPoint(position, zoom);
}

function restaurantsInNaverMarkerViewport(restaurants) {
  const mapNode = document.querySelector("#map");
  const mapWidth = Number(mapNode?.clientWidth);
  const mapHeight = Number(mapNode?.clientHeight);
  if (
    !state.map
    || !window.naver?.maps
    || !Number.isFinite(mapWidth)
    || mapWidth <= 0
    || !Number.isFinite(mapHeight)
    || mapHeight <= 0
  ) {
    return restaurants;
  }

  let projection;
  try {
    projection = state.map.getProjection();
  } catch {
    return restaurants;
  }
  if (!projection || typeof projection.fromCoordToOffset !== "function") return restaurants;

  const paddingX = Math.max(markerClusterRadius * 2, mapWidth * markerViewportPaddingRatio);
  const paddingY = Math.max(markerClusterRadius * 2, mapHeight * markerViewportPaddingRatio);
  const viewportRestaurants = [];
  for (const restaurant of restaurants) {
    const position = restaurantPosition(restaurant);
    if (!position) continue;
    let offset;
    try {
      offset = projection.fromCoordToOffset(
        new naver.maps.LatLng(position.latitude, position.longitude),
      );
    } catch {
      return restaurants;
    }
    const x = Number(offset?.x);
    const y = Number(offset?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return restaurants;
    if (
      x >= -paddingX
      && x <= mapWidth + paddingX
      && y >= -paddingY
      && y <= mapHeight + paddingY
    ) {
      viewportRestaurants.push(restaurant);
    }
  }
  return viewportRestaurants;
}

function clustersForNaverMap(restaurants, zoom) {
  return buildRestaurantClusters(
    restaurants,
    (_restaurant, position) => naverMapPoint(position, zoom),
  );
}

function focusNaverMapOnPoint(position, zoom, options = {}) {
  if (!state.map || !position) return;
  const currentZoom = typeof state.map.getZoom === "function" ? state.map.getZoom() : 0;
  const targetZoom = options.keepCloserZoom && currentZoom && currentZoom > zoom ? currentZoom : zoom;
  if (typeof state.map.morph === "function") {
    state.map.morph(position, targetZoom);
    return;
  }
  state.map.setCenter(position);
  if (typeof state.map.setZoom === "function" && currentZoom !== targetZoom) {
    state.map.setZoom(targetZoom, true);
  }
}

function focusNaverMapOnRestaurant(restaurant, zoom = 16) {
  if (!state.map) return;
  const position = naverPosition(restaurant);
  if (!position) return;
  focusNaverMapOnPoint(position, zoom, { keepCloserZoom: true });
}

function focusNaverMapOnRegion(region) {
  if (!state.map || !window.naver?.maps) return;
  const center = new naver.maps.LatLng(region.center.latitude, region.center.longitude);
  focusNaverMapOnPoint(center, region.zoom);
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

function markerIcon(restaurant, mergedCount = 1, activeOverride = false) {
  const active = activeOverride || state.selectedId === restaurant.id;
  const color = active ? "#f2b84b" : "#3b82f6";
  const stroke = active ? "#1d2939" : "#fff";
  const isMerged = mergedCount > 1;
  const countLabel = mergedCount > 99 ? "99+" : String(mergedCount);
  const countFontSize = countLabel.length > 2 ? 7 : 9;
  return {
    content: `
      <svg
        width="30"
        height="42"
        viewBox="0 0 30 42"
        role="img"
        aria-label="${isMerged ? `묶인 음식점 ${mergedCount}곳` : escapeHtml(restaurant.name)}"
        style="display:block; filter:drop-shadow(0 8px 14px rgba(29,41,57,0.28)); pointer-events:none;"
      >
        <path
          d="M15 40.5C12.5 35.7 2.5 25.1 2.5 15.1C2.5 7.7 8.1 2 15 2C21.9 2 27.5 7.7 27.5 15.1C27.5 25.1 17.5 35.7 15 40.5Z"
          fill="${color}"
          stroke="${stroke}"
          stroke-width="2"
        />
        <circle cx="15" cy="15" r="${isMerged ? 7.5 : 5.25}" fill="#fff" opacity="0.96" />
        ${isMerged ? `
          <text
            x="15"
            y="18"
            fill="#1d2939"
            font-size="${countFontSize}"
            font-weight="900"
            text-anchor="middle"
          >${countLabel}</text>
        ` : ""}
      </svg>
    `,
    size: new naver.maps.Size(30, 42),
    anchor: new naver.maps.Point(15, 40),
  };
}

function clusterIcon(cluster, index) {
  const count = cluster.restaurants.length;
  const countLabel = count > 999 ? "999+" : String(count);
  const active = cluster.restaurants.some((restaurant) => restaurant.id === state.selectedId);
  const clusterAttribute = count > 1 ? `data-map-cluster-index="${index}"` : "";
  const ariaLabel = count > 1
    ? `묶인 음식점 ${count}곳 목록 보기`
    : `${cluster.restaurants[0]?.name || "음식점"} 선택`;
  return {
    content: `
      <button
        type="button"
        class="naver-cluster-marker${active ? " active" : ""}"
        ${clusterAttribute}
        aria-label="${escapeHtml(ariaLabel)}"
      >
        <strong>${countLabel}</strong>
      </button>
    `,
    size: new naver.maps.Size(50, 50),
    anchor: new naver.maps.Point(25, 25),
  };
}

function sortedClusterRestaurants(cluster) {
  return [...cluster.restaurants].sort((left, right) => (
    Number(right.visit_count || 0) - Number(left.visit_count || 0)
    || Number(right.average_rating || 0) - Number(left.average_rating || 0)
    || String(left.name || "").localeCompare(String(right.name || ""), "ko")
  ));
}

function clusterListMarkup(cluster) {
  const restaurants = sortedClusterRestaurants(cluster);
  return `
    <section
      class="map-cluster-popover"
      role="dialog"
      aria-label="이 핀에 묶인 음식점 ${restaurants.length}곳"
    >
      <header class="map-cluster-head">
        <div>
          <strong>이 핀의 음식점</strong>
          <span>${restaurants.length}곳</span>
        </div>
        <button type="button" data-close-map-cluster aria-label="목록 닫기">×</button>
      </header>
      <ol class="map-cluster-list">
        ${restaurants.map((restaurant) => `
          <li>
            <button type="button" data-cluster-restaurant-id="${restaurant.id}">
              <strong>${escapeHtml(restaurant.name)}</strong>
              <small>
                ${escapeHtml(restaurant.category_label || restaurant.category || "기타")}
                · 방문 ${Number(restaurant.visit_count || 0)}회
                ${restaurant.average_rating ? ` · ${Number(restaurant.average_rating).toFixed(1)}점` : ""}
              </small>
            </button>
          </li>
        `).join("")}
      </ol>
    </section>
  `;
}

function handleClusterListInteraction(event) {
  const target = event.target;
  if (!(target instanceof Element)) return false;
  if (target.closest("[data-close-map-cluster]")) {
    event.preventDefault();
    event.stopPropagation();
    closeClusterInfoWindow();
    if (state.fallbackClusterIndex !== null) {
      state.fallbackClusterIndex = null;
      renderFallbackMap();
    }
    return true;
  }
  const restaurantButton = target.closest("[data-cluster-restaurant-id]");
  if (!restaurantButton) return false;
  event.preventDefault();
  event.stopPropagation();
  const restaurantId = Number(restaurantButton.dataset.clusterRestaurantId);
  if (!Number.isFinite(restaurantId) || restaurantId <= 0) return true;
  closeClusterInfoWindow();
  state.fallbackClusterIndex = null;
  selectRestaurant(restaurantId).catch((error) => console.error(error));
  return true;
}

function clusterPopoverOffset(marker) {
  const mapNode = document.querySelector("#map");
  if (!state.map || !mapNode || mapNode.clientWidth < 720) {
    return new naver.maps.Point(0, -10);
  }
  try {
    const markerOffset = state.map.getProjection().fromCoordToOffset(marker.getPosition());
    const markerX = Number(markerOffset?.x);
    const direction = Number.isFinite(markerX) && markerX > mapNode.clientWidth / 2 ? -1 : 1;
    return new naver.maps.Point(direction * 184, 8);
  } catch {
    return new naver.maps.Point(184, 8);
  }
}

function openNaverClusterList(cluster, marker) {
  if (!state.map || !window.naver?.maps || cluster.restaurants.length < 2) return;
  if (state.clusterInfoWindow && state.openClusterMarker === marker) return;
  closeClusterInfoWindow();
  const infoWindow = new naver.maps.InfoWindow({
    content: clusterListMarkup(cluster),
    backgroundColor: "transparent",
    borderWidth: 0,
    anchorSize: new naver.maps.Size(0, 0),
    pixelOffset: clusterPopoverOffset(marker),
    zIndex: 400,
  });
  state.clusterInfoWindow = infoWindow;
  state.openClusterMarker = marker;
  infoWindow.open(state.map, marker);
}

function renderNaverMarkers(options = {}) {
  if (!state.map || !window.naver?.maps) return;
  const zoom = Number(state.map.getZoom?.() || 12);
  const numericMode = zoom <= numericClusterMaxZoom;
  const markerRestaurants = restaurantsInNaverMarkerViewport(state.restaurants);
  const viewportKey = [zoom, ...markerRestaurants.map((restaurant) => restaurant.id)].join(":");
  if (options.skipIfUnchanged && viewportKey === state.markerViewportKey) return;
  clearNaverMarkers();
  const clusters = clustersForNaverMap(markerRestaurants, zoom);
  state.markerViewportKey = viewportKey;
  state.markers = clusters.map((cluster, index) => {
    const isCluster = cluster.restaurants.length > 1;
    const restaurant = cluster.restaurants[0];
    const active = cluster.restaurants.some((item) => item.id === state.selectedId);
    const marker = new naver.maps.Marker({
      position: new naver.maps.LatLng(cluster.latitude, cluster.longitude),
      map: state.map,
      title: isCluster ? `묶인 음식점 ${cluster.restaurants.length}곳` : restaurant.name,
      icon: numericMode
        ? clusterIcon(cluster, index)
        : markerIcon(restaurant, cluster.restaurants.length, active),
      zIndex: active ? 220 : (numericMode ? 120 : 100),
    });
    if (isCluster) {
      state.clusterEntries[index] = { cluster, marker };
      naver.maps.Event.addListener(marker, "click", () => openNaverClusterList(cluster, marker));
    } else {
      naver.maps.Event.addListener(marker, "click", () => selectRestaurant(restaurant.id));
    }
    return marker;
  });
}

function bindNaverMapEvents() {
  if (!state.map || state.mapEventsBound) return;
  state.mapEventsBound = true;
  naver.maps.Event.addListener(state.map, "idle", () => {
    renderNaverMarkers({ skipIfUnchanged: true });
    renderRanking({ skipIfUnchanged: true });
  });
  naver.maps.Event.addListener(state.map, "dragstart", closeClusterInfoWindow);
  naver.maps.Event.addListener(state.map, "click", closeClusterInfoWindow);
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
  state.fallbackClusterIndex = null;
  bindNaverMapEvents();
  renderNaverMarkers();
  renderRanking();
}

function renderFallbackMap() {
  const mapNode = document.querySelector("#map");
  const mapWidth = Math.max(320, mapNode.clientWidth || window.innerWidth || 1200);
  const mapHeight = Math.max(480, mapNode.clientHeight || window.innerHeight || 800);
  const clusters = buildRestaurantClusters(
    state.restaurants,
    (restaurant) => {
      const point = fallbackPoint(restaurant);
      return {
        x: point.left * mapWidth / 100,
        y: point.top * mapHeight / 100,
      };
    },
  );
  state.fallbackClusters = clusters;
  const openCluster = clusters[state.fallbackClusterIndex];
  mapNode.innerHTML = `
    <div class="fallback-map"${fallbackMapStyle()}>
      <div class="water-label">BUSAN</div>
      ${clusters.map((cluster, index) => {
        const left = cluster.x * 100 / mapWidth;
        const top = cluster.y * 100 / mapHeight;
        const isCluster = cluster.restaurants.length > 1;
        const restaurant = cluster.restaurants[0];
        const active = cluster.restaurants.some((item) => item.id === state.selectedId);
        return `
          <button
            type="button"
            class="map-marker map-marker-cluster ${active ? "active" : ""}"
            style="left:${left}%; top:${top}%"
            ${isCluster ? `data-fallback-cluster-index="${index}"` : `data-id="${restaurant.id}"`}
            aria-label="${isCluster ? `묶인 음식점 ${cluster.restaurants.length}곳 목록 보기` : `${escapeHtml(restaurant.name)} 선택`}"
          >
            <strong>${cluster.restaurants.length > 999 ? "999+" : cluster.restaurants.length}</strong>
          </button>
        `;
      }).join("")}
      ${openCluster?.restaurants?.length > 1 ? `
        <div
          class="fallback-cluster-popover${openCluster.x > mapWidth / 2 ? " opens-left" : ""}"
          style="left:${openCluster.x * 100 / mapWidth}%; top:${openCluster.y * 100 / mapHeight}%"
        >
          ${clusterListMarkup(openCluster)}
        </div>
      ` : ""}
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

function waitForDetailPanelSlide(panel) {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let slideTimer;
    const finishSlide = () => {
      window.clearTimeout(slideTimer);
      panel.removeEventListener("transitionend", handleSlideTransitionEnd);
      resolve();
    };
    const handleSlideTransitionEnd = (event) => {
      if (event.target === panel && event.propertyName === "transform") finishSlide();
    };
    panel.addEventListener("transitionend", handleSlideTransitionEnd);
    slideTimer = window.setTimeout(finishSlide, 360);
  });
}

function slideDetailPanelSnapshotOut(panel, stage) {
  stage.querySelectorAll(".detail-panel-snapshot").forEach((snapshot) => snapshot.remove());
  const snapshot = panel.cloneNode(true);
  snapshot.removeAttribute("id");
  snapshot.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
  snapshot.classList.remove("detail-entering", "detail-closing", "detail-resetting");
  snapshot.classList.add("detail-panel-snapshot");
  snapshot.setAttribute("aria-hidden", "true");
  snapshot.inert = true;
  stage.append(snapshot);
  requestAnimationFrame(() => {
    snapshot.classList.add("detail-closing");
    waitForDetailPanelSlide(snapshot).then(() => snapshot.remove());
  });
}

async function selectRestaurant(id, options = {}) {
  const restoreExpanded = options.expanded === true;
  const panel = document.querySelector("#detail-panel");
  const stage = panel.closest(".map-stage");
  const previousSelectedId = state.selectedId;
  const selectionRequest = ++state.detailSelectionRequest;
  const restaurant = await fetchJson(`/api/restaurants/${id}`);
  if (selectionRequest !== state.detailSelectionRequest) return;
  const animatePanelEntry = options.animate === true
    && (panel.hidden || previousSelectedId !== id);
  const switchingRestaurant = animatePanelEntry
    && !panel.hidden
    && previousSelectedId !== null
    && previousSelectedId !== id;
  if (switchingRestaurant) {
    slideDetailPanelSnapshotOut(panel, stage);
  }
  state.selectedId = id;
  renderRanking();
  const visits = Array.isArray(restaurant.visits) ? restaurant.visits : [];
  const aiSummary = restaurant.ai_summary || {};
  const restaurantImages = Array.isArray(restaurant.restaurant_images)
    ? restaurant.restaurant_images
    : [];
  const aiSummaryText = String(aiSummary.text || "").trim();
  const currentReviewCount = Number(aiSummary.current_review_count || restaurant.review_count || 0);
  const summarizedReviewCount = Number(aiSummary.summarized_review_count || 0);
  const aiSummaryMessage = aiSummaryText || (
    currentReviewCount < 5
      ? `공개 리뷰가 5개 모이면 첫 AI 요약이 제공됩니다. 현재 ${currentReviewCount}개입니다.`
      : "AI 요약을 준비하고 있습니다. 생성 또는 재시도 후에는 최소 1시간 동안 같은 요약을 사용합니다."
  );
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
  panel.hidden = false;
  panel.classList.remove("detail-expanded", "detail-entering");
  if (animatePanelEntry) {
    panel.classList.add("detail-resetting", "detail-entering");
    panel.classList.remove("detail-closing");
    void panel.offsetHeight;
    panel.classList.remove("detail-resetting");
    requestAnimationFrame(() => panel.classList.remove("detail-entering"));
  } else {
    panel.classList.remove("detail-closing");
  }
  stage?.classList.remove("detail-expanded");
  stage?.classList.add("detail-open");
  panel.innerHTML = `
    <div class="detail-panel-controls">
      <button
        type="button"
        class="save-restaurant-button${restaurant.is_saved ? " is-saved" : ""}"
        aria-pressed="${restaurant.is_saved ? "true" : "false"}"
        aria-label="${restaurant.is_saved ? "관심 가게 저장 해제" : "관심 가게 저장"}"
        title="${restaurant.is_saved ? "관심 가게 저장 해제" : "관심 가게 저장"}"
      >
        <span aria-hidden="true">${restaurant.is_saved ? "♥" : "♡"}</span>
      </button>
      <button
        type="button"
        class="detail-expand-toggle"
        aria-expanded="false"
        aria-controls="visit-history ai-review-summary detail-review-entry visit-reviews"
      >
        <span class="detail-expand-label">방문 정보 펼치기</span>
      </button>
    </div>
    <button type="button" class="panel-close" aria-label="닫기">×</button>
    <section class="detail-summary">
      <div class="detail-title-line">
        <h2>${escapeHtml(restaurant.name)}</h2>
        <div class="detail-stats">
          <span class="detail-category-label" title="${escapeHtml(restaurant.category_detail_label || restaurant.category_label)}">
            ${escapeHtml(restaurant.category_detail_label || restaurant.category_label)}
          </span>
          <span class="detail-stat-divider" aria-hidden="true">|</span>
          <span>${restaurant.visit_count}회</span>
          <span class="detail-stat-divider" aria-hidden="true">|</span>
          <span>${restaurant.average_rating ? restaurant.average_rating.toFixed(1) : "-"}점</span>
        </div>
      </div>
      <p class="detail-address">${escapeHtml(restaurant.road_address || restaurant.address)}</p>
      <div class="detail-primary-actions">
        <a class="naver-map-link" href="${escapeHtml(naverSearchUrl(restaurant))}" target="_blank" rel="noopener">
          네이버 지도에서 보기
        </a>
      </div>
    </section>
    <section id="restaurant-photo" class="restaurant-photo-card">
      <div class="restaurant-photo-head">
        <div class="restaurant-photo-title">
          <h3>사진</h3>
          <a class="restaurant-photo-add" href="/restaurants/${id}/photos/add">사진 등록</a>
        </div>
        <span>${restaurantImages.length}장</span>
      </div>
      ${restaurantImages.length ? `
        <div class="restaurant-photo-grid" data-image-count="${restaurantImages.length}">
          ${restaurantImages.map((image) => `
            <figure class="restaurant-photo-item">
              <a class="restaurant-photo-link" href="${escapeHtml(image.source_url)}" target="_blank" rel="noopener">
                <img class="restaurant-photo-image" src="${escapeHtml(image.thumbnail_url)}" alt="${escapeHtml(image.alt_text || restaurant.name)}" loading="lazy">
                <span class="restaurant-photo-badge">사용자 등록</span>
              </a>
            </figure>
          `).join("")}
        </div>
      ` : `
        <div class="restaurant-photo-empty">
          <p>여러분의 밥상을 등록해 주세요</p>
          <a href="/restaurants/${id}/photos/add">사진 추가하기 &gt;</a>
        </div>
      `}
    </section>
    <section id="visit-history" class="visit-history" hidden>
      <div class="visit-history-head">
        <h3>방문 기록</h3>
        <span>${visits.length}건</span>
      </div>
      <div class="visit-table-scroll">
        <table class="visit-table">
          <thead>
            <tr>
              <th scope="col">방문일시</th>
              <th scope="col">방문 기관</th>
              <th scope="col">원문 상호명</th>
              <th scope="col">방문 사유</th>
            </tr>
          </thead>
          <tbody id="visit-table-body"></tbody>
        </table>
      </div>
      <nav class="visit-pagination" aria-label="방문 기록 페이지" hidden>
        <button type="button" data-visit-page="previous">이전</button>
        <span class="visit-page-status" aria-live="polite"></span>
        <button type="button" data-visit-page="next">다음</button>
      </nav>
    </section>
    <section id="ai-review-summary" class="ai-summary-card" hidden>
      <div class="ai-summary-head">
        <div>
          <span class="ai-summary-kicker">AI SUMMARY</span>
          <h3>AI 방문 요약</h3>
        </div>
        ${aiSummaryText ? `
          <span class="ai-summary-count">리뷰 ${summarizedReviewCount}개 시점</span>
        ` : `
          <span class="ai-summary-count">${Math.min(currentReviewCount, 5)} / 5</span>
        `}
      </div>
      <p class="ai-summary-text">${escapeHtml(aiSummaryMessage)}</p>
      <div class="ai-summary-meta">
        <span>공개 방문 리뷰를 바탕으로 작성된 AI 요약입니다.</span>
        ${aiSummary.last_generated_at ? `
          <time datetime="${escapeHtml(aiSummary.last_generated_at)}">
            ${escapeHtml(formatVisitDateTime(aiSummary.last_generated_at))} 갱신
          </time>
        ` : ""}
        ${aiSummary.refresh_pending ? `
          <span class="ai-summary-pending">새 리뷰 반영 대기</span>
        ` : ""}
      </div>
    </section>
    <section id="detail-review-entry" class="detail-review-entry" hidden>
      <h3>리뷰 남기기</h3>
      <form id="review-form" class="review-form">
        <select class="review-rating-select" name="rating" aria-label="별점">
          <option value="5">★ × 5</option>
          <option value="4">★ × 4</option>
          <option value="3">★ × 3</option>
          <option value="2">★ × 2</option>
          <option value="1">★ × 1</option>
        </select>
        <input
          name="reviewer_label"
          value="${window.IS_SIGNED_IN ? escapeHtml(window.CURRENT_USER_DISPLAY_NAME || "사용자") : ""}"
          placeholder="${window.IS_SIGNED_IN ? "" : "닉네임"}"
          maxlength="40"
          ${window.IS_SIGNED_IN ? "disabled" : ""}
        >
        <textarea name="body" placeholder="리뷰" rows="3"></textarea>
        <label class="review-consent">
          <input type="checkbox" name="ai_processing_consent" value="1" required>
          <span>공개 리뷰 본문의 AI 요약 처리를 확인하고 동의합니다. 개인정보는 입력하지 마세요. <a href="/privacy" target="_blank" rel="noopener noreferrer">자세히 보기</a></span>
        </label>
        <button type="submit">등록</button>
      </form>
    </section>
    <section id="visit-reviews" class="reviews" hidden>
      <h3>방문 리뷰</h3>
      <div class="review-list">
        ${restaurant.reviews.map((review) => `
          <article>
            <div class="visit-review-meta">
              <div class="review-author">
                <div class="review-identity">
                  <strong class="review-nickname">${escapeHtml(review.reviewer_label)}</strong>
                  <span class="reviewer-review-count">리뷰 ${Number(review.reviewer_review_count || 0)}개</span>
                </div>
                ${reviewStars(review.rating)}
              </div>
            </div>
            <p>${escapeHtml(review.body)}</p>
            <div class="review-actions">
              <button
                type="button"
                class="review-reaction-button${review.current_reaction === "up" ? " is-active" : ""}"
                data-review="${review.id}"
                data-reaction="up"
                aria-label="추천 ${Number(review.recommendation_count || 0)}개"
                aria-pressed="${review.current_reaction === "up" ? "true" : "false"}"
                title="추천"
              >👍 <span class="review-reaction-count">${Number(review.recommendation_count || 0)}</span></button>
              <span class="review-action-separator" aria-hidden="true">|</span>
              <button
                type="button"
                class="review-reaction-button${review.current_reaction === "down" ? " is-active" : ""}"
                data-review="${review.id}"
                data-reaction="down"
                aria-label="비추천 ${Number(review.not_recommended_count || 0)}개"
                aria-pressed="${review.current_reaction === "down" ? "true" : "false"}"
                title="비추천"
              >👎 <span class="review-reaction-count">${Number(review.not_recommended_count || 0)}</span></button>
              <span class="review-action-separator" aria-hidden="true">|</span>
              <button type="button" data-review="${review.id}" class="report-button">신고하기</button>
            </div>
          </article>
        `).join("") || "<p class=\"empty\">등록된 리뷰가 없습니다.</p>"}
      </div>
    </section>
  `;
  const expandToggle = panel.querySelector(".detail-expand-toggle");
  const visitHistory = panel.querySelector("#visit-history");
  const aiReviewSummary = panel.querySelector("#ai-review-summary");
  const detailReviewEntry = panel.querySelector("#detail-review-entry");
  const visitReviews = panel.querySelector("#visit-reviews");
  const visitTableBody = panel.querySelector("#visit-table-body");
  const visitPagination = panel.querySelector(".visit-pagination");
  const visitPageStatus = panel.querySelector(".visit-page-status");
  const visitPageSize = 5;
  const visitPageCount = Math.max(1, Math.ceil(visits.length / visitPageSize));
  let visitPage = 1;
  const renderVisitPage = () => {
    const start = (visitPage - 1) * visitPageSize;
    const pageVisits = visits.slice(start, start + visitPageSize);
    visitTableBody.innerHTML = pageVisits.map((visit) => `
      <tr>
        <td>${escapeHtml(formatVisitDateTime(visit.visited_at))}</td>
        <td>${escapeHtml(visit.institution_name || "-")}</td>
        <td>${escapeHtml(visit.source_place_name || "-")}</td>
        <td>${escapeHtml(visit.purpose || "-")}</td>
      </tr>
    `).join("") || `
      <tr>
        <td colspan="4" class="visit-empty">공개된 방문 기록이 없습니다.</td>
      </tr>
    `;
    const isPaginated = visits.length > visitPageSize;
    visitHistory.classList.toggle("is-paginated", isPaginated);
    visitPagination.hidden = !isPaginated;
    visitPageStatus.textContent = `${visitPage} / ${visitPageCount}`;
    panel.querySelector('[data-visit-page="previous"]').disabled = visitPage === 1;
    panel.querySelector('[data-visit-page="next"]').disabled = visitPage === visitPageCount;
  };
  renderVisitPage();
  panel.querySelector('[data-visit-page="previous"]').addEventListener("click", () => {
    if (visitPage <= 1) return;
    visitPage -= 1;
    renderVisitPage();
  });
  panel.querySelector('[data-visit-page="next"]').addEventListener("click", () => {
    if (visitPage >= visitPageCount) return;
    visitPage += 1;
    renderVisitPage();
  });
  const setDetailExpanded = (expanded) => {
    expandToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    panel.classList.toggle("detail-expanded", expanded);
    stage?.classList.toggle("detail-expanded", expanded);
    visitHistory.hidden = !expanded;
    aiReviewSummary.hidden = !expanded;
    detailReviewEntry.hidden = !expanded;
    visitReviews.hidden = !expanded;
    panel.querySelector(".detail-expand-label").textContent = expanded
      ? "간단히 보기"
      : "방문 정보 펼치기";
  };
  expandToggle.addEventListener("click", () => {
    setDetailExpanded(expandToggle.getAttribute("aria-expanded") !== "true");
  });
  if (restoreExpanded) setDetailExpanded(true);
  panel.querySelector(".panel-close").addEventListener("click", async () => {
    if (panel.classList.contains("detail-closing")) return;
    state.detailSelectionRequest += 1;
    stage?.querySelectorAll(".detail-panel-snapshot").forEach((snapshot) => snapshot.remove());
    panel.classList.add("detail-closing");
    await waitForDetailPanelSlide(panel);
    panel.hidden = true;
    panel.classList.remove("detail-expanded", "detail-entering", "detail-closing");
    stage?.classList.remove("detail-open", "detail-expanded");
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
    const keepExpanded = panel.classList.contains("detail-expanded");
    const form = new FormData(event.currentTarget);
    await fetchJson(`/api/restaurants/${id}/reviews`, {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    await loadRestaurants();
    await selectRestaurant(id, { expanded: keepExpanded });
  });
  panel.querySelector(".save-restaurant-button").addEventListener("click", async (event) => {
    if (!window.IS_SIGNED_IN) {
      const returnTo = `/?restaurant_id=${id}`;
      window.location.href = `/login?return_to=${encodeURIComponent(returnTo)}`;
      return;
    }
    const button = event.currentTarget;
    const isSaved = button.getAttribute("aria-pressed") === "true";
    const result = await fetchJson(
      `/api/restaurants/${id}/${isSaved ? "unsave" : "save"}`,
      { method: "POST", body: "{}" },
    );
    button.classList.toggle("is-saved", result.is_saved);
    button.setAttribute("aria-pressed", result.is_saved ? "true" : "false");
    const label = result.is_saved ? "관심 가게 저장 해제" : "관심 가게 저장";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.querySelector("span").textContent = result.is_saved ? "♥" : "♡";
  });
  panel.querySelectorAll(".review-reaction-button").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.IS_SIGNED_IN) {
        const returnTo = `/?restaurant_id=${id}`;
        window.location.href = `/login?return_to=${encodeURIComponent(returnTo)}`;
        return;
      }
      const article = button.closest("article");
      const reactionButtons = article.querySelectorAll(".review-reaction-button");
      reactionButtons.forEach((item) => { item.disabled = true; });
      try {
        const result = await fetchJson(`/api/reviews/${button.dataset.review}/reaction`, {
          method: "POST",
          body: JSON.stringify({ reaction: button.dataset.reaction }),
        });
        reactionButtons.forEach((item) => {
          const active = item.dataset.reaction === result.reaction;
          const count = item.dataset.reaction === "up"
            ? result.recommendation_count
            : result.not_recommended_count;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
          item.setAttribute("aria-label", `${item.dataset.reaction === "up" ? "추천" : "비추천"} ${count}개`);
          item.querySelector(".review-reaction-count").textContent = String(count);
        });
      } finally {
        reactionButtons.forEach((item) => { item.disabled = false; });
      }
    });
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
  selectRestaurant(Number(button.dataset.id), { animate: true }).catch((error) => console.error(error));
});
document.addEventListener("click", handleClusterListInteraction, true);
document.querySelector("#map").addEventListener("click", (event) => {
  const naverClusterMarker = event.target.closest("[data-map-cluster-index]");
  if (naverClusterMarker) {
    event.stopPropagation();
    const entry = state.clusterEntries[Number(naverClusterMarker.dataset.mapClusterIndex)];
    if (entry) openNaverClusterList(entry.cluster, entry.marker);
    return;
  }
  const fallbackClusterMarker = event.target.closest("[data-fallback-cluster-index]");
  if (fallbackClusterMarker) {
    event.stopPropagation();
    state.fallbackClusterIndex = Number(fallbackClusterMarker.dataset.fallbackClusterIndex);
    renderFallbackMap();
    return;
  }
  const marker = event.target.closest(".map-marker");
  if (!marker) return;
  selectRestaurant(Number(marker.dataset.id)).catch((error) => console.error(error));
});
document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.filter === "category") {
      state.filters.category = button.dataset.value || "";
      state.filters.savedOnly = false;
    }
    if (button.dataset.filter === "min_visit_count") {
      state.filters.minVisitCount = button.dataset.value || "";
      button.closest("details")?.removeAttribute("open");
    }
    if (button.dataset.filter === "saved") {
      if (!window.IS_SIGNED_IN) {
        window.location.href = "/login?return_to=/?saved_only=1";
        return;
      }
      state.filters.savedOnly = !state.filters.savedOnly;
      state.filters.category = "";
    }
    updateFilterIndex();
    loadRestaurants().catch((error) => console.error(error));
  });
});

if (state.filters.savedOnly && !window.IS_SIGNED_IN) {
  window.location.replace("/login?return_to=/?saved_only=1");
}

updateFilterIndex();
loadRestaurants()
  .then(async () => {
    const requestedUrl = new URL(window.location.href);
    const requestedId = Number(requestedUrl.searchParams.get("restaurant_id"));
    if (requestedId > 0) {
      requestedUrl.searchParams.delete("restaurant_id");
      window.history.replaceState(
        {},
        "",
        `${requestedUrl.pathname}${requestedUrl.search}${requestedUrl.hash}`,
      );
      await selectRestaurant(requestedId);
    }
  })
  .catch((error) => {
    document.querySelector("#ranking-list").innerHTML = `<li class="empty">${escapeHtml(error.message)}</li>`;
  });
