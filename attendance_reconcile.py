"""執行對照：排定班表 vs 打卡／服務紀錄。

讀入機構匯出的打卡或服務紀錄（Excel／CSV），逐筆對上班表裡的服務，標出：
未遇、無紀錄（未到或漏登）、遲到、早退、時數不足、缺簽到／簽退、補卡待審、
換人服務、地點異常、排外服務（有打卡但班表沒有）。

只用 ID 運算；姓名由顯示層（app.py）依遮罩設定處理。容許值都來自 PipelineConfig。
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta

import pandas as pd

# ---------------------------------------------------------------------------
# 欄位對照：機構匯出的欄名五花八門，這裡統一成標準欄名
# ---------------------------------------------------------------------------
COLUMN_ALIASES = {
    '任務ID': ['任務ID', '任務編號', '服務單號', '派案編號'],
    '居服員ID': ['居服員ID', '員工編號', '居服員編號', '服務人員編號', '服務人員ID', '派單居服員', '居服員'],
    '案家ID': ['案家ID', '案號', '個案編號', '案家編號', '個案ID', '個案代碼'],
    '日期': ['日期', '服務日期', '打卡日期'],
    '簽到時間': ['簽到時間', '簽到', '上班打卡', '到達時間', '實際開始時間', '實際開始', '服務開始時間', '開始時間'],
    '簽退時間': ['簽退時間', '簽退', '下班打卡', '離開時間', '實際結束時間', '實際結束', '服務結束時間', '結束時間'],
    '服務結果': ['服務結果', '服務狀態', '執行狀態', '執行結果', '狀態'],
    '補卡': ['補卡', '是否補卡', '補登', '補登打卡'],
    '緯度': ['緯度', '簽到緯度', '打卡緯度', 'GPS緯度', 'lat', 'Lat', 'latitude'],
    '經度': ['經度', '簽到經度', '打卡經度', 'GPS經度', 'lon', 'lng', 'Lon', 'longitude'],
    '打卡類型': ['打卡類型', '打卡別', '打卡種類', '簽到退'],
    '打卡時間': ['打卡時間', '打卡時刻'],
    '備註': ['備註', '說明'],
}
REQUIRED_ANY = [['居服員ID', '案家ID', '任務ID']]

MISSED_WORDS = ('未遇', '案家不在', '不在家', '未應門', '無人應門', '拒絕服務', '拒絕')
CANCEL_WORDS = ('取消', '暫停', '住院', '請假')
TRUE_WORDS = {'y', 'yes', 'true', '1', '是', 'v', '✓', '補卡', '補登'}
CHECKIN_WORDS = ('簽到', '上班', '到達', '進入')
CHECKOUT_WORDS = ('簽退', '下班', '離開', '結束')
CHECKIN_EXACT = {'進', 'in', 'checkin', 'check-in'}
CHECKOUT_EXACT = {'出', 'out', 'checkout', 'check-out'}

# 狀態：依優先順序取第一個當「主要狀態」；嚴重度供督導排序處理
FLAG_ORDER = ['排外服務', '重複紀錄', '無紀錄', '未遇', '案家取消', '地點異常', '換人服務', '缺簽到', '缺簽退',
              '時數不足', '補卡待審', '遲到', '早退']
SEVERITY = {
    '排外服務': '高', '無紀錄': '高', '未遇': '高', '地點異常': '高',
    '重複紀錄': '中', '案家取消': '中', '換人服務': '中', '缺簽到': '中', '缺簽退': '中', '時數不足': '中', '補卡待審': '中',
    '遲到': '低', '早退': '低',
}
ADVICE = {
    '無紀錄': '聯絡居服員確認是否到班；未到請啟動代班並通知案家，漏登請走補卡。',
    '未遇': '確認案家安全與去向（必要時聯絡家屬）；依機構規定登錄未遇。',
    '排外服務': '班表沒有這筆服務：確認是否臨時加班、改期或打錯案家。',
    '重複紀錄': '同一筆服務有多筆紀錄，請保留正確的一筆。',
    '案家取消': '確認取消已通知居服員，並依規定處理當日工資與計費。',
    '地點異常': '打卡位置離案家太遠：確認是否在案家打卡。',
    '換人服務': '實際服務人員與班表不同：確認是否為代班並補登。',
    '缺簽到': '缺簽到：請居服員補卡並由督導審核。',
    '缺簽退': '缺簽退：請居服員補卡並由督導審核。',
    '時數不足': '實際服務時間少於排定：確認原因，必要時調整計費時數。',
    '補卡待審': '補卡紀錄，請督導審核。',
    '遲到': '遲到：留意是否轉場時間不足，或是常態性遲到。',
    '早退': '早退：確認是否案家同意提前結束。',
}
STATUS_OK = '正常'
STATUS_NOT_YET = '尚無紀錄（時間未到）'
STATUS_UNASSIGNED = '未派班（不對照）'

OUTPUT_COLUMNS = ['狀態', '嚴重度', '異常項目', '日期', '案家ID', '任務ID', '排定居服員', '實際居服員',
                  '排定開始', '排定結束', '簽到', '簽退', '排定分鐘', '實際分鐘', '遲到(分)', '早退(分)',
                  '距案家(公尺)', '服務結果', '補卡', '建議處理', '紀錄備註']


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text[:-2] if re.fullmatch(r'\d+\.0', text) else text


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _to_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    # 民國年 113/11/05、113-11-05
    m = re.fullmatch(r'(\d{2,3})[/\-.](\d{1,2})[/\-.](\d{1,2})', text)
    if m and int(m[1]) < 1000:
        try:
            return date(int(m[1]) + 1911, int(m[2]), int(m[3]))
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors='coerce')
    return None if pd.isna(parsed) else parsed.date()


def _to_moment(value, day):
    """回傳 datetime；只有時刻時接上 day；無法解析回傳 None。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, time):
        return datetime.combine(day, value) if day else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return None
        if 0 <= value < 1 and day:          # Excel 時間小數
            return datetime.combine(day, time()) + timedelta(days=float(value))
        return None
    text = _text(value)
    if not text:
        return None
    m = re.fullmatch(r'(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?', text)
    if m:
        if not day:
            return None
        hour, minute = int(m[1]), int(m[2])
        if hour == 24 and minute == 0:
            return datetime.combine(day, time()) + timedelta(days=1)
        if hour > 23 or minute > 59:
            return None
        return datetime.combine(day, time(hour, minute, int(m[3] or 0)))
    m = re.fullmatch(r'(\d{1,2})(\d{2})', text)       # 0830
    if m and day and int(m[1]) < 24 and int(m[2]) < 60:
        return datetime.combine(day, time(int(m[1]), int(m[2])))
    parsed = pd.to_datetime(text, errors='coerce')
    if pd.isna(parsed):
        return None
    parsed = parsed.to_pydatetime()
    # 只有時刻的字串被解析成今天：改接 day
    if day and not re.search(r'\d{4}|\d{1,2}[/\-]\d{1,2}[/\-]', text):
        return datetime.combine(day, parsed.time())
    return parsed


def _plain(value):
    """NaT／NaN → None；Timestamp → datetime（避免 NaT 在 if 判斷中被當成 True）。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


def _objectify(frame, columns):
    for col in columns:
        if col in frame:
            frame[col] = pd.Series([_plain(v) for v in frame[col]], index=frame.index, dtype=object)
    return frame


def _haversine_m(lat1, lon1, lat2, lon2):
    vals = [_number(v) for v in (lat1, lon1, lat2, lon2)]
    if any(v is None for v in vals) or not (-90 <= vals[0] <= 90 and -90 <= vals[2] <= 90):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, vals)
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _contains(text, words):
    low = text.lower()
    return any(w.lower() in low for w in words)


def _clock(moment):
    return moment.strftime('%H:%M') if moment else ''


def _cfg(config, name, default):
    return getattr(config, name, default) if config is not None else default


# ---------------------------------------------------------------------------
# 讀檔與正規化
# ---------------------------------------------------------------------------
def read_records(uploaded, filename=''):
    """讀 Excel 或 CSV（UTF-8／Big5 皆可）。uploaded 可以是路徑、bytes 或檔案物件。"""
    import io
    name = (filename or getattr(uploaded, 'name', '') or str(uploaded if isinstance(uploaded, str) else '')).lower()
    data = uploaded
    if hasattr(uploaded, 'getvalue'):
        data = uploaded.getvalue()
    if isinstance(data, (bytes, bytearray)):
        if name.endswith('.csv'):
            for enc in ('utf-8-sig', 'cp950', 'big5'):
                try:
                    return pd.read_csv(io.BytesIO(data), dtype=str, encoding=enc)
                except UnicodeDecodeError:
                    continue
            raise ValueError('CSV 編碼無法辨識，請另存為 UTF-8。')
        return pd.read_excel(io.BytesIO(data), dtype=object)
    if name.endswith('.csv'):
        return pd.read_csv(data, dtype=str, encoding='utf-8-sig')
    return pd.read_excel(data, dtype=object)


def _rename(frame):
    frame = frame.copy()
    frame.columns = [_text(c).replace(' ', '').replace('　', '') for c in frame.columns]
    mapping, used = {}, set()
    for std, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            hit = next((c for c in frame.columns if c == alias and c not in used), None)
            if hit is None:
                hit = next((c for c in frame.columns if c.lower() == alias.lower() and c not in used), None)
            if hit is not None:
                mapping[hit] = std
                used.add(hit)
                break
    return frame[list(mapping)].rename(columns=mapping)


def _pair_punches(frame):
    """一列一次打卡（打卡類型＋打卡時間）→ 一列一次服務（簽到＋簽退），依時間順序配對。"""
    frame = frame.copy()
    frame['_day'] = [_to_day(d) for d in frame.get('日期', pd.Series(None, index=frame.index))]
    frame['_t'] = [_to_moment(t, d) for t, d in zip(frame['打卡時間'], frame['_day'])]
    frame['_day'] = [d or (t.date() if t else None) for d, t in zip(frame['_day'], frame['_t'])]
    frame['_kind'] = ['in' if _contains(_text(k), CHECKIN_WORDS) or _text(k).lower() in CHECKIN_EXACT
                      else 'out' if _contains(_text(k), CHECKOUT_WORDS) or _text(k).lower() in CHECKOUT_EXACT else ''
                      for k in frame['打卡類型']]
    keys = [c for c in ['居服員ID', '案家ID', '任務ID'] if c in frame]
    rows = []
    for _, grp in frame.sort_values('_t', na_position='last').groupby(keys + ['_day'], dropna=False, sort=False):
        open_row = None
        for _, r in grp.iterrows():
            base = {k: r.get(k) for k in keys}
            base['日期'] = r['_day']
            for extra in ['服務結果', '補卡', '備註']:
                if extra in r and _text(r[extra]):
                    base[extra] = r[extra]
            if r['_kind'] == 'in':
                if open_row is not None:
                    rows.append(open_row)
                open_row = dict(base, 簽到時間=r['_t'], 緯度=r.get('緯度'), 經度=r.get('經度'))
            elif r['_kind'] == 'out':
                if open_row is None:
                    rows.append(dict(base, 簽到時間=None, 簽退時間=r['_t']))
                else:
                    for extra in ['服務結果', '補卡', '備註']:
                        if extra in base and not _text(open_row.get(extra)):
                            open_row[extra] = base[extra]
                    open_row['簽退時間'] = r['_t']
                    rows.append(open_row)
                    open_row = None
            else:   # 沒有類型（例如只登錄未遇）：當成一筆獨立紀錄
                rows.append(dict(base, 簽到時間=r['_t']))
        if open_row is not None:
            rows.append(open_row)
    return pd.DataFrame(rows)


def normalize_records(raw):
    """回傳標準欄位的紀錄表（每列一次服務）與辨識說明。

    欄位：紀錄序號、任務ID、居服員ID、案家ID、日期(date)、簽到(datetime)、簽退(datetime)、
    服務結果、補卡(bool)、緯度、經度、備註。
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=['紀錄序號']), {'辨識欄位': [], '略過列數': 0}
    frame = _rename(raw)
    recognized = list(frame.columns)
    if not any(c in frame for c in REQUIRED_ANY[0]):
        raise ValueError('找不到居服員、案家或任務ID欄位；請參考範本欄名（例如「居服員ID」「案家ID」）。')
    if '打卡類型' in frame and '打卡時間' in frame and '簽到時間' not in frame:
        frame = _pair_punches(frame)
    out_rows, skipped = [], 0
    for _, r in frame.iterrows():
        day = _to_day(r.get('日期'))
        start = _to_moment(r.get('簽到時間'), day)
        end = _to_moment(r.get('簽退時間'), day)
        if day is None:
            day = (start or end).date() if (start or end) else None
        if start is None and day is not None:
            start = _to_moment(r.get('簽到時間'), day)
        if end is None and day is not None:
            end = _to_moment(r.get('簽退時間'), day)
        if start and end and end < start and (start - end) > timedelta(hours=12):
            end += timedelta(days=1)      # 跨午夜
        cg, client, task = _text(r.get('居服員ID')), _text(r.get('案家ID')), _text(r.get('任務ID'))
        if day is None and not task:
            skipped += 1
            continue
        if not (cg or client or task):
            skipped += 1
            continue
        out_rows.append({
            '任務ID': task, '居服員ID': cg, '案家ID': client, '日期': day,
            '簽到': start, '簽退': end,
            '服務結果': _text(r.get('服務結果')),
            '補卡': _text(r.get('補卡')).lower() in TRUE_WORDS,
            '緯度': _number(r.get('緯度')), '經度': _number(r.get('經度')),
            '備註': _text(r.get('備註')),
        })
    out = pd.DataFrame(out_rows, columns=['任務ID', '居服員ID', '案家ID', '日期', '簽到', '簽退', '服務結果',
                                          '補卡', '緯度', '經度', '備註'])
    out.insert(0, '紀錄序號', range(1, len(out) + 1))
    return out, {'辨識欄位': recognized, '略過列數': skipped}


def _normalize_schedule(schedule):
    """接受 app 的最終班表（df_final）或任務表；回傳標準欄位。"""
    s = schedule.copy()
    cg_col = next((c for c in ['最終派單居服員', '派單居服員', '居服員ID'] if c in s), None)
    client_col = next((c for c in ['案號', '案家ID'] if c in s), None)
    start_col = next((c for c in ['開始時間', '時間窗_開始'] if c in s), None)
    end_col = next((c for c in ['結束時間', '時間窗_結束'] if c in s), None)
    rows = []
    for _, r in s.drop_duplicates('任務ID', keep='last').iterrows():
        day = _to_day(r.get('日期'))
        start = _to_moment(r.get(start_col), day) if start_col else None
        end = _to_moment(r.get(end_col), day) if end_col else None
        if start and end and end <= start:
            end += timedelta(days=1)
        cg = _text(r.get(cg_col)) if cg_col else ''
        rows.append({
            '任務ID': _text(r.get('任務ID')), '日期': day,
            '案家ID': _text(r.get(client_col)) if client_col else '',
            '排定居服員': '' if cg in ('', '未指派', '待安排') else cg,
            '排定開始': start, '排定結束': end,
            '排定分鐘': _number(r.get('服務分鐘')) or (round((end - start).total_seconds() / 60) if start and end else None),
            '案家緯度': _number(r.get('服務地點_緯度')), '案家經度': _number(r.get('服務地點_經度')),
        })
    return _objectify(pd.DataFrame(rows, columns=['任務ID', '日期', '案家ID', '排定居服員', '排定開始', '排定結束',
                                                  '排定分鐘', '案家緯度', '案家經度']),
                      ['日期', '排定開始', '排定結束', '排定分鐘'])


# ---------------------------------------------------------------------------
# 配對與判定
# ---------------------------------------------------------------------------
def _match(sched, recs, window_mins):
    """回傳 {排定列索引: 紀錄列索引} 與重複紀錄集合。"""
    matched, used, duplicate = {}, set(), set()
    task_index = {t: i for i, t in sched['任務ID'].items() if t}
    # 1) 有任務ID的直接對
    for j, rec in recs.iterrows():
        i = task_index.get(rec['任務ID']) if rec['任務ID'] else None
        if i is None:
            continue
        if i in matched:
            duplicate.add(j)
        else:
            matched[i] = j
        used.add(j)
    # 2) 其餘依 日期＋案家（或沒有案家時用居服員）＋時間最近，貪婪配對
    by_day = {}
    for i, row in sched.iterrows():
        if i not in matched and row['日期']:
            by_day.setdefault(row['日期'], []).append(i)
    pairs = []
    for j, rec in recs.iterrows():
        if j in used or rec['日期'] is None:
            continue
        for i in by_day.get(rec['日期'], []):
            row = sched.loc[i]
            same_client = bool(rec['案家ID']) and rec['案家ID'] == row['案家ID']
            same_cg = bool(rec['居服員ID']) and rec['居服員ID'] == row['排定居服員']
            if not (same_client or (not rec['案家ID'] and same_cg)):
                continue
            anchor = rec['簽到'] or rec['簽退']
            ref = row['排定開始'] if rec['簽到'] else row['排定結束']
            if anchor and ref:
                gap = abs((anchor - ref).total_seconds()) / 60
                if gap > window_mins:
                    continue
            else:
                gap = window_mins       # 沒有時間（例如只登錄未遇）：排在有時間的配對之後
            pairs.append((0 if same_cg else 1, gap, i, j))
    for _, _, i, j in sorted(pairs):
        if i in matched or j in used:
            continue
        matched[i] = j
        used.add(j)
    return matched, duplicate


def _flags_for(row, rec, config):
    grace_late = float(_cfg(config, 'attendance_late_grace_mins', 10.0))
    grace_early = float(_cfg(config, 'attendance_early_leave_grace_mins', 10.0))
    short_ratio = float(_cfg(config, 'attendance_short_ratio', 0.9))
    radius = float(_cfg(config, 'attendance_gps_radius_m', 300.0))
    flags, info = [], {}
    outcome = rec['服務結果']
    if _contains(outcome, MISSED_WORDS):
        flags.append('未遇')
    elif _contains(outcome, CANCEL_WORDS):
        flags.append('案家取消')
    stopped = bool(flags)
    if rec['居服員ID'] and row['排定居服員'] and rec['居服員ID'] != row['排定居服員']:
        flags.append('換人服務')
    if not stopped:
        if rec['簽到'] is None:
            flags.append('缺簽到')
        if rec['簽退'] is None:
            flags.append('缺簽退')
    if rec['補卡']:
        flags.append('補卡待審')
    late = early = actual = None
    if rec['簽到'] and row['排定開始']:
        late = round((rec['簽到'] - row['排定開始']).total_seconds() / 60)
        if late > grace_late and not stopped:
            flags.append('遲到')
    if rec['簽退'] and row['排定結束']:
        early = round((row['排定結束'] - rec['簽退']).total_seconds() / 60)
        if early > grace_early and not stopped:
            flags.append('早退')
    if rec['簽到'] and rec['簽退']:
        actual = round((rec['簽退'] - rec['簽到']).total_seconds() / 60)
        planned = row['排定分鐘']
        if planned and actual < planned * short_ratio and not stopped:
            flags.append('時數不足')
    dist = _haversine_m(rec['緯度'], rec['經度'], row['案家緯度'], row['案家經度'])
    if dist is not None and dist > radius:
        flags.append('地點異常')
    info.update({'遲到(分)': max(late, 0) if late is not None else None,
                 '早退(分)': max(early, 0) if early is not None else None,
                 '實際分鐘': actual, '距案家(公尺)': round(dist) if dist is not None else None})
    return flags, info


def _finish(flags):
    flags = sorted(set(flags), key=FLAG_ORDER.index)
    if not flags:
        return STATUS_OK, '', '', ''
    sev_rank = {'高': 0, '中': 1, '低': 2}
    severity = min((SEVERITY[f] for f in flags), key=sev_rank.get)
    return flags[0], severity, '、'.join(flags), ' '.join(ADVICE[f] for f in flags[:2])


def reconcile(schedule, records, config=None, as_of=None, now=None):
    """排定 vs 實際。

    schedule：最終班表（df_final）或任務表；records：normalize_records 的結果。
    as_of：對照截止日（含）；之後的排定服務不判「無紀錄」。預設為紀錄中最晚的日期。
    now：現在時刻；排定結束還沒到的服務不判「無紀錄」「缺簽退」（當天邊服務邊對照時用）。
    回傳 DataFrame（OUTPUT_COLUMNS），每筆排定服務一列，加上排外紀錄。
    """
    sched = _normalize_schedule(schedule).reset_index(drop=True)
    recs = records.reset_index(drop=True) if records is not None else pd.DataFrame()
    if recs.empty:
        recs = pd.DataFrame(columns=['紀錄序號', '任務ID', '居服員ID', '案家ID', '日期', '簽到', '簽退',
                                     '服務結果', '補卡', '緯度', '經度', '備註'])
    recs = _objectify(recs.copy(), ['日期', '簽到', '簽退'])
    for col in ['任務ID', '居服員ID', '案家ID', '服務結果', '備註']:
        recs[col] = recs[col].map(_text)
    recs['補卡'] = recs['補卡'].map(lambda v: bool(_plain(v)))
    if as_of is None:
        days = [d for d in recs['日期'] if d]
        as_of = max(days) if days else None
    elif isinstance(as_of, datetime):
        as_of = as_of.date()
    now = now or datetime.now()
    window = float(_cfg(config, 'attendance_match_window_mins', 180.0))
    matched, duplicate = _match(sched, recs, window)

    rows = []
    for i, row in sched.iterrows():
        base = {'日期': row['日期'].isoformat() if row['日期'] else '', '案家ID': row['案家ID'], '任務ID': row['任務ID'],
                '排定居服員': row['排定居服員'], '排定開始': _clock(row['排定開始']), '排定結束': _clock(row['排定結束']),
                '排定分鐘': row['排定分鐘']}
        j = matched.get(i)
        if j is None:
            if not row['排定居服員']:
                status, sev, items, advice = STATUS_UNASSIGNED, '', '', ''
            elif (as_of is None or row['日期'] is None or row['日期'] > as_of
                  or (row['排定結束'] is not None and row['排定結束'] > now)):
                status, sev, items, advice = STATUS_NOT_YET, '', '', ''
            else:
                status, sev, items, advice = _finish(['無紀錄'])
            rows.append(dict(base, 狀態=status, 嚴重度=sev, 異常項目=items, 建議處理=advice))
            continue
        rec = recs.loc[j]
        flags, info = _flags_for(row, rec, config)
        if '缺簽退' in flags and row['排定結束'] is not None and row['排定結束'] > now:
            flags.remove('缺簽退')        # 服務還沒結束
        status, sev, items, advice = _finish(flags)
        rows.append(dict(base, **info, 狀態=status, 嚴重度=sev, 異常項目=items, 建議處理=advice,
                         實際居服員=rec['居服員ID'] or row['排定居服員'],
                         簽到=_clock(rec['簽到']), 簽退=_clock(rec['簽退']),
                         服務結果=rec['服務結果'], 補卡='是' if rec['補卡'] else '', 紀錄備註=rec['備註']))

    used = set(matched.values())
    for j, rec in recs.iterrows():
        if j in used and j not in duplicate:
            continue
        flag = '重複紀錄' if j in duplicate else '排外服務'
        start, end = rec['簽到'], rec['簽退']
        actual = round((end - start).total_seconds() / 60) if start and end else None
        status, sev, items, advice = _finish([flag])
        rows.append({'狀態': status, '嚴重度': sev, '異常項目': items, '建議處理': advice,
                     '日期': rec['日期'].isoformat() if rec['日期'] else '', '案家ID': rec['案家ID'],
                     '任務ID': rec['任務ID'], '實際居服員': rec['居服員ID'],
                     '簽到': _clock(start), '簽退': _clock(end), '實際分鐘': actual,
                     '服務結果': rec['服務結果'], '補卡': '是' if rec['補卡'] else '', '紀錄備註': rec['備註']})

    out = pd.DataFrame(rows).reindex(columns=OUTPUT_COLUMNS)
    text_cols = [c for c in OUTPUT_COLUMNS if c not in ('排定分鐘', '實際分鐘', '遲到(分)', '早退(分)', '距案家(公尺)')]
    out[text_cols] = out[text_cols].fillna('')
    sev_rank = {'高': 0, '中': 1, '低': 2, '': 3}
    out['_s'] = out['嚴重度'].fillna('').map(sev_rank).fillna(3)
    out = out.sort_values(['_s', '日期', '排定開始'], na_position='last', kind='stable').drop(columns='_s')
    for col in ['排定分鐘', '實際分鐘', '遲到(分)', '早退(分)', '距案家(公尺)']:
        out[col] = pd.to_numeric(out[col], errors='coerce').astype('Int64')
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------
def summary(result):
    """整體數字：可對照筆數、正常、各類異常、準時率。"""
    comparable = result[~result['狀態'].isin([STATUS_NOT_YET, STATUS_UNASSIGNED, '排外服務', '重複紀錄'])]
    items = result['異常項目'].fillna('')
    count = lambda flag: int(items.str.split('、').apply(lambda xs: flag in xs).sum())
    with_record = comparable[comparable['狀態'] != '無紀錄']
    timed = with_record[(with_record['簽到'].fillna('') != '') & (with_record['狀態'] != '未遇')
                        & (with_record['狀態'] != '案家取消')]
    on_time = int((~timed['異常項目'].fillna('').str.contains('遲到')).sum())
    return {
        '應對照': int(len(comparable)),
        '正常': int((comparable['狀態'] == STATUS_OK).sum()),
        '無紀錄': count('無紀錄'),
        '未遇': count('未遇'),
        '遲到': count('遲到'),
        '早退': count('早退'),
        '時數不足': count('時數不足'),
        '缺卡': count('缺簽到') + count('缺簽退'),
        '補卡待審': count('補卡待審'),
        '換人服務': count('換人服務'),
        '地點異常': count('地點異常'),
        '排外服務': count('排外服務'),
        '準時率': round(on_time / len(timed) * 100, 1) if len(timed) else None,
        '尚無紀錄': int((result['狀態'] == STATUS_NOT_YET).sum()),
    }


def by_caregiver(result):
    """每位居服員的異常次數（依排定居服員；排外服務依實際居服員）。"""
    frame = result.copy()
    frame['居服員ID'] = frame['排定居服員'].fillna('').where(frame['排定居服員'].fillna('') != '', frame['實際居服員'])
    frame = frame[frame['居服員ID'].fillna('') != '']
    frame = frame[~frame['狀態'].isin([STATUS_NOT_YET, STATUS_UNASSIGNED])]
    if frame.empty:
        return pd.DataFrame(columns=['居服員ID', '服務數', '正常', '遲到', '早退', '無紀錄', '未遇', '缺卡', '其他異常'])
    items = frame['異常項目'].fillna('').str.split('、')
    has = lambda flags: items.apply(lambda xs: any(f in xs for f in flags)).astype(int)
    frame = frame.assign(服務數=1, 正常=(frame['狀態'] == STATUS_OK).astype(int), 遲到=has(['遲到']), 早退=has(['早退']),
                         無紀錄=has(['無紀錄']), 未遇=has(['未遇']), 缺卡=has(['缺簽到', '缺簽退']),
                         其他異常=has(['時數不足', '補卡待審', '換人服務', '地點異常', '排外服務', '重複紀錄']))
    cols = ['服務數', '正常', '遲到', '早退', '無紀錄', '未遇', '缺卡', '其他異常']
    out = frame.groupby('居服員ID')[cols].sum().reset_index()
    return out.sort_values(['無紀錄', '遲到', '服務數'], ascending=[False, False, False]).reset_index(drop=True)


def template_frame():
    """匯入範本：一列一次服務。也接受一列一次打卡（打卡類型＋打卡時間）的格式。"""
    return pd.DataFrame([
        {'日期': '2026-11-02', '居服員ID': 'E001', '案家ID': 'C001', '任務ID': '', '簽到時間': '08:03', '簽退時間': '09:58',
         '服務結果': '完成', '補卡': '', '緯度': 25.0123, '經度': 121.4567, '備註': ''},
        {'日期': '2026-11-02', '居服員ID': 'E002', '案家ID': 'C014', '任務ID': '', '簽到時間': '14:00', '簽退時間': '',
         '服務結果': '未遇', '補卡': '', '緯度': '', '經度': '', '備註': '按門鈴無人回應，已聯絡家屬'},
        {'日期': '2026-11-02', '居服員ID': 'E003', '案家ID': 'C020', '任務ID': '', '簽到時間': '10:20', '簽退時間': '11:30',
         '服務結果': '完成', '補卡': '是', '緯度': '', '經度': '', '備註': '手機沒電，事後補卡'},
    ])
