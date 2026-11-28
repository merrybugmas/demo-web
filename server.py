#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo-web 서버: 정적 파일 + 일부러 고장 난 API 몇 개. 표준 라이브러리만 쓴다.

  python3 server.py          # http://localhost:8765
  python3 server.py 9000     # 다른 포트

고장 난 API (프론트엔드가 이걸 부르다 진짜 에러를 낸다)
  GET /api/gifts            200, 그런데 약속한 items 필드가 빠진 응답
  GET /api/notice           200, JSON 이어야 하는데 HTML 이 온다
  GET /api/member/info      503
  POST /api/payment/confirm 500

서버 버그 (서버 안에서 진짜 Python 예외가 난다 → 500, SENTRY_DSN 이 있으면 Sentry 로 보낸다: sentry_server.py)
  GET /api/coupon/apply     KeyError: 'rate'           쿠폰 데이터는 percent 인데 rate 를 읽는다 (apply_coupon)
  GET /api/reviews/summary  ZeroDivisionError          리뷰가 0건인 상품의 평균 별점 (average_rating)
  GET /api/stock            ConnectionRefusedError     데모에 없는 재고 서버에 연결한다 (check_stock, 코드로 못 고친다)

트래픽
  GET /api/ping             200 (부하용 가벼운 요청)
  GET /metrics              Prometheus 형식 (/metrics 자신은 빼고 센다)
                              http_requests_total{status="2xx|3xx|4xx|5xx"}   받은 요청 수
                              http_request_duration_seconds_sum / _count     서버가 잰 처리 시간
                              web_vitals_*                                   브라우저가 보고한 Web Vitals 의 p75
                            MERRY BUGMAS 대시보드(--live)가 이 값을 읽어 줄·5xx 비율·응답 시간·Web Vitals 를 보여 준다
  POST /api/vitals          페이지가 측정한 Web Vitals(LCP·CLS·INP·TTFB) 보고

/api/gifts 는 느린 DB 를 흉내 내 80~160ms 걸린다 (응답 시간 지표가 0 이 아니게). /api/ping 도 20ms.

처리 용량 (트래픽이 몰리면 실제로 밀리게)
  /api/* 는 동시에 capacity 개(기본 2, DEMO_CAPACITY)까지만 처리한다. 자리가 없으면 1초까지 기다리다 503(과부하).
  2 슬롯이면 약 65 rps 가 한계라 loadgen --spike 110 이면 응답 시간과 5xx 가 실제로 오른다.

레버 (MERRY BUGMAS 대시보드의 엘프 에이전트가 당긴다, localhost 에서만)
  GET  /admin/levers        {"cache": false, "capacity": 2, "waitroom": false, ...}
  POST /admin/levers        {"cache": true} · {"capacity": 4} · {"waitroom": true} 중 바꿀 것만
    cache    = /api/gifts 응답 캐시 (80~160ms → 수 ms)
    capacity = 동시 처리 슬롯 수 (1~16, 계산대 수)
    waitroom = 번호표: 자리가 0.15초 안에 안 나면 기다리게 두지 않고 바로 429 (5xx 대신)
  /metrics 에 demo_capacity · demo_inflight · demo_waiting · demo_cache_enabled · demo_waitroom_enabled
  · demo_rejected_total{reason="overload|waitroom"} 이 같이 나간다. /admin 요청은 세지 않는다.
"""
import json
import os
import random
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import sentry_server

ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.Lock()
STATUS = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}  # 상태 코드 묶음별 요청 수
DURATION = {"sum": 0.0, "count": 0}  # 처리 시간(초) 합 · 건수
VITALS = {"lcp": [], "cls": [], "inp": [], "ttfb": []}  # 최근 보고 50건씩 (LCP·INP·TTFB 는 초)
VITAL_MAX = {"lcp": 60.0, "cls": 10.0, "inp": 60.0, "ttfb": 60.0}  # 이보다 크면 잘못된 보고로 보고 버린다
REPORTS = {"n": 0}
LEVERS = {"cache": False, "capacity": max(1, min(16, int(os.environ.get("DEMO_CAPACITY", "2") or 2))), "waitroom": False}
SLOTS = {"inflight": 0, "waiting": 0}
REJECTED = {"overload": 0, "waitroom": 0}
SLOT_CV = threading.Condition()
OVERLOAD_WAIT, WAITROOM_WAIT = 1.0, 0.15  # 자리를 기다리는 최대 시간(초): 평소 · 번호표 켜짐
REPORTER = None  # 서버 에러 → Sentry (main 에서 SENTRY_DSN 으로 만든다. None 이면 안 보낸다)

# 서버 버그 재료
CART = [{"name": "진저브레드 쿠키", "price": 4500, "qty": 2}, {"name": "유리 오너먼트", "price": 12000, "qty": 1}]
COUPONS = {"XMAS10": {"code": "XMAS10", "percent": 10}}  # 할인율 키는 percent (rate 가 아니다)
REVIEWS = {"g1": [5, 4, 5], "g9": []}  # g9 는 아직 리뷰가 없다
STOCK_PORT = int(os.environ.get("DEMO_STOCK_PORT", "8799") or 8799)  # 재고 서버 (데모에는 없다)


def apply_coupon(cart, coupon):
    """쿠폰을 적용한 합계."""
    subtotal = sum(i["price"] * i["qty"] for i in cart)
    return {"coupon": coupon["code"], "subtotal": subtotal,
            "total": round(subtotal * (1 - coupon["rate"]))}  # 버그: 쿠폰에는 rate 가 없다 → KeyError


def average_rating(gift_id):
    """상품의 평균 별점."""
    scores = REVIEWS.get(gift_id, [])
    return {"gift": gift_id, "count": len(scores),
            "average": round(sum(scores) / len(scores), 2)}  # 버그: 리뷰 0건이면 0 으로 나눈다


def check_stock(gift_id):
    """재고 서버에 물어본다. 데모에는 재고 서버가 없어 연결이 거절된다 (코드 버그가 아니라 인프라 문제)."""
    with socket.create_connection(("127.0.0.1", STOCK_PORT), timeout=0.5) as conn:
        conn.sendall(("STOCK %s\n" % gift_id).encode())
        return {"gift": gift_id, "stock": conn.recv(64).decode(errors="replace").strip()}


def take_slot():
    """처리 슬롯 하나를 얻는다. 못 얻으면 None 대신 거절 이유("overload"/"waitroom")를 돌려준다."""
    with SLOT_CV:
        limit = WAITROOM_WAIT if LEVERS["waitroom"] else OVERLOAD_WAIT
        end = time.monotonic() + limit
        SLOTS["waiting"] += 1
        try:
            while SLOTS["inflight"] >= LEVERS["capacity"]:
                left = end - time.monotonic()
                if left <= 0:
                    reason = "waitroom" if LEVERS["waitroom"] else "overload"
                    REJECTED[reason] += 1
                    return reason
                SLOT_CV.wait(left)
            SLOTS["inflight"] += 1
            return None
        finally:
            SLOTS["waiting"] -= 1


def give_slot():
    with SLOT_CV:
        SLOTS["inflight"] -= 1
        SLOT_CV.notify()


def set_levers(data):
    """POST /admin/levers 본문을 반영한다. 모르는 키나 이상한 값은 ValueError."""
    if not isinstance(data, dict) or not data:
        raise ValueError("바꿀 레버가 없음")
    new = dict(LEVERS)
    for k, v in data.items():
        if k in ("cache", "waitroom") and isinstance(v, bool):
            new[k] = v
        elif k == "capacity" and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 16:
            new[k] = v
        else:
            raise ValueError("레버 값이 이상함: %s=%r" % (k, v))
    with SLOT_CV:
        LEVERS.update(new)
        SLOT_CV.notify_all()  # 슬롯이 늘었으면 기다리던 요청을 깨운다
    return lever_state()


def lever_state():
    with SLOT_CV:
        return dict(LEVERS, inflight=SLOTS["inflight"], waiting=SLOTS["waiting"], rejected=dict(REJECTED))


def p75(values):
    v = sorted(values)
    return v[min(len(v) - 1, int(0.75 * len(v)))] if v else None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_body(self, status, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # 요청마다 한 줄씩 찍으면 부하 때 느려진다
        pass

    def send_response(self, code, message=None):
        self.status = code  # 이 요청이 무슨 상태 코드로 끝났는지 기억해 둔다
        super().send_response(code, message)

    def measured(self, fn):
        """요청 하나를 처리하고 상태 코드 묶음과 처리 시간을 기록한다."""
        t0, self.status = time.perf_counter(), 200
        try:
            fn()
        finally:
            cls = "%dxx" % (self.status // 100)
            with LOCK:
                STATUS[cls if cls in STATUS else "5xx"] += 1
                DURATION["sum"] += time.perf_counter() - t0
                DURATION["count"] += 1

    def metrics(self):
        with LOCK:
            out = ["# HELP http_requests_total Total HTTP requests received.", "# TYPE http_requests_total counter"]
            out += ['http_requests_total{status="%s"} %d' % kv for kv in sorted(STATUS.items())]
            out += ["# HELP http_request_duration_seconds Time spent handling requests.",
                    "# TYPE http_request_duration_seconds summary",
                    "http_request_duration_seconds_sum %.6f" % DURATION["sum"],
                    "http_request_duration_seconds_count %d" % DURATION["count"],
                    "# HELP web_vitals_reports_total Web Vitals reports received from browsers.",
                    "# TYPE web_vitals_reports_total counter", "web_vitals_reports_total %d" % REPORTS["n"]]
            for key, name in (("lcp", "web_vitals_lcp_seconds"), ("cls", "web_vitals_cls"),
                              ("inp", "web_vitals_inp_seconds"), ("ttfb", "web_vitals_ttfb_seconds")):
                v = p75(VITALS[key])
                if v is not None:  # 보고가 없으면 아예 내보내지 않는다 (0 으로 꾸미지 않는다)
                    out += ["# TYPE %s gauge" % name, "%s %.4f" % (name, v)]
        st = lever_state()
        out += ["# HELP demo_capacity Concurrent request slots (lever).", "# TYPE demo_capacity gauge",
                "demo_capacity %d" % st["capacity"],
                "# TYPE demo_inflight gauge", "demo_inflight %d" % st["inflight"],
                "# TYPE demo_waiting gauge", "demo_waiting %d" % st["waiting"],
                "# TYPE demo_cache_enabled gauge", "demo_cache_enabled %d" % st["cache"],
                "# TYPE demo_waitroom_enabled gauge", "demo_waitroom_enabled %d" % st["waitroom"],
                "# TYPE demo_rejected_total counter"]
        out += ['demo_rejected_total{reason="%s"} %d' % kv for kv in sorted(st["rejected"].items())]
        return self.send_body(200, "\n".join(out) + "\n", "text/plain; version=0.0.4; charset=utf-8")

    def vitals(self):
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 2000)
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            assert isinstance(data, dict)
        except Exception:  # noqa: BLE001
            return self.send_body(400, json.dumps({"error": "bad report"}))
        with LOCK:
            REPORTS["n"] += 1
            for key in VITALS:
                v = data.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= VITAL_MAX[key]:
                    VITALS[key] = (VITALS[key] + [float(v)])[-50:]
        return self.send_body(200, '{"ok":true}')

    def admin(self):
        """레버 조회·변경. 같은 컴퓨터(127.0.0.1)에서 온 요청만 받는다."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_body(403, json.dumps({"error": "localhost only"}))
        if self.command == "POST":
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 2000))
                st = set_levers(json.loads(self.rfile.read(n).decode("utf-8") or "{}"))
            except (ValueError, UnicodeDecodeError) as e:
                return self.send_body(400, json.dumps({"error": str(e)}, ensure_ascii=False))
            print("lever: %s" % json.dumps({k: st[k] for k in ("cache", "capacity", "waitroom")}))
            return self.send_body(200, json.dumps(st))
        return self.send_body(200, json.dumps(lever_state()))

    def api(self):
        """API 하나. 처리 슬롯을 얻어야 처리하고, 못 얻으면 503(과부하) 또는 429(번호표)."""
        path = self.path.split("?")[0]
        if path == "/api/vitals":  # 브라우저 보고는 슬롯 없이 받는다
            return self.vitals()
        busy = take_slot()
        if busy == "waitroom":
            return self.send_body(429, json.dumps({"error": "busy, please retry", "retry_after": 1}))
        if busy == "overload":
            return self.send_body(503, json.dumps({"error": "overloaded"}))
        try:
            return self.handle_api(path)
        except Exception as e:  # noqa: BLE001 — 처리 안 된 서버 에러: 로그 한 줄 + Sentry, 손님에게는 500
            print("server error: %s %s - %s: %s" % (self.command, path, type(e).__name__, e), file=sys.stderr)
            if REPORTER is not None:
                REPORTER.capture(e, self.command, path)
            return self.send_body(500, json.dumps({"error": "internal server error"}))
        finally:
            give_slot()

    def handle_api(self, path):
        if path == "/api/ping":
            time.sleep(0.02)  # 가벼운 요청도 조금은 걸린다 (슬롯이 꽉 차는 게 보이도록)
            return self.send_body(200, '{"ok":true}')
        if path == "/api/gifts":
            time.sleep(0.003 if LEVERS["cache"] else random.uniform(0.08, 0.16))  # 느린 DB 흉내 (캐시면 거의 즉시)
            # 버그 재료: 문서에는 {"items": [...]} 라고 했지만 total 만 준다
            return self.send_body(200, json.dumps({"total": 3}))
        if path == "/api/notice":
            return self.send_body(200, "<!DOCTYPE html><html><body>점검 중입니다</body></html>", "text/html; charset=utf-8")
        if path == "/api/member/info":
            return self.send_body(503, json.dumps({"error": "service unavailable"}))
        if path == "/api/payment/confirm":
            return self.send_body(500, json.dumps({"error": "internal server error"}))
        if path == "/api/coupon/apply":
            return self.send_body(200, json.dumps(apply_coupon(CART, COUPONS["XMAS10"]), ensure_ascii=False))
        if path == "/api/reviews/summary":
            return self.send_body(200, json.dumps(average_rating("g9")))
        if path == "/api/stock":
            return self.send_body(200, json.dumps(check_stock("g1")))
        return self.send_body(404, json.dumps({"error": "not found"}))

    def do_GET(self):
        if self.path.split("?")[0] == "/metrics":
            return self.metrics()
        if self.path.split("?")[0] == "/admin/levers":
            return self.admin()
        self.measured(self.api if self.path.startswith("/api/") else super().do_GET)

    def do_POST(self):
        if self.path.split("?")[0] == "/admin/levers":
            return self.admin()
        self.measured(self.api)


def main():
    global REPORTER
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    REPORTER = sentry_server.Reporter()
    print("demo-web: http://localhost:%d  (Ctrl-C 로 종료)" % port)
    print(REPORTER.describe())
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
