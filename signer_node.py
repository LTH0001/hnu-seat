# -*- coding: utf-8 -*-
"""
保底签名方案：用 Node 跑站点自带的 base3jm.js 里的 jsencrypt()。

纯 Python 的 DES 复刻只对上了抓包 8 块中的 4 块（见 README「关于签名」），
原因未查明。而直接执行站点自己的 JS 是**定义上正确**的：站点怎么算，我们怎么算。

代价：
  - 需要 Node（装过就行，`node --version` 能跑）
  - 首次运行会把 base3jm.js 下载到 vendor/ 并缓存；站点换密钥时会自动重新下载
  - 会执行一段从站点下载的 JS。只有在你认可这一点时才把 config.json 的
    "signer" 设成 "node"。默认是 "des"（纯 Python）。

用法：把 config.json 改成  "signer": "node"  即可，其余不用动。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 NetType/WIFI "
      "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a13) "
      "UnifiedPCWindowsWechat(0xf2541d41) XWEB/25560 Flue")

# Node 侧的小工具：在受限上下文里加载 base3jm.js 并调用它的 jsencrypt()
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = {
  Math, String, Array, Object, JSON, Number, Boolean, Date, RegExp, Error,
  parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  unescape, escape,
  document: { write() {}, getElementById() { return null; } },
  window: {}, console,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { timeout: 5000 });
if (typeof sandbox.jsencrypt !== 'function') {
  console.error('base3jm.js 里没有 jsencrypt()'); process.exit(3);
}
console.log(sandbox.jsencrypt(process.argv[3]));
"""

BASE3JM_CANDIDATES = [
    "/mobile/js/common/base3jm.js",
    "/mobile/js/common/base3jm.js?v=20250306",
]


def _fetch_base3jm(base_url: str, proxy: str) -> bytes:
    base = base_url.rstrip("/")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last = None
    for path in BASE3JM_CANDIDATES:
        try:
            r = requests.get(base + path, headers={"User-Agent": UA},
                             timeout=20, proxies=proxies, allow_redirects=False)
            if r.ok and b'jsencrypt' in r.content and b'<html' not in r.content[:200].lower():
                return r.content
        except Exception as e:                       # noqa: BLE001
            last = e
    raise RuntimeError(f"下载 base3jm.js 失败：{last}")


class NodeSigner:
    """惰性下载 + 缓存 base3jm.js，用 Node 生成签名。"""

    def __init__(self, base_url: str, proxy: str = "", cache_dir: str = "vendor"):
        self.base_url = base_url
        self.proxy = proxy
        self.cache_dir = cache_dir
        self.js_path = os.path.join(cache_dir, "base3jm.js")
        self.harness_path = os.path.join(cache_dir, "sign.js")
        self._node = None
        self._key_hint = None

    # -- 环境 ---------------------------------------------------------------
    def _check_node(self) -> str:
        if self._node is None:
            try:
                out = subprocess.run(["node", "--version"], capture_output=True,
                                     text=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except FileNotFoundError:
                raise RuntimeError("找不到 node，请先安装 Node.js，或把 signer 改回 \"des\"")
            if out.returncode != 0:
                raise RuntimeError("node 不可用：" + (out.stderr or "").strip())
            self._node = out.stdout.strip()
            print(f"[signer] 使用 Node {self._node}", file=sys.stderr)
        return self._node

    def _ensure_files(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        if not os.path.exists(self.harness_path):
            with open(self.harness_path, "w", encoding="utf-8") as f:
                f.write(HARNESS)
        if not os.path.exists(self.js_path):
            data = _fetch_base3jm(self.base_url, self.proxy)
            with open(self.js_path, "wb") as f:
                f.write(data)
            print(f"[signer] 已缓存 {self.js_path} "
                  f"({len(data)} 字节, sha1={hashlib.sha1(data).hexdigest()[:12]})",
                  file=sys.stderr)

    # -- 对外 ---------------------------------------------------------------
    def refresh(self) -> None:
        """验证 Node 和新脚本可执行后，原子替换缓存。"""
        self._check_node()
        data = _fetch_base3jm(self.base_url, self.proxy)
        os.makedirs(self.cache_dir, exist_ok=True)
        Path(self.harness_path).write_text(HARNESS, encoding='utf-8')
        temp = Path(self.js_path + '.tmp')
        try:
            temp.write_bytes(data)
            out = subprocess.run(['node', self.harness_path, str(temp), '0123456789ABCDEF'],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if out.returncode or not out.stdout.strip():
                raise RuntimeError('官网签名脚本校验失败，保留原缓存。')
            temp.replace(self.js_path)
        finally:
            temp.unlink(missing_ok=True)

    def __call__(self, plain: str) -> str:
        self._check_node()
        self._ensure_files()
        out = subprocess.run(
            ["node", self.harness_path, self.js_path, plain],
            capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if out.returncode != 0:
            raise RuntimeError("Node 计算签名失败：" + (out.stderr or "").strip())
        return out.stdout.strip()


def make_signer(kind: str, base_url: str, proxy: str = ""):
    """按 config 的 signer 字段返回签名函数。"""
    if kind == "node":
        return NodeSigner(base_url, proxy)
    from libseat import jsencrypt
    return jsencrypt
