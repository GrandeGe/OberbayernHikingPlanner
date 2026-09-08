"""
OberbayernHikingPlanner — main entry point
根据天气、公共交通和可用时间，推荐慕尼黑周边徒步路线。
"""

import argparse
import sys

import requests as req_lib  # 用别名避免和模块名冲突


def cmd_weather(args):
    """处理 `python main.py weather ...` 子命令。"""
    from src.weather import fetch_weather, print_forecast

    # 确定坐标
    if args.location:
        # Phase 1 后期：调用 geocoder（目前先用硬编码 fallback）
        try:
            from src.geocoder import geocode
            lat, lon, display_name = geocode(args.location)
        except ImportError:
            print("⚠️  geocoder 模块尚未实现，--location 暂不可用。")
            print("    请使用 --lat / --lon 直接指定坐标。")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 地名解析失败：{e}")
            sys.exit(1)
    elif args.lat is not None and args.lon is not None:
        lat, lon, display_name = args.lat, args.lon, None
    else:
        print("❌ 请提供 --location 或同时提供 --lat 和 --lon")
        sys.exit(1)

    try:
        records = fetch_weather(lat, lon, days=args.days)
    except req_lib.RequestException as e:
        print(f"❌ 网络错误：{e}")
        sys.exit(1)
    except ValueError as e:
        print(f"❌ 数据解析错误：{e}")
        sys.exit(1)

    print_forecast(
        lat=lat,
        lon=lon,
        location_name=display_name or args.location,
        records=records,
        daytime_only=not args.all_hours,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="🥾 OberbayernHikingPlanner — 慕尼黑周边徒步推荐工具",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── weather 子命令 ───────────────────────────────────────────────────
    weather_parser = subparsers.add_parser(
        "weather",
        help="查询某坐标或地名的天气预报",
    )
    loc_group = weather_parser.add_mutually_exclusive_group()
    loc_group.add_argument(
        "--location", "-l",
        metavar="NAME",
        help='地名，例如 "Schliersee" 或 "Tegernsee"（需要 geocoder 模块）',
    )
    weather_parser.add_argument("--lat", type=float, metavar="LAT", help="纬度")
    weather_parser.add_argument("--lon", type=float, metavar="LON", help="经度")
    weather_parser.add_argument(
        "--days", type=int, default=3, metavar="N",
        help="预报天数（默认 3，最多 10）",
    )
    weather_parser.add_argument(
        "--all-hours", action="store_true",
        help="显示全天24小时（默认只显示 06:00–20:00）",
    )
    weather_parser.set_defaults(func=cmd_weather)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        print("\n提示：运行 python main.py weather --help 查看天气子命令。")
        return

    args.func(args)


if __name__ == "__main__":
    main()
