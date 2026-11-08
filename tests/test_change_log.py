"""異動紀錄：五種來源合成一張表、依時間排序、可依類型篩選。"""
import pandas as pd
import change_log


def _sources():
    override = pd.DataFrame([{'時間戳記': '2026-11-08 10:00:00', '任務ID': 'T1', '案家ID': 'C1', 'AI推薦居服員ID': 'A',
                              '居督指定居服員ID': 'B', '變更原因': '居服員請假'}])
    dynamic = [{'時間戳記': pd.Timestamp('2026-11-08 11:00'), '事件類型': '案家臨時改期', '任務ID': 'T2', '案家ID': 'C2',
                '原日期': '2026-11-09', '新日期': '2026-11-10', '最終選擇居服員': 'A', '系統Top3': 'x', '人工改選原因': '案家要求'},
               {'時間戳記': pd.Timestamp('2026-11-08 12:00'), '事件類型': '週計畫異動（新增）', '任務ID': 'T9', '案家ID': 'C4'}]
    whole = [{'工作表': '週服務計畫', '案號': 'C3', '異動': '服務時間後移 30 分鐘', '原值': '一 09:00', '新值': '一 09:30',
              '原因': '其他', '需確認': '與案家確認新時段', '時間': '2026-11-08 09:00'}]
    plan = [{'套用時間': '2026-11-08 12:00', '案家ID': 'C4', '生效日': '2026-11-10', '異動內容': '改時間：一 09:00 → 一 10:00',
             '移除舊班次': 3, '新增班次': 3, '待安排': 1, '原因': '照專複評'}]
    follow = [{'案家': 'C5', '需求變更': '新增失智', '處理方式': '補訓', '待辦': '完成訓練', '建立時間': '2026-11-08 08:30', '狀態': '追蹤中'}]
    audit = pd.DataFrame([{'序號': 1, '時間': '2026-11-08 08:00:00', '帳號': 'boss', '事件': '登入', '內容': ''}])
    return override, dynamic, whole, plan, follow, audit


def test_build_merges_all_sources_newest_first():
    frame = change_log.build(*_sources(), client_label=lambda c: '案' + c, caregiver_label=lambda w: '員' + w)
    assert list(frame.columns) == change_log.COLUMNS
    assert list(frame['類型']) == ['週計畫異動', '案家臨時改期', '改派', '整案調整', '資格追蹤', '系統：登入']
    assert '週計畫異動（新增）' not in set(frame['類型'])           # 逐筆明細不重複列
    moved = frame[frame['類型'] == '改派'].iloc[0]
    assert moved['內容'] == '員A → 員B' and moved['案家'] == '案C1' and moved['原因'] == '居服員請假'
    resched = frame[frame['類型'] == '案家臨時改期'].iloc[0]
    assert '新日期：2026-11-10' in resched['內容'] and '系統Top3' not in resched['內容']
    assert '待安排 1' in frame[frame['類型'] == '週計畫異動'].iloc[0]['內容']


def test_audit_rows_are_opt_in_and_empty_sources_ok():
    frame = change_log.build(*_sources())
    assert '系統：登入' in change_log.kinds(frame)
    assert '系統：登入' not in change_log.default_kinds(frame)
    assert change_log.kinds(frame)[-1] == '系統：登入'
    only_audit = change_log.build(audit=_sources()[-1])
    assert change_log.default_kinds(only_audit) == ['系統：登入']        # 沒有班表異動時全部顯示
    empty = change_log.build()
    assert empty.empty and list(empty.columns) == change_log.COLUMNS
