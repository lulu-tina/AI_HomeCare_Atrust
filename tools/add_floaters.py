"""在活頁簿加入「機動居服員」（沒有被任何案家掛成固定人選／備援，所以不會改變自動排班結果；
只有居督在『整案處理』手動叫用時才會出現在候選裡）。
    python tools/add_floaters.py 你的檔.xlsx 輸出.xlsx [人數，預設 2]
M04 休週日、M05 休週六，機車、女性、無失智證照；住家座標放在服務密集區（可自行改）。
"""
import sys
import pandas as pd

FLOATERS = [('M04', '黃○芳', '週日', 24.9990, 121.5180), ('M05', '林○慧', '週六', 25.0010, 121.5230),
            ('M06', '張○玲', '週一', 25.0050, 121.5100)]


def build(src, out, n=2):
    sheets = pd.read_excel(src, sheet_name=None, dtype=object)
    cg = sheets['員工資料']
    ids = set(cg['編號'].astype(str))
    template = cg[cg['編號'].astype(str) == 'M01'].iloc[0].copy() if 'M01' in ids else cg[cg['性別'].astype(str) == '女'].iloc[0].copy()
    rows, names = [], []
    for cid, name, rest, lat, lon in FLOATERS[:n]:
        if cid in ids:
            continue
        r = template.copy()
        r['編號'] = cid; r['休息日'] = rest; r['交通工具'] = '機車'
        r['取得資格'] = '長照服務人員證明, 急救技能訓練CPR+AED, 身心障礙支持服務核心課程'
        r['經度 (WGS84_Lon)'] = lon; r['緯度 (WGS84_Lat)'] = lat
        for col in ('類別', '開始', '結束', '時間', '時數', '說明'):
            if col in r.index:
                r[col] = None
        rows.append(r)
        names.append({'編號': cid, '姓名': name})
    if rows:
        sheets['員工資料'] = pd.concat([cg, pd.DataFrame(rows)], ignore_index=True)
        nm = sheets['居服員姓名對照']
        sheets['居服員姓名對照'] = pd.concat([nm, pd.DataFrame([{nm.columns[0]: x['編號'], nm.columns[1]: x['姓名']} for x in names])], ignore_index=True)
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return out


if __name__ == '__main__':
    print(build(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2))
