# -*- coding: utf-8 -*-
"""
湖南大学图书馆 自动预约 CLI

    python book.py status                  查看当前预约
    python book.py seat 1-5F1-070          查某座位今天/某天的占用情况
    python book.py once                    立刻按 config.json 抢一次（逐个候选座位）
    python book.py watch                   挂后台，到开放时刻自动抢
    python book.py cancel                  取消当前预约
    python book.py resolve 1-5F1-070       从历史记录反查座位的 area/address/seatno

所有参数都在 config.json 里；命令行可以用 --date / --time / --seat 覆盖。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time

from libseat import (
    Seat, SeatClient, SeatError, client_from_config, hhmm_to_min, load_config,
    min_to_hhmm,
)


# ---------------------------------------------------------------- 辅助
def parse_date(cfg: dict, cli: str | None) -> str:
    """决定预约哪一天。

    支持这些写法（config 的 seatdate 或命令行的 --date 都可以）：

        auto        能约的最远一天（= 今天 + BookAheadDays，实测 2 天）
        today       今天
        tomorrow    明天
        dayafter    后天
        +N          今天往后 N 天，如 +1 = 明天、+0 = 今天
        2026-10-13  写死具体日期
    """
    raw = (cli or cfg.get("seatdate") or "auto").strip()
    low = raw.lower()
    today = dt.date.today()

    if low in ("", "auto"):
        tried = cfg.get("_book_ahead_days", 2)
        try:
            ahead = int(tried)
        except (TypeError, ValueError):
            ahead = 2
        return (today + dt.timedelta(days=ahead)).isoformat()
    if low in ("today", "今天"):
        return today.isoformat()
    if low in ("tomorrow", "明天"):
        return (today + dt.timedelta(days=1)).isoformat()
    if low in ("dayafter", "后天"):
        return (today + dt.timedelta(days=2)).isoformat()
    if re.fullmatch(r"\+\d+", raw):
        return (today + dt.timedelta(days=int(raw[1:]))).isoformat()

    # 写死的日期：校验一下格式，避免手滑
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise SystemExit(f"日期格式看不懂：{raw!r}（可用 auto / today / tomorrow / dayafter / +N / 2026-10-13）")
    try:
        dt.date.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"日期不存在：{raw}")
    return raw


def parse_time(cfg: dict, cli: str | None) -> tuple[int, int]:
    values = cli.split(",") if cli is not None else cfg.get("datetime", [720, 1350])

    def minute(value):
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"\d{1,2}:\d{2}", value):
                hour, mins = map(int, value.split(":"))
                if 0 <= hour < 24 and 0 <= mins < 60:
                    return hour * 60 + mins
                raise ValueError
            if re.fullmatch(r"\d+", value):
                return int(value)
        raise ValueError

    try:
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError
        start, end = map(minute, values)
        if not 0 <= start < end <= 1440:
            raise ValueError
        return start, end
    except (TypeError, ValueError):
        raise SystemExit('时间段格式错误：请填写 ["10:30", "22:30"] 或 [630, 1350]；'
                         '命令行用 --time 10:30,22:30，结束时间必须晚于开始时间。') from None


def build_seats(cfg: dict, cli_seat: str | None) -> list[Seat]:
    if cli_seat:
        items = [{"seat_name": cli_seat}]
    else:
        items = cfg.get("candidates", [])
    seats = []
    for it in items:
        seats.append(Seat(
            seat_name=it["seat_name"],
            area_code=it.get("area_code", ""),
            address_code=it.get("address_code", ""),
            seatno=it.get("seatno", ""),
        ).resolve())
    return seats


# ---------------------------------------------------------------- 子命令
def cmd_status(c: SeatClient):
    rows = c.current()
    if not rows:
        print("当前没有生效中的预约。")
        return
    for r in rows:
        print(f"[{r['Id']}] {r['SeatName']}  {r['Address_Name']}  "
              f"{r['BookDate'][:10]} {r['StartTime'][11:16]}-{r['EndTime'][11:16]}  "
              f"状态={r.get('Status')}")


def cmd_seat(c: SeatClient, cfg: dict, seat_name: str, day: str):
    seat = Seat(seat_name).resolve()
    init = c.seat_init(seat.address_code, seat.seatno)
    print(f"{seat.seat_name}  ({seat.area_code} / {seat.address_code} / {seat.seatno})")
    if init:
        print(f"  可约范围: {init.get('StartTime')} - {init.get('EndTime')}"
              f"   时段划分: {init.get('ScaleData')}")
    used = c.seat_date(seat.seatno, day)
    if not used:
        print(f"  {day}: 全天可约")
    else:
        for u in used:
            print(f"  {day}: 已占用 {u.get('ShowStart')} - {u.get('ShowEnd')}")


def cmd_floors(c: SeatClient, area_code: str, day: str):
    """列出某个分馆下所有楼层/阅览室，以及它们的 address_code。"""
    rows = c._payload(c._post("SeatAddressHandler.ashx", {
        "data_type": "list",
        "selected_date": day,
        "area_code": area_code,
    }), allow_empty=True) or []
    if not rows:
        print(f"{area_code} 下没有楼层数据（日期 {day}？分馆代码对不对？）")
        return 1
    print(f"{area_code}  可选楼层（日期 {day}）：\n")
    print(f"  {'address_code':<18} {'名称'}")
    print(f"  {'-'*18} {'-'*30}")
    for r in rows:
        print(f"  {r.get('Code',''):<18} {r.get('Name','')}")
    print("\n把某一行的 Code 填进 config.json 该候选座位的 address_code 即可（拿不准就用 seat 命令验证）。")
    return 0


def cmd_resolve(c: SeatClient, seat_name: str):
    """用你自己的历史/当前记录，反查这个座位对应的编码，比猜更可靠。"""
    for r in c.current() + c.history(50):
        if r.get("SeatName") == seat_name:
            print(json.dumps({
                "seat_name": r["SeatName"],
                "area_code": r.get("Area_Code"),
                "address_code": r.get("Address_Code"),
                "seatno": r.get("SeatInfo_Code"),
                "seat_addr": r.get("Address_Name"),
            }, ensure_ascii=False))
            return
    s = Seat(seat_name).resolve()
    print("历史记录里没有，按命名规律推导：")
    print(json.dumps({
        "seat_name": s.seat_name, "area_code": s.area_code,
        "address_code": s.address_code, "seatno": s.seatno,
    }, ensure_ascii=False))


def try_book(c: SeatClient, seats: list[Seat], day: str, t0: int, t1: int) -> bool:
    """按优先级逐个候选座位尝试一次。全部失败返回 False。"""
    slot = f"{min_to_hhmm(t0)}-{min_to_hhmm(t1)}"
    for seat in seats:
        try:
            c.book(seat, day, t0, t1)
        except SeatError as e:
            # 座位被占 / 时段不合法 → 换下一个候选
            print(f"  ✗ {seat.seat_name:<12} {slot}  {e.msg}")
            continue
        print(f"  ✓ {seat.seat_name:<12} {day} {slot}  预约成功")
        return True
    return False


def cmd_once(c: SeatClient, cfg: dict, seats, day, t0, t1):
    ok = try_book(c, seats, day, t0, t1)
    if not ok:
        print("所有候选座位都没抢到。")
        return 1
    cmd_status(c)
    return 0


def cmd_watch(c: SeatClient, cfg: dict, seats, day, t0, t1):
    win = cfg.get("booking_window", {"open_hour": 7, "open_minute": 0})
    timing = cfg.get("timing", {})
    offset_ms = int(timing.get("fire_offset_ms", -300))
    interval = int(timing.get("retry_interval_ms", 120)) / 1000.0
    max_retry = float(timing.get("max_retry_seconds", 90))
    keep_alive = float(timing.get("keep_alive_seconds", 300))

    now = dt.datetime.now()
    fire = now.replace(hour=int(win["open_hour"]), minute=int(win["open_minute"]),
                       second=0, microsecond=0)
    if fire <= now:                       # 今天已过 → 明天
        fire += dt.timedelta(days=1)
    fire += dt.timedelta(milliseconds=offset_ms)

    slot = f"{min_to_hhmm(t0)}-{min_to_hhmm(t1)}"
    wait = (fire - dt.datetime.now()).total_seconds()
    print(f"将于 {fire:%Y-%m-%d %H:%M:%S}.{fire.microsecond // 1000:03d} 开抢 "
          f"{day} {slot}（{wait:.1f} 秒后），候选 {len(seats)} 个座位。")
    if wait > 0 and keep_alive > 0:
        print(f"等待期间每 {keep_alive:g} 秒校验会话（查询成功不代表登录有效期已延长）。")
        # 只在距开抢还有超过一个完整保活周期时请求。最后一段直接等待，
        # 避免保活请求卡在放号时刻，影响抢座请求。
        while True:
            wait = (fire - dt.datetime.now()).total_seconds()
            if wait <= keep_alive:
                break
            time.sleep(keep_alive)
            try:
                c.current()
            except SeatError as e:
                if e.code == -401 and cfg.get("_auth_capture"):
                    try:
                        c = refresh_login(c, cfg)
                        continue
                    except (SeatError, OSError, ValueError):
                        print("自动重新登录未通过验证，授权参数可能已失效，需要重新微信授权。",
                              file=sys.stderr)
                        return 2
                print(f"会话校验失败：{e.msg}", file=sys.stderr)
                print("请重新抓包后运行 "
                      "python extract_cookie.py <抓包文件> --write，再重新启动 watch。",
                      file=sys.stderr)
                return 2
            print(f"[{dt.datetime.now():%H:%M:%S}] 会话校验通过。")

    wait = (fire - dt.datetime.now()).total_seconds()
    if wait > 0:
        time.sleep(wait)

    deadline = time.monotonic() + max_retry
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if try_book(c, seats, day, t0, t1):
            print(f"第 {attempt} 次尝试成功。")
            cmd_status(c)
            return 0
        time.sleep(interval)
    print(f"{max_retry:.0f} 秒内重试 {attempt} 次仍未成功，放弃。")
    return 1


def refresh_login(c: SeatClient, cfg: dict) -> SeatClient:
    """可选的回调重登录尝试；只有受保护查询验证成功才替换客户端。"""
    from renew_cookie import renew_client

    print("正在尝试从登录抓包重新获取 Cookie...")
    fresh = renew_client(cfg, cfg["_auth_capture"])
    c.session.close()
    print("自动重新登录成功，当前预约接口验证通过。")
    return fresh


def cmd_cancel(c: SeatClient):
    rows = c.current()
    if not rows:
        print("当前没有可取消的预约。")
        return 1
    for r in rows:
        c.cancel(r["Id"], r["SeatName"])
        print(f"已取消 [{r['Id']}] {r['SeatName']}")
    return 0


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="湖南大学图书馆自动预约")
    ap.add_argument("cmd", choices=["status", "seat", "floors", "once", "watch", "cancel", "resolve"])
    ap.add_argument("arg", nargs="?", help="seat/resolve 的座位名")
    ap.add_argument("--date", help="预约日期：auto / today / tomorrow / dayafter / +N / YYYY-MM-DD")
    ap.add_argument("--time", help="时段 HH:MM,HH:MM 或 起始分钟,结束分钟")
    ap.add_argument("--seat", help="只抢这一个座位，覆盖 config.json 的候选列表")
    ap.add_argument("--area", default="hnu_lib1", help="floors 命令用：分馆代码（默认 hnu_lib1 总馆）")
    ap.add_argument("--auth-capture", help="watch 可选：会话失效时尝试使用此抓包中的授权回调重新登录；参数失效则停止")
    ap.add_argument("-c", "--config", default="config.json")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    c = client_from_config(cfg)
    if args.cmd == "watch" and args.auth_capture:
        cfg["_auth_capture"] = args.auth_capture
        try:
            c.current()
        except SeatError as e:
            if e.code != -401:
                print(f"启动时会话校验失败：{e.msg}", file=sys.stderr)
                return 2
            try:
                c = refresh_login(c, cfg)
            except (SeatError, OSError, ValueError):
                print("启动时重新登录未通过验证，请重新微信授权后更新 Cookie。", file=sys.stderr)
                return 2

    if args.cmd == "status":
        return cmd_status(c)
    if args.cmd == "cancel":
        return cmd_cancel(c)
    if args.cmd == "resolve":
        return cmd_resolve(c, args.arg or args.seat)
    if args.cmd == "floors":
        return cmd_floors(c, args.area, parse_date(cfg, args.date))
    if args.cmd == "seat":
        return cmd_seat(c, cfg, args.arg or args.seat, parse_date(cfg, args.date))

    # once / watch
    day = parse_date(cfg, args.date)
    t0, t1 = parse_time(cfg, args.time)
    seats = build_seats(cfg, args.seat)
    # 用全局参数把「最远可约天数」同步给 parse_date 的 auto
    try:
        sc = c.seat_config()
        cfg["_book_ahead_days"] = int(sc.get("BookAheadDays", 2))
        if cfg.get("seatdate", "auto") == "auto" and not args.date:
            day = (dt.date.today() + dt.timedelta(days=int(sc.get("BookAheadDays", 2)))).isoformat()
    except SeatError as e:
        print(f"取座位配置失败：{e}（cookie 可能已失效）", file=sys.stderr)

    if args.cmd == "once":
        return cmd_once(c, cfg, seats, day, t0, t1)
    return cmd_watch(c, cfg, seats, day, t0, t1)


if __name__ == "__main__":
    sys.exit(main() or 0)
