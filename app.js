// Bugmas Gift Shop — 일부러 심어 둔 버그들.
// 각 시나리오는 흔한 프론트엔드 실수를 그대로 저지른다. 에러를 직접 Sentry 에 보내지 않고(captureException 없음)
// 그냥 터지게 두면, Sentry 브라우저 SDK 가 전역 에러·처리 안 된 Promise 거부로 잡아 간다.

const SCENARIOS = [
  {
    id: "gifts", kind: "fixable", title: "선물 목록 보기",
    desc: "API 응답에 items 가 없는데 확인 없이 .map() 을 부른다.",
    run: async () => {
      const res = await fetch("/api/gifts");
      const data = await res.json();
      return data.items.map((g) => g.name); // 버그: items 가 undefined
    },
  },
  {
    id: "date", kind: "fixable", title: "배송 예정일 표시",
    desc: "잘못된 날짜 문자열을 검사 없이 ISO 형식으로 바꾼다.",
    run: () => new Date("2026-12-25T25:61").toISOString(), // 버그: Invalid Date
  },
  {
    id: "cart", kind: "fixable", title: "장바구니 합계",
    desc: "장바구니가 비었을 때 null 인 쿠폰의 할인율을 읽는다.",
    run: () => {
      const cart = { items: [], coupon: null };
      return cart.items.length * (1 - cart.coupon.rate); // 버그: coupon 이 null
    },
  },
  {
    id: "notice", kind: "fixable", title: "공지 불러오기",
    desc: "점검 페이지(HTML)가 왔는데 상태·타입 확인 없이 JSON 으로 읽는다.",
    run: async () => {
      const res = await fetch("/api/notice");
      // 버그 수정: 응답의 Content-Type 을 확인해 JSON 이 아니면 HTML 을 JSON.parse 하지 않는다
      const ct = (res.headers.get("content-type") || "").toLowerCase();
      if (ct.includes("application/json")) return await res.json();
      const text = await res.text();
      try { return JSON.parse(text); } catch (e) { return { ok: false, error: "not json", body: text }; }
    },
  },
  {
    id: "member", kind: "human", title: "회원 정보 조회",
    desc: "백엔드가 503 을 준다. 프론트 코드로는 못 고친다.",
    run: () => fetchMemberInfo(),
  },
  {
    id: "pay", kind: "human", title: "결제 확인",
    desc: "결제 API 가 500 을 준다. 사람이 봐야 한다.",
    run: () => confirmPayment(),
  },
  {
    id: "ads", kind: "noise", title: "광고 배너 불러오기",
    desc: "존재하지 않는 외부 광고 스크립트를 부른다. 우리 코드 문제가 아니다.",
    run: () => loadScript("https://ads.bugmas-demo.invalid/banner.js"),
  },
];

const KIND_LABEL = { fixable: "fixable · 코드 버그", human: "needs-human · 백엔드", noise: "noise · 외부 출처" };

// 호출 지점마다 따로 던진다: Sentry 는 스택이 같으면 한 이슈로 묶기 때문에, API 별로 이슈가 나뉘게 한다
async function fetchMemberInfo() {
  const res = await fetch("/api/member/info");
  if (!res.ok) throw new Error(`API Error: ${res.status} /api/member/info`);
  return res.json();
}

async function confirmPayment() {
  const res = await fetch("/api/payment/confirm", { method: "POST" });
  if (!res.ok) throw new Error(`API Error: ${res.status} /api/payment/confirm`);
  return res.json();
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Failed to load third-party script (${new URL(src).host})`));
    document.head.appendChild(s);
  });
}

// 트래픽 급증: 일정 시간 동안 가벼운 요청을 초당 rps 번 보낸다. 서버의 /metrics 카운터가 그만큼 오른다.
let surge = null;
function startSurge(rps, seconds, btn) {
  if (surge) return;
  const label = btn.textContent;
  const until = Date.now() + seconds * 1000;
  let sent = 0;
  surge = setInterval(() => {
    for (let i = 0; i < rps / 10; i++) { fetch("/api/ping").catch(() => {}); sent++; }
    const left = Math.ceil((until - Date.now()) / 1000);
    btn.textContent = `급증 중… ${Math.max(0, left)}초 (${sent}건)`;
    if (Date.now() >= until) {
      clearInterval(surge); surge = null; btn.textContent = label;
      log(`🎄 특가 오픈 종료 — ${seconds}초 동안 ${sent}건 요청`);
    }
  }, 100);
  log(`🎄 크리스마스 특가 오픈! 약 ${rps} rps 로 ${seconds}초`);
}

// Web Vitals: 브라우저가 실제로 잰 값을 서버에 보고한다 (서버가 /metrics 로 내보내고 대시보드가 읽는다).
// LCP·CLS·INP 는 Chromium 계열에서만 측정된다. 못 잰 항목은 보내지 않는다.
const vitals = {};
function observe(type, onEntry, opts) {
  try {
    new PerformanceObserver((list) => list.getEntries().forEach(onEntry)).observe({ type, buffered: true, ...opts });
  } catch (e) { /* 이 브라우저는 이 항목을 지원하지 않는다 */ }
}
observe("largest-contentful-paint", (e) => { vitals.lcp = e.startTime / 1000; });
observe("layout-shift", (e) => { if (!e.hadRecentInput) vitals.cls = (vitals.cls || 0) + e.value; });
observe("event", (e) => { if (e.interactionId) vitals.inp = Math.max(vitals.inp || 0, e.duration / 1000); }, { durationThreshold: 16 });
{
  const nav = performance.getEntriesByType("navigation")[0];
  if (nav && nav.responseStart > 0) vitals.ttfb = nav.responseStart / 1000;
}
let vitalsSent = "";
function reportVitals() {
  if (vitals.lcp !== undefined && vitals.cls === undefined) vitals.cls = 0; // 화면이 그려졌고 밀림이 없었다
  const body = JSON.stringify(vitals);
  if (body === "{}" || body === vitalsSent) return;
  vitalsSent = body;
  if (!(navigator.sendBeacon && navigator.sendBeacon("/api/vitals", body))) {
    fetch("/api/vitals", { method: "POST", body, keepalive: true }).catch(() => {});
  }
}
setTimeout(reportVitals, 2500);
setInterval(reportVitals, 5000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") reportVitals(); });

const logEl = document.getElementById("log");
function log(line) {
  const t = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  logEl.textContent = `${t}  ${line}\n` + logEl.textContent;
}

// 전역 핸들러는 화면에 보여 주기만 한다. 기본 동작을 막지 않으므로 Sentry 도 같은 에러를 받는다.
window.addEventListener("error", (e) => log(`💥 ${e.error ? e.error.name + ": " + e.error.message : e.message}`));
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  log(`💥 ${r && r.name ? r.name + ": " + r.message : String(r)}`);
});

const cards = document.getElementById("cards");
for (const s of SCENARIOS) {
  const card = document.createElement("div");
  card.className = "card";
  const kindClass = s.kind === "human" ? "human" : s.kind;
  card.innerHTML = `<span class="tag ${kindClass}">${KIND_LABEL[s.kind]}</span><h2></h2><p></p>`;
  card.querySelector("h2").textContent = s.title;
  card.querySelector("p").textContent = s.desc;
  const btn = document.createElement("button");
  btn.textContent = "눌러서 버그 내기";
  // 핸들러 안에서 잡지 않는다: 동기 에러는 전역 error 로, async 에러는 unhandledrejection 으로 올라간다
  btn.addEventListener("click", () => s.run());
  card.appendChild(btn);
  cards.appendChild(card);
}

{
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<span class="tag traffic">트래픽 · 실제 요청</span><h2>크리스마스 특가 오픈</h2>
    <p>20초 동안 서버에 요청을 몰아 보낸다. 대시보드 트래픽 화면의 줄이 길어진다.</p>`;
  const btn = document.createElement("button");
  btn.textContent = "눌러서 손님 몰기";
  btn.addEventListener("click", () => startSurge(90, 20, btn));
  card.appendChild(btn);
  cards.appendChild(card);
}

document.getElementById("sdk").textContent = window.Sentry
  ? "Sentry 로더 연결됨 — 에러는 테스트 프로젝트 merrybugmas/web 으로 간다 (광고 차단 확장이 켜져 있으면 막힐 수 있다)."
  : "⚠ Sentry 로더를 못 불러왔다 (광고 차단 확장·네트워크 확인). 에러는 화면에만 보인다.";
