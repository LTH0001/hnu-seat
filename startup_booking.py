"""打开界面时检查固定预约，明确拒绝后按最近的半点时段补约。"""
import datetime as dt
import json
import re
import time
from pathlib import Path
from book import parse_time
from daily_schedule import parse_clock
from libseat import Seat, SeatError
from booking_recovery import auth_error, signature_error


def target_day(schedule, now):
    hour, minute = map(int, parse_clock(schedule.get('clock', '22:00')).split(':'))
    return (now.date() + dt.timedelta(days=int((now.hour, now.minute) >= (hour, minute)))).isoformat()


def covered(rows, names, day, start, end):
    return any(row.get('SeatName') in names and str(row.get('BookDate', ''))[:10] == day
               and str(row.get('StartTime', ''))[11:16] <= f'{start//60:02d}:{start%60:02d}'
               and str(row.get('EndTime', ''))[11:16] >= f'{end//60:02d}:{end%60:02d}'
               and len(str(row.get('StartTime', ''))) >= 16 and len(str(row.get('EndTime', ''))) >= 16
               for row in rows)


def limits(data):
    def minutes(value):
        if isinstance(value, int) and 0 <= value <= 1440:
            return value
        match = re.search(r'(\d{1,2}):(\d{2})(?::\d{2})?$', str(value))
        if match and 0 <= int(match[1]) <= 24 and 0 <= int(match[2]) < 60:
            result = int(match[1]) * 60 + int(match[2])
            if result <= 1440:
                return result
        raise RuntimeError('未能识别服务器的可约时间，停止自动补约。')
    return minutes(data.get('StartTime', data.get('sTime'))), minutes(data.get('EndTime', data.get('eTime')))


def fallback_starts(day, now):
    current = now.hour * 60 + now.minute if day == now.date().isoformat() else -1
    return [start for start in range(510, 1440, 120) if start > current]


def recover(cfg, client, rows, emit, submit, state_path, now=None):
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    schedule = cfg.get('daily_schedule', {})
    candidates = schedule.get('candidates', cfg.get('candidates', []))
    names = [seat['seat_name'] for seat in candidates]
    if not names:
        emit('未设置候选座位，跳过启动补约。')
        return
    day = target_day(schedule, now)
    start, end = parse_time({'datetime': schedule.get('datetime', cfg.get('datetime', ['10:30', '22:30']))}, None)
    path = Path(state_path)
    key = {'day': day, 'names': names, 'fixed': [start, end]}
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if covered(rows, names, day, start, end):
        emit(f'{day} 已有预约覆盖固定时段，无需补约。')
        return
    if previous.get('key') == key:
        if previous.get('status') == 'success' and covered(rows, names, day, previous['start'], previous['end']):
            emit(f'{day} 已完成最近时段补约，无需重复提交。')
            return
        if previous.get('status') == 'submitting':
            if covered(rows, names, day, previous['start'], previous['end']):
                previous['status'] = 'success'
                path.write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8')
                emit('已查询确认上次补约成功。')
            else:
                emit('上次补约提交结果尚未确认，请在微信核实；不会自动重发。')
            return
    # 同日已有其他预约时，不擅自变更或另约一个重叠时段。
    if any(str(row.get('BookDate', ''))[:10] == day for row in rows):
        emit(f'{day} 已有其他时段或座位的预约，请先在微信确认；本次不重复预约。')
        return
    scheduled_path = path.parent / 'last_scheduled_result.json'
    if scheduled_path.exists():
        scheduled = json.loads(scheduled_path.read_text(encoding='utf-8'))
        slot = scheduled.get('slot')
        if slot and (dt.datetime.fromisoformat(slot).date() + dt.timedelta(days=1)).isoformat() == day:
            if scheduled.get('status') == 'submitting' or any('提交结果尚未确认' in message for message in scheduled.get('messages', [])):
                emit('定时任务的提交结果尚未确认，请先在微信核实；不会通过启动补约重复发送。')
                return
    seats = [(Seat(item['seat_name'], item.get('area_code', ''), item.get('address_code', ''), item.get('seatno', '')).resolve()) for item in candidates]
    bounds = {}
    def attempt(seat, begin, finish):
        state = {'key': key, 'status': 'submitting', 'start': begin, 'end': finish}
        path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        emit(f'启动补约：{seat.seat_name}，{day}，{begin//60:02d}:{begin%60:02d}–{finish//60:02d}:{finish%60:02d}…')
        try:
            submit(seat, day, begin, finish)
        except SeatError as error:
            state['status'] = 'rejected'
            path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            emit(f'{seat.seat_name}：{error.msg}')
            if auth_error(error) or signature_error(error) or '开放时间' in error.msg or '已预约' in error.msg:
                raise
            time.sleep(1)
            return False
        state['status'] = 'success'
        path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        emit('服务器已返回预约成功。')
        return True
    if day > now.date().isoformat() or start > now.hour * 60 + now.minute:
        for seat in seats:
            if attempt(seat, start, end):
                return
    else:
        emit('固定开始时间已过，改查最近的半点时段。')
    emit('固定时段未完成，按 08:30、10:30、12:30…尝试至服务器允许的最晚结束时间。')
    for begin in fallback_starts(day, now):
        for seat in seats:
            if seat.seat_name not in bounds:
                bounds[seat.seat_name] = limits(client.seat_init(seat.address_code, seat.seatno))
            earliest, latest = bounds[seat.seat_name]
            if earliest <= begin < latest and (begin, latest) != (start, end):
                if attempt(seat, begin, latest):
                    return
    emit('最近时段和候选座位均未预约成功，请查看服务器提示。')
