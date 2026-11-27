# 🎁 Bugmas Gift Shop — demo-web

[MERRY BUGMAS](https://github.com/merrybugmas/merrybugmas) 대시보드의 **Sentry 실연동 시연용 데모 웹앱**이다.
일부러 버그를 심어 둔 작은 상점 페이지로, 버튼을 누르면 진짜 브라우저 에러가 나고 Sentry 로 간다.
대시보드를 `--live` 로 띄우면 그 에러가 쥐로 나타나고 병정(AI 에이전트)이 판정한다.
이 서버가 받는 요청 수도 대시보드의 상점 앞 줄이 된다.

```
버그    브라우저에서 버튼 클릭 → 실제 JS 에러 → Sentry (테스트 프로젝트 merrybugmas/web) → 대시보드 --live → 쥐
트래픽  서버가 받은 요청 수 → /metrics (Prometheus 형식) → 대시보드 --live → 초당 요청 수 → 줄 길이
```

## 실행

```bash
python3 server.py            # http://localhost:8765  (표준 라이브러리만, 의존성 없음)
python3 loadgen.py           # (다른 터미널) 평소 손님: 약 25 rps 로 계속 요청
```

다른 터미널에서 대시보드:

```bash
cd ../merrybugmas && python3 xmas_market.py --live     # .env 에 SENTRY_AUTH_TOKEN · SENTRY_ORG · SENTRY_PROJECT (쥐), TRAFFIC_METRICS_URL=http://localhost:8765/metrics (줄)
```

브라우저에서 버튼을 누르면 수 초 뒤 Sentry 에 이슈가 생기고, 대시보드가 다음 읽기 주기(기본 15초)에 가져온다.

## 심어 둔 버그

| 버튼 | 실제로 나는 에러 | 성격 |
|---|---|---|
| 선물 목록 보기 | `TypeError: Cannot read properties of undefined (reading 'map')` — API 응답에 `items` 가 없는데 확인 없이 `.map()` | fixable (코드 버그) |
| 배송 예정일 표시 | `RangeError: Invalid time value` — 잘못된 날짜를 검사 없이 `toISOString()` | fixable |
| 장바구니 합계 | `TypeError: Cannot read properties of null (reading 'rate')` — null 쿠폰의 할인율 읽기 | fixable |
| 공지 불러오기 | `SyntaxError: Unexpected token '<'` — 점검 페이지(HTML)를 JSON 으로 파싱 | fixable |
| 회원 정보 조회 | `Error: API Error: 503 /api/member/info` — 백엔드가 503 | needs-human |
| 결제 확인 | `Error: API Error: 500 /api/payment/confirm` — 백엔드가 500 | needs-human |
| 광고 배너 불러오기 | `Error: Failed to load third-party script (…)` — 없는 외부 스크립트 | noise |

"성격"은 만든 사람이 의도한 분류다. 대시보드의 AI 병정은 이 표를 보지 않고 Sentry 이슈 제목만으로 판정한다.

에러는 코드에서 `captureException` 으로 직접 보내지 않는다. 그냥 터지게 두고, Sentry 브라우저 SDK 가
전역 에러·처리 안 된 Promise 거부로 잡아 간다 (`app.js`). 고장 난 API 는 `server.py` 가 흉내 낸다.

## 트래픽

- `GET /metrics` 는 `http_requests_total`(지금까지 받은 요청 수)을 Prometheus 형식으로 준다. `/metrics` 자신은 세지 않는다.
- `loadgen.py` 가 평소 트래픽을 만든다. `python3 loadgen.py 25 --spike 110 --at 15 --for 20` 처럼 급증도 넣을 수 있다. localhost 전용이다.
- 페이지의 **크리스마스 특가 오픈** 버튼은 브라우저에서 20초 동안 약 90 rps 로 요청을 보낸다. 대시보드의 줄이 길어지고 인파 경보가 뜬다 (대시보드 `TRAFFIC_PEAK` 기본값 100 rps 기준).

## 알아 둘 것

- 시연용이다. 실제 서비스가 아니고, 에러도 일부러 낸 것이다.
- `index.html` 의 Sentry 키는 브라우저용 **공개 키**(DSN)다. 이벤트를 보내는 데만 쓰이고 읽을 수는 없다.
  자기 Sentry 프로젝트로 보내려면 로더 스크립트 주소의 키를 바꾼다.
- 광고 차단 확장이 Sentry 로 가는 요청을 막을 수 있다. 페이지 아래에 로더 연결 상태가 표시된다.
- Sentry 는 스택이 같은 에러를 한 이슈로 묶는다. 같은 버튼을 여러 번 눌러도 이슈(쥐)는 하나고 횟수만 늘어난다.

## 라이선스

MIT
