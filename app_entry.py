"""软件入口，支持窗口、定时任务、微信导航和离线自检。"""
import contextlib
import json
import os
from pathlib import Path
import sys
import traceback
from app_runtime import initialize, DATA_ROOT, RESOURCE_ROOT


def main():
    initialize()
    if '--scheduled' in sys.argv:
        import datetime
        with (DATA_ROOT / 'scheduler_start.log').open('a', encoding='utf-8') as stream:
            stream.write(datetime.datetime.now().isoformat() + ' started ' + str(DATA_ROOT) + '\n')
    if '--navigate' in sys.argv:
        import wechat_navigation
        sys.argv.remove('--navigate')
        output = None
        if '--nav-output' in sys.argv:
            index = sys.argv.index('--nav-output')
            output = sys.argv[index + 1]
            del sys.argv[index:index + 2]
        if output:
            with open(output, 'w', encoding='utf-8') as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                return wechat_navigation.main()
        return wechat_navigation.main()
    if '--scheduled' in sys.argv:
        import scheduled_book
        return scheduled_book.main()
    if '--query-current' in sys.argv:
        from libseat import load_config
        from quick_book import validated_client
        client, rows = validated_client(load_config(str(DATA_ROOT / 'config.json')))
        try:
            with (DATA_ROOT / 'booking_query.log').open('a', encoding='utf-8') as stream:
                import datetime
                stream.write(datetime.datetime.now().isoformat() + ' ' + json.dumps([
                    {key: row.get(key) for key in ('SeatName', 'BookDate', 'StartTime', 'EndTime')}
                    for row in rows], ensure_ascii=False) + '\n')
        finally:
            client.session.close()
        return 0
    if '--install-schedule' in sys.argv:
        import daily_schedule
        from libseat import load_config
        schedule = load_config(str(DATA_ROOT / 'config.json')).get('daily_schedule', {})
        daily_schedule.configure(schedule.get('enabled', False), schedule.get('clock', '22:00'))
        (DATA_ROOT / 'software_schedule_check.json').write_text(json.dumps(daily_schedule.status(), ensure_ascii=False), encoding='utf-8')
        return 0
    if '--self-check' in sys.argv:
        import tkinter as tk
        import cv2
        import psutil
        import pywinauto
        from libseat import jsencrypt
        from quick_book import App, tshark_path
        root = tk.Tk(); root.withdraw()
        app = App(root)
        root.update()
        report = {'gui': True, 'crypto': len(jsencrypt('0123456789ABCDEF')) == 64,
                  'opencv': cv2.__version__, 'pywinauto': pywinauto.__version__,
                  'data_directory': str(DATA_ROOT), 'tshark': tshark_path(),
                  'assets': (RESOURCE_ROOT / 'wechat_assets/search_icon.png').exists(),
                  'date_options': list(app.date_options)}
        root.destroy()
        (DATA_ROOT / 'software_self_check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    import tkinter as tk
    from quick_book import App
    root = tk.Tk()
    app = App(root)
    if '--login' in sys.argv:
        root.after(500, lambda: app.run('login'))
    root.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / 'software_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if not any(mode in sys.argv for mode in ('--navigate', '--scheduled', '--self-check', '--install-schedule')):
            import tkinter.messagebox
            tkinter.messagebox.showerror('图书馆预约', '启动失败，诊断日志保存在：' + str(DATA_ROOT / 'software_error.log'))
        raise SystemExit(1)
