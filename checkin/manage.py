"""打卡網頁的督導工具（在主機上執行）。

    python -m checkin.manage set-pin E001 123456      # 設定或重設一位的 PIN（也會解鎖）
    python -m checkin.manage new-pins                 # 已發布班表中、還沒有 PIN 的人，各產生一組隨機 PIN
    python -m checkin.manage new-pins E001 E002 --reset   # 指定人員，已有 PIN 也重發
    python -m checkin.manage disable E005             # 離職／停用
    python -m checkin.manage enable E005
    python -m checkin.manage list                     # 帳號狀態與已發布的班表範圍
    python -m checkin.manage export 2026-11-01 2026-11-30   # 匯出打卡紀錄 CSV（可上傳到 04 執行對照）

new-pins 會把 PIN 存成一個 CSV（只此一次看得到明碼），發給居服員後請刪除該檔。
"""
from __future__ import annotations

import argparse
import csv
import secrets
import sys
from datetime import date

from checkin import store


def _new_pin():
    return f'{secrets.randbelow(1_000_000):06d}'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m checkin.manage', description='CareFlow 打卡網頁督導工具')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('set-pin'); p.add_argument('caregiver'); p.add_argument('pin')
    p = sub.add_parser('new-pins'); p.add_argument('caregivers', nargs='*'); p.add_argument('--reset', action='store_true')
    p = sub.add_parser('disable'); p.add_argument('caregiver')
    p = sub.add_parser('enable'); p.add_argument('caregiver')
    sub.add_parser('list')
    p = sub.add_parser('export'); p.add_argument('start'); p.add_argument('end'); p.add_argument('--out')
    args = parser.parse_args(argv)

    if args.cmd == 'set-pin':
        store.set_pin(args.caregiver.upper(), args.pin)
        print(f'{args.caregiver.upper()} 的 PIN 已設定。')
    elif args.cmd == 'new-pins':
        pins = store.generate_pins([c.upper() for c in args.caregivers] or None, reset=args.reset)
        if not pins:
            print('沒有需要產生 PIN 的人（已發布班表的人都有 PIN；要重發請加 --reset 並指定編號）。')
            return 0
        targets = [cg for cg, _ in pins]
        out = store.db_path().parent / f'checkin_pins_{date.today():%Y%m%d}_{secrets.token_hex(3)}.csv'
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['員工編號', 'PIN'])
            w.writerows(pins)
        print(f'已產生 {len(targets)} 組 PIN：{out}')
        print('發給居服員後請刪除這個檔案。')
    elif args.cmd in ('disable', 'enable'):
        n = store.set_active(args.caregiver.upper(), args.cmd == 'enable')
        print('完成。' if n else '找不到這個帳號。')
    elif args.cmd == 'list':
        first, last, count, at = store.published_range()
        print(f'已發布班表：{first or "—"} ～ {last or "—"}，共 {count} 筆（最後發布 {at or "—"}）')
        for a in store.accounts():
            state = '停用' if not a['active'] else ('鎖定中' if a['locked_until'] else '正常')
            print(f"  {a['caregiver_id']}\t{state}")
    elif args.cmd == 'export':
        frame = store.punch_frame(date.fromisoformat(args.start), date.fromisoformat(args.end))
        out = args.out or f'打卡紀錄_{args.start}_{args.end}.csv'
        frame.to_csv(out, index=False, encoding='utf-8-sig')
        print(f'匯出 {len(frame)} 筆：{out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
