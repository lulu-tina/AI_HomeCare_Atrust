"""CSMS 申報檔與案家自付額試算（每月）。

依據（2026-11 查證）：
- 衛福部《長期照顧給付及支付基準》與 1966 長照專區：照顧及專業服務（B 碼、C 碼）每月給付額度
  第 2～8 級 10,020／15,460／18,580／24,100／28,070／32,090／36,180 元；
  部分負擔比率 第一類（長照低收入戶）0%、第二類（中低收入戶）5%、第三類（一般戶）16%，金額小數無條件捨去；
  超過額度的服務由案家全額自費。
- 申報資料欄位參考衛福部長照支付審核系統的服務紀錄結構（個案、服務單位、照顧服務員、照顧組合代碼、
  服務起訖時間、數量、單價、補助／自費、未遇）。**CSMS 實際匯入範本請以機構拿到的官方檔案為準**：
  欄位名稱與順序集中在下方 CLAIM_COLUMNS，拿到範本後只要改這裡。

限制（試算，不是核銷結果）：
- 依「排定班表」試算；打卡網頁登錄「未遇」的服務會標記且不計費。
- 喘息（G 碼）、短照（SC 碼）有另外的年度額度，這裡只套部分負擔比率，不檢查喘息額度。
- 外籍看護工家庭（額度 30%）、身障補助、縣市加碼等資料表沒有，需人工確認。
- 個資：本系統只有案號與員編；身分證字號欄位留空，匯入前請在機構系統對照補上。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import io
import math

import numpy as np
import pandas as pd

import caregiver_engine as engine

MONTHLY_QUOTA = {2: 10020, 3: 15460, 4: 18580, 5: 24100, 6: 28070, 7: 32090, 8: 36180}
COPAY_RATE = {'第一類': 0.0, '第二類': 0.05, '第三類': 0.16}
RESPITE_PREFIXES = ('GA', 'SC')                                     # 喘息／短照：另有額度，只套比率
SELF_PAY_CODES = ('SA10',)                                          # 機構純自費項目

# 申報檔欄位：拿到 CSMS 官方匯入範本後，照範本改「顯示名稱」與順序即可。
CLAIM_COLUMNS = [
    ('unit_code', '服務單位代碼'), ('client_id', '個案案號'), ('client_pid', '個案身分證號'),
    ('date', '服務日期'), ('start', '服務開始時間'), ('end', '服務結束時間'), ('code', '照顧組合代碼'),
    ('units', '數量'), ('price', '單價'), ('amount', '小計'), ('category', '申報類別'),
    ('worker_id', '照顧服務員員編'), ('worker_pid', '照顧服務員身分證號'), ('missed', '未遇'), ('note', '備註'),
]
SUMMARY_COLUMNS = ['案家ID', '長照等級', '收入別', '部分負擔比率', '每月額度', '照顧及專業服務金額', '額度內金額',
                   '部分負擔', '超額自費', '喘息短照部分負擔', '其他自費', '案家應付合計', '政府補助（申報）', '服務次數', '提醒']


def _level(value):
    try:
        level = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 8 else None


def copay_rate(income):
    """回傳 (比率, 提醒)。"""
    text = engine._text(income)
    for key, rate in COPAY_RATE.items():
        if key in text:
            return rate, ''
    if '中低' in text:
        return 0.05, ''
    if '低收' in text:
        return 0.0, ''
    return 0.16, f'收入別「{text or "空白"}」無法辨識，暫以一般戶 16% 試算'


def _client_info(clients):
    info = {}
    if clients is None or clients.empty:
        return info
    key = next((c for c in ('案家ID', '內部案號', '案號') if c in clients), None)
    for _, row in clients.iterrows():
        cid = engine._text(row.get(key))
        if cid:
            info[cid] = {'level': _level(row.get('核定等級')), 'income': engine._text(row.get('收入別')),
                         'program': engine._text(row.get('長照/身障'))}
    return info


def _codes(row):
    """一筆服務的 (服務碼, 次數)；優先讀 Service_Code_n／Units_n，沒有再解析「服務項目」文字。"""
    items = [(c, u) for c, u in engine.iter_service_codes(row) if c and np.isfinite(u) and u > 0]
    if items:
        return items
    text = engine._text(row.get('服務項目')).replace('、', ' ')
    try:
        return engine.parse_service_items(text) if text else []
    except ValueError:
        return []


def _new_summary(cid, c):
    return {'案家ID': cid, '長照等級': c['level'], '收入別': c['income'], 'rate': copay_rate(c['income'])[0],
            'quota': MONTHLY_QUOTA.get(c['level'] or 0, 0), 'used': 0.0, 'over': 0.0, 'respite': 0.0, 'self': 0.0,
            'visits': 0, 'notes': set(), 'self_only': '自費' in c['program']}


def _flag(s, cid, c, known):
    if not known:
        s['notes'].add('個案資料找不到這個案號')
    elif c['level'] is None:
        s['notes'].add('缺核定等級：額度以 0 試算（全部列自費），請補資料')
    if copay_rate(c['income'])[1]:
        s['notes'].add(copay_rate(c['income'])[1])
    if '身障' in c['program']:
        s['notes'].add('身障補助另依縣市規定，請人工確認')


def build_month(schedule, clients, prices, month, missed=(), sa10_price=350.0, unit_code=''):
    """回傳 (申報明細, 案家自付額試算, 提醒清單)。month 為 'YYYY-MM'；只算已派居服員的服務。"""
    missed = {str(t) for t in missed}
    info = _client_info(clients)
    frame = schedule.copy()
    frame['_day'] = pd.to_datetime(frame['日期'], errors='coerce')
    frame = frame[frame['_day'].dt.strftime('%Y-%m') == month]
    worker_col = next((c for c in ('最終派單居服員', '派單居服員') if c in frame), None)
    client_col = next((c for c in ('案號', '案家ID') if c in frame), None)
    if worker_col is None or client_col is None:
        raise ValueError('班表缺少居服員或案號欄位')
    frame = frame[~frame[worker_col].astype(str).isin(['', '未指派', '待安排', 'nan', 'None'])]
    frame = frame.sort_values(['_day', '開始時間'] if '開始時間' in frame else ['_day'], kind='stable')
    claims, unknown_codes, summary = [], set(), {}
    empty = {'level': None, 'income': '', 'program': ''}
    for _, row in frame.iterrows():
        cid = engine._text(row.get(client_col))
        c = info.get(cid, empty)
        if cid not in summary:
            summary[cid] = _new_summary(cid, c)
            _flag(summary[cid], cid, c, cid in info)
        s = summary[cid]
        tid = engine._text(row.get('任務ID'))
        is_missed = tid in missed
        s['visits'] += 0 if is_missed else 1
        base = {'unit_code': unit_code, 'client_id': cid, 'client_pid': '', 'date': row['_day'].strftime('%Y/%m/%d'),
                'start': engine._text(row.get('開始時間'))[:5], 'end': engine._text(row.get('結束時間'))[:5],
                'worker_id': engine._text(row.get(worker_col)), 'worker_pid': '', 'missed': 'Y' if is_missed else 'N'}
        for code, units in _codes(row):
            code = code.upper()
            price = sa10_price if code in SELF_PAY_CODES else prices.get(code)
            line = {**base, 'code': code, 'units': units, 'price': price}
            if price is None or not np.isfinite(price):
                unknown_codes.add(code)
                claims.append({**line, 'price': None, 'amount': None, 'category': '', 'note': '主檔沒有價格（加計項目或新服務碼），請人工填寫'})
                continue
            amount = round(price * units, 2)
            if is_missed:
                claims.append({**line, 'amount': 0, 'category': '', 'note': '未遇（打卡網頁登錄），不計費'})
            elif code in SELF_PAY_CODES or s['self_only']:
                s['self'] += amount
                claims.append({**line, 'amount': amount, 'category': '2 自費', 'note': ''})
            elif code.startswith(RESPITE_PREFIXES):
                s['respite'] += amount
                claims.append({**line, 'amount': amount, 'category': '1 補助', 'note': '喘息／短照：另有年度額度，請確認剩餘額度'})
            else:                                   # 照顧及專業服務：先用額度，超過的單位改列自費
                room = max(0.0, s['quota'] - s['used'])
                inside = units if amount <= room + 1e-9 else math.floor(room / price)
                for part, category, note in ((inside, '1 補助', ''), (units - inside, '2 自費', '超過當月額度，案家自費')):
                    if part > 0:
                        value = round(price * part, 2)
                        s['used' if category == '1 補助' else 'over'] += value
                        claims.append({**line, 'units': part, 'amount': value, 'category': category, 'note': note})
    rows = []
    for s in summary.values():
        copay, respite_copay = math.floor(s['used'] * s['rate']), math.floor(s['respite'] * s['rate'])
        over, other = round(s['over']), round(s['self'])
        rows.append({'案家ID': s['案家ID'], '長照等級': s['長照等級'], '收入別': s['收入別'] or '（空白）',
                     '部分負擔比率': f"{s['rate']:.0%}", '每月額度': s['quota'],
                     '照顧及專業服務金額': round(s['used'] + s['over']), '額度內金額': round(s['used']),
                     '部分負擔': copay, '超額自費': over, '喘息短照部分負擔': respite_copay, '其他自費': other,
                     '案家應付合計': copay + over + respite_copay + other,
                     '政府補助（申報）': round(s['used'] - copay + s['respite'] - respite_copay),
                     '服務次數': s['visits'], '提醒': '；'.join(sorted(s['notes']))})
    summary_df = pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values('案家ID').reset_index(drop=True)
    claims_df = pd.DataFrame(claims, columns=[k for k, _ in CLAIM_COLUMNS])
    claims_df.columns = [label for _, label in CLAIM_COLUMNS]
    notes = []
    if unknown_codes:
        notes.append('下列服務碼在 Service_code 主檔沒有價格，申報檔已標記待人工填寫：' + '、'.join(sorted(unknown_codes)))
    return claims_df, summary_df, notes


EXPLAIN = [
    ('用途', '依 CareFlow 排定班表試算當月申報明細與案家自付額，供居督核對後匯入 CSMS、寄繳費通知。不是核銷結果。'),
    ('額度', '照顧及專業服務（B、C 碼）每月額度：2 級 10,020、3 級 15,460、4 級 18,580、5 級 24,100、6 級 28,070、7 級 32,090、8 級 36,180 元。'),
    ('部分負擔', '第一類（長照低收入戶）0%、第二類（中低收入戶）5%、第三類（一般戶）16%，小數無條件捨去；超過額度部分全額自費。'),
    ('未遇', '打卡網頁登錄「未遇」的服務，申報檔標記 Y、金額 0。'),
    ('需要補的欄位', '服務單位代碼、個案與照顧服務員身分證號：本系統不存身分證，請在機構系統對照補上後再匯入。'),
    ('欄位格式', '欄位依衛福部支付審核系統的服務紀錄結構整理；若 CSMS 匯入範本不同，請以範本為準（billing.py 的 CLAIM_COLUMNS 可調整）。'),
    ('未涵蓋', '喘息年度額度、外籍看護工家庭（額度 30%）、身障補助、縣市加碼與交通接送，請人工確認。'),
]


def to_excel(claims, summary, notes, month):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        claims.to_excel(writer, sheet_name='服務紀錄申報', index=False)
        summary.to_excel(writer, sheet_name='自付額試算', index=False)
        pd.DataFrame(EXPLAIN + [('提醒', n) for n in notes] + [('月份', month)], columns=['項目', '說明']).to_excel(
            writer, sheet_name='說明', index=False)
        for ws in writer.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value or '')) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(48, max(9, width * 1.6))
    return buf.getvalue()
