# -*- coding: utf-8 -*-
"""
湖南大学图书馆座位预约 —— API 客户端

协议来自 hnu.pcapng 抓包 + 预约页 seatdate.js / base3jm.js 还原：

    getSeatInit  -> 该座位的可约时间范围(ScaleTime / sTime / eTime)
    getSeatDate  -> 该座位指定日期已被占用的时段
    getCode      -> 一次性随机数(16 字节 hex)
    seatDate     -> 提交预约，code = jsencrypt(getCode 的返回值)

所有接口都是 POST http://chaxin.hnu.edu.cn/apim/seat/<Handler>.ashx
表单编码(application/x-www-form-urlencoded)，返回 {"code":0,"msg":...,"data":"<JSON字符串>"}

【签名算法】页面里的 jsencrypt() 定义在 /mobile/js/common/base3jm.js，
是 Tero des.js 的加密分支，密钥硬编码为 "TBAIHN58"：

    jsencrypt(a) = hex( des("TBAIHN58", a, 1, 0) )

des() 加密时每 4 个字符打成 1 个 64 位块：
    c = ch0<<16 | ch1 ;  b = ch2<<16 | ch3 ;  块 = (c<<32)|b
每块输出 8 字节，所以 32 字符 -> 8 块 -> 64 字节 -> 128 个 hex 字符。
"""
from __future__ import annotations

import json
from functools import partial
import re
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
    "NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541d41) XWEB/25560 Flue"
)


class SeatError(RuntimeError):
    """接口返回 code != 0，或会话失效。"""

    def __init__(self, code: int, msg: str, raw: Any = None):
        self.code = code
        self.msg = msg
        self.raw = raw
        super().__init__(f"[{code}] {msg}")


# --------------------------------------------------------------------------
# 签名：等价于 base3jm.js 里的 jsencrypt()
# --------------------------------------------------------------------------
DES_KEY = "TBAIHN58"      # base3jm.js 中 _0x24fe("0x12","fUWe") 解出的值
DES_BLOCK_PAD = 8         # des() 内部固定追加 8 个 NUL，供非 4 倍数长度补齐


def _pack(message: str) -> bytes:
    """按 des() 加密分支的规则，把字符串打成 64 位块（每块 4 个字符）。"""
    padded = message + "\0" * DES_BLOCK_PAD
    n = len(message)
    out = bytearray()
    m = 0
    while m < n:
        ch = [ord(padded[m + k]) for k in range(4)]
        c = (ch[0] << 16) | ch[1]
        b = (ch[2] << 16) | ch[3]
        out += ((c << 32) | b).to_bytes(8, "big")
        m += 4
    return bytes(out)


def jsencrypt(plain: str, key: str = DES_KEY) -> str:
    """复刻 base3jm.js 的 jsencrypt()：DES-ECB 后转大写 hex。"""
    from Crypto.Cipher import DES

    data = _pack(plain)
    if not data:
        return ""
    kb = key.encode("ascii")
    if len(kb) < 8:                       # des_createKeys 对短密钥补 \0 到 8 字节
        kb = kb.ljust(8, b"\0")
    ct = DES.new(kb[:8], DES.MODE_ECB).encrypt(data)
    return ct.hex().upper()


# --------------------------------------------------------------------------
# 座位编号推导
# --------------------------------------------------------------------------
SEAT_RE = re.compile(r"^(?P<lib>\d+)-(?P<floor>[0-9A-Za-z]+)-(?P<no>\d+)$")


@dataclass
class Seat:
    seat_name: str                     # 1-5F1-070   （页面座位名，也是提交时的 seatname）
    area_code: str = ""                # hnu_lib1
    address_code: str = ""             # hnu_lib1_5F1
    seatno: str = ""                   # HNU15F1070  （提交时的 seatno / SeatInfo_Code）

    def resolve(self) -> "Seat":
        """把没填的字段按 hnu 的命名规律补齐。"""
        if not self.seatno:
            if self.seat_name.upper().startswith("HNU"):
                self.seatno = self.seat_name            # 研讨间等本身就是 HNU 编号
            else:
                self.seatno = "HNU" + self.seat_name.replace("-", "")
        m = SEAT_RE.match(self.seat_name)
        if m:
            if not self.area_code:
                self.area_code = f"hnu_lib{m.group('lib')}"
            if not self.address_code:
                self.address_code = f"{self.area_code}_{m.group('floor')}"
        return self


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------
@dataclass
class SeatClient:
    base_url: str
    cookie: str
    proxy: str = ""                    # 如 "http://127.0.0.1:7897"（Clash Verge）
    timeout: float = 10.0
    signer: Any = None                 # 签名函数；None = 用内置 jsencrypt()，见 signer_node
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        s = self.session
        if self.proxy:
            s.proxies.update({"http": self.proxy, "https": self.proxy})
        s.headers.update({
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/mobile/html/seat/seatdate.html?v=20250306",
        })
        s.headers.pop("Cookie", None)
        if self.cookie:
            parsed = SimpleCookie()
            parsed.load(self.cookie.strip())
            for name, morsel in parsed.items():
                s.cookies.set(name, morsel.value,
                              domain=urlsplit(self.base_url).hostname, path="/")

    # -- 底层 ---------------------------------------------------------------
    def _post(self, handler: str, data: dict) -> dict:
        r = self.session.post(
            f"{self.base_url}/apim/seat/{handler}",
            data=data, timeout=self.timeout,
        )
        r.raise_for_status()
        r.encoding = "utf-8"          # 服务端声明 charset=utf-8，但别依赖自动探测
        txt = r.text.strip()
        if "页面停留时间过长" in txt:
            raise SeatError(-401, "页面授权已失效，请重新进入微信授权入口或更新登录凭据")
        if txt.startswith("<!") or "<html" in txt[:200].lower():
            raise SeatError(-401, "会话已失效（返回的是登录页），请更新 config.json 里的 cookie")
        try:
            return r.json()
        except ValueError:
            raise SeatError(-500, f"响应不是 JSON: {txt[:200]}")

    @staticmethod
    def _payload(resp: dict, allow_empty: bool = False) -> Any:
        code = resp.get("code")
        data = resp.get("data", "")
        if code != 0:
            # 查询类接口用 code=1 表示「无数据」，不是错误
            # （实测：current_seat_date_list -> "暂无数据"；getSeatDate -> "该座位暂无任何预约"）
            empty_message = resp.get("msg", "")
            if (allow_empty and code == 1 and (data == "" or data is None)
                    and any(marker in empty_message for marker in
                            ("暂无数据", "暂无任何预约", "暂无预约"))):
                return None
            raise SeatError(code if code is not None else -1,
                            resp.get("msg", "未知错误"), resp)
        if (data == "" or data is None) and allow_empty:
            return None
        try:
            return json.loads(data)
        except (TypeError, ValueError):
            return data  # 有的接口 data 本身就是裸字符串（比如 getCode）

    # -- 业务接口 -----------------------------------------------------------
    def seat_config(self) -> dict:
        """全局参数：BookAheadDays / BookTimeScale / DateCount / MaxScaleNum ..."""
        return self._payload(self._post("SeatConfigHandler.ashx", {"data_type": "seat_config"}))

    def seat_init(self, address_code: str, seatno: str) -> dict:
        """单个座位的可约时间范围。返回 {sTime, eTime, BookTimeScale, ScaleData:"08:00,12:00,..."}"""
        p = self._payload(self._post("SeatDateHandler.ashx", {
            "data_type": "getSeatInit",
            "addresscode": address_code,
            "seatcode": seatno,
        }), allow_empty=True)
        return (p or [{}])[0] if isinstance(p, list) else p

    def seat_date(self, seatno: str, seatdate: str) -> list:
        """该座位在某天已被占用的时段列表（空 = 全天空闲）。"""
        p = self._payload(self._post("SeatDateHandler.ashx", {
            "data_type": "getSeatDate",
            "seatno": seatno,
            "seatdate": seatdate,
        }), allow_empty=True)
        return p or []

    def get_code(self) -> str:
        """一次性随机数，16 字节 hex。"""
        return self._payload(self._post("SeatDateHandler.ashx", {"data_type": "getCode"}))

    def book(self, seat: Seat, seatdate: str, start_min: int, end_min: int) -> dict:
        """提交预约。datetime 是 "起始分钟,结束分钟"（从 00:00 起算）。"""
        seed = self.get_code()
        code = (self.signer or jsencrypt)(seed)
        return self._payload(self._post("SeatDateHandler.ashx", {
            "code": code,
            "data_type": "seatDate",
            "seatno": seat.seatno,
            "seatname": seat.seat_name,
            "seatdate": seatdate,
            "datetime": f"{start_min},{end_min}",
        }))   # 提交类接口：任何非 0 code 都是失败，必须抛出

    def current(self) -> list:
        """当前生效的预约记录。"""
        return self._payload(self._post("SeatRecordHandler.ashx",
                                        {"data_type": "current_seat_date_list"}),
                             allow_empty=True) or []

    def history(self, top: int = 20) -> list:
        """历史预约记录（用来反查座位的 area/address/seatno 编码）。"""
        return self._payload(self._post("SeatRecordHandler.ashx",
                                        {"data_type": "history_seat_date_list", "top": top}),
                             allow_empty=True) or []

    def cancel(self, record_id: int, seat_name: str) -> dict:
        return self._payload(self._post("SeatDateHandler.ashx", {
            "data_type": "cancel_seat_date", "id": record_id, "seat_name": seat_name,
        }))

    def sign_out(self, record_id: int) -> dict:
        return self._payload(self._post("SeatDateHandler.ashx",
                                        {"data_type": "signOut", "id": record_id}))


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def hhmm_to_min(s: str) -> int:
    """'12:00' / '12:00:00' -> 720"""
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def min_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def load_config(path: str = "config.json") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def client_from_config(cfg: dict) -> SeatClient:
    signer = partial(jsencrypt, key=cfg.get("des_key", DES_KEY))
    if cfg.get("signer", "des") == "node":
        from signer_node import NodeSigner      # 可选依赖，只有选了才导入
        signer = NodeSigner(cfg["base_url"], cfg.get("proxy", "") or "")
    return SeatClient(
        base_url=cfg["base_url"],
        cookie=cfg.get("cookie", ""),
        proxy=cfg.get("proxy", "") or "",
        timeout=float(cfg.get("timing", {}).get("request_timeout", 10)),
        signer=signer,
    )
