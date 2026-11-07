"""案家登錄「不適合居服員」後，自動排班不會再派給那位居服員。"""
import pathlib
import sys

import pandas as pd
import pytest

import caregiver_engine as e

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBOOK = ROOT / '居服機構資料_三工作表_v9.xlsx'


def test_parse_unsuitable_ids():
    assert e.unsuitable_caregivers('H1、M04, 03816／X9  Y2') == {'H1', 'M04', '03816', 'X9', 'Y2'}
    assert e.unsuitable_caregivers(None) == set()
    assert e.unsuitable_caregivers(float('nan')) == set()


@pytest.mark.skipif(not WORKBOOK.exists(), reason='缺機構範例 Excel')
def test_full_schedule_never_assigns_unsuitable_worker():
    sys.path.insert(0, str(ROOT / 'tools'))
    import benchmark as bm
    import sandbox_travel

    picked = {}

    def hook(cl, cfg):
        cl = cl.copy()
        owners = cl.dropna(subset=['歷史首選']) if '歷史首選' in cl else cl.iloc[0:0]
        rows = owners.head(3)
        cl['不適合居服員'] = None
        for idx, row in rows.iterrows():
            cl.at[idx, '不適合居服員'] = str(row['歷史首選']).strip()
            picked[str(row['案家ID'])] = str(row['歷史首選']).strip()
        return cl

    patches = sandbox_travel.patches()
    for p in patches:
        p.start()
    try:
        cfg, metrics, extra = bm.run(str(WORKBOOK), '2026-10-01', '2026-10-14', {}, client_hook=hook)
    finally:
        for p in patches:
            p.stop()
    assert picked, '範例 Excel 應有歷史首選可測'
    frames = extra['frames']
    done = frames['assigned'][['任務ID', '派單居服員']].astype(str).merge(
        frames['tasks'][['任務ID', '案家ID']].astype(str), on='任務ID')
    for client, worker in picked.items():
        rows = done.loc[done['案家ID'].astype(str) == client]
        assert not (rows['派單居服員'].astype(str) == worker).any(), f'{client} 仍派給不適合的 {worker}'
        assert cfg.client_primary_map.get(client) != worker
