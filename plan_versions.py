"""週計畫版本（異動單）：「從某天起，這個案家的週服務時段改成這樣」。

跟機構 Excel 現有欄位相容，不新增工作表：
- 停用的舊時段：把「到期日」設成生效日前一天，生效日之前的班次照舊。
- 新增的時段：在「週服務計畫」最後新增一列，「生效日」＝異動生效日。
- 沒有變的時段：原列完全不動（隔週服務的起算週也不變）。

注意：機構資料裡同一案家不同生效日的列，本來就是「後來追加的時段」而不是取代，
所以這裡只用「到期日」切版本，不會拿「最新生效日」自動蓋掉舊列。

本模組只放純計算與 Excel 寫回（可單獨測試）；畫面在 app.py。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO

import pandas as pd

import caregiver_engine as engine
from workbook_edit import Workbook, _column, _nodes

SHEET_NAMES = ('週服務計畫', '周服務計畫', 'Weekly_Service_Plan')
WEEKDAYS = list(engine.WEEKDAY_NAME_TO_NUM)
FREQUENCIES = ('每週', '隔週')
EDIT_COLUMNS = ['星期', '開始', '結束', '頻率', '服務項目']
_EXCEL_EPOCH = date(1899, 12, 30)


def items_text(value):
    """服務項目統一寫法，例如 'BA02(1) BA20(2)'；格式錯誤時丟出 ValueError。"""
    return ' '.join(f'{code}({units:g})' for code, units in engine.parse_service_items(value))


def _frequency(value):
    return engine._text(value).replace('隔周', '隔週')


def _slot_key(weekday, start, end, frequency, items):
    return (weekday, start, end, frequency, items)


def _to_day(value):
    if isinstance(value, date):
        return value
    return engine._date(value)


def weekly_sheet_name(sheet_names):
    found = [name for name in SHEET_NAMES if name in sheet_names]
    if len(found) != 1:
        raise ValueError('請保留一張「週服務計畫」工作表；目前找不到或有多張')
    return found[0]


def read_weekly(workbook_bytes):
    """讀出週服務計畫；「來源列」＝Excel 列號（與排班引擎的任務ID W0123_日期 同一套）。"""
    with pd.ExcelFile(BytesIO(workbook_bytes)) as xl:
        frame = pd.read_excel(xl, sheet_name=weekly_sheet_name(xl.sheet_names), dtype=object)
    frame.columns = [str(c).strip() for c in frame.columns]
    frame['來源列'] = frame.index + 2
    return frame


def client_plan(weekly, client):
    """某案家在週服務計畫的所有列（含已到期、未來生效），欄位整理成統一格式。"""
    rows = []
    if '案號' not in weekly:
        return pd.DataFrame(columns=['來源列'] + EDIT_COLUMNS + ['生效日', '到期日'])
    for _, row in weekly[weekly['案號'].map(engine._text) == str(client)].iterrows():
        try:
            items = items_text(row.get('服務項目'))
        except ValueError:
            items = engine._text(row.get('服務項目'))
        rows.append({'來源列': int(row['來源列']), '星期': engine._text(row.get('星期')),
                     '開始': engine._hhmm(row.get('開始時間')) or engine._text(row.get('開始時間')),
                     '結束': engine._hhmm(row.get('結束時間')) or engine._text(row.get('結束時間')),
                     '頻率': _frequency(row.get('頻率')), '服務項目': items,
                     '生效日': engine._date(row.get('生效日')), '到期日': engine._date(row.get('到期日'))})
    frame = pd.DataFrame(rows, columns=['來源列'] + EDIT_COLUMNS + ['生效日', '到期日'])
    order = {name: i for i, name in enumerate(WEEKDAYS)}
    return frame.assign(_o=frame['星期'].map(order)).sort_values(['_o', '開始', '來源列']).drop(columns='_o').reset_index(drop=True)


def plan_as_of(weekly, client, day):
    """回傳 (當天有效的時段, 之後才生效的時段)。生效日空白或無法解析的列不算有效。"""
    day = _to_day(day)
    plan = client_plan(weekly, client)
    begin_ok = plan['生效日'].map(lambda d: d is not None and d <= day)
    end_ok = plan['到期日'].map(lambda d: d is None or d >= day)
    future = plan['生效日'].map(lambda d: d is not None and d > day)
    return plan[begin_ok & end_ok].reset_index(drop=True), plan[future].reset_index(drop=True)


def validate_slots(edited, known_codes=None):
    """檢查居督填的新版時段；回傳 (時段清單, 錯誤訊息清單)。整列空白的列略過。"""
    slots, errors = [], []
    known = {str(c).strip().upper() for c in known_codes} if known_codes is not None else None
    records = edited.to_dict('records') if isinstance(edited, pd.DataFrame) else list(edited or [])
    for number, row in enumerate(records, start=1):
        values = {col: engine._text(row.get(col)) for col in EDIT_COLUMNS}
        if not any(values.values()):
            continue
        label = f'第 {number} 列'
        weekday, frequency = values['星期'], _frequency(values['頻率']) or '每週'
        start, end = engine._hhmm(values['開始']), engine._hhmm(values['結束'])
        if weekday not in WEEKDAYS:
            errors.append(f'{label}：星期請選 {"、".join(WEEKDAYS)}。'); continue
        if not start or not end:
            errors.append(f'{label}：開始、結束請用 HH:MM（例如 09:00）。'); continue
        if end <= start:
            errors.append(f'{label}：結束時間必須晚於開始時間。'); continue
        if frequency not in FREQUENCIES:
            errors.append(f'{label}：頻率請填「每週」或「隔週」。'); continue
        try:
            codes = engine.parse_service_items(values['服務項目'])
        except ValueError as exc:
            errors.append(f'{label}：{exc}'); continue
        if any(units <= 0 for _, units in codes):
            errors.append(f'{label}：服務次數必須大於零。'); continue
        unknown = sorted({code for code, _ in codes if known is not None and code not in known})
        if unknown:
            errors.append(f'{label}：服務碼 {"、".join(unknown)} 不在 Service_code 主檔。'); continue
        slots.append({'星期': weekday, '開始': start, '結束': end, '頻率': frequency,
                      '服務項目': ' '.join(f'{c}({u:g})' for c, u in codes)})
    for i, first in enumerate(slots):
        for second in slots[i + 1:]:
            if first['星期'] == second['星期'] and first['開始'] < second['結束'] and second['開始'] < first['結束']:
                errors.append(f"{first['星期']} {first['開始']}-{first['結束']} 與 {second['開始']}-{second['結束']} 重疊；"
                              '同一案家同一天的時段不可重疊，請合併成一列或錯開。')
    if not slots and not errors:
        errors.append('新版時段是空的。若案家從生效日起暫停所有服務，請改走結案或暫停流程；這裡至少要保留一個時段。')
    return slots, errors


@dataclass
class PlanChange:
    client: str
    effective: date
    keep: list = field(default_factory=list)    # 原列不動（Excel 列號）
    stop: list = field(default_factory=list)    # 到期日改為生效日前一天
    add: list = field(default_factory=list)     # 新增列的時段內容
    stopped_slots: list = field(default_factory=list)
    future_rows: list = field(default_factory=list)  # 生效日之後才開始的列，維持原狀

    @property
    def expire_on(self):
        return self.effective - timedelta(days=1)

    def summary(self):
        """給人看的異動摘要；同一天同內容的「停用＋新增」合併成一行「改時間」。"""
        def fmt(slot):
            return f"{slot['星期']} {slot['開始']}–{slot['結束']}（{slot['頻率']}）{slot['服務項目']}"
        lines = [f'案家 {self.client}：{self.effective:%Y-%m-%d} 起生效']
        added = list(self.add)
        for old in self.stopped_slots:
            twin = next((i for i, new in enumerate(added) if all(new[c] == old[c] for c in ('星期', '頻率', '服務項目'))), None)
            if twin is None:
                lines.append('停用：' + fmt(old))
            else:
                new = added.pop(twin)
                lines.append(f"改時間：{old['星期']} {old['開始']}–{old['結束']} → {new['開始']}–{new['結束']}"
                             f"（{old['頻率']}）{old['服務項目']}")
        lines += ['新增：' + fmt(s) for s in added]
        if self.keep:
            lines.append(f'不變：{len(self.keep)} 個時段')
        return lines


def build_change(weekly, client, effective, edited, known_codes=None):
    """比對生效日當天的現行時段與新版時段；回傳 (PlanChange 或 None, 錯誤訊息清單)。"""
    effective = _to_day(effective)
    if effective is None:
        return None, ['請選擇生效日。']
    current, future = plan_as_of(weekly, client, effective)
    slots, errors = validate_slots(edited, known_codes)
    if errors:
        return None, errors
    remaining = list(slots)
    change = PlanChange(client=str(client), effective=effective, future_rows=future['來源列'].astype(int).tolist())
    for _, row in current.iterrows():
        key = _slot_key(row['星期'], row['開始'], row['結束'], row['頻率'], row['服務項目'])
        match = next((i for i, s in enumerate(remaining) if _slot_key(*(s[c] for c in EDIT_COLUMNS)) == key), None)
        if match is None:
            change.stop.append(int(row['來源列']))
            change.stopped_slots.append({col: row[col] for col in EDIT_COLUMNS})
        else:
            change.keep.append(int(row['來源列']))
            remaining.pop(match)
    change.add = remaining
    if not change.stop and not change.add:
        return None, ['新版時段和生效日當天的現行時段完全一樣，沒有需要異動的地方。']
    return change, []


# ---------------- Excel 寫回 ----------------

def _cell(book, sheet, row_number, header):
    col = book.headers(sheet).get(header)
    row = book.rows(sheet).get(row_number)
    if col is None or row is None:
        return None
    return next((c for c in _nodes(row, 'c') if _column(c.getAttribute('r')) == col), None)


def _is_number_cell(cell):
    return cell is not None and cell.getAttribute('t') in ('', 'n') and bool(_nodes(cell, 'v'))


def _date_value(template_cell, day):
    """跟範本儲存格同型態：數字日期就寫 Excel 序號，文字就寫 YYYY-MM-DD。"""
    return (day - _EXCEL_EPOCH).days if _is_number_cell(template_cell) else day.isoformat()


def _time_value(template_cell, hhmm):
    hour, minute = map(int, hhmm.split(':'))
    return (hour * 60 + minute) / 1440 if _is_number_cell(template_cell) else f'{hhmm}:00'


def _ensure_header(book, sheet, name):
    headers = book.headers(sheet)
    if name in headers:
        return
    doc = book.doc(sheet)
    header_row = book.rows(sheet)[1]
    cell = book._cell(doc, header_row, max(headers.values()) + 1)
    for child in list(cell.childNodes):
        cell.removeChild(child)
    cell.setAttribute('t', 'inlineStr')
    container, leaf = book._element(doc, 'is'), book._element(doc, 't')
    leaf.appendChild(doc.createTextNode(name))
    container.appendChild(leaf)
    cell.appendChild(container)


def write_change(workbook_bytes, change):
    """把一張異動單寫回 Excel；回傳 (新 Excel bytes, 新增的列號清單)。其他工作表、格式、公式不動。"""
    book = Workbook(workbook_bytes)
    sheet = weekly_sheet_name(book.sheets)
    _ensure_header(book, sheet, '到期日')
    headers = book.headers(sheet)
    template = (change.keep or change.stop or [None])[0]
    if template is None:
        weekly = read_weekly(workbook_bytes)
        rows = client_plan(weekly, change.client)['來源列'].tolist()
        if not rows:
            raise ValueError(f'週服務計畫找不到案家 {change.client}，請先在 Excel 建立這個案家的服務列。')
        template = int(rows[-1])
    for row_number in change.stop:
        begin_cell = _cell(book, sheet, row_number, '生效日')
        style = begin_cell.getAttribute('s') if begin_cell is not None and begin_cell.hasAttribute('s') else None
        book.set_cell(sheet, row_number, '到期日', _date_value(begin_cell, change.expire_on), style_from=style)
        if _is_number_cell(begin_cell) and style:
            # 數字日期要帶日期格式，否則 Excel 與匯入程式都會把它當成一般數字
            _cell(book, sheet, row_number, '到期日').setAttribute('s', style)
    copy = {name: book.get(sheet, template, name) for name in
            ('案號', '費用類型', '經度 (WGS84_Lon)', '緯度 (WGS84_Lat)') if name in headers}
    added = []
    for slot in change.add:
        start, end = slot['開始'], slot['結束']
        minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
        values = {**copy, '星期': slot['星期'], '頻率': slot['頻率'], '服務項目': slot['服務項目'],
                  '開始時間': _time_value(_cell(book, sheet, template, '開始時間'), start),
                  '結束時間': _time_value(_cell(book, sheet, template, '結束時間'), end),
                  '服務時數': round(minutes / 60, 6), '服務分鐘': minutes,
                  '生效日': _date_value(_cell(book, sheet, template, '生效日'), change.effective)}
        added.append(book.append_row(sheet, {k: v for k, v in values.items() if k in headers and v != ''}, template))
    return book.to_bytes(), added


# ---------------- 班表差異 ----------------

def task_delta(old_tasks, new_tasks, client, effective):
    """生效日（含）之後、這個案家的班次差異：回傳 (要移除的舊任務ID, 要新增的新任務 DataFrame)。

    沒變的時段沿用同一個任務ID（同一 Excel 列＋同一天），已排好的居服員保持不動。
    """
    day = _to_day(effective).isoformat()

    def scope(frame):
        if frame is None or frame.empty:
            return frame.iloc[0:0] if frame is not None else pd.DataFrame(columns=['任務ID'])
        dates = pd.to_datetime(frame['日期'], errors='coerce').dt.strftime('%Y-%m-%d')
        return frame[(frame['案家ID'].astype(str) == str(client)) & (dates >= day)]

    old_scope, new_scope = scope(old_tasks), scope(new_tasks)
    old_ids = set(old_scope['任務ID'].astype(str))
    new_ids = set(new_scope['任務ID'].astype(str))
    removed = sorted(old_ids - new_ids)
    added = new_scope[~new_scope['任務ID'].astype(str).isin(old_ids)].copy()
    return removed, added


# ---------------- 選單式畫面用的小工具 ----------------
ACTIONS = ('不變', '改時間', '停用')


def time_options(extra=(), start='06:00', end='22:00', step=15):
    """時間下拉選單：每 15 分鐘一格；現有資料不在格線上的時間（例如 08:50）也會放進去。"""
    h, m = map(int, start.split(':'))
    stop = int(end[:2]) * 60 + int(end[3:])
    values, minute = set(), h * 60 + m
    while minute <= stop:
        values.add(f'{minute // 60:02d}:{minute % 60:02d}')
        minute += step
    values |= {engine._hhmm(v) for v in extra if engine._hhmm(v)}
    return sorted(values)


def shift_time(hhmm, minutes):
    total = int(hhmm[:2]) * 60 + int(hhmm[3:]) + int(minutes)
    total = max(0, min(total, 23 * 60 + 59))
    return f'{total // 60:02d}:{total % 60:02d}'


def compose_edit(current, actions, new_slots):
    """把選單結果組成新版時段表（給 build_change）。

    current：生效日當天的現行時段（plan_as_of 的結果）
    actions：每個現行時段的 {'動作': 不變/改時間/停用, '開始', '結束'}，順序同 current
    new_slots：新增時段清單，每筆 {'星期','開始','結束','頻率','服務項目'}
    """
    rows = []
    for (_, row), act in zip(current.iterrows(), actions):
        kind = act.get('動作', '不變')
        if kind == '停用':
            continue
        slot = {col: row[col] for col in EDIT_COLUMNS}
        if kind == '改時間':
            slot['開始'], slot['結束'] = act.get('開始') or slot['開始'], act.get('結束') or slot['結束']
        rows.append(slot)
    rows += [{col: s.get(col, '') for col in EDIT_COLUMNS} for s in new_slots]
    return pd.DataFrame(rows, columns=EDIT_COLUMNS)


def example_actions(current, kind):
    """「帶入範例」：回傳 (actions, new_slots, 說明)。current 為空時只能示範新增。"""
    actions = [{'動作': '不變', '開始': r['開始'], '結束': r['結束']} for _, r in current.iterrows()]
    new_slots = []
    if current.empty or kind == 'add':
        used = set(current['星期']) if not current.empty else set()
        weekday = next((d for d in WEEKDAYS if d not in used), WEEKDAYS[0])
        base = current.iloc[0] if not current.empty else {'開始': '09:00', '結束': '10:00', '頻率': '每週', '服務項目': 'BA02(1)'}
        new_slots.append({'星期': weekday, '開始': base['開始'], '結束': base['結束'], '頻率': base['頻率'] or '每週',
                          '服務項目': base['服務項目']})
        text = f"範例：新增每週{weekday[-1]} {base['開始']}–{base['結束']}（服務項目沿用現有時段）。"
    elif kind == 'move':
        row = current.iloc[0]
        actions[0] = {'動作': '改時間', '開始': shift_time(row['開始'], 60), '結束': shift_time(row['結束'], 60)}
        text = f"範例：{row['星期']} {row['開始']}–{row['結束']} 改成 {actions[0]['開始']}–{actions[0]['結束']}（往後 1 小時）。"
    else:
        row = current.iloc[-1]
        actions[-1]['動作'] = '停用'
        text = f"範例：從生效日起停掉 {row['星期']} {row['開始']}–{row['結束']} 這個時段。"
    return actions, new_slots, text
