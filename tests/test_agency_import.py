"""不含真實個資的回歸測試：python -m unittest test_agency_import.py"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
import caregiver_engine as e

class AgencyTests(unittest.TestCase):
    def setUp(self):
        self.provider=patch.object(e,'_get_travel_provider',return_value='local');self.provider.start()
        self.g=pd.DataFrame([{'編號':'CG_A','性別':'女','交通工具':'公車、捷運','取得資格':'失智症照顧服務20小時訓練課程, 照顧服務員單一級技術士',
            '休息日':'週日','經度 (WGS84_Lon)':121.5,'緯度 (WGS84_Lat)':25.0,'長照服務人員到期日':'2027-01-01'}])
        self.c=pd.DataFrame([{'內部案號':'CL_A','經度 (WGS84_Lon)':121.6,'緯度 (WGS84_Lat)':25.1}])
        self.w=pd.DataFrame([{'案號':'CL_A','星期':'星期一','開始時間':'09:00:00','結束時間':'10:00:00','服務分鐘':60,
            '服務項目':'BA20(2) BA07(1) BA17d1(1) SA10(1)','頻率':'每週','生效日':'2026-09-28',
            '經度 (WGS84_Lon)':121.51,'緯度 (WGS84_Lat)':25.01}])
    def tearDown(self):self.provider.stop()
    def load(self,start='2026-10-05',end='2026-10-05',**kwargs):
        return e.load_agency_data(self.g,self.c,self.w,start,end,availability_confirmed=True,**kwargs)
    def merged(self,x):return e.merge_task_client_tables(x['Tasks'],x['Client_Profiles'])
    def test_single_workbook_reads_three_named_sheets(self):
        excel=SimpleNamespace(sheet_names=['員工資料','個案資料','週服務計畫'])
        from unittest.mock import MagicMock
        context=MagicMock();context.__enter__.return_value=excel
        frames={'員工資料':self.g,'個案資料':self.c,'週服務計畫':self.w}
        with patch.object(e.pd,'ExcelFile',return_value=context),patch.object(e.pd,'read_excel',side_effect=lambda xl,sheet_name,**kw:frames[sheet_name]):
            x=e.load_agency_workbook('fake.xlsx','2026-10-05','2026-10-05',availability_confirmed=True)
        self.assertEqual(len(x['Tasks']),1)
    def test_single_workbook_missing_sheet_is_actionable(self):
        from unittest.mock import MagicMock
        context=MagicMock();context.__enter__.return_value=SimpleNamespace(sheet_names=['轉換結果'])
        with patch.object(e.pd,'ExcelFile',return_value=context):
            with self.assertRaisesRegex(ValueError,'員工資料'):
                e.load_agency_workbook('fake.xlsx','2026-10-05','2026-10-05')
    def test_week_expansion_all_codes_and_unknowns(self):
        x=self.load(end='2026-10-12');self.assertEqual(len(x['Tasks']),2)
        self.assertEqual(list(e.iter_service_codes(x['Tasks'].iloc[0])),[('BA20',2.),('BA07',1.),('BA17D1',1.),('SA10',1.)])
        self.assertTrue(x['Caregiver_Profiles']['當月累計服務時數(疲勞度)'].isna().all())
        t=self.merged(x);self.assertEqual(t.iloc[0]['服務地點_經度'],121.51)
        self.assertEqual(t.iloc[0]['特殊照護需求'],'')
    def test_missing_keys_are_reported_without_fake_ids(self):
        self.c.loc[1]= {'內部案號':np.nan}
        self.w.loc[1]=self.w.iloc[0];self.w.loc[1,'案號']=np.nan
        x=self.load();self.assertEqual(len(x['Tasks']),1);self.assertEqual(len(x['Import_Issues']),2)
    def test_duplicate_keys_raise(self):
        self.c.loc[1]=self.c.iloc[0]
        with self.assertRaises(ValueError):self.load()
    def test_no_care_need_inference(self):
        self.c['核定等級']=8
        x=self.load();self.assertTrue(pd.isna(x['Client_Profiles'].iloc[0]['需重度移位協助(0/1)']))
    def test_biweekly_requires_confirmation_and_anchor(self):
        self.w['頻率']='隔週'
        x=self.load(end='2026-10-12');self.assertTrue(x['Tasks'].empty)
        x=self.load(end='2026-10-12',biweekly_anchor_confirmed=True)
        self.assertEqual(x['Tasks']['日期'].tolist(),['2026-10-12'])
    def test_expiry_and_effective_dates(self):
        self.w['生效日']='2026-10-06';self.w['到期日']='2026-10-11'
        self.assertTrue(self.load(end='2026-10-12')['Tasks'].empty)
    def test_invalid_minutes_are_reported(self):
        self.w['服務分鐘']=30
        x=self.load();self.assertTrue(x['Tasks'].empty);self.assertEqual(len(x['Import_Issues']),1)
    def test_unknown_service_suffix_not_truncated(self):
        with self.assertRaises(ValueError):e.parse_service_items('BA20(1) UNKNOWN(2)')
    def test_overlap_records_blocked_not_deleted(self):
        self.w.loc[1]=self.w.iloc[0]
        x=self.load();self.assertEqual(len(x['Tasks']),2)
        self.assertTrue(x['Tasks']['任務資料異常'].ne('').all())
        self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_unknown_fatigue_and_finance_not_zero(self):
        x=self.load();t=self.merged(x);m=e.run_phase1_matching(t,x['Caregiver_Profiles'],e.PipelineConfig())
        self.assertEqual(len(m),1);self.assertTrue(m['當月累計服務時數'].isna().all())
        self.assertTrue(m['實際工時負荷扣分'].isna().all());self.assertEqual(m.iloc[0]['滿意度調整'],0.)
        f=e.calculate_task_revenue_and_salary(t,e.PipelineConfig());self.assertTrue(f['預估長照申報點數(營收)'].isna().all())
        self.assertFalse(e.run_phase3_did(x['Historical_Service_Logs'])['available'])
    def test_supplied_duration_preserved_without_master(self):
        x=self.load();t=e.apply_service_duration(x['Tasks'],x['Service_Code']);self.assertEqual(t.iloc[0]['服務歷時(分鐘)'],60)
    def test_multi_day_leave_partial_boundary(self):
        self.g['類別']='請假';self.g['開始']='2026-10-05';self.g['結束']='2026-10-06';self.g['時間']='09:30 - 10:00'
        x=self.load();t=self.merged(x)
        self.assertTrue(e.run_phase1_matching(t,x['Caregiver_Profiles'],e.PipelineConfig()).empty)
        self.g['時間']='10:00 - 10:00';x=self.load()
        self.assertEqual(len(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig())),1)
    def test_zero_length_leave_blocks_employee(self):
        self.g['類別']='請假';self.g['開始']='2026-10-05';self.g['結束']='2026-10-05';self.g['時間']='13:00 - 13:00'
        x=self.load();self.assertFalse(x['Caregiver_Profiles'].iloc[0]['可服務資料已確認'])
        self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_license_expired_blocks(self):
        self.g['長照服務人員到期日']='2026-10-04'
        x=self.load();self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_rest_day_blocks(self):
        self.g['休息日']='週一'
        x=self.load();self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_multi_certs_and_unknown_heavy_capacity(self):
        x=self.load();self.assertTrue(e.caregiver_has_cert(x['Caregiver_Profiles'].iloc[0],'失智症照顧專長'))
        x['Client_Profiles']['需重度移位協助(0/1)']=1
        self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_missing_coords_blocks_before_api(self):
        self.g['經度 (WGS84_Lon)']=np.nan;x=self.load()
        with patch.object(e.requests,'get',side_effect=AssertionError('Network must not be used')):
            self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_manual_reassignment_cannot_bypass_leave(self):
        self.g['類別']='請假';self.g['開始']='2026-10-05';self.g['結束']='2026-10-05';self.g['時間']='08:00 - 17:00'
        x=self.load();t=self.merged(x)
        reason=e.check_reassignment_conflict(t.iloc[0]['任務ID'],'CG_A',x['Tasks'],pd.DataFrame(),x['Caregiver_Profiles'],e.PipelineConfig(),df_cl=x['Client_Profiles'])
        self.assertIn('請假',reason)
    def test_batch_rolls_plan_hours_without_fabricating_actual(self):
        x=self.load(end='2026-10-12');t=self.merged(x)
        batch=e.run_monthly_batch_dispatch(t,x['Caregiver_Profiles'],e.PipelineConfig())
        self.assertEqual(batch['total_assigned_count'],2)
        self.assertEqual(batch['caregiver_plan_hours'].iloc[0]['本次計畫已排時數'],2.)
        self.assertTrue(x['Caregiver_Profiles']['當月累計服務時數(疲勞度)'].isna().all())
        self.assertEqual(batch['df_result_all'].iloc[1]['本次計畫已排時數'],1.)
        self.assertTrue(batch['df_result_all']['當月累計服務時數'].isna().all())
    def test_default_unconfirmed_no_assignment(self):
        x=e.load_agency_data(self.g,self.c,self.w,'2026-10-05','2026-10-05')
        self.assertTrue(e.run_phase1_matching(self.merged(x),x['Caregiver_Profiles'],e.PipelineConfig()).empty)
    def test_long_range_rejected(self):
        with self.assertRaises(ValueError):self.load(end='2026-12-01')
    def test_local_transit_and_motorcycle_differ(self):
        config=e.PipelineConfig()
        self.assertGreater(e.calc_travel_minutes(25,121.5,25.01,121.51,config,'大眾運輸'),e.calc_travel_minutes(25,121.5,25.01,121.51,config,'機車'))


class BackupCaregiverTests(unittest.TestCase):
    """主責＋固定備援：預設關閉；開啟後僅承接固定人選無法承接的服務。"""
    def setUp(self):
        self.provider=patch.object(e,'_get_travel_provider',return_value='local');self.provider.start()
        self.route=patch.object(e,'get_osrm_travel_time',return_value=5.0);self.route.start()
        base={'性別':'女','交通工具':'機車','取得資格':'照顧服務員單一級技術士','長照服務人員到期日':'2030-01-01','到職日':'2020-01-01'}
        self.g=pd.DataFrame([{**base,'編號':'CG_A','休息日':'週日','經度 (WGS84_Lon)':121.50,'緯度 (WGS84_Lat)':25.00},
                             {**base,'編號':'CG_B','休息日':'週六','經度 (WGS84_Lon)':121.51,'緯度 (WGS84_Lat)':25.01}])
        self.c=pd.DataFrame([{'內部案號':'CL_A','歷史首選':'CG_A','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}])
        plan=lambda day:{'案號':'CL_A','星期':day,'開始時間':'09:00:00','結束時間':'10:00:00','服務分鐘':60,'服務項目':'BA13(2)',
                         '頻率':'每週','生效日':'2026-09-28','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
        self.w=pd.DataFrame([plan('星期一'),plan('星期日')])   # 週日是 CG_A 的休息日
    def tearDown(self):self.provider.stop();self.route.stop()
    def run_period(self,**flags):
        from monthly_scheduler import run_fixed_period_dispatch
        x=e.load_agency_data(self.g,self.c,self.w,'2026-10-05','2026-10-11',availability_confirmed=True)
        config=e.PipelineConfig(**flags)
        return run_fixed_period_dispatch(x['Tasks'],x['Caregiver_Profiles'],x['Client_Profiles'],config),config
    def test_default_off_leaves_rest_day_visit_unassigned(self):
        result,config=self.run_period()
        self.assertFalse(config.enable_backup_caregiver)
        self.assertEqual(len(result['df_result']),1)
        self.assertEqual(len(result['diagnostics']),1)
        self.assertEqual(result['client_backup_map'],{})
    def test_backup_takes_only_what_owner_cannot(self):
        result,_=self.run_period(enable_backup_caregiver=True)
        rows=result['df_result'].set_index('排班日期')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows.loc['2026-10-05','派單居服員'],'CG_A')       # 固定人選照舊
        self.assertEqual(rows.loc['2026-10-11','派單居服員'],'CG_B')       # 休息日改由備援
        self.assertEqual(rows.loc['2026-10-11','排班來源'],'備援居服員')
        self.assertFalse(rows.loc['2026-10-11','是否請假代班'])
        self.assertEqual(rows.loc['2026-10-11','固定居服員ID'],'CG_A')
        self.assertEqual(result['client_backup_map'],{'CL_A':('CG_B',)})
        self.assertTrue(result['diagnostics'].empty)
    def test_primary_guard_allows_only_designated_backup(self):
        from agency_rules import primary_guard
        task={'案家ID':'CL_A','日期':'2026-10-11','時間窗_開始':'09:00','時間窗_結束':'10:00'}
        config=e.PipelineConfig(client_primary_map={'CL_A':'CG_A'},client_backup_map={'CL_A':('CG_B',)})
        self.assertIsNone(primary_guard(task,{'居服員ID':'CG_B'},config))
        self.assertIn('固定居服員',primary_guard(task,{'居服員ID':'CG_C'},config))
    def test_support_only_after_fixed_team_full(self):
        # CL_A 的週日兩次服務：主責 CG_A 週日休息；CG_B、CG_C 各被自己的案家佔掉其中一個時段。
        # 唯一備援（CG_B）只接得了 09:00，14:00 那次才由 CG_C 臨時支援，且 CG_C 不列入固定備援。
        base={'性別':'女','交通工具':'機車','取得資格':'照顧服務員單一級技術士','長照服務人員到期日':'2030-01-01','到職日':'2020-01-01'}
        self.g=pd.DataFrame([{**base,'編號':code,'休息日':rest,'經度 (WGS84_Lon)':121.50+i*.01,'緯度 (WGS84_Lat)':25.00+i*.01}
                             for i,(code,rest) in enumerate([('CG_A','週日'),('CG_B','週六'),('CG_C','週五')])])
        self.c=pd.DataFrame([{'內部案號':client,'歷史首選':owner,'經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
                             for client,owner in [('CL_A','CG_A'),('CL_B','CG_B'),('CL_C','CG_C')]])
        plan=lambda client,start,end:{'案號':client,'星期':'星期日','開始時間':start,'結束時間':end,'服務分鐘':60,'服務項目':'BA13(2)',
                                      '頻率':'每週','生效日':'2026-09-28','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
        self.w=pd.DataFrame([plan('CL_A','09:00:00','10:00:00'),plan('CL_A','14:00:00','15:00:00'),
                             plan('CL_B','14:00:00','15:00:00'),plan('CL_C','09:00:00','10:00:00')])
        result,_=self.run_period(enable_backup_caregiver=True,max_backup_caregivers=1)
        self.assertEqual(len(result['df_result']),3)              # 未開啟臨時支援：維持原行為
        result,config=self.run_period(enable_backup_caregiver=True,max_backup_caregivers=1,enable_support_caregiver=True)
        rows=result['df_result']
        self.assertEqual(len(rows),4)
        support=rows[rows['排班來源'].eq('臨時支援')]
        self.assertEqual(len(support),1)
        self.assertEqual(result['client_backup_map'],{'CL_A':('CG_B',)})
        self.assertEqual(support.iloc[0]['派單居服員'],'CG_C')
        self.assertEqual(support.iloc[0]['服務時段'],'14:00-15:00')
        self.assertEqual(result['task_support_map'],{str(support.iloc[0]['任務ID']):'CG_C'})
        from agency_rules import primary_guard
        task={'案家ID':'CL_A','任務ID':support.iloc[0]['任務ID'],'日期':'2026-10-11','時間窗_開始':'14:00','時間窗_結束':'15:00'}
        self.assertIsNone(primary_guard(task,{'居服員ID':'CG_C'},config))
        self.assertIn('固定居服員',primary_guard({**task,'任務ID':'OTHER'},{'居服員ID':'CG_C'},config))


class FlexibleDispatchTests(unittest.TestCase):
    """時段彈性、Excel「歷史備援」優先、寫回 Excel；預設行為不變。"""
    def setUp(self):
        self.provider=patch.object(e,'_get_travel_provider',return_value='local');self.provider.start()
        self.route=patch.object(e,'get_osrm_travel_time',return_value=5.0);self.route.start()
    def tearDown(self):self.provider.stop();self.route.stop()
    @staticmethod
    def worker(code,rest,lon,lat):
        return {'性別':'女','交通工具':'機車','取得資格':'照顧服務員單一級技術士','長照服務人員到期日':'2030-01-01',
                '到職日':'2020-01-01','編號':code,'休息日':rest,'經度 (WGS84_Lon)':lon,'緯度 (WGS84_Lat)':lat}
    @staticmethod
    def plan(client,day,start,end,minutes):
        return {'案號':client,'星期':day,'開始時間':start,'結束時間':end,'服務分鐘':minutes,'服務項目':'BA13(2)','頻率':'每週',
                '生效日':'2026-09-28','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
    def run_period(self,workers,clients,plans,start='2026-10-05',end='2026-10-11',**flags):
        from monthly_scheduler import run_fixed_period_dispatch
        x=e.load_agency_data(pd.DataFrame(workers),pd.DataFrame(clients),pd.DataFrame(plans),start,end,availability_confirmed=True)
        config=e.PipelineConfig(**flags)
        return run_fixed_period_dispatch(x['Tasks'],x['Caregiver_Profiles'],x['Client_Profiles'],config),x
    def overlap_case(self,**flags):
        workers=[self.worker('CG_A','週日',121.50,25.00)]
        clients=[{'內部案號':c,'歷史首選':'CG_A','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005} for c in ('CL_A','CL_B')]
        plans=[self.plan('CL_A','星期一','09:00:00','10:00:00',60),self.plan('CL_B','星期一','09:00:00','10:00:00',60)]
        return self.run_period(workers,clients,plans,**flags)
    def test_flex_off_by_default_leaves_overlap_unassigned(self):
        result,_=self.overlap_case()
        self.assertEqual(len(result['df_result']),1)
        self.assertTrue(result['time_shifts'].empty)
    def test_time_flex_moves_start_but_keeps_duration(self):
        result,_=self.overlap_case(time_flex_minutes=90)
        self.assertEqual(len(result['df_result']),2)
        shift=result['time_shifts'].iloc[0]
        self.assertEqual(shift['新時段'],'10:15-11:15')           # 同址：只需15分鐘緩衝，最小的15分鐘刻度位移為75
        self.assertEqual(shift['位移(分)'],75)
        moved=result['df_result'].set_index('任務ID').loc[shift['任務ID']]
        self.assertEqual(moved['服務時段'],'10:15-11:15');self.assertEqual(moved['排班來源'],'時段調整')
        adjusted=result['tasks_adjusted'].set_index('任務ID').loc[shift['任務ID']]
        self.assertEqual((adjusted['時間窗_開始'],adjusted['時間窗_結束']),('10:15','11:15'))
        self.assertEqual(adjusted['服務歷時(分鐘)'],60)
        self.assertTrue(result['diagnostics'].empty)
    def test_time_flex_too_small_cannot_help(self):
        result,_=self.overlap_case(time_flex_minutes=60)
        self.assertEqual(len(result['df_result']),1)
    def test_per_task_flex_column_overrides_default(self):
        workers=[self.worker('CG_A','週日',121.50,25.00)]
        clients=[{'內部案號':c,'歷史首選':'CG_A','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005} for c in ('CL_A','CL_B')]
        plans=[self.plan('CL_A','星期一','09:00:00','10:00:00',60),self.plan('CL_B','星期一','09:00:00','10:00:00',60)]
        plans[1]['時間彈性(分)']=90
        result,_=self.run_period(workers,clients,plans)                 # 全域彈性0，只有該列填90
        self.assertEqual(len(result['df_result']),2)
    def test_seeded_backup_from_excel_is_tried_first(self):
        workers=[self.worker('CG_A','週日',121.50,25.00),self.worker('CG_B','週六',121.60,25.10),self.worker('CG_C','週六',121.506,25.006)]
        plans=[self.plan('CL_A','星期一','09:00:00','10:00:00',60),self.plan('CL_A','星期日','09:00:00','10:00:00',60)]
        base={'內部案號':'CL_A','歷史首選':'CG_A','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
        # 代班順序看交通時間區段：這個測試讓交通依直線距離變化（每公里 3 分），而不是全部 5 分鐘。
        self.route.stop()
        self.route=patch.object(e,'get_osrm_travel_time',side_effect=lambda a,b,c,d,*x,**k:max(3.0,e._distance_m(a,b,c,d)/1000*3.0));self.route.start()
        plain,_=self.run_period(workers,[base],plans,enable_backup_caregiver=True)
        self.assertEqual(plain['client_backup_map'],{'CL_A':('CG_C',)})     # 無名單：選交通最近的（順路區段）
        seeded,_=self.run_period(workers,[{**base,'歷史備援':'CG_B'}],plans,enable_backup_caregiver=True)
        self.assertEqual(seeded['client_backup_map'],{'CL_A':('CG_B',)})    # 有名單：先試名單上的人
    def test_default_results_unchanged_by_new_options(self):
        result,_=self.overlap_case()
        self.assertEqual(result['client_backup_map'],{})
        self.assertEqual(result['dispatch_stats'].get('time_shifted_services'),0)
    def test_workbook_writes_owner_and_backup_columns(self):
        import io,openpyxl
        from source_workbook import update_source_workbook
        wb=openpyxl.Workbook();ws=wb.active;ws.title='個案資料';ws.append(['內部案號','性別']);ws.append(['C1','女']);ws.append(['C2','男'])
        wb.create_sheet('週服務計畫').append(['案號','開始時間','結束時間']);buf=io.BytesIO();wb.save(buf)
        out=update_source_workbook(buf.getvalue(),{'C1':'W1'},None,{'C1':'W2、W3','C2':'W9'})
        sheet=openpyxl.load_workbook(io.BytesIO(out))['個案資料']
        self.assertEqual([c.value for c in sheet[1]],['內部案號','性別','歷史首選','歷史備援'])
        self.assertEqual([c.value for c in sheet[2]],['C1','女','W1','W2、W3'])
        self.assertEqual([c.value for c in sheet[3]],['C2','男',None,'W9'])
    def test_only_uniform_whole_row_shifts_are_written_back(self):
        from schedule_assist import weekly_changes_from_shifts
        tasks=pd.DataFrame({'任務ID':['a1','a2','b1','b2'],'來源列':[2,2,3,3]})
        shifts=pd.DataFrame([{'來源列':2,'新開始':'10:30','新結束':'11:30'},{'來源列':2,'新開始':'10:30','新結束':'11:30'},
                             {'來源列':3,'新開始':'10:30','新結束':'11:30'}])
        changes,skipped=weekly_changes_from_shifts(shifts,tasks)
        self.assertEqual(changes,{2:{'開始時間':'10:30','結束時間':'11:30'}})
        self.assertEqual(skipped['來源列'].tolist(),[3])
    def test_memoized_weekday_and_date_helpers_stay_correct(self):
        for _ in range(2):
            self.assertEqual(e.get_weekday_name('2026-10-05'),'星期一')
            self.assertEqual(e.get_weekday_name(np.nan),'')
            self.assertEqual(e._date('2026-10-05').isoformat(),'2026-10-05')
            self.assertIsNone(e._date(''))

if __name__=='__main__':unittest.main()

class PrimaryOptimizerTests(unittest.TestCase):
    """歷史首選最佳化：模擬規則與引擎一致；超載的首選會被搬給有空的居服員。"""
    def setUp(self):
        self.provider=patch.object(e,'_get_travel_provider',return_value='local');self.provider.start()
        self.route=patch.object(e,'get_osrm_travel_time',return_value=5.0);self.route.start()
        base={'性別':'女','交通工具':'機車','取得資格':'照顧服務員單一級技術士','長照服務人員到期日':'2030-01-01','到職日':'2020-01-01','休息日':'週日'}
        self.g=pd.DataFrame([{**base,'編號':'CG_A','經度 (WGS84_Lon)':121.50,'緯度 (WGS84_Lat)':25.00},
                             {**base,'編號':'CG_B','經度 (WGS84_Lon)':121.51,'緯度 (WGS84_Lat)':25.01}])
        self.c=pd.DataFrame([{'內部案號':'CL_A','歷史首選':'CG_A','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005},
                             {'內部案號':'CL_B','歷史首選':'CG_A','經度 (WGS84_Lon)':121.506,'緯度 (WGS84_Lat)':25.006}])
        plan=lambda case:{'案號':case,'星期':'星期一','開始時間':'09:00:00','結束時間':'10:00:00','服務分鐘':60,'服務項目':'BA13(2)',
                          '頻率':'每週','生效日':'2026-09-28','經度 (WGS84_Lon)':121.505,'緯度 (WGS84_Lat)':25.005}
        self.w=pd.DataFrame([plan('CL_A'),plan('CL_B')])
    def tearDown(self):self.provider.stop();self.route.stop()
    def model(self):
        from primary_optimizer import PrimaryModel
        x=e.load_agency_data(self.g,self.c,self.w,'2026-10-05','2026-10-11',availability_confirmed=True)
        tasks=e.merge_task_client_tables(x['Tasks'],x['Client_Profiles'])
        return PrimaryModel(tasks,x['Caregiver_Profiles'],e.PipelineConfig(),lambda a,b,c,d,m:5.0)
    def test_day_error_matches_shared_workload_rules(self):
        import random
        from datetime import datetime,timedelta
        from schedule_checks import workload_error
        from primary_optimizer import day_error
        rnd=random.Random(3);base=datetime(2026,10,5)
        for _ in range(400):
            n=rnd.randint(1,5);t=rnd.randint(480,600);blocks=[]
            for _ in range(n):
                dur=rnd.choice([30,45,60,90,120]);blocks.append((t,t+dur));t+=dur+rnd.choice([0,10,20,30,45,60])
            transfers=[rnd.choice([0,5,15,25]) for _ in range(n-1)]
            slots=[(base+timedelta(minutes=a),base+timedelta(minutes=b)) for a,b in blocks]
            expected=workload_error(slots,8,0,240,30,{i+1:v for i,v in enumerate(transfers)})
            self.assertEqual(bool(day_error(blocks,480,transfers,240,30)),bool(expected),(blocks,transfers))
    def test_two_clients_same_slot_cannot_share_one_primary(self):
        from primary_optimizer import evaluate
        model=self.model()
        served,total,_=evaluate(model,{'CL_A':'CG_A','CL_B':'CG_A'})
        self.assertEqual((served,total),(1,2))
    def test_optimizer_moves_overloaded_client_and_engine_agrees(self):
        from primary_optimizer import lns_optimize,evaluate
        from monthly_scheduler import run_fixed_period_dispatch
        model=self.model()
        best,_=lns_optimize(model,{'CL_A':'CG_A','CL_B':'CG_A'},seconds=2,keep_bonus=0.3)
        self.assertEqual(evaluate(model,best)[:2],(2,2))
        self.assertEqual(len({best['CL_A'],best['CL_B']}),2)
        clients=self.c.copy();clients['歷史首選']=clients['內部案號'].map(best)
        x=e.load_agency_data(self.g,clients,self.w,'2026-10-05','2026-10-11',availability_confirmed=True)
        result=run_fixed_period_dispatch(x['Tasks'],x['Caregiver_Profiles'],x['Client_Profiles'],e.PipelineConfig())
        self.assertEqual(len(result['df_result']),2)
    def test_protected_clients_are_not_moved(self):
        from primary_optimizer import lns_optimize
        model=self.model()
        best,_=lns_optimize(model,{'CL_A':'CG_A','CL_B':'CG_A'},seconds=1,protected={'CL_A','CL_B'})
        self.assertEqual(best,{'CL_A':'CG_A','CL_B':'CG_A'})


class PolicyTests(unittest.TestCase):
    def master(self):
        return pd.DataFrame([{'系統代碼':'BA20','CareFlow排班分鐘(暫定)':30,'是否納入CareFlow':'是','必要證照':''},
          {'系統代碼':'BA08','CareFlow排班分鐘(暫定)':20,'是否納入CareFlow':'是','必要證照':'BA08足部照護'}])
    def task(self):
        return pd.DataFrame([{'任務ID':'T','Service_Code_1':'BA20','Units_1':2,'服務歷時(分鐘)':40,
          '服務歷時來源':'機構服務分鐘／起迄時間相符','使用Service_code重算':True,'時間窗_開始':'09:00','時間窗_結束':'09:40'}])
    def test_master_changes_duration_and_end_time(self):
        m=self.master();t=e.apply_service_duration(self.task(),m)
        self.assertEqual(t.iloc[0]['服務歷時(分鐘)'],60);self.assertEqual(t.iloc[0]['時間窗_結束'],'10:00')
        m.loc[0,'CareFlow排班分鐘(暫定)']=20
        t=e.apply_service_duration(t,m);self.assertEqual(t.iloc[0]['服務歷時(分鐘)'],40);self.assertEqual(t.iloc[0]['時間窗_結束'],'09:40')
    def test_all_codes_and_required_certs(self):
        t=self.task();t['Service_Code_2']='BA08';t['Units_2']=1
        r=e.apply_service_duration(t,self.master()).iloc[0]
        self.assertEqual(r['服務歷時(分鐘)'],80);self.assertEqual(r['服務碼必要證照'],'BA08足部照護')
    def test_missing_and_negative_minutes_rejected(self):
        for value in [np.nan,-5,0]:
            m=self.master();m.loc[0,'CareFlow排班分鐘(暫定)']=value
            with self.assertRaises(ValueError):e.apply_service_duration(self.task(),m)
    def test_difficulty_not_double_counted_or_diagnosis(self):
        from care_policy import difficulty_reference
        r=difficulty_reference({'核定等級':8,'失能程度':'重','障礙程度':'極重','身障手冊':'有','身障證明':'第01類,第07類'})
        self.assertEqual(r['照護難度參考係數'],1.7)
        self.assertIn('移動',r['照護提示']);self.assertNotIn('失智',r['照護提示'])
        self.assertEqual(difficulty_reference({'身障手冊':'有'})['照護難度參考係數'],1.)
    def test_special_training_not_replaced_by_general_cert(self):
        self.assertFalse(e.caregiver_has_cert({'取得資格':'照顧服務員單一級技術士'},'BA08足部照護'))
        self.assertTrue(e.caregiver_has_cert({'取得資格':'BA08足部照護'},'BA08足部照護'))
        self.assertFalse(e.caregiver_has_cert({'取得資格':'BA17d1'},'BA17A抽吸訓練'))

class RegistrationTests(unittest.TestCase):
    def test_dementia_requires_correct_training_text(self):
        from care_policy import special_qualification_error
        task={'失智症個案(0/1)':1}
        self.assertIn('缺特殊訓練',special_qualification_error(task,{'取得資格':'精神疾病照顧專長'},set()))
        self.assertIsNone(special_qualification_error(task,{'取得資格':'失智症照顧服務20小時訓練課程'},set()))
    def test_ba08_text_sufficient_without_registration(self):
        from care_policy import special_qualification_error
        self.assertIsNone(special_qualification_error({}, {'取得資格':'BA08足部照護'}, {'BA08足部照護'}))
        self.assertIn('缺特殊訓練',special_qualification_error({}, {'取得資格':'照顧服務員單一級技術士'}, {'BA08足部照護'}))
    def test_disability_category_not_dementia(self):
        from care_policy import dementia_status
        self.assertIsNone(dementia_status({'身障證明':'第01類'}))
        self.assertFalse(dementia_status({'失智症個案(0/1)':0}))
    def test_attachment_master_and_missing_sa10(self):
        from unittest.mock import MagicMock
        fixture=AgencyTests();fixture.setUp()
        self.addCleanup(fixture.tearDown)
        master_frame=pd.DataFrame([{'系統代碼':'BA08','CareFlow排班分鐘(暫定)':50,'是否納入CareFlow':'視機構'}])
        frames={'員工資料':fixture.g,'個案資料':fixture.c,'週服務計畫':fixture.w,'Service_code':master_frame}
        context=MagicMock();context.__enter__.return_value=SimpleNamespace(sheet_names=list(frames))
        with patch.object(e.pd,'ExcelFile',return_value=context),patch.object(e.pd,'read_excel',side_effect=lambda xl,sheet_name,**kw:frames[sheet_name].copy()):
            x=e.load_agency_workbook('fake.xlsx','2026-10-05','2026-10-11',availability_confirmed=True,biweekly_anchor_confirmed=True)
        master=x['Service_Code'].set_index('系統代碼')
        self.assertEqual(master.loc['BA08','CareFlow排班分鐘(暫定)'],50)
        self.assertEqual(master.loc['BA08','必要證照'],'BA08足部照護')
        self.assertIn('SA10',' '.join(x['Import_Issues']['問題']))
        self.assertNotIn('BA08訓練登錄確認(0/1)',x['Caregiver_Profiles'])

class WorkloadTests(unittest.TestCase):
    def test_hours_and_weighted_modes_change_penalty(self):
        fixture=AgencyTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        x=fixture.load();g=x['Caregiver_Profiles'];g['本次計畫已排時數']=20.;g['本次計畫加權負荷時數']=30.
        t=fixture.merged(x)
        regular=e.run_phase1_matching(t,g,e.PipelineConfig(plan_load_mode='hours',fatigue_reference_hours=40,fatigue_weight=10))
        weighted=e.run_phase1_matching(t,g,e.PipelineConfig(plan_load_mode='weighted',fatigue_reference_hours=40,fatigue_weight=10))
        self.assertEqual(regular.iloc[0]['本次計畫負荷扣分'],5.)
        self.assertEqual(weighted.iloc[0]['本次計畫負荷扣分'],7.5)


class FinanceTests(unittest.TestCase):
    def test_master_prices_quantities_suffix_and_salary(self):
        master=pd.DataFrame({'系統代碼':['BA20','BA17d1','AA01'], '目前給付價格(元)':['1,200',50,0]})
        config=e.PipelineConfig(service_unit_prices=e.service_price_map(master))
        tasks=pd.DataFrame([{'資料來源':'機構三檔','Service_Code_1':'BA20','Units_1':2,
            'Service_Code_2':'BA17D1','Units_2':3,'Service_Code_3':'AA01','Units_3':1}])
        result=e.calculate_task_revenue_and_salary(tasks,config)
        self.assertEqual(result.iloc[0]['預估長照申報點數(營收)'],2550)
        self.assertEqual(result.iloc[0]['預估居服員拆帳薪資'],1657.5)
        config.caregiver_salary_rate_per_point=.7
        self.assertEqual(e.calculate_task_revenue_and_salary(tasks,config).iloc[0]['預估居服員拆帳薪資'],1785)
        config.service_unit_prices['BA20']=1300
        self.assertEqual(e.calculate_task_revenue_and_salary(tasks,config).iloc[0]['預估長照申報點數(營收)'],2750)
    def test_missing_self_pay_is_not_zero_or_old_price(self):
        config=e.PipelineConfig(service_unit_prices={'BA20':100})
        tasks=pd.DataFrame([{'資料來源':'機構三檔','Service_Code_1':'BA20','Units_1':1,'Service_Code_2':'SA10','Units_2':1}])
        result=e.calculate_task_revenue_and_salary(tasks,config)
        self.assertTrue(pd.isna(result.iloc[0]['預估長照申報點數(營收)']))
        self.assertIn('SA10',result.iloc[0]['財務估算狀態'])
    def test_invalid_and_duplicate_price_require_correction(self):
        master=pd.DataFrame({'系統代碼':['BA20','ba20','BA07','BA08','AA01'], '目前給付價格(元)':[10,20,-1,'待確認',0]})
        self.assertEqual(e.service_price_map(master),{'AA01':0})
    def test_batch_progress_finishes_and_keeps_input_unchanged(self):
        fixture=AgencyTests();fixture.setUp()
        try:
            x=fixture.load(end='2026-10-12');tasks=fixture.merged(x);events=[]
            result=e.run_monthly_batch_dispatch(tasks,x['Caregiver_Profiles'],e.PipelineConfig(),progress_callback=lambda *event: events.append(event))
            self.assertEqual(events[-1],(2,2,None))
            self.assertEqual(len(result['daily_results']),2)
            self.assertEqual(x['Caregiver_Profiles'].iloc[0]['本次計畫已排時數'],0.0)
        finally:fixture.tearDown()

class ScheduleTravelTests(unittest.TestCase):
    def fixture(self):
        tasks=pd.DataFrame([{'任務ID':key,'案家ID':key,'日期':day,'時間窗_開始':start,'服務地點_緯度':25.0,'服務地點_經度':lon} for key,day,start,lon in [('A','2026-10-05','09:00',121.1),('B','2026-10-05','11:00',121.2),('C','2026-10-05','15:00',121.3),('D','2026-10-06','09:00',121.4)]])
        caregivers=pd.DataFrame([{'居服員ID':cg,'服務起點_緯度(家)':25.,'服務起點_經度(家)':lon,'常用交通工具':'機車'} for cg,lon in [('G1',121.),('G2',122.)]])
        result=pd.DataFrame([{'任務ID':key,'派單居服員':'G1','預估車程(分)':99} for key in ['C','B','A','D']])
        return result,tasks,caregivers
    def test_service_order_and_daily_reset(self):
        result,tasks,cg=self.fixture();calls=[]
        def route(*args,**kwargs):calls.append(args[:4]);return 10
        with patch.object(e,'calc_travel_minutes',side_effect=route):
            out=e.calculate_schedule_travel(result,tasks,cg,e.PipelineConfig(buffer_mins=15))
        self.assertEqual(calls,[(25.,121.,25.,121.1),(25.,121.1,25.,121.2),(25.,121.2,25.,121.3),(25.,121.,25.,121.4)])
        self.assertEqual(out.set_index('任務ID').loc['A','路段緩衝(分)'],0)
        self.assertEqual(out.set_index('任務ID').loc['B','路段緩衝(分)'],15)
        daily=e.summarize_schedule_travel(out)
        self.assertEqual(daily.iloc[0]['交通合計(分)'],30)
        self.assertEqual(daily.iloc[0]['含緩衝合計(分)'],60)
        self.assertTrue(out['配對起點車程(分)'].eq(99).all())
        self.assertTrue(result['預估車程(分)'].eq(99).all())
    def test_cancel_middle_and_reassign_reset_origin(self):
        result,tasks,cg=self.fixture();result=result[result['任務ID'].ne('B')].copy()
        result.loc[result['任務ID'].eq('C'),'派單居服員']='G2';calls=[]
        with patch.object(e,'calc_travel_minutes',side_effect=lambda *args,**kwargs: calls.append(args[:4]) or 5):
            e.calculate_schedule_travel(result,tasks,cg,e.PipelineConfig())
        self.assertIn((25.,122.,25.,121.3),calls)
        result['派單居服員']='G1';calls=[]
        with patch.object(e,'calc_travel_minutes',side_effect=lambda *args,**kwargs: calls.append(args[:4]) or 5):
            e.calculate_schedule_travel(result,tasks,cg,e.PipelineConfig())
        self.assertIn((25.,121.1,25.,121.3),calls)
    def test_missing_coordinates_do_not_become_zero(self):
        result,tasks,cg=self.fixture();tasks.loc[tasks['任務ID'].eq('B'),'服務地點_經度']=np.nan
        with patch.object(e,'calc_travel_minutes',return_value=5):out=e.calculate_schedule_travel(result,tasks,cg,e.PipelineConfig())
        self.assertTrue(out.set_index('任務ID').loc[['B','C'],'預估車程(分)'].isna().all())
        daily=e.summarize_schedule_travel(out)
        self.assertTrue(pd.isna(daily.iloc[0]['交通合計(分)']))
        self.assertEqual(daily.iloc[0]['缺資料路段數'],2)

class OsrmMotorcycleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.cache_dir=tempfile.mkdtemp()   # 每個測試用自己的硬碟快取，不會讀到上一次測試寫的路段
        self.environment=patch.dict(e.os.environ,{'CAREFLOW_OSRM_MOTORCYCLE_HOST':'http://motor.test:5001','CAREFLOW_CACHE_DIR':self.cache_dir})
        self.environment.start();e._OSRM_TRAVEL_TIME_CACHE.clear()
    def tearDown(self):self.environment.stop();e._OSRM_TRAVEL_TIME_CACHE.clear()
    def response(self,data):
        from unittest.mock import Mock
        response=Mock();response.json.return_value=data;return response
    def test_motorcycle_routes_cache_and_removed_mode(self):
        with patch.object(e.requests,'get',return_value=self.response({'code':'Ok','routes':[{'duration':120}]})) as get:
            self.assertEqual(e.get_osrm_travel_time(25,121,25.1,121.1,transport_mode='機車'),2)
            self.assertIn('http://motor.test:5001/route/v1/motorcycle/',get.call_args.args[0])
            e.get_osrm_travel_time(25,121,25.1,121.1,transport_mode='機車')
            self.assertEqual(get.call_count,1)
            with self.assertRaisesRegex(ValueError,'僅支援機車'):
                e.get_osrm_travel_time(25,121,25.1,121.1,transport_mode='汽車')
            self.assertEqual(get.call_count,1)
    def test_osrm_dispatch_preserves_employee_transport(self):
        with patch.object(e,'_get_travel_provider',return_value='osrm'),patch.object(e,'_use_google_routes',return_value=False),patch.object(e,'get_osrm_travel_time',return_value=3) as route:
            self.assertEqual(e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),transport_mode='機車'),3)
            self.assertEqual(route.call_args.kwargs['transport_mode'],'機車')
    def test_connection_failure_does_not_use_car_or_straight_line(self):
        with patch.object(e.requests,'get',side_effect=e.requests.ConnectionError('offline')) as get:
            with self.assertRaisesRegex(RuntimeError,'機車.*未使用汽車或直線'):
                e.get_osrm_travel_time(25,121,25.1,121.1)
            self.assertEqual(get.call_count,1)
        self.assertFalse(e._OSRM_TRAVEL_TIME_CACHE)
    def test_table_populates_motor_cache_only(self):
        # 一次矩陣請求同時拿回去程與回程（2×2 矩陣），之後兩個方向都不必再查。
        with patch.object(e,'_get_travel_provider',return_value='osrm'),patch.object(e.requests,'get',return_value=self.response({'code':'Ok','durations':[[0,180],[240,0]]})) as get:
            e.prefetch_osrm_travel_times([(25,121)],[(25.1,121.1)],transport_mode='機車')
            self.assertIn('/table/v1/motorcycle/',get.call_args.args[0])
            self.assertEqual(e.get_osrm_travel_time(25,121,25.1,121.1),3)
            self.assertEqual(e.get_osrm_travel_time(25.1,121.1,25,121),4)
            self.assertEqual(get.call_count,1)
            self.assertEqual(len([k for k in e._OSRM_TRAVEL_TIME_CACHE if k[-4:-2]!=k[-2:]]),2)
    def test_transit_is_not_car_routing(self):
        with self.assertRaisesRegex(ValueError,'公車'):
            e.get_osrm_travel_time(25,121,25.1,121.1,transport_mode='大眾運輸')
    def test_changing_host_invalidates_cache(self):
        with patch.object(e.requests,'get',return_value=self.response({'code':'Ok','routes':[{'duration':120}]})) as get:
            e.get_osrm_travel_time(25,121,25.1,121.1)
            with patch.dict(e.os.environ,{'CAREFLOW_OSRM_MOTORCYCLE_HOST':'http://new-motor.test:5001'}):
                e.get_osrm_travel_time(25,121,25.1,121.1)
            self.assertEqual(get.call_count,2)

    def test_supported_modes_are_motorcycle_and_transit(self):
        self.assertEqual(e.GOOGLE_TRAVEL_MODE_MAP,{'機車':'TWO_WHEELER','大眾運輸':'TRANSIT'})
        with patch.object(e,'_get_travel_provider',return_value='local'):
            with self.assertRaisesRegex(ValueError,'僅支援機車或公車'):
                e.get_google_travel_time(25,121,25.1,121.1,'汽車')

class HybridTravelTests(unittest.TestCase):
    def setUp(self):
        e.begin_travel_query_run(100)
        self.provider=patch.object(e,'_get_travel_provider',return_value='hybrid');self.provider.start()
        self.key=patch.object(e,'_get_google_maps_api_key',return_value='test-key');self.key.start()
    def tearDown(self):
        self.provider.stop();self.key.stop();e.begin_travel_query_run()
    def response(self,**changes):
        from unittest.mock import Mock
        r=Mock(ok=True,status_code=200)
        row={'status':{},'condition':'ROUTE_EXISTS','duration':'1200s'};row.update(changes)
        r.json.return_value=[row];return r
    def indexed_response(self):
        response=self.response();response.json.return_value=[{**response.json.return_value[0],'originIndex':0,'destinationIndex':0}]
        return response
    def test_same_weekday_and_clock_share_one_transit_query(self):
        """同星期、同時刻的大眾運輸查詢，一次排班內只打一次 Google；不同時刻或不同星期仍各自查詢。"""
        with patch.object(e.requests,'post',return_value=self.indexed_response()) as post:
            for day in ('2026-10-05','2026-10-12','2026-10-19'):   # 三個星期一
                self.assertEqual(e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time=day+' 10:15'),20)
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.kwargs['json']['departureTime'],'2026-10-05T10:15:00+08:00')
            e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time='2026-10-06 10:15')  # 星期二
            e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time='2026-10-12 11:15')  # 同星期不同時刻
            self.assertEqual(post.call_count,3)
    def test_weekday_reuse_can_be_turned_off(self):
        with patch.object(e,'GOOGLE_TRANSIT_REUSE_WEEKDAY',False),patch.object(e.requests,'post',return_value=self.indexed_response()) as post:
            for day in ('2026-10-05','2026-10-12'):
                e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time=day+' 10:15')
            self.assertEqual(post.call_count,2)
    def test_mixed_mode_routes_and_timed_query_memo(self):
        with patch.object(e,'get_osrm_travel_time',return_value=3) as motor,patch.object(e.requests,'post',return_value=self.response()) as post:
            self.assertEqual(e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'機車'),3)
            self.assertEqual(post.call_count,0)
            for _ in range(2):
                self.assertEqual(e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time='2026-10-05 10:15'),20)
            self.assertEqual(post.call_count,1)
            body=post.call_args.kwargs['json']
            self.assertEqual(body['travelMode'],'TRANSIT')
            self.assertEqual(body['departureTime'],'2026-10-05T10:15:00+08:00')
            self.assertNotIn('routingPreference',body)
            e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸',departure_time='2026-10-05 11:15')
            self.assertEqual(post.call_count,2)
            self.assertEqual(motor.call_count,1)
    def test_first_leg_arrival_and_next_leg_departure(self):
        result,tasks,cg=ScheduleTravelTests().fixture()
        tasks['時間窗_結束']=tasks['時間窗_開始'].map(lambda t:(pd.Timestamp('2000-01-01 '+t)+pd.Timedelta(hours=1)).strftime('%H:%M'))
        cg['常用交通工具']='大眾運輸'
        with patch.object(e.requests,'post',return_value=self.response()) as post:
            out=e.calculate_schedule_travel(result,tasks,cg,e.PipelineConfig(buffer_mins=15))
        bodies=[c.kwargs['json'] for c in post.call_args_list]
        # 路段會批次平行預抓，請求順序不固定：首案用抵達時間，次案用「前案結束＋緩衝」出發。
        self.assertTrue(any('arrivalTime' in b and b['arrivalTime'].endswith('09:00:00+08:00') for b in bodies))
        self.assertTrue(any(b.get('departureTime','').endswith('10:15:00+08:00') for b in bodies))
        self.assertTrue(out['交通估算來源'].str.contains('Google Maps').all())
    def test_missing_key_errors_without_osrm_fallback(self):
        with patch.object(e,'_get_google_maps_api_key',return_value=None),patch.object(e,'get_osrm_travel_time') as motor:
            with self.assertRaisesRegex(RuntimeError,'secrets.toml'):
                e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸')
            motor.assert_not_called()
    def test_no_route_http_status_and_budget_never_fallback(self):
        from unittest.mock import Mock
        for response in [self.response(condition='ROUTE_NOT_FOUND'),self.response(status={'code':7}),Mock(ok=False,status_code=403)]:
            e.begin_travel_query_run(100)
            with patch.object(e.requests,'post',return_value=response),patch.object(e,'get_osrm_travel_time') as motor:
                with self.assertRaisesRegex(RuntimeError,'未改用機車或直線'):
                    e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸')
                motor.assert_not_called()
        e.begin_travel_query_run(1)
        with patch.object(e.requests,'post',return_value=self.response()) as post:
            e.calc_travel_minutes(25,121,25.1,121.1,e.PipelineConfig(),'大眾運輸')
            with self.assertRaisesRegex(RuntimeError,'查詢上限'):
                e.calc_travel_minutes(25,121,25.2,121.2,e.PipelineConfig(),'大眾運輸')
            self.assertEqual(post.call_count,1)
    def test_more_than_ten_queries_and_new_batch_reset(self):
        with patch.object(e.requests,'post',return_value=self.response()) as post:
            for n in range(12):
                self.assertEqual(e.calc_travel_minutes(25,121,25+n/1000,121.1,e.PipelineConfig(),'大眾運輸'),20)
            self.assertEqual(post.call_count,12)
            e.begin_travel_query_run()
            e.calc_travel_minutes(25,121,25.001,121.1,e.PipelineConfig(),'大眾運輸')
            self.assertEqual(post.call_count,13)
    def test_reverse_interval_uses_earlier_task_end(self):
        a=(pd.Timestamp('2000-01-01 11:00'),pd.Timestamp('2000-01-01 12:00'),25,121.2)
        b=(pd.Timestamp('2000-01-01 09:00'),pd.Timestamp('2000-01-01 10:00'),25,121.1)
        with patch.object(e.requests,'post',return_value=self.response()) as post:
            e._interval_travel(a,b,e.PipelineConfig(buffer_mins=15),'大眾運輸',{'日期':'2026-10-05'})
        body=post.call_args.kwargs['json']
        self.assertEqual(body['origins'][0]['waypoint']['location']['latLng']['longitude'],121.1)
        self.assertEqual(body['departureTime'],'2026-10-05T10:15:00+08:00')
    def test_existing_secrets_key_and_environment_override(self):
        self.key.stop()
        try:
            with patch.dict(e.os.environ,{},clear=True),patch.object(e.st,'secrets',{'GOOGLE_MAPS_API_KEY':'secrets-test'}):
                self.assertTrue(e.google_key_is_configured())
                self.assertEqual(e._get_google_maps_api_key(),'secrets-test')
            with patch.dict(e.os.environ,{'GOOGLE_MAPS_API_KEY':'env-test'}),patch.object(e.st,'secrets',{}):
                self.assertEqual(e._get_google_maps_api_key(),'env-test')
        finally:
            self.key.start()
    def test_overlapping_intervals_do_not_call_paid_api(self):
        a=(pd.Timestamp('2000-01-01 09:00'),pd.Timestamp('2000-01-01 10:00'),25,121)
        b=(pd.Timestamp('2000-01-01 09:30'),pd.Timestamp('2000-01-01 10:30'),25,121.1)
        with patch.object(e.requests,'post') as post:
            self.assertEqual(e._interval_travel(a,b,e.PipelineConfig(),'大眾運輸',{'日期':'2026-10-05'}),float('inf'))
            post.assert_not_called()
