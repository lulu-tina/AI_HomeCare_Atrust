"""代班挑選順序：熟悉 → 交通區段（前一案）→ 本月工時 → 年資。不需要交通服務。"""
from types import SimpleNamespace

from substitute_rank import (build_profile, familiarity_counts, incoming_leg, order_workers, rank_key,
                             travel_band)

CONFIG = SimpleNamespace(normal_daily_hours=8.0, max_monthly_overtime_hours=46.0, work_day_minutes={})
TASK = {'任務ID': 'T', '案家ID': 'C', '日期': '2026-10-14', '時間窗_開始': '14:00', '時間窗_結束': '15:00',
        '服務地點_緯度': 25.0, '服務地點_經度': 121.5}


def _ledger(hours_by_worker, extra_people=0, extra_hours=100):
    """把本月工時平均分成 20 天記錄。"""
    ledger = {}
    for worker, hours in list(hours_by_worker.items()) + [(f'X{i}', extra_hours) for i in range(extra_people)]:
        for d in range(1, 21):
            ledger[(worker, f'2026-10-{d:02d}')] = hours * 60 / 20
    return ledger


def _person(years):
    return {'到職日': f'{2026 - years}-01-01', '服務起點_緯度(家)': 25.1, '服務起點_經度(家)': 121.6, '常用交通工具': '機車'}


def test_travel_band_edges():
    assert travel_band(3) == (0, '順路') and travel_band(10) == (0, '順路')
    assert travel_band(10.5)[1] == '可接受' and travel_band(20)[1] == '可接受'
    assert travel_band(21)[1] == '較遠' and travel_band(None)[1] == '交通未知'


def test_example_from_discussion_picks_familiar_and_nearby():
    """甲：沒服務過、家裡 5 分、60h、8 年；乙：服務過、前一案 3 分、110h、2 年；丙：服務過、家裡 15 分、70h、5 年。"""
    ledger = _ledger({'甲': 60, '乙': 110, '丙': 70}, extra_people=5, extra_hours=110)
    profiles = [
        build_profile('甲', TASK, _person(8), CONFIG, familiar=0, ledger=ledger, known_minutes=5, known_from='家裡'),
        build_profile('乙', TASK, _person(2), CONFIG, familiar=6, ledger=ledger, known_minutes=3, known_from='前一案'),
        build_profile('丙', TASK, _person(5), CONFIG, familiar=6, ledger=ledger, known_minutes=15, known_from='家裡'),
    ]
    assert [p['worker'] for p in order_workers(profiles)] == ['乙', '丙', '甲']
    assert '服務過 6 次' in profiles[1]['reason'] and '前一案過來 3 分（順路）' in profiles[1]['reason']


def test_same_band_is_a_tie_then_lower_hours_then_seniority():
    ledger = _ledger({'A': 90, 'B': 70, 'C': 70})
    a = build_profile('A', TASK, _person(9), CONFIG, familiar=0, ledger=ledger, known_minutes=2, known_from='家裡')
    b = build_profile('B', TASK, _person(1), CONFIG, familiar=0, ledger=ledger, known_minutes=9, known_from='家裡')
    c = build_profile('C', TASK, _person(6), CONFIG, familiar=0, ledger=ledger, known_minutes=8, known_from='家裡')
    # 2 分與 9 分同屬「順路」→ 比工時：B、C 70h 比 A 90h 少；B、C 再比年資 → C 在前。
    assert [p['worker'] for p in order_workers([a, b, c])] == ['C', 'B', 'A']


def test_overloaded_familiar_worker_is_demoted():
    ledger = _ledger({'熟': 150, '新': 80}, extra_people=4, extra_hours=90)
    busy = build_profile('熟', TASK, _person(5), CONFIG, familiar=10, ledger=ledger, known_minutes=4, known_from='前一案')
    fresh = build_profile('新', TASK, _person(5), CONFIG, familiar=0, ledger=ledger, known_minutes=4, known_from='前一案')
    assert busy['demoted'] and not fresh['demoted']
    # 降級後不享熟悉優先：同交通區段，工時少的「新」在前。
    assert [p['worker'] for p in order_workers([busy, fresh])] == ['新', '熟']
    assert '不優先' in busy['reason']


def test_near_overtime_limit_is_last_resort():
    # 每天 10 小時 × 20 天 → 加班 40 小時，達 46 小時上限的八成以上。
    ledger = _ledger({'累': 200, '可': 100})
    tired = build_profile('累', TASK, _person(5), CONFIG, familiar=5, ledger=ledger, known_minutes=1, known_from='前一案')
    other = build_profile('可', TASK, _person(1), CONFIG, familiar=0, ledger=ledger, known_minutes=25, known_from='家裡')
    assert tired['skipped'] and not other['skipped']
    assert rank_key(other) < rank_key(tired)


def test_familiarity_counts_and_incoming_leg():
    assignments = [('t1', 'A'), ('t2', 'A'), ('t3', 'B'), ('t4', '未指派')]
    clients = {'t1': 'C', 't2': 'C', 't3': 'D', 't4': 'C'}
    counts = familiarity_counts(assignments, clients, backup_map={'C': ('E',)}, support_map={'t3': 'F'})
    assert counts[('C', 'A')] == 2 and counts[('C', 'E')] == 1 and counts[('D', 'F')] == 1 and counts[('C', '未指派')] == 0
    fake = lambda la1, lo1, la2, lo2, cfg, transport_mode='機車': 7.0
    prev = [{'start': '12:00', 'end': '13:00', 'lat': 25.01, 'lon': 121.51, 'client': 'Z'}]
    assert incoming_leg(TASK, _person(3), prev, CONFIG, fake) == (7.0, '前一案', False)
    assert incoming_leg(TASK, _person(3), [], CONFIG, fake)[1] == '家裡'
    transit = {**_person(3), '常用交通工具': '大眾運輸'}
    assert incoming_leg(TASK, transit, [], CONFIG, fake) == (7.0 * 1.5 + 10.0, '家裡', True)
    same = [{'start': '13:00', 'end': '14:00', 'lat': 25.0, 'lon': 121.5, 'client': 'C'}]
    assert incoming_leg(TASK, _person(3), same, CONFIG, fake)[0] == 0.0
