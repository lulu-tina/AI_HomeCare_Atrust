"""週計畫異動（生效日起改時段）：純計算＋Excel 寫回＋排班引擎重新展開。"""
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import pytest
from openpyxl import load_workbook

import caregiver_engine as engine
import plan_versions as pv

KW = dict(daily_start='08:00', daily_end='21:00', daily_cap=12.0,
          availability_confirmed=True, biweekly_anchor_confirmed=True)


def _workbook(weekly_rows, with_expiry=True):
    employees = pd.DataFrame([{'編號': 'A', '性別': '女', '休息日': '星期日', '取得資格': '', '交通工具': '機車',
                               '經度 (WGS84_Lon)': 121.53, '緯度 (WGS84_Lat)': 25.03}])
    clients = pd.DataFrame([{'內部案號': c, '經度 (WGS84_Lon)': 121.54, '緯度 (WGS84_Lat)': 25.04} for c in ('C', 'D')])
    weekly = pd.DataFrame(weekly_rows)
    if not with_expiry:
        weekly = weekly.drop(columns=['到期日'], errors='ignore')
    master = pd.DataFrame([{'系統代碼': code, '服務名稱': code, '目前給付價格(元)': 200, 'CareFlow排班分鐘(暫定)': 30}
                           for code in ('BA02', 'BA20')])
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        employees.to_excel(writer, sheet_name='員工資料', index=False)
        clients.to_excel(writer, sheet_name='個案資料', index=False)
        weekly.to_excel(writer, sheet_name='週服務計畫', index=False)
        master.to_excel(writer, sheet_name='Service_code', index=False)
    return out.getvalue()


def _row(client, weekday, start, end, begin, items='BA02(1) BA20(1)', frequency='每週', expiry=None):
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return {'案號': client, '星期': weekday, '開始時間': start + ':00', '結束時間': end + ':00', '服務時數': minutes / 60,
            '服務分鐘': minutes, '費用類型': '基本', '頻率': frequency, '生效日': begin, '到期日': expiry,
            '服務項目': items, '經度 (WGS84_Lon)': '121.54', '緯度 (WGS84_Lat)': '25.04'}


# C 的週一 2026-10 起、週三 2026-11-05 起（機構資料常見：後來追加的時段，不是取代）；D 是其他案家
BASE = [_row('C', '星期一', '09:00', '10:00', '2026-10-01'),
        _row('C', '星期三', '14:00', '15:00', '2026-11-05'),
        _row('D', '星期一', '09:00', '10:00', '2026-10-01')]


def _tasks(data):
    return engine.load_agency_workbook(BytesIO(data), '2026-11-01', '2026-11-30', **KW)['Tasks']


def _edit(frame):
    return frame[pv.EDIT_COLUMNS].copy()


def test_additive_rows_with_different_start_dates_are_all_current():
    weekly = pv.read_weekly(_workbook(BASE))
    current, future = pv.plan_as_of(weekly, 'C', date(2026, 11, 16))
    assert current['星期'].tolist() == ['星期一', '星期三']
    assert future.empty
    current, future = pv.plan_as_of(weekly, 'C', date(2026, 11, 2))
    assert current['星期'].tolist() == ['星期一'] and future['星期'].tolist() == ['星期三']


def test_build_change_keeps_unchanged_rows_and_splits_changed_ones():
    weekly = pv.read_weekly(_workbook(BASE))
    current, _ = pv.plan_as_of(weekly, 'C', date(2026, 11, 16))
    edited = _edit(current)
    edited.loc[edited['星期'] == '星期一', ['開始', '結束']] = ['10:00', '11:00']
    edited = pd.concat([edited, pd.DataFrame([{'星期': '星期五', '開始': '09:00', '結束': '09:30', '頻率': '每週',
                                               '服務項目': 'ba02（1）'}])], ignore_index=True)
    change, errors = pv.build_change(weekly, 'C', '2026-11-16', edited, ['BA02', 'BA20'])
    assert errors == []
    assert change.stop == [2] and change.keep == [3]
    assert [(s['星期'], s['開始'], s['服務項目']) for s in change.add] == [('星期一', '10:00', 'BA02(1) BA20(1)'),
                                                                        ('星期五', '09:00', 'BA02(1)')]
    assert change.expire_on == date(2026, 11, 15)
    assert change.summary()[1:] == ['改時間：星期一 09:00–10:00 → 10:00–11:00（每週）BA02(1) BA20(1)',
                                    '新增：星期五 09:00–09:30（每週）BA02(1)', '不變：1 個時段']


@pytest.mark.parametrize('edited,message', [
    (None, '完全一樣'),
    ([{'星期': '星期一', '開始': '9點', '結束': '10:00', '頻率': '每週', '服務項目': 'BA02(1)'}], 'HH:MM'),
    ([{'星期': '星期一', '開始': '10:00', '結束': '09:00', '頻率': '每週', '服務項目': 'BA02(1)'}], '晚於'),
    ([{'星期': '星期一', '開始': '09:00', '結束': '10:00', '頻率': '每週', '服務項目': 'BA99(1)'}], '不在 Service_code'),
    ([{'星期': '星期一', '開始': '09:00', '結束': '10:00', '頻率': '每週', '服務項目': 'BA02(1)'},
      {'星期': '星期一', '開始': '09:30', '結束': '11:00', '頻率': '每週', '服務項目': 'BA20(1)'}], '重疊'),
    ([], '空的'),
])
def test_build_change_rejects_invalid_or_empty_input(edited, message):
    weekly = pv.read_weekly(_workbook(BASE))
    if edited is None:
        edited = _edit(pv.plan_as_of(weekly, 'C', date(2026, 11, 16))[0])
    change, errors = pv.build_change(weekly, 'C', '2026-11-16', pd.DataFrame(edited, columns=pv.EDIT_COLUMNS), ['BA02', 'BA20'])
    assert change is None and any(message in e for e in errors), errors


@pytest.mark.parametrize('numeric_dates', [False, True])
def test_write_change_round_trip_only_touches_client_after_effective_date(numeric_dates):
    rows = [dict(r) for r in BASE]
    if numeric_dates:
        for r in rows:
            r['生效日'] = datetime.fromisoformat(r['生效日'])
    source = _workbook(rows)
    weekly = pv.read_weekly(source)
    edited = _edit(pv.plan_as_of(weekly, 'C', date(2026, 11, 16))[0])
    edited.loc[edited['星期'] == '星期一', ['開始', '結束']] = ['10:00', '11:00']
    change, errors = pv.build_change(weekly, 'C', '2026-11-16', edited)
    assert not errors
    updated, added_rows = pv.write_change(source, change)
    assert added_rows == [5]

    sheet = load_workbook(BytesIO(updated))['週服務計畫']
    headers = [c.value for c in sheet[1]]
    expiry = sheet.cell(row=2, column=headers.index('到期日') + 1).value
    begin = sheet.cell(row=5, column=headers.index('生效日') + 1).value
    if numeric_dates:
        assert isinstance(expiry, datetime) or isinstance(expiry, (int, float))
    else:
        assert expiry == '2026-11-15' and begin == '2026-11-16'
    assert load_workbook(BytesIO(updated)).sheetnames == load_workbook(BytesIO(source)).sheetnames

    before, after = _tasks(source), _tasks(updated)
    removed, added = pv.task_delta(before, after, 'C', '2026-11-16')
    assert removed == ['W0002_20261116', 'W0002_20261123', 'W0002_20261130']
    assert added['任務ID'].tolist() == ['W0005_20261116', 'W0005_20261123', 'W0005_20261130']
    assert added['時間窗_開始'].tolist() == ['10:00'] * 3
    # 生效日前的 C、所有 D、未改動的週三：任務ID 與時間完全相同
    keep = lambda t: t[(t['案家ID'] == 'D') | (t['日期'] < '2026-11-16') | (t['任務ID'].str.startswith('W0003'))]
    cols = ['任務ID', '日期', '時間窗_開始', '時間窗_結束']
    pd.testing.assert_frame_equal(keep(before)[cols].reset_index(drop=True), keep(after)[cols].reset_index(drop=True))


def test_missing_expiry_column_is_added_and_second_change_builds_on_first():
    source = _workbook(BASE, with_expiry=False)
    weekly = pv.read_weekly(source)
    edited = _edit(pv.plan_as_of(weekly, 'C', date(2026, 11, 9))[0])
    edited = edited[edited['星期'] != '星期一']           # 11/9 起停掉週一（週三 11/5 已生效，保留）
    change, errors = pv.build_change(weekly, 'C', '2026-11-09', edited)
    assert not errors and change.stop == [2] and change.add == []
    first, _ = pv.write_change(source, change)
    assert '到期日' in pv.read_weekly(first).columns
    weekly = pv.read_weekly(first)
    current, _ = pv.plan_as_of(weekly, 'C', date(2026, 11, 23))
    assert current['星期'].tolist() == ['星期三']
    edited = pd.concat([_edit(current), pd.DataFrame([{'星期': '星期二', '開始': '08:00', '結束': '09:00',
                                                       '頻率': '每週', '服務項目': 'BA20(1)'}])], ignore_index=True)
    change, errors = pv.build_change(weekly, 'C', '2026-11-23', edited)
    second, rows = pv.write_change(first, change)
    tasks = _tasks(second)
    mondays = tasks[(tasks['案家ID'] == 'C') & (tasks['星期'] == '星期一')]['日期'].tolist()
    tuesdays = tasks[(tasks['案家ID'] == 'C') & (tasks['星期'] == '星期二')]['日期'].tolist()
    assert mondays == ['2026-11-02'] and tuesdays == ['2026-11-24']


def test_biweekly_anchor_of_untouched_rows_is_preserved():
    rows = [_row('C', '星期一', '09:00', '10:00', '2026-10-05', frequency='隔週'),
            _row('C', '星期四', '09:00', '10:00', '2026-10-01')]
    source = _workbook(rows)
    weekly = pv.read_weekly(source)
    edited = _edit(pv.plan_as_of(weekly, 'C', date(2026, 11, 12))[0])
    edited.loc[edited['星期'] == '星期四', ['開始', '結束']] = ['13:00', '14:00']
    change, _ = pv.build_change(weekly, 'C', '2026-11-12', edited)
    assert change.keep == [2]
    updated, _ = pv.write_change(source, change)
    biweekly = lambda data: _tasks(data).query("星期 == '星期一'")['日期'].tolist()
    assert biweekly(source) == biweekly(updated) == ['2026-11-02', '2026-11-16', '2026-11-30']


def test_write_change_for_unknown_client_raises():
    source = _workbook(BASE)
    change = pv.PlanChange(client='Z', effective=date(2026, 11, 16),
                           add=[{'星期': '星期一', '開始': '09:00', '結束': '10:00', '頻率': '每週', '服務項目': 'BA02(1)'}])
    with pytest.raises(ValueError, match='找不到案家'):
        pv.write_change(source, change)


def test_menu_helpers_compose_and_examples():
    weekly = pv.read_weekly(_workbook(BASE))
    current, _ = pv.plan_as_of(weekly, 'C', date(2026, 11, 16))
    for kind, expect in [('move', '改時間：星期一 09:00–10:00 → 10:00–11:00'), ('stop', '停用：星期三'), ('add', '新增：星期二')]:
        actions, new_slots, text = pv.example_actions(current, kind)
        change, errors = pv.build_change(weekly, 'C', '2026-11-16', pv.compose_edit(current, actions, new_slots))
        assert not errors and change.summary()[1].startswith(expect), (kind, change.summary())
        assert text.startswith('範例')
    assert '08:50' in pv.time_options(['08:50']) and pv.time_options()[0] == '06:00'
    actions, new_slots, _ = pv.example_actions(current.iloc[0:0], 'move')   # 沒有現行時段時只能示範新增
    assert actions == [] and new_slots[0]['星期'] == '星期一'
