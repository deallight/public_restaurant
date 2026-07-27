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
    savedOnly: new URLSearchParams(window.location.search).get("saved_only") === "1",
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
  const visits = Array.isArray(restaurant.visits) ? restaurant.visits : [];
  const restaurantImages = (
    Array.isArray(restaurant.restaurant_images)
      ? restaurant.restaurant_images
      : (restaurant.restaurant_image ? [restaurant.restaurant_image] : [])
  ).filter((image) => image?.thumbnail_url && image?.source_url).slice(0, 4);
  const hasRestaurantImages = restaurantImages.length > 0;
  const adminRestaurantImageCount = restaurantImages.filter((image) => image.is_admin_image).length;
  const searchedRestaurantImageCount = restaurantImages.length - adminRestaurantImageCount;
  const aiSummary = restaurant.ai_summary || {};
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
  const panel = document.querySelector("#detail-panel");
  const stage = panel.closest(".map-stage");
  panel.hidden = false;
  panel.classList.remove("detail-expanded");
  stage?.classList.remove("detail-expanded");
  stage?.classList.add("detail-open");
  panel.innerHTML = `
    <button type="button" class="panel-close" aria-label="닫기">×</button>
    <section class="detail-summary">
      <h2>
        <button
          type="button"
          class="detail-expand-toggle"
          aria-expanded="false"
          aria-controls="visit-history ai-review-summary restaurant-photo detail-review-entry visit-reviews"
        >
          <span>${escapeHtml(restaurant.name)}</span>
          <small class="detail-expand-label">방문 정보 펼치기</small>
        </button>
      </h2>
      <p class="detail-address">${escapeHtml(restaurant.road_address || restaurant.address)}</p>
      <div class="detail-stats">
        <span>${restaurant.category_label}</span>
        <span>${restaurant.visit_count}회</span>
        <span>${restaurant.average_rating ? restaurant.average_rating.toFixed(1) : "-"}점</span>
      </div>
      <div class="detail-primary-actions">
        <a class="naver-map-link" href="${escapeHtml(naverSearchUrl(restaurant))}" target="_blank" rel="noopener">
          네이버 지도에서 보기
        </a>
        <button
          type="button"
          class="save-restaurant-button${restaurant.is_saved ? " is-saved" : ""}"
          aria-pressed="${restaurant.is_saved ? "true" : "false"}"
        >
          <span aria-hidden="true">${restaurant.is_saved ? "♥" : "♡"}</span>
          ${restaurant.is_saved ? "저장됨" : "관심 가게 저장"}
        </button>
      </div>
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
    <section id="restaurant-photo" class="restaurant-photo-card">
      <div class="restaurant-photo-head">
        <h3>음식점 사진</h3>
        <span>${adminRestaurantImageCount ? "관리자 등록 사진 우선" : "네이버 이미지 검색 결과"}</span>
      </div>
      ${hasRestaurantImages ? `
        <div class="restaurant-photo-grid" data-image-count="${restaurantImages.length}">
          ${restaurantImages.map((image, index) => `
            <article class="restaurant-photo-item">
              <a
                class="restaurant-photo-link"
                href="${escapeHtml(image.source_url)}"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="${escapeHtml(restaurant.name)} 사진 ${index + 1} 원본 보기"
              >
                <img
                  class="restaurant-photo-image"
                  src="${escapeHtml(image.thumbnail_url)}"
                  alt="${escapeHtml(image.title || `${restaurant.name} 사진 ${index + 1}`)}"
                  loading="lazy"
                  referrerpolicy="no-referrer"
                >
                ${image.is_admin_image ? `
                  <span class="restaurant-photo-badge admin">관리자 등록</span>
                ` : image.is_naver_place_image ? `
                  <span class="restaurant-photo-badge">플레이스 이미지</span>
                ` : ""}
              </a>
            </article>
          `).join("")}
        </div>
        <div class="restaurant-photo-meta">
          <span>${adminRestaurantImageCount ? `등록 ${adminRestaurantImageCount}장${searchedRestaurantImageCount ? ` · 검색 ${searchedRestaurantImageCount}장` : ""}` : `검색 결과 ${searchedRestaurantImageCount}장`}</span>
          <span>${adminRestaurantImageCount ? "관리자 사진 다음에 검색 사진을 표시합니다." : "사진을 누르면 원본을 확인할 수 있습니다."}</span>
        </div>
        <p class="restaurant-photo-empty" hidden>
          사진을 불러오지 못했습니다.
        </p>
      ` : `
        <p class="restaurant-photo-empty">
          가게명과 일치하는 네이버 이미지 검색 결과가 없습니다.
        </p>
      `}
    </section>
    <section id="detail-review-entry" class="detail-review-entry" hidden>
      <h3>리뷰 남기기</h3>
      <form id="review-form" class="review-form">
        <select name="rating" aria-label="별점">
          <option value="5">5점</option>
          <option value="4">4점</option>
          <option value="3">3점</option>
          <option value="2">2점</option>
          <option value="1">1점</option>
        </select>
        <input
          name="reviewer_label"
          placeholder="${window.IS_SIGNED_IN ? "로그인 이름으로 등록" : "닉네임"}"
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
            <div><b>${review.rating}점</b><span>${escapeHtml(review.reviewer_label)}</span></div>
            <p>${escapeHtml(review.body)}</p>
            <button type="button" data-review="${review.id}" class="report-button">신고</button>
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
        <td>${escapeHtml(visit.purpose || "-")}</td>
      </tr>
    `).join("") || `
      <tr>
        <td colspan="3" class="visit-empty">공개된 방문 기록이 없습니다.</td>
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
  const restaurantPhotoItems = [...panel.querySelectorAll(".restaurant-photo-item")];
  const updateRestaurantPhotoFallback = () => {
    if (!restaurantPhotoItems.length) return;
    const hasVisiblePhoto = restaurantPhotoItems.some((item) => !item.hidden);
    panel.querySelector(".restaurant-photo-grid").hidden = !hasVisiblePhoto;
    panel.querySelector(".restaurant-photo-meta").hidden = !hasVisiblePhoto;
    panel.querySelector(".restaurant-photo-empty").hidden = hasVisiblePhoto;
  };
  restaurantPhotoItems.forEach((item) => {
    item.querySelector(".restaurant-photo-image").addEventListener("error", () => {
      item.hidden = true;
      updateRestaurantPhotoFallback();
    });
  });
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
  expandToggle.addEventListener("click", () => {
    const expanded = expandToggle.getAttribute("aria-expanded") !== "true";
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
  });
  panel.querySelector(".panel-close").addEventListener("click", () => {
    panel.hidden = true;
    panel.classList.remove("detail-expanded");
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
    const form = new FormData(event.currentTarget);
    await fetchJson(`/api/restaurants/${id}/reviews`, {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    await loadRestaurants();
    await selectRestaurant(id);
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
    button.innerHTML = `<span aria-hidden="true">${result.is_saved ? "♥" : "♡"}</span> ${result.is_saved ? "저장됨" : "관심 가게 저장"}`;
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
    const requestedId = Number(new URLSearchParams(window.location.search).get("restaurant_id"));
    if (requestedId > 0) await selectRestaurant(requestedId);
  })
  .catch((error) => {
    document.querySelector("#ranking-list").innerHTML = `<li class="empty">${escapeHtml(error.message)}</li>`;
  });
