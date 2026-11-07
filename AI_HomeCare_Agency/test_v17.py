"""v17 tests: plain-language reasons, readable edit summary, plan notes, case-workbook builder."""
CAREFLOW_BUILD = '2026.10.11-v18'
import unittest
import pandas as pd
from pending_advice import plain_reason, edits_summary, empty_edits, apply_suggestion, _plan_notes, diagnose


class ReasonTests(unittest.TestCase):
    def test_gap(self):
        why, hint, kind = plain_reason('W1 → W2 只有 15 分鐘空檔，交通加緩衝需 18 分鐘。')
        self.assertIn('只剩 15 分鐘', why)
        self.assertIn('差 3 分鐘', why)
        self.assertEqual(kind, '時間')
        self.assertIn('挪', hint)

    def test_continuous(self):
        why, hint, kind = plain_reason('休息不足：連續工作（含轉場）將達 355 分鐘，上限 240 分鐘；需保留至少 30 分鐘休息（不含轉場）。')
        self.assertIn('355', why)
        self.assertEqual(kind, '連續工作')

    def test_cert(self):
        why, hint, kind = plain_reason('缺特殊訓練：BA08足部照護')
        self.assertIn('BA08足部照護', why)
        self.assertEqual(kind, '資格')

    def test_overlap_and_unknown(self):
        self.assertEqual(plain_reason('服務時間重疊，不能同時接兩案。')[2], '時間')
        self.assertTrue(plain_reason('某種新訊息')[0])


class SummaryTests(unittest.TestCase):
    def test_labels(self):
        e = apply_suggestion(empty_edits(), {'type': 'shift', 'pid': 'p1', 'minutes': 30})
        e = apply_suggestion(e, {'type': 'pattern_worker', 'pid': 'p2', 'worker': 'F5005'})
        lines = edits_summary(e, {'p1': '每週一 12:30～13:20', 'p2': '每週二 13:00～13:50'})
        self.assertIn('每週一 12:30～13:20：後移 30 分鐘', lines[0])
        self.assertIn('每週二 13:00～13:50：整個固定時段改由 F5005 服務', lines[1])

    def test_unlabeled_fallback(self):
        e = apply_suggestion(empty_edits(), {'type': 'shift', 'pid': 'abcdef123', 'minutes': -30})
        self.assertIn('前移 30 分鐘', edits_summary(e)[0])

    def test_notes(self):
        self.assertEqual(len(_plan_notes([{'種類': '時間挪動'}])), 1)
        self.assertEqual(len(_plan_notes([{'種類': '拆成兩段'}])), 2)
        self.assertEqual(_plan_notes([]), [])


class DiagnoseTests(unittest.TestCase):
    def test_required_split_message(self):
        tasks = pd.DataFrame([{'任務ID': 'W1_20261007', '案家ID': 'X', '日期': '2026-10-07', '星期': '星期三', '時間窗_開始': '12:40',
                               '時間窗_結束': '17:40', '服務歷時(分鐘)': 300.0}])
        preview = {'failed': pd.DataFrame([{'任務ID': 'W1_20261007', '日期': '2026-10-07', '原因': '休息不足：連續工作（含轉場）將達 355 分鐘，上限 240 分鐘；'}]),
                   'tasks': tasks, 'pattern_of': {'W1_20261007': 'pid'}}
        (g,) = diagnose(preview, {}, 240)
        self.assertIn('300', g['原因'])
        self.assertIn('拆成兩段', g['方向'])
        self.assertEqual(g['日期'], ['10/7'])
        self.assertEqual(g['時段'], '每週三 12:40～17:40')


class CaseWorkbookTests(unittest.TestCase):
    def test_builder_adds_cases_and_floaters(self):
        import openpyxl, tempfile, os
        from tools.make_cases_workbook import build
        wb = openpyxl.Workbook()
        staff = wb.active; staff.title = '員工資料'
        staff.append(['編號', '性別', '年齡', '交通工具', '取得資格', '休息日', '類別', '經度 (WGS84_Lon)', '緯度 (WGS84_Lat)'])
        staff.append(['M01', '女', '70', '機車', 'x', '週日', None, 121.5, 25.0])
        cl = wb.create_sheet('個案資料')
        cl.append(['內部案號', '性別', '年齡', '核定等級', '收入別', '失能程度', '障礙程度', '身障手冊', '是否失智', '身障證明', '經度 (WGS84_Lon)', '緯度 (WGS84_Lat)', '歷史首選', '歷史備援'])
        cl.append(['A1', '男', 80, 8, '第二類', '重', '重', None, '否', 'x', 121.5, 25.0, 'M01', None])
        wk = wb.create_sheet('週服務計畫')
        wk.append(['狀態', '案號', '星期', '開始時間', '結束時間', '服務時數', '服務分鐘', '費用類型', '頻率', '生效日', '到期日', '服務項目', '經度 (WGS84_Lon)', '緯度 (WGS84_Lat)'])
        wk.append([None, 'A1', '星期一', '09:00:00', '10:00:00', 1, 60, '基本', '每週', '2026-01-01', None, 'BA13(2)', 121.5, 25.0])
        wb.create_sheet('案家姓名對照').append(['案號', '姓名'])
        wb.create_sheet('居服員姓名對照').append(['編號', '姓名'])
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, 's.xlsx'), os.path.join(tmp, 'o.xlsx')
            wb.save(src)
            build(src, out)
            res = openpyxl.load_workbook(out)
            ids = [r[0].value for r in res['員工資料'].iter_rows(min_row=2)]
            self.assertEqual(ids, ['M01', 'M04', 'M05'])
            cases = [r[0].value for r in res['個案資料'].iter_rows(min_row=2)]
            self.assertEqual(cases, ['A1', 'DEMO0003', 'DEMO0004'])
            plan = {r[1].value: r for r in res['週服務計畫'].iter_rows(min_row=2)}
            self.assertEqual(plan['DEMO0003'][3].value, '12:30:00')
            self.assertEqual(plan['DEMO0004'][11].value, 'BA08(1)')


class StampTests(unittest.TestCase):
    def test_modules_carry_v17(self):
        import pathlib
        from build_info import CAREFLOW_BUILD as build, REQUIRED_MODULES
        self.assertEqual(build, CAREFLOW_BUILD)
        here = pathlib.Path(__file__).resolve().parent
        for name in REQUIRED_MODULES + ('build_info',):
            self.assertIn("CAREFLOW_BUILD = '%s'" % build, (here / f'{name}.py').read_text(encoding='utf-8'), name)


if __name__ == '__main__':
    unittest.main()
