import unittest
from io import BytesIO
from zipfile import ZipFile

import caregiver_engine as engine
from workbook_edit import Workbook
from repair_workbook import to_clock, to_minutes


class NearWalkTests(unittest.TestCase):
    def test_close_points_are_walking(self):
        # 約 300 公尺：走路估算，不是 inf／找不到路線
        minutes = engine.near_walk_minutes(25.0000, 121.5000, 25.0027, 121.5000)
        self.assertIsNotNone(minutes)
        self.assertGreaterEqual(minutes, engine.WALK_MIN_MINUTES)
        self.assertLessEqual(minutes, 10)

    def test_far_points_are_not_walking(self):
        self.assertIsNone(engine.near_walk_minutes(25.0, 121.5, 25.05, 121.5))

    def test_same_point_uses_minimum(self):
        self.assertEqual(engine.near_walk_minutes(25.0, 121.5, 25.0, 121.5), engine.WALK_MIN_MINUTES)


class TimeHelperTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(to_minutes('10:15:00'), 615)
        self.assertEqual(to_clock(615), '10:15:00')

    def test_excel_fraction(self):
        self.assertEqual(to_minutes('0.4861111111111111'), 700)


class WorkbookEditTests(unittest.TestCase):
    def test_workbook_requires_valid_zip(self):
        with self.assertRaises(Exception):
            Workbook(b'not a zip')


class BackupGuardTests(unittest.TestCase):
    """畫面每次重跑 config 都是新的：備援名單一定要跟著班表還原，否則備援接手的服務會被退回。"""

    def test_backup_is_accepted_only_when_map_is_restored(self):
        from agency_rules import primary_guard
        task = {'案家ID': 'C1', '日期': '2026-10-05', '時間窗_開始': '09:00', '時間窗_結束': '10:00'}
        owner = {'居服員ID': 'A'}
        other = {'居服員ID': 'B'}
        config = engine.PipelineConfig(client_primary_map={'C1': 'A'}, primary_profiles={'A': owner})
        self.assertIsNotNone(primary_guard(task, other, config))          # 沒帶回備援名單 → 被退回
        config.client_backup_map = {'C1': ('B',)}
        self.assertIsNone(primary_guard(task, other, config))             # 帶回後 → 通過


class BuildStampTests(unittest.TestCase):
    def test_all_required_modules_carry_the_same_build(self):
        import pathlib
        from build_info import CAREFLOW_BUILD, REQUIRED_MODULES
        folder = pathlib.Path(__file__).resolve().parent
        for name in REQUIRED_MODULES:
            text = (folder / (name + '.py')).read_text(encoding='utf-8')
            self.assertIn("CAREFLOW_BUILD = '%s'" % CAREFLOW_BUILD, text, name)


if __name__ == '__main__':
    unittest.main()
