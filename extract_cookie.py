# -*- coding: utf-8 -*-
"""
从抓包文件里自动提取 chaxin.hnu.edu.cn 的 Cookie，直接写进 config.json。

微信内置浏览器没有开发者工具，但微信 PC 版会走系统代理，
用 Fiddler / Charles / Wireshark 抓一份 pcapng 就能把 Cookie 捞出来。

    python extract_cookie.py traffic.pcapng              # 只打印
    python extract_cookie.py traffic.pcapng --write      # 写回 config.json

也支持 Fiddler 导出的 .saz（本质是 zip）：
    python extract_cookie.py session.saz --write
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
from collections import defaultdict

HOST_HINT = "chaxin.hnu.edu.cn"
COOKIE_KEYS = ("ASP.NET_SessionId", "cookie_come_sno")


# ------------------------------------------------------------------ 解析
def _read_pcapng(data: bytes) -> list[bytes]:
    """返回所有抓到的原始帧。"""
    off, ifaces, pkts = 0, [], []
    while off + 12 <= len(data):
        btype, blen = struct.unpack_from("<II", data, off)
        if blen < 12 or off + blen > len(data):
            break
        body = data[off + 8: off + blen - 4]
        if btype == 0x0A0D0D0A:            # Section Header
            ifaces = []
        elif btype == 0x00000001:          # Interface Description
            ifaces.append(struct.unpack_from("<H", body, 0)[0])
        elif btype == 0x00000006:          # Enhanced Packet
            ifid, _, _, caplen, _ = struct.unpack_from("<IIIII", body, 0)
            pkts.append((ifid, body[20:20 + caplen]))
        elif btype == 0x00000003:          # Simple Packet
            pkts.append((0, body[4:]))
        off += blen
    return ifaces, pkts


def _tcp_payloads(ifaces, pkts):
    """(src,sport,dst,dport) -> [payload...]"""
    streams = defaultdict(list)
    for ifid, pkt in pkts:
        lt = ifaces[ifid] if ifid < len(ifaces) else 1
        if lt == 1:                                    # Ethernet
            if len(pkt) < 14:
                continue
            et = struct.unpack_from(">H", pkt, 12)[0]
            ipoff = 14
            if et == 0x8100:
                et = struct.unpack_from(">H", pkt, 16)[0]
                ipoff = 18
        elif lt == 0:                                  # NULL / loopback
            if len(pkt) < 4 or struct.unpack_from("<I", pkt, 0)[0] not in (2, 24, 28, 30):
                continue
            ipoff, et = 4, 0x0800
        elif lt in (101, 228):
            ipoff, et = 0, 0x0800
        else:
            continue
        if et != 0x0800 or len(pkt) < ipoff + 20 or pkt[ipoff] >> 4 != 4:
            continue
        ihl = (pkt[ipoff] & 0xF) * 4
        tot = struct.unpack_from(">H", pkt, ipoff + 2)[0]
        if pkt[ipoff + 9] != 6 or len(pkt) < ipoff + ihl + 20:
            continue
        t = ipoff + ihl
        sport, dport = struct.unpack_from(">HH", pkt, t)
        doff = (pkt[t + 12] >> 4) * 4
        src = ".".join(map(str, pkt[ipoff + 12:ipoff + 16]))
        dst = ".".join(map(str, pkt[ipoff + 16:ipoff + 20]))
        pl = pkt[t + doff: ipoff + tot]
        if pl:
            streams[(src, sport, dst, dport)].append(pl)
    return {k: b"".join(v) for k, v in streams.items()}


def _http_from_saz(data: bytes) -> bytes:
    """把一个 .saz 里所有会话的请求/响应文本拼起来。"""
    out = []
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        for n in z.namelist():
            if n.endswith(("_c.txt", "_s.txt")):
                out.append(z.read(n))
    return b"\n".join(out)


# ------------------------------------------------------------------ 提取
def find_cookies(raw: bytes) -> list[str]:
    """按出现顺序返回所有 Cookie 头的值。"""
    found = []
    for m in re.finditer(rb"(?im)^Cookie:\s*(.+?)\s*$", raw):
        try:
            found.append(m.group(1).decode("latin1").strip())
        except Exception:
            pass
    return found


def pick_best(cookies: list[str]) -> str | None:
    """优先选带 ASP.NET_SessionId 的、且看起来最完整的。"""
    good = [c for c in cookies if COOKIE_KEYS[0] in c]
    pool = good or cookies
    if not pool:
        return None
    # 去重后取最长的一条（最完整）
    uniq = sorted(set(pool), key=len, reverse=True)
    # 同一会话可能重复出现，用 SessionId 归并
    best, seen = [], set()
    for c in uniq:
        m = re.search(r"ASP\.NET_SessionId=([^;]+)", c)
        key = m.group(1) if m else c[:40]
        if key in seen:
            continue
        seen.add(key)
        best.append(c)
    return best[0] if best else None


def extract(path: str) -> str | None:
    with open(path, "rb") as f:
        data = f.read()

    if data[:2] == b"PK":                       # Fiddler .saz
        raw = _http_from_saz(data)
        return pick_best(find_cookies(raw))

    if data[:4] == b"\x0a\x0d\x0d\x0a":         # pcapng
        ifaces, pkts = _read_pcapng(data)
        streams = _tcp_payloads(ifaces, pkts)
        # 只看发往图书馆的流
        cookies = []
        for (src, sport, dst, dport), buf in streams.items():
            if HOST_HINT.encode() in buf:
                cookies += find_cookies(buf)
        if not cookies:                          # 兜底：全量扫
            cookies = find_cookies(b"".join(streams.values()))
        return pick_best(cookies)

    # 其它格式（Fiddler 导出的 .txt 等）直接当文本扫
    return pick_best(find_cookies(data))


def main():
    ap = argparse.ArgumentParser(description="从抓包文件提取图书馆 Cookie")
    ap.add_argument("capture", help="pcapng / .saz / 文本")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--write", action="store_true", help="写回 config.json")
    args = ap.parse_args()

    cookie = extract(args.capture)
    if not cookie:
        print("没找到 Cookie。确认抓包时在微信里打开过 chaxin.hnu.edu.cn，"
              "并且抓的是请求方向。", file=sys.stderr)
        return 1

    print(cookie)

    if args.write:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["cookie"] = cookie
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("已写入 config.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
