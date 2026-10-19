"""有界预约恢复：只对明确拒绝重试，对不确定提交只查询。"""
from functools import partial
from pathlib import Path
import requests
from fetch_key import fetch_des_key
from libseat import SeatError, jsencrypt
from signer_node import NodeSigner
from app_runtime import DATA_ROOT


def auth_error(error):
    return error.code == -401 or any(x in error.msg for x in ('请先登录', '授权已失效', '会话已失效'))


def signature_error(error):
    # 不将模糊的“参数错误”当作签名失败。
    return any(x in error.msg.lower() for x in
               ('签名错误', '签名失败', '签名无效', '校验码错误', '校验码无效',
                '验证码错误', 'code校验失败', 'signature invalid', 'invalid signature'))


def submit_with_recovery(client, cfg, seat, day, start, end, emit,
                         refresh_login, matches, persist_signer):
    auth_used = False
    stage = 0
    changed = {}
    while True:
        try:
            client.book(seat, day, start, end)  # 每次都会重新请求 getCode。
        except requests.RequestException:
            emit('请求中断，正在查询预约结果；不会自动重复提交。')
            try:
                confirmed = any(matches(row) for row in client.current())
            except (SeatError, requests.RequestException):
                confirmed = False
            if not confirmed:
                raise RuntimeError('提交结果尚未确认，请在微信核实后再操作；本次已停止。') from None
            emit('已查询确认：目标预约存在。')
        except SeatError as error:
            if error.code == -500:
                emit('服务器响应无法解析，正在查询预约结果；不会重复提交。')
                try:
                    confirmed = any(matches(row) for row in client.current())
                except (SeatError, requests.RequestException):
                    confirmed = False
                if confirmed:
                    emit('已查询确认：目标预约存在。')
                    return client
                raise RuntimeError('提交结果尚未确认，请在微信核实后再操作；本次已停止。') from None
            if auth_error(error) and not auth_used:
                auth_used = True
                emit('预约过程中登录失效，正在重新获取登录信息。')
                fresh, rows = refresh_login(cfg, emit)
                client.session.close()
                client.session = fresh.session
                client.cookie = fresh.cookie
                cfg['cookie'] = fresh.cookie
                if any(matches(row) for row in rows):
                    emit('重新登录后查到目标预约，停止重复提交。')
                    return client
                continue
            if signature_error(error) and stage < 2:
                if stage == 0:
                    stage = 1
                    emit('服务器明确拒绝签名，正在从官网更新密钥。')
                    try:
                        key = fetch_des_key(cfg['base_url'], cfg.get('proxy', ''))
                    except (requests.RequestException, ValueError, IndexError):
                        key = None
                    if isinstance(key, str) and len(key) == 8 and key.isascii():
                        client.signer = partial(jsencrypt, key=key)
                        changed = {'des_key': key, 'signer': 'des'}
                        continue
                    emit('密钥提取未成功，尝试官网 JavaScript 签名。')
                stage = 2
                signer = NodeSigner(cfg['base_url'], cfg.get('proxy', ''),
                                    cache_dir=str(DATA_ROOT / 'vendor'))
                signer.refresh()
                client.signer = signer
                changed = {'signer': 'node'}
                emit('官网脚本已更新，重新获取随机码后再尝试一次。')
                continue
            raise
        if changed:
            try:
                persist_signer(changed)
                cfg.update(changed)
            except OSError:
                emit('预约成功，但新签名配置保存失败。')
        return client
