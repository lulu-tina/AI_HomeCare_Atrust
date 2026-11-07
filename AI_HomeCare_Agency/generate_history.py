"""命令列：替沒有「歷史首選」的新案產生固定人選與備援，寫回 Excel。

用法（在專案根目錄）：
    python generate_history.py 00_DB/居服機構資料_三工作表.xlsx 2026-10-01 2026-10-31
    python generate_history.py 來源.xlsx 2026-10-01 2026-10-31 --extra 3 --flex 30 --apply-shifts

輸出：<來源檔名>_首選已補.xlsx（只改「個案資料」的歷史首選／歷史備援兩欄；
加 --apply-shifts 時另改「週服務計畫」中整列一致的時段）、新案首選摘要.csv。
交通來源沿用 CAREFLOW_TRAVEL_PROVIDER 與 .streamlit/secrets.toml 的設定。
"""
import argparse
import os

from caregiver_engine import PipelineConfig, load_agency_workbook
from monthly_scheduler import run_fixed_period_dispatch
from schedule_assist import build_history_workbook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('workbook'); ap.add_argument('start'); ap.add_argument('end')
    ap.add_argument('--extra', type=int, default=3, help='同案最多幾位居服員（含主責）；1＝不用備援')
    ap.add_argument('--flex', type=float, default=0.0, help='時段彈性（分鐘，前後各）；0＝不挪動')
    ap.add_argument('--apply-shifts', action='store_true', help='把整列一致的時段調整也寫回週服務計畫')
    args = ap.parse_args()
    data = load_agency_workbook(args.workbook, args.start, args.end,
                                availability_confirmed=True, biweekly_anchor_confirmed=True)
    config = PipelineConfig(enable_backup_caregiver=args.extra > 1,
                            max_backup_caregivers=max(1, args.extra - 1), time_flex_minutes=args.flex)
    result = run_fixed_period_dispatch(data['Tasks'], data['Caregiver_Profiles'], data['Client_Profiles'], config)
    source = open(args.workbook, 'rb').read()
    out, summary, skipped = build_history_workbook(source, result, data['Client_Profiles'], data['Caregiver_Profiles'],
                                                   include_backups=args.extra > 1, apply_shifts=args.apply_shifts,
                                                   tasks=result['tasks_adjusted'])
    base = os.path.splitext(args.workbook)[0]
    open(base + '_首選已補.xlsx', 'wb').write(out)
    summary.to_csv(base + '_新案首選摘要.csv', index=False, encoding='utf-8-sig')
    n = len(data['Tasks'])
    print(f"派單 {len(result['df_result'])}/{n}（{len(result['df_result'])/n:.1%}）；新案首選 {len(summary)} 案；"
          f"備援案家 {len(result.get('client_backup_map') or {})}；時段調整 {result['dispatch_stats'].get('time_shifted_services', 0)} 次")
    if not skipped.empty:
        print('以下時段調整未寫回（需居督確認）：'); print(skipped.to_string(index=False))
    print('已輸出：', base + '_首選已補.xlsx')


if __name__ == '__main__':
    main()
