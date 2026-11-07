"""修正「案家臨時改期」：pandas 3 字串日期欄寫入 Timestamp 失敗，並改用新時段做衝突檢查。
用法（在專案資料夾執行）：python fix_reschedule.py
會先備份成 app.py.bak，再修改 app.py。不依賴行號，也不受 Windows 換行符號影響。
"""
import re, shutil, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = p.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if '_date_cell(' in s:
    sys.exit('app.py 已經套用過這個修正，不需要再改。')
ok = []

# 1) 在「暫時移除原任務並重新找候選人」按鈕前加入 _date_cell 小工具
m = re.search(r'\n([ \t]*)if st\.button\(\s*\n\s*"🔎 暫時移除原任務並重新找候選人"', s)
if m:
    ind = m.group(1)
    helper = (f'\n{ind}def _date_cell(frame, day):\n'
              f'{ind}    """依欄位型別寫入日期：pandas 3 的字串欄位不接受 Timestamp，會丟 TypeError。"""\n'
              f'{ind}    day = pd.Timestamp(day)\n'
              f'{ind}    if "日期" in frame.columns and pd.api.types.is_datetime64_any_dtype(frame["日期"]):\n'
              f'{ind}        return day\n'
              f'{ind}    return day.strftime("%Y-%m-%d")\n')
    s = s[:m.start()] + helper + s[m.start():]
    ok.append('加入 _date_cell')

# 2) 候選人計算時的兩處日期寫入
n1 = s.count('_new_task["日期"] = pd.Timestamp(_new_date)')
s = s.replace('_new_task["日期"] = pd.Timestamp(_new_date)', '_new_task["日期"] = _date_cell(df_tasks, _new_date)')
n2 = s.count('_tasks_changed.loc[_mask, "日期"] = pd.Timestamp(_new_date)')
s = s.replace('_tasks_changed.loc[_mask, "日期"] = pd.Timestamp(_new_date)', '_tasks_changed.loc[_mask, "日期"] = _date_cell(_tasks_changed, _new_date)')
if n1 and n2:
    ok.append('候選人計算日期寫入')

# 3) 確認改期：先換新時段再檢查衝突，失敗就還原
pat = re.compile(
    r'(?P<ind>[ \t]*)_conflict_msg = _quick_reassign\(\s*_reschedule_task_id,\s*_new_cg,\s*_reason,\s*\)\s*\n'
    r'\s*if _conflict_msg:\s*\n\s*st\.error\(_conflict_msg\)\s*\n\s*else:\s*\n'
    r'.*?(?=\n[ \t]*_confirmed_at = pd\.Timestamp\.now\(\))', re.S)
m = pat.search(s)
if m:
    i = m.group('ind'); j = i + '    '
    new = (f'{i}# 先把任務換成新日期／時段，再做衝突檢查；否則會拿「舊時段」去檢查。\n'
           f'{i}_main_tasks = st.session_state["last_result"]["df_tasks"]\n'
           f'{i}_main_mask = _main_tasks["任務ID"].astype(str) == str(_reschedule_task_id)\n'
           f'{i}_cols = [c for c in ("日期", "時間窗_開始", "時間窗_結束") if c in _main_tasks.columns]\n'
           f'{i}_backup = _main_tasks.loc[_main_mask, _cols].copy()\n'
           f'{i}if "日期" in _cols:\n'
           f'{j}_main_tasks.loc[_main_mask, "日期"] = _date_cell(_main_tasks, _new_task_info.get("日期"))\n'
           f'{i}if "時間窗_開始" in _cols:\n'
           f'{j}_main_tasks.loc[_main_mask, "時間窗_開始"] = _new_task_info.get("時間窗_開始")\n'
           f'{i}if "時間窗_結束" in _cols:\n'
           f'{j}_main_tasks.loc[_main_mask, "時間窗_結束"] = _new_task_info.get("時間窗_結束")\n\n'
           f'{i}_conflict_msg = _quick_reassign(\n{j}_reschedule_task_id,\n{j}_new_cg,\n{j}_reason,\n{i})\n\n'
           f'{i}if _conflict_msg:\n'
           f'{j}_main_tasks.loc[_main_mask, _cols] = _backup.values   # 檢查失敗就還原原時段\n'
           f'{j}st.error(_conflict_msg)\n'
           f'{i}else:')
    s = s[:m.start()] + new + s[m.end():]
    ok.append('確認改期檢查順序')

if len(ok) != 3:
    sys.exit(f'找不到全部要改的地方（只找到：{ok}），app.py 沒有被修改。請把你的 app.py 傳給我。')
compile(s, str(p), 'exec')   # 語法檢查，失敗就不寫入
shutil.copy(p, str(p) + '.bak')
p.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print('完成：' + '、'.join(ok) + f'。原檔備份在 {p}.bak')
