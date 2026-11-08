"""錯誤不再被默默吞掉：寫進紀錄檔（含追蹤），側邊欄提示，畫面其他功能照常。"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

import caregiver_engine as engine
import error_log


def test_log_error_writes_traceback_and_returns_code(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, 'LOG_DIR', tmp_path)
    for handler in list(error_log._logger.handlers):
        error_log._logger.removeHandler(handler)
    try:
        {}['missing']
    except KeyError as exc:
        code = error_log.log_error('測試位置', exc)
    text = (tmp_path / 'careflow_errors.log').read_text(encoding='utf-8')
    assert code.startswith('E') and code in text and '測試位置' in text and 'Traceback' in text and "KeyError: 'missing'" in text
    for handler in list(error_log._logger.handlers):
        error_log._logger.removeHandler(handler)


def test_swallowed_error_is_logged_and_shown_in_sidebar(tmp_path, monkeypatch):
    from test_plan_change_ui import _setup
    import session_store
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(error_log, 'LOG_DIR', tmp_path / 'logs')
    for handler in list(error_log._logger.handlers):
        error_log._logger.removeHandler(handler)

    def broken(*a, **k):
        raise RuntimeError('存檔資料庫壞了')
    monkeypatch.setattr(session_store, 'info', broken)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    assert not app.exception, [e.message for e in app.exception]          # 畫面照常
    errors = app.session_state['_error_log']
    assert any(e['位置'] == '讀取自動存檔資訊' and '存檔資料庫壞了' in e['錯誤'] for e in errors)
    assert '存檔資料庫壞了' in (tmp_path / 'logs' / 'careflow_errors.log').read_text(encoding='utf-8')
    app.run()                                                              # 下一次畫面重跑，側邊欄就看得到
    assert any('本次作業有' in e.label for e in app.sidebar.expander)
    for handler in list(error_log._logger.handlers):
        error_log._logger.removeHandler(handler)
