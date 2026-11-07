"""v15 tests: transit fallback, unified worker status table, multi-worker team suggestion, de-identified worker column."""
CAREFLOW_BUILD = '2026.10.11-v18'
import unittest
from unittest.mock import patch
import pandas as pd


class TransitFallbackTests(unittest.TestCase):
    def setUp(self):
        import caregiver_engine as e
        self.e = e
        self.saved = dict(e.TRANSIT_FALLBACK)

    def tearDown(self):
        self.e.TRANSIT_FALLBACK.clear(); self.e.TRANSIT_FALLBACK.update(self.saved)

    def test_walk_estimate_within_two_km(self):
        self.e.TRANSIT_FALLBACK['enabled'] = True
        minutes = self.e.transit_fallback_minutes(25.0000, 121.5000, 25.0100, 121.5000)   # ≈1.1 km
        self.assertIsNotNone(minutes)
        self.assertTrue(10 < minutes < 40)

    def test_motorcycle_estimate_when_far(self):
        self.e.TRANSIT_FALLBACK['enabled'] = True
        with patch.object(self.e, 'get_osrm_travel_time', return_value=20.0):
            minutes = self.e.transit_fallback_minutes(25.00, 121.50, 25.05, 121.50)   # ≈5.5 km
        self.assertEqual(minutes, 20.0 * self.e.TRANSIT_FALLBACK['motor_factor'] + self.e.TRANSIT_FALLBACK['wait_min'])

    def test_disabled_returns_none(self):
        self.e.TRANSIT_FALLBACK['enabled'] = False
        self.assertIsNone(self.e.transit_fallback_minutes(25.0, 121.5, 25.01, 121.5))

    def test_unreachable_osrm_returns_none(self):
        self.e.TRANSIT_FALLBACK['enabled'] = True
        with patch.object(self.e, 'get_osrm_travel_time', return_value=float('inf')):
            self.assertIsNone(self.e.transit_fallback_minutes(25.00, 121.50, 25.05, 121.50))


class StatusTableTests(unittest.TestCase):
    def test_same_format_for_tried_and_untried(self):
        from eligibility import worker_status_table
        screen = pd.DataFrame([
            {'居服員ID': 'A', '_ok': 3, '資格可排': '18/18', '阻擋原因': ''},
            {'居服員ID': 'B', '_ok': 3, '資格可排': '18/18', '阻擋原因': ''},
            {'居服員ID': 'C', '_ok': 0, '資格可排': '10/18', '阻擋原因': '缺失智症證照'},
            {'居服員ID': 'D', '_ok': 3, '資格可排': '18/18', '阻擋原因': ''}])
        trial = pd.DataFrame([
            {'居服員ID': 'A', '可安排': 4, '總次數': 4, '月加班(時)': '0', '主要原因': ''},
            {'居服員ID': 'B', '可安排': 0, '總次數': 4, '月加班(時)': '0', '主要原因': '時間衝突'}])
        t = worker_status_table(screen, trial, 4)
        states = dict(zip(t['居服員ID'], t['狀態']))
        self.assertTrue(states['A'].startswith('✅'))
        self.assertTrue(states['B'].startswith('❌'))
        self.assertTrue(states['D'].startswith('🔘'))      # eligible but not yet tried: still listed, never missing
        self.assertTrue(states['C'].startswith('⛔'))
        self.assertEqual(set(t['居服員ID']), {'A', 'B', 'C', 'D'})
        self.assertEqual(t.iloc[0]['居服員ID'], 'A')
        self.assertEqual(t.iloc[-1]['居服員ID'], 'C')


def _tasks(days_hours):
    rows = []
    for i, (day, start) in enumerate(days_hours):
        rows.append({'任務ID': f'T{i}', '案家ID': 'X', '日期': day, '時間窗_開始': start, '時間窗_結束': '10:00', '來源列': i})
    return pd.DataFrame(rows)


class TeamSuggestionTests(unittest.TestCase):
    def test_single_worker_when_one_can_do_everything(self):
        from pending_clients import suggest_team
        tasks = _tasks([('2026-10-05', '09:00'), ('2026-10-06', '09:00')])
        plan = suggest_team(tasks, {'A': {'T0', 'T1'}, 'B': {'T0'}})
        self.assertEqual(plan['team'], ['A'])
        self.assertEqual((plan['covered'], plan['total']), (2, 2))

    def test_two_workers_cover_what_neither_can_alone(self):
        from pending_clients import suggest_team
        tasks = _tasks([('2026-10-05', '09:00'), ('2026-10-06', '09:00')])   # Monday pattern, Tuesday pattern
        plan = suggest_team(tasks, {'A': {'T0'}, 'B': {'T1'}})
        self.assertEqual(set(plan['team']), {'A', 'B'})
        self.assertEqual((plan['covered'], plan['total']), (2, 2))
        self.assertEqual(plan['uncovered_patterns'], [])

    def test_uncovered_pattern_is_reported(self):
        from pending_clients import suggest_team
        tasks = _tasks([('2026-10-05', '09:00'), ('2026-10-06', '09:00')])
        plan = suggest_team(tasks, {'A': {'T0'}})
        self.assertEqual(plan['covered'], 1)
        self.assertEqual(len(plan['uncovered_patterns']), 1)


class IdentityTests(unittest.TestCase):
    def test_worker_column_named_居服員_is_masked_for_display(self):
        from caregiver_engine import identity_display
        out = identity_display(pd.DataFrame({'居服員': ['D9577', 'Z0000'], '備註': ['x', 'y']}), {'D9577': '施○○'})
        self.assertEqual(out.loc[0, '居服員'], 'D9577｜施○○')
        self.assertEqual(out.loc[1, '居服員'], 'Z0000')


class StampTests(unittest.TestCase):
    def test_modules_carry_v15(self):
        import pathlib
        from build_info import CAREFLOW_BUILD as build, REQUIRED_MODULES
        self.assertEqual(build, CAREFLOW_BUILD)
        here = pathlib.Path(__file__).resolve().parent
        for name in REQUIRED_MODULES + ('build_info',):
            self.assertIn("CAREFLOW_BUILD = '%s'" % build, (here / f'{name}.py').read_text(encoding='utf-8'), name)


if __name__ == '__main__':
    unittest.main()
