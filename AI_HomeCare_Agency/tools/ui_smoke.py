"""Smoke test: run app.py end to end against a fake `streamlit` (no browser).

It cannot judge layout, but it executes every code path the page reaches and fails on the first Python error:
    python tools/ui_smoke.py [excel.xlsx]
Scenario: empty page -> upload Excel -> click 開始排班 -> rerun with results (all sections render).
Network travel lookups are replaced with a constant so nothing leaves the sandbox.
"""
import io, os, pathlib, runpy, sys, traceback, types
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


class StopApp(BaseException):
    pass


class RerunApp(BaseException):
    pass


class SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class Upload(io.BytesIO):
    def __init__(self, path):
        super().__init__(pathlib.Path(path).read_bytes())
        self.name = pathlib.Path(path).name
        self.size = len(self.getvalue())
        self.type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        self.file_id = 'smoke-1'


class Fake:
    """Context manager + container + widget factory in one."""
    calls = []

    def __init__(self, st):
        self._st = st

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        fn = getattr(self._st, '_w_' + name, None)
        if fn is not None:
            return fn
        return lambda *a, **k: Fake(self._st)

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __call__(self, *a, **k):
        return Fake(self._st)


class FakeSt(types.ModuleType):
    def __init__(self, uploads, clicks):
        super().__init__('streamlit')
        self.session_state = SS()
        self.uploads, self.clicks, self.fired = uploads, set(clicks), set()
        self.errors, self.reruns = [], 0
        self.seen_options = {}
        self.sidebar = Fake(self)
        self.column_config = Fake(self)
        self.secrets = {}
        self.query_params = {}
        self.__version__ = '1.52.0'

    # ---- decorators
    def cache_data(self, *a, **k):
        import copy, hashlib, pickle

        def deco(fn):
            memo = self.__dict__.setdefault('_memo', {}).setdefault(fn.__qualname__, {})

            def wrapper(*args, **kwargs):
                key = hashlib.sha256(pickle.dumps((args, sorted(kwargs.items())))).hexdigest()
                if key not in memo:
                    memo[key] = fn(*args, **kwargs)
                return copy.deepcopy(memo[key])
            wrapper.clear = memo.clear
            return wrapper
        return deco(a[0]) if a and callable(a[0]) else deco
    cache_resource = cache_data

    def fragment(self, *a, **k):
        return a[0] if a and callable(a[0]) else (lambda fn: fn)

    # ---- control flow
    def stop(self):
        raise StopApp()

    def rerun(self, *a, **k):
        self.reruns += 1
        raise RerunApp()

    def set_page_config(self, *a, **k):
        pass

    # ---- state-aware widgets
    def _keyed(self, key, default):
        if key is None:
            return default
        if key not in self.session_state:
            self.session_state[key] = default
        return self.session_state[key]

    def _w_button(self, label, key=None, on_click=None, args=None, kwargs=None, disabled=False, **k):
        hit = (not disabled) and key is not None and key not in self.fired and any(
            key == c or (c.endswith('*') and key.startswith(c[:-1])) for c in self.clicks)
        if hit:
            self.fired.add(key)
            if on_click:
                on_click(*(args or ()), **(kwargs or {}))
        return hit

    def _w_form_submit_button(self, *a, **k):
        return False

    def _w_download_button(self, *a, **k):
        return False

    def _w_file_uploader(self, label, key=None, **k):
        return self._keyed(key, self.uploads.get(key))

    def _w_checkbox(self, label, value=False, key=None, **k):
        return self._keyed(key, value)
    _w_toggle = _w_checkbox

    def _w_selectbox(self, label, options=(), index=0, key=None, **k):
        options = list(options)
        self.seen_options[key] = options
        default = options[index] if options and index is not None else None
        return self._keyed(key, default)

    def _w_radio(self, label, options=(), index=0, key=None, **k):
        return self._w_selectbox(label, options, index, key)

    def _w_segmented_control(self, label, options=(), default=None, key=None, **k):
        return self._keyed(key, default if default is not None else (list(options)[0] if options else None))

    def _w_multiselect(self, label, options=(), default=None, key=None, **k):
        return self._keyed(key, list(default or []))

    def _w_slider(self, label, min_value=0, max_value=100, value=None, step=None, key=None, **k):
        return self._keyed(key, min_value if value is None else value)

    def _w_number_input(self, label, min_value=None, max_value=None, value=None, step=None, key=None, **k):
        return self._keyed(key, value if value is not None else (min_value if min_value is not None else 0.0))

    def _w_text_input(self, label, value='', key=None, **k):
        return self._keyed(key, value)
    _w_text_area = _w_text_input

    def _w_date_input(self, label, value=None, key=None, **k):
        return self._keyed(key, value)

    def _w_data_editor(self, data, *a, **k):
        return data

    def _w_columns(self, spec, *a, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Fake(self) for _ in range(n)]

    def _w_tabs(self, labels, *a, **k):
        return [Fake(self) for _ in labels]

    def _w_error(self, msg, *a, **k):
        self.errors.append(str(msg))
        return Fake(self)

    def _w_progress(self, *a, **k):
        return Fake(self)

    def __getattr__(self, name):
        if name.startswith('__') or name.startswith('_w_') or name == 'session_state':
            raise AttributeError(name)
        fn = getattr(type(self), '_w_' + name, None)
        if fn is not None:
            return fn.__get__(self)
        return lambda *a, **k: Fake(self)


def make_st(uploads, clicks, state=None):
    st = FakeSt(uploads, clicks)
    if state is not None:
        st.session_state = state
    return st


def run_once(st, label):
    sys.modules['streamlit'] = st
    st.fired = set()
    for attempt in range(6):
        for m in list(sys.modules):
            if m in ('app',):
                del sys.modules[m]
        try:
            runpy.run_path(str(ROOT / 'app.py'), run_name='__main__')
            status = 'finished'
        except StopApp:
            tb = traceback.extract_tb(sys.exc_info()[2])
            status = 'stopped (st.stop) at app.py line %s' % next((f.lineno for f in reversed(tb) if f.filename.endswith('app.py')), '?')
        except RerunApp:
            continue
        except Exception:
            print(f'[{label}] EXCEPTION'); traceback.print_exc()
            return False, st
        print(f'[{label}] {status}; st.error count={len(st.errors)}')
        for e in st.errors[:5]:
            print('   error:', e[:200])
        return True, st
    print(f'[{label}] too many reruns')
    return False, st


def main():
    os.environ.setdefault('CAREFLOW_SYNC_ANALYSIS', '1')
    excel = sys.argv[1] if len(sys.argv) > 1 else '/mnt/user-data/outputs/careflow_v13/居服機構資料_三工作表_v2_修復後.xlsx'
    from unittest.mock import patch
    import caregiver_engine as e
    sys.path.insert(0, str(ROOT / 'tools'))
    import sandbox_travel
    patches = sandbox_travel.patches()
    for p in patches:
        p.start()
    st = make_st({}, [], None)
    sys.modules['streamlit'] = st

    def scenario(label, clicks):
        import time
        st.clicks, st.errors, st.reruns = set(clicks), [], 0
        t0 = time.time()
        prof = None
        if label in os.environ.get('SMOKE_PROFILE', '').split('|'):
            import cProfile
            prof = cProfile.Profile(); prof.enable()
        ok_ = run_once(st, label)[0]
        if prof:
            import pstats
            prof.disable(); pstats.Stats(prof).sort_stats('cumulative').print_stats(45)
        print(f'      ({time.time() - t0:.2f} s)')
        return ok_

    ok1 = scenario('empty page', [])
    st.session_state['agency_workbook'] = Upload(excel)
    st.session_state['agency_range_start'] = date(2026, 10, 1)
    ok2 = scenario('after upload', [])
    ok3 = scenario('run schedule', ['run_full_period'])
    state = st.session_state
    EX1 = ("請問貴單位有人力嗎？ 姓名：劉O 性別/年齡：女性82歲 地址：新北市中和區大智街106巷O號 失能等級：3 失智：X 身障：X 身高/體重：155/62"
           "服務時間及項目：10/2(五)08:20~11:20 BA14+BA133 聯合醫院昆明院區概況簡述：… 備註：1.女性居服員，須注意個案外出就醫安全2.個案有敬老優先權，看診及領藥皆可優先安排")
    EX2 = ("請問貴單位有人力嗎？ 姓名：樓O志 性別/年齡：男99歲 地址：新北市中和區景福里和平街O號 失能等級：5 失智：V 身障：V 身高/體重：161/60 "
           "服務時間及項目：9/26(六)10:00~12:00 GA09×1 9/27(日)10:00~12:00 GA09×1 9/28(一)16:00~18:00 GA09×1 概況簡述：… 備註：1.女性居服員，無須協助沐浴 2.可拆單位")
    if os.environ.get('SMOKE_EX1_FILE'):
        EX1 = pathlib.Path(os.environ['SMOKE_EX1_FILE']).read_text(encoding='utf-8')
    if os.environ.get('SMOKE_EX2_FILE'):
        EX2 = pathlib.Path(os.environ['SMOKE_EX2_FILE']).read_text(encoding='utf-8')
    ss = st.session_state
    oks = [ok1, ok2, ok3]
    ss['dynamic_event_type_selector'] = '➕ 臨時新增案件'
    for ex_name, ex in (() if os.environ.get('SMOKE_SKIP_TEMP') else (('Example 1', EX1), ('Example 2', EX2))):
        ss['tc_raw'] = ex
        oks.append(scenario(f'{ex_name}: parse', ['tc_parse']))
        oks.append(scenario(f'{ex_name}: edit view', []))
        oks.append(scenario(f'{ex_name}: search', ['tc_search_*']))
        oks.append(scenario(f'{ex_name}: results view', []))
        oks.append(scenario(f'{ex_name}: confirm', ['tc_confirm_*']))
        oks.append(scenario(f'{ex_name}: after confirm', []))
        oks.append(scenario(f'{ex_name}: clear', ['tc_clear']))
    print('dynamic_insertions:', len(ss.get('dynamic_insertions', [])))
    for ev in (() if os.environ.get('SMOKE_SKIP_TEMP') else ('🚨 居服員臨時請假', '🕒 案家臨時改期', '⏱️ 前一服務延遲')):
        ss['dynamic_event_type_selector'] = ev
        oks.append(scenario(f'event {ev}', []))
    # 新流程：卡片 → 方案 → 自動預覽 → 確認寫入（逐案走一遍）；SMOKE_V17=案家1,案家2,...
    n_before = len(st.session_state.get('pending_client_summary', []))
    def _wait_bg():
        import analysis_bg, time as _t
        t0 = _t.time()
        while any(v['state'] in ('queued', 'running') for v in analysis_bg._REG.values()):
            _t.sleep(0.5)
        print('      bg wait', round(_t.time() - t0, 1), 's')
    for case in [c for c in os.environ.get('SMOKE_V17', '').split(',') if c]:
        _wait_bg()
        ss['pending_client_choice'] = case
        oks.append(scenario(f'v17 {case} screen', []))
        import analysis_bg
        _slots = [v for k, v in analysis_bg._REG.items() if k[2] == case and v['state'] == 'done' and v['entry'] is not None]
        print('      plans:', [(p['種類'], p['仍未派']) for p in (_slots[-1]['entry']['plans'] if _slots else [])])
        oks.append(scenario(f'v17 {case} card click', [f'pending_card_{case}']))
        oks.append(scenario(f'v17 {case} apply plan 1', [f'pending_plan_{case}_0']))
        print('      edits:', ss.get('pending_edits', {}).get(case))
        oks.append(scenario(f'v17 {case} state after plan', []))
        oks.append(scenario(f'v17 {case} manual shift', [f'pending_edit_later_{case}']))
        oks.append(scenario(f'v17 {case} reset', [f'pending_edit_clear_{case}']))
        oks.append(scenario(f'v17 {case} manual split', [f'pending_edit_split_{case}_*']))
        print('      edits after manual split:', ss.get('pending_edits', {}).get(case))
        oks.append(scenario(f'v17 {case} reset 2', [f'pending_edit_clear_{case}']))
        oks.append(scenario(f'v17 {case} apply plan again', [f'pending_plan_{case}_0']))
        oks.append(scenario(f'v17 {case} commit', [f'pending_simple_commit_{case}']))
        _wait_bg()
        oks.append(scenario(f'v17 {case} after commit', []))
        print('      notice/log:', len(ss.get('last_result', {}).get('whole_client_log', []) or []))
    n_after = len(st.session_state.get('pending_client_summary', []))
    print('pending clients before/after:', n_before, '->', n_after)
    for cid in list(st.seen_options.get('pending_client_choice', [])):
        ss['pending_client_choice'] = cid
        oks.append(scenario(f'pending {cid} screen', []))
        oks.append(scenario(f'pending {cid} trial', ['pending_trial_run']))
        for e_ in st.errors:
            print('      note:', e_[:120])
        oks.append(scenario(f'pending {cid} preview', ['pending_preview_btn']))
        oks.append(scenario(f'pending {cid} apply', ['apply_pending_client']))
    # ---- v16: 月曆上一週／下一週、整案調整（拆成兩段／後移）、處理建議
    wk0 = ss.get('cf_week')
    oks.append(scenario('calendar: next week', ['cf_next_week']))
    wk1 = ss.get('cf_week')
    oks.append(scenario('calendar: prev week', ['cf_prev_week']))
    wk2 = ss.get('cf_week')
    print('calendar weeks:', wk0, '->', wk1, '->', wk2)
    if wk0 is not None and not (wk1 is not None and (wk1 - wk0).days == 7 and wk2 == wk0):
        print('CALENDAR WEEK BUTTONS FAILED')
        oks.append(False)
    oks.append(scenario('final rerun', []))
    ok1 = all(oks)
    state = ss
    print('last_result present:', 'last_result' in state)
    ok4 = scenario('rerun with results', [])
    ok = ok1 and ok4 and 'last_result' in state
    print('SMOKE', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
