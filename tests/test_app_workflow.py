"""Small synthetic workbook UI regression; no external routing requests."""
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import caregiver_engine as engine
from dispatch_workflow import WorkflowStore


def test_full_month_save_and_next_month_reuse(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CAREFLOW_AGENCY_DIR', str(tmp_path))
    monkeypatch.setattr(engine, 'calc_travel_minutes', lambda *a, **k: 5.0)
    monkeypatch.setattr(engine, 'prefetch_osrm_travel_times', lambda *a, **k: None)
    monkeypatch.setattr(engine, 'prefetch_google_transit_pairs', lambda *a, **k: None)
    import monthly_scheduler
    monkeypatch.setattr(monthly_scheduler, 'prefetch_osrm_travel_times', lambda *a, **k: None)
    monkeypatch.setattr(st, 'pyplot', lambda *a, **k: None)
    rankings=[]
    rank_original=engine.rank_candidates_by_availability
    def rank_when_requested(*args,**kwargs):
        rankings.append(args[0]);return rank_original(*args,**kwargs)
    monkeypatch.setattr(engine,'rank_candidates_by_availability',rank_when_requested)
    source_reads=[]
    source_loader=engine.load_agency_workbook
    def load_changed_source(*args,**kwargs):
        source_reads.append(True);return source_loader(*args,**kwargs)
    monkeypatch.setattr(engine,'load_agency_workbook',load_changed_source)
    employees=pd.DataFrame([{'編號':identifier,'性別':'女','休息日':'星期日','取得資格':'失智症照顧服務20小時',
                              '交通工具':'機車','經度 (WGS84_Lon)':121.53,'緯度 (WGS84_Lat)':25.03} for identifier in ['A','B']])
    clients=pd.DataFrame([{'內部案號':'C','經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04}])
    weekly=pd.DataFrame([{'案號':'C','星期':weekday,'開始時間':start,'結束時間':end,'服務分鐘':60,
                          '頻率':'每週','生效日':'2026-11-02','服務項目':'SA10(1)','費用類型':'基本',
                          '經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04}
                         for weekday,start,end in [('星期一','09:00','10:00'),('星期三','15:00','16:00')]])
    master=pd.DataFrame([{'系統代碼':'SA10','服務名稱':'自費服務','目前給付價格(元)':None,
                          'CareFlow排班分鐘(暫定)':60,'是否納入CareFlow':'是'}])
    with pd.ExcelWriter(tmp_path/'居服機構資料_三工作表.xlsx',engine='openpyxl') as writer:
        for name,frame in [('員工資料',employees),('個案資料',clients),('週服務計畫',weekly),('Service_code',master),
                            ('案家姓名對照',pd.DataFrame(columns=['案家ID','姓名'])),('居服員姓名對照',pd.DataFrame(columns=['居服員ID','姓名']))]:
            frame.to_excel(writer,sheet_name=name,index=False)
    app_path=Path(engine.__file__).with_name('app.py')
    app=AppTest.from_file(str(app_path),default_timeout=60).run()
    assert not app.exception
    app.date_input(key='agency_range_start').set_value(date(2026,11,1)).run()
    read_count=len(source_reads)
    app.radio(key='cf_module').set_value('schedule').run()   # 開始排班在「02 智慧排班作業」
    assert not app.exception
    app.button(key='run_full_period').click().run()
    assert not app.exception,[e.message for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    result=app.session_state['last_result']
    assert result['client_primary_map']=={'C':'A'}
    assert len(result['phase2']['df_result'])==len(result['df_tasks'])==9
    hidden_keys={'cfg_sa10_price','cfg_hourly_wage','cfg_transition_wage','cfg_daily_cap','cfg_monthly_ot'}
    assert not hidden_keys.intersection({w.key for w in app.number_input})
    assert set(app.session_state['last_result']['phase2']['df_result']['預估服務營收'])=={350}
    assert not rankings # Opening a schedule must not compute manual candidates.
    app.button(key='override_check_candidates').click().run()
    assert not app.exception and len(rankings)==1
    app.run()
    assert not app.exception and len(rankings)==1
    assert len(source_reads)==read_count
    app.button(key='save_client_primary').click().run()
    assert not app.exception
    assert WorkflowStore('中化').primary_map()=={'C':'A'}
    store=WorkflowStore('中化')
    store.add_blocks('A','2026-11-04',[('15:00','16:00')],'請假')
    app.button(key='run_full_period').click().run()
    assert not app.exception,[e.message for e in app.exception]
    backup=app.session_state['last_result']['phase2']['df_result']
    backup=backup[backup['排班日期'].eq('2026-11-04')].iloc[0]
    assert backup['派單居服員']=='B' and backup['是否請假代班']
    store.remove_block(store.blocks()[0]['id'])
    app.run()
    assert not app.exception
    from calendar_view import apply_overrides_to_result
    effective=apply_overrides_to_result(app.session_state['last_result']['phase2']['df_result'],app.session_state['overrides'])
    # Removing leave no longer makes an otherwise qualified partner invalid.
    assert backup['任務ID'] in set(effective['任務ID'])
    assert store.primary_map()=={'C':'A'}
    app.radio(key='cf_module').set_value('import').run()     # 排班期間在「01 資料匯入與排班設定」
    app.date_input(key='agency_range_start').set_value(date(2026,12,1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    assert app.session_state['agency_range_start']==date(2026,12,1)   # 換頁後期間仍保留
    app.button(key='run_full_period').click().run()
    assert not app.exception,[e.message for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    result=app.session_state['last_result']
    assert result['client_primary_map']=={'C':'A'}
    assert result['phase2']['df_result']['派單居服員'].eq('A').all()
    app.radio(key='cf_module').set_value('import').run()
    app.selectbox(key='agency_period').select('一年').run()
    assert not app.exception
    assert app.date_input(key='agency_range_end').value==date(2027,11,30)


def test_pending_clients_bulk_apply_resolves_all_weeks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CAREFLOW_AGENCY_DIR', str(tmp_path))
    monkeypatch.setattr(engine,'calc_travel_minutes',lambda *a,**k:5.0)
    monkeypatch.setattr(engine,'prefetch_osrm_travel_times',lambda *a,**k:None)
    monkeypatch.setattr(engine,'prefetch_google_transit_pairs',lambda *a,**k:None)
    import monthly_scheduler
    monkeypatch.setattr(monthly_scheduler,'prefetch_osrm_travel_times',lambda *a,**k:None)
    monkeypatch.setattr(st,'pyplot',lambda *a,**k:None)
    employees=pd.DataFrame([{'編號':i,'性別':'女','休息日':'星期日','取得資格':'失智症照顧服務20小時',
        '交通工具':'機車','經度 (WGS84_Lon)':121.53,'緯度 (WGS84_Lat)':25.03} for i in ['A','B']])
    clients=pd.DataFrame([{'內部案號':i,'歷史首選':'B' if i=='C3' else 'A','是否失智':'否',
        '經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04} for i in ['C1','C2','C3']])
    weekly=pd.DataFrame([{'案號':i,'星期':'星期一','開始時間':'09:00','結束時間':'10:00','服務分鐘':60,
        '頻率':'每週','生效日':'2026-11-02','服務項目':'SA10(1)','費用類型':'自費',
        '經度 (WGS84_Lon)':121.54,'緯度 (WGS84_Lat)':25.04} for i in ['C1','C2','C3']])
    master=pd.DataFrame([{'系統代碼':'SA10','服務名稱':'自費服務','目前給付價格(元)':None,
        'CareFlow排班分鐘(暫定)':60,'是否納入CareFlow':'是'}])
    with pd.ExcelWriter(tmp_path/'居服機構資料_三工作表.xlsx',engine='openpyxl') as writer:
        for name,frame in [('員工資料',employees),('個案資料',clients),('週服務計畫',weekly),('Service_code',master)]:
            frame.to_excel(writer,sheet_name=name,index=False)
    app=AppTest.from_file(str(Path(engine.__file__).with_name('app.py')),default_timeout=60).run()
    app.date_input(key='agency_range_start').set_value(date(2026,11,1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    assert not app.exception,[e.message for e in app.exception]
    app.radio(key='cf_module').set_value('issues').run()     # 未派案家整案處理在「03 衝突與異常處理」
    assert not app.exception,[e.message for e in app.exception]
    grouped=app.session_state['pending_client_summary']
    assert len(grouped)==1 and grouped.iloc[0]['案家ID']=='C2' and grouped.iloc[0]['未派服務數']==5
    app.selectbox(key='pending_client_worker_C2').select('B')
    editor=st.data_editor
    def edit_weekly_pattern(frame,*args,**kwargs):
        result=editor(frame,*args,**kwargs)
        if kwargs.get('key')=='pending_client_patterns_C2':
            result=result.copy()
            result.loc[:,['開始時間','結束時間','居服員ID']]=['10:30','11:30','B']
        return result
    monkeypatch.setattr(st,'data_editor',edit_weekly_pattern)
    next(b for b in app.button if b.label=='預覽整案排班').click().run()
    assert not app.exception,[e.message for e in app.exception]
    preview=app.session_state['pending_client_preview']['result']
    assert len(preview['accepted'])==5 and preview['failed'].empty
    app.button(key='apply_pending_client').click().run()
    assert not app.exception,[e.message for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    assert app.session_state['pending_client_summary'].empty
    assert app.session_state['last_result']['client_primary_map']['C2']=='B'
    assert len(app.session_state['overrides'])==5
    assert WorkflowStore('中化').primary_map()['C2']=='B'
    app.run()
    assert not app.exception and app.session_state['pending_client_summary'].empty
