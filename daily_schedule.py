"""Windows 每日预约计划，使用当前用户的交互式登录会话。"""
import datetime as dt
from pathlib import Path
import sys
from app_runtime import DATA_ROOT, scheduled_command

TASK_NAME = 'ZZLibrary-DailyBooking'
ROOT = DATA_ROOT


def parse_clock(value):
    try:
        parts = value.strip().split(':')
        if len(parts) != 2:
            raise ValueError
        hour, minute = map(int, parts)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return f'{hour:02d}:{minute:02d}'
    except (ValueError, AttributeError):
        raise ValueError('触发时间请填写 HH:MM，例如 22:00。') from None


def next_fire(clock, now=None):
    hour, minute = map(int, parse_clock(clock).split(':'))
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + dt.timedelta(days=1)


def configure(enabled, clock='22:00'):
    import pythoncom
    import win32com.client
    import win32api
    import win32security
    pythoncom.CoInitialize()
    service = folder = definition = trigger = login = action = task = None
    try:
        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        folder = service.GetFolder('\\')
        if not enabled:
            try:
                task = folder.GetTask(TASK_NAME)
            except pythoncom.com_error as error:
                if error.hresult == -2147024894:  # 文件不存在
                    return
                raise
            task.Enabled = False
            return
        clock = parse_clock(clock)
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        definition = service.NewTask(0)
        definition.RegistrationInfo.Description = '每天按配置预约明天的图书馆座位；需要用户已登录 Windows。'
        definition.Principal.UserId = user
        definition.Principal.LogonType = 3  # InteractiveToken，无需保存密码。
        definition.Principal.RunLevel = 0
        definition.Settings.Enabled = True
        definition.Settings.StartWhenAvailable = True
        definition.Settings.DisallowStartIfOnBatteries = False
        definition.Settings.StopIfGoingOnBatteries = False
        definition.Settings.MultipleInstances = 2  # IgnoreNew
        definition.Settings.ExecutionTimeLimit = 'PT10M'
        definition.Settings.RestartCount = 3
        definition.Settings.RestartInterval = 'PT5M'
        trigger = definition.Triggers.Create(2)  # Daily
        trigger.StartBoundary = next_fire(clock).isoformat()
        trigger.DaysInterval = 1
        trigger.Enabled = True
        trigger.Repetition.Interval = 'PT5M'
        trigger.Repetition.Duration = 'PT1H45M'
        login = definition.Triggers.Create(9)  # 用户登录后检查是否有漏跑。
        login.UserId = user
        login.Delay = 'PT1M'  # 等待网络和代理启动。
        login.Enabled = True
        login.Repetition.Interval = 'PT5M'
        login.Repetition.Duration = 'PT30M'
        unlock = definition.Triggers.Create(11)  # 解锁桌面后也检查补跑。
        unlock.StateChange = 8
        unlock.UserId = user
        unlock.Delay = 'PT30S'
        unlock.Enabled = True
        prepare = definition.Triggers.Create(2)
        prepare.StartBoundary = (next_fire(clock) - dt.timedelta(minutes=5)).isoformat()
        prepare.DaysInterval = 1
        prepare.Enabled = True
        recovery = definition.Triggers.Create(1)
        recovery.StartBoundary = (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))) + dt.timedelta(minutes=5)).isoformat()
        recovery.Repetition.Interval = 'PT5M'
        recovery.Enabled = True
        action = definition.Actions.Create(0)
        action.Path, action.Arguments = scheduled_command()
        # Windows 计划服务启动时使用稳定目录，程序自行切换到配置目录。
        import os
        action.WorkingDirectory = os.environ.get('SystemRoot', 'C:\\Windows')
        folder.RegisterTaskDefinition(TASK_NAME, definition, 6, user, None, 3)
    finally:
        service = folder = definition = trigger = login = action = task = None
        import gc
        gc.collect()
        pythoncom.CoUninitialize()


def status():
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    service = task = None
    try:
        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        try:
            task = service.GetFolder('\\').GetTask(TASK_NAME)
        except pythoncom.com_error as error:
            if error.hresult == -2147024894:
                return {'enabled': False}
            raise
        return {'enabled': bool(task.Enabled), 'next_run': task.NextRunTime.strftime('%Y-%m-%d %H:%M:%S'),
                'last_result': task.LastTaskResult}
    finally:
        service = task = None
        import gc
        gc.collect()
        pythoncom.CoUninitialize()
