import re
from datetime import date, datetime, time

import pandas as pd
import pytest

from attendance_reconcile import normalize_records, reconcile
from checkin import store
from checkin.server import create_app


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / 'checkin.sqlite3')


def _schedule(day=None):
    day = (day or date.today()).isoformat()
    return pd.DataFrame({
        '任務ID': ['T1', 'T2', 'T3', 'T4'],
        '日期': [day, day, day, day],
        '開始時間': ['00:00', '00:00', '00:00', '00:00'],
        '結束時間': ['23:59', '23:59', '23:59', '23:59'],
        '案號': ['C1', 'C2', 'C3', 'C4'],
        '派單居服員': ['E001', 'E001', 'E002', '未指派'],
        '服務分鐘': [1439, 1439, 1439, 1439],
        '服務地點_緯度': [25.0, 25.0, 25.0, 25.0],
        '服務地點_經度': [121.5] * 4,
    })


def _login(client, cg='E001', pin='123456'):
    page = client.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
    return client.post('/login', data={'csrf': token, 'cg': cg, 'pin': pin})


def _csrf(client):
    page = client.get('/').get_data(as_text=True)
    return re.search(r'var CSRF = "([^"]+)"', page)[1], page


def test_publish_only_assigned_and_masks_name(db):
    n, removed, _ = store.publish_shifts(_schedule(), {'C1': '王小明', 'C2': '李美'}, path=db)
    assert n == 3 and removed == 0
    rows = store.shifts_for('E001', date.today(), db)
    assert [r['client_label'] for r in rows] == ['王○明', '李○']
    # 重新發布時，範圍內被拿掉的班會移除
    n, removed, _ = store.publish_shifts(_schedule().iloc[:1], path=db)
    assert (n, removed) == (1, 2)


def test_pin_lockout(db):
    store.set_pin('E001', '123456', db)
    for _ in range(store.MAX_FAILED - 1):
        assert not store.verify_pin('E001', '000000', db)[0]
    ok, msg = store.verify_pin('E001', '000000', db)
    assert not ok and '鎖定' in msg
    assert not store.verify_pin('E001', '123456', db)[0]      # 鎖定中，正確 PIN 也不行
    store.set_pin('E001', '123456', db)                        # 督導重設即解鎖
    assert store.verify_pin('E001', '123456', db)[0]
    with pytest.raises(ValueError):
        store.set_pin('E001', '12ab', db)


def test_full_flow_reconciles(db):
    store.publish_shifts(_schedule(), path=db)
    store.set_pin('E001', '123456', db)
    store.set_pin('E002', '654321', db)
    app = create_app(db)
    app.config['TESTING'] = True

    c1 = app.test_client()
    assert _login(c1, cg='X999').status_code == 200             # 查不到的員編停在登入頁（改用手機綁定，不再用 PIN）
    assert _login(c1).status_code == 302
    token, page = _csrf(c1)
    assert 'C1' in page and 'C2' in page and 'C3' not in page   # 只看到自己的班
    h = {'X-CSRF-Token': token}
    assert c1.post('/punch', json={'task_id': 'T1', 'kind': '簽到', 'lat': 25.0, 'lon': 121.5, 'accuracy': 12}, headers=h).json['ok']
    r = c1.post('/punch', json={'task_id': 'T1', 'kind': '簽到'}, headers=h)
    assert r.status_code == 409 and '已經簽到' in r.json['message']
    assert c1.post('/punch', json={'task_id': 'T1', 'kind': '簽退', 'lat': 25.0, 'lon': 121.5}, headers=h).json['ok']
    assert c1.post('/punch', json={'task_id': 'T2', 'kind': '未遇'}, headers=h).json['ok']
    r = c1.post('/punch', json={'task_id': 'T3', 'kind': '簽到'}, headers=h)   # 別人的班
    assert r.status_code == 409
    assert c1.post('/punch', json={'task_id': 'T1', 'kind': '簽到'}).status_code == 400   # 沒有 CSRF

    c2 = app.test_client()
    _login(c2, 'E002', '654321')
    token2, _ = _csrf(c2)
    r = c2.post('/punch', json={'task_id': 'T3', 'kind': '簽到', 'at': '00:00', 'note': ''}, headers={'X-CSRF-Token': token2})
    assert not r.json['ok'] and '原因' in r.json['message']
    r = c2.post('/punch', json={'task_id': 'T3', 'kind': '簽到', 'at': '00:00', 'note': '手機沒電', 'lat': 24.0, 'lon': 121.0},
                headers={'X-CSRF-Token': token2})
    assert r.json['ok'] and '補登' in r.json['message']

    frame = store.punch_frame(date.today(), date.today(), db)
    assert sorted(frame['打卡類型']) == sorted(['簽到', '簽退', '未遇', '簽到'])
    assert frame.loc[frame['任務ID'] == 'T3', '緯度'].isna().all()      # 補登不記位置
    recs, _ = normalize_records(frame)
    out = reconcile(_schedule(), recs, now=datetime.combine(date.today(), time(23, 59, 59))).set_index('任務ID')
    # 測試班表是整天，現在簽到簽退會是遲到、早退、時數不足；重點是有配對、定位在案家附近
    assert out.loc['T1', '簽到'] and out.loc['T1', '簽退'] and out.loc['T1', '距案家(公尺)'] == 0
    assert set(out.loc['T1', '異常項目'].split('、')) <= {'遲到', '早退', '時數不足'}
    assert out.loc['T2', '狀態'] == '未遇'
    assert set(out.loc['T3', '異常項目'].split('、')) == {'缺簽退', '補卡待審'}


def test_punches_are_append_only(db):
    store.publish_shifts(_schedule(), path=db)
    assert store.add_punch('E001', 'T1', '簽到', path=db)[0]
    con = store.connect(db)
    with pytest.raises(Exception):
        con.execute("UPDATE punches SET at='2000-01-01 00:00:00'")
    con.close()


def test_cannot_punch_other_days(db):
    store.publish_shifts(_schedule(date(2020, 1, 1)), path=db)
    ok, msg = store.add_punch('E001', 'T1', '簽到', path=db)
    assert not ok and '今天' in msg
