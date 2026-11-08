"""CareFlow 錯誤紀錄：畫面上給居督看白話訊息，完整的錯誤追蹤寫進紀錄檔給資訊人員。

- 紀錄檔：output_results/logs/careflow_errors.log（每檔 1MB、保留 5 份，自動輪替）
- 畫面：同一次作業的錯誤會留在 st.session_state['_error_log']（最多 30 筆），側邊欄可展開或下載
- 不記錄個資：只記程式位置、錯誤類型與訊息
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import logging
import os
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.getenv('CAREFLOW_LOG_DIR', str(Path(__file__).parent / 'output_results' / 'logs')))
_logger = logging.getLogger('careflow')


def _ensure_handler():
    if any(getattr(h, '_careflow', False) for h in _logger.handlers):
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_DIR / 'careflow_errors.log', maxBytes=1_000_000, backupCount=5, encoding='utf-8')
    except OSError:
        handler = logging.StreamHandler()
    handler._careflow = True
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def log_error(where, exc=None):
    """記一筆錯誤；回傳錯誤代碼（例 E1108-1415-3），畫面可以顯示給使用者回報。"""
    _ensure_handler()
    stamp = datetime.now()
    detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc is not None else ''
    message = f'{type(exc).__name__}: {exc}' if exc is not None else ''
    entry = {'時間': stamp.strftime('%m/%d %H:%M:%S'), '位置': str(where), '錯誤': message[:300]}
    try:
        import streamlit as st
        items = st.session_state.setdefault('_error_log', [])
        entry['代碼'] = f"E{stamp:%m%d-%H%M}-{len(items) + 1}"
        items.append(entry)
        del items[:-30]
    except Exception:          # 沒有 Streamlit 畫面（例如命令列、測試）時只寫檔
        entry['代碼'] = f"E{stamp:%m%d-%H%M%S}"
    _logger.error('[%s] %s %s\n%s', entry['代碼'], where, message, detail)
    return entry['代碼']


def session_errors():
    try:
        import streamlit as st
        return list(st.session_state.get('_error_log', []))
    except Exception:
        return []


def log_file():
    return LOG_DIR / 'careflow_errors.log'
