"""命令列：重新挑選「歷史首選」，讓固定居服員整月排得進去的筆數最多，並寫回 Excel。

用法（在專案根目錄）：
    python optimize_primary.py 來源.xlsx 2026-10-01 2026-10-31
    python optimize_primary.py 來源.xlsx 2026-10-01 2026-10-31 --seconds 240 --keep 0.3 --margin 1.1 --extra 3

做法：先用與排班引擎同一套規則，在記憶體內模擬「某居服員固定接某幾個案家」整月能排進幾筆，
再用大鄰域搜尋在居服員間搬移／交換案家；--keep 越大越不願意動現有首選（延續性），
0 表示完全以成功率為準。--margin 把車程放大（預設 1.1），讓結果對塞車、路線差異有餘裕。
最後用真正的排班引擎跑一次驗證，並（--extra>1 時）產生同案備援寫入「歷史備援」。

輸出：<來源檔名>_首選已最佳化.xlsx（只改「個案資料」的歷史首選／歷史備援兩欄）、
<來源檔名>_首選異動明細.csv（逐案：原首選 → 新首選、整月排進筆數前後比較）。
交通來源沿用 CAREFLOW_TRAVEL_PROVIDER 與 .streamlit/secrets.toml 的設定；
大眾運輸居服員在模擬中以機車車程×2 粗估，最終仍由引擎驗證。
"""
import argparse
import os

import pandas as pd

from caregiver_engine import (PipelineConfig, calc_travel_minutes, load_agency_workbook,
                              merge_task_client_tables, prefetch_osrm_travel_times)
from monthly_scheduler import run_fixed_period_dispatch
from primary_optimizer import PrimaryModel, evaluate, lns_optimize, prune_changes
from schedule_assist import merged_backups
from source_fields import identifier
from source_workbook import update_source_workbook


def current_owners(clients, known):
    out = {}
    for _, row in clients.iterrows():
        owner = identifier(row.get('歷史首選居服員ID') or row.get('歷史首選'))
        if owner and str(row['案家ID']) in known:
            out[str(row['案家ID'])] = owner
    return out


def optimize_workbook(path, start, end, seconds=180.0, keep=0.3, margin=1.1, extra=3, log=print):
    """回傳 (xlsx bytes, 異動明細 DataFrame, 摘要 dict)。"""
    data = load_agency_workbook(path, start, end, availability_confirmed=True, biweekly_anchor_confirmed=True)
    tasks = merge_task_client_tables(data['Tasks'], data['Client_Profiles'])
    caregivers, clients = data['Caregiver_Profiles'], data['Client_Profiles']
    config = PipelineConfig()
    coords = list(zip(tasks['服務地點_緯度'].astype(float), tasks['服務地點_經度'].astype(float)))
    prefetch_osrm_travel_times(coords, coords, config.travel_min_per_km, transport_mode='機車')

    def travel(lat1, lon1, lat2, lon2, mode):
        minutes = calc_travel_minutes(lat1, lon1, lat2, lon2, config, transport_mode='機車')
        return minutes * (2.0 if mode != '機車' else 1.0)

    model = PrimaryModel(tasks, caregivers, config, travel, travel_margin=margin)
    current = current_owners(clients, model.client_visits)
    before, total, per_before = evaluate(model, current)
    log(f'目前首選（模擬）：整月可排進 {before}/{total}（{before / total:.1%}）')
    best, info = lns_optimize(model, current, owners=dict(current), seconds=seconds, keep_bonus=keep,
                              progress=lambda i, s: log(f'  搜尋 {i} 輪：{s}/{total}（{s / total:.1%}）') if i % 2000 == 0 else None)
    best = prune_changes(model, current, best)
    after, _, per_after = evaluate(model, best)
    log(f'重選後（模擬）：{after}/{total}（{after / total:.1%}）')
    owners = {c: w for c, w in best.items() if w}

    source = open(path, 'rb').read()
    clear = {c: '' for c in owners}
    stage = update_source_workbook(source, owners, backups=clear)
    backups_text = None
    result = None
    if extra > 1:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            tmp.write(stage)
        data2 = load_agency_workbook(tmp.name, start, end, availability_confirmed=True, biweekly_anchor_confirmed=True)
        os.unlink(tmp.name)
        cfg2 = PipelineConfig(enable_backup_caregiver=True, max_backup_caregivers=max(1, extra - 1))
        result = run_fixed_period_dispatch(data2['Tasks'], data2['Caregiver_Profiles'], data2['Client_Profiles'], cfg2)
        backups_text = merged_backups(result, data2['Client_Profiles'].assign(歷史備援=''),
                                      set(caregivers['居服員ID'].astype(str)))
    final = update_source_workbook(source, owners, backups=({**clear, **backups_text} if backups_text else clear))

    rows = []
    for c in sorted(set(current) | set(owners)):
        n = len(model.client_visits.get(c, ()))
        rows.append({'案家ID': c, '原首選': current.get(c, ''), '新首選': owners.get(c, ''),
                     '是否更動': '是' if current.get(c, '') != owners.get(c, '') else '',
                     '本期服務次數': n, '原首選可排進': per_before.get(c, 0), '新首選可排進': per_after.get(c, 0),
                     '備援': (backups_text or {}).get(c, '')})
    detail = pd.DataFrame(rows)
    summary = {'sim_before': before, 'sim_after': after, 'total': total,
               'changed': int((detail['是否更動'] == '是').sum()), 'clients': len(detail),
               'team_result': result}
    return final, detail, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('workbook'); ap.add_argument('start'); ap.add_argument('end')
    ap.add_argument('--seconds', type=float, default=180.0, help='搜尋秒數（越久越好，通常 120–300 就收斂）')
    ap.add_argument('--keep', type=float, default=0.3, help='保留現有首選的加分；0＝完全以成功率為準')
    ap.add_argument('--margin', type=float, default=1.1, help='車程放大倍數（餘裕）')
    ap.add_argument('--extra', type=int, default=3, help='同案最多幾位居服員（含主責）；1＝不產生備援')
    args = ap.parse_args()
    out, detail, summary = optimize_workbook(args.workbook, args.start, args.end, args.seconds, args.keep,
                                             args.margin, args.extra)
    base = os.path.splitext(args.workbook)[0]
    open(base + '_首選已最佳化.xlsx', 'wb').write(out)
    detail.to_csv(base + '_首選異動明細.csv', index=False, encoding='utf-8-sig')
    print(f"更動 {summary['changed']}/{summary['clients']} 個案家；已輸出 {base}_首選已最佳化.xlsx、{base}_首選異動明細.csv")


if __name__ == '__main__':
    main()
