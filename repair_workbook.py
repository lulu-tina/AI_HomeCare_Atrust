"""Excel 最小改動修復：用「真的跑一次派單」的結果，找出少數幾個改動就能大幅提高派單成功率的地方。

做法（由小到大，每一步都用實際派單驗證，沒有變好就不保留）：
  A. 居服員休息日改到事少的星期（員工資料「休息日」，每人仍只休一天）。
  B. 單次連續服務超過可連續工作上限（預設 240 分）的案，從中間拆成兩段、中間留 30 分鐘休息，服務分鐘不變。
  C. 案家服務時間小幅前後挪動（5、10、15…最多 60 分鐘，每一輪對所有仍失敗的週期列一起試）。
  D. 仍排不進去的週期列，才改星期（最後手段，需與案家確認）。
改動以「週服務計畫的一列＝整個月、整年同一個週期」為單位，不是逐日、逐筆修正。
缺必要證照／特殊訓練（例如失智症照顧專長）的服務一律不動，保留給人工重新派單。

用法（在專案資料夾）：
    python repair_workbook.py 居服機構資料.xlsx --start 2026-10-01 --end 2026-10-31
會輸出 <原檔名>_修復後.xlsx 與 <原檔名>_異動明細.csv。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import argparse
import os
import re
import tempfile
import time

import pandas as pd

from workbook_edit import Workbook

WEEKDAY_SHEET = {'一': '星期一', '二': '星期二', '三': '星期三', '四': '星期四', '五': '星期五', '六': '星期六', '日': '星期日'}
SHEET_EMP, SHEET_WEEK = '員工資料', '週服務計畫'
WINDOW_START, WINDOW_END = 8 * 60, 21 * 60
SHIFT_LADDER = [5, -5, 10, -10, 15, -15, 20, -20, 30, -30, 40, -40, 45, -45, 60, -60]
CERT_CATEGORY = '必要證照'
BREAK_MINUTES = 30


# ---------------------------------------------------------------- 基礎
def to_minutes(text):
    raw = str(text).strip()
    if ':' not in raw:  # Excel 時間序列值（例如 0.486 = 11:40）
        return int(round(float(raw) * 1440))
    parts = raw.split(':')
    return int(parts[0]) * 60 + int(parts[1])


def to_clock(minutes):
    minutes = int(round(minutes))
    return '%02d:%02d:00' % (minutes // 60, minutes % 60)


def make_config(institution_key='中化'):
    import caregiver_engine as engine
    return engine.PipelineConfig(enable_backup_caregiver=True, max_backup_caregivers=2,
                                 time_flex_minutes=0.0, institution_key=institution_key)


def dispatch_workbook(xlsx_bytes, start, end, institution_key='中化'):
    """與主程式相同的派單流程（固定首選＋備援）。回傳 dict：tasks, assigned(set), diag, primary_map。"""
    import caregiver_engine as engine
    from dispatch_workflow import WorkflowStore, attach_blocks
    from monthly_scheduler import run_fixed_period_dispatch
    from source_fields import source_primary_map
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'w.xlsx')
        with open(path, 'wb') as handle:
            handle.write(xlsx_bytes)
        data = engine.load_agency_workbook(path, start, end, availability_confirmed=True, biweekly_anchor_confirmed=True)
        caregivers, clients = data['Caregiver_Profiles'], data['Client_Profiles']
        store = WorkflowStore(institution_key, os.path.join(folder, 'w.sqlite3'))
        config = make_config(institution_key)
        config.client_primary_map = source_primary_map(clients, set(caregivers['居服員ID'].astype(str)), store.primary_map())
        df_cg = attach_blocks(caregivers, store)
        config.primary_profiles = {str(r['居服員ID']): r.to_dict() for _, r in df_cg.iterrows()}
        result = run_fixed_period_dispatch(data['Tasks'], df_cg, clients, config, primary_map=config.client_primary_map)
    tasks = result['tasks_adjusted']
    assigned = set(result['df_result']['任務ID'].astype(str))
    return {'tasks': tasks, 'assigned': assigned, 'diag': result['diagnostics'], 'primary_map': result['client_primary_map'],
            'total': int(tasks['任務ID'].nunique()), 'ok': len(assigned)}


class State:
    """所有改動都記成 ops，從原檔重新套用，所以可以任意還原、重播。"""

    def __init__(self, source_bytes):
        self.source = source_bytes
        self.ops = []

    def build(self, extra=()):
        book = Workbook(self.source)
        appended = []
        for op in list(self.ops) + list(extra):
            if op[0] == 'set':
                _, sheet, row, header, value = op
                book.set_cell(sheet, row, header, value)
            elif op[0] == 'append':
                _, sheet, values, copy_from = op
                appended.append(book.append_row(sheet, values, copy_from))
        return book.to_bytes(), appended

    def row_values(self, extra=()):
        """{週服務計畫列號: {欄位: 值}}（目前狀態）。"""
        data, _ = self.build(extra)
        book = Workbook(data)
        headers = book.headers(SHEET_WEEK)
        table = book.table(SHEET_WEEK)
        return {n: {h: values.get(h, '') for h in headers} for n, values in table.items()}, data


def score(info):
    """非缺證照的未派筆數（越小越好）。"""
    return len(info['tasks_unassigned_actionable'])


def analyze(res):
    tasks, diag = res['tasks'], res['diag']
    cert_tasks = set(diag.loc[diag['問題分類'] == CERT_CATEGORY, '任務ID'].astype(str)) if len(diag) else set()
    unassigned = tasks[~tasks['任務ID'].astype(str).isin(res['assigned'])]
    actionable = unassigned[~unassigned['任務ID'].astype(str).isin(cert_tasks)]
    res = dict(res)
    res['tasks_unassigned_actionable'] = set(actionable['任務ID'].astype(str))
    res['cert_tasks'] = cert_tasks
    res['rows_failed'] = {int(r) for r in actionable['來源列']}
    res['rows_cert'] = {int(r) for r in tasks.loc[tasks['任務ID'].astype(str).isin(cert_tasks), '來源列']}
    return res


# ---------------------------------------------------------------- 修復器
class Repairer:
    def __init__(self, source_bytes, start, end, log=print, target_actionable=0, initial_ops=()):
        self.start, self.end, self.log = start, end, log
        self.state = State(source_bytes)
        self.state.ops.extend(initial_ops)
        self.changes = []
        self.target = target_actionable
        self.evals = 0

    def evaluate(self, extra=()):
        data, _ = self.state.build(extra)
        self.evals += 1
        return analyze(dispatch_workbook(data, self.start, self.end))

    def say(self, text):
        self.log(text)

    # ---- A. 休息日
    def phase_rest_days(self, cur):
        owners = {}
        for text in cur['diag'].loc[cur['diag']['問題分類'] == '可服務星期', '剔除原因'].astype(str):
            m = re.search(r'固定人選 (\S+?)：當日不排班', text)
            if m:
                owners[m.group(1)] = owners.get(m.group(1), 0) + 1
        if not owners:
            return cur
        book = Workbook(self.state.build()[0])
        emp_table = book.table(SHEET_EMP)
        emp_rows = {values.get('編號', '').strip(): n for n, values in emp_table.items()}
        tasks = cur['tasks']
        pmap = cur['primary_map']
        for cg, count in sorted(owners.items(), key=lambda kv: -kv[1]):
            row = emp_rows.get(cg)
            if row is None:
                continue
            old = Workbook(self.state.build()[0]).table(SHEET_EMP)[row].get('休息日', '').strip()
            if not re.fullmatch(r'週[一二三四五六日]', old):
                continue
            load = {}
            mine = tasks[tasks['案家ID'].astype(str).map(lambda c: str(pmap.get(c, '')) == cg)]
            for day in '一二三四五六日':
                load[day] = int((mine['星期'] == WEEKDAY_SHEET[day]).sum())
            options = sorted((d for d in '一二三四五六日' if '週' + d != old), key=lambda d: load[d])[:3]
            best = (score(cur), None)
            for day in options:
                trial = self.evaluate([('set', SHEET_EMP, row, '休息日', '週' + day)])
                if score(trial) < best[0]:
                    best = (score(trial), day, trial)
            if best[1]:
                self.state.ops.append(('set', SHEET_EMP, row, '休息日', '週' + best[1]))
                self.changes.append({'階段': 'A 居服員休息日', '工作表': SHEET_EMP, '列': row, '對象': cg, '欄位': '休息日',
                                     '原值': old, '新值': '週' + best[1],
                                     '說明': f'原休息日有 {owners[cg]} 筆固定服務排不進去；改後該星期無衝突，每人仍只休一天',
                                     '改善筆數': score(cur) - best[0], '需督導確認': '是（居服員排休）'})
                self.say(f'  休息日 {cg}: {old} → 週{best[1]}  未派 {score(cur)} → {best[0]}')
                cur = best[2]
        return cur

    # ---- B. 拆分過長連續服務
    def phase_split(self, cur):
        rows, _ = self.state.row_values()
        failing_rest = {}
        for _, d in cur['diag'].iterrows():
            if d['問題分類'] == '休息不足':
                failing_rest[int(d['來源列'])] = str(d['剔除原因'])
        splits = []
        for row in sorted(failing_rest):
            info = rows.get(row)
            if not info or row in cur['rows_cert']:
                continue
            s, e = to_minutes(info['開始時間']), to_minutes(info['結束時間'])
            duration = e - s
            if duration < 150:
                continue
            first = int(round(duration / 2 / 5) * 5)
            second = duration - first
            new_end = e + BREAK_MINUTES
            shift_back = 0
            if new_end > WINDOW_END:
                shift_back = new_end - WINDOW_END
            splits.append((row, info, s - shift_back, first, second))
        if not splits:
            return cur
        groups = []  # 每個拆分 = 這一列的 4 個 set ＋ 1 個 append
        for row, info, s, first, second in splits:
            ops = [('set', SHEET_WEEK, row, '開始時間', to_clock(s)),
                   ('set', SHEET_WEEK, row, '結束時間', to_clock(s + first)),
                   ('set', SHEET_WEEK, row, '服務時數', first / 60.0),
                   ('set', SHEET_WEEK, row, '服務分鐘', float(first))]
            copy = {h: v for h, v in info.items() if v not in ('', None) and h not in ('服務時數', '服務分鐘')}
            copy['開始時間'] = to_clock(s + first + BREAK_MINUTES)
            copy['結束時間'] = to_clock(s + first + BREAK_MINUTES + second)
            copy['服務時數'] = second / 60.0
            copy['服務分鐘'] = float(second)
            ops.append(('append', SHEET_WEEK, copy, row))
            groups.append((row, info, s, first, second, ops))
        # 一列一列試：整批一起拆會互相搶班，單列驗證有改善才保留
        accepted = 0
        for group in groups:
            row, info, s, first, second, ops = group
            trial = self.evaluate(ops)
            _, new_rows = self.state.build(ops)
            if score(trial) < score(cur) and row not in trial['rows_failed'] and new_rows[0] not in trial['rows_failed']:
                self.state.ops.extend(ops)
                accepted += 1
                self.changes.append({'階段': 'B 拆分過長連續服務', '工作表': SHEET_WEEK, '列': row, '對象': info['案號'], '欄位': '開始時間/結束時間',
                                     '原值': f"{info['星期']} {info['開始時間'][:5]}～{info['結束時間'][:5]}（{to_minutes(info['結束時間']) - to_minutes(info['開始時間'])} 分）",
                                     '新值': f"{to_clock(s)[:5]}～{to_clock(s + first)[:5]} 與 {to_clock(s + first + BREAK_MINUTES)[:5]}～{to_clock(s + first + BREAK_MINUTES + second)[:5]}（共 {first + second} 分不變，中間休息 {BREAK_MINUTES} 分）",
                                     '說明': '單次連續工作超過上限，任何人都無法一次接下；拆成兩段後可由同人或不同人承接，新增一列週期',
                                     '改善筆數': score(cur) - score(trial), '需督導確認': '是（需與案家確認分兩段）'})
                self.say(f'  拆分列 {row}: 未派 {score(cur)} → {score(trial)}')
                cur = trial
        if not accepted:
            self.say('  拆分沒有任何一列有淨改善，略過')
        return cur

    # ---- C/D. 時間／星期梯度
    def _ladder(self, cur, label, make_ops, steps, stage, explain, confirm):
        done = {}
        for step in steps:
            todo = sorted(cur['rows_failed'] - cur['rows_cert'])
            if not todo or score(cur) <= self.target:
                break
            rows, _ = self.state.row_values()
            extra = []
            for row in todo:
                if row in done or row not in rows:
                    continue
                extra += make_ops(row, rows[row], step) or []
            if not extra:
                continue
            trial = self.evaluate(extra)
            fixed = [r for r in todo if r not in trial['rows_failed']]
            if not fixed:
                continue
            keep = [op for op in extra if op[2] in fixed]
            verify = self.evaluate(keep)
            if score(verify) < score(cur):
                self.state.ops.extend(keep)
                for row in fixed:
                    done[row] = step
                    info = rows[row]
                    new = {op[3]: op[4] for op in keep if op[2] == row}
                    self.changes.append({'階段': stage, '工作表': SHEET_WEEK, '列': row, '對象': info['案號'], '欄位': '/'.join(new),
                                         '原值': ' '.join(f'{k}:{info[k]}' for k in new), '新值': ' '.join(f'{k}:{v}' for k, v in new.items()),
                                         '說明': explain(step), '改善筆數': '', '需督導確認': confirm})
                self.say(f'  {label} {step}: 修好 {len(fixed)} 列  未派 {score(cur)} → {score(verify)}')
                cur = verify
        return cur

    def phase_shift(self, cur):
        def ops(row, info, step):
            s, e = to_minutes(info['開始時間']), to_minutes(info['結束時間'])
            if s + step < WINDOW_START or e + step > WINDOW_END:
                return None
            return [('set', SHEET_WEEK, row, '開始時間', to_clock(s + step)), ('set', SHEET_WEEK, row, '結束時間', to_clock(e + step))]
        return self._ladder(cur, '時間挪動', ops, SHIFT_LADDER, 'C 案家服務時間微調',
                            lambda st: f'整列週期起訖時間同步{"後移" if st > 0 else "前移"} {abs(st)} 分鐘，服務分鐘不變', '是（需與案家確認）')

    def phase_weekday(self, cur):
        def ops(row, info, step):
            if WEEKDAY_SHEET[step] == info['星期']:
                return None
            return [('set', SHEET_WEEK, row, '星期', WEEKDAY_SHEET[step])]
        return self._ladder(cur, '改星期', ops, list('一二三四五日六'), 'D 案家服務星期調整',
                            lambda st: f'改到星期{st}（最後手段）', '是（需與案家確認）')

    def run(self):
        t0 = time.time()
        cur = self.evaluate()
        base = (cur['total'], cur['ok'], score(cur), len(cur['cert_tasks']))
        self.say(f'原始：{cur["ok"]}/{cur["total"]} = {cur["ok"] / cur["total"] * 100:.1f}%　可處理未派 {score(cur)}（缺證照保留 {len(cur["cert_tasks"])}）')
        for name, phase in (('A 休息日', self.phase_rest_days), ('B 拆分', self.phase_split),
                            ('C 時間微調', self.phase_shift), ('D 改星期', self.phase_weekday),
                            ('C 時間微調（第二輪）', self.phase_shift)):
            if score(cur) <= self.target:
                break
            self.say(f'階段 {name}')
            cur = phase(cur)
        self.say(f'完成：{cur["ok"]}/{cur["total"]} = {cur["ok"] / cur["total"] * 100:.1f}%　可處理未派 {score(cur)}　{self.evals} 次派單驗證 {time.time() - t0:.0f}s')
        return cur, base

    def output(self):
        return self.state.build()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('xlsx')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--out-dir', default=None)
    parser.add_argument('--target', type=int, default=0, help='可處理未派筆數降到幾筆以下就停（預設 0＝盡量全修）')
    args = parser.parse_args()
    with open(args.xlsx, 'rb') as handle:
        source = handle.read()
    repairer = Repairer(source, args.start, args.end, target_actionable=args.target)
    final, base = repairer.run()
    folder = args.out_dir or os.path.dirname(os.path.abspath(args.xlsx))
    stem = os.path.splitext(os.path.basename(args.xlsx))[0]
    out_xlsx = os.path.join(folder, stem + '_修復後.xlsx')
    out_csv = os.path.join(folder, stem + '_異動明細.csv')
    with open(out_xlsx, 'wb') as handle:
        handle.write(repairer.output())
    pd.DataFrame(repairer.changes).to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(out_xlsx)
    print(out_csv)


if __name__ == '__main__':
    main()
