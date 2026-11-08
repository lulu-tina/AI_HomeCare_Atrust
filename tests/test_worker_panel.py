"""以居服員為主軸排班：所屬案家、未排時段、有空天數與距離。"""
import pandas as pd

import worker_panel as wp


def _task(tid, client, day, start, end, row, weekday='星期一'):
    return {'任務ID': tid, '案家ID': client, '日期': day, '星期': weekday, '時間窗_開始': start, '時間窗_結束': end,
            '來源列': row, '頻率': '每週', 'Service_Code_1': 'BA02', 'Units_1': 1.0,
            '服務地點_緯度': 25.04, '服務地點_經度': 121.54}


TASKS = pd.DataFrame([
    _task('C1', 'C', '2026-11-02', '09:00', '10:00', 2), _task('C2', 'C', '2026-11-09', '09:00', '10:00', 2),
    _task('D1', 'D', '2026-11-02', '09:30', '10:30', 3), _task('D2', 'D', '2026-11-09', '09:30', '10:30', 3),
    _task('E1', 'E', '2026-11-03', '14:00', '15:00', 4, '星期二'),
    _task('E2', 'E', '2026-11-10', '14:00', '15:00', 4, '星期二'),
])
CG = pd.DataFrame([{'居服員ID': 'A', '服務起點_緯度(家)': 25.03, '服務起點_經度(家)': 121.53}])


def test_overview_counts_owned_and_open_slots():
    effective = pd.DataFrame([{'任務ID': 'C1', '派單居服員': 'A'}, {'任務ID': 'C2', '派單居服員': 'B'},
                              {'任務ID': 'D1', '派單居服員': 'None'}])
    view = wp.slot_overview(TASKS, effective, 'A', owners={'C': 'A', 'D': 'B'}, caregivers=CG)
    c = view.set_index('案家ID').loc['C']
    assert (c['本期次數'], c['由他服務'], c['他人服務'], c['未排']) == (2, 1, 1, 0)
    assert wp.status_text(c) == '1 次由別人代班'
    assert wp.owned_slots(view)['案家ID'].tolist() == ['C']
    d = view.set_index('案家ID').loc['D']
    assert d['未排'] == 2 and d['未排任務ID'] == ['D1', 'D2']
    # 11/02 09:30 與 A 已排的 C1（09:00-10:00）重疊；11/09 A 沒有班
    assert d['他有空的天數'] == 1
    assert 0 < d['距離(公里)'] < 2
    opened = wp.open_slots(view)
    assert opened['案家ID'].tolist() == ['E', 'D']        # E 兩天都有空，排在前面
    assert wp.open_slots(view, only_owned=True).empty


def test_empty_inputs_return_empty_tables():
    assert wp.slot_overview(pd.DataFrame(), pd.DataFrame(), 'A').empty
    view = wp.slot_overview(TASKS, None, 'A')
    assert view['未排'].sum() == 6 and not view['所屬'].any()
