"""v14 tests: LINE message parsing, offline geocoding, batch temp-case search, eligibility screen, version stamps."""
CAREFLOW_BUILD = '2026.10.11-v18'
import pathlib, unittest
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
CODES = ['BA14', 'BA13', 'BA09', 'GA09', 'BA15', 'BA17A']
EX1 = ("請問貴單位有人力嗎？ 姓名：劉O 性別/年齡：女性82歲 地址：新北市中和區大智街106巷O號 失能等級：3 失智：X 身障：X 身高/體重：155/62"
       "服務時間及項目：10/2(五)08:20~11:20 BA14+BA133 聯合醫院昆明院區概況簡述：… 備註：1.女性居服員，須注意個案外出就醫安全2.個案有敬老優先權，看診及領藥皆可優先安排")
EX2 = ("請問貴單位有人力嗎？ 姓名：樓O志 性別/年齡：男99歲 地址：新北市中和區景福里和平街O號 失能等級：5 失智：V 身障：V 身高/體重：161/60 "
       "服務時間及項目：9/26(六)10:00~12:00 GA09×1 9/27(日)10:00~12:00 GA09×1 9/28(一)16:00~18:00 GA09×1 概況簡述：… 備註：1.女性居服員，無須協助沐浴 2.可拆單位")


class ParserTests(unittest.TestCase):
    def test_example1(self):
        from dispatch_message import parse_dispatch_message
        p = parse_dispatch_message(EX1, CODES, 2026)
        self.assertEqual(p['client_gender'], '女')
        self.assertEqual((p['age'], p['disability_level'], p['dementia']), (82, 3, False))
        self.assertEqual(len(p['tasks']), 1)
        t = p['tasks'][0]
        self.assertEqual((t['開始時間'], t['結束時間']), ('08:20', '11:20'))
        self.assertEqual((t['Service_Code_1'], t['Service_Code_2']), ('BA14', 'BA13'))
        self.assertEqual(p['caregiver_gender'], '女')
        self.assertTrue(p['escort'])

    def test_example2(self):
        from dispatch_message import parse_dispatch_message
        p = parse_dispatch_message(EX2, CODES, 2026)
        self.assertEqual(len(p['tasks']), 3)
        self.assertEqual([t['開始時間'] for t in p['tasks']], ['10:00', '10:00', '16:00'])
        self.assertTrue(p['dementia'])
        self.assertTrue(p['splittable'] and p['no_bath'])

    def test_redaction_removes_house_number_and_name(self):
        from dispatch_message import redact_message
        r = redact_message('姓名：劉小明 地址：新北市中和區大智街106巷8號 電話0912-345-678')
        self.assertNotIn('小明', r)
        self.assertNotIn('8號', r)
        self.assertNotIn('345', r)


class GeocoderTests(unittest.TestCase):
    def test_masked_lane_and_village(self):
        from address_geocoder import get_geocoder
        g = get_geocoder()
        a = g.lookup('新北市中和區大智街106巷O號')
        self.assertIsNotNone(a.lat)
        self.assertTrue(24.9 < a.lat < 25.1 and 121.4 < a.lon < 121.6)
        self.assertTrue(a.approximate)
        b = g.lookup('新北市中和區景福里和平街O號')
        self.assertIsNotNone(b.lat)
        self.assertLess(abs(b.lat - 24.99), 0.05)

    def test_unknown_address_has_no_coordinates(self):
        from address_geocoder import get_geocoder
        self.assertIsNone(get_geocoder().lookup('新北市中和區不存在路999號').lat)


class StampTests(unittest.TestCase):
    def test_all_required_modules_carry_same_build(self):
        from build_info import CAREFLOW_BUILD as build, REQUIRED_MODULES
        for name in REQUIRED_MODULES + ('build_info',):
            text = (HERE / f'{name}.py').read_text(encoding='utf-8')
            self.assertIn("CAREFLOW_BUILD = '%s'" % build, text, name)

    def test_geo_index_files_shipped(self):
        data = HERE / 'data'
        self.assertTrue(data.is_dir() and any(data.iterdir()))


if __name__ == '__main__':
    unittest.main()
