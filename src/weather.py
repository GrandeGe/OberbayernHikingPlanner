"""
src/weather.py
天气模块：调用 Bright Sky API 获取德国境内任意坐标的天气预报。

Bright Sky 是 DWD（德国气象局）数据的 JSON 封装，免费无需 API Key。
文档：https://brightsky.dev/docs/
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from src.errors import DataSourceError

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
        DataSourceError — 网络错误或 API 返回意外格式（保留原始异常）
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
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("weather"), list):
            raise ValueError("Expected an object containing a weather list.")
        for record in data["weather"]:
            if not isinstance(record, dict) or not isinstance(record.get("timestamp"), str):
                raise ValueError("Each weather record must contain a timestamp string.")
            datetime.fromisoformat(record["timestamp"])
        return data["weather"]
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"Bright Sky request failed or returned invalid data: {exc}") from exc


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
    dominant = max(conditions, key=conditions.count) if conditions else None

    return {
        "temp_min": min(temps) if temps else None,
        "temp_max": max(temps) if temps else None,
        "total_rain": sum(rains) if rains else None,
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

    rain = rec.get("precipitation")
    rain_str = f"rain {_format_number(rain, 1)}mm"

    return f"  {hour_str}  {emoji}  {temp_str}  {wind_str}  {rain_str}"


def _weekday_de(date_str: str) -> str:
    """把 '2026-05-05' 转成 'Di, 05.05.' 格式。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    days_de = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    weekday = days_de[dt.weekday()]
    return f"{weekday}, {dt.strftime('%d.%m.%Y')}"


def _format_number(value: float | None, digits: int = 0) -> str:
    """Format a numeric value, using an em dash for missing data."""
    return "—" if value is None else f"{value:.{digits}f}"


def format_forecast(
    lat: float,
    lon: float,
    location_name: str | None = None,
    records: list[dict] | None = None,
    daytime_only: bool = True,
) -> str:
    """Return forecast text; fetch records only when none were supplied.

    Missing numeric values appear as —. The daytime filter keeps hourly rows
    from 06:00 through 20:00; daily summaries include every supplied hour.
    """
    if records is None:
        records = fetch_weather(lat, lon)

    loc_label = location_name if location_name else f"{lat}°N, {lon}°E"
    lines = [f"\n📍 {loc_label} ({lat:.4f}°N, {lon:.4f}°E)", "━" * 50]
    grouped = group_by_day(records)

    for day_key, day_records in sorted(grouped.items()):
        summary = summarize_day(day_records)
        lines.extend([
            f"\n📅 {_weekday_de(day_key)}",
            f"   摘要: {_format_number(summary['temp_min'])}°C — "
            f"{_format_number(summary['temp_max'])}°C  "
            f"累计雨量 {_format_number(summary['total_rain'], 1)}mm  "
            f"最大风速 {_format_number(summary['max_wind'])}km/h",
            "",
        ])
        for rec in day_records:
            hour = datetime.fromisoformat(rec["timestamp"]).hour
            if daytime_only and not (6 <= hour <= 20):
                continue
            lines.append(_format_hour_row(rec))

    return "\n".join(lines) + "\n\n"
