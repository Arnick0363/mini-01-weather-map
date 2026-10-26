# -*- coding: utf-8 -*-
"""
make_map.py — 大湾区实时气象站点 · 一次性落图

读取 weather_data.csv，生成静态地图 weather_map.html。
要在浏览器里自动刷新，请改用 python app.py。
用法：python make_map.py
"""

import json
import math
import os
from html import escape

import folium
import pandas as pd
from branca.element import Element, MacroElement
from folium.elements import EventHandler
from folium.plugins import MarkerCluster
from folium.template import Template
from folium.utilities import JsCode

# ---------- 配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_CSV = os.path.join(BASE_DIR, "weather_data.csv")
META_JSON = os.path.join(BASE_DIR, "weather_meta.json")
OUTPUT_HTML = os.path.join(BASE_DIR, "weather_map.html")

# 大湾区几何中心附近。有站点时会再 fit_bounds，保证城市都在视野里。
CENTER_LAT, CENTER_LON = 22.9, 113.5
ZOOM_START = 8

# 色相从蓝（212°）连续转到红（4°），中间经过青、绿、黄。
# 固定分档会把相差不到 2°C 的城市收成同一种颜色；按本次最低温到最高温拉开后才能看出差别。
# 在 RGB 里直接从蓝插到黄会穿过灰色，所以改走色相，饱和度保持住。
HUE_COLD = 212.0
HUE_HOT = 4.0
COMPASS_8 = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

PAGE_CSS = """
<style>
.wx-panel {
    background: rgba(255, 255, 255, 0.96);
    border-radius: 10px;
    border-left: 4px solid #2166AC;
    box-shadow: 0 1px 6px rgba(16, 24, 40, 0.18);
    padding: 10px 12px;
    max-width: 250px;
    color: #1c2430;
    font: 13px/1.45 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.wx-panel b { display: block; font-size: 14px; margin-bottom: 2px; }
.wx-panel-error { border-left-color: #D73027; }
.wx-panel-warn { border-left-color: #E6A23C; }
.wx-panel-empty { border-left-color: #E6A23C; }
.wx-muted { color: #526070; font-size: 12px; margin-top: 2px; }
.wx-scale {
    height: 8px;
    margin-top: 8px;
    border-radius: 99px;
    border: 1px solid rgba(0, 0, 0, 0.08);
}
.wx-scale-labels {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 3px;
    color: #526070;
    font-size: 11px;
}
#wx-boot {
    position: fixed;
    left: 12px;
    bottom: 18px;
    z-index: 10000;
}
.leaflet-marker-icon.wx-label-icon,
.leaflet-marker-icon.wx-label-icon .wx-label {
    background: none !important;
    border: none !important;
    pointer-events: none !important;
}
.wx-label {
    color: #1c2430;
    font-size: 12px;
    font-weight: 700;
    line-height: 16px;
    text-align: center;
    white-space: nowrap;
    text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 1px 0 #fff;
}
.wx-popup .leaflet-popup-content-wrapper { border-radius: 12px; }
.wx-popup .leaflet-popup-content { margin: 12px 14px; }
.wx-card {
    min-width: 210px;
    color: #1c2430;
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.wx-bar { height: 6px; border-radius: 99px; margin-bottom: 8px; }
.wx-city { font-size: 18px; font-weight: 700; line-height: 1.2; padding-right: 8px; }
.wx-sub { color: #526070; font-size: 12px; margin-top: 2px; }
.wx-temp { font-size: 28px; font-weight: 700; line-height: 1.1; margin-top: 6px; }
.wx-unit { margin-left: 2px; color: #526070; font-size: 14px; font-weight: 600; }
.wx-feels { color: #526070; font-size: 13px; margin: 2px 0 8px; }
.wx-row {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding: 4px 0;
    border-top: 1px solid #eef0f3;
    font-size: 13px;
}
.wx-row span { color: #526070; }
.wx-row b { color: #1c2430; font-weight: 600; text-align: right; }
</style>
"""

PAGE_GUARD = """
<script>
window.onerror = function (msg) {
    var text = "地图脚本出错：" + msg;
    var boot = document.getElementById("wx-boot");
    if (boot) {
        boot.className = "wx-panel wx-panel-error";
        boot.textContent = text;
    }
    var status = document.getElementById("wx-status");
    if (status) {
        status.className = "wx-panel wx-panel-error";
        status.textContent = text;
    }
    return false;
};
setTimeout(function () {
    var boot = document.getElementById("wx-boot");
    if (!boot) return;
    boot.className = "wx-panel wx-panel-error";
    boot.textContent = "地图加载超时。请检查网络后刷新，底图或脚本可能没有载入。";
}, 20000);
</script>
"""

BOOT_HTML = '<div id="wx-boot" class="wx-panel">正在加载气象站点…</div>'


class HtmlPopup(MacroElement):
    """不依赖 jQuery 的弹窗。CDN 上的 jQuery 失败时，标记和弹窗仍能工作。"""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
            {{ this._parent.get_name() }}.bindPopup({{ this.html_js }}, {{ this.options_js }});
        {% endmacro %}
        """
    )

    def __init__(self, html):
        super().__init__()
        self._name = "HtmlPopup"
        self.html_js = js_string(html)
        self.options_js = json.dumps(
            {
                "maxWidth": 320,
                "minWidth": 230,
                "className": "wx-popup",
                "autoPan": True,
            },
            ensure_ascii=False,
        )


class StatusControl(MacroElement):
    _template = Template(
        """
        {% macro script(this, kwargs) %}
            var {{ this.get_name() }} = L.control({position: "bottomleft"});
            {{ this.get_name() }}.onAdd = function () {
                var div = L.DomUtil.create("div", "wx-panel {{ this.kind_class }}");
                div.id = "wx-status";
                div.innerHTML = {{ this.html_js }};
                L.DomEvent.disableClickPropagation(div);
                L.DomEvent.disableScrollPropagation(div);
                return div;
            };
            {{ this.get_name() }}.addTo({{ this._parent.get_name() }});
            var boot = document.getElementById("wx-boot");
            if (boot) boot.remove();
        {% endmacro %}
        """
    )

    def __init__(self, html, kind):
        super().__init__()
        self._name = "StatusControl"
        self.html_js = js_string(html)
        self.kind_class = {
            "ok": "wx-panel-ok",
            "warn": "wx-panel-warn",
            "empty": "wx-panel-empty",
            "error": "wx-panel-error",
        }.get(kind, "wx-panel-ok")


def js_string(value):
    # 放进 <script> 的字符串里时，把 < 转义掉，避免内容提前结束脚本。
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def hsl_to_hex(hue, saturation, lightness):
    hue = hue % 360
    saturation /= 100.0
    lightness /= 100.0
    chroma = (1 - abs(2 * lightness - 1)) * saturation
    channel = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    if hue < 60:
        red, green, blue = chroma, channel, 0
    elif hue < 120:
        red, green, blue = channel, chroma, 0
    elif hue < 180:
        red, green, blue = 0, chroma, channel
    elif hue < 240:
        red, green, blue = 0, channel, chroma
    elif hue < 300:
        red, green, blue = channel, 0, chroma
    else:
        red, green, blue = chroma, 0, channel
    match = lightness - chroma / 2
    rgb = tuple(round((value + match) * 255) for value in (red, green, blue))
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def color_at(fraction):
    fraction = min(1.0, max(0.0, fraction))
    hue = HUE_COLD + (HUE_HOT - HUE_COLD) * fraction
    # 黄绿一段略提亮，避免变成暗橄榄。
    lightness = 42 + 12 * math.sin(math.pi * fraction)
    return hsl_to_hex(hue, 76, lightness)


def gradient_css():
    parts = [f"{color_at(stop)} {stop * 100:.0f}%" for stop in (0, 0.25, 0.5, 0.75, 1)]
    return "linear-gradient(90deg, " + ", ".join(parts) + ")"


def temp_color(temp, tmin, tmax):
    """冷蓝、热红，中间按色相平滑过渡。温差小于 0.3°C 时不再强行拉开。"""
    span = tmax - tmin
    fraction = 0.5 if span < 0.3 else (temp - tmin) / span
    return color_at(fraction)


def is_missing(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def finite_number(value):
    if is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def fmt_num(value, digits):
    number = finite_number(value)
    if number is None:
        return None
    return f"{number:.{digits}f}"


def text_of(row, key):
    if key not in row.index:
        return ""
    value = row[key]
    if is_missing(value):
        return ""
    return str(value).strip()


def display_name(row):
    return text_of(row, "city_cn") or text_of(row, "city") or "未命名"


def wind_direction(value):
    number = finite_number(value)
    if number is None:
        return None
    index = int((number % 360 + 22.5) // 45) % 8
    return COMPASS_8[index] + "风"


def format_coord(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}°{ns}, {abs(lon):.4f}°{ew}"


def format_time(value):
    if is_missing(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if is_missing(parsed):
        raw = str(value).strip()
        return raw or None
    return parsed.strftime("%Y-%m-%d %H:%M")


def format_visibility(value):
    meters = finite_number(value)
    if meters is None:
        return None
    if meters >= 1000:
        km = meters / 1000.0
        text = f"{km:.0f}" if km >= 10 else f"{km:.1f}"
        return f"{text} km"
    return f"{meters:.0f} m"


def wind_text(row):
    speed = fmt_num(row.get("wind_speed"), 2)
    direction = wind_direction(row.get("wind_deg"))
    degrees = fmt_num(row.get("wind_deg"), 0)
    if speed and direction and degrees:
        return f"{speed} m/s · {direction}（{degrees}°）"
    if speed and direction:
        return f"{speed} m/s · {direction}"
    if speed:
        return f"{speed} m/s"
    return direction


def detail_rows(row):
    humidity = fmt_num(row.get("humidity"), 0)
    gust = fmt_num(row.get("wind_gust"), 2)
    pressure = fmt_num(row.get("pressure"), 0)
    rain = fmt_num(row.get("rain_1h"), 1)
    snow = fmt_num(row.get("snow_1h"), 1)
    items = [
        ("湿度", f"{humidity}%" if humidity else None),
        ("风速", wind_text(row)),
        ("阵风", f"{gust} m/s" if gust else None),
        ("降水", f"{rain} mm/h" if rain else None),
        ("降雪", f"{snow} mm/h" if snow else None),
        ("气压", f"{pressure} hPa" if pressure else None),
        ("能见度", format_visibility(row.get("visibility"))),
        ("经纬度", format_coord(float(row["lat"]), float(row["lon"]))),
        ("更新时间", format_time(row.get("timestamp"))),
    ]
    html = []
    for label, value in items:
        if not value:
            continue
        html.append(
            f"<div class='wx-row'><span>{escape(label)}</span><b>{escape(value)}</b></div>"
        )
    return "".join(html)


def popup_html(row, color):
    city = display_name(row)
    city_en = text_of(row, "city")
    weather = text_of(row, "weather")
    subtitle = " · ".join(bit for bit in (city_en if city_en != city else "", weather) if bit)
    temp = fmt_num(row.get("temp"), 1)
    feels = fmt_num(row.get("feels_like"), 1)
    feels_html = f"<div class='wx-feels'>体感 {escape(feels)}°C</div>" if feels else ""
    sub_html = f"<div class='wx-sub'>{escape(subtitle)}</div>" if subtitle else ""
    return (
        "<div class='wx-card'>"
        f"<div class='wx-bar' style='background:{color}'></div>"
        f"<div class='wx-city'>{escape(city)}</div>"
        f"{sub_html}"
        f"<div class='wx-temp'>{escape(temp)}<span class='wx-unit'>°C</span></div>"
        f"{feels_html}"
        f"<div class='wx-rows'>{detail_rows(row)}</div>"
        "</div>"
    )


def join_names(names, limit=6):
    clean = [str(name) for name in names if str(name).strip()]
    if not clean:
        return ""
    if len(clean) <= limit:
        return "、".join(clean)
    return "、".join(clean[:limit]) + f" 等 {len(clean)} 个"


def status_html(title, lines, tmin=None, tmax=None):
    body = "".join(f"<div class='wx-muted'>{escape(line)}</div>" for line in lines if line)
    legend = ""
    if tmin is not None and tmax is not None:
        legend = (
            f"<div class='wx-scale' style='background:{gradient_css()}'></div>"
            "<div class='wx-scale-labels'>"
            f"<span>{tmin:.1f}°C 冷</span><span>热 {tmax:.1f}°C</span>"
            "</div>"
        )
    return f"<b>{escape(title)}</b>{body}{legend}"


def load_table(path):
    """返回 (dataframe 或 None, problem 或 None)。problem 是 (状态, 标题, 说明)。"""
    if not os.path.exists(path):
        return None, (
            "error",
            "天气数据没有加载成功",
            "找不到 weather_data.csv。请先运行 python fetch_weather.py。",
        )
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None, ("empty", "没有可显示的站点", "weather_data.csv 是空文件。")
    except (OSError, pd.errors.ParserError, UnicodeError, ValueError) as exc:
        detail = str(exc).strip().splitlines()[0][:180]
        return None, ("error", "天气数据没有加载成功", f"无法读取 weather_data.csv：{detail}")
    if frame.empty:
        return frame, ("empty", "没有可显示的站点", "weather_data.csv 里没有城市记录。")
    missing = [name for name in ("temp", "lat", "lon") if name not in frame.columns]
    if missing:
        return None, (
            "error",
            "天气数据没有加载成功",
            "weather_data.csv 缺少字段：" + "、".join(missing) + "。",
        )
    return frame, None


def read_meta(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"warning": "weather_meta.json 无法解析，站点仍按表格绘制。"}
    if not isinstance(data, dict):
        return {"warning": "weather_meta.json 格式不对，站点仍按表格绘制。"}
    return data


def split_rows(frame):
    valid = []
    skipped = []
    for _, row in frame.iterrows():
        temp = finite_number(row.get("temp"))
        lat = finite_number(row.get("lat"))
        lon = finite_number(row.get("lon"))
        if temp is None or lat is None or lon is None:
            skipped.append(display_name(row))
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped.append(display_name(row))
            continue
        valid.append(row)
    return valid, skipped


def meta_bits(meta):
    warning = meta.get("warning")
    warning = warning.strip() if isinstance(warning, str) else ""
    failed = meta.get("failed") if isinstance(meta.get("failed"), list) else []
    failed = [str(item) for item in failed if str(item).strip()]
    fetched_at = meta.get("fetched_at") if isinstance(meta.get("fetched_at"), str) else ""
    saved = meta.get("saved")
    if saved == 0:
        fetched_at = ""
    return warning, failed, fetched_at.strip()


def build_map(rows, skipped, meta, problem):
    m = folium.Map(
        location=[CENTER_LAT, CENTER_LON],
        zoom_start=ZOOM_START,
        tiles=None,
        zoom_control=True,
    )
    root = m.get_root()
    root.header.add_child(Element(PAGE_CSS), name="wx-css")
    root.header.add_child(Element(PAGE_GUARD), name="wx-guard")
    root.html.add_child(Element(BOOT_HTML), name="wx-boot")

    # 与原来同一套 Esri 底图：街道图为默认，卫星影像可在右上角切换。
    folium.TileLayer("Esri WorldStreetMap", name="街道图", attr="Esri").add_to(m)
    folium.TileLayer("Esri WorldImagery", name="卫星影像", show=False).add_to(m)

    warning, failed, fetched_at = meta_bits(meta)
    tmin = tmax = None
    if rows:
        temps = [float(row["temp"]) for row in rows]
        tmin, tmax = min(temps), max(temps)
        cluster = MarkerCluster(
            name="城市站点",
            overlay=True,
            control=True,
            show=True,
            options={
                "disableClusteringAtZoom": 7,
                "maxClusterRadius": 32,
                "spiderfyOnMaxZoom": True,
                "showCoverageOnHover": False,
            },
        )
        labels = folium.FeatureGroup(name="温度标签", show=True)
        for row in rows:
            lat = float(row["lat"])
            lon = float(row["lon"])
            temp = float(row["temp"])
            color = temp_color(temp, tmin, tmax)
            html = popup_html(row, color)
            weather = text_of(row, "weather")
            tooltip = f"{display_name(row)}  {temp:.1f}°C" + (f"  {weather}" if weather else "")
            circle = folium.CircleMarker(
                location=[lat, lon],
                radius=12,
                color="#243044",
                weight=1.5,
                fill=True,
                fill_color=color,
                fill_opacity=0.95,
                tooltip=escape(tooltip),
            )
            circle.add_child(HtmlPopup(html))
            circle.add_child(
                EventHandler(
                    "mouseover",
                    JsCode("function (event) { event.target.bringToFront(); }"),
                )
            )
            circle.add_to(cluster)
            label = folium.Marker(
                location=[lat, lon],
                icon=folium.DivIcon(
                    icon_size=(76, 16),
                    icon_anchor=(38, 32),
                    class_name="wx-label-icon",
                    html=f"<div class='wx-label'>{temp:.1f}°C</div>",
                ),
            )
            label.add_to(labels)
        cluster.add_to(m)
        labels.add_to(m)
        lats = [float(row["lat"]) for row in rows]
        lons = [float(row["lon"]) for row in rows]
        m.fit_bounds(
            [[min(lats), min(lons)], [max(lats), max(lons)]],
            padding_top_left=(28, 56),
            padding_bottom_right=(180, 150),
            max_zoom=9,
        )

    if problem:
        lines = [problem[2]]
        if warning:
            lines.append(warning)
        html = status_html(problem[1], lines)
        kind = problem[0]
    else:
        lines = []
        if fetched_at:
            lines.append(f"拉取于 {fetched_at}")
        lines.append("点击标记查看温度、湿度、风速")
        if tmin is not None and tmax - tmin < 0.3:
            lines.append("各城温度几乎相同，颜色会很接近")
        else:
            lines.append(f"冷蓝到热红，按本次温差 {tmax - tmin:.1f}°C 平滑过渡")
        if failed:
            lines.append("拉取失败：" + join_names(failed))
        if skipped:
            lines.append("无效记录未显示：" + join_names(skipped))
        if warning:
            lines.insert(0, warning)
        html = status_html(f"已加载 {len(rows)} 个城市", lines, tmin, tmax)
        kind = "warn" if (warning or failed or skipped) else "ok"

    StatusControl(html, kind).add_to(m)
    # 必须最后加入。控件脚本会立刻引用图层变量，写早了会在标记创建前抛错，页面就只剩底图。
    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    return m


def main():
    frame, problem = load_table(INPUT_CSV)
    meta = read_meta(META_JSON)
    rows, skipped = ([], [])
    if problem is None:
        rows, skipped = split_rows(frame)
        if not rows:
            problem = (
                "empty",
                "没有可显示的站点",
                "记录里没有同时包含温度和经纬度的城市。",
            )
    build_map(rows, skipped, meta, problem).save(OUTPUT_HTML)
    if not rows:
        title = problem[1] if problem else "没有可显示的站点"
        print(f"已写出 weather_map.html，但没有站点标记：{title}")
        return 1
    temps = [float(row["temp"]) for row in rows]
    tmin, tmax = min(temps), max(temps)
    print(f"地图已生成：weather_map.html（{len(rows)} 个城市，{tmin:.1f}–{tmax:.1f}°C）")
    for row in rows:
        temp = float(row["temp"])
        print(f"  {display_name(row)}  {temp:.1f}°C  {temp_color(temp, tmin, tmax)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
