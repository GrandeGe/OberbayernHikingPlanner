"""
src/weather.py
天气模块：调用 Bright Sky API 获取德国境内任意坐标的天气预报。

Bright Sky 是 DWD（德国气象局）数据的 JSON 封装，免费无需 API Key。
文档：https://brightsky.dev/docs/
"""

import requests
from datetime import datetime, timedelta, timezone


# ─────────────────────────────────────────
# 常量
# ─────────────────────────────────────────

API_BASE = "https://api.brightsky.dev"

# condition 字符串 → emoji
CONDITION_EMOJI = {
    "dry":           "☀️ ",
    "fog":           "🌫️ ",
    "rain":          "🌧️ ",
    "sleet":         "🌨️ ",
    "snow":          "❄️ ",
    "hail":          "🌩️ ",
    "thunderstorm":  "⛈️ ",
}

# 风向角度 → 中文方向名
def _wind_dir_label(degrees: float | None) -> str:
    if degrees is None:
        return "—"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(degrees / 45) % 8
    return dirs[idx]


# ─────────────────────────────────────────
# 核心函数
# ─────────────────────────────────────────

def fetch_weather(lat: float, lon: float, days: int = 3) -> list[dict]:
    """
    调用 Bright Sky API，获取从今天起 `days` 天的逐小时天气预报。

    参数：
        lat   — 纬度
        lon   — 经度
        days  — 预报天数（默认3天）

    返回：
        list of dict，每个 dict 是一个小时的天气记录（原始 API 字段）。

    异常：
        requests.RequestException — 网络错误
        ValueError                — API 返回错误
    """
    today = datetime.now(timezone.utc).date()
    last_day = today + timedelta(days=days - 1)

    url = f"{API_BASE}/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "date": today.isoformat(),
        "last_date": last_day.isoformat(),
        "units": "dwd",   # 使用 DWD 默认单位（°C、km/h、mm）
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()   # HTTP 4xx/5xx → 抛出异常
    except requests.ConnectionError:
        raise requests.RequestException("无法连接到 Bright Sky API，请检查网络。")
    except requests.Timeout:
        raise requests.RequestException("请求超时（10秒），请稍后重试。")

    data = resp.json()

    if "weather" not in data:
        raise ValueError(f"API 返回了意外的格式：{data}")

    return data["weather"]


def group_by_day(records: list[dict]) -> dict[str, list[dict]]:
    """
    把逐小时记录按日期分组。

    返回：
        {"2026-05-05": [record, record, ...], "2026-05-06": [...], ...}
    """
    grouped: dict[str, list[dict]] = {}
    for rec in records:
        # timestamp 格式示例："2026-05-05T08:00:00+00:00"
        dt = datetime.fromisoformat(rec["timestamp"])
        day_key = dt.date().isoformat()
        grouped.setdefault(day_key, []).append(rec)
    return grouped


def summarize_day(records: list[dict]) -> dict:
    """
    计算一天的天气摘要（用于将来的推荐评分逻辑）。

    返回 dict，包含：
        temp_min, temp_max, total_rain, max_wind,
        dominant_condition, avg_cloud_cover
    """
    temps = [r["temperature"] for r in records if r.get("temperature") is not None]
    rains = [r["precipitation"] for r in records if r.get("precipitation") is not None]
    winds = [r["wind_speed"] for r in records if r.get("wind_speed") is not None]
    clouds = [r["cloud_cover"] for r in records if r.get("cloud_cover") is not None]

    # 最常出现的 condition 为 dominant
    conditions = [r["condition"] for r in records if r.get("condition")]
    dominant = max(set(conditions), key=conditions.count) if conditions else "unknown"

    return {
        "temp_min": min(temps) if temps else None,
        "temp_max": max(temps) if temps else None,
        "total_rain": sum(rains) if rains else 0.0,
        "max_wind": max(winds) if winds else None,
        "dominant_condition": dominant,
        "avg_cloud_cover": sum(clouds) / len(clouds) if clouds else None,
        "record_count": len(records),
    }


# ─────────────────────────────────────────
# 输出格式化
# ─────────────────────────────────────────

def _format_hour_row(rec: dict) -> str:
    """把单条逐小时记录格式化成一行字符串。"""
    dt = datetime.fromisoformat(rec["timestamp"])
    hour_str = dt.strftime("%H:%M")

    temp = rec.get("temperature")
    temp_str = f"{temp:>3.0f}°C" if temp is not None else "  —°C"

    condition = rec.get("condition", "")
    emoji = CONDITION_EMOJI.get(condition, "❓ ")

    wind_speed = rec.get("wind_speed")
    wind_dir = _wind_dir_label(rec.get("wind_direction"))
    wind_str = f"wind {wind_speed:.0f} km/h {wind_dir}" if wind_speed is not None else "wind —"

    rain = rec.get("precipitation", 0.0) or 0.0
    rain_str = f"rain {rain:.1f}mm"

    return f"  {hour_str}  {emoji}  {temp_str}  {wind_str}  {rain_str}"


def _weekday_de(date_str: str) -> str:
    """把 '2026-05-05' 转成 'Di, 05.05.' 格式。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    days_de = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    weekday = days_de[dt.weekday()]
    return f"{weekday}, {dt.strftime('%d.%m.%Y')}"


def print_forecast(
    lat: float,
    lon: float,
    location_name: str | None = None,
    records: list[dict] | None = None,
    daytime_only: bool = True,
) -> None:
    """
    把天气预报漂亮地打印到终端。

    参数：
        lat, lon        — 坐标（用于显示）
        location_name   — 可选的地名字符串
        records         — fetch_weather() 的返回值（传入以避免重复请求）
        daytime_only    — 只显示 06:00–20:00 的记录（默认 True，减少噪音）
    """
    if records is None:
        records = fetch_weather(lat, lon)

    loc_label = location_name if location_name else f"{lat}°N, {lon}°E"
    print(f"\n📍 {loc_label} ({lat:.4f}°N, {lon:.4f}°E)")
    print("━" * 50)

    grouped = group_by_day(records)

    for day_key, day_records in sorted(grouped.items()):
        summary = summarize_day(day_records)

        print(f"\n📅 {_weekday_de(day_key)}")
        print(
            f"   摘要: {summary['temp_min']:.0f}°C — {summary['temp_max']:.0f}°C  "
            f"累计雨量 {summary['total_rain']:.1f}mm  "
            f"最大风速 {summary['max_wind']:.0f}km/h"
        )
        print()

        for rec in day_records:
            if daytime_only:
                hour = datetime.fromisoformat(rec["timestamp"]).hour
                if not (6 <= hour <= 20):
                    continue
            print(_format_hour_row(rec))

    print()