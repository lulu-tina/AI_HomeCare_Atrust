"""CareFlow 登入與權限（內建帳號密碼）。

- 帳號存在本機 output_results/accounts.sqlite3；密碼只存 PBKDF2 雜湊（加鹽），系統與管理者都看不到原本的密碼。
- 角色：主管（全部模組＋帳號管理＋05 人力與效益分析）、居督（01～04）。
- 連續輸錯 5 次鎖 10 分鐘。第一次使用沒有任何帳號時，畫面請使用者建立第一個主管帳號。
- 開發／測試可設 CAREFLOW_AUTH=off 關閉登入。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROLES = ('主管', '居督')
MANAGER = '主管'
MAX_FAILED, LOCK_SECONDS, ROUNDS, MIN_LENGTH = 5, 600, 200_000, 8


def enabled():
    return os.getenv('CAREFLOW_AUTH', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def db_path(path=None):
    return Path(path or os.getenv('CAREFLOW_ACCOUNTS_DB') or Path(__file__).parent / 'output_results' / 'accounts.sqlite3')


def _connect(path=None):
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, '
               'salt TEXT NOT NULL, pw_hash TEXT NOT NULL, failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, '
               'active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT)')
    return db


def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ROUNDS).hex()


def _check_password(password):
    if len(password or '') < MIN_LENGTH:
        raise ValueError(f'密碼至少 {MIN_LENGTH} 個字。')


def _norm(username):
    return (username or '').strip().lower()


def has_users(path=None):
    db = _connect(path)
    try:
        return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None
    finally:
        db.close()


def create_user(username, name, role, password, path=None):
    username = _norm(username)
    if not username or not username.replace('_', '').replace('.', '').isalnum():
        raise ValueError('帳號請用英文字母或數字（可含 . 和 _）。')
    if role not in ROLES:
        raise ValueError('角色只能是主管或居督。')
    if not (name or '').strip():
        raise ValueError('請填姓名。')
    _check_password(password)
    salt = secrets.token_hex(16)
    db = _connect(path)
    try:
        with db:
            if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise ValueError(f'帳號 {username} 已經有人用了。')
            db.execute('INSERT INTO users(username, name, role, salt, pw_hash, created_at) VALUES(?,?,?,?,?,?)',
                       (username, name.strip(), role, salt, _hash(password, salt), datetime.now().isoformat(timespec='seconds')))
    finally:
        db.close()
    return username


def verify(username, password, path=None):
    """回傳 (成功, 使用者 dict 或錯誤訊息)。帳號不存在與密碼錯誤花一樣久、訊息一樣。"""
    username = _norm(username)
    db = _connect(path)
    try:
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if row is None or not row['active']:
            _hash(password or 'x', '00' * 16)
            return False, '帳號或密碼不正確。'
        if row['locked_until'] > time.time():
            return False, f"輸錯太多次，請 {int((row['locked_until'] - time.time()) // 60) + 1} 分鐘後再試，或請主管重設密碼。"
        if hmac.compare_digest(_hash(password or '', row['salt']), row['pw_hash']):
            with db:
                db.execute('UPDATE users SET failed=0, locked_until=0, last_login=? WHERE username=?',
                           (datetime.now().isoformat(timespec='seconds'), username))
            return True, {'username': username, 'name': row['name'], 'role': row['role']}
        failed = row['failed'] + 1
        locked = time.time() + LOCK_SECONDS if failed >= MAX_FAILED else 0
        with db:
            db.execute('UPDATE users SET failed=?, locked_until=? WHERE username=?', (0 if locked else failed, locked, username))
        return False, '帳號或密碼不正確。' + ('已鎖定 10 分鐘。' if locked else '')
    finally:
        db.close()


def set_password(username, password, path=None):
    _check_password(password)
    salt = secrets.token_hex(16)
    db = _connect(path)
    try:
        with db:
            n = db.execute('UPDATE users SET salt=?, pw_hash=?, failed=0, locked_until=0 WHERE username=?',
                           (salt, _hash(password, salt), _norm(username))).rowcount
    finally:
        db.close()
    if not n:
        raise ValueError('找不到這個帳號。')


def set_active(username, active, path=None):
    db = _connect(path)
    try:
        with db:
            if not active:
                row = db.execute('SELECT role FROM users WHERE username=?', (_norm(username),)).fetchone()
                managers = db.execute("SELECT COUNT(*) FROM users WHERE role=? AND active=1", (MANAGER,)).fetchone()[0]
                if row and row['role'] == MANAGER and managers <= 1:
                    raise ValueError('至少要保留一個可以登入的主管帳號。')
            db.execute('UPDATE users SET active=?, failed=0, locked_until=0 WHERE username=?', (int(bool(active)), _norm(username)))
    finally:
        db.close()


def users(path=None):
    db = _connect(path)
    try:
        return [dict(r) for r in db.execute('SELECT username, name, role, active, failed, locked_until, created_at, last_login '
                                            'FROM users ORDER BY role, username')]
    finally:
        db.close()
