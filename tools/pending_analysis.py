"""Classify every unassigned client: can a supervisor fix it by choosing one person, a team, or not at all?
    python tools/pending_analysis.py [excel.xlsx]            (prints a table; uses the same engine calls as the app)
Network travel lookups are replaced by a constant when SANDBOX=1.
"""
import os, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
import pandas as pd
from dataclasses import replace
import caregiver_engine as e
from monthly_scheduler import run_fixed_period_dispatch
from source_fields import source_primary_map, normalize_clients
from agency_rules import payroll_tables
from calendar_view import apply_overrides_to_result
from pending_clients import pending_client_summary, client_patterns, preview_client_assignment, plan_team
from eligibility import screen_caregivers, client_profile, simulate_worker


def prepare(excel, start='2026-10-01', end='2026-10-31'):
    x = e.load_agency_workbook(excel, start, end, availability_confirmed=True, biweekly_anchor_confirmed=True)
    cg, cl, tasks = x['Caregiver_Profiles'], normalize_clients(x['Client_Profiles'].copy(), set(x['Caregiver_Profiles']['居服員ID'].astype(str))), x['Tasks']
    cfg = e.PipelineConfig(enable_backup_caregiver=True, max_backup_caregivers=2)
    pm = source_primary_map(cl, set(cg['居服員ID'].astype(str)), {})
    cfg.client_primary_map = dict(pm)
    r = run_fixed_period_dispatch(tasks, cg, cl, cfg, primary_map=pm)
    cfg.client_primary_map = dict(r['client_primary_map']); cfg.client_backup_map = dict(r.get('client_backup_map') or {})
    cfg.primary_profiles = {str(row['居服員ID']): row.to_dict() for _, row in cg.iterrows()}
    assigned = apply_overrides_to_result(r['df_result'], {})
    merged = e.merge_task_client_tables(tasks, cl)
    _, _, _, ledger = payroll_tables(merged, assigned, cg, r['route_result'], cfg)
    cfg.work_day_minutes = dict(ledger)
    return dict(x=x, cg=cg, cl=cl, tasks=tasks, cfg=cfg, assigned=assigned, merged=merged, result=r)


def analyze(ctx, verbose=True):
    cfg, cg, cl, tasks, assigned, merged = (ctx[k] for k in ('cfg', 'cg', 'cl', 'tasks', 'assigned', 'merged'))
    pend = pending_client_summary(tasks, assigned, ctx['result'].get('matching_diagnostics'), cfg.client_primary_map)
    rows = []
    for _, p in pend.iterrows():
        case = str(p['案家ID'])
        ct = merged.loc[merged['案家ID'].astype(str).eq(case)]
        owner = str(cfg.client_primary_map.get(case) or '')
        screen, _ = screen_caregivers(ct, cg, cfg, owner, tuple(cfg.client_backup_map.get(case) or ()), assigned, merged)
        eligible = [str(r['居服員ID']) for _, r in screen.iterrows() if r['_ok'] > 0]
        pats = client_patterns(tasks, case)
        solo, accepted = {}, {}
        for w in eligible:
            pv = preview_client_assignment(tasks, assigned, cg, cl, cfg, case, w, pats)
            solo[w] = len(pv['accepted']); accepted[w] = set(pv['accepted']['任務ID'])
        total = len(ct)
        best = max(solo.items(), key=lambda kv: kv[1], default=('', 0))
        verdict, detail = '', ''
        if best[1] == total:
            verdict, detail = '一人可解', f'{best[0]} 可接全部 {total} 次'
        else:
            plan = plan_team(tasks, assigned, cg, cl, cfg, case, pats, accepted, preferred=best[0])
            n = plan['accepted']
            members = sorted(set(plan['pattern_workers'].values()) | set(plan['task_workers'].values()))
            if n == total:
                verdict, detail = '多人可解', f"{'、'.join(members)}（主責 {plan['main']}）共接 {n}/{total} 次；個別補人 {len(plan['task_workers'])} 次"
            else:
                verdict, detail = ('部分可解' if n else '無解'), f"組合最佳 {n}/{total} 次（{'、'.join(members) or best[0]}）；單人最多 {best[0]} {best[1]}/{total}"
        rows.append({'案家': case, '未派次數': int(p['未派服務數']), '總次數': total, '合格人數': len(eligible), '結論': verdict, '說明': detail})
        if verbose:
            print(rows[-1], flush=True)
    return pd.DataFrame(rows)


if __name__ == '__main__':
    from unittest.mock import patch
    excel = sys.argv[1] if len(sys.argv) > 1 else '/mnt/user-data/outputs/careflow_v13/居服機構資料_三工作表_v2_修復後.xlsx'
    import sandbox_travel
    ps = sandbox_travel.patches() if os.getenv('SANDBOX', '1') == '1' else []
    for p in ps: p.start()
    ctx = prepare(excel)
    print('assigned', len(ctx['assigned']), 'of', len(ctx['tasks']))
    print(analyze(ctx).to_string())
