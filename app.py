# -*- coding: utf-8 -*-
"""
app.py — 大湾区天气地图的本地服务

用法：python app.py
浏览器打开 http://127.0.0.1:8765

页面和 /api/weather 同源。Key 只从 .env 读取，不会写进响应。
缓存未过期时直接返回，不重复请求 OpenWeatherMap。
"""

import json
import math
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from fetch_weather import (
    API_KEY,
    CITIES,
    collect_weather,
    load_cached_cities,
    load_meta,
    refresh_cooldown,
    refresh_interval,
    sanitize,
    store_result,
)

HOST = "127.0.0.1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "static", "index.html")


def parse_time(text):
    if not isinstance(text, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


class WeatherCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.cities = load_cached_cities()
        meta = load_meta()
        fetched = meta.get("fetched_at") if isinstance(meta.get("fetched_at"), str) else ""
        if not parse_time(fetched):
            stamps = [city.get("timestamp") for city in self.cities]
            parsed = [parse_time(stamp) for stamp in stamps]
            parsed = [item for item in parsed if item]
            fetched = max(parsed).strftime("%Y-%m-%d %H:%M:%S") if parsed else ""
        self.fetched_at = fetched
        warning = meta.get("warning") if isinstance(meta.get("warning"), str) else ""
        self.warning = warning.strip()
        failed = meta.get("failed") if isinstance(meta.get("failed"), list) else []
        self.failed = [str(name) for name in failed if str(name).strip()]
        self.stale = bool(self.cities and self.warning)
        self.refreshing = False
        self.fetching = False
        self.last_external = 0.0
        self.waiters = []
        age = self.age_seconds()
        interval = refresh_interval()
        if age is None:
            self.next_refresh = time.monotonic()
        else:
            self.next_refresh = time.monotonic() + max(0.0, interval - age)

    def age_seconds(self):
        when = parse_time(self.fetched_at)
        if when is None:
            return None
        return max(0.0, (datetime.now() - when).total_seconds())

    def _manual_wait(self):
        if not self.last_external:
            return 0
        left = refresh_cooldown() - (time.monotonic() - self.last_external)
        return max(0, int(math.ceil(left)))

    def _due(self):
        return time.monotonic() >= self.next_refresh

    def _begin(self):
        if self.fetching:
            return False
        self.fetching = True
        self.refreshing = True
        self.last_external = time.monotonic()
        threading.Thread(target=self._run, name="weather-refresh", daemon=True).start()
        return True

    def _run(self):
        try:
            result = collect_weather()
        except Exception as exc:
            result = {
                "cities": [],
                "failed": [city_cn for _city, city_cn, _lat, _lon in CITIES],
                "errors": {},
                "error": "拉取中断：" + sanitize(str(exc))[:180],
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        with self.lock:
            try:
                stored = store_result(self.cities, result, self.fetched_at)
                self.cities = stored["cities"]
                self.failed = stored["failed"]
                self.warning = stored["warning"]
                self.stale = stored["stale"]
                self.fetched_at = stored["fetched_at"]
            finally:
                self.fetching = False
                self.refreshing = False
                self.next_refresh = time.monotonic() + refresh_interval()
                waiters = self.waiters
                self.waiters = []
        fresh = len(result.get("cities") or [])
        print(
            f"刷新结束：本轮成功 {fresh} 个，失败 {len(result.get('failed') or [])} 个，"
            f"数据时间 {self.fetched_at or '无'}"
        )
        for event in waiters:
            event.set()

    def snapshot(self, message="", throttled=False):
        left = self.next_refresh - time.monotonic()
        seconds_until = 0 if self.refreshing else max(0, int(math.ceil(left)))
        return {
            "ok": bool(self.cities) or self.refreshing,
            "stale": self.stale,
            "refreshing": self.refreshing,
            "throttled": throttled,
            "message": sanitize(message),
            "warning": self.warning,
            "source": "OpenWeatherMap",
            "fetched_at": self.fetched_at,
            "refresh_seconds": refresh_interval(),
            "cooldown_seconds": refresh_cooldown(),
            "seconds_until_refresh": seconds_until,
            "seconds_until_manual": self._manual_wait(),
            "failed": list(self.failed),
            "cities": [dict(city) for city in self.cities],
        }

    def ensure(self, force=False):
        waiter = None
        with self.lock:
            message = ""
            throttled = False
            if force and self._manual_wait() > 0 and not self.fetching:
                throttled = True
                remain = self._manual_wait()
                when = self.fetched_at or "上一次"
                message = (
                    f"刚刚请求过天气接口，请 {remain} 秒后再刷新，"
                    f"以免连续打满 {len(CITIES)} 个城市。地图仍显示 {when} 的数据。"
                )
            elif not self.fetching and (force or self._due()):
                self._begin()
                if force or not self.cities:
                    waiter = threading.Event()
                    self.waiters.append(waiter)
            elif self.fetching and (force or not self.cities):
                waiter = threading.Event()
                self.waiters.append(waiter)
            payload = self.snapshot(message=message, throttled=throttled)
        if waiter is not None:
            waiter.wait(timeout=180)
            with self.lock:
                payload = self.snapshot()
        return payload


CACHE = WeatherCache()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_index()
            return
        if path == "/api/weather":
            self._send_json(CACHE.ensure(force=False))
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(min(length, 8192))
        if path == "/api/refresh":
            self._send_json(CACHE.ensure(force=True))
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _send_index(self):
        try:
            with open(INDEX_PATH, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send(500, "找不到 static/index.html".encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _send_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if API_KEY:
            body = body.replace(API_KEY.encode("utf-8"), b"***")
        self._send(200, body, "application/json; charset=utf-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve():
    port = int(os.getenv("PORT") or "8765")
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        raise SystemExit(f"端口 {port} 无法监听：{exc}") from exc

    def loop():
        while True:
            time.sleep(5)
            CACHE.ensure(force=False)

    threading.Thread(target=loop, name="weather-schedule", daemon=True).start()
    print(f"天气地图 http://{HOST}:{port}")
    print(
        f"自动刷新间隔 {refresh_interval()} 秒，手动刷新最短间隔 {refresh_cooldown()} 秒。"
        "缓存未过期时不请求外部接口。"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
