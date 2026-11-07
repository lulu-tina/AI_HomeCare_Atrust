"""v18 tests: demand heat map, month-ledger memo, route-signature stability, plan memo."""
CAREFLOW_BUILD = '2026.10.11-v18'
import unittest
import pandas as pd
from dashboard_metrics import demand_heat, peak_slots, build_dashboard
import schedule_checks


def _tasks(rows):
    df = pd.DataFrame(rows, columns=['任務ID', '日期', '時間窗_開始', '時間窗_結束', 'ok'])
    df['_date'] = pd.to_datetime(df['日期']).dt.normalize()
    df['_assigned'] = df['ok']
    return df


class DemandHeatTests(unittest.TestCase):
    def test_hour_split_and_weeks(self):
        # 2026-10-01 是週四；13:30–15:10 要拆成 13時30分、14時60分、15時10分。期間共 14 天＝週四出現 2 次。
        t = _tasks([('a', '2026-10-01', '13:30', '15:10', True), ('b', '2026-10-14', '14:00', '15:00', False)])
        h = demand_heat(t)
        self.assertAlmostEqual(h['hours'].loc['週四', '13時'], 0.5)
        self.assertAlmostEqual(h['hours'].loc['週四', '14時'], 1.0)
        self.assertAlmostEqual(h['hours'].loc['週四', '15時'], 10 / 60)
        self.assertEqual(h['hours'].values.sum().round(6), round(160 / 60, 6))
        self.assertAlmostEqual(h['unassigned_hours'].sum().sum(), 1.0)

    def test_total_hours_equal_service_minutes(self):
        t = _tasks([('a', '2026-10-05', '08:00', '09:30', True), ('b', '2026-10-06', '09:15', '12:45', True)])
        self.assertAlmostEqual(demand_heat(t)['hours'].sum().sum(), (90 + 210) / 60)

    def test_supply_and_pressure(self):
        t = _tasks([('a', '2026-10-01', '10:00', '11:00', True), ('b', '2026-10-08', '10:00', '11:00', True)])
        cg = pd.DataFrame({'居服員ID': ['x', 'y', 'z'], '可排班星期': ['1,2,3,4,5', '1,2,3,5', '4'],
                           '每日可服務時段_起': ['08:00', '08:00', '11:00'], '每日可服務時段_迄': ['21:00', '21:00', '21:00']})
        h = demand_heat(t, cg)
        self.assertEqual(int(h['supply'].loc['週四', '10時']), 1)   # x 可上班；y 休週四；z 11 點才開始
        self.assertAlmostEqual(h['pressure'].loc['週四', '10時'], 1.0)   # 需求 1 位 ÷ 可上班 1 位

    def test_peak_slots_orders_by_value(self):
        t = _tasks([('a', '2026-10-01', '10:00', '11:00', True), ('b', '2026-10-01', '10:00', '11:00', True),
                    ('c', '2026-10-02', '15:00', '16:00', True)])
        h = demand_heat(t)
        top = peak_slots(h['hours'], None, h['demand'], n=2)
        self.assertEqual((top[0]['星期'], top[0]['時段']), ('週四', '10時'))
        self.assertEqual(top[0]['值'], 2.0)

    def test_empty_and_missing_columns(self):
        self.assertTrue(demand_heat(pd.DataFrame())['demand'].empty)
        self.assertTrue(demand_heat(pd.DataFrame({'_date': [pd.Timestamp('2026-10-01')]}))['demand'].empty)

    def test_build_dashboard_exposes_heat(self):
        tasks = pd.DataFrame({'任務ID': ['t1', 't2'], '案家ID': ['c1', 'c2'], '日期': ['2026-10-01', '2026-10-01'],
                              '時間窗_開始': ['10:00', '10:00'], '時間窗_結束': ['11:00', '11:30']})
        assign = pd.DataFrame({'任務ID': ['t1'], '派單居服員': ['A']})
        D = build_dashboard(tasks, assign, None, None, None, None, None, {})
        self.assertAlmostEqual(D['heat_hours'].loc['週四', '10時'], 2.0)
        self.assertAlmostEqual(D['heat_unassigned_hours'].loc['週四', '10時'], 1.0)
        self.assertTrue(D['heat_supply'].empty)


class RouteSignatureTests(unittest.TestCase):
    def test_client_maps_do_not_invalidate_routes(self):
        from caregiver_engine import PipelineConfig
        result = pd.DataFrame({'任務ID': ['t1'], '派單居服員': ['A'], '排班日期': ['2026-10-01']})
        tasks = pd.DataFrame({'任務ID': ['t1'], '日期': ['2026-10-01'], '案家ID': ['c1']})
        cg = pd.DataFrame({'居服員ID': ['A']})
        calls = []

        def calc(group, subset, caregivers, config):
            calls.append(1)
            out = group.copy()
            out['預估車程(分)'] = 5.0
            return out
        cfg = PipelineConfig()
        cache = {}
        schedule_checks.incremental_travel(result, tasks, cg, cfg, cache, calc)
        cfg.client_primary_map = {'c1': 'A'}
        cfg.client_backup_map = {'c1': ('B',)}
        schedule_checks.incremental_travel(result, tasks, cg, cfg, cache, calc)
        self.assertEqual(len(calls), 1)


class MemoTests(unittest.TestCase):
    def test_build_plans_reuses_advice_and_previews(self):
        import pending_advice as pa
        n = {'advise': 0, 'run': 0}
        real = pa.advise

        def fake_advise(preview, *a, **k):
            n['advise'] += 1
            return [{'apply': {'type': 'shift', 'pid': 'p', 'minutes': 30}, '種類': '時間挪動', '必要': False, '標題': 't', '人選': ['A']}]

        def run(edits):
            n['run'] += 1
            return {'failed': pd.DataFrame(), 'total': 1}
        pa.advise = fake_advise
        try:
            base = {'failed': pd.DataFrame({'任務ID': ['x']}), 'total': 1}
            memo = {}
            pa.build_plans(base, None, None, run, {}, None, max_plans=3, memo=memo)
            first = dict(n)
            pa.build_plans(base, None, None, run, {}, None, max_plans=3, memo=memo)
            self.assertEqual(n, first)  # 第二次完全沿用
            self.assertEqual(first['advise'], 1)
        finally:
            pa.advise = real


if __name__ == '__main__':
    unittest.main()


class BackgroundAnalysisTests(unittest.TestCase):
    def setUp(self):
        import analysis_bg
        self.bg = analysis_bg
        self.bg._REG.clear()

    def test_runs_in_background_and_dedupes(self):
        import threading
        import time
        calls = []
        gate = threading.Event()

        def job():
            calls.append(1)
            gate.wait(2)
            return {'ok': True}
        key = ('s', 'sig', 'c', 'w', 'q')
        slot = self.bg.submit(key, job)
        again = self.bg.submit(key, job)          # 同一個 key 不會重排
        self.assertIs(slot, again)
        self.assertTrue(self.bg.busy('s', 'sig'))
        self.assertFalse(self.bg.busy('s', 'other'))
        gate.set()
        for _ in range(50):
            if slot['state'] == 'done':
                break
            time.sleep(0.05)
        self.assertEqual(slot['state'], 'done')
        self.assertEqual(slot['entry'], {'ok': True})
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.bg.busy('s', 'sig'))

    def test_error_is_recorded_not_raised(self):
        import time
        slot = self.bg.submit(('s', 'sig', 'c', 'w', 'q'), lambda: 1 / 0)
        for _ in range(50):
            if slot['state'] in ('done', 'error'):
                break
            time.sleep(0.05)
        self.assertEqual(slot['state'], 'error')
        self.assertTrue(slot['error'])

    def test_old_signature_results_are_forgotten(self):
        import os
        os.environ['CAREFLOW_SYNC_ANALYSIS'] = '1'
        try:
            self.bg.submit(('s', 'old', 'c', 'w', 'q'), lambda: {'x': 1})
            self.bg.submit(('s', 'new', 'c', 'w', 'q'), lambda: {'x': 2})
            self.bg.forget_other_signatures('s', 'new')
            self.assertIsNone(self.bg.get(('s', 'old', 'c', 'w', 'q')))
            self.assertIsNotNone(self.bg.get(('s', 'new', 'c', 'w', 'q')))
        finally:
            os.environ.pop('CAREFLOW_SYNC_ANALYSIS', None)


class TransitSkipTests(unittest.TestCase):
    def test_transit_candidates_skipped_in_suggestions(self):
        import inspect
        import pending_clients
        src = inspect.getsource(pending_clients.preview_client_assignment)
        self.assertIn("live_transit=False", src)
        self.assertIn("大眾運輸", src)
