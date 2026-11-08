import datetime as dt
import pandas as pd
from agency_rules import consecutive_run_before
from caregiver_engine import iter_service_codes


def test_consecutive_run_before_accepts_strings_timestamps_and_bad_input():
    days = {'2026-11-01', '2026-11-02', '2026-11-04'}
    assert consecutive_run_before(days, '2026-11-03') == 2
    assert consecutive_run_before(days, pd.Timestamp('2026-11-03')) == 2
    assert consecutive_run_before(days, dt.date(2026, 11, 5)) == 1
    assert consecutive_run_before(days, '2026-11-01') == 0
    assert consecutive_run_before(days, 'not a date') == 0
    assert consecutive_run_before(set(), '2026-11-03') == 0
    assert consecutive_run_before({'2026-02-28'}, '2026/03/01') == 1


def test_iter_service_codes_same_result_for_repeated_and_different_keys():
    row = {'Service_Code_2': 'ba02', 'Units_2': 1, 'Service_Code_1': ' BA01 ', 'Units_1': '2', 'x': 1}
    assert list(iter_service_codes(row)) == [('BA01', 2.0), ('BA02', 1.0)]
    assert list(iter_service_codes(row)) == [('BA01', 2.0), ('BA02', 1.0)]
    assert list(iter_service_codes({'Service_Code_3': 'BA07', 'Units_3': 1})) == [('BA07', 1.0)]
    assert list(iter_service_codes(pd.Series(row))) == [('BA01', 2.0), ('BA02', 1.0)]
