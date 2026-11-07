"""臨時異動畫面三項補強（請先執行過 fix_reschedule.py）：
  1. 請假：自動代班後顯示明細表（哪幾筆服務、原居服員 → 代班人／找不到的原因）
  2. 改期：服務選單顯示居服員；候選表標出「原居服員」
  3. 延遲：可先選居服員，再挑案家／日期／服務
用法（在專案資料夾執行）：python fix_ui.py
會先備份成 app.py.bak2，再修改 app.py；任何一處找不到就不改檔。
"""
import shutil, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = p.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if 'leave_cover_report' in s:
    sys.exit('app.py 已經套用過這個修正，不需要再改。')

steps = []
def sub(name, old, new):
    global s
    n = s.count(old)
    if n != 1:
        sys.exit(f'找不到「{name}」要修改的位置（出現 {n} 次），app.py 沒有被修改。請把你的 app.py 傳給我。')
    s = s.replace(old, new)
    steps.append(name)

# ---------- 1. 請假代班明細 ----------
sub('請假：記錄每筆代班結果',
'''            covered = 0
            for tid in todo:
                tasks_now, effective_now = _workflow_frames()
                row = tasks_now[tasks_now['任務ID'].astype(str) == str(tid)]
                client = str(row.iloc[0]['案家ID']) if not row.empty else ''
''',
'''            covered = 0
            report = []
            for tid in todo:
                tasks_now, effective_now = _workflow_frames()
                row = tasks_now[tasks_now['任務ID'].astype(str) == str(tid)]
                client = str(row.iloc[0]['案家ID']) if not row.empty else ''
                info = row.iloc[0] if not row.empty else {}
                entry = {'任務ID': str(tid),
                         '日期': str(info.get('日期', ''))[:10] if len(row) else '',
                         '時段': f"{info.get('時間窗_開始', '')}–{info.get('時間窗_結束', '')}" if len(row) else '',
                         '案家': client_label(client), '原居服員': caregiver_label(cg_id)}
''')
sub('請假：找不到候選時也記錄',
'''                except Exception:
                    continue
                pick = next((r['cg_id'] for r in ranked if r.get('available')), None)
                if pick is not None and _quick_reassign(tid, str(pick), '請假代班（系統自動建議）', apply_scope='僅本次') is None:
                    covered += 1
            return covered, len(todo)
''',
'''                except Exception as exc:
                    report.append({**entry, '代班居服員': '—', '結果': f'候選計算失敗：{exc}'})
                    continue
                pick = next((r['cg_id'] for r in ranked if r.get('available')), None)
                msg = _quick_reassign(tid, str(pick), '請假代班（系統自動建議）', apply_scope='僅本次') if pick is not None else '找不到可派人選，請到「人工調整班表」處理'
                if msg is None:
                    covered += 1
                report.append({**entry, '代班居服員': caregiver_label(str(pick)) if msg is None else '—',
                               '結果': '已代班' if msg is None else msg})
            return covered, len(todo), report
''')
sub('請假：保存明細供畫面顯示',
'''                _covered, _affected_total = _auto_cover_leave(_cover_cg)
''',
'''                _covered, _affected_total, _cover_rows = _auto_cover_leave(_cover_cg)
            st.session_state['leave_cover_report'] = {'cg': caregiver_label(str(_cover_cg)), 'rows': _cover_rows}
''')
sub('請假：顯示代班明細表',
'''            render_leave(_workflow_store, res['df_cg'], _wf_tasks, _wf_result, overrides)
''',
'''            _leave_report = st.session_state.get('leave_cover_report')
            if _leave_report and _leave_report.get('rows'):
                st.markdown(f"**最近一次請假代班結果｜{_leave_report['cg']}**")
                show_table(pd.DataFrame(_leave_report['rows']), width='stretch', hide_index=True)
                if st.button('關閉代班結果', key='leave_cover_report_close'):
                    st.session_state.pop('leave_cover_report', None)
                    st.rerun()
            render_leave(_workflow_store, res['df_cg'], _wf_tasks, _wf_result, overrides)
''')

# ---------- 共用：服務選單可保留未指派任務 ----------
sub('選單：可保留未指派任務',
'''        def _task_picker(prefix, tasks, assigned=None):''',
'''        def _task_picker(prefix, tasks, assigned=None, keep_unassigned=False):''')
sub('選單：過濾條件',
'''            if assigned is not None:
                frame['居服員'] = frame['任務ID'].map(assigned)
                frame = frame[frame['居服員'].notna() & (frame['居服員'].astype(str) != '未指派')]
''',
'''            if assigned is not None:
                frame['居服員'] = frame['任務ID'].map(assigned)
                if not keep_unassigned:
                    frame = frame[frame['居服員'].notna() & (frame['居服員'].astype(str) != '未指派')]
''')
sub('選單：未指派顯示文字',
'''                if row.get('居服員') is not None:
                    text += f"｜{caregiver_label(str(row['居服員']))}"
''',
'''                if row.get('居服員') is not None and pd.notna(row.get('居服員')) and str(row['居服員']) not in ('', '未指派', 'None'):
                    text += f"｜{caregiver_label(str(row['居服員']))}"
                elif assigned is not None:
                    text += "｜未指派"
''')

# ---------- 2. 改期顯示原居服員 ----------
sub('改期：選單帶出居服員',
'''            _reschedule_task_id = _task_picker('reschedule', df_tasks)
''',
'''            _resched_assigned = None
            if not _effective_result_for_reschedule.empty:
                _resched_unique = _effective_result_for_reschedule.drop_duplicates('任務ID')
                _resched_assigned = pd.Series(_resched_unique['派單居服員'].values, index=_resched_unique['任務ID'].astype(str).values)
            _reschedule_task_id = _task_picker('reschedule', df_tasks, assigned=_resched_assigned, keep_unassigned=True)
            _resched_orig_cg = ''
            if _reschedule_task_id is not None and _resched_assigned is not None:
                _resched_orig_cg = _resched_assigned.get(str(_reschedule_task_id))
                _resched_orig_cg = '' if _resched_orig_cg is None or pd.isna(_resched_orig_cg) or str(_resched_orig_cg) in ('未指派', 'None') else str(_resched_orig_cg)
''')
sub('改期：原排程顯示原居服員',
'''                        f"{_orig_task.get('時間窗_結束', '')}"
                    )
''',
'''                        f"{_orig_task.get('時間窗_結束', '')}"
                        + (f"｜原居服員 {caregiver_label(_resched_orig_cg)}" if _resched_orig_cg else "｜目前未指派")
                    )
''')
sub('改期：候選表標示原居服員',
'''
                                _top3 = _available.head(3).copy()
''',
'''
                                _top3 = _available.head(3).copy()
                                if _resched_orig_cg:
                                    _top3.insert(2, "原居服員", _top3["居服員ID"].astype(str).eq(_resched_orig_cg).map({True: "✓", False: ""}))
''')
sub('改期：候選按鈕標示原居服員',
'''                                    _label = f"候選 {_rank}｜{caregiver_label(_new_cg)}"
                                    if pd.notna(_score):
                                        _label += f"｜適配分數 {float(_score):.1f}"
                                    _label += f"｜擾動成本 {_cost}"
                                    _left.markdown(_label)
                                    _left.caption(
                                        f"🕒 前一任務 {_prev_slot} → 本次服務 → 下一任務 {_next_slot}"
                                    )

                                    if _right.button(
                                        "✅ 確認改期",''',
'''                                    _label = f"候選 {_rank}｜{caregiver_label(_new_cg)}"
                                    if _resched_orig_cg and _new_cg == _resched_orig_cg:
                                        _label += "｜⭐ 原居服員"
                                    if pd.notna(_score):
                                        _label += f"｜適配分數 {float(_score):.1f}"
                                    _label += f"｜擾動成本 {_cost}"
                                    _left.markdown(_label)
                                    _left.caption(
                                        f"🕒 前一任務 {_prev_slot} → 本次服務 → 下一任務 {_next_slot}"
                                    )

                                    if _right.button(
                                        "✅ 確認改期",''')

# ---------- 3. 延遲可先選居服員 ----------
sub('延遲：居服員篩選',
'''            _delay_task_id = _task_picker('delay', df_tasks, assigned=_delay_assigned) if _delay_assigned is not None else None
''',
'''            _delay_tasks_view, _delay_who = df_tasks, '全部'
            if _delay_assigned is not None:
                _delay_people = sorted({str(v) for v in _delay_assigned.dropna()} - {'', '未指派', 'None', 'nan'})
                _delay_who = st.selectbox('居服員（可先選人，再挑案家與服務）', ['全部'] + _delay_people,
                                          format_func=lambda v: '全部居服員' if v == '全部' else caregiver_label(v),
                                          key='delay_pick_cg')
                if _delay_who != '全部':
                    _delay_ids = set(_delay_assigned[_delay_assigned.astype(str) == _delay_who].index)
                    _delay_tasks_view = df_tasks[df_tasks['任務ID'].astype(str).isin(_delay_ids)]
            _delay_task_id = _task_picker(f'delay_{_delay_who}', _delay_tasks_view, assigned=_delay_assigned) if _delay_assigned is not None else None
''')

compile(s, str(p), 'exec')   # 語法檢查，失敗就不寫入
shutil.copy(p, str(p) + '.bak2')
p.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print(f'完成 {len(steps)} 處修改。原檔備份在 {p}.bak2')
