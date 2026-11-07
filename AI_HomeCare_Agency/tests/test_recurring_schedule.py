import pandas as pd
import monthly_scheduler as scheduler
from recurring_schedule import representative_days
from pending_clients import pending_client_summary, client_patterns, preview_client_assignment
from dispatch_workflow import WorkflowStore
from test_agency_rules import worker, task, config, mocked_routes


def test_fifty_two_regular_weeks_evaluate_route_once(monkeypatch):
    visits=pd.DataFrame([task(f't{i}',day=str(d.date())) for i,d in enumerate(pd.date_range('2026-11-02',periods=52,freq='7D'))])
    calls=[];original=scheduler._evaluate_reassignment
    def trace(*args,**kwargs):
        calls.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(scheduler,'_evaluate_reassignment',trace)
    result=scheduler.run_fixed_period_dispatch(visits,pd.DataFrame([worker()]),pd.DataFrame([{'案家ID':'C','歷史首選':'A'}]),config())
    assert len(result['df_result'])==52
    assert calls==['t0']
    assert result['dispatch_stats']['copied_worker_days']==51


def test_alternating_week_shapes_do_not_create_extra_visits():
    rows=[]
    for i,day in enumerate(['2026-11-02','2026-11-09','2026-11-16','2026-11-23']):
        rows.append(task(f'a{i}',client='C',day=day))
        if i%2==0:
            rows.append(task(f'b{i}',client='D',day=day,start='11:00',end='12:00'))
    visits=pd.DataFrame(rows);people=pd.DataFrame([worker()])
    basis=representative_days(visits,people)
    assert basis['日期'].nunique()==2
    assert set(basis['_template_occurrences'])=={2}
    result=scheduler.run_fixed_period_dispatch(visits,people,pd.DataFrame([{'案家ID':'C','歷史首選':'A'},{'案家ID':'D','歷史首選':'A'}]),config())
    assert set(result['df_result']['任務ID'])==set(visits['任務ID'])
    assert len(result['df_result'])==6


def test_future_expiry_and_leave_are_not_copied_from_regular_week():
    dates=['2026-11-02','2026-11-09','2026-11-16','2026-11-23']
    visits=pd.DataFrame([task(f't{i}',day=day) for i,day in enumerate(dates)])
    people=pd.DataFrame([worker(長照服務人員到期日='2026-11-12',請假日期='2026-11-09'),worker('B')])
    result=scheduler.run_fixed_period_dispatch(visits,people,pd.DataFrame([{'案家ID':'C','歷史首選':'A'}]),config())
    assigned=result['df_result'].set_index('任務ID')
    assert set(assigned.index)=={'t0','t1'}
    assert assigned.loc['t0','派單居服員']=='A'
    assert assigned.loc['t1','派單居服員']=='B' and assigned.loc['t1','是否請假代班']
    assert result['diagnostics']['剔除原因'].str.contains('到期').all()


def test_month_overtime_failure_does_not_poison_next_month():
    dates=list(pd.date_range('2026-11-01',periods=14))+[pd.Timestamp('2026-12-01')]
    visits=pd.DataFrame([task(f't{i}',day=str(d.date()),start='08:00',end='20:00') for i,d in enumerate(dates)])
    result=scheduler.run_fixed_period_dispatch(visits,pd.DataFrame([worker()]),pd.DataFrame([{'案家ID':'C','歷史首選':'A'}]),config(continuous_work_limit_mins=1000))
    assert len(result['df_result'])==12
    assert 't14' in set(result['df_result']['任務ID'])


def test_five_hundred_pending_visits_are_sixty_client_rows():
    visits=pd.DataFrame([task(f't{i}',client=f'C{i%60}') for i in range(500)])
    assignments=pd.DataFrame(columns=['任務ID','派單居服員'])
    grouped=pending_client_summary(visits,assignments)
    assert len(grouped)==60 and grouped['未派服務數'].sum()==500
    assignments=pd.DataFrame([{'任務ID':tid,'派單居服員':'A'} for tid in visits.loc[visits['案家ID'].eq('C0'),'任務ID']])
    grouped=pending_client_summary(visits,assignments)
    assert len(grouped)==59 and 'C0' not in set(grouped['案家ID'])


def test_whole_client_time_edit_fills_all_dates_without_mutating_inputs():
    rows=[]
    for i,day in enumerate(['2026-11-02','2026-11-09','2026-11-16','2026-11-23']):
        rows += [task(f'case{i}',client='C',day=day),task(f'other{i}',client='D',day=day)]
    visits=pd.DataFrame(rows)
    assignments=pd.DataFrame([{'任務ID':f'other{i}','派單居服員':'A'} for i in range(4)])
    clients=pd.DataFrame([{'案家ID':'C','歷史首選':'A'},{'案家ID':'D','歷史首選':'A'}])
    patterns=client_patterns(visits,'C')
    assert len(patterns)==1
    patterns.loc[:,['開始時間','結束時間']]=['10:30','11:30']
    cfg=config(client_primary_map={'C':'A','D':'A'})
    preview=preview_client_assignment(visits,assignments,pd.DataFrame([worker()]),clients,cfg,'C','A',patterns)
    assert len(preview['accepted'])==4 and preview['failed'].empty
    assert preview['tasks'].loc[preview['tasks']['案家ID'].eq('C'),'時間窗_開始'].eq('10:30').all()
    assert visits['時間窗_開始'].eq('09:00').all()
    assert len(assignments)==4 and cfg.client_primary_map=={'C':'A','D':'A'}


def test_whole_client_choice_covers_different_weekdays_and_keeps_qualification():
    visits=pd.DataFrame([task('mon'),task('wed',day='2026-11-04',Service_Code_1='BA08',Units_1=1)])
    clients=pd.DataFrame([{'案家ID':'C','歷史首選':'A','是否失智':'是'}])
    assignments=pd.DataFrame([{'任務ID':'mon','派單居服員':'A'}])
    people=pd.DataFrame([worker(),worker('B',取得資格='失智症照顧服務20小時,BA08足部照護')])
    good=preview_client_assignment(visits,assignments,people,clients,config(client_primary_map={'C':'A'}),'C','B')
    assert len(good['accepted'])==2 and good['accepted']['派單居服員'].eq('B').all()
    assert good['primary_map']['C']=='B'
    bad=preview_client_assignment(visits,assignments,people,clients,config(client_primary_map={'C':'A'}),'C','A')
    assert len(bad['failed'])==1 and 'BA08' in bad['failed'].iloc[0]['原因']


def test_only_explicit_whole_client_review_updates_saved_owner(tmp_path):
    store=WorkflowStore('test',tmp_path/'workflow.sqlite3')
    store.save_primary_map({'C':'A'})
    store.set_reviewed_primary('C','B','居督確認整案固定人選')
    assert store.primary_map()=={'C':'B'}


def test_reviewed_template_can_extend_next_month_without_route_search(monkeypatch):
    people=pd.DataFrame([worker()]);clients=pd.DataFrame([{'案家ID':'C','歷史首選':'A'}])
    first=scheduler.run_fixed_period_dispatch(pd.DataFrame([task()]),people,clients,config())
    def forbidden(*args,**kwargs):raise AssertionError('The unchanged regular route must be copied')
    monkeypatch.setattr(scheduler,'_evaluate_reassignment',forbidden)
    visits=pd.DataFrame([task('next',day='2026-12-07')])
    result=scheduler.run_fixed_period_dispatch(visits,people,clients,config(),routine_templates=first['_day_templates'])
    assert len(result['df_result'])==1
    assert result['dispatch_stats']['copied_worker_days']==1


def test_reused_template_invalidates_changed_worker_cap():
    people=pd.DataFrame([worker()]);clients=pd.DataFrame([{'案家ID':'C','歷史首選':'A'}])
    visits=pd.DataFrame([task('a',start='09:00',end='11:00'),task('b',start='11:30',end='13:30')])
    first=scheduler.run_fixed_period_dispatch(visits,people,clients,config())
    assert len(first['df_result'])==2
    changed=visits.copy();changed['日期']='2026-11-09'
    people.loc[0,'每日工時上限(小時)']=3.9
    second=scheduler.run_fixed_period_dispatch(changed,people,clients,config(),routine_templates=first['_day_templates'])
    assert len(second['df_result'])==1 and second['dispatch_stats']['copied_worker_days']==0


def test_reused_template_invalidates_changed_transfer_buffer():
    people=pd.DataFrame([worker()]);clients=pd.DataFrame([{'案家ID':'C','歷史首選':'A'},{'案家ID':'D','歷史首選':'A'}])
    visits=pd.DataFrame([task('a'),task('b',client='D',start='10:30',end='11:30')])
    first=scheduler.run_fixed_period_dispatch(visits,people,clients,config())
    assert len(first['df_result'])==2
    changed=visits.copy();changed['日期']='2026-11-09'
    second=scheduler.run_fixed_period_dispatch(changed,people,clients,config(buffer_mins=40),routine_templates=first['_day_templates'])
    assert len(second['df_result'])==1 and second['dispatch_stats']['copied_worker_days']==0
