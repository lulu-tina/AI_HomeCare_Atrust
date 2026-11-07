"""撤銷請假時，可一併把代班的服務還給原居服員（請先執行過 fix_leave.py）。
  ‧ 撤銷區多一個勾選「撤銷後，把這段時間代班的服務還給原居服員」（預設勾選）
  ‧ 還原前逐筆檢查原居服員是否仍有空；有衝突的保留代班並列出原因
  ‧ 撤銷選單改成顯示「編號｜居服員 日期時段（原因）」，不必對照編號
用法（在專案資料夾執行）：python fix_revoke.py
會備份 app.py.bak4、dispatch_workflow.py.bak4；任何一處找不到就不改檔。
"""
import shutil, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
files = {}
for name in ('app.py', 'dispatch_workflow.py'):
    raw = (root / name).read_bytes().decode('utf-8')
    files[name] = ['\r\n' in raw, raw.replace('\r\n', '\n')]
app, wf = files['app.py'][1], files['dispatch_workflow.py'][1]
if 'restore_after_revoke' in app:
    sys.exit('已經套用過這個修正，不需要再改。')
if 'auto_cover_day' not in app:
    sys.exit('請先執行 fix_leave.py，再執行這支。')

def swap(text, what, old, new):
    if text.count(old) != 1:
        sys.exit(f'找不到「{what}」要修改的位置，檔案沒有被修改。請把 app.py 和 dispatch_workflow.py 傳給我。')
    return text.replace(old, new)

# 1) dispatch_workflow.py：撤銷區加勾選與易讀選單
wf = swap(wf, '撤銷區',
"""            bid=st.selectbox('撤銷時段', [b['id'] for b in blocks])
            if st.button('撤銷選取時段'):
                store.remove_block(bid)
                st.session_state['workflow_notice']='已撤銷不可排時段；已改派的服務不會自動還原。'
                st.rerun()
""",
"""            def _block_text(i):
                b=next((x for x in blocks if x['id']==i),None)
                return str(i) if b is None else f"{i}｜{names.get(b['cg'],b['cg'])}｜{b['start'][:10]} {b['start'][11:16]}～{b['end'][11:16]}（{b['reason']}）"
            bid=st.selectbox('撤銷時段', [b['id'] for b in blocks], format_func=_block_text)
            restore=st.checkbox('撤銷後，把這段時間代班的服務還給原居服員', value=True,
                                help='逐筆檢查原居服員是否仍有空；有衝突的保留代班並列出原因。')
            if st.button('撤銷選取時段'):
                chosen=next(b for b in blocks if b['id']==bid)
                store.remove_block(bid)
                if restore:
                    st.session_state['restore_after_revoke']={'cg':chosen['cg'],'start':chosen['start'],'end':chosen['end']}
                    st.session_state['workflow_notice']='已撤銷不可排時段，正在把代班的服務還給原居服員…'
                else:
                    st.session_state['workflow_notice']='已撤銷不可排時段；已改派的服務維持代班。'
                st.rerun()
""")

# 2) app.py：撤銷後還原代班服務
app = swap(app, '還原處理', """        if st.session_state.get('workflow_notice'):
""", """        _restore = st.session_state.pop('restore_after_revoke', None)
        if _restore:
            from dispatch_workflow import overlap as _wf_overlap
            _r_cg = str(_restore['cg'])
            _r_tasks, _ = _workflow_frames()
            _r_rows, _restored = [], 0
            _r_tasks = _r_tasks.sort_values([c for c in ('日期', '時間窗_開始') if c in _r_tasks.columns])
            for _, _t in _r_tasks.iterrows():
                _tid = str(_t['任務ID'])
                if str(assigned_map.get(_t['任務ID'], '')) != _r_cg or not _wf_overlap(_t, _restore):
                    continue
                _ov = overrides.get(_tid)
                if _ov is None or str(_ov.get('cg_id')) == _r_cg:
                    continue          # 沒被改派過，或已經是原居服員
                _now = _ov.get('cg_id')
                _entry = {'任務ID': _tid, '日期': str(_t.get('日期', ''))[:10],
                          '時段': f"{_t.get('時間窗_開始', '')}–{_t.get('時間窗_結束', '')}",
                          '案家': client_label(str(_t.get('案家ID', ''))),
                          '代班居服員': caregiver_label(str(_now)) if _now else '待安排'}
                _msg = _quick_reassign(_tid, _r_cg, '撤銷請假，還給原居服員')
                if _msg is None:
                    _restored += 1
                _r_rows.append({**_entry, '目前居服員': caregiver_label(_r_cg) if _msg is None else _entry['代班居服員'],
                                '結果': '已還給原居服員' if _msg is None else f'未還原：{_msg}'})
            st.session_state['leave_cover_report'] = {
                'title': '撤銷請假還原結果', 'cg': caregiver_label(_r_cg), 'day': str(_restore['start'])[:10], 'rows': _r_rows,
                'empty': '這段時間沒有代班中的服務，不需要還原。'}
            st.session_state['workflow_notice'] = (
                f'已撤銷 {caregiver_label(_r_cg)} {str(_restore["start"])[:10]} 的不可排時段：'
                f'{len(_r_rows)} 筆代班服務中，{_restored} 筆已還給原居服員'
                + (f'；{len(_r_rows) - _restored} 筆因衝突保留代班' if len(_r_rows) > _restored else '')
                + '。明細見下方表格。')
        if st.session_state.get('workflow_notice'):
""")

# 3) app.py：明細表標題與空白提示依情境顯示
app = swap(app, '明細表標題',
"""                st.markdown(f"**最近一次請假代班結果｜{_leave_report['cg']} {_leave_report.get('day', '')}**")
""",
"""                st.markdown(f"**{_leave_report.get('title', '最近一次請假代班結果')}｜{_leave_report['cg']} {_leave_report.get('day', '')}**")
""")
app = swap(app, '明細表空白提示',
"""                    st.info('這位居服員在請假日期沒有原排的服務，不需要代班。')
""",
"""                    st.info(_leave_report.get('empty', '這位居服員在請假日期沒有原排的服務，不需要代班。'))
""")

for name, text in (('app.py', app), ('dispatch_workflow.py', wf)):
    compile(text, name, 'exec')
for name, text in (('app.py', app), ('dispatch_workflow.py', wf)):
    path = root / name
    shutil.copy(path, str(path) + '.bak4')
    path.write_bytes((text.replace('\n', '\r\n') if files[name][0] else text).encode('utf-8'))
print('完成：撤銷請假時可把代班服務還給原居服員。備份為 *.bak4')
