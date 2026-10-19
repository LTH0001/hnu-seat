# -*- coding: utf-8 -*-
"""
从站点的 base3jm.js 里把 jsencrypt() 用的 DES 密钥抠出来。

密钥被 javascript-obfuscator 混淆成 base64 + RC4 的字符串数组，这个脚本
复刻它的解码器（atob -> decodeURIComponent -> RC4）还原出明文密钥。

    python fetch_key.py            # 抓取并打印
    python fetch_key.py --write    # 写回 config.json 的 des_key 字段

需要能访问 chaxin.hnu.edu.cn（校园网 / VPN / Clash 代理）。
"""
from __future__ import annotations

import argparse
import ast
import base64
import json
import re
import sys
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 NetType/WIFI "
      "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a13) "
      "UnifiedPCWindowsWechat(0xf2541d41) XWEB/25560 Flue")
ENTRY = "/mobile/html/seat/seatdate.html?v=20250306"


# ---------------------------------------------------------------- 抓脚本
def _abs_url(base: str, src: str) -> str:
    if src.startswith("//"):
        return "http:" + src
    if src.startswith("http"):
        return src
    if src.startswith("/"):
        return base + src
    return base + "/mobile/html/seat/" + src


def collect_scripts(base: str, proxies) -> list[str]:
    """收集页面加载的所有 JS（含 commonJsAndCss.js 用 document.write 注入的）。"""
    html = requests.get(base + ENTRY, headers={"User-Agent": UA},
                        timeout=15, proxies=proxies).text
    urls = [_abs_url(base, s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)]

    texts = []
    for u in list(urls):
        try:
            js = requests.get(u, headers={"User-Agent": UA}, timeout=20, proxies=proxies).text
        except Exception:
            continue
        texts.append((u, js))
        # 跟进 document.write 注入的脚本（JS 里 src\x3d"..." 的等号是转义的）
        decoded = js.replace("\\x3d", "=").replace("\\x3D", "=")
        for s in re.findall(r'''src\s*=\s*["']([^"']+\.js[^"']*)["']''', decoded):
            v = _abs_url(base, s)
            if v not in urls:
                urls.append(v)
                try:
                    texts.append((v, requests.get(v, headers={"User-Agent": UA},
                                                  timeout=20, proxies=proxies).text))
                except Exception:
                    pass
    return texts


# ---------------------------------------------------------------- 解混淆
def _rc4(key: str, data: str) -> str:
    h = list(range(256))
    g = 0
    kb = [ord(c) for c in key]
    for k in range(256):
        g = (g + h[k] + kb[k % len(kb)]) % 256
        h[k], h[g] = h[g], h[k]
    out, k, g = [], 0, 0
    for ch in data:
        k = (k + 1) % 256
        g = (g + h[k]) % 256
        h[k], h[g] = h[g], h[k]
        out.append(chr(ord(ch) ^ h[(h[k] + h[g]) % 256]))
    return "".join(out)


def _decode(tok: str, key: str) -> str:
    raw = base64.b64decode(tok + "=" * (-len(tok) % 4))
    s = urllib.parse.unquote("".join("%%%02x" % b for b in raw), encoding="utf-8")
    return _rc4(key, s)


def extract_key(js: str) -> str | None:
    """从 base3jm.js 里解出 jsencrypt 用的密钥字符串。"""
    # 1) 字符串数组 + 旋转次数
    m = re.search(r'var\s+(_0x[0-9a-fA-F]+)\s*=\s*"(.*?)"\s*\.\s*split\(" "\)', js, re.S)
    if not m:
        return None
    arr_name, lit = m.group(1), m.group(2)
    arr = lit.replace("\\x3d", "=").replace("\\x3D", "=").split(" ")

    rot_m = re.search(re.escape(arr_name) + r"\s*,\s*(\d+)\)", js)
    if rot_m:
        rot = int(rot_m.group(1)) % len(arr)
        arr = arr[rot:] + arr[:rot]

    # 2) jsencrypt 里的 des(<decode>(...), ...) 调用
    jm = re.search(r"function\s+jsencrypt\s*\((\w+)\)\s*\{(.*?)\}", js, re.S)
    if not jm:
        return None
    body = jm.group(2)
    call = re.search(r'des\(\s*[\w\$]+\("(0x[0-9a-fA-F]+)"\s*,\s*"((?:[^"\\]|\\.)*)"\)', body)
    if not call:
        # 也可能被拆成变量
        call = re.search(r'\(_?0x[\w\$]+\("(0x[0-9a-fA-F]+)"\s*,\s*"((?:[^"\\]|\\.)*)"\)', js)
    if not call:
        return None
    idx, key = call.group(1), call.group(2)
    try:
        key = key.encode("latin1").decode("unicode_escape")
    except Exception:
        pass
    return _decode(arr[int(idx, 16)], key)


def fetch_des_key(base_url: str, proxy: str = "") -> str | None:
    base = base_url.rstrip("/")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    for url, js in collect_scripts(base, proxies):
        if "jsencrypt" in js and ("des(" in js or "des_createKeys" in js):
            k = extract_key(js)
            if k:
                print(f"来源: {url}", file=sys.stderr)
                return k
    return None


def main():
    ap = argparse.ArgumentParser(description="提取 jsencrypt 的 DES 密钥")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    key = fetch_des_key(cfg["base_url"], cfg.get("proxy", ""))
    if not key:
        print("没提到密钥。确认网络可达；或手动在 base3jm.js 里搜 jsencrypt 看 des(...) 的第一个参数。",
              file=sys.stderr)
        return 1
    print(key)
    if args.write:
        cfg["des_key"] = key
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("已写入 config.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
