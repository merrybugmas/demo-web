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
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))


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

    def api(self):
        path = self.path.split("?")[0]
        if path == "/api/gifts":
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
        if self.path.startswith("/api/"):
            return self.api()
        return super().do_GET()

    def do_POST(self):
        return self.api()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print("demo-web: http://localhost:%d  (Ctrl-C 로 종료)" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
