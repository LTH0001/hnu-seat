"""本地模拟登录服务的回归检查，不访问真实预约系统。"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from libseat import SeatClient, SeatError
from renew_cookie import callback_from_capture, cookie_header, renew_client


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        if "reject" in self.path:
            self.end_headers()
            self.wfile.write("链接地址错误".encode("utf-8"))
        else:
            self.send_header("Set-Cookie", "cookie_come_sno=fresh; Path=/")
            self.send_header("Set-Cookie", "ASP.NET_SessionId=newsession; Path=/")
            self.end_headers()
            self.wfile.write(b"ok")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        incoming = self.headers.get("Cookie", "")
        self.server.seen.append(incoming)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if "cookie_come_sno=fresh" in incoming:
            self.send_header("Set-Cookie", "cookie_come_sno=rotated; Path=/")
        self.end_headers()
        if "cookie_come_sno=" in incoming:
            response = {"code": 0, "data": "[]"}
        else:
            response = {"code": 1, "data": "", "msg": "请先登录"}
        self.wfile.write(json.dumps(response).encode())


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.seen = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.cfg = {"base_url": cls.base, "cookie": "", "proxy": ""}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_success_validated_and_rotated_cookie_sent_next_time(self):
        with patch("renew_cookie.callback_from_capture", return_value=self.base + "/login"):
            client = renew_client(self.cfg, "fixture")
        try:
            self.assertIn("cookie_come_sno=rotated", cookie_header(client))
            client.current()
            self.assertIn("cookie_come_sno=rotated", self.server.seen[-1])
            self.assertNotIn("Cookie", client.session.headers)
        finally:
            client.session.close()

    def test_error_page_is_rejected_even_with_http_200(self):
        with patch("renew_cookie.callback_from_capture", return_value=self.base + "/reject"):
            with self.assertRaisesRegex(SeatError, "链接地址错误"):
                renew_client(self.cfg, "fixture")

    def test_auth_failure_not_treated_as_empty_booking_list(self):
        with self.assertRaises(SeatError):
            SeatClient._payload({"code": 1, "data": "", "msg": "请先登录"}, allow_empty=True)
        self.assertIsNone(SeatClient._payload(
            {"code": 1, "data": "", "msg": "暂无数据"}, allow_empty=True))

    def test_callback_host_is_restricted(self):
        host = self.base.split("//", 1)[1]
        request = ("GET /api/hnu/CheckReader.aspx?wfw_token=fake HTTP/1.1\r\n"
                   "Host: " + host + "\r\n\r\n").encode()
        with patch("renew_cookie.Path.read_bytes", return_value=request):
            self.assertEqual(callback_from_capture("fixture", self.base),
                             self.base + "/api/hnu/CheckReader.aspx?wfw_token=fake")
            with self.assertRaises(SeatError):
                callback_from_capture("fixture", "http://other.invalid")


if __name__ == "__main__":
    unittest.main()
