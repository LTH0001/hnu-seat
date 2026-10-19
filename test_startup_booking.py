import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from libseat import SeatError
import startup_booking as s


class StartupTests(unittest.TestCase):
    def test_day_switches_at_configured_clock(self):
        self.assertEqual(s.target_day({'clock': '22:00'}, dt.datetime(2026, 10, 15, 21)), '2026-10-15')
        self.assertEqual(s.target_day({'clock': '22:00'}, dt.datetime(2026, 10, 15, 22)), '2026-10-16')

    def test_past_start_times_are_skipped(self):
        self.assertEqual(s.fallback_starts('2026-10-15', dt.datetime(2026, 10, 15, 10, 30))[0], 750)
        self.assertEqual(s.fallback_starts('2026-10-16', dt.datetime(2026, 10, 15, 22))[0], 510)

    def test_bounds_use_server_end_and_reject_unknown(self):
        self.assertEqual(s.limits({'StartTime': '08:00:00', 'EndTime': '22:30:00'}), (480, 1350))
        self.assertEqual(s.limits({'sTime': 480, 'eTime': 1320}), (480, 1320))
        with self.assertRaises(RuntimeError):
            s.limits({})

    def test_fixed_failure_then_nearest_start_before_next_seat(self):
        cfg = {'daily_schedule': {'clock': '22:00', 'datetime': ['16:30', '22:30'],
               'candidates': [{'seat_name': '1-8F1-101'}, {'seat_name': '1-8F1-102'}]}}
        client = Mock()
        client.seat_init.return_value = {'StartTime': '08:00', 'EndTime': '23:00'}
        submit = Mock(side_effect=[SeatError(-1, '座位已占用'), SeatError(-1, '座位已占用'), None])
        with tempfile.TemporaryDirectory() as folder, patch.object(s.time, 'sleep'):
            path = Path(folder) / 'state.json'
            s.recover(cfg, client, [], Mock(), submit, path, dt.datetime(2026, 10, 15, 9))
            calls = submit.call_args_list
            self.assertEqual([(c.args[0].seat_name, c.args[2:]) for c in calls],
                             [('1-8F1-101', (990, 1350)), ('1-8F1-102', (990, 1350)), ('1-8F1-101', (630, 1380))])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'], 'success')

    def test_existing_covering_booking_never_submits(self):
        row = {'SeatName': '1-8F1-101', 'BookDate': '2026-10-15',
               'StartTime': '2026-10-15T08:30:00', 'EndTime': '2026-10-15T22:30:00'}
        cfg = {'daily_schedule': {'candidates': [{'seat_name': '1-8F1-101'}], 'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as folder:
            submit = Mock()
            s.recover(cfg, Mock(), [row], Mock(), submit, Path(folder)/'state.json', dt.datetime(2026, 10, 15, 9))
            submit.assert_not_called()

    def test_unknown_submit_is_not_retried_on_next_open(self):
        cfg = {'daily_schedule': {'candidates': [{'seat_name': '1-8F1-101'}], 'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'state.json'
            submit = Mock(side_effect=RuntimeError('提交结果尚未确认'))
            with self.assertRaises(RuntimeError):
                s.recover(cfg, Mock(), [], Mock(), submit, path, dt.datetime(2026, 10, 15, 9))
            s.recover(cfg, Mock(), [], Mock(), submit, path, dt.datetime(2026, 10, 15, 9))
            self.assertEqual(submit.call_count, 1)

    def test_closed_booking_window_stops_fallback(self):
        cfg = {'daily_schedule': {'candidates': [{'seat_name': '1-8F1-101'}], 'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as folder:
            submit = Mock(side_effect=SeatError(-1, '预约明天开放时间：22:00 - 23:45'))
            with self.assertRaises(SeatError):
                s.recover(cfg, Mock(), [], Mock(), submit, Path(folder)/'state.json', dt.datetime(2026, 10, 15, 9))
            self.assertEqual(submit.call_count, 1)
