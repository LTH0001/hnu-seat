import unittest
from unittest.mock import Mock, patch
import requests
import booking_recovery as r
from libseat import SeatError


class RecoveryTests(unittest.TestCase):
    def run_flow(self, client, **kw):
        return r.submit_with_recovery(client, {'base_url': 'http://example.test'},
            Mock(), '2026-10-16', 630, 1350, Mock(), kw.get('login', Mock()),
            lambda row: row == 'match', kw.get('save', Mock()))

    def test_busy_seat_does_not_refresh_signature(self):
        c = Mock()
        c.book.side_effect = SeatError(1, '座位已被预约')
        with patch.object(r, 'fetch_des_key') as fetch:
            with self.assertRaises(SeatError):
                self.run_flow(c)
            fetch.assert_not_called()
        self.assertEqual(c.book.call_count, 1)

    def test_new_key_persisted_only_after_success(self):
        c, save = Mock(), Mock()
        c.book.side_effect = [SeatError(1, '签名错误'), None]
        with patch.object(r, 'fetch_des_key', return_value='12345678'):
            self.run_flow(c, save=save)
        save.assert_called_once_with({'des_key': '12345678', 'signer': 'des'})
        self.assertEqual(c.book.call_count, 2)

    def test_node_fallback_has_bounded_retries(self):
        c, save = Mock(), Mock()
        c.book.side_effect = SeatError(1, '签名错误')
        with patch.object(r, 'fetch_des_key', return_value='12345678'), \
             patch.object(r, 'NodeSigner') as node:
            with self.assertRaises(SeatError):
                self.run_flow(c, save=save)
            node.return_value.refresh.assert_called_once()
        self.assertEqual(c.book.call_count, 3)
        save.assert_not_called()

    def test_timeout_queries_and_never_resubmits(self):
        c = Mock()
        c.book.side_effect = requests.Timeout()
        c.current.return_value = []
        with self.assertRaisesRegex(RuntimeError, '尚未确认'):
            self.run_flow(c)
        self.assertEqual(c.book.call_count, 1)

    def test_timeout_existing_booking_counts_as_success(self):
        c = Mock()
        c.book.side_effect = requests.Timeout()
        c.current.return_value = ['match']
        self.assertIs(self.run_flow(c), c)
        self.assertEqual(c.book.call_count, 1)

    def test_unparseable_response_does_not_resubmit(self):
        c = Mock()
        c.book.side_effect = SeatError(-500, '响应不是 JSON')
        c.current.return_value = []
        with self.assertRaisesRegex(RuntimeError, '尚未确认'):
            self.run_flow(c)
        self.assertEqual(c.book.call_count, 1)

    def test_auth_recovery_only_once_and_closes_old_session(self):
        c, fresh, login = Mock(), Mock(), Mock()
        old_session = c.session
        c.book.side_effect = [SeatError(-401, 'expired'), None]
        login.return_value = (fresh, [])
        self.assertIs(self.run_flow(c, login=login), c)
        login.assert_called_once()
        old_session.close.assert_called_once()
        self.assertIs(c.session, fresh.session)

    def test_auth_recovery_existing_booking_does_not_resubmit(self):
        c, login = Mock(), Mock()
        c.book.side_effect = SeatError(-401, 'expired')
        login.return_value = (Mock(), ['match'])
        self.run_flow(c, login=login)
        self.assertEqual(c.book.call_count, 1)


if __name__ == '__main__':
    unittest.main()
