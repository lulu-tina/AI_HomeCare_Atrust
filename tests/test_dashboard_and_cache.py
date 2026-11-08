import unittest
import pandas as pd
from dashboard_metrics import build_dashboard
from schedule_checks import incremental_travel


def sample():
    tasks = pd.DataFrame({
        '任務ID': ['t1', 't2', 't3', 't4'], '案家ID': ['c1', 'c1', 'c2', 'c2'],
        '日期': ['2026-10-05', '2026-10-06', '2026-10-05', '2026-10-10'],
        '時間窗_開始': ['09:00', '09:00', '10:30', '15:00'], '時間窗_結束': ['10:00', '10:00', '11:30', '16:00']})
    assign = pd.DataFrame({'任務ID': ['t1', 't2', 't3'], '派單居服員': ['A', 'B', 'B']})
    payroll = pd.DataFrame({'任務ID': ['t1', 't2', 't3'], '固定居服員ID': ['A', 'A', 'B'],
                            '是否請假代班': [False, True, False]})
    return tasks, assign, payroll


class DashboardTests(unittest.TestCase):
    def test_counts_reasons_and_stability(self):
        tasks, assign, payroll = sample()
        diag = [{'任務ID': 't4', '問題分類': '休息不足', '建議處理': '留休息'}]
        d = build_dashboard(tasks, assign, diag, payroll, None, None, None, {'c1': 'A', 'c2': 'B'})
        s = d['summary']
        self.assertEqual((s['total'], s['assigned'], s['unassigned']), (4, 3, 1))
        self.assertEqual((s['primary'], s['leave'], s['backup']), (2, 1, 0))
        self.assertAlmostEqual(s['primary_share'], 2 / 3 * 100)
        self.assertEqual(d['reasons'].loc[0, '原因'], '休息不足')
        self.assertEqual(int(d['by_day']['未派'].sum()), 1)
        self.assertEqual(int(d['heat_unassigned'].values.sum()), 1)
        self.assertEqual(d['nonprimary'].loc[0, '案家ID'], 'c1')

    def test_unassigned_without_diagnostics_is_still_explained(self):
        tasks, assign, _ = sample()
        d = build_dashboard(tasks, assign.iloc[:1], None, None, None, None, None, {})
        self.assertEqual(int(d['reasons']['未派筆數'].sum()), 3)
        self.assertTrue((d['reasons']['原因'] == '待核對目前班表').all())


class RouteCacheTests(unittest.TestCase):
    def test_unchanged_groups_are_not_recalculated(self):
        tasks = pd.DataFrame({'任務ID': ['a', 'b', 'c'], '日期': ['2026-10-05'] * 3,
                              '時間窗_開始': ['09:00', '11:00', '09:00'], '時間窗_結束': ['10:00', '12:00', '10:00']})
        cg = pd.DataFrame({'居服員ID': ['X', 'Y']})
        result = pd.DataFrame({'任務ID': ['a', 'b', 'c'], '派單居服員': ['X', 'X', 'Y']})
        calls = []

        def calculate(group, subset, caregivers, config):
            calls.append(tuple(group['任務ID']))
            out = group[['任務ID']].copy()
            out['預估車程(分)'] = 5.0
            return out

        class Cfg:
            buffer_mins = 15
        cache = {}
        first = incremental_travel(result, tasks, cg, Cfg(), cache, calculate)
        self.assertEqual(len(calls), 2)
        again = incremental_travel(result, tasks, cg, Cfg(), cache, calculate)
        self.assertEqual(len(calls), 2)
        pd.testing.assert_frame_equal(first, again)
        moved = result.copy()
        moved.loc[2, '派單居服員'] = 'X'
        incremental_travel(moved, tasks, cg, Cfg(), cache, calculate)
        self.assertEqual(len(calls), 3)  # only the changed caregiver-day is recalculated
        self.assertEqual(calls[-1], ('a', 'b', 'c'))


if __name__ == '__main__':
    unittest.main()
