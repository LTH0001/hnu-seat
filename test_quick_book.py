import unittest
from unittest.mock import Mock, patch
import quick_book as q
from libseat import SeatError, client_from_config, jsencrypt


class QuickTests(unittest.TestCase):
    def test_configured_signing_key_is_used(self):
        c = client_from_config({'base_url': 'http://example.test', 'des_key': '12345678'})
        try:
            self.assertEqual(c.signer('ABCD'), jsencrypt('ABCD', '12345678'))
        finally:
            c.session.close()

    def test_failed_login_closes_client(self):
        c = Mock()
        c.current.side_effect = SeatError(-401, 'expired')
        with patch.object(q, 'client_from_config', return_value=c):
            with self.assertRaises(SeatError):
                q.validated_client({})
        c.session.close.assert_called_once()

    def test_capture_validates_latest_then_saves_only_success(self):
        process = Mock(returncode=0)
        process.communicate.return_value = ('old=value\nnew=value\n', '')
        client = Mock()
        with patch.object(q, 'tshark_path', return_value='tshark'), \
             patch.object(q.subprocess, 'run', return_value=Mock(stdout='1. loopback\n')), \
             patch.object(q.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(q, 'validated_client', side_effect=[SeatError(-401, 'expired'), (client, [])]) as verify, \
             patch.object(q, 'save_cookie') as save:
            self.assertEqual(q.capture_login({'base_url': 'http://example.test', 'proxy': 'http://127.0.0.1:7897'}, Mock()), (client, []))
            self.assertEqual([x.args[1] for x in verify.call_args_list], ['new=value', 'old=value'])
            self.assertIn('http.host == "example.test" && http.cookie', spawn.call_args.args[0])
            self.assertIn('occurrence=f', spawn.call_args.args[0])
            save.assert_called_once_with(client)

    def test_capture_without_cookie_never_saves(self):
        process = Mock(returncode=0)
        process.communicate.return_value = ('', '')
        with patch.object(q, 'tshark_path', return_value='tshark'), \
             patch.object(q.subprocess, 'run', return_value=Mock(stdout='1. loopback\n')), \
             patch.object(q.subprocess, 'Popen', return_value=process), \
             patch.object(q, 'save_cookie') as save:
            with self.assertRaises(RuntimeError):
                q.capture_login({'base_url': 'http://example.test', 'proxy': 'proxy'}, Mock())
            save.assert_not_called()

    def test_duplicate_booking_requires_exact_date_time_and_seat(self):
        row = {'SeatName': '1-8F1-144', 'BookDate': '2026-10-16T00:00:00',
               'StartTime': '2026-10-16T10:30:00', 'EndTime': '2026-10-16T22:30:00'}
        self.assertTrue(q.same_booking(row, ['1-8F1-144'], '2026-10-16', 630, 1350))
        self.assertFalse(q.same_booking(row, ['1-8F1-144'], '2026-10-17', 630, 1350))

    def test_rejected_first_seat_immediately_tries_second_and_stops(self):
        import queue
        app = q.App.__new__(q.App)
        app.events = queue.Queue()
        client = Mock()
        client.seat_config.return_value = {'BookAheadDays': 2}
        client.current.return_value = []
        cfg = {'candidates': [], 'datetime': ['10:30', '22:30']}
        with patch.object(q, 'load_config', return_value=cfg), \
             patch.object(q, 'validated_client', return_value=(client, [])), \
             patch.object(q, 'save_cookie'), \
             patch.object(q, 'submit_with_recovery', side_effect=[SeatError(1, '座位已被预约'), client]) as submit:
            app.work('book', ('1-8F1-102,1-8F1-103,1-8F1-104', 'tomorrow', '10:30', '22:30', False))
        self.assertEqual([call.args[2].seat_name for call in submit.call_args_list], ['1-8F1-102', '1-8F1-103'])


if __name__ == '__main__':
    unittest.main()
