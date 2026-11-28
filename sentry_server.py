# -*- coding: utf-8 -*-
"""서버 에러 → Sentry. 표준 라이브러리만 쓴다 (sentry-sdk 없이 envelope API 로 직접 보낸다).

  SENTRY_DSN=https://<공개키>@<호스트>/<프로젝트번호> python3 server.py

- SENTRY_DSN 이 없거나 이상하면 아무것도 보내지 않는다 (서버는 그대로 돈다)
- 보내는 것: 예외 종류·메시지·스택(파일·줄·함수·코드 한 줄), 요청 메서드와 경로(쿼리 제외), 태그 service=WEB · layer=server.
  요청 본문·헤더·쿼리·IP·호스트 이름은 보내지 않는다
- 보내기는 백그라운드 스레드에서 한다 (제한 2초). 실패해도 응답은 그대로 나가고 stderr 에 한 줄만 남긴다
- 같은 에러(종류 + 우리 코드의 마지막 위치)는 10초에 한 번만 보낸다. 부하를 걸 때 Sentry 로 이벤트가 쏟아지지 않게
"""
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT = "demo-web-sentry/1.0"
TIMEOUT = 2.0
DEDUP_SEC = 10.0
TAGS = {"service": "WEB", "layer": "server"}


def parse_dsn(dsn):
    """DSN → (envelope 주소, 공개 키). 모양이 이상하면 ValueError."""
    u = urllib.parse.urlsplit((dsn or "").strip())
    path = u.path.rstrip("/")
    prefix, _, project = path.rpartition("/")
    if u.scheme not in ("http", "https") or not u.hostname or not u.username or not project.isdigit():
        raise ValueError("SENTRY_DSN 모양이 이상함 (https://<공개키>@<호스트>/<프로젝트번호>)")
    host = u.hostname + (":%d" % u.port if u.port else "")
    return "%s://%s%s/api/%s/envelope/" % (u.scheme, host, prefix, project), u.username


def _in_app(path):
    return os.path.abspath(path).startswith(ROOT + os.sep)


def frames_of(exc):
    """traceback → Sentry 스택 프레임 (바깥 호출부터, Sentry 순서 그대로)."""
    out = []
    for fs in traceback.extract_tb(exc.__traceback__):
        app = _in_app(fs.filename)
        out.append({"filename": os.path.relpath(fs.filename, ROOT) if app else os.path.basename(fs.filename),
                    "function": fs.name, "lineno": fs.lineno, "context_line": fs.line or "", "in_app": app})
    return out


def build_event(exc, method="", path="", release="demo-web@1.0.0", environment="demo"):
    t = type(exc)
    value = {"type": t.__name__, "value": str(exc), "stacktrace": {"frames": frames_of(exc)},
             "mechanism": {"type": "demo-web", "handled": False}}
    if t.__module__ not in ("builtins", "__main__"):
        value["module"] = t.__module__
    event = {"event_id": uuid.uuid4().hex, "timestamp": time.time(), "platform": "python", "level": "error",
             "logger": "demo-web.server", "release": release, "environment": environment, "tags": dict(TAGS),
             "exception": {"values": [value]}}
    if path:
        event["request"] = {"method": method, "url": path.split("?")[0]}
    return event


def envelope(event, dsn):
    item = json.dumps(event, ensure_ascii=False).encode("utf-8")
    head = {"event_id": event["event_id"], "dsn": dsn,
            "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return b"\n".join([json.dumps(head).encode("utf-8"),
                       json.dumps({"type": "event", "content_type": "application/json", "length": len(item)}).encode("utf-8"),
                       item]) + b"\n"


def _post(url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read()


class Reporter:
    """처리 안 된 서버 예외를 Sentry 로 보낸다. send 를 바꿔 끼우면 네트워크 없이 시험할 수 있다."""

    def __init__(self, dsn=None, send=None, clock=time.monotonic, dedup=DEDUP_SEC):
        self.dsn = (os.environ.get("SENTRY_DSN", "") if dsn is None else dsn).strip()
        self.url = self.key = None
        self.error = ""
        if self.dsn:
            try:
                self.url, self.key = parse_dsn(self.dsn)
            except ValueError as e:
                self.error = str(e)
        self.send, self.clock, self.dedup = send or _post, clock, dedup
        self.lock = threading.Lock()
        self.last, self.threads = {}, []
        self.stats = {"sent": 0, "skipped": 0, "failed": 0}

    @property
    def enabled(self):
        return self.url is not None

    def describe(self):
        if self.enabled:
            return "Sentry: 서버 에러를 보낸다 (%s)" % urllib.parse.urlsplit(self.url).hostname
        if self.error:
            return "Sentry: %s - 서버 에러는 보내지 않는다" % self.error
        return "Sentry: SENTRY_DSN 없음 - 서버 에러는 보내지 않는다 (콘솔에만 남김)"

    def capture(self, exc, method="", path=""):
        """보내기 시작했으면 True. 꺼져 있거나 10초 안에 같은 에러를 보냈으면 False."""
        if not self.enabled:
            return False
        frames = frames_of(exc)
        last = next((f for f in reversed(frames) if f["in_app"]), frames[-1] if frames else {})
        key = (type(exc).__name__, last.get("filename"), last.get("lineno"))
        now = self.clock()
        with self.lock:
            if now - self.last.get(key, -1e18) < self.dedup:
                self.stats["skipped"] += 1
                return False
            self.last[key] = now
        body = envelope(build_event(exc, method, path), self.dsn)
        headers = {"Content-Type": "application/x-sentry-envelope",
                   "X-Sentry-Auth": "Sentry sentry_version=7, sentry_key=%s, sentry_client=%s" % (self.key, CLIENT)}
        th = threading.Thread(target=self._deliver, args=(headers, body), daemon=True)
        with self.lock:
            self.threads = [t for t in self.threads if t.is_alive()] + [th]
        th.start()
        return True

    def _deliver(self, headers, body):
        try:
            self.send(self.url, headers, body)
            with self.lock:
                self.stats["sent"] += 1
        except Exception as e:  # noqa: BLE001 — Sentry 가 안 돼도 서버는 계속 돈다
            with self.lock:
                self.stats["failed"] += 1
            print("sentry: 전송 실패 (%s) - 응답은 그대로 나갔다" % type(e).__name__, file=sys.stderr)

    def flush(self, timeout=TIMEOUT + 1):
        """보내는 중인 것을 기다린다 (시험·종료용)."""
        with self.lock:
            threads = list(self.threads)
        for t in threads:
            t.join(timeout)
