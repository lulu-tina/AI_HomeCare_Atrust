"""簡化版打卡：一鍵 PIN、打卡網址與 QR Code、今日打卡看板、手機畫面用語。"""
import re
from datetime import date, datetime

import pandas as pd
import pytest

from checkin import runner, store
from checkin.server import create_app


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / 'checkin.sqlite3')
    monkeypatch.setenv('CAREFLOW_CHECKIN_DB', path)
    monkeypatch.delenv('CAREFLOW_CHECKIN_PUBLIC_URL', raising=False)
    return path


def _publish(db, day=None, start='09:00', end='10:00'):
    day = (day or date.today()).isoformat()
    schedule = pd.DataFrame({'任務ID': ['T1', 'T2', 'T3'], '日期': [day] * 3, '開始時間': [start, '13:00', '15:00'],
                             '結束時間': [end, '14:00', '16:00'], '案號': ['C1', 'C2', 'C3'],
                             '派單居服員': ['E001', 'E001', 'E002'], '服務地點_緯度': [25.0] * 3, '服務地點_經度': [121.5] * 3})
    return store.publish_shifts(schedule, {'C1': '王小明'}, path=db)


def test_generate_pins_only_for_people_without_one(db):
    _publish(db)
    first = store.generate_pins(path=db)
    assert [cg for cg, _ in first] == ['E001', 'E002'] and all(re.fullmatch(r'\d{6}', p) for _, p in first)
    assert store.verify_pin('E001', first[0][1], db)[0]
    assert store.generate_pins(path=db) == []                    # 都有了就不重發
    again = store.generate_pins(['e001'], reset=True, path=db)   # 重設（員工編號不分大小寫）
    assert again[0][0] == 'E001' and store.verify_pin('E001', again[0][1], db)[0]
    assert not store.verify_pin('E001', first[0][1], db)[0] or first[0][1] == again[0][1]


def test_checkin_url_prefers_saved_fixed_url(db, monkeypatch):
    url, kind = runner.checkin_url(db)
    assert kind == '辦公室網路' and url.startswith('http://') and url.endswith(':8600')
    monkeypatch.setenv('CAREFLOW_CHECKIN_PUBLIC_URL', 'https://env.example.org/')
    assert runner.checkin_url(db) == ('https://env.example.org', '固定網址')
    runner.save_public_url('https://checkin.example.org/', path=db)
    assert runner.checkin_url(db) == ('https://checkin.example.org', '固定網址')
    with pytest.raises(ValueError, match='https'):
        runner.save_public_url('http://not-secure.example.org', path=db)
    runner.save_public_url('', path=db)                          # 清空＝回到環境變數或辦公室網址
    assert runner.checkin_url(db)[0] == 'https://env.example.org'
    assert runner.qr_png('https://checkin.example.org')[:8] == b'\x89PNG\r\n\x1a\n'


def test_day_board_states_distance_and_overdue(db):
    today = date.today()
    _publish(db, today, start='00:00', end='23:59')
    assert store.add_punch('E001', 'T1', '簽到', lat=25.001, lon=121.5, path=db)[0]
    assert store.add_punch('E001', 'T2', '未遇', path=db)[0]
    moment = datetime.combine(today, datetime.min.time()).replace(hour=15, minute=20)
    board = store.day_board(today, path=db, moment=moment).set_index('任務ID')
    assert board.loc['T1', '狀態'] == '服務中' and board.loc['T1', '到勤'] != '' and 100 <= board.loc['T1', '距案家(公尺)'] <= 125
    assert board.loc['T2', '狀態'] == '未遇'
    assert board.loc['T3', '狀態'] == '逾時未到勤'            # 15:00 開始，15:20 還沒到勤
    early = store.day_board(today, path=db, moment=moment.replace(hour=15, minute=5)).set_index('任務ID')
    assert early.loc['T3', '狀態'] == '未開始'
    assert store.day_board(date(2000, 1, 1), path=db).empty
    # 離開打卡另外記位置與距離，和到勤分開
    assert store.add_punch('E001', 'T1', '簽退', lat=25.02, lon=121.5, path=db)[0]
    done = store.day_board(today, path=db, moment=moment).set_index('任務ID')
    assert done.loc['T1', '打卡位置'].endswith('25.001000,121.500000')
    assert done.loc['T1', '離開位置'].endswith('25.020000,121.500000')
    assert done.loc['T1', '離開距案家(公尺)'] > done.loc['T1', '距案家(公尺)']
    assert store.add_punch('E002', 'T3', '簽到', path=db)[0]
    assert '到勤無定位' in store.day_board(today, path=db, moment=moment).set_index('任務ID').loc['T3', '提醒']


def test_phone_page_uses_plain_words_and_proxy_prefix(db):
    _publish(db, start='00:00', end='23:59')
    store.set_pin('E001', '123456', db)
    app = create_app(db_path=db)
    assert app.permanent_session_lifetime.days == 365
    client = app.test_client()
    prefix = {'X-Forwarded-Prefix': '/checkin', 'X-Forwarded-Proto': 'https'}
    page = client.get('/login', headers=prefix).get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
    client.post('/login', data={'csrf': token, 'cg': 'E001', 'pin': '123456'}, headers=prefix)
    page = client.get('/', headers=prefix).get_data(as_text=True)
    assert '到勤打卡' in page and '>簽到<' not in page
    assert 'var PUNCH_URL = "/checkin/punch"' in page           # 架在子路徑後面也打得到
    token = re.search(r'var CSRF = "([^"]+)"', page)[1]
    reply = client.post('/punch', json={'task_id': 'T1', 'kind': '簽到'}, headers={'X-CSRF-Token': token}).json
    assert reply['ok'] and reply['message'].startswith('到勤打卡完成')


def test_ensure_running_reports_when_disabled(monkeypatch):
    monkeypatch.setenv('CAREFLOW_CHECKIN_AUTOSTART', '0')
    monkeypatch.setattr(runner, 'is_up', lambda *a, **k: False)
    assert runner.ensure_running(port=65530) == (False, '自動啟動已關閉（CAREFLOW_CHECKIN_AUTOSTART=0）')


IPCONFIG_ZH = '''Windows IP 設定

乙太網路卡 vEthernet (WSL (Hyper-V firewall)):

   IPv4 位址 . . . . . . . . . . . . : 192.168.128.2
   子網路遮罩 . . . . . . . . . . . . : 255.255.240.0
   預設閘道 . . . . . . . . . . . . . :

無線區域網路介面卡 Wi-Fi:

   IPv4 位址 . . . . . . . . . . . . : 192.168.1.105(偏好選項)
   預設閘道 . . . . . . . . . . . . . : 192.168.1.1

無線區域網路介面卡 區域連線* 1:

   媒體狀態 . . . . . . . . . . . . . : 媒體已中斷連線
'''
IPCONFIG_EN = '''Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 10.0.5.23(Preferred)
   Default Gateway . . . . . . . . . : 10.0.5.1

Ethernet adapter vEthernet (Default Switch):

   IPv4 Address. . . . . . . . . . . : 172.29.80.1
   Default Gateway . . . . . . . . . :
'''


def test_parse_ipconfig_puts_real_wifi_before_virtual_adapters():
    zh = runner.parse_ipconfig(IPCONFIG_ZH)
    assert [(a['名稱'], a['IP'], a['虛擬網卡']) for a in zh] == [
        ('Wi-Fi', '192.168.1.105', False), ('vEthernet (WSL (Hyper-V firewall))', '192.168.128.2', True)]
    en = runner.parse_ipconfig(IPCONFIG_EN)
    assert [a['IP'] for a in en] == ['10.0.5.23', '172.29.80.1'] and en[1]['虛擬網卡']


def test_office_url_uses_chosen_adapter(db):
    nics = runner.parse_ipconfig(IPCONFIG_ZH)
    assert runner.office_url(db, nics) == 'http://192.168.1.105:8600'
    store.set_setting(runner.CHOSEN_IP_KEY, '192.168.128.2', db)
    assert runner.office_url(db, nics) == 'http://192.168.128.2:8600'
    store.set_setting(runner.CHOSEN_IP_KEY, '10.9.9.9', db)              # 選過的網卡已不存在 → 用建議的
    assert runner.office_url(db, nics) == 'http://192.168.1.105:8600'


class _FakeProc:
    """假的 cloudflared：照實際輸出格式印出 trycloudflare 網址。"""
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


def test_start_tunnel_reads_url_and_becomes_checkin_url(db, monkeypatch, tmp_path):
    lines = ['2026-11-07T14:20:01Z INF Requesting new quick Tunnel on trycloudflare.com...\n',
             '2026-11-07T14:20:03Z INF |  https://fancy-river-1234.trycloudflare.com                    |\n']
    monkeypatch.setattr(runner, 'cloudflared_path', lambda: tmp_path / 'cloudflared.exe')
    monkeypatch.setattr(runner._subprocess, 'Popen', lambda *a, **k: _FakeProc(lines))
    runner.stop_tunnel()
    ok, url = runner.start_tunnel(wait=5)
    assert ok and url == 'https://fancy-river-1234.trycloudflare.com'
    assert runner.checkin_url(db) == (url, '外部網址')
    runner.save_public_url('https://checkin.example.org', path=db)      # 固定網址優先
    assert runner.checkin_url(db)[1] == '固定網址'
    runner.save_public_url('', path=db)
    runner.stop_tunnel()
    assert runner.tunnel_url() == '' and runner.checkin_url(db)[1] == '辦公室網路'


def test_start_tunnel_reports_when_no_url(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, 'cloudflared_path', lambda: tmp_path / 'cloudflared.exe')
    monkeypatch.setattr(runner._subprocess, 'Popen', lambda *a, **k: _FakeProc([
        'INF Requesting new quick Tunnel on trycloudflare.com...\n',
        'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": dial tcp: no such host\n']))
    runner.stop_tunnel()
    ok, msg = runner.start_tunnel(wait=0.5)
    assert not ok and '沒有取得外部網址' in msg and 'failed to request' in msg and runner.tunnel_url() == ''   # 錯誤訊息裡的 api 網址不能當成功


def test_download_refuses_unknown_platform(monkeypatch):
    monkeypatch.setattr(runner._platform, 'system', lambda: 'Plan9')
    with pytest.raises(RuntimeError, match='手動安裝'):
        runner.download_cloudflared()


UA_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1'
UA_ANDROID = 'Mozilla/5.0 (Linux; Android 14; SM-S9180 Build/UP1A.231005.007) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36'


def _start(client, cg, ua):
    page = client.get('/login', headers={'User-Agent': ua}).get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
    return client.post('/login', data={'csrf': token, 'cg': cg}, headers={'User-Agent': ua})


def test_first_phone_binds_automatically_and_new_phone_needs_approval(db):
    _publish(db, start='00:00', end='23:59')
    app = create_app(db_path=db)
    phone1, phone2, stranger = app.test_client(), app.test_client(), app.test_client()

    assert '查不到' in _start(stranger, 'X999', UA_IPHONE).get_data(as_text=True)     # 亂打的員編不會建資料
    assert store.devices(path=db) == []

    r = _start(phone1, 'e001', UA_IPHONE)                       # 第一支手機：只輸入員編就直接進來
    assert r.status_code == 302 and r.headers['Location'].endswith('/')
    assert '已綁定' in phone1.get('/').get_data(as_text=True)
    phone1.post('/logout', data={'csrf': re.search(r'var CSRF = "([^"]+)"', phone1.get('/').get_data(as_text=True))[1]})
    assert phone1.get('/login').status_code == 302               # 登出後再打開，同一支手機直接進來

    r = _start(phone2, 'E001', UA_ANDROID)                       # 換手機：等督導核准
    assert r.headers['Location'].endswith('/pending')
    page = phone2.get('/pending').get_data(as_text=True)
    assert '等督導核准' in page and 'Android 14' in page
    assert phone2.get('/').status_code == 302 and phone2.get('/device-status').json['status'] == 'pending'
    pending = store.devices(store.DEVICE_PENDING, db)
    assert len(pending) == 1 and pending[0]['label'].startswith('Android 14（SM-S9180）')

    assert store.decide_device(pending[0]['id'], True, db) == 'E001'   # 督導核准 → 新手機可用、舊手機失效
    assert phone2.get('/device-status').json['status'] == 'active'
    assert phone2.get('/pending').status_code == 302 and '今天的班' in phone2.get('/').get_data(as_text=True)
    assert phone1.get('/').status_code == 302                    # 舊手機被擋回登入
    reply = phone1.post('/punch', json={'task_id': 'T1', 'kind': '簽到'}, headers={'X-CSRF-Token': 'x'})
    assert reply.status_code in (400, 401)


def test_revoked_or_rejected_phone_cannot_punch(db):
    _publish(db, start='00:00', end='23:59')
    app = create_app(db_path=db)
    phone, other = app.test_client(), app.test_client()
    _start(phone, 'E001', UA_IPHONE)
    store.revoke_device(store.devices(store.DEVICE_ACTIVE, db)[0]['id'], db)
    assert phone.get('/').status_code == 302
    r = _start(phone, 'E001', UA_IPHONE)                         # 被解除後重新登入：舊手機都已解除 → 視為第一支，自動綁定
    assert r.headers['Location'].endswith('/')
    _start(other, 'E001', UA_ANDROID)
    store.decide_device(store.devices(store.DEVICE_PENDING, db)[0]['id'], False, db)
    assert '沒有通過' in other.get('/pending').get_data(as_text=True)


def test_auto_bind_can_be_turned_off(db):
    _publish(db, start='00:00', end='23:59')
    store.set_setting(store.AUTO_BIND_KEY, '0', db)
    app = create_app(db_path=db)
    phone = app.test_client()
    assert _start(phone, 'E001', UA_IPHONE).headers['Location'].endswith('/pending')   # 關掉後第一支也要核准


def test_demo_shift_board_link_and_clear(db):
    tid = store.add_demo_shift('E001', path=db)
    assert store.add_punch('E001', tid, '簽到', lat=25.04, lon=121.53, path=db)[0]
    row = store.day_board(date.today(), path=db).set_index('任務ID').loc[tid]
    assert row['狀態'] == '服務中' and row['打卡位置'].startswith('https://www.google.com/maps?q=25.04')
    assert pd.isna(row['距案家(公尺)']) or row['距案家(公尺)'] is None
    assert store.punch_frame(path=db).empty                       # 示範打卡不進執行對照
    _publish(db)                                                  # 重新發布正式班表不會刪掉示範班
    assert tid in set(store.day_board(date.today(), path=db)['任務ID'])
    assert store.clear_demo(db) == 1 and tid not in set(store.day_board(date.today(), path=db)['任務ID'])


def test_rebuilding_demo_shift_replaces_previous_one(db, monkeypatch):
    first = store.add_demo_shift('E001', path=db)
    assert store.add_punch('E001', first, '簽到', path=db)[0]
    from datetime import timedelta
    later = store.now() + timedelta(minutes=5)
    monkeypatch.setattr(store, 'now', lambda: later)
    second = store.add_demo_shift('E001', path=db)
    ids = [s['task_id'] for s in store.shifts_for('E001', later.date(), db) if s['task_id'].startswith('DEMO-')]
    assert ids == [second] and first != second
    assert store.day_board(later.date(), path=db).set_index('任務ID').loc[second, '狀態'] == '未開始'   # 舊的示範打卡一併清掉
