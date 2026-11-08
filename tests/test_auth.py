"""登入與權限：第一次建立主管、登入／鎖定、居督不能看 05、主管可以管理帳號、稽核記錄實際帳號。"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import auth
import caregiver_engine as engine


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / 'accounts.sqlite3')


def test_accounts_lockout_and_last_manager(db):
    assert not auth.has_users(db)
    auth.create_user('Boss', '王主管', '主管', 'secret123', db)
    auth.create_user('amy', '林居督', '居督', 'secret123', db)
    with pytest.raises(ValueError, match='已經有人用'):
        auth.create_user('boss', '另一位', '居督', 'secret123', db)
    with pytest.raises(ValueError, match='至少'):
        auth.create_user('bob', '短', '居督', '123', db)
    ok, user = auth.verify('BOSS', 'secret123', db)
    assert ok and user == {'username': 'boss', 'name': '王主管', 'role': '主管'}
    for _ in range(auth.MAX_FAILED):
        assert not auth.verify('amy', 'wrong', db)[0]
    ok, msg = auth.verify('amy', 'secret123', db)
    assert not ok and '輸錯太多次' in msg                       # 鎖定中，密碼對也不能進
    auth.set_password('amy', 'newpass99', db)                   # 主管重設密碼同時解鎖
    assert auth.verify('amy', 'newpass99', db)[0]
    with pytest.raises(ValueError, match='至少要保留一個'):
        auth.set_active('boss', False, db)
    auth.set_active('amy', False, db)
    assert auth.verify('amy', 'newpass99', db) == (False, '帳號或密碼不正確。')
    stored = Path(db).read_bytes()
    assert b'secret123' not in stored and b'newpass99' not in stored   # 不存明碼


def _app(tmp_path, monkeypatch):
    from test_plan_change_ui import _setup
    _setup(tmp_path, monkeypatch)
    monkeypatch.setenv('CAREFLOW_AUTH', 'on')
    monkeypatch.setenv('CAREFLOW_ACCOUNTS_DB', str(tmp_path / 'accounts.sqlite3'))
    monkeypatch.setenv('CAREFLOW_AUDIT_DIR', str(tmp_path / 'audit'))
    return AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90)


def test_first_run_creates_manager_then_login_and_roles(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch).run()
    assert any('建立第一個' in i.value for i in app.info)
    assert not any(r.key == 'cf_module' for r in app.radio)              # 沒登入看不到任何排班畫面
    app.text_input(key='setup_user').input('boss')
    app.text_input(key='setup_name').input('王主管')
    app.text_input(key='setup_pw1').input('secret123')
    app.text_input(key='setup_pw2').input('secret123')
    app.button[0].click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.session_state['auth_user']['role'] == '主管'
    assert any('王主管（主管）' in c.value for c in app.sidebar.caption)
    auth.create_user('amy', '林居督', '居督', 'secret123', str(tmp_path / 'accounts.sqlite3'))

    app.session_state['auth_user'] = None                                # 模擬重新整理後的新連線
    del app.session_state['auth_user']
    app.run()
    app.text_input(key='login_user').input('amy')
    app.text_input(key='login_pw').input('wrong-pass')
    app.button[0].click().run()
    assert any('帳號或密碼不正確' in e.value for e in app.error)
    app.text_input(key='login_pw').input('secret123')
    app.button[0].click().run()
    assert app.session_state['auth_user']['role'] == '居督'
    app.radio(key='cf_module').set_value('analytics').run()
    assert any('只有主管帳號可以查看' in w.value for w in app.warning)    # 居督不能看 05
    assert not any('帳號管理' in e.label for e in app.sidebar.expander)  # 居督沒有帳號管理

    import audit_log
    events = audit_log.entries(folder=str(tmp_path / 'audit'))
    logins = [e for e in events if e['event'] == 'login']
    assert [e['account'] for e in logins] == ['amy']                     # 稽核記的是實際登入的帳號，不再是 supervisor
    assert any(e['event'] == 'login_failed' and e['account'] == 'amy' for e in events)
