#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo-web 부하 생성기: 평소 손님(기본 트래픽)을 흉내 낸다. 표준 라이브러리만 쓴다.

  python3 loadgen.py                 # 약 25 rps 로 계속 (Ctrl-C 로 종료)
  python3 loadgen.py 40              # 약 40 rps
  python3 loadgen.py 25 --spike 110 --at 15 --for 20   # 15초 뒤 20초 동안 110 rps 로 급증

자기 컴퓨터의 demo-web 서버(localhost)에만 쓴다. 남의 서버에 쓰지 않는다.
"""
import argparse
import math
import random
import sys
import threading
import time
import urllib.request


def hit(url):
    try:
        urllib.request.urlopen(url, timeout=2).read()
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rps", nargs="?", type=float, default=25)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--spike", type=float, default=0, help="급증 때의 rps")
    ap.add_argument("--at", type=float, default=15, help="급증 시작 (초)")
    ap.add_argument("--for", dest="dur", type=float, default=20, help="급증 길이 (초)")
    args = ap.parse_args()
    url = "http://127.0.0.1:%d/api/ping" % args.port  # localhost 고정
    print("loadgen: %s 에 약 %g rps%s" % (url, args.rps, (" · %g초 뒤 %g초 동안 %g rps" % (args.at, args.dur, args.spike)) if args.spike else ""))
    t0, acc, last = time.time(), 0.0, time.time()
    try:
        while True:
            time.sleep(0.05)
            now = time.time()
            t = now - t0
            rate = args.rps * (1 + 0.25 * math.sin(t / 7.0)) + random.uniform(-1, 1)  # 살짝 출렁이게
            if args.spike and args.at <= t <= args.at + args.dur:
                rate = args.spike
            acc += max(0.0, rate) * (now - last)
            last = now
            while acc >= 1:
                acc -= 1
                threading.Thread(target=hit, args=(url,), daemon=True).start()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
