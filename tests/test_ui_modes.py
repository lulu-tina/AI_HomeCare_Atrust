"""居督模式（預設）把進階功能藏起來；打開「進階模式」才出現。04 的異動紀錄合成一張表。"""
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

import caregiver_engine as engine
from test_plan_change_ui import _setup


def _texts(app):
    return ' '.join([e.label for e in app.expander] + [m.value for m in app.markdown] + [c.value for c in app.caption])


def test_supervisor_mode_hides_advanced_and_toggle_brings_back(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    assert app.session_state['ui_advanced'] is False
    # 01：沒有規則滑桿、資料表只供檢視
    assert not [s for s in app.slider if s.key == 'cfg_buffer_mins']
    assert '檢視居服員、案家與服務任務' in _texts(app)
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    assert not app.exception, [e.message for e in app.exception]
    text = _texts(app)
    assert '人工調整' not in text and '演算法指派明細' not in text and '單筆配對說明' not in text

    app.sidebar.toggle(key='ui_advanced').set_value(True).run()
    assert not app.exception, [e.message for e in app.exception]
    text = _texts(app)
    assert '人工調整' in text and '演算法指派明細（進階）' in text
    app.radio(key='cf_module').set_value('import').run()
    assert [s for s in app.slider if s.key == 'cfg_buffer_mins']


def test_review_page_has_single_change_log(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    text = _texts(app)
    assert '異動紀錄' in text and '居督覆寫稽覈日誌' not in [t.label for t in app.tabs]


def test_schedule_waits_for_sleeping_traffic_host_then_runs(tmp_path, monkeypatch):
    """交通主機還在開機時按開始排班：先等主機開好（畫面顯示已等幾秒），再自動排班。"""
    import time
    import traffic_wake
    _setup(tmp_path, monkeypatch)
    woke_at = {}

    def fake_status(host):
        if 'start' not in woke_at:
            return {'status': 'idle'}
        return {'status': 'waking' if time.time() < woke_at['start'] + 3 else 'ready', 'since': 0}
    monkeypatch.setattr(traffic_wake, 'is_ready', lambda host: fake_status(host)['status'] == 'ready')
    monkeypatch.setattr(traffic_wake, 'status', fake_status)
    monkeypatch.setattr(traffic_wake, 'start', lambda host: woke_at.setdefault('start', time.time()) and True)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    began = time.time()
    app.button(key='run_full_period').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert time.time() - began >= 2                                   # 有等主機
    assert not app.session_state['last_result']['df_tasks'].empty     # 等完有排班
