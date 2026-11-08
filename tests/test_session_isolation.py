"""多人同時使用：一位使用者的交通設定與估算紀錄，不能影響另一位（Streamlit 所有人共用同一個程式）。"""
import contextvars
from datetime import date

import pandas as pd

import caregiver_engine as engine


def _in_new_session(fn):
    return contextvars.Context().run(fn)


def test_transit_fallback_marks_are_per_session():
    def user_a():
        engine.begin_travel_query_run()
        engine._google_query_state()['fallback_pairs'].add(engine._pair_key(25.0, 121.5, 25.01, 121.51))
        return engine.transit_fallback_used(25.0, 121.5, 25.01, 121.51)

    def user_b():
        engine.begin_travel_query_run()
        return engine.transit_fallback_used(25.0, 121.5, 25.01, 121.51)

    assert _in_new_session(user_a) is True
    assert _in_new_session(user_b) is False          # A 用過估算，不會讓 B 的班表也被標成估算


def test_fallback_switch_is_per_session():
    def turn_off():
        engine.set_transit_fallback_enabled(False)
        return engine.transit_fallback_enabled()

    assert _in_new_session(turn_off) is False
    assert _in_new_session(engine.transit_fallback_enabled) is bool(engine.TRANSIT_FALLBACK['enabled'])


def test_month_ledger_memo_not_shared_across_fallback_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, '_route_day_totals', lambda *a, **k: calls.append(1) or {'2026-11-02': 60.0})
    monkeypatch.setattr(engine, 'osrm_endpoint', lambda: ('http://osrm', 'motorcycle'))
    engine._LEDGER_MEMO.clear()
    tasks = pd.DataFrame([{'任務ID': 'T1', '日期': '2026-11-02', '時間窗_開始': '09:00', '時間窗_結束': '10:00', '案家ID': 'C'}])
    assigned = pd.DataFrame([{'任務ID': 'T1', '派單居服員': 'A'}])
    people = pd.DataFrame([{'居服員ID': 'A'}])
    config = engine.PipelineConfig()

    def ledger(enabled):
        def run():
            engine.set_transit_fallback_enabled(enabled)
            return engine._month_totals_memo('A', pd.Timestamp('2026-11-03'), tasks, assigned, people, config)
        return run

    _in_new_session(ledger(True))
    _in_new_session(ledger(True))                    # 同樣設定：沿用快取
    assert len(calls) == 1
    _in_new_session(ledger(False))                   # 另一位關掉估算：要重算，不能拿別人的結果
    assert len(calls) == 2
