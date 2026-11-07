"""衝突與異常分類（純資料整理，不需要 Streamlit 或交通服務）。"""
import pandas as pd

from issue_center import assigned_overlaps, issue_group, issue_summary, transition_issues, unassigned_issues


def test_issue_group_maps_engine_categories():
    assert issue_group('時段重疊') == '班次重疊'
    assert issue_group('轉場空檔不足') == '轉場不足'
    assert issue_group('必要證照') == '條件不符'
    assert issue_group('休息不足') == '工時與休息'
    assert issue_group('人工待處理') == '請假與待處理'
    assert issue_group('資料或其他限制') == '其他未成功派班'
    assert issue_group(None) == '其他未成功派班'


def test_unassigned_issues_uses_last_diagnostic_and_fallback():
    pending = pd.DataFrame([{'任務ID': 't1', '案家ID': 'C1', '日期': '2026-11-02', '時間窗_開始': '09:00', '時間窗_結束': '10:00'},
                            {'任務ID': 't2', '案家ID': 'C2', '日期': '2026-11-02', '時間窗_開始': '13:00', '時間窗_結束': '14:00'}])
    diagnostics = [{'任務ID': 't1', '問題分類': '必要證照', '剔除原因': '缺特殊訓練', '建議處理': '核對取得資格'},
                   {'任務ID': 't1', '問題分類': '人工待處理', '剔除原因': '拒接'}]
    frame = unassigned_issues(pending, diagnostics)
    first = frame.set_index('任務ID').loc['t1']
    assert first['問題類型'] == '請假與待處理' and first['原因'] == '拒接'
    second = frame.set_index('任務ID').loc['t2']
    assert second['問題類型'] == '其他未成功派班' and second['原因'] == '待核對可用人選'


def test_assigned_overlaps_detects_same_worker_same_day_only():
    tasks = pd.DataFrame([
        {'任務ID': 'a', '案家ID': 'C1', '日期': '2026-11-02', '時間窗_開始': '09:00', '時間窗_結束': '11:00'},
        {'任務ID': 'b', '案家ID': 'C2', '日期': '2026-11-02', '時間窗_開始': '10:30', '時間窗_結束': '11:30'},
        {'任務ID': 'c', '案家ID': 'C3', '日期': '2026-11-02', '時間窗_開始': '11:00', '時間窗_結束': '12:00'},
        {'任務ID': 'd', '案家ID': 'C4', '日期': '2026-11-03', '時間窗_開始': '09:30', '時間窗_結束': '10:00'}])
    assignments = pd.DataFrame([{'任務ID': 'a', '派單居服員': 'E1'}, {'任務ID': 'b', '派單居服員': 'E1'},
                                {'任務ID': 'c', '派單居服員': 'E1'}, {'任務ID': 'd', '派單居服員': 'E1'},
                                {'任務ID': 'x', '派單居服員': '未指派'}])
    overlaps = assigned_overlaps(tasks, assignments)
    # b 與 a 重疊；c 在 b 結束（11:30）前開始，也算重疊；隔天的 d 不算。
    assert list(overlaps['任務ID']) == ['b', 'c']
    assert list(overlaps['重疊任務ID']) == ['a', 'b']
    assert assigned_overlaps(tasks, pd.DataFrame()).empty


def test_transition_issues_and_summary():
    routes = pd.DataFrame([
        {'任務ID': 'a', '交通日期': '2026-11-02', '派單居服員': 'E1', '轉場時段檢查': '轉場時間不足，需調班', '交通計算狀態': '依當日服務順序估算'},
        {'任務ID': 'b', '交通日期': '2026-11-02', '派單居服員': 'E2', '轉場時段檢查': '轉場時間足夠', '交通計算狀態': '缺起點或終點座標，無法計算'},
        {'任務ID': 'c', '交通日期': '2026-11-02', '派單居服員': 'E3', '轉場時段檢查': '轉場時間足夠', '交通計算狀態': '依當日服務順序估算'}])
    found = transition_issues(routes)
    assert dict(zip(found['任務ID'], found['問題'])) == {'a': '轉場時間不足', 'b': '交通資料待確認'}
    pending = unassigned_issues(pd.DataFrame([{'任務ID': 't', '案家ID': 'C9', '日期': '2026-11-02',
                                               '時間窗_開始': '09:00', '時間窗_結束': '10:00'}]),
                                [{'任務ID': 't', '問題分類': '轉場空檔不足'}])
    summary = issue_summary(pending, pd.DataFrame([{'任務ID': 'b'}]), found).set_index('問題類型')
    assert summary.loc['轉場不足', '已派需覆核'] == 2 and summary.loc['轉場不足', '未派服務'] == 1
    assert summary.loc['班次重疊', '已派需覆核'] == 1
    assert summary['未派服務'].sum() == 1
