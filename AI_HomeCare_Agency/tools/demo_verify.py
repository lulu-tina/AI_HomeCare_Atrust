"""Verify the four demo scenarios on a workbook with the same engine calls the app makes.
    python tools/demo_verify.py demo.xlsx          (SANDBOX=0 to use your real OSRM/Google times)
Prints PASS/FAIL per scenario with the candidates the screen should list.
"""
import os, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
import pandas as pd
import caregiver_engine as e
import pending_analysis as pa
import demo_finder as df
import sandbox_travel

D = pd.Timestamp
SCEN = dict(
    new1=dict(day=D(2026, 10, 6), start='15:00', end='18:00'),
    new2=dict(sat=D(2026, 10, 10), s1='14:00', s3='09:00'),
    leave=dict(worker='C9761', day='2026-10-08'),
    reschedule=dict(task='W0026_20261005', start='12:30', end='13:30'),
    delay=dict(worker='03816', day='2026-10-02', delayed='W0348_20261002', minutes=30, affected='W0361_20261002'),
)


def texts():
    s = SCEN
    t1 = df.EX1.format(slots=df.slot_text(s['new1']['day'], s['new1']['start'], s['new1']['end'], 'BA14+BA13 聯合醫院昆明院區'))
    sat = s['new2']['sat']
    t2 = df.EX2.format(slots='\n'.join([df.slot_text(sat, s['new2']['s1'], df._plus(s['new2']['s1'], 120), 'GA09×1'),
                                         df.slot_text(sat + pd.Timedelta(days=1), s['new2']['s1'], df._plus(s['new2']['s1'], 120), 'GA09×1'),
                                         df.slot_text(sat + pd.Timedelta(days=2), s['new2']['s3'], df._plus(s['new2']['s3'], 120), 'GA09×1')]))
    return t1, t2


def main(excel):
    ctx = pa.prepare(excel)
    cfg, cg, merged, assigned = ctx['cfg'], ctx['cg'], ctx['merged'], ctx['assigned']
    results = {}
    t1, t2 = texts()
    pathlib.Path('demo_ex1.txt').write_text(t1, encoding='utf-8'); pathlib.Path('demo_ex2.txt').write_text(t2, encoding='utf-8')
    transit = {str(r['居服員ID']) for _, r in cg.iterrows() if str(r.get('常用交通工具')) == '大眾運輸'}
    for name, text in (('新增例1', t1), ('新增例2', t2)):
        found, plan = df.search_message(ctx, text, 2026)
        if found is None:
            results[name] = (False, f'解析失敗：{plan}'); continue
        per = [[i for i in df.ok_ids(f['frame'])] for f in found]
        ok = all(per)
        single = set.intersection(*[set(p) for p in per]) if per else set()
        results[name] = (ok, f'各時段可直接插入人選：{per}；可一人包全部：{sorted(single)}')
    # leave
    s = SCEN['leave']
    tm = merged.drop_duplicates('任務ID').set_index('任務ID')
    a = assigned.copy(); a['日期'] = a['任務ID'].map(tm['日期'])
    f = a[(a['派單居服員'].astype(str) == s['worker']) & (pd.to_datetime(a['日期']).dt.strftime('%Y-%m-%d') == s['day'])]
    from caregiver_engine import rank_candidates_by_availability
    eff = assigned[~assigned['任務ID'].isin(f['任務ID'])].copy()
    base = [i for i in cg['居服員ID'].astype(str) if i != s['worker'] and i not in transit]
    subs, ok = [], True
    for tid in f['任務ID']:
        ranked = rank_candidates_by_availability(tid, base, merged, eff, cg, cfg, date_column='日期', df_cl=ctx['cl'])
        av = [r['cg_id'] for r in ranked if r.get('available')]
        subs.append((tid, av[:3])); ok &= bool(av)
        if av:
            eff = pd.concat([eff, pd.DataFrame([{'任務ID': tid, '派單居服員': av[0]}])], ignore_index=True)
    results['請假'] = (ok, f"{s['worker']} {s['day']} 受影響 {len(f)} 筆 → {subs}")
    # reschedule
    s = SCEN['reschedule']
    changed = merged.copy(); m = changed['任務ID'] == s['task']
    changed.loc[m, '時間窗_開始'] = s['start']; changed.loc[m, '時間窗_結束'] = s['end']
    cand = e.find_insertion_candidates(changed[m].iloc[0], changed, assigned[assigned['任務ID'] != s['task']], cg, cfg, top_n=5, date_column='日期')
    ids = df.ok_ids(cand)
    results['改期'] = (bool(ids), f"{s['task']} 改到 {s['start']}-{s['end']}：可直接插入 {ids}")
    # delay
    s = SCEN['delay']
    cands = cg[cg['居服員ID'].astype(str) != s['worker']]
    base = assigned[assigned['任務ID'] != s['affected']]
    cand = e.find_insertion_candidates(merged[merged['任務ID'] == s['affected']].iloc[0], merged, base, cands, cfg, top_n=5, date_column='日期')
    ids = df.ok_ids(cand)
    results['延遲'] = (bool(ids), f"{s['worker']} 的 {s['delayed']} 延遲 {s['minutes']} 分 → 受影響 {s['affected']}：可接手 {ids}")
    for k, (ok, msg) in results.items():
        print(('PASS ' if ok else 'FAIL ') + k + '｜' + msg)
    return all(ok for ok, _ in results.values())


if __name__ == '__main__':
    ps = sandbox_travel.patches() if os.getenv('SANDBOX', '1') == '1' else []
    for p in ps:
        p.start()
    sys.exit(0 if main(sys.argv[1]) else 1)
