"""CSMS 申報檔與自付額試算：額度、部分負擔比率、超額自費、未遇、純自費。數字都可手算核對。"""
from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

import billing

PRICES = {'BA01': 260, 'BA02': 195, 'BA07': 325, 'BA20': 175, 'GA09': 770}


def _row(tid, day, client, worker='A', **codes):
    row = {'任務ID': tid, '日期': day, '開始時間': '09:00', '結束時間': '10:00', '案號': client, '派單居服員': worker}
    for i, (code, units) in enumerate(codes.items(), 1):
        row[f'Service_Code_{i}'], row[f'Units_{i}'] = code, units
    return row


CLIENTS = pd.DataFrame([
    {'案家ID': 'C2', '核定等級': '2', '收入別': '第三類', '長照/身障': '老福科'},
    {'案家ID': 'C3', '核定等級': 3, '收入別': '第一類(低收)', '長照/身障': '老福科'},
    {'案家ID': 'C5', '核定等級': '5', '收入別': '第二類(中低收)', '長照/身障': '老福科'},
    {'案家ID': 'CS', '核定等級': '4', '收入別': '第三類', '長照/身障': '純自費(無公費)'},
    {'案家ID': 'CX', '核定等級': None, '收入別': '', '長照/身障': '身障科'},
])


def test_copay_and_quota_split_for_general_household():
    # C2（2 級，額度 10,020，一般戶 16%）：每次 BA02×4＋BA07×1 = 780+325 = 1,105 元，做 10 次 = 11,050 元
    rows = [_row(f'T{i}', f'2026-11-{i + 1:02d}', 'C2', BA02=4, BA07=1) for i in range(10)]
    claims, summary, notes = billing.build_month(pd.DataFrame(rows), CLIENTS, PRICES, '2026-11')
    s = summary.set_index('案家ID').loc['C2']
    # 前 9 次用掉 9,945 元；剩 75 元不夠任何一個單位，第 10 次的 1,105 元全部自費
    assert (s['額度內金額'], s['超額自費']) == (9945, 1105)
    assert s['部分負擔'] == 1591                                              # 9,945 × 16% = 1,591.2 → 無條件捨去
    assert s['案家應付合計'] == s['部分負擔'] + s['超額自費']
    assert s['政府補助（申報）'] == s['額度內金額'] - s['部分負擔']
    assert set(claims['申報類別']) == {'1 補助', '2 自費'} and claims['小計'].sum() == 11050
    assert claims[claims['申報類別'] == '2 自費']['備註'].str.contains('超過當月額度').all()


def test_low_income_middle_income_self_pay_missed_and_unknown():
    rows = [_row('L1', '2026-11-02', 'C3', BA01=1), _row('M1', '2026-11-02', 'C5', BA02=3),
            _row('S1', '2026-11-03', 'CS', BA02=2), _row('X1', '2026-11-03', 'CX', BA20=1, AA08=1),
            _row('N1', '2026-11-04', 'C3', BA01=1), _row('P1', '2026-11-05', 'C5', SA10=1, GA09=1),
            _row('U1', '2026-11-06', 'C5', worker='未指派', BA02=1), _row('O1', '2026-12-01', 'C5', BA02=1)]
    claims, summary, notes = billing.build_month(pd.DataFrame(rows), CLIENTS, PRICES, '2026-11', missed={'N1'})
    s = summary.set_index('案家ID')
    assert s.loc['C3', '部分負擔'] == 0 and s.loc['C3', '服務次數'] == 1          # 低收 0%；N1 未遇不算
    assert claims[claims['未遇'] == 'Y']['小計'].tolist() == [0]
    assert s.loc['C5', '部分負擔'] == int(585 * 0.05)                            # 中低收 5%：585 → 29
    assert s.loc['C5', '其他自費'] == 350 and s.loc['C5', '喘息短照部分負擔'] == int(770 * 0.05)
    assert s.loc['C5', '服務次數'] == 2                                         # 未指派、12 月的不算
    assert s.loc['CS', '其他自費'] == 390 and s.loc['CS', '部分負擔'] == 0        # 純自費個案：全部自費
    assert '缺核定等級' in s.loc['CX', '提醒'] and '身障' in s.loc['CX', '提醒'] and '收入別' in s.loc['CX', '提醒']
    assert s.loc['CX', '超額自費'] == 175                                       # 沒等級：額度 0，全部自費
    assert any('AA08' in n for n in notes) and claims[claims['照顧組合代碼'] == 'AA08']['小計'].isna().all()
    assert claims['個案身分證號'].eq('').all()                                  # 不輸出身分證（本系統沒有）


def test_service_text_fallback_and_excel_sheets():
    rows = [{'任務ID': 'T1', '日期': '2026-11-02', '開始時間': '09:00', '結束時間': '10:00', '案號': 'C2',
             '派單居服員': 'A', '服務項目': 'BA02(1.0)、BA20(2.0)'}]
    claims, summary, notes = billing.build_month(pd.DataFrame(rows), CLIENTS, PRICES, '2026-11', unit_code='U123')
    assert claims['照顧組合代碼'].tolist() == ['BA02', 'BA20'] and claims['小計'].sum() == 545
    assert claims['服務單位代碼'].eq('U123').all() and claims['服務日期'].iloc[0] == '2026/11/02'
    book = load_workbook(BytesIO(billing.to_excel(claims, summary, notes, '2026-11')))
    assert book.sheetnames == ['服務紀錄申報', '自付額試算', '說明']
    assert [c.value for c in book['服務紀錄申報'][1]] == [label for _, label in billing.CLAIM_COLUMNS]


def test_billing_section_in_review_page(tmp_path, monkeypatch):
    from datetime import date
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import caregiver_engine as engine
    from test_plan_change_ui import _setup
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.selectbox(key='billing_month').value == '2026-11'
    metrics = {m.label: m.value for m in app.metric}
    assert metrics['服務案家'] == '2 案'
    # 測試資料全是 SA10 自費（1 小時 350 元）：C 每週 2 次、D 每週 1 次，11 月共 13 次 × 350
    assert metrics['案家自付合計'] == f'{13 * 350:,} 元' and metrics['政府補助（申報）'] == '0 元'
    assert any('有提醒' in c.value for c in app.caption)                 # 測試個案沒有核定等級 → 提醒補資料
