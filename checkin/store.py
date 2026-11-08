"""打卡網頁的資料庫（SQLite）。

三張表：
- shifts   ：居督從 CareFlow 04 模組「發布」的班表（只有 ID、時間、遮罩後的案家名稱）。
- punches  ：打卡紀錄，只能新增、不能修改（資料庫觸發器擋 UPDATE）；更正用「補登」新增一筆。
- accounts ：居服員 PIN（PBKDF2 雜湊＋鹽），連續輸錯鎖定。

punch_frame() 輸出的欄位就是 attendance_reconcile 吃的「一列一次打卡」格式，
CareFlow 04「執行對照」可直接讀取，不必再匯出匯入。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / 'output_results' / 'checkin.sqlite3'
KINDS = ('簽到', '簽退', '未遇')
MAX_FAILED = 5
LOCK_SECONDS = 10 * 60
PBKDF2_ROUNDS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts(
    task_id TEXT PRIMARY KEY, day TEXT NOT NULL, client_id TEXT, caregiver_id TEXT NOT NULL,
    start TEXT, end TEXT, minutes REAL, client_label TEXT, service TEXT, lat REAL, lon REAL, published_at TEXT);
CREATE INDEX IF NOT EXISTS shifts_cg_day ON shifts(caregiver_id, day);
CREATE TABLE IF NOT EXISTS punches(
    id INTEGER PRIMARY KEY AUTOINCREMENT, caregiver_id TEXT NOT NULL, client_id TEXT, task_id TEXT,
    kind TEXT NOT NULL, at TEXT NOT NULL, recorded_at TEXT NOT NULL,
    lat REAL, lon REAL, accuracy REAL, late_entry INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS punches_cg_at ON punches(caregiver_id, at);
CREATE TRIGGER IF NOT EXISTS punches_no_update BEFORE UPDATE ON punches
    BEGIN SELECT RAISE(ABORT, 'punches are append-only'); END;
CREATE TABLE IF NOT EXISTS accounts(
    caregiver_id TEXT PRIMARY KEY, salt TEXT NOT NULL, pin_hash TEXT NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS devices(
    id INTEGER PRIMARY KEY AUTOINCREMENT, caregiver_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, last_seen TEXT);
CREATE INDEX IF NOT EXISTS devices_cg ON devices(caregiver_id, status);
"""


def db_path(path=None):
    return Path(path or os.getenv('CAREFLOW_CHECKIN_DB') or DEFAULT_DB)


def connect(path=None):
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    return db


def now():
    return datetime.now().replace(microsecond=0)


def _stamp(moment):
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    out = str(value).strip()
    return out[:-2] if out.endswith('.0') and out[:-2].isdigit() else out


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float('inf') else None


def mask_name(name):
    """王小明 → 王○明；兩字 → 王○；用在居服員手機畫面。"""
    name = _text(name)
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + '○'
    return name[0] + '○' * (len(name) - 2) + name[-1]


# ---------------------------------------------------------------------------
# 班表發布
# ---------------------------------------------------------------------------
def publish_shifts(schedule, client_names=None, path=None):
    """把 CareFlow 最終班表（df_final）寫進打卡資料庫。

    只發布已指派的服務；發布範圍內（最早～最晚日期）原有但這次沒有的班會移除。
    打卡紀錄不受影響。回傳 (發布筆數, 移除筆數, 日期範圍)。
    """
    client_names = client_names or {}
    cg_col = next(c for c in ['最終派單居服員', '派單居服員', '居服員ID'] if c in schedule)
    client_col = next((c for c in ['案號', '案家ID'] if c in schedule), None)
    rows = []
    for _, r in schedule.drop_duplicates('任務ID', keep='last').iterrows():
        cg = _text(r.get(cg_col))
        day = pd.to_datetime(_text(r.get('日期')), errors='coerce')
        if not cg or cg in ('未指派', '待安排') or pd.isna(day):
            continue
        client = _text(r.get(client_col)) if client_col else ''
        rows.append((_text(r['任務ID']), day.date().isoformat(), client, cg,
                     _text(r.get('開始時間', r.get('時間窗_開始')))[:5], _text(r.get('結束時間', r.get('時間窗_結束')))[:5],
                     _num(r.get('服務分鐘')), mask_name(client_names.get(client, '')),
                     re.sub(r'\((\d+)\.0\)', r'(\1)', _text(r.get('服務項目'))), _num(r.get('服務地點_緯度')), _num(r.get('服務地點_經度')), _stamp(now())))
    if not rows:
        return 0, 0, None
    first, last = min(r[1] for r in rows), max(r[1] for r in rows)
    ids = {r[0] for r in rows}
    db = connect(path)
    try:
        with db:
            old = [x[0] for x in db.execute('SELECT task_id FROM shifts WHERE day BETWEEN ? AND ? AND task_id NOT LIKE ?',
                                            (first, last, 'DEMO-%'))]
            removed = [t for t in old if t not in ids]
            db.executemany('DELETE FROM shifts WHERE task_id=?', [(t,) for t in removed])
            db.executemany('INSERT OR REPLACE INTO shifts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    finally:
        db.close()
    return len(rows), len(removed), (first, last)


def shifts_for(caregiver_id, day, path=None):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute(
            'SELECT * FROM shifts WHERE caregiver_id=? AND day=? ORDER BY start, task_id', (caregiver_id, day.isoformat()))]
    finally:
        db.close()


def published_range(path=None):
    db = connect(path)
    try:
        r = db.execute('SELECT MIN(day), MAX(day), COUNT(*), MAX(published_at) FROM shifts').fetchone()
        return tuple(r)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 打卡
# ---------------------------------------------------------------------------
def punches_for(caregiver_id, day, path=None):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM punches WHERE caregiver_id=? AND substr(at,1,10)=? ORDER BY at, id",
            (caregiver_id, day.isoformat()))]
    finally:
        db.close()


def shift_state(punches):
    """一筆班的打卡狀態：未開始／服務中／已完成／未遇。"""
    kinds = {p['kind']: p for p in punches}
    if '未遇' in kinds:
        return '未遇'
    if '簽退' in kinds:
        return '已完成'
    if '簽到' in kinds:
        return '服務中'
    return '未開始'


def add_punch(caregiver_id, task_id, kind, lat=None, lon=None, accuracy=None, at=None, note='', path=None, today=None):
    """新增一筆打卡。at 有值＝補登（必須寫原因）。回傳 (成功, 訊息)。"""
    if kind not in KINDS:
        return False, '打卡類型不正確。'
    stamp = now()
    today = today or stamp.date()
    note = _text(note)[:200]
    late = at is not None
    if late:
        if kind == '未遇':
            return False, '未遇請直接按「未遇」，不需補登。'
        if not note:
            return False, '補登請寫原因（例如：手機沒電）。'
        try:
            hh, mm = [int(x) for x in str(at).split(':')[:2]]
            moment = datetime.combine(today, datetime.min.time()).replace(hour=hh, minute=mm)
        except (ValueError, TypeError):
            return False, '補登時間格式為 HH:MM。'
        if moment > stamp:
            return False, '補登時間不能晚於現在。'
    else:
        moment = stamp
    lat, lon, accuracy = _num(lat), _num(lon), _num(accuracy)
    if late:          # 補登時的位置不是當時的位置，不記，避免誤判地點異常
        lat = lon = accuracy = None
    if lat is not None and not (-90 <= lat <= 90 and -180 <= (lon or 0) <= 180):
        lat = lon = accuracy = None
    db = connect(path)
    try:
        shift = db.execute('SELECT * FROM shifts WHERE task_id=? AND caregiver_id=?', (task_id, caregiver_id)).fetchone()
        if shift is None or shift['day'] != today.isoformat():
            return False, '這筆班不是你今天的班；如果是臨時代班，請聯絡督導發布班表。'
        done = {r['kind'] for r in db.execute('SELECT kind FROM punches WHERE task_id=? AND caregiver_id=?',
                                               (task_id, caregiver_id))}
        if '未遇' in done:
            return False, '這筆班已登錄未遇。'
        if kind in done:
            return False, f'這筆班已經{kind}過了。'
        if kind == '簽退' and '簽到' not in done:
            return False, '還沒有簽到。忘記簽到請先用「補登」補簽到時間。'
        if kind == '未遇' and '簽到' in done:
            return False, '已經簽到的班不能改成未遇，請聯絡督導。'
        if kind == '簽退' and late:
            start = db.execute("SELECT at FROM punches WHERE task_id=? AND kind='簽到'", (task_id,)).fetchone()
            if start and _stamp(moment) < start['at']:
                return False, '簽退時間不能早於簽到時間。'
        with db:
            db.execute('INSERT INTO punches(caregiver_id, client_id, task_id, kind, at, recorded_at, lat, lon, accuracy, late_entry, note)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (caregiver_id, shift['client_id'], task_id, kind, _stamp(moment), _stamp(stamp),
                        lat, lon, accuracy, int(late), note))
    finally:
        db.close()
    head = f'已登錄未遇 {moment:%H:%M}，請記得通知督導' if kind == '未遇' else f"{ {'簽到': '到勤打卡', '簽退': '離開打卡'}[kind] }完成 {moment:%H:%M}"
    return True, head + ('（補登，待督導審核）' if late else '') + ('' if lat is not None or late else '（未取得定位）')


def punch_frame(start=None, end=None, path=None):
    """attendance_reconcile 的「一列一次打卡」格式。start/end 為 date（含）。"""
    db = connect(path)
    try:
        sql, args = 'SELECT * FROM punches WHERE task_id NOT LIKE ?', [DEMO_PREFIX + '%']   # 示範打卡不算進執行對照
        if start:
            sql += ' AND at >= ?'
            args.append(start.isoformat())
        if end:
            sql += ' AND at < ?'
            args.append((end + timedelta(days=1)).isoformat())
        rows = [dict(r) for r in db.execute(sql + ' ORDER BY at, id', args)]
    finally:
        db.close()
    cols = ['日期', '居服員ID', '案家ID', '任務ID', '打卡類型', '打卡時間', '服務結果', '補卡', '緯度', '經度', '定位精度(公尺)', '備註']
    out = [{'日期': r['at'][:10], '居服員ID': r['caregiver_id'], '案家ID': r['client_id'], '任務ID': r['task_id'],
            '打卡類型': r['kind'], '打卡時間': r['at'], '服務結果': '未遇' if r['kind'] == '未遇' else '',
            '補卡': '是' if r['late_entry'] else '', '緯度': r['lat'], '經度': r['lon'],
            '定位精度(公尺)': r['accuracy'], '備註': r['note']} for r in rows]
    return pd.DataFrame(out, columns=cols)


# ---------------------------------------------------------------------------
# 帳號與 PIN
# ---------------------------------------------------------------------------
def _hash(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def set_pin(caregiver_id, pin, path=None):
    pin = _text(pin)
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        raise ValueError('PIN 必須是 4～8 位數字。')
    salt = secrets.token_hex(16)
    db = connect(path)
    try:
        with db:
            db.execute('INSERT OR REPLACE INTO accounts(caregiver_id, salt, pin_hash, failed, locked_until, active)'
                       ' VALUES(?,?,?,0,0,1)', (_text(caregiver_id), salt, _hash(pin, salt)))
    finally:
        db.close()


def verify_pin(caregiver_id, pin, path=None):
    """回傳 (成功, 訊息)。連續錯 MAX_FAILED 次鎖定 LOCK_SECONDS。"""
    caregiver_id, pin = _text(caregiver_id), _text(pin)
    db = connect(path)
    try:
        row = db.execute('SELECT * FROM accounts WHERE caregiver_id=?', (caregiver_id,)).fetchone()
        if row is None or not row['active']:
            _hash(pin or '0000', '00' * 16)   # 讓「帳號不存在」與「PIN 錯」花一樣久
            return False, '員工編號或 PIN 不正確。'
        if row['locked_until'] > time.time():
            return False, f"輸錯太多次，請 {int((row['locked_until'] - time.time()) // 60) + 1} 分鐘後再試，或請督導重設。"
        if hmac.compare_digest(_hash(pin, row['salt']), row['pin_hash']):
            with db:
                db.execute('UPDATE accounts SET failed=0, locked_until=0 WHERE caregiver_id=?', (caregiver_id,))
            return True, ''
        failed = row['failed'] + 1
        locked = time.time() + LOCK_SECONDS if failed >= MAX_FAILED else 0
        with db:
            db.execute('UPDATE accounts SET failed=?, locked_until=? WHERE caregiver_id=?',
                       (0 if locked else failed, locked, caregiver_id))
        return False, '員工編號或 PIN 不正確。' + ('已鎖定 10 分鐘。' if locked else '')
    finally:
        db.close()


def accounts(path=None):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute('SELECT caregiver_id, failed, locked_until, active FROM accounts ORDER BY caregiver_id')]
    finally:
        db.close()


def set_active(caregiver_id, active, path=None):
    db = connect(path)
    try:
        with db:
            n = db.execute('UPDATE accounts SET active=?, failed=0, locked_until=0 WHERE caregiver_id=?',
                           (int(bool(active)), _text(caregiver_id))).rowcount
    finally:
        db.close()
    return n


def secret_key(path=None):
    """Flask session 金鑰：第一次啟動時產生並存進資料庫，重開不會把大家登出。"""
    env = os.getenv('CAREFLOW_CHECKIN_SECRET')
    if env:
        return env
    db = connect(path)
    try:
        row = db.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        if row:
            return row['value']
        key = secrets.token_hex(32)
        with db:
            db.execute("INSERT INTO settings VALUES('secret_key', ?)", (key,))
        return key
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 設定（打卡網址等）
# ---------------------------------------------------------------------------
def get_setting(key, default='', path=None):
    db = connect(path)
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default
    finally:
        db.close()


def set_setting(key, value, path=None):
    db = connect(path)
    try:
        with db:
            if _text(value):
                db.execute('INSERT OR REPLACE INTO settings VALUES(?, ?)', (key, _text(value)))
            else:
                db.execute('DELETE FROM settings WHERE key=?', (key,))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 一鍵產生 PIN（CareFlow 04 頁與 manage.py 共用）
# ---------------------------------------------------------------------------
def new_pin():
    return f'{secrets.randbelow(1_000_000):06d}'


def published_caregivers(path=None):
    db = connect(path)
    try:
        return [r[0] for r in db.execute('SELECT DISTINCT caregiver_id FROM shifts ORDER BY caregiver_id')]
    finally:
        db.close()


def generate_pins(caregivers=None, reset=False, path=None):
    """替居服員產生 6 位數 PIN；回傳 [(員工編號, PIN)]。明碼只在回傳值出現一次，資料庫只存雜湊。

    caregivers 省略＝已發布班表中的所有人；reset=False 時已經有 PIN 的人略過。
    """
    targets = [_text(c).upper() for c in (caregivers if caregivers is not None else published_caregivers(path)) if _text(c)]
    if not reset:
        have = {a['caregiver_id'] for a in accounts(path)}
        targets = [c for c in targets if c not in have]
    out = []
    for cg in dict.fromkeys(targets):
        pin = new_pin()
        set_pin(cg, pin, path)
        out.append((cg, pin))
    return out


# ---------------------------------------------------------------------------
# 今日打卡看板（督導後台）
# ---------------------------------------------------------------------------
def _metres(lat1, lon1, lat2, lon2):
    import math
    r = math.pi / 180
    a = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 12742000 * math.asin(min(1.0, math.sqrt(a)))


BOARD_COLUMNS = ['任務ID', '時段', '案家ID', '居服員ID', '狀態', '到勤', '離開', '距案家(公尺)', '打卡位置', '提醒']


def day_board(day, path=None, moment=None, late_grace_mins=15):
    """某一天已發布的班 × 打卡紀錄，一班一列。今天的班過了開始時間 15 分鐘還沒到勤，標「逾時未到勤」。"""
    moment = moment or now()
    db = connect(path)
    try:
        shifts = [dict(r) for r in db.execute('SELECT * FROM shifts WHERE day=? ORDER BY start, caregiver_id, task_id',
                                             (day.isoformat(),))]
        punches = [dict(r) for r in db.execute('SELECT * FROM punches WHERE substr(at,1,10)=? ORDER BY at, id',
                                              (day.isoformat(),))]
    finally:
        db.close()
    by_task = {}
    for p in punches:
        by_task.setdefault(p['task_id'], []).append(p)
    rows = []
    for s in shifts:
        mine = by_task.get(s['task_id'], [])
        kinds = {p['kind']: p for p in mine}
        state = shift_state(mine)
        notes = []
        if state == '未開始' and day == moment.date() and s['start']:
            hh, mm = map(int, s['start'].split(':'))
            if moment >= datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm) + timedelta(minutes=late_grace_mins):
                state = '逾時未到勤'
        distance = None
        first = kinds.get('簽到') or kinds.get('未遇')
        if first and first['lat'] is not None and s['lat'] is not None and s['lon'] is not None:
            distance = round(_metres(first['lat'], first['lon'], s['lat'], s['lon']))
        if any(p['late_entry'] for p in mine):
            notes.append('補登待審')
        if first and first['lat'] is None and not first['late_entry']:
            notes.append('無定位')
        for p in mine:
            if p['note'] and p['kind'] != '未遇':
                notes.append(p['note'])
        rows.append({'任務ID': s['task_id'], '時段': f"{s['start']}–{s['end']}", '案家ID': s['client_id'],
                     '居服員ID': s['caregiver_id'], '狀態': state,
                     '到勤': kinds['簽到']['at'][11:16] if '簽到' in kinds else '',
                     '離開': kinds['簽退']['at'][11:16] if '簽退' in kinds else '',
                     '距案家(公尺)': distance,
                     '打卡位置': (f"https://www.google.com/maps?q={first['lat']:.6f},{first['lon']:.6f}"
                                  if first and first['lat'] is not None and first['lon'] is not None else ''),
                     '提醒': '、'.join(notes)})
    return pd.DataFrame(rows, columns=BOARD_COLUMNS)


# ---------------------------------------------------------------------------
# 手機綁定（取代 PIN）：居服員只輸入員工編號。
# - 這個員編還沒有綁定任何手機 → 第一支手機自動綁定，直接可以打卡（可在 04 頁關掉自動綁定）。
# - 已經綁定別支手機 → 新手機變成「待核准」，督導在 04 頁按核准後才能用（舊手機同時解除）。
# 手機上只存一組隨機代碼（cookie），資料庫只存它的雜湊；督導可隨時解除綁定。
# ---------------------------------------------------------------------------
AUTO_BIND_KEY = 'auto_bind_first_device'
DEVICE_ACTIVE, DEVICE_PENDING, DEVICE_REVOKED, DEVICE_REJECTED = 'active', 'pending', 'revoked', 'rejected'


def _token_hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def device_label(user_agent):
    """從瀏覽器資訊猜手機種類，讓督導認得出是哪一支（例：iPhone・Safari、Android 14・Chrome）。"""
    ua = _text(user_agent)
    import re as _r
    if 'iPhone' in ua:
        phone = 'iPhone'
    elif 'iPad' in ua:
        phone = 'iPad'
    elif 'Android' in ua:
        m = _r.search(r'Android (\d+)', ua)
        model = _r.search(r'Android [\d.]+; (?:[^;)]*; )?([^;)]+?)(?: Build|\))', ua)
        phone = 'Android' + (f' {m.group(1)}' if m else '') + (f'（{model.group(1).strip()}）' if model and model.group(1).strip() not in ('K', 'wv') else '')
    elif 'Windows' in ua:
        phone = 'Windows 電腦'
    elif 'Macintosh' in ua:
        phone = 'Mac 電腦'
    else:
        phone = '其他裝置'
    browser = ('LINE' if ' Line/' in ua else 'Edge' if 'Edg/' in ua else 'Chrome' if ('Chrome/' in ua or 'CriOS' in ua)
               else 'Safari' if 'Safari/' in ua else '瀏覽器')
    return f'{phone}・{browser}'


def auto_bind_enabled(path=None):
    return get_setting(AUTO_BIND_KEY, '1', path=path) != '0'


def known_caregiver(caregiver_id, path=None):
    """只有出現在已發布班表（或已有帳號）的員編才能綁手機，亂打的編號不會建資料。"""
    caregiver_id = _text(caregiver_id).upper()
    db = connect(path)
    try:
        return bool(db.execute('SELECT 1 FROM shifts WHERE caregiver_id=? LIMIT 1', (caregiver_id,)).fetchone()
                    or db.execute('SELECT 1 FROM devices WHERE caregiver_id=? LIMIT 1', (caregiver_id,)).fetchone())
    finally:
        db.close()


def request_device(caregiver_id, user_agent='', path=None):
    """手機第一次用這個員編登入。回傳 (token, 狀態)；狀態 active＝可以直接打卡、pending＝等督導核准。"""
    caregiver_id = _text(caregiver_id).upper()
    token = secrets.token_urlsafe(32)
    stamp = _stamp(now())
    db = connect(path)
    try:
        with db:
            has_active = db.execute('SELECT 1 FROM devices WHERE caregiver_id=? AND status=?',
                                    (caregiver_id, DEVICE_ACTIVE)).fetchone()
            status = DEVICE_PENDING if has_active or not auto_bind_enabled(path) else DEVICE_ACTIVE
            # 同一人同時只留一筆待核准，避免重複按出很多筆
            db.execute('UPDATE devices SET status=?, decided_at=? WHERE caregiver_id=? AND status=?',
                       (DEVICE_REJECTED, stamp, caregiver_id, DEVICE_PENDING))
            db.execute('INSERT INTO devices(caregiver_id, token_hash, label, status, created_at, decided_at, last_seen)'
                       ' VALUES(?,?,?,?,?,?,?)', (caregiver_id, _token_hash(token), device_label(user_agent)[:60], status,
                                                  stamp, stamp if status == DEVICE_ACTIVE else None, stamp))
    finally:
        db.close()
    return token, status


def device_for_token(token, path=None, touch=True):
    """手機 cookie 對應的綁定紀錄（dict）或 None。"""
    if not token:
        return None
    db = connect(path)
    try:
        row = db.execute('SELECT * FROM devices WHERE token_hash=?', (_token_hash(token),)).fetchone()
        if row and touch and row['status'] == DEVICE_ACTIVE:
            with db:
                db.execute('UPDATE devices SET last_seen=? WHERE id=?', (_stamp(now()), row['id']))
        return dict(row) if row else None
    finally:
        db.close()


def decide_device(device_id, approve, path=None):
    """督導核准或拒絕。核准新手機時，同一人的舊手機會自動解除（一人一支手機）。回傳員編或 None。"""
    stamp = _stamp(now())
    db = connect(path)
    try:
        row = db.execute('SELECT * FROM devices WHERE id=?', (int(device_id),)).fetchone()
        if row is None or row['status'] != DEVICE_PENDING:
            return None
        with db:
            if approve:
                db.execute('UPDATE devices SET status=?, decided_at=? WHERE caregiver_id=? AND status=?',
                           (DEVICE_REVOKED, stamp, row['caregiver_id'], DEVICE_ACTIVE))
            db.execute('UPDATE devices SET status=?, decided_at=? WHERE id=?',
                       (DEVICE_ACTIVE if approve else DEVICE_REJECTED, stamp, row['id']))
        return row['caregiver_id']
    finally:
        db.close()


def revoke_device(device_id, path=None):
    db = connect(path)
    try:
        with db:
            return db.execute('UPDATE devices SET status=?, decided_at=? WHERE id=? AND status IN (?, ?)',
                              (DEVICE_REVOKED, _stamp(now()), int(device_id), DEVICE_ACTIVE, DEVICE_PENDING)).rowcount
    finally:
        db.close()


def devices(status=None, path=None):
    db = connect(path)
    try:
        sql, args = 'SELECT * FROM devices', ()
        if status:
            sql, args = sql + ' WHERE status=?', (status,)
        return [dict(r) for r in db.execute(sql + ' ORDER BY created_at DESC, id DESC', args)]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 示範班（demo 用）：今天、現在開始的一筆班，不設案家位置，不算進執行對照，可一鍵清除。
# ---------------------------------------------------------------------------
DEMO_PREFIX = 'DEMO-'


def add_demo_shift(caregiver_id, minutes=60, path=None):
    caregiver_id = _text(caregiver_id).upper()
    start = now().replace(second=0)
    end = min(start + timedelta(minutes=minutes), start.replace(hour=23, minute=59))
    task_id = f'{DEMO_PREFIX}{start:%Y%m%d%H%M}-{caregiver_id}'
    db = connect(path)
    try:
        with db:
            # 同一位居服員只留一筆示範班：重按「建立示範班」會換成新的時間，不會越疊越多
            old = [r[0] for r in db.execute('SELECT task_id FROM shifts WHERE task_id LIKE ? AND caregiver_id=?',
                                            (DEMO_PREFIX + '%', caregiver_id))]
            db.executemany('DELETE FROM punches WHERE task_id=?', [(t,) for t in old])
            db.executemany('DELETE FROM shifts WHERE task_id=?', [(t,) for t in old])
            db.execute('INSERT OR REPLACE INTO shifts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (task_id, start.date().isoformat(), 'DEMO', caregiver_id, f'{start:%H:%M}', f'{end:%H:%M}',
                        float(minutes), '示範案家', '示範服務', None, None, _stamp(now())))
    finally:
        db.close()
    return task_id


def clear_demo(path=None):
    """刪除示範班與示範打卡（只動 DEMO- 開頭的資料，正式打卡紀錄不受影響）。回傳刪除的班數。"""
    db = connect(path)
    try:
        with db:
            db.execute('DELETE FROM punches WHERE task_id LIKE ?', (DEMO_PREFIX + '%',))
            return db.execute('DELETE FROM shifts WHERE task_id LIKE ?', (DEMO_PREFIX + '%',)).rowcount
    finally:
        db.close()
