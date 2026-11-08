"""04 頁「📱 居服員打卡」：發布班表 → 一鍵產生 PIN → 今日打卡看板，全程不用指令視窗。"""
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

import caregiver_engine as engine
from checkin import store
from test_plan_change_ui import _setup


def test_checkin_panel_publish_pins_and_board(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    db = str(tmp_path / 'checkin.sqlite3')
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    assert any('居服員打卡' in m.value for m in app.markdown)
    assert any(':8600' in c.value for c in app.code)                     # 沒設固定網址：顯示辦公室網路位址

    app.button(key='checkin_publish').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert store.published_range(db)[2] > 0
    # 居服員 A 的第一支手機自動綁定；換第二支手機 → 04 頁出現待核准，督導按核准
    store.request_device('A', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Safari/604.1', db)
    store.request_device('A', 'Mozilla/5.0 (Linux; Android 14; SM-S9180 Build/UP1A) Chrome/120 Mobile Safari/537.36', db)
    app.run()
    assert any('1 支新手機等你核准' in w.value for w in app.warning)
    pending = store.devices(store.DEVICE_PENDING, db)[0]
    app.button(key=f"checkin_ok_{pending['id']}").click().run()
    assert not app.exception, [e.message for e in app.exception]
    active = store.devices(store.DEVICE_ACTIVE, db)
    assert [d['label'][:10] for d in active] == ['Android 14'] and not store.devices(store.DEVICE_PENDING, db)
    assert not any('等你核准' in w.value for w in app.warning)

    shift = store.shifts_for('A', date(2026, 11, 2), db)[0]
    app.date_input(key='checkin_board_day').set_value(date(2026, 11, 2)).run()
    assert not app.exception, [e.message for e in app.exception]
    board = store.day_board(date(2026, 11, 2), path=db)
    assert shift['task_id'] in set(board['任務ID'])
    assert any(m.label == '今天的班' and str(m.value) == str(len(board)) for m in app.metric)


def test_checkin_panel_without_qr_package_shows_install_button(tmp_path, monkeypatch):
    """Windows 上還沒裝 segno：不能整區報錯，要顯示網址＋一鍵安裝。"""
    from checkin import runner
    _setup(tmp_path, monkeypatch)
    missing = ['segno>=1.5,<2']
    calls = []
    monkeypatch.setattr(runner, 'missing_packages', lambda: list(missing))
    monkeypatch.setattr(runner, 'qr_available', lambda: not missing)

    def fake_install():
        calls.append(True)
        missing.clear()
        return True, '已安裝：segno'
    monkeypatch.setattr(runner, 'install_missing', fake_install)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    assert any('還缺打卡用的元件（segno）' in w.value for w in app.warning)
    assert any('裝好上方元件後' in i.value for i in app.info)
    assert any(':8600' in c.value for c in app.code)              # 網址照樣顯示
    app.button(key='checkin_install').click().run()
    assert calls and not app.exception, [e.message for e in app.exception]
    assert not any('還缺打卡用的元件' in w.value for w in app.warning)


def test_install_missing_reports_pip_failure(monkeypatch):
    import subprocess
    from checkin import runner
    monkeypatch.setattr(runner, 'missing_packages', lambda: ['segno>=1.5,<2'])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'ERROR: No matching distribution'))
    ok, msg = runner.install_missing()
    assert not ok and '連不上網路' in msg and 'No matching distribution' in msg


def test_checkin_panel_lets_supervisor_pick_network_adapter(tmp_path, monkeypatch):
    from checkin import runner
    from test_checkin_simple import IPCONFIG_ZH
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, 'lan_candidates', lambda: runner.parse_ipconfig(IPCONFIG_ZH))
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.selectbox(key='checkin_nic').value == '192.168.1.105'      # 預設挑真的 Wi-Fi，不是 WSL 虛擬網卡
    assert any(c.value == 'http://192.168.1.105:8600' for c in app.code)
    assert any('只在辦公室試用' in e.label for e in app.expander)
    app.selectbox(key='checkin_nic').select('192.168.128.2').run()
    assert any(c.value == 'http://192.168.128.2:8600' for c in app.code)


def test_updated_checkin_module_is_reloaded_without_restarting(tmp_path, monkeypatch):
    """CareFlow 開著時更新了 checkin/runner.py：重新整理頁面就要用新版，不能報 has no attribute。"""
    from checkin import runner
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    assert not app.exception
    monkeypatch.delattr(runner, 'lan_candidates')          # 模擬記憶體裡還是舊版模組
    monkeypatch.setattr(runner, '_careflow_mtime', 0.0, raising=False)
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    import sys
    assert hasattr(sys.modules['checkin.runner'], 'lan_candidates')


def test_checkin_panel_external_url_and_reset_all_pins(tmp_path, monkeypatch):
    from checkin import runner
    _setup(tmp_path, monkeypatch)
    state = {'url': ''}
    monkeypatch.setattr(runner, 'start_tunnel', lambda: (state.update(url='https://demo-1.trycloudflare.com') or True, state['url']))
    monkeypatch.setattr(runner, 'tunnel_url', lambda: state['url'])
    db = str(tmp_path / 'checkin.sqlite3')
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert any('只有連同一個 Wi-Fi' in w.value for w in app.warning)
    app.button(key='checkin_tunnel_start').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert any(c.value == 'https://demo-1.trycloudflare.com' for c in app.code)
    assert any('外部網址已開啟' in s.value for s in app.success)

    # 示範工具：建立示範班 → 手機打卡 → 看板出現地圖連結 → 清除
    app.button(key='checkin_publish').click().run()
    app.selectbox(key='checkin_demo_cg').select('A').run()
    app.button(key='checkin_demo_add').click().run()
    assert not app.exception, [e.message for e in app.exception]
    demo = [s for s in store.shifts_for('A', date.today(), db) if s['task_id'].startswith('DEMO-')]
    assert len(demo) == 1 and demo[0]['client_label'] == '示範案家'
    assert store.add_punch('A', demo[0]['task_id'], '簽到', lat=25.04, lon=121.53, path=db)[0]
    app.button(key='checkin_board_refresh').click().run() if False else app.run()
    assert not app.exception, [e.message for e in app.exception]
    board = [t for t in app.dataframe if '到勤位置' in t.value.columns]
    assert board and board[-1].value['到勤位置'].astype(str).str.contains('google.com/maps').any()
    assert '離開位置' in board[-1].value.columns
    app.button(key='checkin_demo_clear').click().run()
    assert not [s for s in store.shifts_for('A', date.today(), db) if s['task_id'].startswith('DEMO-')]
