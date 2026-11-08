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
