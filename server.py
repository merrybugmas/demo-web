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

트래픽
  GET /api/ping             200 (부하용 가벼운 요청)
  GET /metrics              Prometheus 형식 (/metrics 자신은 빼고 센다)
                              http_requests_total{status="2xx|3xx|4xx|5xx"}   받은 요청 수
                              http_request_duration_seconds_sum / _count     서버가 잰 처리 시간
                              web_vitals_*                                   브라우저가 보고한 Web Vitals 의 p75
                            MERRY BUGMAS 대시보드(--live)가 이 값을 읽어 줄·5xx 비율·응답 시간·Web Vitals 를 보여 준다
  POST /api/vitals          페이지가 측정한 Web Vitals(LCP·CLS·INP·TTFB) 보고

/api/gifts 는 느린 DB 를 흉내 내 80~160ms 걸린다 (응답 시간 지표가 0 이 아니게).
"""
import json
import os
import random
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.Lock()
STATUS = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}  # 상태 코드 묶음별 요청 수
DURATION = {"sum": 0.0, "count": 0}  # 처리 시간(초) 합 · 건수
VITALS = {"lcp": [], "cls": [], "inp": [], "ttfb": []}  # 최근 보고 50건씩 (LCP·INP·TTFB 는 초)
VITAL_MAX = {"lcp": 60.0, "cls": 10.0, "inp": 60.0, "ttfb": 60.0}  # 이보다 크면 잘못된 보고로 보고 버린다
REPORTS = {"n": 0}


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

    def api(self):
        path = self.path.split("?")[0]
        if path == "/api/ping":
            return self.send_body(200, '{"ok":true}')
        if path == "/api/vitals":
            return self.vitals()
        if path == "/api/gifts":
            time.sleep(random.uniform(0.08, 0.16))  # 느린 DB 흉내
            # 버그 재료: 문서에는 {"items": [...]} 라고 했지만 total 만 준다
            return self.send_body(200, json.dumps({"total": 3}))
        if path == "/api/notice":
            return self.send_body(200, "<!DOCTYPE html><html><body>점검 중입니다</body></html>", "text/html; charset=utf-8")
        if path == "/api/member/info":
            return self.send_body(503, json.dumps({"error": "service unavailable"}))
        if path == "/api/payment/confirm":
            return self.send_body(500, json.dumps({"error": "internal server error"}))
        return self.send_body(404, json.dumps({"error": "not found"}))

    def do_GET(self):
        if self.path.split("?")[0] == "/metrics":
            return self.metrics()
        self.measured(self.api if self.path.startswith("/api/") else super().do_GET)

    def do_POST(self):
        self.measured(self.api)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print("demo-web: http://localhost:%d  (Ctrl-C 로 종료)" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
