import io
from types import SimpleNamespace

import pandas as pd

from attendance_reconcile import (STATUS_NOT_YET, STATUS_OK, STATUS_UNASSIGNED, by_caregiver, normalize_records,
                                  read_records, reconcile, summary, template_frame)

CFG = SimpleNamespace(attendance_late_grace_mins=10, attendance_early_leave_grace_mins=10, attendance_short_ratio=0.9,
                      attendance_match_window_mins=180, attendance_gps_radius_m=300)


def _schedule():
    # 與 app 的 df_final 同欄名
    return pd.DataFrame({
        '任務ID': ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8'],
        '日期': ['2026-11-02'] * 6 + ['2026-11-09', '2026-11-02'],
        '開始時間': ['08:00', '10:00', '14:00', '08:00', '16:00', '09:00', '08:00', '18:00'],
        '結束時間': ['09:00', '12:00', '15:00', '10:00', '17:00', '10:00', '09:00', '19:00'],
        '案號': ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C1', 'C9'],
        '派單居服員': ['A', 'A', 'B', 'B', 'C', 'C', 'A', '未指派'],
        '服務分鐘': [60, 120, 60, 120, 60, 60, 60, 60],
        '服務地點_緯度': [25.0, 25.0, 25.0, 25.0, 25.0, 25.0, 25.0, 25.0],
        '服務地點_經度': [121.5] * 8,
    })


def _records():
    raw = pd.DataFrame([
        {'日期': '2026-11-02', '員工編號': 'A', '案號': 'C1', '簽到時間': '08:02', '簽退時間': '09:01', '緯度': 25.0005, '經度': 121.5},
        {'日期': '2026-11-02', '員工編號': 'A', '案號': 'C2', '簽到時間': '10:25', '簽退時間': '11:40'},   # 遲到＋早退＋時數不足
        {'日期': '2026-11-02', '員工編號': 'B', '案號': 'C3', '簽到時間': '14:00', '服務結果': '未遇（案家不在）'},
        # T4 無紀錄
        {'日期': '2026-11-02', '員工編號': 'D', '案號': 'C5', '簽到時間': '16:00', '簽退時間': '17:00'},   # 換人
        {'日期': '2026-11-02', '員工編號': 'C', '案號': 'C6', '簽到時間': '09:00', '簽退時間': '10:00',
         '緯度': 25.05, '經度': 121.5, '補卡': '是'},                                                      # 地點異常＋補卡
        {'日期': '2026-11-02', '員工編號': 'A', '案號': 'C7', '簽到時間': '13:00', '簽退時間': '14:00'},   # 排外
    ])
    recs, info = normalize_records(raw)
    assert info['略過列數'] == 0
    return recs


def _by_task(result):
    return result.set_index('任務ID')


def test_classifies_each_visit():
    out = _by_task(reconcile(_schedule(), _records(), CFG))
    assert out.loc['T1', '狀態'] == STATUS_OK
    assert out.loc['T1', '距案家(公尺)'] < 300
    t2 = out.loc['T2', '異常項目'].split('、')
    assert {'遲到', '早退', '時數不足'} <= set(t2) and out.loc['T2', '遲到(分)'] == 25
    assert out.loc['T3', '狀態'] == '未遇'
    assert '缺簽退' not in out.loc['T3', '異常項目']           # 未遇不再要求簽退
    assert out.loc['T4', '狀態'] == '無紀錄' and out.loc['T4', '嚴重度'] == '高'
    assert out.loc['T5', '狀態'] == '換人服務' and out.loc['T5', '實際居服員'] == 'D'
    assert set(out.loc['T6', '異常項目'].split('、')) == {'地點異常', '補卡待審'}
    assert out.loc['T7', '狀態'] == STATUS_NOT_YET            # 對照截止日之後
    assert out.loc['T8', '狀態'] == STATUS_UNASSIGNED


def test_unscheduled_record_is_reported():
    out = reconcile(_schedule(), _records(), CFG)
    extra = out[out['狀態'] == '排外服務']
    assert len(extra) == 1 and extra.iloc[0]['案家ID'] == 'C7'


def test_summary_and_caregiver_table():
    out = reconcile(_schedule(), _records(), CFG)
    s = summary(out)
    assert s['應對照'] == 6 and s['正常'] == 1 and s['無紀錄'] == 1 and s['未遇'] == 1 and s['排外服務'] == 1
    table = by_caregiver(out).set_index('居服員ID')
    assert table.loc['A', '遲到'] == 1 and table.loc['B', '無紀錄'] == 1


def test_task_id_match_wins_and_duplicates_flagged():
    raw = pd.DataFrame([
        {'任務ID': 'T2', '居服員ID': 'A', '日期': '2026-11-02', '簽到時間': '10:00', '簽退時間': '12:00'},
        {'任務ID': 'T2', '居服員ID': 'A', '日期': '2026-11-02', '簽到時間': '10:01', '簽退時間': '12:00'},
    ])
    recs, _ = normalize_records(raw)
    out = reconcile(_schedule(), recs, CFG)
    assert _by_task(out[out['狀態'] != '重複紀錄']).loc['T2', '狀態'] == STATUS_OK
    assert (out['狀態'] == '重複紀錄').sum() == 1


def test_punch_log_format_is_paired():
    raw = pd.DataFrame([
        {'居服員ID': 'A', '案家ID': 'C1', '打卡類型': '簽到', '打卡時間': '2026-11-02 08:01'},
        {'居服員ID': 'A', '案家ID': 'C1', '打卡類型': '簽退', '打卡時間': '2026-11-02 09:00'},
        {'居服員ID': 'A', '案家ID': 'C2', '打卡類型': '簽到', '打卡時間': '2026-11-02 10:00'},
    ])
    recs, _ = normalize_records(raw)
    assert len(recs) == 2
    out = _by_task(reconcile(_schedule(), recs, CFG))
    assert out.loc['T1', '狀態'] == STATUS_OK
    assert out.loc['T2', '狀態'] == '缺簽退'


def test_minguo_date_excel_time_and_csv_big5():
    raw = pd.DataFrame([{'服務日期': '115/11/02', '居服員編號': 'A', '個案編號': 'C1', '實際開始': '0800', '實際結束': '09:00'}])
    recs, _ = normalize_records(raw)
    assert str(recs.iloc[0]['日期']) == '2026-11-02' and recs.iloc[0]['簽到'].hour == 8
    data = template_frame().to_csv(index=False).encode('cp950')
    frame = read_records(io.BytesIO(data), 'x.csv')
    assert '居服員ID' in frame.columns and len(normalize_records(frame)[0]) == 3


def test_record_far_from_schedule_time_is_not_matched():
    raw = pd.DataFrame([{'居服員ID': 'A', '案家ID': 'C1', '日期': '2026-11-02', '簽到時間': '20:00', '簽退時間': '21:00'}])
    recs, _ = normalize_records(raw)
    out = reconcile(_schedule(), recs, CFG)
    assert _by_task(out[out['任務ID'] != '']).loc['T1', '狀態'] == '無紀錄'
    assert (out['狀態'] == '排外服務').sum() == 1


def test_same_day_visits_not_yet_due_are_not_flagged():
    from datetime import datetime
    raw = pd.DataFrame([{'日期': '2026-11-02', '居服員ID': 'A', '案家ID': 'C2', '簽到時間': '10:00'}])
    recs, _ = normalize_records(raw)
    out = _by_task(reconcile(_schedule(), recs, CFG, now=datetime(2026, 11, 2, 11, 0)))
    assert out.loc['T2', '狀態'] == STATUS_OK          # 服務中，還不用簽退
    assert out.loc['T3', '狀態'] == STATUS_NOT_YET     # 14:00 的班還沒到
    assert out.loc['T1', '狀態'] == '無紀錄'           # 09:00 已結束
    later = _by_task(reconcile(_schedule(), recs, CFG, now=datetime(2026, 11, 2, 18, 0)))
    assert later.loc['T2', '狀態'] == '缺簽退'
