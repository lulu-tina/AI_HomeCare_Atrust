import json
from io import BytesIO

import pandas as pd
import pytest

import caregiver_engine as engine
from agency_rules import consecutive_workdays_error, daily_pay, leave_overlap, payroll_tables, primary_guard
from dispatch_workflow import WorkflowStore, attach_blocks
from monthly_scheduler import run_fixed_period_dispatch
from schedule_checks import workload_error
from schedule_output import schedule_excel_bytes


@pytest.fixture(autouse=True)
def mocked_routes(monkeypatch):
    # No real coordinates or API credentials are sent by the regression suite.
    monkeypatch.setattr(engine, 'calc_travel_minutes', lambda *a, **k: 5.0)
    monkeypatch.setattr(engine, 'prefetch_osrm_travel_times', lambda *a, **k: None)
    monkeypatch.setattr(engine, 'prefetch_google_transit_pairs', lambda *a, **k: None)
    import monthly_scheduler
    monkeypatch.setattr(monthly_scheduler, 'prefetch_osrm_travel_times', lambda *a, **k: None)


def worker(identifier='A', **values):
    return {'居服員ID': identifier, '性別': '女', '常用交通工具': '機車',
            '取得資格': '失智症照顧服務20小時', '核心專長證照': '失智症照顧專長',
            '服務起點_緯度(家)': 25.03, '服務起點_經度(家)': 121.53,
            '每日工時上限(小時)': 12.0, '每日可服務時段_起': '00:00', '每日可服務時段_迄': '23:59',
            '可排班星期': '1,2,3,4,5,6,7', '可服務資料已確認': True,
            '當月累計服務時數(疲勞度)': 0.0, '歷史滿意度均值': 4.5,
            '特殊排斥條件': '', '請假紀錄': None, **values}


def task(identifier='t1', client='C', day='2026-11-02', start='09:00', end='10:00', **values):
    duration = (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60
    return {'任務ID': identifier, '案家ID': client, '日期': day,
            '星期': engine.get_weekday_name(day), '時間窗_開始': start, '時間窗_結束': end,
            '服務歷時(分鐘)': duration, 'Service_Code_1': 'SA10', 'Units_1': duration / 60,
            '服務地點_緯度': 25.04, '服務地點_經度': 121.54, '任務優先級': '一般',
            '指定居服員性別': '', '特殊照護需求': '', '需重度移位協助(0/1)': 0,
            '案家環境特徵': '', '歷史首選居服員ID': '', '資料來源': '機構三檔', **values}


def config(people=None, **values):
    values.setdefault('continuity_policy','strict')
    cfg = engine.PipelineConfig(**values)
    cfg.primary_profiles = {p['居服員ID']: p for p in (people or [worker(), worker('B')])}
    return cfg


def check(tasks, target, person='A', assigned=None, cfg=None, people=None):
    people = people or [worker(), worker('B')]
    assigned = assigned if assigned is not None else pd.DataFrame(columns=['任務ID', '派單居服員'])
    return engine._evaluate_reassignment(target, person, pd.DataFrame(tasks), assigned,
                                         pd.DataFrame(people), cfg or config(people))


@pytest.mark.parametrize('day,start', [('2026-11-02','09:00'), ('2026-11-04','15:00'), ('2026-11-07','18:00')])
def test_fixed_owner_covers_all_days_and_times(day, start):
    cfg=config(client_primary_map={'C':'A'})
    assert primary_guard(task(day=day,start=start,end=f'{int(start[:2])+1:02}:00'),worker('B'),cfg)


def test_repeated_client_merge_has_no_duplicate_columns():
    tasks=pd.DataFrame([task(**{'核定等級':3})])
    clients=pd.DataFrame([{'案家ID':'C','核定等級':3,'服務地點_緯度':25.04,'服務地點_經度':121.54}])
    first=engine.merge_task_client_tables(tasks,clients)
    second=engine.merge_task_client_tables(first,clients)
    assert not second.columns.duplicated().any()
    assert second.iloc[0]['案家ID']=='C'


def test_only_overlapping_explicit_leave_permits_backup(tmp_path):
    store=WorkflowStore('test',tmp_path/'workflow.sqlite3')
    store.add_blocks('A','2026-11-02',[('09:00','10:00')],'請假')
    people=attach_blocks(pd.DataFrame([worker(),worker('B')]),store).to_dict('records')
    cfg=config(people,client_primary_map={'C':'A'})
    assert check([task()], 't1', 'B', cfg=cfg,people=people)['available']
    assert not check([task(start='10:00',end='11:00')], 't1', 'B', cfg=cfg,people=people)['available']
    assert not check([task()], 't1', 'A', cfg=cfg,people=people)['available']
    store.save_primary_map({'C':'A'})
    assert WorkflowStore('test',tmp_path/'workflow.sqlite3').primary_map()=={'C':'A'}
    with pytest.raises(ValueError):store.save_primary_map({'C':'B'})
    assert WorkflowStore('other',tmp_path/'workflow.sqlite3').primary_map()=={}


def test_non_leave_block_is_not_permission_to_substitute():
    original=worker(臨時不可排時段=json.dumps([{'start':'2026-11-02 09:00','end':'2026-11-02 10:00','reason':'教育訓練'}]))
    assert not leave_overlap(task(),original)
    assert primary_guard(task(),worker('B'),config([original,worker('B')],client_primary_map={'C':'A'}))


def test_no_implicit_substitution_for_expiry_or_rest_day():
    for fields in [{'長照服務人員到期日':'2026-11-01'},{'可排班星期':'2,3,4,5,6,7'}]:
        people=[worker(**fields),worker('B')]
        cfg=config(people,client_primary_map={'C':'A'})
        assert not check([task()], 't1', 'A',cfg=cfg,people=people)['available']
        assert not check([task()], 't1', 'B',cfg=cfg,people=people)['available']


def test_initial_manual_assignment_also_locks_client():
    tasks=[task(),task('t2',day='2026-11-03')]
    assigned=pd.DataFrame([{'任務ID':'t1','派單居服員':'A'}])
    assert not check(tasks,'t2','B',assigned)['available']
    assert check(tasks,'t2','A',assigned)['available']


def test_dementia_qualification_removed_is_rejected():
    people=[worker(取得資格='',核心專長證照=''),worker('B')]
    dementia=task(**{'失智症個案(0/1)':1})
    assert not check([dementia],'t1',people=people)['available']
    assert check([dementia],'t1','B',people=people)['available']


def test_deleted_raw_qualification_does_not_return_from_cached_specialty():
    stale=worker(取得資格='',核心專長證照='失智症照顧專長')
    assert not engine.caregiver_has_cert(stale,'失智症照顧專長')
    assert not check([task(**{'失智症個案(0/1)':1})],'t1',people=[stale,worker('B')])['available']


def test_move_uses_actual_new_date_not_stale_weekday_label():
    people=[worker(可排班星期='1')]
    moved=task(day='2026-11-04',星期='星期一')
    assert not check([moved],'t1',people=people)['available']


def test_manual_new_case_can_supply_its_own_demand_fields():
    tasks=pd.DataFrame([task(client='NEW')])
    people=pd.DataFrame([worker()])
    checked=engine._evaluate_reassignment('t1','A',tasks,pd.DataFrame(columns=['任務ID','派單居服員']),
        people,config(),df_cl=pd.DataFrame([{'案家ID':'OTHER'}]))
    assert checked['available']


@pytest.mark.parametrize('hours,expected', [(8,1680),(10,2242.8),(12,2944.2)])
def test_full_overtime_multiplier_equivalence(hours,expected):
    assert daily_pay(hours*60,0,0,config())['預估居服員薪資']==pytest.approx(expected)


def test_transition_wage_not_double_paid_and_travel_counts_as_work():
    pay=daily_pay(480,60,1,config())
    assert pay['總工時']==9
    assert pay['轉場工資']==46
    assert pay['加班加給']==pytest.approx(71.4)
    assert pay['預估居服員薪資']==pytest.approx(1797.4)


def test_twelve_hours_allowed_with_real_breaks_and_no_same_client_transitions():
    tasks=[task('t1',start='08:00',end='12:00'),task('t2',start='12:30',end='16:30'),task('t3',start='17:00',end='21:00')]
    assigned=pd.DataFrame([{'任務ID':'t1','派單居服員':'A'},{'任務ID':'t2','派單居服員':'A'}])
    checked=check(tasks,'t3',assigned=assigned)
    assert checked['available']
    assert checked['working_minutes']==720
    assert not check([*tasks,task('t4',start='21:30',end='22:00')],'t4',assigned=pd.concat([assigned,pd.DataFrame([{'任務ID':'t3','派單居服員':'A'}])]))['available']


def test_transfer_counts_toward_daily_cap():
    tasks=[task('t1',client='C1',start='08:00',end='12:00'),task('t2',client='C2',start='13:00',end='17:00'),task('t3',client='C3',start='18:00',end='22:00')]
    assigned=pd.DataFrame([{'任務ID':'t1','派單居服員':'A'},{'任務ID':'t2','派單居服員':'A'}])
    checked=check(tasks,'t3',assigned=assigned)
    assert not checked['available'] and '工時超標' in checked['detail']


def test_transfer_is_not_a_break():
    slots=[(pd.Timestamp('08:00'),pd.Timestamp('12:00')),(pd.Timestamp('12:30'),pd.Timestamp('13:00'))]
    assert '休息不足' in workload_error(slots,12,transfers={1:20})
    assert workload_error(slots,12,transfers={1:0}) is None


def test_monthly_overtime_cap_and_month_boundary():
    cfg=config(continuous_work_limit_mins=1000)
    cfg.work_day_minutes={('A',f'2026-11-{i:02}'):12*60 for i in range(3,13)}
    cfg.work_day_minutes[('A','2026-10-31')]=12*60
    # Ten valid 12h planning days: 40 overtime hours; a 12h day raises it to 44.
    assert check([task(start='08:00',end='20:00')],'t1',cfg=cfg)['available']
    cfg.work_day_minutes[('A','2026-11-13')]=12*60
    assert not check([task(start='08:00',end='20:00')],'t1',cfg=cfg)['available']
    assert check([task(day='2026-12-02',start='08:00',end='20:00')],'t1',cfg=cfg)['available']


def test_manual_move_releases_previous_day_overtime():
    cfg=config(max_monthly_overtime_hours=1,continuous_work_limit_mins=1000)
    cfg.work_day_minutes={('A','2026-11-02'):560}
    tasks=[task('t1',client='C1',start='08:00',end='12:00'),
           task('t2',client='C2',day='2026-11-03',start='13:00',end='18:00')]
    assigned=pd.DataFrame([{'任務ID':'t1','派單居服員':'A'},{'任務ID':'t2','派單居服員':'A'}])
    assert check(tasks,'t2',assigned=assigned,cfg=cfg)['available']


def test_full_period_manual_check_derives_monthly_budget_without_cached_ledger():
    tasks=[task(f't{i}',client=f'C{i}',day=f'2026-11-{i+1:02}',start='08:00',end='20:00') for i in range(12)]
    assigned=pd.DataFrame([{'任務ID':t['任務ID'],'派單居服員':'A'} for t in tasks[:-1]])
    checked=check(tasks,'t11',assigned=assigned,cfg=config(continuous_work_limit_mins=1000))
    assert not checked['available'] and '每月加班工時超標' in checked['detail']


def test_consecutive_workdays_error_blocks_only_the_7th_day():
    cfg = config()
    # 已連續上班 6 天（11/02～11/07），第 7 天（11/08）必須擋下；只到第 6 天（11/07）本身仍放行。
    cfg.worker_workdays = {'A': {f'2026-11-{d:02}' for d in range(2, 8)}}
    assert consecutive_workdays_error('A', '2026-11-08', cfg)
    assert not consecutive_workdays_error('A', '2026-11-07', cfg)
    # 中間斷過一天，第 7 天前的連續鏈就重新起算，不應被擋。
    cfg.worker_workdays = {'A': {'2026-11-02', '2026-11-03', '2026-11-04', '2026-11-06', '2026-11-07'}}
    assert not consecutive_workdays_error('A', '2026-11-08', cfg)
    # 沒有紀錄的居服員（例如代班的 B）不受既有狀態影響。
    assert not consecutive_workdays_error('B', '2026-11-08', cfg)


def test_workflow_store_persists_workdays_across_scopes_and_lookback_window(tmp_path):
    store = WorkflowStore('inst', tmp_path / 'workflow.sqlite3')
    store.record_workdays([('A', '2026-10-29'), ('A', '2026-10-30'), ('A', '2026-11-05'), ('', '2026-11-01')])
    # 10 天回溯窗（11/01 之前 10 天＝10/22～10/31）只含窗內日期，11/05 在窗外不回傳。
    seed = store.recent_workdays('2026-11-01', lookback=10)
    assert seed == {'A': {'2026-10-29', '2026-10-30'}}
    # 同一 institution_key（scope）跨月持久；不同機構互不可見。
    assert WorkflowStore('inst', tmp_path / 'workflow.sqlite3').recent_workdays('2026-12-01', lookback=40)['A'] == \
        {'2026-10-29', '2026-10-30', '2026-11-05'}
    assert WorkflowStore('other', tmp_path / 'workflow.sqlite3').recent_workdays('2026-12-01', lookback=40) == {}


def test_sa10_self_pay_has_no_public_revenue_and_preserves_mismatch():
    row=task(start='15:30',end='16:00',Units_1=1,費用類型='基本')
    result=engine.calculate_task_revenue_and_salary(pd.DataFrame([row]),config()).iloc[0]
    assert result['費用類型']=='純自費'
    assert result['預估服務營收']==350
    assert result['預估BA服務營收']==0
    assert result['服務基本工資']==105
    assert result['SA10計費提醒']
    assert result['時間窗_結束']=='16:00'


def test_unknown_code_price_does_not_destroy_hourly_wage():
    result=engine.calculate_task_revenue_and_salary(pd.DataFrame([task(Service_Code_1='UNKNOWN')]),config()).iloc[0]
    assert pd.isna(result['預估服務營收'])
    assert result['服務基本工資']==210


def test_ba02_combination_warning_removed():
    assert engine.check_ba_code_compatibility({'Service_Code_1':'BA02','Units_1':1,'Service_Code_2':'BA07','Units_2':1}) is None
    assert engine.check_ba_code_compatibility({'Service_Code_1':'BA01','Units_1':1,'Service_Code_2':'BA07','Units_2':1})


def test_payroll_effective_routes_and_same_coordinate_different_clients():
    tasks=pd.DataFrame([task('t1',client='C1'),task('t2',client='C2',start='11:00',end='12:00'),task('t3',client='C2',start='12:00',end='13:00')])
    assigned=pd.DataFrame([{'任務ID':tid,'派單居服員':'A'} for tid in tasks['任務ID']])
    people=pd.DataFrame([worker(),worker('B')]);cfg=config()
    routes=engine.calculate_schedule_travel(assigned,tasks,people,cfg)
    details,daily,monthly,ledger=payroll_tables(tasks,assigned,people,routes,cfg)
    assert daily.iloc[0]['轉場次數']==1
    assert daily.iloc[0]['轉場工資']==46
    assert daily.iloc[0]['總工時']==pytest.approx(3+20/60)
    assert details['預估居服員薪資'].sum()==daily['預估居服員薪資'].sum()
    assert routes.set_index('任務ID').loc['t3','含緩衝交通時間(分)']==0
    changed=assigned.copy();changed['派單居服員']='B'
    _,stale,_,_=payroll_tables(tasks,changed,people,routes,cfg)
    assert pd.isna(stale.iloc[0]['預估居服員薪資'])
    workbook=pd.ExcelFile(BytesIO(schedule_excel_bytes(tasks,daily,monthly,{'C1':'A'})))
    assert {'班表','每日工資估算','每月工資估算','固定居服員','逐段交通'}.issubset(workbook.sheet_names)


def test_full_period_same_client_owner_and_leave_restoration():
    leave=('2026-11-04 14:00','2026-11-04 16:00')
    people=pd.DataFrame([worker(請假紀錄=leave),worker('B')])
    clients=pd.DataFrame([{'案家ID':'C'}])
    tasks=pd.DataFrame([task(),task('t2',day='2026-11-04',start='15:00',end='16:00'),task('t3',day='2026-11-11',start='15:00',end='16:00'),task('t4',day='2026-12-02')])
    cfg=config()
    result=run_fixed_period_dispatch(tasks,people,clients,cfg,primary_map={'C':'A'})
    assignments=result['df_result'].set_index('任務ID')
    assert assignments.loc['t1','派單居服員']=='A'
    assert assignments.loc['t2','派單居服員']=='B'
    assert assignments.loc['t3','派單居服員']=='A'
    assert assignments.loc['t4','派單居服員']=='A'
    assert result['client_primary_map']=={'C':'A'}
    assert bool(assignments.loc['t2','是否請假代班'])


def test_new_client_is_never_split_to_accommodate_weekday_availability():
    # A works Monday only, B Wednesday only: do not split a client across them.
    people=pd.DataFrame([worker(可排班星期='1'),worker('B',可排班星期='3')])
    tasks=pd.DataFrame([task(),task('t2',day='2026-11-04')])
    result=run_fixed_period_dispatch(tasks,people,pd.DataFrame([{'案家ID':'C'}]),config())
    assert result['df_result']['派單居服員'].nunique()==1
    assert len(result['df_result'])==1
    assert len(result['diagnostics'])==1
    assert result['client_primary_map']['C'] in {'A','B'}


def test_later_new_pattern_is_considered_during_first_owner_selection():
    people=pd.DataFrame([worker(可排班星期='1'),worker('B')])
    tasks=pd.DataFrame([task(),task('t2',day='2026-12-02')])
    result=run_fixed_period_dispatch(tasks,people,pd.DataFrame([{'案家ID':'C'}]),config())
    assert result['client_primary_map']=={'C':'B'}
    assert set(result['df_result']['派單居服員'])=={'B'}


def test_whole_year_dispatch_keeps_one_owner_across_year_boundary():
    dates=pd.date_range('2026-12-01','2027-11-30',freq='W-MON')
    tasks=pd.DataFrame([task(f't{i}',day=d.strftime('%Y-%m-%d')) for i,d in enumerate(dates)])
    result=run_fixed_period_dispatch(tasks,pd.DataFrame([worker()]),pd.DataFrame([{'案家ID':'C'}]),config(),primary_map={'C':'A'})
    assert len(result['df_result'])==len(dates)
    assert result['client_primary_map']=={'C':'A'}
    assert result['df_result']['派單居服員'].eq('A').all()


def test_biweekly_anchor_crosses_year_and_expiry_is_honored():
    employees=pd.DataFrame([{'編號':'A','性別':'女','休息日':'星期日','取得資格':'失智症照顧服務20小時',
                            '交通工具':'機車','經度 (WGS84_Lon)':121.53,'緯度 (WGS84_Lat)':25.03}])
    clients=pd.DataFrame([{'內部案號':'C'}])
    plans=pd.DataFrame([{'案號':'C','星期':'星期一','開始時間':'09:00','結束時間':'10:00','服務分鐘':60,
                        '頻率':'隔週','生效日':'2026-12-28','到期日':'2027-01-20','服務項目':'SA10(1)',
                        '費用類型':'基本','經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04}])
    imported=engine.load_agency_data(employees,clients,plans,'2026-12-01','2027-11-30',availability_confirmed=True)
    assert imported['Tasks']['日期'].tolist()==['2026-12-28','2027-01-11']
    assert imported['Tasks']['費用類型'].eq('純自費').all()
