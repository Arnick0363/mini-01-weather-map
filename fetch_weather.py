# -*- coding: utf-8 -*-
"""
fetch_weather.py — 大湾区实时气象站点 · 数据拉取

按城市坐标请求 OpenWeatherMap，解析 JSON。
python app.py 会复用这里的逻辑并按间隔刷新；
python fetch_weather.py 仍把结果导出为 weather_data.csv。
"""

import csv
import json
import math
import os
import time
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------- 配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "weather_data.csv")
META_PATH = os.path.join(BASE_DIR, "weather_meta.json")

load_dotenv(os.path.join(BASE_DIR, ".env"))
API_KEY = os.getenv("API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# 用坐标请求，不用英文名检索。
# 「Zhongshan」曾被接口解析到雷州半岛附近（约 21.32°N, 110.58°E），标记会落出珠三角。
CITIES = [
    ("Guangzhou", "广州", 23.1291, 113.2644),
    ("Foshan", "佛山", 23.0218, 113.1219),
    ("Dongguan", "东莞", 23.0205, 113.7518),
    ("Shenzhen", "深圳", 22.5431, 114.0579),
    ("Zhongshan", "中山", 22.5170, 113.3926),
    ("Zhuhai", "珠海", 22.2710, 113.5767),
    ("Jiangmen", "江门", 22.5789, 113.0815),
    ("Huizhou", "惠州", 23.1116, 114.4152),
    ("Zhaoqing", "肇庆", 23.0472, 112.4655),
    ("Hong Kong", "香港", 22.3193, 114.1694),
    ("Macau", "澳门", 22.1987, 113.5439),
]

COLUMNS = [
    "city",
    "city_cn",
    "temp",
    "feels_like",
    "humidity",
    "pressure",
    "wind_speed",
    "wind_deg",
    "wind_gust",
    "weather",
    "visibility",
    "rain_1h",
    "snow_1h",
    "lat",
    "lon",
    "timestamp",
]

# 返回的测站若离请求点太远，仍把标记放在城市中心，避免再出现中山落点错误。
MAX_STATION_KM = 40
HEADERS = {"User-Agent": "mini-01-weather-map/1.0"}

# 当前天气不是秒级数据。默认约 10 分钟才向接口拉一轮，手动刷新最短间隔 30 秒。
# 在 .env 里调整：WEATHER_REFRESH_SECONDS、WEATHER_REFRESH_COOLDOWN。下限留 5 秒，便于临时改短验证。
DEFAULT_REFRESH_SECONDS = 600
DEFAULT_REFRESH_COOLDOWN = 30
NUMERIC_COLUMNS = {
    "temp",
    "feels_like",
    "humidity",
    "pressure",
    "wind_speed",
    "wind_deg",
    "wind_gust",
    "visibility",
    "rain_1h",
    "snow_1h",
    "lat",
    "lon",
}
INTEGER_COLUMNS = {"humidity", "pressure", "wind_deg", "visibility"}


class FetchError(Exception):
    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


def sanitize(text):
    """异常文本里可能带上完整请求 URL，先去掉 Key。"""
    if API_KEY:
        text = text.replace(API_KEY, "***")
    return text


def distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def fetch_one(city, city_cn, lat, lon):
    """拉取单个城市并解析为扁平字典。失败抛 FetchError，不把 Key 写进异常。"""
    params = {
        "lat": lat,
        "lon": lon,
        "appid": API_KEY,
        "units": "metric",
        "lang": "zh_cn",
    }
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
    except requests.exceptions.Timeout as exc:
        raise FetchError("请求超时") from exc
    except requests.exceptions.ConnectionError as exc:
        raise FetchError("连不上天气接口") from exc
    except requests.exceptions.RequestException as exc:
        raise FetchError(sanitize(str(exc))[:180]) from exc

    if resp.status_code == 401:
        raise FetchError("API Key 无效或尚未激活", fatal=True)
    if resp.status_code == 429:
        raise FetchError("请求过于频繁，已触发限流", fatal=True)
    if resp.status_code != 200:
        raise FetchError(f"天气接口返回 HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise FetchError("天气接口返回的不是 JSON") from exc

    cod = str(data.get("cod", 200))
    if cod != "200":
        message = sanitize(str(data.get("message") or f"接口返回 {cod}"))[:180]
        raise FetchError(message, fatal=cod in {"401", "403"})

    main = data.get("main") or {}
    if "temp" not in main:
        raise FetchError("JSON 里没有温度（main.temp）")

    wind = data.get("wind") or {}
    weather_list = data.get("weather") or []
    weather = weather_list[0].get("description") if weather_list else None
    rain = data.get("rain") or {}
    snow = data.get("snow") or {}
    coord = data.get("coord") or {}

    station_lat = coord.get("lat", lat)
    station_lon = coord.get("lon", lon)
    try:
        station_lat = float(station_lat)
        station_lon = float(station_lon)
        gap = distance_km(lat, lon, station_lat, station_lon)
    except (TypeError, ValueError):
        station_lat, station_lon = lat, lon
        gap = 0
    if gap > MAX_STATION_KM:
        print(f"  [WARN] {city_cn} 测站距市中心 {gap:.0f} km，标记改放城市中心")
        station_lat, station_lon = lat, lon

    # 风速在 wind.speed，不在 main 里；缺测保持空值，不抛 KeyError。
    return {
        "city": city,
        "city_cn": city_cn,
        "temp": main.get("temp"),
        "feels_like": main.get("feels_like"),
        "humidity": main.get("humidity"),
        "pressure": main.get("pressure"),
        "wind_speed": wind.get("speed"),
        "wind_deg": wind.get("deg"),
        "wind_gust": wind.get("gust"),
        "weather": weather,
        "visibility": data.get("visibility"),
        "rain_1h": rain.get("1h"),
        "snow_1h": snow.get("1h"),
        "lat": station_lat,
        "lon": station_lon,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_with_retry(city, city_cn, lat, lon):
    """网络抖动时再试一次。Key 无效或限流不再重试。"""
    try:
        return fetch_one(city, city_cn, lat, lon)
    except FetchError as exc:
        if exc.fatal:
            raise
        print(f"  [RETRY] {city_cn}  {exc}")
        time.sleep(1.2)
        return fetch_one(city, city_cn, lat, lon)


def write_meta(payload):
    with open(META_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def setting_int(name, default, minimum):
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, value)


def refresh_interval():
    return setting_int("WEATHER_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS, 5)


def refresh_cooldown():
    return setting_int("WEATHER_REFRESH_COOLDOWN", DEFAULT_REFRESH_COOLDOWN, 5)


def _blank(value):
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() == ""


def coerce_row(raw):
    item = {}
    for col in COLUMNS:
        value = raw.get(col) if isinstance(raw, dict) else None
        if _blank(value):
            item[col] = None
            continue
        if col in NUMERIC_COLUMNS:
            try:
                item[col] = float(value)
            except (TypeError, ValueError):
                item[col] = None
        else:
            item[col] = str(value).strip()
    return item


def load_cached_cities():
    if not os.path.exists(CSV_PATH):
        return []
    cities = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = coerce_row(raw)
            if row.get("temp") is None or row.get("lat") is None or row.get("lon") is None:
                continue
            if not (row.get("city_cn") or row.get("city")):
                continue
            cities.append(row)
    return cities


def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    try:
        with open(META_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def csv_cell(col, value):
    if _blank(value):
        return ""
    if isinstance(value, float):
        if col in INTEGER_COLUMNS and value.is_integer():
            return str(int(value))
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return value


def save_cached_cities(cities):
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for city in cities:
            writer.writerow({col: csv_cell(col, city.get(col)) for col in COLUMNS})


def merge_cities(previous, fresh):
    by_name = {}
    for row in previous:
        key = row.get("city_cn") or row.get("city")
        if key:
            by_name[key] = row
    for row in fresh:
        key = row.get("city_cn") or row.get("city")
        if key:
            by_name[key] = row
    ordered = []
    seen = set()
    for city, city_cn, _lat, _lon in CITIES:
        row = by_name.get(city_cn) or by_name.get(city)
        if not row:
            continue
        ordered.append(row)
        seen.add(city_cn)
        seen.add(city)
    for key, row in by_name.items():
        if key not in seen:
            ordered.append(row)
    return ordered


def collect_weather():
    """按坐标拉取 11 城。成功的在 cities 里，失败的只记名字。全部失败时 error 有说明。"""
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not API_KEY or API_KEY.strip() == "your_api_key_here":
        return {
            "cities": [],
            "failed": [],
            "errors": {},
            "error": "未找到可用的 API_KEY。请复制 .env.example 为 .env 并填入 Key。",
            "fetched_at": fetched_at,
        }

    print(f"开始拉取 {len(CITIES)} 个城市的实时天气 ...")
    records = []
    failed = []
    errors = {}
    reasons = []
    for index, (city, city_cn, lat, lon) in enumerate(CITIES):
        if index:
            time.sleep(1.2)  # 免费额度 60 次/分钟，留出余量
        try:
            rec = fetch_with_retry(city, city_cn, lat, lon)
            records.append(rec)
            weather = rec["weather"] or ""
            print(f"  [OK] {city_cn}  {rec['temp']:.1f}°C  {weather}")
        except FetchError as exc:
            failed.append(city_cn)
            errors[city_cn] = str(exc)
            reasons.append(str(exc))
            print(f"  [FAIL] {city_cn}  {exc}")
            if exc.fatal:
                print("  已停止后续请求。")
                break

    error = None
    if not records:
        reason = "；".join(dict.fromkeys(reasons)) or "未知错误"
        error = f"这次没有拉到新数据（{reason}）。"
    return {
        "cities": records,
        "failed": failed,
        "errors": errors,
        "error": error,
        "fetched_at": fetched_at,
    }


def store_result(previous, result, previous_fetched_at=""):
    """合并进上次成功的记录。这一轮全部失败时不改 CSV，避免把地图清空。"""
    fresh = result.get("cities") or []
    failed = [str(name) for name in result.get("failed") or [] if str(name).strip()]
    errors = result.get("errors") or {}
    if fresh:
        cities = merge_cities(previous, fresh)
        fetched_at = result.get("fetched_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        warning = ""
        if failed:
            detail = "；".join(f"{name} {errors.get(name, '')}".strip() for name in failed)
            warning = sanitize(f"部分城市没有刷新：{detail}。这些城市仍显示上次成功的数据。")
        save_cached_cities(cities)
        payload = {
            "source": "OpenWeatherMap",
            "fetched_at": fetched_at,
            "requested": len(CITIES),
            "saved": len(cities),
            "failed": failed,
        }
        if warning:
            payload["warning"] = warning
        write_meta(payload)
        return {
            "cities": cities,
            "failed": failed,
            "warning": warning,
            "stale": False,
            "fetched_at": fetched_at,
        }

    warning = result.get("error") or "这次没有拉到新数据。"
    if previous:
        warning += " 地图仍显示上次成功的数据。"
    else:
        warning += " 也没有已保存的 weather_data.csv 可显示。"
    warning = sanitize(warning)
    write_meta(
        {
            "source": "OpenWeatherMap",
            "fetched_at": previous_fetched_at or "",
            "requested": len(CITIES),
            "saved": len(previous),
            "failed": failed,
            "warning": warning,
        }
    )
    return {
        "cities": list(previous),
        "failed": failed,
        "warning": warning,
        "stale": bool(previous),
        "fetched_at": previous_fetched_at or "",
    }


def main():
    previous = load_cached_cities()
    meta = load_meta()
    result = collect_weather()
    stored = store_result(previous, result, meta.get("fetched_at") or "")
    if result.get("error") and not result.get("cities"):
        print(f"\n{stored['warning']}")
        raise SystemExit(1)

    frame = pd.DataFrame(stored["cities"], columns=COLUMNS)
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 180)
    view = frame[
        ["city_cn", "temp", "feels_like", "humidity", "wind_speed", "weather", "lat", "lon"]
    ].copy()
    view.columns = ["城市", "温度°C", "体感°C", "湿度%", "风速m/s", "天气", "纬度", "经度"]
    print("\n===== 解析结果一览 =====")
    print(view.to_string(index=False))
    fresh = len(result.get("cities") or [])
    failed = result.get("failed") or []
    print(f"\n本轮成功 {fresh} 个 / 失败 {len(failed)} 个 → 已保存 weather_data.csv")
    if failed:
        print(f"失败城市：{'、'.join(failed)}（仍保留这些城市上次成功的记录）")


if __name__ == "__main__":
    main()
