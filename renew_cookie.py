# -*- coding: utf-8 -*-
"""使用抓包中的本站授权回调尝试重新登录；授权参数失效时不能自动生成。

    python renew_cookie.py --capture test.pcapng --write

仅访问登录回调及当前预约查询，不提交或取消预约。不打印授权参数或 Cookie。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import requests

from extract_cookie import _http_from_saz, _read_pcapng, _tcp_payloads
from libseat import SeatClient, SeatError, client_from_config, load_config

LOGIN_PATH = "/api/hnu/CheckReader.aspx"


def callback_from_capture(path: str, base_url: str) -> str:
    """从本站请求提取回调；不接受其他域名，不向外站发送授权参数。"""
    data = Path(path).read_bytes()
    if data[:2] == b"PK":
        chunks = [_http_from_saz(data)]
    elif data[:4] == b"\x0a\x0d\x0d\x0a":
        chunks = list(_tcp_payloads(*_read_pcapng(data)).values())
    else:
        chunks = [data]
    base = urlsplit(base_url)
    candidates = []
    pattern = rb"(?m)^GET ([^\r\n ]+) HTTP/1\.[01]\r?\n(.*?)(?:\r\n\r\n|\n\n)"
    for chunk in chunks:
        for match in re.finditer(pattern, chunk, re.S):
            target, headers = match.groups()
            host = re.search(rb"(?im)^Host:\s*([^\r\n]+)", headers)
            if not host or host.group(1).decode("latin1").strip().lower() != base.netloc.lower():
                continue
            target = urlsplit(target.decode("latin1"))
            if target.path != LOGIN_PATH or not target.query:
                continue
            if target.netloc and target.netloc.lower() != base.netloc.lower():
                continue
            keys = dict(parse_qsl(target.query))
            if not keys.get("wfw_token"):
                continue
            candidates.append(urlunsplit((base.scheme, base.netloc, LOGIN_PATH, target.query, "")))
    if not candidates:
        raise SeatError(-401, "抓包中没有本站 CheckReader.aspx 授权请求，请从微信正式入口重新进入后抓包")
    # 流拼接并非跨连接时间排序；建议每份抓包仅包含一次重新授权。
    return candidates[-1]


def renew_client(cfg: dict, capture: str) -> SeatClient:
    """用已有授权参数重新请求 Cookie，然后以受保护接口验证身份。"""
    url = callback_from_capture(capture, cfg["base_url"])
    fresh = client_from_config({**cfg, "cookie": ""})
    try:
        response = fresh.session.get(url, timeout=fresh.timeout, allow_redirects=False)
        response.encoding = "utf-8"
        if "链接地址错误" in response.text:
            raise SeatError(-401, "登录回调返回“链接地址错误”，现有授权参数不能重用，需要重新微信授权")
        if response.status_code not in (200, 301, 302, 303, 307, 308):
            raise SeatError(-401, f"登录回调返回 HTTP {response.status_code}，需要重新微信授权")
        if not fresh.session.cookies.get_dict().get("cookie_come_sno"):
            raise SeatError(-401, "登录回调未下发身份 Cookie，授权参数可能已失效，需要重新微信授权")
        # 收到 Set-Cookie 还不能证明可用，以当前预约接口成功响应为准。
        fresh.current()
    except requests.RequestException:
        fresh.session.close()
        # requests 异常可能包含带凭据的 URL，不原样打印。
        raise SeatError(-401, "重新登录或校验时网络请求失败，请检查代理和连接") from None
    except SeatError:
        fresh.session.close()
        raise
    return fresh


def cookie_header(client: SeatClient) -> str:
    # 用实际本站请求的准备过程按域名、路径和到期时间筛选 Cookie。
    request = requests.Request("POST", client.base_url + "/apim/seat/SeatRecordHandler.ashx")
    return client.session.prepare_request(request).headers.get("Cookie", "")


def main():
    parser = argparse.ArgumentParser(description="从登录抓包尝试重新获取有效 Cookie（不预约）")
    parser.add_argument("--capture", required=True, help="含一次新授权回调的 pcapng / saz / HTTP 文本")
    parser.add_argument("-c", "--config", default="config.json")
    parser.add_argument("--write", action="store_true", help="验证通过后更新 config.json 的 cookie")
    args = parser.parse_args()
    try:
        fresh = renew_client(load_config(args.config), args.capture)
        try:
            if args.write:
                config = json.loads(Path(args.config).read_text(encoding="utf-8"))
                config["cookie"] = cookie_header(fresh)
                Path(args.config).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
            print("重新获取 Cookie 成功，当前预约接口验证通过。" +
                  (" 已更新配置。" if args.write else " 未写入配置。"))
        finally:
            fresh.session.close()
    except (SeatError, OSError, ValueError) as exc:
        print("重新获取 Cookie 失败：" + (exc.msg if isinstance(exc, SeatError) else "配置或抓包文件读取失败"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
