"""畫面流程：排班完成後，在「03 › 臨時異動 › 週計畫異動」改某案家生效日後的時段並套用。"""
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import caregiver_engine as engine
import plan_versions as pv


def _setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CAREFLOW_AGENCY_DIR', str(tmp_path))
    monkeypatch.setenv('CAREFLOW_AUDIT_DIR', str(tmp_path / 'audit'))
    monkeypatch.setenv('CAREFLOW_CHECKIN_DB', str(tmp_path / 'checkin.sqlite3'))   # 打卡資料庫也隔離，不碰正式資料
    monkeypatch.setenv('CAREFLOW_CHECKIN_AUTOSTART', '0')                          # 測試不要真的開 8600 埠
    monkeypatch.setenv('CAREFLOW_AUTH', 'off')                                     # 這些測試專注排班流程；登入另有測試
    monkeypatch.setattr(engine, 'calc_travel_minutes', lambda *a, **k: 5.0)
    import traffic_wake
    monkeypatch.setattr(traffic_wake, 'is_ready', lambda host: True)   # 測試不等真的交通主機開機
    monkeypatch.setattr(engine, 'prefetch_osrm_travel_times', lambda *a, **k: None)
    monkeypatch.setattr(engine, 'prefetch_google_transit_pairs', lambda *a, **k: None)
    import monthly_scheduler
    monkeypatch.setattr(monthly_scheduler, 'prefetch_osrm_travel_times', lambda *a, **k: None)
    monkeypatch.setattr(st, 'pyplot', lambda *a, **k: None)
    employees = pd.DataFrame([{'編號': i, '性別': '女', '休息日': '星期日', '取得資格': '失智症照顧服務20小時',
                               '交通工具': '機車', '經度 (WGS84_Lon)': 121.53, '緯度 (WGS84_Lat)': 25.03} for i in ['A', 'B']])
    clients = pd.DataFrame([{'內部案號': c, '歷史首選': 'A', '是否失智': '否', '經度 (WGS84_Lon)': 121.54,
                             '緯度 (WGS84_Lat)': 25.04} for c in ['C', 'D']])
    weekly = pd.DataFrame([{'案號': c, '星期': d, '開始時間': s, '結束時間': e, '服務分鐘': 60, '頻率': '每週',
                            '生效日': '2026-11-02', '服務項目': 'SA10(1)', '費用類型': '自費',
                            '經度 (WGS84_Lon)': 121.54, '緯度 (WGS84_Lat)': 25.04}
                           for c, d, s, e in [('C', '星期一', '09:00', '10:00'), ('C', '星期三', '15:00', '16:00'),
                                              ('D', '星期二', '09:00', '10:00')]])
    master = pd.DataFrame([{'系統代碼': 'SA10', '服務名稱': '自費服務', '目前給付價格(元)': None,
                            'CareFlow排班分鐘(暫定)': 60, '是否納入CareFlow': '是'}])
    with pd.ExcelWriter(tmp_path / '居服機構資料_三工作表.xlsx', engine='openpyxl') as writer:
        for name, frame in [('員工資料', employees), ('個案資料', clients), ('週服務計畫', weekly), ('Service_code', master)]:
            frame.to_excel(writer, sheet_name=name, index=False)


def test_plan_change_replaces_only_tasks_from_effective_date(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    assert not app.exception, [e.message for e in app.exception]
    before = app.session_state['last_result']['df_tasks'].copy()
    assert {'W0002_20261116', 'W0002_20261123', 'W0002_20261130'} <= set(before['任務ID'])

    app.radio(key='cf_module').set_value('issues').run()
    app.session_state['cf_issue_view'] = '週計畫異動'
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    app.selectbox(key='pv_client').select('C').run()
    app.date_input(key='pv_effective').set_value(date(2026, 11, 16)).run()
    assert not app.exception, [e.message for e in app.exception]

    # 還沒改任何東西：預覽按鈕不能按
    assert app.button(key='pv_preview_btn').disabled

    # 按「範例：改時間」：第一個時段（週一 09:00-10:00）往後 1 小時，週三不變
    app.button(key='pv_ex_move_C_2026-11-16').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.selectbox(key='pv_act_C_2026-11-16_0').value == '改時間'
    assert app.selectbox(key='pv_s_C_2026-11-16_0').value == '10:00'
    assert any('範例' in i.value for i in app.info)
    assert any('改時間：星期一 09:00–10:00 → 10:00–11:00' in m.value for m in app.markdown)
    app.button(key='pv_preview_btn').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    preview = app.session_state['pv_preview']
    assert preview['removed'] == ['W0002_20261116', 'W0002_20261123', 'W0002_20261130']
    assert [p['task']['任務ID'] for p in preview['picks']] == ['W0005_20261116', 'W0005_20261123', 'W0005_20261130']
    assert all(p['cg_id'] == 'A' for p in preview['picks'])

    app.button(key='pv_commit').click().run()
    assert not app.exception, [e.message for e in app.exception]
    result = app.session_state['last_result']
    after = result['df_tasks']
    ids = set(after['任務ID'])
    assert not ids & set(preview['removed'])
    assert {'W0005_20261116', 'W0005_20261123', 'W0005_20261130'} <= ids
    unchanged = before[~before['任務ID'].isin(preview['removed'])]
    pd.testing.assert_frame_equal(
        unchanged[['任務ID', '日期', '時間窗_開始']].reset_index(drop=True),
        after[after['任務ID'].isin(unchanged['任務ID'])][['任務ID', '日期', '時間窗_開始']].reset_index(drop=True))
    assert not set(result['phase2']['df_result']['任務ID']) & set(preview['removed'])
    overrides = app.session_state['overrides']
    assert all(overrides[t]['cg_id'] == 'A' for t in ['W0005_20261116', 'W0005_20261123', 'W0005_20261130'])
    assert result['plan_version_log'][0]['生效日'] == '2026-11-16'

    # 更新機構 Excel：寫回的是異動後版本，重新匯入可得到同樣的班次
    weekly = pv.read_weekly(result['plan_version_bytes'])
    assert weekly.loc[weekly['來源列'] == 2, '到期日'].astype(str).iloc[0].startswith('2026-11-15')
    assert weekly.loc[weekly['來源列'] == 5, '開始時間'].iloc[0] == '10:00:00'
    reloaded = engine.load_agency_workbook(BytesIO(result['plan_version_bytes']), '2026-11-01', '2026-11-30')['Tasks']
    assert set(reloaded['任務ID']) == ids

    app.radio(key='cf_module').set_value('review').run()
    assert not app.exception, [e.message for e in app.exception]
    app.button(key='build_source_update').click().run()
    # AppTest 不支援 fragment 內的 st.rerun(scope='fragment')（瀏覽器正常）；只容許這一種例外
    assert all('scope="fragment"' in e.message for e in app.exception), [e.message for e in app.exception]
    exported = app.session_state['_source_update_cache'][1]
    assert set(engine.load_agency_workbook(BytesIO(exported), '2026-11-01', '2026-11-30')['Tasks']['任務ID']) == ids


def test_worker_panel_assigns_ticked_open_slots(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = AppTest.from_file(str(Path(engine.__file__).with_name('app.py')), default_timeout=90).run()
    app.date_input(key='agency_range_start').set_value(date(2026, 11, 1)).run()
    app.radio(key='cf_module').set_value('schedule').run()
    app.button(key='run_full_period').click().run()
    assert not app.exception, [e.message for e in app.exception]
    tasks = app.session_state['last_result']['df_tasks']
    tuesdays = tasks[tasks['案家ID'] == 'D']['任務ID'].tolist()
    for tid in tuesdays:                                    # 把 D 的週二都改成未排
        app.session_state['overrides'][tid] = {'cg_id': None, 'reason': '測試：改成未排'}
    app.session_state['cf_calendar_mode'] = '個人週班'
    app.session_state['cf_person'] = 'A'                    # A 是 D 的固定居服員
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    assert any('以居服員排班' in m.value for m in app.markdown)
    editor = 'wp_pick_A_1'                                  # 只有 D 週二這一個未排時段
    tick = {'edited_rows': {0: {'排入': True}}, 'added_rows': [], 'deleted_rows': []}
    app.session_state[editor] = tick
    app.run()                                               # 勾選後按鈕才會亮
    assert not app.button(key='wp_assign').disabled
    app.session_state[editor] = tick                        # AppTest 每次重跑都要重給表格的勾選狀態
    app.button(key='wp_assign').click().run()
    assert not app.exception, [e.message for e in app.exception]
    overrides = app.session_state['overrides']
    assert all(overrides[t]['cg_id'] == 'A' for t in tuesdays)
    assert any('已排入' in s.value for s in app.success)
