"""Parse the "請問貴單位有人力嗎？" LINE dispatch request into structured data.

Pure Python (no Streamlit), so it can be unit-tested.  Works whether the sender kept
line breaks or the text was flattened into one line.  Only the facts needed to find
a caregiver are extracted; free-text 概況簡述 is never interpreted as a hard rule.
"""
CAREFLOW_BUILD = '2026.10.11-v18'

import re
import unicodedata
from datetime import date

import pandas as pd

LABELS = [
    ('name', r'姓名'),
    ('gender_age', r'性別\s*/\s*年齡|性別|年齡'),
    ('address', r'服務地址|地址'),
    ('level', r'失能等級|失能'),
    ('dementia', r'失智'),
    ('disability', r'身障'),
    ('body', r'身高\s*/\s*體重|身高|體重'),
    ('services', r'服務時間及項目|服務時間|服務項目'),
    ('summary', r'概況簡述|概況'),
    ('note', r'備註'),
]
_LABEL_RE = re.compile('(' + '|'.join(f'(?P<{k}>{v})' for k, v in LABELS) + r')\s*[:：]')

_YES = re.compile(r'[Vv✓✔√是有]')
_NO = re.compile(r'[XxＸ×✗✘否無]')

_SLOT_RE = re.compile(
    r'(?:(?P<year>\d{4})\s*[/\-.年]\s*)?'
    r'(?P<month>\d{1,2})\s*[/\-.月]\s*(?P<day>\d{1,2})\s*日?\s*'
    r'(?:[\(（]\s*(?P<wd>[一二三四五六日天])\s*[\)）])?\s*'
    r'(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*[~\-–—至到]\s*(?P<eh>\d{1,2}):(?P<em>\d{2})'
)
_CODE_RE = re.compile(r'(?<![A-Za-z0-9\-])([A-Za-z]{2,3}\d{1,4}(?:-\d+)?)\s*(?:[×xX*]\s*(\d+(?:\.\d+)?))?')
_WEEKDAY = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
KNOWN_ALIASES = {'BA133': 'BA13'}      # 已向處長確認的誤植


def normalize_text(raw: str) -> str:
    s = unicodedata.normalize('NFKC', str(raw or ''))
    s = s.replace('\\~', '~').replace('～', '~').replace('﹣', '-').replace('－', '-').replace('—', '-').replace('–', '-')
    return s.replace('\r\n', '\n').replace('\r', '\n')


def split_fields(text: str) -> dict[str, str]:
    """依「標籤：」切欄位。換行與否都可以；同一標籤只取第一次出現的位置。"""
    hits = []
    seen = set()
    for m in _LABEL_RE.finditer(text):
        key = m.lastgroup
        # lastgroup 是最後一個命中的具名群組；上面每個標籤各自一組
        for k, _ in LABELS:
            if m.group(k):
                key = k
                break
        if key in seen:
            continue
        seen.add(key)
        hits.append((key, m.start(), m.end()))
    fields = {}
    for i, (key, _, end) in enumerate(hits):
        stop = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        fields[key] = text[end:stop].strip()
    return fields


def _flag(value: str):
    v = (value or '').strip()
    if not v:
        return None
    if _YES.search(v) and not _NO.fullmatch(v):
        return True
    if _NO.search(v):
        return False
    return None


def _norm_code(raw: str, valid: set[str]):
    code = raw.upper()
    if code in valid:
        return code, ''
    if code in KNOWN_ALIASES and KNOWN_ALIASES[code] in valid:
        return KNOWN_ALIASES[code], f'已依確認規則將 {raw} 修正為 {KNOWN_ALIASES[code]}。'
    if len(code) > 3 and code[:-1] in valid and code[-1].isdigit():
        return code[:-1], f'{raw} 不是服務碼，疑似 {code[:-1]} 後多打一碼，已先帶入 {code[:-1]}，請確認。'
    return None, f'無法辨識服務碼 {raw}，請人工確認。'


def parse_service_items(text: str, valid_codes) -> tuple[list[tuple[str, float]], list[str], str]:
    """解析 BA14+BA13、BA13×2、GA09x1。回傳 (items, warnings, 其餘文字)。"""
    valid = {str(c).strip().upper() for c in valid_codes}
    items: list[tuple[str, float]] = []
    warnings: list[str] = []
    for m in _CODE_RE.finditer(text or ''):
        code, warn = _norm_code(m.group(1), valid)
        if warn:
            warnings.append(warn)
        if code is None:
            continue
        units = float(m.group(2)) if m.group(2) else 1.0
        if units <= 0:
            warnings.append(f'{code} 的 Units 必須大於 0，請人工確認。')
            continue
        idx = next((i for i, (c, _) in enumerate(items) if c == code), None)
        if idx is None:
            items.append((code, units))
        else:
            items[idx] = (code, items[idx][1] + units)
    rest = _CODE_RE.sub(' ', text or '')
    rest = re.sub(r'^[\s+、,，;；/]+|[\s+、,，;；/]+$', '', rest)
    rest = re.sub(r'\s*\+\s*', ' ', rest).strip()
    return items, warnings, rest


def _resolve_date(year, month, day, weekday, default_year, warnings):
    years = [year] if year else [default_year, default_year + 1, default_year - 1]
    first_valid = None
    for y in years:
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        first_valid = first_valid or d
        if weekday is None or d.weekday() == weekday:
            if y != years[0]:
                warnings.append(f'{month}/{day} 依括號星期推定為 {y} 年。')
            return pd.Timestamp(d)
    if first_valid is None:
        warnings.append(f'無法解析日期：{month}/{day}')
        return None
    if weekday is not None:
        warnings.append(f'{month}/{day} 的括號星期與 {first_valid.year} 年不符，請核對日期。')
    return pd.Timestamp(first_valid)


def parse_slots(segment: str, valid_codes, default_year: int):
    warnings: list[str] = []
    heads = list(_SLOT_RE.finditer(segment))
    slots = []
    for i, m in enumerate(heads):
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(segment)
        tail = segment[m.end():stop]
        weekday = _WEEKDAY.get(m.group('wd')) if m.group('wd') else None
        d = _resolve_date(int(m.group('year')) if m.group('year') else None, int(m.group('month')), int(m.group('day')),
                          weekday, default_year, warnings)
        if d is None:
            continue
        start = f"{int(m.group('sh')):02d}:{m.group('sm')}"
        end = f"{int(m.group('eh')):02d}:{m.group('em')}"
        if end <= start:
            warnings.append(f'{d:%m/%d} {start}-{end} 結束時間不晚於開始時間，請核對。')
        items, item_warn, rest = parse_service_items(tail, valid_codes)
        warnings += item_warn
        label = f'{d:%m/%d} {start}-{end}'
        if not items:
            warnings.append(f'{label} 未抓到有效服務碼，請人工確認。')
        if len(items) > 2:
            warnings.append(f'{label} 抓到 {len(items)} 個服務碼；單筆任務目前最多帶入 2 個，其餘請另外拆開。')
        first = items[0] if items else (None, 0.0)
        second = items[1] if len(items) > 1 else (None, 0.0)
        slots.append({
            '序號': len(slots) + 1, '日期': d, '開始時間': start, '結束時間': end,
            'Service_Code_1': first[0], 'Units_1': first[1], 'Service_Code_2': second[0], 'Units_2': second[1],
            '原始服務字串': tail.strip(), '時段備註': rest,
        })
    return slots, warnings


def parse_notes(note: str, whole: str) -> dict:
    """備註只做保守辨識；找不到就當沒有，不從病況描述推論硬性條件。"""
    haystack = note or ''
    gender = '男女不拘'
    if re.search(r'女性?\s*居服員', whole):
        gender = '女'
    elif re.search(r'男性?\s*居服員', whole):
        gender = '男'
    flags = []
    if gender != '男女不拘':
        flags.append(f'{gender}性居服員')
    if re.search(r'可拆', haystack):
        flags.append('可拆單位')
    if re.search(r'無須|不需|免|不用', haystack) and '沐浴' in haystack:
        flags.append('無須協助沐浴')
    elif '沐浴' in haystack:
        flags.append('含沐浴')
    if re.search(r'就醫|看診|領藥|陪同|外出', haystack + whole):
        flags.append('外出／就醫陪同')
    if '敬老' in haystack:
        flags.append('敬老優先權')
    return {'caregiver_gender': gender, 'flags': list(dict.fromkeys(flags)), 'splittable': '可拆單位' in flags,
            'no_bath': '無須協助沐浴' in flags, 'escort': '外出／就醫陪同' in flags}


def parse_dispatch_message(raw_text: str, valid_codes, default_year: int) -> dict:
    text = normalize_text(raw_text)
    fields = split_fields(text)
    warnings: list[str] = []

    gender_age = fields.get('gender_age', '')
    gm = re.search(r'([男女])', gender_age)
    am = re.search(r'(\d{1,3})\s*歲?', gender_age)
    body = re.search(r'(\d{2,3})\s*/\s*(\d{2,3})', fields.get('body', ''))
    level_m = re.search(r'\d', fields.get('level', ''))
    address = fields.get('address', '').split('\n')[0].strip()
    if not address:
        warnings.append('沒有抓到「地址：」，請在下方手動填寫。')

    slots, slot_warn = parse_slots((fields.get('services') or text), valid_codes, int(default_year))
    warnings += slot_warn
    notes = parse_notes(fields.get('note', ''), text)
    dementia = _flag(fields.get('dementia', ''))
    disability = _flag(fields.get('disability', ''))
    return {
        'name_masked': fields.get('name', '').split('\n')[0].strip(),
        'client_gender': gm.group(1) if gm else '',
        'age': int(am.group(1)) if am else None,
        'address': address,
        'disability_level': int(level_m.group(0)) if level_m else None,
        'dementia': bool(dementia),
        'disabled': bool(disability),
        'height_cm': int(body.group(1)) if body else None,
        'weight_kg': int(body.group(2)) if body else None,
        'summary': fields.get('summary', '').strip(),
        'note': fields.get('note', '').strip(),
        'special_need': '失智引導與精神陪伴' if dementia else '一般照護',
        'tasks': slots,
        'warnings': list(dict.fromkeys(warnings)),
        **notes,
    }


def redact_message(raw_text: str) -> str:
    """寫入稽覈紀錄用：姓名只留姓、門牌號改 O、電話與身分證遮蔽。"""
    s = normalize_text(raw_text)
    s = re.sub(r'(姓名\s*[:：]\s*)([^\s\n])\S*', lambda m: m.group(1) + m.group(2) + 'O', s)
    s = re.sub(r'(\d+(?:之\d+)?)(\s*號)', 'O號', s)
    s = re.sub(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', '09**-***-***', s)
    s = re.sub(r'\b0\d{1,2}[-\s]?\d{6,8}\b', '市話已遮蔽', s)
    s = re.sub(r'\b[A-Z][12]\d{8}\b', '身分證已遮蔽', s)
    return s
