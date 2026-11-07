"""Find demo scenarios that SUCCEED on your own workbook (so a screen recording never gets stuck).
    python tools/demo_finder.py excel.xlsx [new1 new2 leave reschedule delay]
Set SANDBOX=0 to use your real OSRM/Google travel times (recommended on your own machine); the default stub is only for tests.
Every scenario is checked with the same engine calls the app uses; the output lists the exact clicks / text to use.
"""
import os, pathlib, sys, re
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
import pandas as pd
import caregiver_engine as e
import pending_analysis as pa
from calendar_view import apply_overrides_to_result
from dispatch_message import parse_dispatch_message
from address_geocoder import get_geocoder
import temp_case as tc

WEEK = '一二三四五六日'
EX1 = ("請問貴單位有人力嗎？\n姓名：劉O\n性別/年齡：女性82歲\n地址：新北市中和區大智街106巷O號\n失能等級：3\n失智：X\n身障：X\n身高/體重：155/62\n"
       "服務時間及項目：\n{slots}\n"
       "概況簡述：個案意識清楚、對談無礙，能以國、台語溝通。個案三餐可自行簡單準備，或由案家協助購買外食，並可自行使用電鍋加熱食物；可自行使用筷子進食，飯量約一碗，偶爾補充奶粉(益力壯)及其他營養品。個案無法執行備餐及家務工作，用藥可自理，未見嗆咳情形。個案起身、盥洗、穿脫衣物、如廁及沐浴皆可緩慢自理；偶有小便失禁或漏尿情形，可自行清潔與更換，外出時會使用護墊預防，沐浴採坐姿進行。個案於家內行走不需使用輔具，惟需攙扶牆面或大型家具以維持平衡，可上下樓梯，外出時則持雨傘拐輔助行走，平時喜歡獨自至附近市場買菜。就醫時需由他人陪同，以維護外出安全。\n"
       "備註：\n1.女性居服員，須注意個案外出就醫安全\n2.個案有敬老優先權，看診及領藥皆可優先安排")
EX2 = ("請問貴單位有人力嗎？\n姓名：樓O志\n性別/年齡：男99歲\n地址：新北市中和區景福里和平街O號\n失能等級：5\n失智：V\n身障：V\n身高/體重：161/60\n"
       "服務時間及項目：\n{slots}\n"
       "概況簡述：個案意識清楚，雙耳重聽，溝通時需靠近並提高音量，對簡單問答可予以簡短回應，主要以國語溝通。個案三餐由外籍看護協助準備，飲食以軟質食物為主，可自行進食；用藥需由他人協助給藥，進食及飲水時偶有嗆咳情形，需持續留意吞嚥安全。目前個案已無法自行備餐及處理家務，相關生活事務皆需仰賴他人協助。個案如廁及穿脫衣物需他人部分協助，偶有大小便失禁情形；起身、盥洗及沐浴則需由他人全程協助，以維持個人清潔及日常生活安全。個案因心臟附近罹患帶狀皰疹，目前服用止痛藥物，案家表示受藥物影響，近期下肢較為無力。家中行走時需一手持單拐，另一手由他人攙扶，步態較不穩；上下樓梯需他人大量攙扶協助，具跌倒風險。外出則需使用輪椅，並由他人全程陪同及協助，以維護移動及外出安全。\n"
       "備註：\n1.女性居服員，無須協助沐浴\n2.可拆單位")


def slot_text(day, start, end, items):
    return f"{day.month}/{day.day}({WEEK[day.weekday()]}){start}~{end} {items}"


def _plus(start, minutes):
    t = pd.Timestamp('2000-01-01 ' + start) + pd.Timedelta(minutes=minutes)
    return t.strftime('%H:%M')


def search_message(ctx, text, year):
    master = ctx['x']['Service_Code']
    parsed = parse_dispatch_message(text, master['系統代碼'].tolist(), year)
    geo = get_geocoder().lookup(parsed['address'])
    case = dict(client_id='TEMP_CLIENT_T', lat=geo.lat, lon=geo.lon, gender=parsed['caregiver_gender'] or '男女不拘', need=parsed['special_need'],
                heavy=False, env='一般居家環境', note=parsed['note'], dementia=parsed['dementia'])
    tt, errors = tc.build_temp_tasks(case, tc.slots_to_frame(parsed['tasks']), master, 'DEMO', 'demo')
    if errors:
        return None, errors
    found = tc.search_slots(tt, ctx['merged'], ctx['assigned'], ctx['cg'], ctx['cfg'])
    return found, tc.single_caregiver_plan(found)


def ok_ids(frame):
    return [] if frame.empty else [str(x) for x in frame.loc[frame['可直接插入'] == True, '居服員ID']]


def label_cg(ctx, cid):
    return str(cid)


def find_new1(ctx, days):
    out = []
    for day in days:
        for start in ('08:20', '08:00', '09:00', '10:00', '13:00', '14:00', '15:00'):
            text = EX1.format(slots=slot_text(day, start, _plus(start, 180), 'BA14+BA13 聯合醫院昆明院區'))
            found, plan = search_message(ctx, text, day.year)
            if found is None:
                continue
            ids = ok_ids(found[0]['frame'])
            transit = {str(r['居服員ID']) for _, r in ctx['cg'].iterrows() if str(r.get('常用交通工具')) == '大眾運輸'}
            out.append(dict(day=day, start=start, n=len(ids), best=ids[:3], nontransit=len([i for i in ids if i not in transit]), text=text))
    return pd.DataFrame(out).sort_values(['nontransit', 'n'], ascending=False)


def find_new2(ctx, weekends):
    out = []
    for sat in weekends:
        for s1 in ('10:00', '09:00', '14:00'):
            for s3 in ('16:00', '15:00', '14:00'):
                slots = '\n'.join([slot_text(sat, s1, _plus(s1, 120), 'GA09×1'), slot_text(sat + pd.Timedelta(days=1), s1, _plus(s1, 120), 'GA09×1'),
                                   slot_text(sat + pd.Timedelta(days=2), s3, _plus(s3, 120), 'GA09×1')])
                text = EX2.format(slots=slots)
                found, plan = search_message(ctx, text, sat.year)
                if found is None:
                    continue
                per = [len(ok_ids(f['frame'])) for f in found]
                out.append(dict(sat=sat, s1=s1, s3=s3, per_slot=per, one_person=0 if isinstance(plan, list) else len(plan),
                                best=list(plan['居服員ID'].head(3)) if not isinstance(plan, list) and len(plan) else [], text=text))
    return pd.DataFrame(out).sort_values(['one_person'], ascending=False)


def _nontransit(ctx):
    return {str(r['居服員ID']) for _, r in ctx['cg'].iterrows() if str(r.get('常用交通工具')) != '大眾運輸'}


def find_leave(ctx, limit=40):
    from caregiver_engine import rank_candidates_by_availability
    cfg, cg, merged, assigned = ctx['cfg'], ctx['cg'], ctx['merged'], ctx['assigned']
    safe = _nontransit(ctx)
    tm = merged.drop_duplicates('任務ID').set_index('任務ID')
    a = assigned.copy(); a['日期'] = a['任務ID'].map(tm['日期']); a['案家ID'] = a['任務ID'].map(tm['案家ID'])
    out = []
    groups = [(g, f) for g, f in a.groupby([a['派單居服員'].astype(str), pd.to_datetime(a['日期']).dt.date]) if 2 <= len(f) <= 3 and g[0] in safe]
    for (worker, day), frame in groups[:limit]:
        base_ids = [i for i in cg['居服員ID'].astype(str) if i != worker and i in safe]
        effective = assigned[~assigned['任務ID'].isin(frame['任務ID'])].copy()
        covered, subs = 0, []
        for tid in frame['任務ID']:
            try:
                ranked = rank_candidates_by_availability(tid, base_ids, merged, effective, cg, cfg, date_column='日期', df_cl=ctx['cl'])
            except Exception:
                continue
            pick = next((r['cg_id'] for r in ranked if r.get('available')), None)
            if pick is not None:
                effective = pd.concat([effective, pd.DataFrame([{'任務ID': tid, '派單居服員': pick}])], ignore_index=True)
                covered += 1; subs.append((tid, pick))
        out.append(dict(worker=worker, day=str(day), affected=len(frame), covered=covered, subs=subs))
        if covered == len(frame) and len([o for o in out if o['covered'] == o['affected']]) >= 5:
            break
    return pd.DataFrame(out).sort_values(['covered', 'affected'], ascending=False)


def find_reschedule(ctx, limit=60):
    cfg, cg, merged, assigned = ctx['cfg'], ctx['cg'], ctx['merged'], ctx['assigned']
    safe = _nontransit(ctx)
    tm = merged.drop_duplicates('任務ID')
    amap = dict(zip(assigned['任務ID'], assigned['派單居服員'].astype(str)))
    out = []
    sample = tm[tm['任務ID'].isin(amap) & tm['任務ID'].map(amap).isin(safe)]
    sample = sample[pd.to_datetime(sample['日期']).dt.day.between(5, 9)].head(limit)
    for _, t in sample.iterrows():
        tid = t['任務ID']; start, end = str(t['時間窗_開始']), str(t['時間窗_結束'])
        dur = int((pd.Timestamp('2000-01-01 ' + end) - pd.Timestamp('2000-01-01 ' + start)).total_seconds() / 60)
        if dur < 60:
            continue
        for shift in (60, 120):
            ns = _plus(start, shift); ne = _plus(ns, dur)
            if ne > '19:30':
                continue
            changed = merged.copy(); m = changed['任務ID'] == tid
            changed.loc[m, '時間窗_開始'] = ns; changed.loc[m, '時間窗_結束'] = ne
            base = assigned[assigned['任務ID'] != tid]
            try:
                cand = e.find_insertion_candidates(changed[m].iloc[0], changed, base, cg, cfg, top_n=5, date_column='日期')
            except Exception:
                continue
            ids = ok_ids(cand)
            out.append(dict(task=tid, client=t['案家ID'], day=str(t['日期'])[:10], old=f'{start}-{end}', new=f'{ns}-{ne}', original=amap[tid],
                            n=len(ids), keeps_original=amap[tid] in ids, best=ids[:3]))
    df = pd.DataFrame(out)
    return df.sort_values(['keeps_original', 'n'], ascending=False) if not df.empty else df


def find_delay(ctx, limit=60):
    cfg, cg, merged, assigned = ctx['cfg'], ctx['cg'], ctx['merged'], ctx['assigned']
    safe = _nontransit(ctx)
    tm = merged.drop_duplicates('任務ID').set_index('任務ID')
    a = assigned.copy(); a['日期'] = a['任務ID'].map(tm['日期']); a['s'] = a['任務ID'].map(tm['時間窗_開始']); a['en'] = a['任務ID'].map(tm['時間窗_結束'])
    out = []
    for (worker, day), f in a.groupby([a['派單居服員'].astype(str), pd.to_datetime(a['日期']).dt.date]):
        if worker not in safe or len(f) < 2:
            continue
        f = f.sort_values('s')
        rows = f.to_dict('records')
        for i in range(len(rows) - 1):
            p, n = rows[i], rows[i + 1]
            gap = (pd.Timestamp('2000-01-01 ' + str(n['s'])) - pd.Timestamp('2000-01-01 ' + str(p['en']))).total_seconds() / 60
            if not 20 <= gap <= 75:
                continue
            for delay in (30, 45, 60):
                new_end = pd.Timestamp('2000-01-01 ' + str(p['en'])) + pd.Timedelta(minutes=delay)
                avail = (pd.Timestamp('2000-01-01 ' + str(n['s'])) - new_end).total_seconds() / 60
                pl, pn = tm.loc[p['任務ID']], tm.loc[n['任務ID']]
                travel = e.calc_travel_minutes(pl['服務地點_緯度'], pl['服務地點_經度'], pn['服務地點_緯度'], pn['服務地點_經度'], cfg, transport_mode='機車') \
                    if pl['案家ID'] != pn['案家ID'] else 0.0
                if avail >= travel + cfg.buffer_mins:
                    continue
                base = assigned[assigned['任務ID'] != n['任務ID']]
                cands = cg[cg['居服員ID'].astype(str) != worker]
                try:
                    cand = e.find_insertion_candidates(merged[merged['任務ID'] == n['任務ID']].iloc[0], merged, base, cands, cfg, top_n=5, date_column='日期')
                except Exception:
                    continue
                ids = ok_ids(cand)
                out.append(dict(worker=worker, day=str(day), delayed=p['任務ID'], delay=delay, affected=n['任務ID'], gap=gap, need=round(travel + cfg.buffer_mins, 1), n=len(ids), best=ids[:3]))
                break
        if len(out) >= limit:
            break
    df = pd.DataFrame(out)
    return df.sort_values('n', ascending=False) if not df.empty else df


if __name__ == '__main__':
    import sandbox_travel
    excel = sys.argv[1]
    which = sys.argv[2:] or ['new1', 'new2', 'leave', 'reschedule', 'delay']
    ps = sandbox_travel.patches() if os.getenv('SANDBOX', '1') == '1' else []
    for p in ps: p.start()
    ctx = pa.prepare(excel)
    print('assigned', len(ctx['assigned']), 'of', len(ctx['tasks']), flush=True)
    pd.set_option('display.width', 250); pd.set_option('display.max_colwidth', 60)
    d = lambda y, m, dd: pd.Timestamp(y, m, dd)
    if 'new1' in which:
        r = find_new1(ctx, [d(2026, 10, 2), d(2026, 10, 5), d(2026, 10, 6), d(2026, 10, 7), d(2026, 10, 8), d(2026, 10, 9)])
        print('== new1'); print(r.drop(columns='text').head(12).to_string())
        r.to_pickle('/tmp/claude-0/prof/new1.pkl')
    if 'new2' in which:
        r = find_new2(ctx, [d(2026, 10, 3), d(2026, 10, 10), d(2026, 10, 17)])
        print('== new2'); print(r.drop(columns='text').head(12).to_string())
        r.to_pickle('/tmp/claude-0/prof/new2.pkl')
    if 'leave' in which:
        r = find_leave(ctx); print('== leave'); print(r.head(10).to_string())
    if 'reschedule' in which:
        r = find_reschedule(ctx); print('== reschedule'); print(r.head(10).to_string())
    if 'delay' in which:
        r = find_delay(ctx); print('== delay'); print(r.head(10).to_string())
