# mini-01-weather-map ｜ 大湾区实时气象站点地图

基于 OpenWeatherMap 的大湾区气象地图，覆盖广州 / 佛山 / 东莞 / 深圳 / 中山 / 珠海 / 江门 / 惠州 / 肇庆 / 香港 / 澳门。运行 `python app.py` 后打开页面即可查看：标记、温度着色、图例和弹窗会就地更新。也可以导出一份不会自动刷新的静态 HTML。

![大湾区实时气象地图](docs/screenshot.png)

## 功能特性

- **11 城实时数据**：温度 / 体感 / 湿度 / 风速 / 天气描述 / 经纬度。打开页面即有数据，默认约每 10 分钟自动刷新，也可手动刷新
- **交互地图**：MarkerCluster 聚合标记 + 按本次温差从冷蓝到热红连续配色 + 站点温度数字标签
- **双图层切换**：Esri WorldStreetMap 街道图 + Esri WorldImagery 卫星影像，右上角图层控件自由切换
- **限流保护**：请求间隔 1.2 秒，适配免费额度（60 次/分钟）并留足余量
- **异常容错**：单城市拉取失败不影响整体，结束汇总失败清单可单独重跑
- **Key 安全**：API Key 存于 `.env`（已被 `.gitignore` 排除），代码零硬编码

## 数据来源

- 天气数据：[OpenWeatherMap Current Weather API](https://openweathermap.org/current)（免费账号）
- 底图瓦片：Esri WorldStreetMap / WorldImagery（保留其署名）

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/<你的用户名>/mini-01-weather-map.git
cd mini-01-weather-map

# 2. 安装依赖（建议先创建虚拟环境）
python -m venv venv
venv\Scripts\activate        # Windows（Linux/macOS 用 source venv/bin/activate）
pip install -r requirements.txt

# 3. 配置 API Key（先到 https://home.openweathermap.org/api_keys 免费申请）
cp .env.example .env         # Windows 用 copy .env.example .env
# 编辑 .env，填入 API_KEY=你的Key

# 4. 启动本地服务（页面和数据接口同源，Key 只留在 .env）
python app.py
# 浏览器打开 http://127.0.0.1:8765
```

可选：导出一份静态快照（打开后不会自己更新）。

```bash
python fetch_weather.py      # 拉取 11 城数据 → weather_data.csv
python make_map.py           # 生成静态地图 → weather_map.html
```

## 项目结构

```
mini-01-weather-map/
├── app.py              # 本地服务：页面、缓存和定时刷新
├── static/index.html   # 会就地更新的地图页面
├── fetch_weather.py    # 数据拉取与解析（app.py 和一次性脚本共用）
├── make_map.py         # 一次性静态地图（CSV → weather_map.html）
├── weather_data.csv    # 最近一次拉取的数据（运行时生成）
├── weather_map.html    # 交互地图成品（运行时生成）
├── .env.example        # API Key 配置模板
├── .gitignore
└── requirements.txt
```

## 踩坑记录（真实开发日志）

1. **风速字段位置**：OpenWeatherMap 返回的 JSON 中风速在 `wind.speed`，而非 `main.speed`——按直觉写嵌套路径会抛 `KeyError`，已改用 `data.get("wind", {}).get("speed")` 容错。
2. **OSM 瓦片 403**：Folium 默认的 OpenStreetMap 瓦片源对非浏览器应用返回 403（OSM 瓦片使用政策），表现为地图灰底 + "Access blocked" 卡片。改用 Esri WorldStreetMap 后解决（允许应用使用，保留署名即可）。
3. **API 的 lang 参数只翻译天气描述**：`lang=zh_cn` 会把 "cloudy" 译成「多云」，但城市名不翻译——中文名映射在代码里自行维护。

## 局限说明

- 当前天气大约每 10 分钟向接口拉一轮（`.env` 里的 `WEATHER_REFRESH_SECONDS`，默认 600）。手动刷新最短间隔 30 秒（`WEATHER_REFRESH_COOLDOWN`）。`weather_map.html` 仍是静态快照
- 免费额度为 60 次/分钟、1,000,000 次/月。默认节奏下每小时大约 66 次调用；30 秒内不会把 11 个城市再打一遍
- 本项目为个人学习作品（空间数据可视化练习），非商业服务

## License

MIT
