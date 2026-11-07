"""請假代班明細第二次修正（請先執行過 fix_revoke.py）。
問題：請假還沒撤銷就重新執行派單時，排班引擎會直接把該居服員的服務排給別人（標為「請假代班」），
      所以 AI 結果裡已經沒有他的名字，畫面就誤報「沒有原排的服務」。
修正：「原居服員的服務」= AI 結果排給他的服務 ＋ 排班結果中標為請假代班、固定居服員是他的服務。
      明細依目前實際派單顯示：待安排的自動找代班；已有人服務的列出由誰代班。撤銷還原也用同一份清單。
用法（在專案資料夾執行）：python fix_leave2.py
會備份 app.py.bak5；任何一處找不到就不改檔。
"""
import re, shutil, sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = path.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if '_leave_owned_tasks' in s:
    sys.exit('已經套用過這個修正，不需要再改。')
if 'restore_after_revoke' not in s:
    sys.exit('請先執行 fix_revoke.py，再執行這支。')

def fail(what):
    sys.exit(f'找不到「{what}」要修改的位置，app.py 沒有被修改。請把 app.py 傳給我。')

# 1) 重寫自動代班函式（含共用的「原居服員服務」與「目前派單」小工具）
m = re.search(r"\n        def _auto_cover_leave\(.*?\n(?=        _cover_cg = st\.session_state\.pop\('auto_cover_cg', None\))", s, re.S)
if not m: fail('自動代班函式')
s = s[:m.start()] + '''
        def _leave_owned_tasks(cg_id):
            """原本屬於這位居服員的服務：AI 結果排給他的，加上排班時就因他請假而由別人代班的（固定居服員是他）。"""
            cg_id = str(cg_id)
            owned = {str(t) for t, c in assigned_map.items() if str(c) == cg_id}
            if {'固定居服員ID', '是否請假代班'} <= set(df_result.columns):
                flagged = df_result[(df_result['固定居服員ID'].astype(str) == cg_id)
                                    & df_result['是否請假代班'].astype(str).str.lower().isin(['true', '1', '是'])]
                owned |= set(flagged['任務ID'].astype(str))
            return owned

        def _leave_current_map():
            """目前實際派單（含所有人工改派）；沒有人服務的回傳 None。"""
            _, effective = _workflow_frames()
            current = {}
            for tid, cg in zip(effective.get('任務ID', []), effective.get('派單居服員', [])):
                text = '' if cg is None or (isinstance(cg, float) and pd.isna(cg)) else str(cg)
                current[str(tid)] = None if text in ('', '未指派', 'None', 'nan') else text
            return current

        def _auto_cover_leave(cg_id, day=None, limit=60):
            """請假登錄後，逐筆檢查該居服員當天原排的服務：沒人服務的自動找代班（優先該案備援，再依空檔排序，
            通過同一套衝突檢查才套用）；已有人服務的列出由誰代班。回傳 (已代班數, 需代班數, 明細)。"""
            cg_id = str(cg_id)
            tasks_all, _ = _workflow_frames()
            ids_all = tasks_all['任務ID'].astype(str)
            dates = dict(zip(ids_all, pd.to_datetime(tasks_all['日期'], errors='coerce').dt.strftime('%Y-%m-%d')))
            starts = dict(zip(ids_all, tasks_all['時間窗_開始'].astype(str))) if '時間窗_開始' in tasks_all else {}
            mine = [t for t in _leave_owned_tasks(cg_id) if day is None or dates.get(t) == str(day)[:10]]
            mine = sorted(mine, key=lambda t: (dates.get(t) or '', starts.get(t, '')))[:limit]
            base_ids = [str(x) for x in res['df_cg']['居服員ID'] if str(x) != cg_id]
            covered, need, report = 0, 0, []
            for tid in mine:
                current = _leave_current_map().get(tid)
                if current == cg_id:
                    continue          # 不在請假時段內，仍由本人服務
                tasks_now, effective_now = _workflow_frames()
                row = tasks_now[tasks_now['任務ID'].astype(str) == tid]
                client = str(row.iloc[0]['案家ID']) if not row.empty else ''
                info = row.iloc[0] if not row.empty else {}
                entry = {'任務ID': tid, '日期': dates.get(tid) or '',
                         '時段': f"{info.get('時間窗_開始', '')}–{info.get('時間窗_結束', '')}" if len(row) else '',
                         '案家': client_label(client), '原居服員': caregiver_label(cg_id)}
                if current is not None:
                    report.append({**entry, '代班居服員': caregiver_label(current), '結果': '已由代班服務'})
                    continue
                need += 1
                team = [str(x) for x in (config.client_backup_map.get(client) or ())]
                ids = [x for x in team if x in base_ids] + [x for x in base_ids if x not in team]
                try:
                    ranked = rank_candidates_by_availability(tid, ids, tasks_now, effective_now, res['df_cg'], config,
                                                             date_column='日期', df_cl=res.get('df_cl'))
                except Exception as exc:
                    report.append({**entry, '代班居服員': '—', '結果': f'候選計算失敗：{exc}'})
                    continue
                pick = next((r['cg_id'] for r in ranked if r.get('available')), None)
                msg = (_quick_reassign(tid, str(pick), '請假代班（系統自動建議）', apply_scope='僅本次')
                       if pick is not None else '找不到可派人選，請到「人工調整班表」處理')
                if msg is None:
                    covered += 1
                report.append({**entry, '代班居服員': caregiver_label(str(pick)) if msg is None else '—',
                               '結果': '本次自動代班' if msg is None else msg})
            return covered, need, report

''' + s[m.end():]

# 2) 通知文字：「先前已改派」改成「已由代班服務」
old = "+ (f'；{_earlier} 筆先前已改派' if _earlier else '')"
if s.count(old) != 1: fail('通知文字')
s = s.replace(old, "+ (f'；{_earlier} 筆已由代班服務' if _earlier else '')")

# 3) 撤銷還原：用同一份「原居服員服務」清單與目前派單
old = '''            for _, _t in _r_tasks.iterrows():
                _tid = str(_t['任務ID'])
                if str(assigned_map.get(_t['任務ID'], '')) != _r_cg or not _wf_overlap(_t, _restore):
                    continue
                _ov = overrides.get(_tid)
                if _ov is None or str(_ov.get('cg_id')) == _r_cg:
                    continue          # 沒被改派過，或已經是原居服員
                _now = _ov.get('cg_id')
'''
if s.count(old) != 1: fail('撤銷還原')
s = s.replace(old, '''            _r_owned, _r_now_map = _leave_owned_tasks(_r_cg), _leave_current_map()
            for _, _t in _r_tasks.iterrows():
                _tid = str(_t['任務ID'])
                if _tid not in _r_owned or not _wf_overlap(_t, _restore):
                    continue
                _now = _r_now_map.get(_tid)
                if _now == _r_cg:
                    continue          # 已經是原居服員
''')

compile(s, str(path), 'exec')
shutil.copy(path, str(path) + '.bak5')
path.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print('完成：請假明細會列出原居服員當天所有服務與代班人。備份為 app.py.bak5')
