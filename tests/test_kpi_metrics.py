import pandas as pd
import pytest

from kpi_metrics import (KPI_SPECS, compute_pilot_kpis, continuity_parts, dispatch_rate_parts,
                         error_parts, load_baseline, override_parts, save_baseline, transition_parts,
                         workload_spread_parts)


def _tasks():
    return pd.DataFrame({
        '任務ID': ['T1', 'T2', 'T3', 'T4'],
        '案家ID': ['C1', 'C1', 'C2', 'C3'],
        '日期': ['2026-10-01', '2026-10-01', '2026-10-01', '2026-10-02'],
        '時間窗_開始': ['08:00', '10:00', '09:30', '08:00'],
        '時間窗_結束': ['09:00', '11:00', '10:30', '09:00'],
    })


def _assign():
    # T3 與 T1 給同一人，但時段不重疊；T4 未派
    return pd.DataFrame({'任務ID': ['T1', 'T2', 'T3', 'T4'], '派單居服員': ['A', 'B', 'A', '未指派']})


def test_dispatch_rate_counts_only_real_assignments():
    assert dispatch_rate_parts(_tasks(), _assign()) == (3, 4)


def test_transition_excludes_first_leg_and_same_client():
    routes = pd.DataFrame({
        '任務ID': ['T1', 'T3', 'T2', 'T5'],
        '預估車程(分)': [12.0, 8.0, 0.0, 20.0],
        '路段緩衝(分)': [0.0, 5.0, 0.0, 5.0],
        '轉場時段檢查': ['首案，請確認出門時間', '轉場時間足夠', '轉場時間足夠', '轉場時間不足，需調班'],
    })
    assert transition_parts(routes) == (28.0, 2)


def test_continuity_only_counts_clients_with_primary():
    hit, total = continuity_parts(_tasks(), _assign(), {'C1': 'A', 'C2': 'A'})
    # C1: T1 by A (hit), T2 by B (miss); C2: T3 by A (hit); C3 無主責不計
    assert (hit, total) == (2, 3)


def test_override_rate_uses_period_tasks_and_dedupes():
    log = pd.DataFrame({'任務ID': ['T1', 'T1', 'X9', 'T2'],
                        'AI推薦居服員ID': ['A', 'A', 'A', 'B'],
                        '居督指定居服員ID': ['B', 'C', 'B', 'B']})
    # T1 兩次只算一次；X9 不在本期；T2 前後相同不算覆寫
    assert override_parts(_tasks(), _assign(), log) == (1, 3)


def test_error_rate_flags_overlap_and_short_transition():
    assign = pd.DataFrame({'任務ID': ['T1', 'T3'], '派單居服員': ['A', 'A']})
    tasks = _tasks().copy()
    tasks.loc[tasks['任務ID'] == 'T3', '時間窗_開始'] = '08:30'
    routes = pd.DataFrame({'任務ID': ['T1'], '轉場時段檢查': ['轉場時間不足，需調班'], '交通計算狀態': ['']})
    n, d = error_parts(tasks, assign, routes)
    assert (n, d) == (2, 2)


def test_workload_spread():
    m = pd.DataFrame({'居服員ID': ['A', 'B', 'C'], '總工時': [100, 100, 0]})
    std, mean, people = workload_spread_parts(m)
    assert (std, mean, people) == (0.0, 100.0, 2)


def test_full_table_has_numerator_denominator_baseline(tmp_path):
    path = tmp_path / 'b.json'
    save_baseline({'K1': {'value': 70, 'target': 80, 'period': '2026-09 人工排班'}}, str(path))
    base = load_baseline(str(path))
    table = compute_pilot_kpis(_tasks(), _assign(), baseline=base,
                               manual={'K2': {'numerator': 120}, 'K8': {'numerator': 40, 'denominator': 10}})
    assert list(table['代碼']) == [s.code for s in KPI_SPECS]
    k1 = table.set_index('代碼').loc['K1']
    assert k1['分子'] == '3' and k1['分母'] == '4' and k1['數值'] == 75.0
    assert k1['基準值'] == 70 and k1['與基準差異'] == 5.0 and k1['是否達標'] == '否'
    assert '2026-10-01～2026-10-02' == k1['測量期間']
    assert table.set_index('代碼').loc['K8', '數值'] == 4.0
    assert table.set_index('代碼').loc['K2', '數值'] == 120.0


def test_missing_manual_values_are_marked_not_invented():
    table = compute_pilot_kpis(_tasks(), _assign()).set_index('代碼')
    assert table.loc['K2', '是否達標'] == '待量測'
    assert table.loc['K8', '是否達標'] == '待量測'
