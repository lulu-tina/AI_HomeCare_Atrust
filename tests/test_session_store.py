"""自動存檔與每位使用者獨立的交通估算設定。"""
import contextvars

import pandas as pd

import caregiver_engine as engine
import session_store


def test_snapshot_round_trip_keeps_result_overrides_and_file(tmp_path):
    db = str(tmp_path / 'store.sqlite3')
    matches = pd.DataFrame({'任務ID': ['t1']})
    matches.attrs['matching_diagnostics'] = [{'任務ID': 't2', '問題分類': '時段重疊'}]
    state = {'last_result': {'phase2': {'df_result': pd.DataFrame({'任務ID': ['t1'], '派單居服員': ['A']}),
                                       'df_matches': matches},
                             'df_matches': matches, '_plan_cache': {'big': 1}, 'route_result': pd.DataFrame({'x': [1]})},
             'overrides': {'t1': {'cg_id': 'B', 'reason': '案家要求換人'}},
             'cfg_buffer_mins': 20.0, 'unrelated_widget': 'skip me'}
    key = session_store.file_key(b'excel-bytes')
    session_store.save('機構', key, 'agency::abc', state, file_name='a.xlsx', file_bytes=b'excel-bytes', summary='摘要', path=db)
    found = session_store.info('機構', path=db)
    assert found['file'] == key and found['file_name'] == 'a.xlsx' and found['source'] == 'agency::abc'
    loaded, name, data, source = session_store.load('機構', key, path=db)
    assert name == 'a.xlsx' and data == b'excel-bytes' and source == 'agency::abc'
    assert loaded['overrides'] == state['overrides'] and loaded['cfg_buffer_mins'] == 20.0
    assert 'unrelated_widget' not in loaded and '_plan_cache' not in loaded['last_result']
    assert loaded['last_result']['df_matches'].attrs['matching_diagnostics'][0]['任務ID'] == 't2'
    # 同一份檔案再存一次只會更新內容，不會多一筆；Excel 本身保留。
    session_store.save('機構', key, 'agency::abc', {'overrides': {}}, file_name='a.xlsx', path=db)
    loaded, _, data, _ = session_store.load('機構', key, path=db)
    assert loaded['overrides'] == {} and data == b'excel-bytes'
    session_store.discard('機構', key, path=db)
    assert session_store.info('機構', key, path=db) is None


def test_transit_fallback_setting_is_per_session():
    default = engine.TRANSIT_FALLBACK['enabled']
    first, second = contextvars.copy_context(), contextvars.copy_context()

    def turn_off():
        engine.set_transit_fallback_enabled(False)
        return engine.transit_fallback_enabled()

    assert first.run(turn_off) is False
    # 另一位使用者（另一個連線）不受影響，全站預設也沒被改。
    assert second.run(engine.transit_fallback_enabled) == default
    assert engine.TRANSIT_FALLBACK['enabled'] == default
