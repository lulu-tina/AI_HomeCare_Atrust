"""前一服務延遲：受影響的下一案找不到接手人選（「目前沒有可用候選人結果」）的修正。
原因：系統規定案家只能由固定居服員服務，只有固定居服員「請假」才開放別人代班；
      延遲不算請假，所以除了固定居服員本人，所有人都被擋掉。
修正：
  ‧ 找候選人時，把「原居服員延遲趕不上這一案」視同他這段時間請假，開放代班
  ‧ 按「改派此任務」時，自動登錄一筆「請假（前一服務延遲）」不可排時段，只涵蓋受影響那一案的時間，
    讓改派在重新整理後也能保留；之後可在請假頁撤銷並還給原居服員
用法（在專案資料夾執行）：python fix_delay.py
會備份 app.py.bak6、agency_rules.py.bak6；任何一處找不到就不改檔。
"""
import shutil, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
files = {}
for name in ('app.py', 'agency_rules.py'):
    raw = (root / name).read_bytes().decode('utf-8')
    files[name] = ['\r\n' in raw, raw.replace('\r\n', '\n')]
app, rules = files['app.py'][1], files['agency_rules.py'][1]
if '_delay_cfg_for' in app:
    sys.exit('已經套用過這個修正，不需要再改。')

def swap(text, what, old, new):
    if text.count(old) != 1:
        sys.exit(f'找不到「{what}」要修改的位置，檔案沒有被修改。請把 app.py 和 agency_rules.py 傳給我。')
    return text.replace(old, new)

# 1) agency_rules.py：「請假（前一服務延遲）」也算請假，開放代班
rules = swap(rules, '請假判斷',
             "        if block.get('reason') == '請假' and overlap(task, block):",
             "        if str(block.get('reason', '')).startswith('請假') and overlap(task, block):")

# 2) app.py：找候選人時暫時視同原居服員請假
app = swap(app, '延遲候選人計算',
'''                for _a in _affected:
                    try:
                        _cand = find_insertion_candidates(
                            _a["row"],
                            _tasks_merged,
                            _base_result,
                            _candidate_cg,
                            config,
''',
'''                def _delay_cfg_for(_a):
                    """延遲當次視同原固定居服員趕不上這一案（這段時間請假），只在這次計算開放代班，不改動正式設定。"""
                    import json as _json
                    from dataclasses import replace as _replace
                    _row = _a["row"]
                    _client = str(_row.get("案家ID", ""))
                    _owner = config.client_primary_map.get(_client) or _row.get("歷史首選居服員ID")
                    _owner = _cg_id if _owner is None or pd.isna(_owner) or not str(_owner).strip() else str(_owner)
                    _cfg = _replace(config, primary_profiles=dict(config.primary_profiles))
                    _prof = dict(_cfg.primary_profiles.get(_owner) or {})
                    _raw = _prof.get("臨時不可排時段") or "[]"
                    try:
                        _blocks = _json.loads(_raw) if isinstance(_raw, str) else list(_raw)
                    except ValueError:
                        _blocks = []
                    _day = pd.to_datetime(_row.get("日期")).strftime("%Y-%m-%d")
                    _blocks.append({"start": f"{_day}T{str(_row.get('時間窗_開始'))[:5]}:00",
                                    "end": f"{_day}T{str(_row.get('時間窗_結束'))[:5]}:00",
                                    "reason": "請假（前一服務延遲）"})
                    _prof["臨時不可排時段"] = _json.dumps(_blocks, ensure_ascii=False)
                    _cfg.primary_profiles[_owner] = _prof
                    return _cfg

                for _a in _affected:
                    try:
                        _cand = find_insertion_candidates(
                            _a["row"],
                            _tasks_merged,
                            _base_result,
                            _candidate_cg,
                            _delay_cfg_for(_a),
''')

# 3) app.py：確認改派時登錄只涵蓋這一案的「請假（前一服務延遲）」，失敗就撤回
app = swap(app, '延遲確認改派',
'''                                            _conflict_msg = _quick_reassign(
                                                _task_id,
                                                _new_cg,
                                                _reason,
                                            )
''',
'''                                            # 原居服員趕不上這一案：登錄只涵蓋這一案時段的「請假（前一服務延遲）」，
                                            # 才能依規定開放代班，且重新整理後改派不會被退回。
                                            _d_row = _a["row"]
                                            _d_day = pd.to_datetime(_d_row.get("日期")).strftime("%Y-%m-%d")
                                            _d_span = (str(_d_row.get("時間窗_開始"))[:5], str(_d_row.get("時間窗_結束"))[:5])
                                            _d_cg = str(_a.get("original_cg") or _delay_ctx["cg_id"])
                                            _d_before = {b["id"] for b in _workflow_store.blocks()}
                                            _workflow_store.add_blocks(_d_cg, _d_day, [_d_span], "請假（前一服務延遲）")
                                            res["df_cg"] = attach_blocks(res["df_cg"], _workflow_store)
                                            config.primary_profiles = {str(r["居服員ID"]): r.to_dict() for _, r in res["df_cg"].iterrows()}
                                            _conflict_msg = _quick_reassign(
                                                _task_id,
                                                _new_cg,
                                                _reason,
                                            )
                                            if _conflict_msg:
                                                for _b in _workflow_store.blocks():
                                                    if _b["id"] not in _d_before:
                                                        _workflow_store.remove_block(_b["id"])
                                                res["df_cg"] = attach_blocks(res["df_cg"], _workflow_store)
                                                config.primary_profiles = {str(r["居服員ID"]): r.to_dict() for _, r in res["df_cg"].iterrows()}
''')

for name, text in (('app.py', app), ('agency_rules.py', rules)):
    compile(text, name, 'exec')
for name, text in (('app.py', app), ('agency_rules.py', rules)):
    path = root / name
    shutil.copy(path, str(path) + '.bak6')
    path.write_bytes((text.replace('\n', '\r\n') if files[name][0] else text).encode('utf-8'))
print('完成：延遲受影響的服務可以找到接手人選並改派。備份為 *.bak6')
