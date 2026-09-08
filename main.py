"""Command-line weather forecasts and offline hiking route database."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src import db, geocoder, routes, weather
from src.errors import DatabaseError, DataSourceError, HikingPlannerError
from src.models import OBERBAYERN_BBOX, BBox

_CACHE_MAX_AGE = 30 * 24 * 60 * 60


def cmd_weather(args: argparse.Namespace) -> None:
    """Resolve a location and print the formatted weather forecast."""
    if args.location:
        lat, lon, display_name = geocoder.geocode(args.location)
    elif args.lat is not None and args.lon is not None:
        lat, lon, display_name = args.lat, args.lon, None
    else:
        print("❌ 请提供 --location 或同时提供 --lat 和 --lon")
        sys.exit(1)
    records = weather.fetch_weather(lat, lon, days=args.days)
    print(weather.format_forecast(
        lat=lat, lon=lon, location_name=display_name or args.location,
        records=records, daytime_only=not args.all_hours,
    ), end="")


def _cached_tile(path: Path, tile: BBox) -> tuple[dict, str] | None:
    try:
        age = time.time() - path.stat().st_mtime
        if not 0 <= age < _CACHE_MAX_AGE:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        # Keep the raw Overpass fields, with local metadata identifying this tile.
        metadata = payload.get("_hiking_planner_cache", {})
        if not isinstance(metadata, dict) or metadata.get("bbox") != asdict(tile):
            return None
        timestamp = metadata.get("fetched_at")
        if not isinstance(timestamp, str):
            return None
        fetched = datetime.fromisoformat(timestamp)
        if fetched.utcoffset() is None or not 0 <= time.time() - fetched.timestamp() < _CACHE_MAX_AGE:
            return None
        if not isinstance(payload.get("elements"), list) or payload.get("remark"):
            return None
        return payload, timestamp
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, tile: BBox, payload: dict, fetched_at: str) -> None:
    content = dict(payload)
    content["_hiking_planner_cache"] = {"bbox": asdict(tile), "fetched_at": fetched_at}
    temporary = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise DataSourceError(f"无法写入缓存 {path}: {exc}") from exc


def cmd_routes_build(args: argparse.Namespace) -> None:
    """Fetch tiled route data with optional caching and report build progress."""
    tiles = routes.tile_bbox(args.bbox, args.step)
    existing = args.db.exists() and args.db.stat().st_size > 0
    total_written = 0
    fetched_once = False
    with closing(db.connect(args.db)) as conn:
        if existing:
            db.check_schema(conn)
        else:
            db.init_db(conn)
        for index, tile in enumerate(tiles, 1):
            cache_path = args.cache_dir / f"overpass_{tile.min_lat:.5f}_{tile.min_lon:.5f}.json"
            cached = None if args.no_cache else _cached_tile(cache_path, tile)
            if cached is None:
                if fetched_once:
                    time.sleep(2)
                fetched_once = True
                payload = routes.fetch_raw(tile)
                fetched_at = datetime.now(timezone.utc).isoformat()
            else:
                payload, fetched_at = cached
            parsed = routes.parse_response(payload, fetched_at=fetched_at)
            total_written += routes.store_routes(conn, parsed)
            if cached is None and not args.no_cache:
                _write_cache(cache_path, tile, payload, fetched_at)
            suffix = " (缓存)" if cached is not None else ""
            print(f"[{index}/{len(tiles)}] {tile.min_lat:.5f},{tile.min_lon:.5f} → {len(parsed)} routes{suffix}")
        try:
            count = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        except sqlite3.Error as exc:
            raise DatabaseError(f"无法统计路线：{exc}") from exc
    print(f"完成：{len(tiles)} 个分块，写入/更新 {total_written} 条记录；数据库共 {count} 条路线。")


def cmd_routes_list(args: argparse.Namespace) -> None:
    """Print stored route names, networks, lengths and start coordinates."""
    if not args.db.is_file():
        raise DatabaseError(f"数据库不存在：{args.db}；请先运行 routes build。")
    with closing(db.connect(args.db)) as conn:
        db.check_schema(conn)
        selected = routes.load_routes(conn, max_length_km=args.max_km, min_length_km=args.min_km)
    print(f"{'Name':40}  {'Network':7}  {'Length (km)':>11}  Start (lat, lon)")
    for route in selected[:args.limit]:
        print(f"{route.name:40}  {route.network or '—':7}  {route.length_km:11.2f}  "
              f"{route.start_lat:.5f}, {route.start_lon:.5f}")
    print(f"显示 {min(len(selected), args.limit)} / {len(selected)} 条路线。")


def _bbox_arg(value: str) -> BBox:
    try:
        bbox = BBox(*(float(part) for part in value.split(",")))
        routes.tile_bbox(bbox, step_deg=360)
        return bbox
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("bbox 格式应为 MINLAT,MINLON,MAXLAT,MAXLON，且最小值小于最大值。") from exc


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入正数。") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("请输入有限的正数。")
    return number


def _nonnegative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入非负数。") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("请输入有限的非负数。")
    return number


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入正整数。") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("请输入正整数。")
    return number


def build_parser() -> argparse.ArgumentParser:
    """Create the weather and routes subcommand argument parser."""
    parser = argparse.ArgumentParser(
        prog="main.py", description="🥾 OberbayernHikingPlanner — 慕尼黑周边徒步推荐工具",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    weather_parser = subparsers.add_parser("weather", help="查询某坐标或地名的天气预报")
    weather_parser.add_argument("--location", "-l", metavar="NAME", help="地名，例如 Schliersee")
    weather_parser.add_argument("--lat", type=float, metavar="LAT", help="纬度")
    weather_parser.add_argument("--lon", type=float, metavar="LON", help="经度")
    weather_parser.add_argument("--days", type=int, default=3, metavar="N", help="预报天数（默认 3）")
    weather_parser.add_argument("--all-hours", action="store_true", help="显示全天24小时（默认 06:00–20:00）")
    weather_parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    weather_parser.set_defaults(func=cmd_weather)

    routes_parser = subparsers.add_parser("routes", help="构建或查询徒步路线数据库")
    route_commands = routes_parser.add_subparsers(dest="routes_command", required=True)
    build = route_commands.add_parser("build", help="分块获取 OSM 徒步路线")
    build.add_argument("--bbox", type=_bbox_arg, default=OBERBAYERN_BBOX, metavar="MINLAT,MINLON,MAXLAT,MAXLON")
    build.add_argument("--step", type=_positive_float, default=0.25)
    build.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    build.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    build.add_argument("--no-cache", action="store_true", help="禁用缓存读写")
    build.set_defaults(func=cmd_routes_build)
    listing = route_commands.add_parser("list", help="列出本地路线")
    listing.add_argument("--max-km", type=_nonnegative_float)
    listing.add_argument("--min-km", type=_nonnegative_float)
    listing.add_argument("--limit", type=_positive_int, default=20)
    listing.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    listing.set_defaults(func=cmd_routes_list)
    return parser


def main() -> None:
    """Dispatch CLI commands and turn project errors into friendly exit messages."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    try:
        args.func(args)
    except HikingPlannerError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
