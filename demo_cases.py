"""示範案例：從「目前的班表」自動找出四種臨時異動各一個一定找得到代班人選的情境。

只用畫面同一套引擎函式試算（find_insertion_candidates／rank_candidates_by_availability），
不改動班表；找到後由 app.py 把欄位預先填好，居督按原本的按鈕即可操作。
優先挑「第一順位人選是從前一案過來」的情境，方便確認交通是看前一案而不是住家。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import time
from dataclasses import replace

import pandas as pd

import caregiver_engine as engine

EMPTY = ('', '未指派', 'None', 'nan', 'NaT')
WEEK = '一二三四五六日'


def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _hhmm(value):
    return _text(value)[:5]


def _minutes(text):
    return int(text[:2]) * 60 + int(text[3:5])


def _clock(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _day(value):
    stamp = pd.to_datetime(value, errors='coerce')
    return '' if pd.isna(stamp) else stamp.strftime('%Y-%m-%d')


class Context:
    """目前班表的快照（呼叫端傳入，皆為副本）。"""

    def __init__(self, tasks, assignments, caregivers, clients, config):
        self.tasks = tasks.copy()
        self.merged = engine.merge_task_client_tables(self.tasks, clients)
        valid = assignments[['任務ID', '派單居服員']].copy() if not assignments.empty else pd.DataFrame(columns=['任務ID', '派單居服員'])
        valid = valid.loc[~valid['派單居服員'].astype(str).isin(EMPTY)].drop_duplicates('任務ID', keep='last')
        self.assigned = valid.loc[valid['任務ID'].isin(set(self.tasks['任務ID']))].copy()
        self.caregivers = caregivers.copy()
        self.clients = clients
        self.config = config
        info = self.merged.drop_duplicates('任務ID', keep='last').set_index('任務ID')
        frame = self.assigned.copy()
        frame['日期'] = frame['任務ID'].map(info['日期']).map(_day)
        frame['開始'] = frame['任務ID'].map(info['時間窗_開始']).map(_hhmm)
        frame['結束'] = frame['任務ID'].map(info['時間窗_結束']).map(_hhmm)
        frame['案家ID'] = frame['任務ID'].map(info['案家ID']).astype(str)
        frame['緯度'] = frame['任務ID'].map(info['服務地點_緯度'])
        frame['經度'] = frame['任務ID'].map(info['服務地點_經度'])
        self.routes = frame.loc[frame['日期'].ne('') & frame['開始'].str.len().eq(5) & frame['結束'].str.len().eq(5)]
        self.motor = {str(r['居服員ID']) for r in caregivers.to_dict('records') if _text(r.get('常用交通工具')) != '大眾運輸'}

    def cfg(self):
        """每次試算用獨立的設定副本，不影響畫面上的 config。"""
        return replace(self.config, primary_profiles=dict(getattr(self.config, 'primary_profiles', {}) or {}),
                       client_primary_map=dict(self.config.client_primary_map),
                       client_backup_map=dict(getattr(self.config, 'client_backup_map', {}) or {}),
                       task_support_map=dict(getattr(self.config, 'task_support_map', {}) or {}),
                       work_day_minutes=dict(getattr(self.config, 'work_day_minutes', {}) or {}), dispatch_cache={})

    def worker_days(self, size=(2, 4)):
        """（居服員, 日期, 當天班表）依日期先後；只取機車居服員（不必查 Google，試算快）。"""
        out = []
        for (worker, day), frame in self.routes.groupby(['派單居服員', '日期'], sort=True):
            if str(worker) in self.motor and size[0] <= len(frame) <= size[1]:
                out.append((str(worker), day, frame.sort_values('開始')))
        return out


def _top(frame):
    ok = frame.loc[frame['可直接插入'] == True] if frame is not None and not frame.empty else pd.DataFrame()  # noqa: E712
    return ok.iloc[0].to_dict() if len(ok) else None


def _score(reason, n_ok):
    """前一案過來的情境優先，其次可選人數多。"""
    return (2 if '前一案過來' in (reason or '') else 1 if '同案家接續' in (reason or '') else 0, n_ok)


# ---------------- ① 居服員臨時請假 ----------------

def find_leave(ctx, budget=25.0):
    started, best = time.time(), None
    for worker, day, frame in ctx.worker_days((1, 3)):
        if time.time() - started > budget:
            break
        caregivers = ctx.caregivers.copy()
        mask = caregivers['居服員ID'].astype(str).eq(worker)
        old = caregivers.loc[mask, '請假日期'].astype(str).iloc[0] if '請假日期' in caregivers else ''
        caregivers.loc[mask, '請假日期'] = ','.join(x for x in [old if old not in EMPTY else '', day] if x)
        cfg = ctx.cfg()
        effective = ctx.assigned.loc[~ctx.assigned['任務ID'].isin(set(frame['任務ID']))].copy()
        others = [i for i in caregivers['居服員ID'].astype(str) if i != worker]
        picks = []
        for tid in frame['任務ID']:
            try:
                ranked = engine.rank_candidates_by_availability(tid, others, ctx.tasks, effective, caregivers, cfg,
                                                               date_column='日期', df_cl=ctx.clients)
            except Exception:
                ranked = []
            ok = [r for r in ranked if r.get('available')]
            if not ok:
                break
            pick = ok[0]
            picks.append({'任務ID': tid, '代班': str(pick['cg_id']), '理由': pick.get('rank_reason', ''), '可選人數': len(ok)})
            effective = pd.concat([effective, pd.DataFrame([{'任務ID': tid, '派單居服員': pick['cg_id']}])], ignore_index=True)
        if len(picks) != len(frame):
            continue
        # 每筆都至少有兩位可選（才看得出「推薦＋可改選」），其次第一順位從前一案過來。
        score = (all(p['可選人數'] >= 2 for p in picks), sum('前一案過來' in p['理由'] for p in picks), len(picks))
        if best is None or score > best['score']:
            best = {'kind': 'leave', 'worker': worker, 'day': day, 'tasks': list(frame['任務ID']), 'picks': picks, 'score': score}
        if score[0] and score[1] >= 1:
            break
    return best


# ---------------- ② 案家臨時改期 ----------------

def find_reschedule(ctx, budget=25.0):
    started, best = time.time(), None
    for worker, day, frame in ctx.worker_days((2, 4)):
        if time.time() - started > budget:
            break
        rows = frame.to_dict('records')
        # 把當天最後一案往後移，讓它接在前一案之後：第一順位人選就是「從前一案過來」。
        for i in range(1, len(rows)):
            target, prev = rows[i], rows[i - 1]
            length = _minutes(target['結束']) - _minutes(target['開始'])
            later = [r for r in rows[i + 1:]]
            for shift in (60, 30, 90, 120):
                start = _minutes(target['開始']) + shift
                end = start + length
                if end > _minutes('20:30') or any(_minutes(r['開始']) < end + 30 for r in later):
                    continue
                changed = ctx.merged.copy()
                hit = changed['任務ID'] == target['任務ID']
                changed.loc[hit, '時間窗_開始'] = _clock(start)
                changed.loc[hit, '時間窗_結束'] = _clock(end)
                base = ctx.assigned.loc[ctx.assigned['任務ID'] != target['任務ID']]
                try:
                    cand = engine.find_insertion_candidates(changed.loc[hit].iloc[0], changed, base, ctx.caregivers, ctx.cfg(),
                                                            top_n=5, date_column='日期')
                except Exception:
                    continue
                top = _top(cand)
                if top is None:
                    continue
                n_ok = int((cand['可直接插入'] == True).sum())  # noqa: E712
                score = _score(top.get('代班排序理由'), n_ok)
                if best is None or score > best['score']:
                    best = {'kind': 'reschedule', 'task': target['任務ID'], 'client': target['案家ID'], 'day': day,
                            'old': f"{target['開始']}-{target['結束']}", 'new_start': _clock(start), 'new_end': _clock(end),
                            'original': worker, 'top': str(top['居服員ID']), 'reason': top.get('代班排序理由', ''),
                            'n': n_ok, 'score': score}
                break
            if best and best['score'][0] == 2:
                return best
    return best


# ---------------- ③ 前一服務延遲 ----------------

def find_delay(ctx, budget=30.0):
    started, best = time.time(), None
    for worker, day, frame in ctx.worker_days((2, 4)):
        if time.time() - started > budget:
            break
        rows = frame.to_dict('records')
        for i in range(len(rows) - 1):
            first, second = rows[i], rows[i + 1]
            gap = _minutes(second['開始']) - _minutes(first['結束'])
            if not 15 <= gap <= 90 or first['案家ID'] == second['案家ID']:
                continue
            delay = min(120, ((gap + 15) // 15 + 1) * 15)   # 延遲到原本的空檔不夠用
            cfg = ctx.cfg()
            owner = cfg.client_primary_map.get(second['案家ID']) or worker
            profile = dict(cfg.primary_profiles.get(owner) or {})
            import json
            blocks = []
            raw = profile.get('臨時不可排時段') or '[]'
            try:
                blocks = json.loads(raw) if isinstance(raw, str) else list(raw)
            except ValueError:
                blocks = []
            blocks.append({'start': f"{day}T{second['開始']}:00", 'end': f"{day}T{second['結束']}:00", 'reason': '請假（前一服務延遲）'})
            profile['臨時不可排時段'] = json.dumps(blocks, ensure_ascii=False)
            cfg.primary_profiles[owner] = profile
            base = ctx.assigned.loc[ctx.assigned['任務ID'] != second['任務ID']]
            candidates = ctx.caregivers.loc[ctx.caregivers['居服員ID'].astype(str) != worker]
            row = ctx.merged.loc[ctx.merged['任務ID'] == second['任務ID']].iloc[0]
            try:
                cand = engine.find_insertion_candidates(row, ctx.merged, base, candidates, cfg, top_n=5, date_column='日期')
            except Exception:
                continue
            top = _top(cand)
            if top is None:
                continue
            n_ok = int((cand['可直接插入'] == True).sum())  # noqa: E712
            score = _score(top.get('代班排序理由'), n_ok)
            if best is None or score > best['score']:
                best = {'kind': 'delay', 'worker': worker, 'day': day, 'task': first['任務ID'], 'client': first['案家ID'],
                        'end': first['結束'], 'delay': int(delay), 'affected': second['任務ID'], 'top': str(top['居服員ID']),
                        'reason': top.get('代班排序理由', ''), 'n': n_ok, 'score': score}
            if best['score'][0] == 2:
                return best
    return best


# ---------------- ④ 臨時新增案件 ----------------

def common_service_code(ctx):
    """機構最常用、且在 Service_code 主檔內的服務碼（優先非自費 SA 碼）。"""
    master = getattr(ctx, 'service_master', None)
    known = set() if master is None or '系統代碼' not in master else {str(c).strip().upper() for c in master['系統代碼'].dropna()}
    codes = ctx.tasks.get('Service_Code_1', pd.Series(dtype=object)).dropna().astype(str).str.strip()
    codes = codes.loc[codes.ne('') & (codes.str.upper().isin(known) if known else True)]
    preferred = codes.loc[~codes.str.upper().str.startswith('SA')]
    pool = preferred if len(preferred) else codes
    if len(pool):
        return pool.mode().iloc[0]
    return sorted(known)[0] if known else 'BA07'


LINE_EXAMPLES = {
    1: ("請問貴單位有人力嗎？\n姓名：劉O\n性別/年齡：女性82歲\n地址：新北市中和區大智街106巷O號\n失能等級：3\n失智：X\n身障：X\n"
        "身高/體重：155/62\n服務時間及項目：\n{slots}\n"
        "概況簡述：個案意識清楚、對談無礙，能以國、台語溝通。個案三餐可自行簡單準備，或由案家協助購買外食，並可自行使用電鍋加熱食物；"
        "可自行使用筷子進食，飯量約一碗，偶爾補充奶粉(益力壯)及其他營養品。個案無法執行備餐及家務工作，用藥可自理，未見嗆咳情形。"
        "個案起身、盥洗、穿脫衣物、如廁及沐浴皆可緩慢自理；偶有小便失禁或漏尿情形，可自行清潔與更換，外出時會使用護墊預防，沐浴採坐姿進行。"
        "個案於家內行走不需使用輔具，惟需攙扶牆面或大型家具以維持平衡，可上下樓梯，外出時則持雨傘拐輔助行走，平時喜歡獨自至附近市場買菜。"
        "就醫時需由他人陪同，以維護外出安全。\n"
        "備註：\n1.女性居服員，須注意個案外出就醫安全\n2.個案有敬老優先權，看診及領藥皆可優先安排"),
    2: ("請問貴單位有人力嗎？\n姓名：樓O志\n性別/年齡：男99歲\n地址：新北市中和區景福里和平街O號\n失能等級：5\n失智：V\n身障：V\n"
        "身高/體重：161/60\n服務時間及項目：\n{slots}\n"
        "概況簡述：個案意識清楚，雙耳重聽，溝通時需靠近並提高音量，對簡單問答可予以簡短回應，主要以國語溝通。個案三餐由外籍看護協助準備，"
        "飲食以軟質食物為主，可自行進食；用藥需由他人協助給藥，進食及飲水時偶有嗆咳情形，需持續留意吞嚥安全。目前個案已無法自行備餐及處理家務，"
        "相關生活事務皆需仰賴他人協助。個案如廁及穿脫衣物需他人部分協助，偶有大小便失禁情形；起身、盥洗及沐浴則需由他人全程協助，"
        "以維持個人清潔及日常生活安全。個案因心臟附近罹患帶狀皰疹，目前服用止痛藥物，案家表示受藥物影響，近期下肢較為無力。"
        "家中行走時需一手持單拐，另一手由他人攙扶，步態較不穩；上下樓梯需他人大量攙扶協助，具跌倒風險。外出則需使用輪椅，"
        "並由他人全程陪同及協助，以維護移動及外出安全。\n"
        "備註：\n1.女性居服員，無須協助沐浴\n2.可拆單位"),
}
EXAMPLE_ADDRESS = {1: '新北市中和區大智街106巷O號', 2: '新北市中和區景福里和平街O號'}


def _slot_line(day, start, end, items):
    stamp = pd.Timestamp(day)
    return f"{stamp.month}/{stamp.day}({WEEK[stamp.weekday()]}){start}~{end} {items}"


def _known_codes(ctx):
    master = getattr(ctx, 'service_master', None)
    return set() if master is None or '系統代碼' not in master else {str(c).strip().upper() for c in master['系統代碼'].dropna()}


def _example_items(ctx, example):
    """範例原本的服務碼；機構主檔沒有時改用機構最常用的服務碼（只換服務碼，其他文字不變）。"""
    known = _known_codes(ctx)
    if example == 1:
        return 'BA14+BA133 聯合醫院昆明院區' if {'BA14', 'BA133'} <= known else f'{common_service_code(ctx)} 聯合醫院昆明院區'
    return 'GA09×1' if 'GA09' in known else f'{common_service_code(ctx)}×1'


def example_message(ctx, example, slots):
    items = _example_items(ctx, example)
    lines = '\n'.join(_slot_line(day, start, end, items) for day, start, end in slots)
    return LINE_EXAMPLES[example].format(slots=lines)


def _female(ctx):
    return {str(r['居服員ID']) for r in ctx.caregivers.to_dict('records') if '女' in _text(r.get('性別'))}


def _prescore(ctx, day, start, end, allowed):
    """不查交通的粗估：當天在新時段前 10～120 分鐘剛結束一案、且前後 30 分鐘沒有其他班的人數。"""
    s, e = _minutes(start), _minutes(end)
    frame = ctx.routes.loc[ctx.routes['日期'].eq(day)]
    score = 0
    for worker, rows in frame.groupby('派單居服員'):
        if str(worker) not in allowed:
            continue
        ends = [_minutes(x) for x in rows['結束']]
        busy = any(_minutes(a) < e + 30 and _minutes(b) > s - 30 for a, b in zip(rows['開始'], rows['結束']))
        if not busy and any(s - 120 <= x <= s - 10 for x in ends):
            score += 1
    return score


def _search_example(ctx, example, slots, geo):
    from dispatch_message import parse_dispatch_message
    from temp_case import build_temp_tasks, slots_to_frame, search_slots, single_caregiver_plan
    message = example_message(ctx, example, slots)
    parsed = parse_dispatch_message(message, sorted(_known_codes(ctx)), pd.Timestamp(slots[0][0]).year)
    case = {'client_id': 'TEMP_CLIENT_DEMO', 'lat': geo['lat'], 'lon': geo['lon'],
            'gender': parsed.get('caregiver_gender') or '男女不拘', 'need': parsed.get('special_need', '一般照護'),
            'heavy': False, 'env': '一般居家環境', 'note': parsed.get('note', ''), 'dementia': bool(parsed.get('dementia'))}
    temp, errors = build_temp_tasks(case, slots_to_frame(parsed['tasks']), ctx.service_master, 'DEMO', 'demo')
    if errors or temp.empty:
        return None
    found = search_slots(temp, ctx.merged, ctx.assigned, ctx.caregivers, ctx.cfg())
    per_slot = [int((f['frame']['可直接插入'] == True).sum()) if not f['frame'].empty else 0 for f in found]  # noqa: E712
    plan = single_caregiver_plan(found)
    tops = [_top(f['frame']) for f in found]
    return {'message': message, 'per_slot': per_slot, 'one_person': 0 if plan is None or plan.empty else len(plan),
            'top': [str(t['居服員ID']) if t else '' for t in tops], 'reason': [t.get('代班排序理由', '') if t else '' for t in tops]}


def find_line_example(ctx, example=1, budget=60.0):
    """用使用者提供的 LINE 派案範例（範例1：單次就醫陪同；範例2：失智、週六日一共三次），只改日期時段，
    挑一個在目前班表裡有多位人選可排、且第一順位從前一案過來的時段。"""
    from address_geocoder import get_geocoder
    geocoder = get_geocoder()
    hit = geocoder.lookup(EXAMPLE_ADDRESS[example]) if geocoder.ready else None
    if hit is None or hit.lat is None:
        return None
    geo = {'lat': float(hit.lat), 'lon': float(hit.lon)}
    period = pd.to_datetime(ctx.tasks['日期'], errors='coerce').dropna()
    days = [d.strftime('%Y-%m-%d') for d in pd.date_range(period.min(), period.max())] if len(period) else []
    allowed = _female(ctx)
    options = []
    if example == 1:
        for day in days:
            if pd.Timestamp(day).weekday() >= 5:
                continue
            for start in ('08:20', '09:00', '10:00', '13:00', '13:30', '14:00', '15:00'):
                end = _clock(_minutes(start) + 180)
                options.append(([(day, start, end)], _prescore(ctx, day, start, end, allowed)))
    else:
        for day in days:
            sat = pd.Timestamp(day)
            if sat.weekday() != 5:
                continue
            sun, mon = (sat + pd.Timedelta(days=1)).strftime('%Y-%m-%d'), (sat + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
            if mon not in days:
                continue
            for s1 in ('10:00', '09:00', '14:00'):
                for s3 in ('16:00', '15:00', '14:00', '10:00'):
                    slots = [(day, s1, _clock(_minutes(s1) + 120)), (sun, s1, _clock(_minutes(s1) + 120)), (mon, s3, _clock(_minutes(s3) + 120))]
                    score = sum(_prescore(ctx, d, a, b, allowed) for d, a, b in slots)
                    options.append((slots, score))
    options.sort(key=lambda item: (-item[1], item[0][0][0], item[0][0][1]))
    started, best = time.time(), None
    for slots, _ in options[:8]:
        if time.time() - started > budget and best is not None:
            break
        try:
            result = _search_example(ctx, example, slots, geo)
        except Exception:
            result = None
        if not result or min(result['per_slot'] or [0]) == 0:
            continue
        score = (min(result['per_slot']) >= 2, result['one_person'] > 0, sum('前一案過來' in r for r in result['reason']),
                 sum(result['per_slot']))
        if best is None or score > best['score']:
            best = {'kind': f'new{example}', 'example': example, 'slots': slots, **result, 'score': score}
        if score[0] and score[2] >= 1 and (example == 1 or score[1]):
            break
    return best


def find_new_case(ctx, budget=30.0):
    """挑一位當天有一案剛結束、之後有空檔的居服員，把新案放在那一案附近、結束後 30 分開始。"""
    from temp_case import build_temp_tasks
    started, best = time.time(), None
    master = getattr(ctx, 'service_master', None)
    code = common_service_code(ctx)
    for worker, day, frame in ctx.worker_days((1, 3)):
        if time.time() - started > budget:
            break
        rows = frame.to_dict('records')
        for i, prev in enumerate(rows):
            start = ((_minutes(prev['結束']) + 30 + 14) // 15) * 15
            end = start + 120
            later = rows[i + 1:]
            if end > _minutes('20:00') or any(_minutes(r['開始']) < end + 30 for r in later):
                continue
            try:
                lat, lon = float(prev['緯度']) + 0.0015, float(prev['經度']) + 0.0015   # 前一案附近約 200 公尺
            except (TypeError, ValueError):
                continue
            case = {'client_id': 'TEMP_DEMO', 'lat': lat, 'lon': lon, 'gender': '男女不拘', 'need': '一般照護',
                    'env': '一般居家環境', 'note': '示範案例', 'dementia': False, 'heavy': False}
            slot = pd.DataFrame([{'納入': True, '日期': pd.Timestamp(day).date(), '開始': _clock(start), '結束': _clock(end),
                                  '服務碼1': code, 'Units1': 1.0, '服務碼2': '', 'Units2': 0.0, '時段備註': ''}])
            try:
                temp, errors = build_temp_tasks(case, slot, master, 'DEMO', 'demo')
                if errors or temp.empty:
                    continue
                cand = engine.find_insertion_candidates(temp.iloc[0], ctx.merged, ctx.assigned, ctx.caregivers, ctx.cfg(),
                                                        top_n=5, date_column='日期')
            except Exception:
                continue
            top = _top(cand)
            if top is None:
                continue
            n_ok = int((cand['可直接插入'] == True).sum())  # noqa: E712
            score = _score(top.get('代班排序理由'), n_ok)
            if best is None or score > best['score']:
                best = {'kind': 'new', 'day': day, 'start': _clock(start), 'end': _clock(end), 'lat': lat, 'lon': lon,
                        'code': code, 'near_client': prev['案家ID'], 'near_worker': worker, 'top': str(top['居服員ID']),
                        'reason': top.get('代班排序理由', ''), 'n': n_ok, 'score': score,
                        'message': new_case_message(day, _clock(start), _clock(end), code)}
            if best['score'][0] == 2:
                return best
            break
    return best


# ---------------- ⑤ 個案照顧需求變更（資格重新檢核） ----------------

def find_need_change(ctx, need='dementia', prefer=None, budget=40.0, want_split=False):
    """找一個「由同一位沒有該訓練的居服員服務、且還沒有這項需求」的案家，
    且至少一位有訓練的人能接下全部服務（最好兩位以上，才看得出推薦＋可改選）。
    prefer：優先試的案家（例如示範用的 DEMO0004，足部照護）。
    want_split=True：改找「沒有一位能全包、但依星期分給兩位以上就能全部排進」的案家（示範分給多位）。"""
    import qualification_recheck as qr
    from care_policy import dementia_status
    started, best = time.time(), None
    ok_ids = qr.certified(ctx.caregivers, need)
    if not ok_ids:
        return None
    first = ctx.clients.drop_duplicates('案家ID')
    clients = first.set_index(first['案家ID'].astype(str))
    groups = sorted(ctx.routes.groupby('案家ID'), key=lambda g: (g[0] != prefer, abs(len(g[1]) - 4), g[0]))
    current = dict(zip(ctx.assigned['任務ID'].astype(str), ctx.assigned['派單居服員'].astype(str)))
    items = ctx.merged.drop_duplicates('任務ID').set_index('任務ID')
    for client, frame in groups:
        if time.time() - started > budget:
            break
        if not (2 <= len(frame) <= (16 if want_split or client == prefer else 8)) or client not in clients.index:
            continue
        if need == 'dementia' and dementia_status(clients.loc[client].to_dict()) is True:
            continue
        if need == 'foot' and any('BA08' in _text(items.at[t, '服務項目']) if '服務項目' in items else False
                                  for t in frame['任務ID'] if t in items.index):
            continue
        workers = set(frame['派單居服員'].astype(str))
        if len(workers) != 1 or workers & ok_ids or next(iter(workers)) not in ctx.motor:
            continue
        affected = qr.affected_tasks(client, ctx.tasks, current, ctx.caregivers, need=need)
        if len(affected) != len(frame):
            continue
        ids = [a['任務ID'] for a in affected]
        avail = qr.availability(ids, sorted(ok_ids), ctx.tasks, ctx.assigned, ctx.caregivers, ctx.cfg(), ctx.clients)
        cover = qr.covering(avail, ids)
        split = None
        if want_split:
            if cover:
                continue
            split = qr.split_cover(avail, ids, day_of={a['任務ID']: a['日期'] for a in affected})
            people = set(split['assign'].values())
            if split['missing'] or len(people) < 2:
                continue
            cover = [{'cg': w, 'reason': next(g['理由'] for g in split['groups'] if g['居服員'] == w)} for w in sorted(people)]
        elif not cover:
            continue
        score = ((client == prefer, len(cover) == 2, -len(ids)) if want_split
                 else (client == prefer, len(cover) >= 2, -abs(len(ids) - 4)))
        if best is None or score > best['score']:
            best = {'kind': need, 'need': need, 'client': client, 'worker': next(iter(workers)), 'tasks': ids,
                    'since': affected[0]['日期'], 'top': cover[0]['cg'], 'reason': cover[0]['reason'],
                    'options': len(cover), 'score': score, 'split': split}
        if score[0] or (prefer is None and score[1] and (not want_split or len(ids) <= 13)):
            break
    return best


def find_dementia(ctx, budget=40.0):
    """示範用的 DEMO0005（M04 長期服務、沒有失智症訓練）優先；資料裡沒有時再從班表找。"""
    return find_need_change(ctx, 'dementia', prefer='DEMO0005', budget=budget)
