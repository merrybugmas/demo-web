# 🎁 Bugmas Gift Shop — demo-web

[MERRY BUGMAS](https://github.com/merrybugmas/merrybugmas) 대시보드의 **Sentry 실연동 시연용 데모 웹앱**이다.
일부러 버그를 심어 둔 작은 상점 페이지로, 버튼을 누르면 진짜 브라우저 에러가 나고 Sentry 로 간다.
대시보드를 `--live` 로 띄우면 그 에러가 쥐로 나타나고 병정(AI 에이전트)이 판정한다.
이 서버가 받는 요청 수도 대시보드의 상점 앞 줄이 된다.

```
버그    브라우저에서 버튼 클릭 → 실제 JS 에러 → Sentry (테스트 프로젝트 merrybugmas/web) → 대시보드 --live → 쥐
서버    버튼 → 서버 API 안에서 실제 Python 예외 → server.py 가 Sentry 로 보냄 (같은 프로젝트, SENTRY_DSN) → 쥐
트래픽  서버가 받은 요청 수 → /metrics (Prometheus 형식) → 대시보드 --live → 초당 요청 수 → 줄 길이
```

## 실행

```bash
python3 server.py            # http://localhost:8765  (표준 라이브러리만, 의존성 없음)
SENTRY_DSN='https://<공개키>@<호스트>/<프로젝트번호>' python3 server.py   # 서버 에러도 Sentry 로 (아래 "서버 버그")
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

### 서버 버그

| 버튼 | 서버에서 나는 에러 | 성격 |
|---|---|---|
| 쿠폰 적용 | `KeyError: 'rate'` — 쿠폰 데이터는 `percent` 인데 `rate` 를 읽는다 (`server.py` `apply_coupon`) | fixable (서버 코드 버그) |
| 평균 별점 | `ZeroDivisionError: division by zero` — 리뷰 0건인 상품의 평균 (`average_rating`) | fixable |
| 재고 확인 | `ConnectionRefusedError` — 데모에 없는 재고 서버(`DEMO_STOCK_PORT`, 기본 8799)에 연결 (`check_stock`) | needs-human (인프라) |

- 서버 API(`/api/coupon/apply` · `/api/reviews/summary` · `/api/stock`) 안에서 진짜 Python 예외가 난다. 서버는 콘솔(stderr)에 한 줄 남기고 손님에게 500 을 준다
- `SENTRY_DSN` 이 있으면 `sentry_server.py` 가 그 예외를 Sentry 로 보낸다 (표준 라이브러리로 envelope API 에 직접, sentry-sdk 없음). 브라우저 에러와 같은 프로젝트(merrybugmas/web)의 DSN 을 쓰면 대시보드가 따로 설정 없이 쥐로 읽는다
  - DSN 은 Sentry → 프로젝트 설정 → Client Keys (DSN) 에 있다. 코드에 넣지 않고 환경변수로만 준다. 없으면 보내지 않는다
  - 보내는 것: 예외 종류·메시지·스택(파일·줄·함수·코드 한 줄), 요청 메서드와 경로, 태그 `service=WEB` · `layer=server`. 요청 본문·헤더·쿼리·IP 는 보내지 않는다
  - 같은 에러는 10초에 한 번만 보낸다 (부하 때 폭주 방지). Sentry 로 못 보내도 응답은 그대로 나가고 콘솔에 한 줄만 남는다
- 브라우저는 응답 상태만 로그에 보여 주고 에러를 던지지 않는다. 던지면 같은 버그가 브라우저 쥐로 하나 더 생긴다

"성격"은 만든 사람이 의도한 분류다. 대시보드의 AI 병정은 이 표를 보지 않고 Sentry 이슈 제목만으로 판정한다.

에러는 코드에서 `captureException` 으로 직접 보내지 않는다. 그냥 터지게 두고, Sentry 브라우저 SDK 가
전역 에러·처리 안 된 Promise 거부로 잡아 간다 (`app.js`). 고장 난 API 는 `server.py` 가 흉내 낸다.

## 트래픽

- `GET /metrics` 는 Prometheus 형식으로 아래를 준다. `/metrics` 자신은 세지 않는다.
  - `http_requests_total{status="2xx|3xx|4xx|5xx"}` — 받은 요청 수 (대시보드: rps · 5xx 비율)
  - `http_request_duration_seconds_sum` / `_count` — 서버가 잰 처리 시간 (대시보드: 평균 응답 시간). `/api/gifts` 는 느린 DB 를 흉내 내 80~160ms 걸린다
  - `web_vitals_lcp_seconds` · `web_vitals_cls` · `web_vitals_inp_seconds` · `web_vitals_ttfb_seconds` — 아래 Web Vitals 의 p75
- **Web Vitals**: 페이지(`app.js`)가 브라우저의 `PerformanceObserver` 로 LCP · CLS · INP · TTFB 를 실제로 재서 `POST /api/vitals` 로 보고한다. 서버는 최근 50건의 p75 를 낸다. LCP · CLS · INP 는 Chromium 계열에서만 측정되고, INP 는 사람이 실제로 클릭해야 생긴다. 보고가 없는 항목은 내보내지 않는다. 탭을 뒤에 둔 채 열면 화면이 늦게 그려져 LCP 가 크게 나온다
- 5xx 비율을 올려 보려면 `python3 loadgen.py 25 --errors 0.15` (요청의 15% 를 503 나는 API 로).
- `loadgen.py` 가 평소 트래픽을 만든다. `python3 loadgen.py 25 --spike 110 --at 15 --for 20` 처럼 급증도 넣을 수 있다. localhost 전용이다.
- **처리 용량**: `/api/*` 는 동시에 2개(`DEMO_CAPACITY`)까지만 처리하고 `/api/ping` 도 20ms 걸린다. 약 65 rps 가 한계라
  `--spike 110` 이면 응답 시간과 5xx(자리를 1초 못 얻으면 503 overloaded)가 **실제로** 오른다.
- **레버** (`GET`·`POST /admin/levers`, localhost 에서만): MERRY BUGMAS 대시보드의 엘프 에이전트가 당긴다.
  `{"cache": true}`(/api/gifts 캐시) · `{"capacity": 4}`(슬롯 1~16) · `{"waitroom": true}`(번호표: 0.15초 안에 자리가 없으면 429).
  상태는 `/metrics` 의 `demo_capacity` · `demo_inflight` · `demo_waiting` · `demo_cache_enabled` · `demo_waitroom_enabled` · `demo_rejected_total{reason}`.
  110 rps 급증에서 측정: 그대로 5xx 15% · 769ms → 캐시 0% · 46ms → 캐시+슬롯 4 0% · 25ms / 번호표만 5xx 0% · 429 35%
- 페이지의 **크리스마스 특가 오픈** 버튼은 브라우저에서 20초 동안 약 90 rps 로 요청을 보낸다. 대시보드의 줄이 길어지고 인파 경보가 뜬다 (대시보드 `TRAFFIC_PEAK` 기본값 100 rps 기준).

## 알아 둘 것

- 시연용이다. 실제 서비스가 아니고, 에러도 일부러 낸 것이다.
- `index.html` 의 Sentry 키는 브라우저용 **공개 키**(DSN)다. 이벤트를 보내는 데만 쓰이고 읽을 수는 없다.
  자기 Sentry 프로젝트로 보내려면 로더 스크립트 주소의 키를 바꾼다.
- 광고 차단 확장이 Sentry 로 가는 요청을 막을 수 있다. 페이지 아래에 로더 연결 상태가 표시된다.
- Sentry 는 스택이 같은 에러를 한 이슈로 묶는다. 같은 버튼을 여러 번 눌러도 이슈(쥐)는 하나고 횟수만 늘어난다.

## 라이선스

MIT
