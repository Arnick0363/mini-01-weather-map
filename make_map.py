# -*- coding: utf-8 -*-
"""
make_map.py — Q3 实时气象站点地图 · 落图渲染脚本
读取 weather_data.csv，用 Folium 生成交互式气象地图 weather_map.html
用法：python make_map.py
"""

import folium
import pandas as pd
from folium.plugins import MarkerCluster

# ---------- 配置 ----------
INPUT_CSV = "weather_data.csv"
OUTPUT_HTML = "weather_map.html"

# 地图中心（大湾区几何中心附近，可按数据微调）
CENTER_LAT, CENTER_LON = 22.9, 113.5
ZOOM_START = 8

# 温度配色：冷蓝 → 青 → 黄 → 橙 → 红（手动分档，简单直观）
TEMP_COLORS = [
    (10, "#2166AC"),   # ≤10°C  深蓝
    (18, "#4393C3"),   # ≤18°C  蓝
    (24, "#92C5DE"),   # ≤24°C  浅蓝
    (28, "#F4A582"),   # ≤28°C  橙
    (99, "#D6604D"),   # >28°C  红
]


def temp_color(temp: float) -> str:
    for threshold, color in TEMP_COLORS:
        if temp <= threshold:
            return color
    return TEMP_COLORS[-1][1]


def popup_html(row) -> str:
    """构建标记弹窗 HTML。"""
    return (
        f"<div style='font-family: sans-serif; min-width: 160px;'>"
        f"<h4 style='margin:0 0 4px;'>{row['city_cn']}</h4>"
        f"<b>温度：</b>{row['temp']:.1f}°C（体感 {row['feels_like']:.1f}°C）<br>"
        f"<b>湿度：</b>{row['humidity']}%<br>"
        f"<b>风速：</b>{row['wind_speed']} m/s<br>"
        f"<b>天气：</b>{row['weather']}<br>"
        f"<span style='color:#888;font-size:12px;'>更新于 {row['timestamp']}</span>"
        f"</div>"
    )


def main():
    df = pd.read_csv(INPUT_CSV)
    if df.empty:
        raise SystemExit("weather_data.csv 为空，请先运行 python fetch_weather.py")

    m = folium.Map(
        location=[CENTER_LAT, CENTER_LON],
        zoom_start=ZOOM_START,
        tiles="Esri WorldStreetMap",  # Esri 瓦片允许应用使用（保留署名即可）；OSM 默认瓦片会 403 封禁
        attr="Esri",
    )
    # 附赠一个卫星影像图层，右上角图层控件可切换
    folium.TileLayer("Esri WorldImagery", name="卫星影像", show=False).add_to(m)
    folium.LayerControl(collapsed=False, position="topright").add_to(m)

    cluster = MarkerCluster(name="城市站点").add_to(m)

    for _, row in df.iterrows():
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(popup_html(row), max_width=260),
            tooltip=f"{row['city_cn']} {row['temp']:.1f}°C",
            icon=folium.Icon(
                color="white",
                icon_color=temp_color(row["temp"]),
                icon="thermometer-half",
                prefix="fa",
            ),
        ).add_to(cluster)

    # 每个站点加一个温度标签，直观展示分布
    for _, row in df.iterrows():
        folium.map.Marker(
            [row["lat"], row["lon"]],
            icon=folium.DivIcon(
                icon_size=(60, 24),
                icon_anchor=(30, -14),
                html=(
                    f"<div style='font-size:12px;font-weight:bold;color:#333;"
                    f"text-shadow:0 0 3px white;white-space:nowrap;'>"
                    f"{row['temp']:.1f}°C</div>"
                ),
            ),
        ).add_to(m)

    m.save(OUTPUT_HTML)
    print(f"地图已生成：{OUTPUT_HTML}（浏览器打开即可查看）")


if __name__ == "__main__":
    main()
