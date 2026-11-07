"""Team continuity, joint choices and date exceptions using synthetic visits."""
from dataclasses import replace
import json

import pandas as pd
import pytest

import caregiver_engine as engine
from agency_rules import payroll_tables
from dispatch_workflow import WorkflowStore
from fast_calendar import FastCalendar
from monthly_scheduler import run_fixed_period_dispatch
from pending_clients import client_patterns, pattern_token, preview_client_assignment
from runtime_views import payroll_key, snapshot
from test_agency_rules import worker, task, mocked_routes


def cfg(**values):
    defaults = dict(team_solver_seconds=1.0,team_repair_seconds=0.0,template_repair_seconds=0.0)
    defaults.update(values)
    return engine.PipelineConfig(**defaults)


def clients(*ids,**fields):
    return pd.DataFrame([{'案家ID':identifier,**fields} for identifier in ids])


def test_team_default_preserves_primary_and_stable_weekday_partners():
    visits = pd.DataFrame([task(f'{prefix}{i}',day=day) for i,(mon,wed) in enumerate([
        ('2026-11-02','2026-11-04'),('2026-11-09','2026-11-11')])
        for prefix,day in [('mon',mon),('wed',wed)]])
    people = pd.DataFrame([worker(可排班星期='1'),worker('B',可排班星期='3')])
    result = run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),cfg())
    assigned = result['df_result'].set_index('任務ID')
    assert len(assigned)==4 and result['client_primary_map']=={'C':'A'}
    assert assigned.loc[['mon0','mon1'],'派單居服員'].eq('A').all()
    assert assigned.loc[['wed0','wed1'],'派單居服員'].eq('B').all()
    assert assigned.loc[['wed0','wed1'],'是否固定搭配'].all()
    assert not assigned['是否請假代班'].any()
    # Automatic and manual assignment share the relaxed continuity policy.
    assert engine._evaluate_reassignment('wed0','B',visits,pd.DataFrame([
        {'任務ID':'mon0','派單居服員':'A'}]),people,cfg(client_primary_map={'C':'A'}))['available']


def test_joint_choices_escape_greedy_qualification_trap():
    dates = ['2026-11-02','2026-11-09','2026-11-16','2026-11-23']
    visits = pd.DataFrame([task(f'foot{i}',client='FOOT',day=day,Service_Code_1='BA08',Units_1=1)
        for i,day in enumerate(dates)]+[task(f'{client}{i}',client=client,day=day)
        for client in ['D1','D2'] for i,day in enumerate(dates[:3])])
    people = pd.DataFrame([
        worker(取得資格='失智症照顧服務20小時、BA08足部照護'),
        worker('B',取得資格='BA08足部照護',**{'服務起點_緯度(家)':25.07}),
        worker('C')])
    demands = clients('FOOT',是否失智='否')
    demands = pd.concat([demands,clients('D1','D2',是否失智='是')],ignore_index=True)
    result = run_fixed_period_dispatch(visits,people,demands,cfg(team_solver_seconds=3.0))
    stats = result['dispatch_stats']
    assert stats['greedy_template_services']==7
    assert stats['joint_template_services']==10
    assert len(result['df_result'])==10
    assert result['df_result'].loc[result['df_result']['案家ID'].eq('FOOT'),'派單居服員'].eq('B').all()


def test_whole_series_exchange_recovers_all_weeks_without_losing_services():
    dates = ['2026-11-02','2026-11-09','2026-11-16','2026-11-23']
    visits = pd.DataFrame([task(f'foot{i}',client='FOOT',day=day,Service_Code_1='BA08',Units_1=1)
        for i,day in enumerate(dates)]+[task(f'{client}{i}',client=client,day=day)
        for client in ['D1','D2'] for i,day in enumerate(dates[:3])])
    people = pd.DataFrame([worker(取得資格='失智症照顧服務20小時、BA08足部照護'),
        worker('B',取得資格='BA08足部照護',**{'服務起點_緯度(家)':25.07}),worker('C')])
    demands = pd.concat([clients('FOOT',是否失智='否'),clients('D1','D2',是否失智='是')],ignore_index=True)
    result = run_fixed_period_dispatch(visits,people,demands,cfg(team_solver_seconds=0,template_repair_seconds=2))
    assert result['dispatch_stats']['series_exchanges']>=1
    assert len(result['df_result'])==10


def test_qualification_and_expiry_never_softened_by_team_policy():
    visits = pd.DataFrame([task('a',Service_Code_1='BA08',Units_1=1),
        task('b',day='2026-11-09',Service_Code_1='BA08',Units_1=1)])
    people = pd.DataFrame([worker(取得資格=''),worker('B',取得資格='失智症照顧服務20小時、BA08足部照護',長照服務人員到期日='2026-11-05')])
    result = run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A',是否失智='是'),cfg())
    assert result['df_result'][['任務ID','派單居服員']].to_dict('records')==[{'任務ID':'a','派單居服員':'B'}]
    reasons = result['diagnostics'].iloc[0]['逐人排班原因']
    assert '訓練' in reasons['A'] and '到期' in reasons['B']
    assert result['client_primary_map']['C']=='A'


def test_series_leave_does_not_overwrite_routine_or_mislabel_other_partners():
    visits = pd.DataFrame([task('first'),task('leave',day='2026-11-09'),task('last',day='2026-11-16')])
    token = pattern_token(visits.iloc[0])
    people = pd.DataFrame([worker(請假日期='2026-11-02'),worker('B',請假日期='2026-11-09')])
    config = cfg(client_primary_map={'C':'A'},series_primary_map={token:'B'})
    result = run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),config)
    assignments = result['df_result'].set_index('任務ID')
    assert assignments['派單居服員'].to_dict()=={'first':'B','leave':'A','last':'B'}
    assert not assignments.loc['first','是否請假代班']
    assert assignments.loc['leave','是否請假代班']
    assert result['series_primary_map'][token]=='B' and result['client_primary_map']['C']=='A'
    details,_,_,_ = payroll_tables(result['df_tasks'],result['df_result'],people,result['route_result'],config)
    assert not details.set_index('任務ID').loc['first','是否請假代班']


def test_confirmed_service_wins_conflict_and_is_never_exchanged():
    visits = pd.DataFrame([task('unconfirmed',client='C1'),task('confirmed',client='C2')])
    result = run_fixed_period_dispatch(visits,pd.DataFrame([worker(),worker('B')]),
        clients('C1','C2',歷史首選='A'),cfg(frozen_task_assignments={'confirmed':'A'},template_repair_seconds=.5))
    assigned = result['df_result'].set_index('任務ID')['派單居服員'].to_dict()
    assert assigned=={'unconfirmed':'B','confirmed':'A'}
    assert result['client_primary_map']=={'C1':'A','C2':'A'}


def test_month_long_source_leave_never_becomes_permanent_partner():
    visits=pd.DataFrame([task('a'),task('b',day='2026-11-09')])
    people=pd.DataFrame([worker(請假紀錄=('2026-11-01 00:00','2026-11-30 23:59')),worker('B')])
    result=run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),cfg())
    assert result['df_result']['派單居服員'].eq('B').all()
    assert result['df_result']['是否請假代班'].all()
    assert result['series_primary_map'][pattern_token(visits.iloc[0])]=='A'


def test_registered_partial_leave_is_not_selected_as_regular_representative():
    visits=pd.DataFrame([task('a'),task('b',day='2026-11-09')])
    people=pd.DataFrame([worker(臨時不可排時段=json.dumps([
        {'start':'2026-11-02 09:00','end':'2026-11-02 10:00','reason':'請假'}])),worker('B')])
    result=run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),cfg())
    assigned=result['df_result'].set_index('任務ID')
    assert assigned['派單居服員'].to_dict()=={'a':'B','b':'A'}
    assert assigned.loc['a','是否請假代班'] and not assigned.loc['b','是否請假代班']
    assert result['series_primary_map'][pattern_token(visits.iloc[0])]=='A'


def test_confirmed_unqualified_service_requires_review_instead_of_auto_change():
    result = run_fixed_period_dispatch(pd.DataFrame([task()]),pd.DataFrame([worker(取得資格=''),worker('B')]),
        clients('C',是否失智='是'),cfg(frozen_task_assignments={'t1':'A'}))
    assert result['df_result'].empty
    assert '居督確認' in result['diagnostics'].iloc[0]['剔除原因']


def test_fifty_two_motorcycle_weeks_reuse_route_but_keep_actual_dates():
    visits = pd.DataFrame([task(f't{i}',day=str(d.date())) for i,d in enumerate(pd.date_range('2026-11-02',periods=52,freq='7D'))])
    result = run_fixed_period_dispatch(visits,pd.DataFrame([worker()]),clients('C',歷史首選='A'),cfg())
    assert len(result['df_result'])==52
    assert result['dispatch_stats']['route_cache_hits']>=51
    assert result['dispatch_stats']['template_service_count']==1
    assert set(result['route_result']['交通日期'])==set(visits['日期'])


def test_transit_week_is_rechecked_at_actual_date_and_time(monkeypatch):
    requests = []
    def travel(*args,**kw):
        requests.append(kw.get('arrival_time') or kw.get('departure_time'))
        return 5.0
    monkeypatch.setattr(engine,'calc_travel_minutes',travel)
    visits = pd.DataFrame([task('first'),task('next',day='2026-11-09')])
    result = run_fixed_period_dispatch(visits,pd.DataFrame([worker(常用交通工具='大眾運輸')]),clients('C',歷史首選='A'),cfg())
    assert len(result['df_result'])==2
    assert {'2026-11-02','2026-11-09'} <= {str(v.date()) for v in requests}


@pytest.mark.parametrize('slots',[
    [('08:00','12:00'),('12:30','13:00')],
    [('09:00','10:00'),('09:30','10:30')],
    [('09:00','10:00'),('10:15','11:15')],
    [('08:00','12:00'),('13:00','17:00'),('18:00','22:00')],
    [('08:00','12:00'),('13:00','17:00')],
])
def test_compact_calendar_matches_central_route_checks(slots):
    visits = pd.DataFrame([task(f't{i}',client=f'C{i}',start=a,end=b) for i,(a,b) in enumerate(slots)])
    people = pd.DataFrame([worker()]);config = cfg()
    calendar = FastCalendar(visits,people,config)
    compact = calendar.evaluate('A','2026-11-02',visits['任務ID'].tolist())
    assigned = pd.DataFrame([{'任務ID':tid,'派單居服員':'A'} for tid in visits['任務ID'].iloc[:-1]])
    central = engine._evaluate_reassignment(visits.iloc[-1]['任務ID'],'A',visits,assigned,people,cfg())
    assert compact['available']==central['available']
    if compact['available']:
        assert compact['working_minutes']==pytest.approx(central['working_minutes'])


def test_month_overtime_and_next_month_still_checked_in_copied_team_routes():
    days = list(pd.date_range('2026-11-01',periods=14))+[pd.Timestamp('2026-12-01')]
    visits = pd.DataFrame([task(f't{i}',day=str(day.date()),start='08:00',end='20:00') for i,day in enumerate(days)])
    result = run_fixed_period_dispatch(visits,pd.DataFrame([worker()]),clients('C',歷史首選='A'),cfg(continuous_work_limit_mins=1000))
    assert len(result['df_result'])==12 and 't14' in set(result['df_result']['任務ID'])
    assert result['diagnostics']['剔除原因'].str.contains('每月加班工時超標').all()


def test_manual_case_preview_uses_weekday_team_and_preserves_input():
    visits = pd.DataFrame([task('mon'),task('wed',day='2026-11-04',Service_Code_1='BA08',Units_1=1)])
    people = pd.DataFrame([worker(),worker('B',取得資格='失智症照顧服務20小時、BA08足部照護')])
    assignments = pd.DataFrame([{'任務ID':'mon','派單居服員':'A'}])
    patterns = client_patterns(visits,'C');patterns['居服員ID']=['A','B']
    preview = preview_client_assignment(visits,assignments,people,clients('C',歷史首選='A',是否失智='是'),cfg(),'C','A',patterns,keep_existing=True)
    assert preview['accepted']['派單居服員'].tolist()==['A','B'] and preview['failed'].empty
    assert preview['primary_map']['C']=='A' and len(preview['series_primary_map'])==2
    assert assignments.to_dict('records')==[{'任務ID':'mon','派單居服員':'A'}]


def test_case_preview_recognizes_patterns_before_client_fields_are_merged():
    visits=pd.DataFrame([task('a'),task('b',day='2026-11-09')])
    demands=clients('C',歷史首選='A',是否失智='否')
    patterns=client_patterns(visits,'C')
    patterns.loc[:,['開始時間','結束時間']]=['10:30','11:30']
    patterns['居服員ID']='B'
    preview=preview_client_assignment(visits,pd.DataFrame(columns=['任務ID','派單居服員']),
        pd.DataFrame([worker(),worker('B')]),demands,cfg(),'C','A',patterns)
    assert preview['tasks']['時間窗_開始'].eq('10:30').all()
    assert preview['accepted']['派單居服員'].eq('B').all()


def test_exported_weekly_workers_reload_without_database_or_primary_search():
    from io import BytesIO
    from source_workbook import update_source_workbook
    employees=pd.DataFrame([{'編號':i,'性別':'女','休息日':'星期日',
        '取得資格':'失智症照顧服務20小時','交通工具':'機車',
        '經度 (WGS84_Lon)':121.53,'緯度 (WGS84_Lat)':25.03} for i in ['A','B']])
    demands=pd.DataFrame([{'內部案號':'C','歷史首選':'A','是否失智':'否',
        '經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04}])
    plans=pd.DataFrame([{'案號':'C','星期':day,'開始時間':a,'結束時間':b,'服務分鐘':60,
        '頻率':'每週','生效日':'2026-11-01','服務項目':'SA10(1)',
        '經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04}
        for day,a,b in [('星期一','09:00','10:00'),('星期三','15:00','16:00')]])
    source=BytesIO()
    with pd.ExcelWriter(source,engine='openpyxl') as writer:
        for name,frame in [('員工資料',employees),('個案資料',demands),('週服務計畫',plans)]:
            frame.to_excel(writer,sheet_name=name,index=False)
    updated=update_source_workbook(source.getvalue(),{},weekly_workers={2:'A',3:'B'})
    loaded=engine.load_agency_workbook(BytesIO(updated),'2026-11-01','2026-11-30',availability_confirmed=True)
    result=run_fixed_period_dispatch(loaded['Tasks'],loaded['Caregiver_Profiles'],loaded['Client_Profiles'],cfg())
    assigned=result['df_result'].merge(loaded['Tasks'][['任務ID','星期']],on='任務ID')
    assert len(assigned)==9 and assigned.loc[assigned['星期'].eq('星期三'),'派單居服員'].eq('B').all()
    assert result['client_primary_map']['C']=='A'


def test_runtime_snapshot_changes_only_when_relevant_version_changes():
    tasks = pd.DataFrame([task()]);assignments = pd.DataFrame([{'任務ID':'t1','派單居服員':'A'}])
    people = pd.DataFrame([worker()]);routes = pd.DataFrame();config = cfg()
    cache,calls = {},[]
    def calculate():calls.append(True);return len(calls)
    key = payroll_key(tasks,assignments,people,routes,config)
    assert snapshot(cache,'payroll',key,calculate)==1
    config.work_day_minutes={('A','2026-11-02'):60} # output ledger is not an input
    assert snapshot(cache,'payroll',payroll_key(tasks,assignments,people,routes,config),calculate)==1
    config.hourly_wage=220
    assert snapshot(cache,'payroll',payroll_key(tasks,assignments,people,routes,config),calculate)==2


def test_daily_route_cache_ignores_team_role_but_invalidates_road_version(monkeypatch):
    from schedule_checks import incremental_travel
    visits=pd.DataFrame([task('a'),task('b',day='2026-11-03')])
    assignments=pd.DataFrame([{'任務ID':tid,'派單居服員':'A'} for tid in visits['任務ID']])
    people=pd.DataFrame([worker()]);config=cfg();cache={};calls=[]
    def calculate(*args):
        calls.append(True);return engine.calculate_schedule_travel(*args)
    first=incremental_travel(assignments,visits,people,config,cache,calculate)
    assert len(calls)==2
    config.series_primary_map={'example':'B'}
    config.frozen_task_assignments={'a':'A'}
    second=incremental_travel(assignments,visits,people,config,cache,calculate)
    assert len(calls)==2 and first['預估車程(分)'].tolist()==second['預估車程(分)'].tolist()
    monkeypatch.setenv('CAREFLOW_OSRM_MAP_VERSION','another-map')
    incremental_travel(assignments,visits,people,config,cache,calculate)
    assert len(calls)==4


def test_manual_weekly_change_does_not_keep_old_payroll_role():
    from calendar_view import apply_overrides_to_result
    visits=pd.DataFrame([task()]);people=pd.DataFrame([worker(),worker('B')])
    original=pd.DataFrame([{'任務ID':'t1','派單居服員':'A','服務序列居服員ID':'A'}])
    edited=apply_overrides_to_result(original,{'t1':{'cg_id':'B','reason':'居督整案搭配'}})
    config=cfg(client_primary_map={'C':'A'},series_primary_map={pattern_token(visits.iloc[0]):'B'})
    routes=engine.calculate_schedule_travel(edited,visits,people,config)
    details,_,_,_=payroll_tables(visits,edited,people,routes,config)
    assert details.iloc[0]['服務序列居服員ID']=='B'
    assert details.iloc[0]['是否固定搭配'] and not details.iloc[0]['是否請假代班']
    assert original.iloc[0]['服務序列居服員ID']=='A'


def test_saved_team_scoped_separately_from_primary(tmp_path):
    path = tmp_path/'workflow.sqlite3'
    store = WorkflowStore('agency',path)
    store.save_primary_map({'C':'A'});store.save_series_map({'mon':'A','wed':'B'})
    assert WorkflowStore('agency',path).series_map()=={'mon':'A','wed':'B'}
    assert WorkflowStore('agency',path).primary_map()=={'C':'A'}
    assert WorkflowStore('other',path).series_map()=={}


def test_ai_dispatch_never_assigns_a_7th_consecutive_day_but_resumes_after_the_gap():
    """AI 排班一律不得連續上班超過 6 天：第 7 天硬性擋下、不指派任何人；
    擋下的那天沒有上班，連續天數就重新起算，第 8 天可以再排。"""
    dates = pd.date_range('2026-11-02',periods=8,freq='D').strftime('%Y-%m-%d').tolist()
    visits = pd.DataFrame([task(f't{i}',day=day) for i,day in enumerate(dates)])
    people = pd.DataFrame([worker(),worker('B')])
    result = run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),cfg())
    assigned = result['df_result'].set_index('任務ID')
    for i in [0,1,2,3,4,5,7]:
        assert assigned.loc[f't{i}','派單居服員']=='A'
    assert 't6' not in assigned.index
    blocked = result['diagnostics']
    row = blocked[blocked['任務ID']=='t6']
    assert not row.empty and row.iloc[0]['問題分類']=='連續工作天數'


def test_consecutive_workdays_seed_carries_over_the_month_boundary():
    """跨月：居服員在本期間開始前（由 WorkflowStore 歷史種子帶入）已連續上班到門檻邊緣，
    本期間第一天就要被視為第7天而擋下，不能因為換了月份重新從零起算。"""
    visits = pd.DataFrame([task('t0',day='2026-12-01')])
    people = pd.DataFrame([worker(),worker('B')])
    seed = {'A':{f'2026-11-{d:02}' for d in range(25,31)}}  # 11/25～11/30 共6天連續上班
    result = run_fixed_period_dispatch(visits,people,clients('C',歷史首選='A'),cfg(),worker_workday_seed=seed)
    assert 't0' not in set(result['df_result'].get('任務ID',[]))
    row = result['diagnostics']
    row = row[row['任務ID']=='t0']
    assert not row.empty and row.iloc[0]['問題分類']=='連續工作天數'
