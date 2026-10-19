import datetime as dt
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import daily_schedule as d
import scheduled_book as s


class ScheduleTests(unittest.TestCase):
    def test_next_trigger_rolls_to_next_day(self):
        zone = dt.timezone(dt.timedelta(hours=8))
        now = dt.datetime(2026, 10, 14, 22, 0, tzinfo=zone)
        self.assertEqual(d.next_fire('22:00', now).isoformat(), '2026-10-15T22:00:00+08:00')
        self.assertEqual(d.next_fire('23:00', now).day, 14)

    def test_invalid_trigger_rejected(self):
        for value in ['24:00', '22:60', 'abc', '22:00:00']:
            with self.assertRaises(ValueError):
                d.parse_clock(value)

    def test_disabled_schedule_does_not_submit(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(s, 'ROOT', Path(directory)), patch.object(s.os, 'chdir'), \
             patch.object(s, 'load_config', return_value={'daily_schedule': {'enabled': False}}), \
             patch.object(s.sys, 'argv', ['scheduled_book.py', '--scheduled']), \
             patch.object(s.App, 'work') as work:
            self.assertEqual(s.main(), 0)
            work.assert_not_called()

    def test_schedule_uses_saved_target_and_relative_tomorrow(self):
        config = {'candidates': [{'seat_name': 'old'}], 'datetime': ['08:00', '10:00'],
                  'daily_schedule': {'enabled': True, 'candidates': [{'seat_name': '1-8F1-102', 'seatno': 'CUSTOM'}],
                                     'datetime': ['16:30', '22:30'], 'auto_navigation': True}}
        def fake_work(app, action, values):
            self.assertEqual(action, 'book')
            self.assertEqual(values[:5], ('1-8F1-102', '2099-10-16', '16:30', '22:30', True))
            self.assertEqual(values[5][0]['seatno'], 'CUSTOM')
            app.events.put('服务器已返回预约成功。')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(s, 'ROOT', Path(directory)), patch.object(s.os, 'chdir'), \
             patch.object(s, 'load_config', return_value=config), \
             patch.object(s.sys, 'argv', ['scheduled_book.py', '--scheduled']), \
             patch.object(s.App, 'work', fake_work), \
             patch.object(s, 'due_booking', return_value=('2099-10-15T22:00:00', '2099-10-16')):
            self.assertEqual(s.main(), 0)
            self.assertTrue((Path(directory) / 'last_scheduled_result.json').exists())

    def test_morning_catchup_keeps_original_target_date(self):
        self.assertEqual(s.due_booking({'clock': '22:00'}, dt.datetime(2026, 10, 15, 8)),
                         ('2026-10-14T22:00:00', '2026-10-15'))

    def test_on_time_booking_targets_tomorrow(self):
        self.assertEqual(s.due_booking({'clock': '22:00'}, dt.datetime(2026, 10, 14, 22)),
                         ('2026-10-14T22:00:00', '2026-10-15'))

    def test_completed_slot_not_repeated_on_login(self):
        self.assertIsNone(s.due_booking({'clock': '22:00'}, dt.datetime(2026, 10, 15, 8),
                                       {'slot': '2026-10-14T22:00:00'}))

    def test_login_failure_can_be_caught_up(self):
        self.assertIsNotNone(s.due_booking({'clock': '22:00'}, dt.datetime(2026, 10, 15, 8),
                             {'slot': '2026-10-14T22:00:00', 'retryable': True}))

    def test_uncertain_submission_is_not_repeated(self):
        self.assertIsNone(s.due_booking({'clock': '22:00'}, dt.datetime(2026, 10, 15, 8),
                         {'slot': '2026-10-14T22:00:00', 'status': 'submitting', 'retryable': False}))

    def test_failed_login_is_retryable_but_submission_is_not(self):
        config = {'daily_schedule': {'enabled': True, 'clock': '22:00',
                  'candidates': [{'seat_name': '1-8F1-102'}], 'datetime': ['10:30', '22:30']}}
        import json
        for submitted in (False, True):
            def work(app, action, values):
                app.submission_started = submitted
                if submitted:
                    app.before_submit()
                    marker = json.loads((s.ROOT / 'last_scheduled_result.json').read_text(encoding='utf-8'))
                    self.assertFalse(marker['retryable'])
                app.events.put('测试失败。')
            with tempfile.TemporaryDirectory() as directory, patch.object(s, 'ROOT', Path(directory)), \
                 patch.object(s.os, 'chdir'), patch.object(s, 'load_config', return_value=config), \
                 patch.object(s.sys, 'argv', ['scheduled_book.py', '--scheduled']), \
                 patch.object(s, 'due_booking', return_value=('2099-10-15T22:00:00', '2099-10-16')), \
                 patch.object(s.App, 'work', work):
                self.assertEqual(s.main(), 1)
                report = json.loads((Path(directory) / 'last_scheduled_result.json').read_text(encoding='utf-8'))
                self.assertEqual(report['retryable'], not submitted)

    def test_no_catchup_before_plan_was_enabled(self):
        self.assertIsNone(s.due_booking({'clock': '22:00', 'enabled_since': '2026-10-14T14:00:00'},
                                       dt.datetime(2026, 10, 14, 15)))

    def test_expired_start_time_does_not_submit(self):
        config = {'daily_schedule': {'enabled': True, 'clock': '22:00',
                  'candidates': [{'seat_name': '1-8F1-102'}], 'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as directory, patch.object(s, 'ROOT', Path(directory)), \
             patch.object(s.os, 'chdir'), patch.object(s, 'load_config', return_value=config), \
             patch.object(s.sys, 'argv', ['scheduled_book.py', '--scheduled']), \
             patch.object(s, 'due_booking', return_value=('2000-01-01T22:00:00', '2000-01-02')), \
             patch.object(s.App, 'work') as work:
            self.assertEqual(s.main(), 1)
            work.assert_not_called()

    def test_gui_seats_persist_and_restore_with_schedule(self):
        import tkinter as tk
        import quick_book as q
        import json
        config = {'candidates': [{'seat_name': '1-8F1-144'}], 'datetime': ['10:30', '22:30'],
                  'daily_schedule': {'enabled': True, 'clock': '22:00', 'candidates': [{'seat_name': '1-8F1-144'}],
                                     'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            with patch.object(q, 'CONFIG', path), patch.object(q.daily_schedule, 'status', return_value={'enabled': False}):
                root = tk.Tk(); root.withdraw()
                app = q.App(root)
                app.seat_rows[0].set('1-8F1-102')
                app.seat_rows[1].set('1-8F1-103')
                app.seat_rows[2].set('1-8F1-104')
                app.persist_seats()
                app.close()
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['candidates'], saved['daily_schedule']['candidates'])
                root = tk.Tk(); root.withdraw()
                app = q.App(root)
                self.assertEqual([v.get() for v in app.seat_rows], ['1-8F1-102', '1-8F1-103', '1-8F1-104'])
                app.close()

    def test_schedule_time_changes_persist_and_update_windows(self):
        import tkinter as tk
        import quick_book as q
        import json
        config = {'candidates': [{'seat_name': '1-8F1-101'}], 'datetime': ['08:00', '09:00'],
                  'daily_schedule': {'enabled': True, 'clock': '22:00', 'datetime': ['10:30', '22:30']}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            with patch.object(q, 'CONFIG', path), patch.object(q, 'ROOT', Path(directory)), \
                 patch.object(q.daily_schedule, 'status', return_value={'enabled': False}), \
                 patch.object(q.daily_schedule, 'configure') as configure:
                root = tk.Tk(); root.withdraw()
                app = q.App(root)
                self.assertEqual(app.schedule_start.get(), '10:30')
                app.schedule_clock.set('22:15')
                app.schedule_start.set('16:30')
                app.schedule_end.set('21:30')
                app.save_schedule()
                configure.assert_called_once_with(True, '22:15')
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['daily_schedule']['datetime'], ['16:30', '21:30'])
                self.assertEqual(saved['datetime'], ['08:00', '09:00'])
                app.close()
                root = tk.Tk(); root.withdraw()
                app = q.App(root)
                self.assertEqual(app.schedule_clock.get(), '22:15')
                self.assertEqual(app.schedule_start.get(), '16:30')
                self.assertEqual(app.schedule_end.get(), '21:30')
                app.close()


if __name__ == '__main__':
    unittest.main()
