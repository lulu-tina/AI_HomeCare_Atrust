"""Build a demo workbook from your Excel: adds a small "機動居服員" pool so temporary changes have people to move to.
    python tools/make_demo_workbook.py src.xlsx out.xlsx [--exclude CASE1,CASE2]
- 3 機動居服員 (M01–M03): female, motorcycle, no 失智症 training (so dementia cases still need regular staff).
- Each non-dementia client that is currently fully served gets two of them as 歷史備援 (fixed-team backups), because the app only lets
  fixed-team members cover a client's visits. Clients in --exclude (e.g. the pending ones you want to demo) are left alone.
Nothing else in the workbook is changed.
"""
import sys
import pandas as pd

POOL = [('M01', '吳○玲', '週日', 24.9895, 121.5100), ('M02', '鄭○君', '週六', 25.0030, 121.4950), ('M03', '陳○婷', '週三', 24.9800, 121.5050)]


SYNTHETIC = [
    # id, 姓名, 歷史首選（沒有失智證照 → 自動排班無法派）, 服務星期, 說明
    ('DEMO0001', '李○英', 'H5156', '二四', '失智個案、首選居服員沒有失智證照 → 自動排不進；居督改選一位有證照者即可（一人可解）'),
    ('DEMO0002', '許○梅', 'M03', '一二三四五六日', '失智個案、每週七天都要服務，首選沒有證照 → 沒有任何一人能全包（每人都有休息日），要選兩位（多人可解）'),
]


def add_synthetic_clients(sheets):
    """Two made-up clients (clearly named DEMO…) whose weekly plan cannot be covered by a single caregiver."""
    cl, wk, names = sheets['個案資料'], sheets['週服務計畫'], sheets['案家姓名對照']
    base = cl[cl['內部案號'].astype(str) == '115010007'].iloc[0].copy()
    wbase = wk[wk['案號'].astype(str) == '115010007'].iloc[0].copy()
    new_cl, new_wk, new_names = [], [], []
    for cid, name, first, days, _note in SYNTHETIC:
        r = base.copy()
        r['內部案號'] = cid; r['性別'] = '女'; r['年齡'] = 78; r['核定等級'] = 5; r['失能程度'] = '中'; r['是否失智'] = '是'
        r['歷史首選'] = first; r['歷史備援'] = None
        new_cl.append(r)
        new_names.append({names.columns[0]: cid, names.columns[1]: name})
        for d in days:
            w = wbase.copy()
            w['案號'] = cid; w['星期'] = '星期' + d; w['開始時間'] = '09:00:00'; w['結束時間'] = '10:25:00'
            w['服務時數'] = 1.4167; w['服務分鐘'] = 85; w['服務項目'] = 'BA16-1(1) BA20(1) BA07(1)'; w['頻率'] = '每週'
            w['生效日'] = '2026-04-24'; w['到期日'] = None
            w['經度 (WGS84_Lon)'] = r['經度 (WGS84_Lon)']; w['緯度 (WGS84_Lat)'] = r['緯度 (WGS84_Lat)']
            new_wk.append(w)
    sheets['個案資料'] = pd.concat([cl, pd.DataFrame(new_cl)], ignore_index=True)
    sheets['週服務計畫'] = pd.concat([wk, pd.DataFrame(new_wk)], ignore_index=True)
    sheets['案家姓名對照'] = pd.concat([names, pd.DataFrame(new_names)], ignore_index=True)


def build(src, out, exclude=(), synthetic=False):
    sheets = pd.read_excel(src, sheet_name=None, dtype=object)
    cg = sheets['員工資料']
    template = cg[cg['性別'].astype(str) == '女'].iloc[0].copy()
    rows = []
    for cid, name, rest, lat, lon in POOL:
        r = template.copy()
        r['編號'] = cid; r['休息日'] = rest; r['交通工具'] = '機車'
        r['取得資格'] = '長照服務人員證明, 急救技能訓練CPR+AED, 身心障礙支持服務核心課程'
        r['經度 (WGS84_Lon)'] = lon; r['緯度 (WGS84_Lat)'] = lat
        for col in ('類別', '開始', '結束', '時間', '時數', '說明'):
            if col in r.index:
                r[col] = None
        rows.append(r)
    sheets['員工資料'] = pd.concat([cg, pd.DataFrame(rows)], ignore_index=True)
    names = sheets['居服員姓名對照']
    sheets['居服員姓名對照'] = pd.concat([names, pd.DataFrame([{names.columns[0]: c, names.columns[1]: n} for c, n, *_ in POOL])], ignore_index=True)
    cl = sheets['個案資料']
    if '歷史備援' not in cl:
        cl['歷史備援'] = None
    cl['歷史備援'] = cl['歷史備援'].astype(object)
    k = 0
    for idx, row in cl.iterrows():
        if str(row['內部案號']) in set(exclude) or str(row.get('是否失智')) == '是':
            continue
        pair = [POOL[k % 3][0], POOL[(k + 1) % 3][0]]
        existing = [x for x in str(row['歷史備援']).replace('、', ',').split(',') if x and x != 'nan']
        cl.at[idx, '歷史備援'] = '、'.join(existing + [p for p in pair if p not in existing])
        k += 1
    sheets['個案資料'] = cl
    if synthetic:
        add_synthetic_clients(sheets)
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return out


if __name__ == '__main__':
    src, out = sys.argv[1], sys.argv[2]
    exclude = []
    if '--exclude' in sys.argv:
        exclude = sys.argv[sys.argv.index('--exclude') + 1].split(',')
    print(build(src, out, exclude, synthetic='--synthetic' in sys.argv))
