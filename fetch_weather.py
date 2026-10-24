# -*- coding: utf-8 -*-
"""
fetch_weather.py — Q3 实时气象站点地图 · 数据拉取脚本
从 OpenWeatherMap 拉取大湾区 11 城市的实时天气数据，保存为 weather_data.csv
用法：python fetch_weather.py
"""

import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv

# ---------- 配置 ----------
load_dotenv()  # 从 .env 读取 API_KEY（该文件已被 .gitignore 排除）

API_KEY = os.getenv("API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# 大湾区 11 城市（按指南要求：含澳门/珠海/广州，可自行增删）
CITIES = [
    "Guangzhou", "Foshan", "Dongguan", "Shenzhen", "Zhongshan",
    "Zhuhai", "Jiangmen", "Huizhou", "Zhaoqing",
    "Hong Kong", "Macau",
]

# 城市中文名映射（API 的 lang 参数只翻译天气描述，不翻译城市名，故自行维护）
CITY_CN = {
    "Guangzhou": "广州", "Foshan": "佛山", "Dongguan": "东莞",
    "Shenzhen": "深圳", "Zhongshan": "中山", "Zhuhai": "珠海",
    "Jiangmen": "江门", "Huizhou": "惠州", "Zhaoqing": "肇庆",
    "Hong Kong": "香港", "Macau": "澳门",
}

# 请求头（部分 API 对默认 UA 有限流）
HEADERS = {"User-Agent": "mini-01-weather-map/1.0"}


def fetch_one(city: str) -> dict:
    """拉取单个城市天气，返回扁平化字典；失败抛出异常。"""
    params = {
        "q": city,
        "appid": API_KEY,
        "units": "metric",   # 摄氏度
        "lang": "zh_cn",     # 天气描述用中文
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()  # 4xx/5xx 直接抛错
    data = resp.json()
    return {
        "city": city,
        "city_cn": CITY_CN.get(city, data["name"]),
        "temp": data["main"]["temp"],
        "feels_like": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],
        "wind_speed": data.get("wind", {}).get("speed"),  # 无风数据时为 None
        "weather": data["weather"][0]["description"],
        "lat": data["coord"]["lat"],
        "lon": data["coord"]["lon"],
        "timestamp": pd.Timestamp.now(),
    }


def main():
    if not API_KEY:
        raise SystemExit("未找到 API_KEY：请复制 .env.example 为 .env 并填入你的 Key")

    records = []
    failed = []
    print(f"开始拉取 {len(CITIES)} 个城市实时天气 ...")
    for city in CITIES:
        try:
            rec = fetch_one(city)
            records.append(rec)
            print(f"  [OK] {city:<12} {rec['temp']:>6.1f}°C  {rec['weather']}")
        except requests.exceptions.RequestException as e:
            failed.append(city)
            print(f"  [FAIL] {city}: {e}")
        time.sleep(1.2)  # 免费额度限流保护：60 次/分钟，留足余量

    if not records:
        raise SystemExit("全部城市拉取失败，请检查 API Key / 网络")

    df = pd.DataFrame(records)
    df.to_csv("weather_data.csv", index=False, encoding="utf-8-sig")

    # 打印完整解析结果，让每个字段都看得见
    pd.set_option("display.unicode.east_asian_width", True)  # 中文对齐
    view = df[["city_cn", "temp", "feels_like", "humidity",
               "wind_speed", "weather", "lat", "lon"]].copy()
    view.columns = ["城市", "温度°C", "体感°C", "湿度%", "风速m/s", "天气", "纬度", "经度"]
    print("\n===== 解析结果一览 =====")
    print(view.to_string(index=False))

    print(f"\n成功 {len(records)} 个 / 失败 {len(failed)} 个 → 已保存 weather_data.csv")
    if failed:
        print(f"失败城市：{', '.join(failed)}（可单独重跑本脚本）")


if __name__ == "__main__":
    main()
