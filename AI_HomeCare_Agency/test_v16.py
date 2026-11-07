"""v16 tests: pattern token stability, split/shift helpers, edits store, change log."""
CAREFLOW_BUILD = '2026.10.11-v18'
import unittest
import pandas as pd
from pending_clients import (pattern_token, split_points, default_split_minutes, split_items, split_task, edited_pattern_table,
                             unit_minutes_from_master, service_items)
from pending_advice import empty_edits, apply_suggestion, edits_summary, change_log_rows

UNITS = {'BA13': 30.0, 'BA18': 30.0, 'BA02': 30.0}


def task(**extra):
    row = {'任務ID': 'W0207_20261007', '案家ID': 'N1112090025', '日期': '2026-10-07', '星期': '星期三', '來源列': 7,
           '時間窗_開始': '12:40', '時間窗_結束': '17:40', '服務歷時(分鐘)': 300.0,
           'Service_Code_1': 'BA13', 'Units_1': 2, 'Service_Code_2': 'BA18', 'Units_2': 7, 'Service_Code_3': 'BA02', 'Units_3': 1}
    row.update(extra)
    return row


class TokenTests(unittest.TestCase):
    def test_token_ignores_client_columns(self):
        a = task()
        b = task(是否失智=1, **{'失智症個案(0/1)': 1})
        self.assertEqual(pattern_token(a), pattern_token(b))

    def test_token_changes_with_time(self):
        self.assertNotEqual(pattern_token(task()), pattern_token(task(時間窗_開始='13:00', 時間窗_結束='18:00')))

    def test_same_weekday_same_token(self):
        self.assertEqual(pattern_token(task()), pattern_token(task(日期='2026-10-14', 任務ID='W0207_20261014')))


class SplitTests(unittest.TestCase):
    def test_points_on_unit_boundaries(self):
        pts = split_points(pd.Series(task()), UNITS)
        self.assertIn(150, pts)
        self.assertTrue(all(30 <= p <= 270 and p % 30 == 0 for p in pts))

    def test_default_is_half(self):
        self.assertEqual(default_split_minutes(pd.Series(task()), UNITS), 150)

    def test_split_items_conserve_units(self):
        one, two = split_items(pd.Series(task()), 150, UNITS)
        total = {}
        for c, u in one + two:
            total[c] = total.get(c, 0) + u
        self.assertEqual(total, {'BA13': 2, 'BA18': 7, 'BA02': 1})
        self.assertEqual(sum(u * 30 for _, u in one), 150)

    def test_split_task_ids_and_times(self):
        a, b = split_task(pd.Series(task()), 150, UNITS, {'開始時間': '12:40', '結束時間': '15:10'}, {'開始時間': '15:10', '結束時間': '17:40'})
        self.assertEqual(a['任務ID'], 'W0207_20261007')
        self.assertEqual(b['任務ID'], 'W0207B_20261007')
        self.assertEqual((a['時間窗_結束'], b['時間窗_開始']), ('15:10', '15:10'))
        self.assertEqual(a['服務歷時(分鐘)'] + b['服務歷時(分鐘)'], 300)
        self.assertEqual(sum(u for _, u in service_items(a)) + sum(u for _, u in service_items(b)), 10)

    def test_split_wrong_times_rejected(self):
        with self.assertRaises(ValueError):
            split_task(pd.Series(task()), 150, UNITS, {'開始時間': '12:40', '結束時間': '15:00'}, {'開始時間': '15:10', '結束時間': '17:40'})

    def test_too_short(self):
        with self.assertRaises(ValueError):
            default_split_minutes(pd.Series(task(**{'服務歷時(分鐘)': 30.0, 'Service_Code_1': 'BA13', 'Units_1': 1, 'Service_Code_2': None, 'Units_2': None,
                                                    'Service_Code_3': None, 'Units_3': None})), UNITS)

    def test_master_parse(self):
        m = pd.DataFrame({'系統代碼': ['ba13', 'BA02'], 'CareFlow排班分鐘(暫定)': [30, 'x']})
        self.assertEqual(unit_minutes_from_master(m), {'BA13': 30.0})


class EditsTests(unittest.TestCase):
    def test_shift_accumulates(self):
        e = apply_suggestion(empty_edits(), {'type': 'shift', 'pid': 'abc#1', 'minutes': 30})
        e = apply_suggestion(e, {'type': 'shift', 'pid': 'abc', 'minutes': 30})
        self.assertEqual(e['shifts']['abc'], 60)

    def test_does_not_mutate_input(self):
        base = empty_edits()
        apply_suggestion(base, {'type': 'shift', 'pid': 'abc', 'minutes': 30})
        self.assertEqual(base['shifts'], {})

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            apply_suggestion(empty_edits(), {'type': 'zzz'})

    def test_summary_and_log(self):
        e = apply_suggestion(empty_edits(), {'type': 'split', 'pid': 'p1', 'first': 150, 'gap': 0, 'first_worker': 'M04', 'second_worker': 'M05'})
        e = apply_suggestion(e, {'type': 'shift', 'pid': 'p2', 'minutes': 30})
        self.assertEqual(len(edits_summary(e)), 2)
        base = pd.DataFrame([
            {'模式ID': 'p1', '星期': '星期三', '開始時間': '12:40', '結束時間': '17:40', '服務分鐘': 300},
            {'模式ID': 'p2', '星期': '星期一', '開始時間': '15:30', '結束時間': '17:30', '服務分鐘': 120}])
        rows = change_log_rows('X', e, base, 'M04', ('M05',), '測試')
        kinds = [r['異動'] for r in rows]
        self.assertTrue(any('拆成兩段' in k for k in kinds))
        shift = next(r for r in rows if '後移' in r['異動'])
        self.assertEqual(shift['新值'], '星期一 16:00～18:00')
        self.assertEqual(set(rows[0]), {'工作表', '案號', '異動', '原值', '新值', '原因', '需確認'})


class StampTests(unittest.TestCase):
    def test_modules_carry_v16(self):
        import pathlib
        from build_info import CAREFLOW_BUILD as build, REQUIRED_MODULES
        self.assertEqual(build, CAREFLOW_BUILD)
        self.assertIn('pending_advice', REQUIRED_MODULES)
        here = pathlib.Path(__file__).resolve().parent
        for name in REQUIRED_MODULES + ('build_info',):
            self.assertIn("CAREFLOW_BUILD = '%s'" % build, (here / f'{name}.py').read_text(encoding='utf-8'), name)


if __name__ == '__main__':
    unittest.main()
