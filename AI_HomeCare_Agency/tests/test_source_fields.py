import pandas as pd
import pytest
import caregiver_engine as engine
import monthly_scheduler as scheduler
from source_fields import normalize_clients, source_primary_map, seniority_score, resolve_worker
from test_agency_rules import worker, task, config, check, mocked_routes


def test_alias_fields_override_stale_cached_fields():
    clients=pd.DataFrame([{'案家ID':'C','是否失智':'是','失智症個案(0/1)':0,
                          '歷史首選':'03816','歷史首選居服員ID':'old'}])
    result=engine.merge_task_client_tables(pd.DataFrame([task()]),clients).iloc[0]
    assert result['失智症個案(0/1)']==1
    assert result['歷史首選居服員ID']=='03816'
    assert source_primary_map(clients,{'03816'},{'C':'old'})=={'C':'03816'}


def test_unique_zero_repair_and_masked_identifier():
    assert resolve_worker('3816',{'03816'})=='03816'
    assert resolve_worker('3816',{'03816','003816'})=='3816'
    assert resolve_worker('03816｜王○○',{'03816'})=='03816'


def test_raw_dementia_flag_and_ba08_are_both_required():
    people=[worker(取得資格='失智症照顧服務20小時'),worker('B',取得資格='BA08足部照護')]
    row=task(是否失智='是',Service_Code_1='BA08')
    assert not check([row],'t1','A',people=people)['available']
    assert not check([row],'t1','B',people=people)['available']
    qualified=worker(取得資格='失智症照顧服務20小時,BA08足部照護')
    assert check([row],'t1',people=[qualified])['available']


def test_seniority_is_capped_soft_bonus_as_of_service_date():
    cfg=config()
    years,bonus=seniority_score(worker(到職日='2010-01-01'),'2026-10-01',cfg)
    assert years>16 and bonus==10
    assert seniority_score(worker(到職日='bad'),'2026-10-01',cfg)==(0,0)
    assert seniority_score(worker(到職日='2027-01-01'),'2026-10-01',cfg)==(0,0)
    old=worker(到職日='2010-01-01',取得資格='')
    assert not check([task(是否失智='是')],'t1',people=[old])['available']


def test_known_history_skips_owner_search_and_checks_each_visit_once(monkeypatch):
    people=pd.DataFrame([worker(),worker('B')])
    clients=pd.DataFrame([{'案家ID':'C','歷史首選':'B','是否失智':'是'}])
    tasks=pd.DataFrame([task(),task('t2',day='2026-11-04')])
    calls=[]
    original=scheduler._evaluate_reassignment
    def trace(*args,**kwargs):
        calls.append(args[0]);return original(*args,**kwargs)
    def forbidden(*args,**kwargs):raise AssertionError('Known owner must bypass Phase 1')
    monkeypatch.setattr(scheduler,'_evaluate_reassignment',trace)
    monkeypatch.setattr(scheduler,'run_phase1_matching',forbidden)
    result=scheduler.run_fixed_period_dispatch(tasks,people,clients,config(),primary_map={'C':'A'})
    assert result['client_primary_map']=={'C':'B'}
    assert result['df_result']['派單居服員'].eq('B').all()
    assert calls==['t1','t2']
    assert result['df_matches']['歷史首選加分'].eq(60).all()
    assert result['dispatch_stats']['ownership_search_clients']==0


def test_accepted_sa10_half_hour_is_scoped_to_source_plan():
    row=task(client='114080027',day='2026-10-07',start='15:30',end='16:00',Units_1=1)
    r=engine.calculate_task_revenue_and_salary(pd.DataFrame([row]),config()).iloc[0]
    assert r['SA10計費提醒']=='' and r['服務基本工資']==105
    row['案家ID']='other'
    assert engine.calculate_task_revenue_and_salary(pd.DataFrame([row]),config()).iloc[0]['SA10計費提醒']


def test_excel_float_noise_is_accepted_but_real_duration_error_is_not():
    people=pd.DataFrame([{'編號':'A','性別':'女','休息日':'星期日','取得資格':'失智症照顧服務20小時',
                          '交通工具':'機車','經度 (WGS84_Lon)':121.53,'緯度 (WGS84_Lat)':25.03}])
    clients=pd.DataFrame([{'內部案號':'C','是否失智':'是','歷史首選':'A'}])
    visits=pd.DataFrame([{'案號':'C','星期':'星期一','開始時間':'09:00','結束時間':'10:00',
        '服務分鐘':59.99999999999994,'頻率':'每週','生效日':'2026-11-02','服務項目':'SA10(1)'}])
    loaded=engine.load_agency_data(people,clients,visits,'2026-11-02','2026-11-02',availability_confirmed=True)
    assert len(loaded['Tasks'])==1 and loaded['Tasks'].iloc[0]['服務歷時(分鐘)']==60
    assert loaded['Client_Profiles'].iloc[0]['失智症個案(0/1)']==1
    visits.loc[0,'服務分鐘']=50
    assert engine.load_agency_data(people,clients,visits,'2026-11-02','2026-11-02')['Tasks'].empty


def test_bare_ba08_preserves_service_and_requires_training():
    assert engine.parse_service_items('BA20(3) BA08')==[('BA20',3),('BA08',1)]
    with pytest.raises(ValueError):engine.parse_service_items('BA20(3) BA08(x)')


def test_cached_patterns_recheck_monthly_overtime():
    days=pd.date_range('2026-11-01',periods=14)
    tasks=pd.DataFrame([task(f't{i}',day=d.strftime('%Y-%m-%d'),start='08:00',end='20:00') for i,d in enumerate(days)])
    cfg=config(continuous_work_limit_mins=1000)
    result=scheduler.run_fixed_period_dispatch(tasks,pd.DataFrame([worker()]),pd.DataFrame([{'案家ID':'C','歷史首選':'A'}]),cfg)
    assert len(result['df_result'])==11
    assert result['diagnostics']['剔除原因'].str.contains('每月加班工時超標').all()


def test_google_transit_checks_each_actual_date(monkeypatch):
    people=pd.DataFrame([worker(常用交通工具='大眾運輸')])
    tasks=pd.DataFrame([task(f't{i}',day=d) for i,d in enumerate(['2026-11-02','2026-11-09','2026-11-16'])])
    calls=[];original=scheduler._evaluate_reassignment
    def trace(*args,**kwargs):calls.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(scheduler,'_evaluate_reassignment',trace)
    result=scheduler.run_fixed_period_dispatch(tasks,people,pd.DataFrame([{'案家ID':'C','歷史首選':'A'}]),config())
    assert len(result['df_result'])==3 and calls==['t0','t1','t2']
