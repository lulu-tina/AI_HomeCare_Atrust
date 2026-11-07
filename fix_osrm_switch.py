"""側邊欄「交通進階設定」加入機車路網切換：本機 Docker ／ 雲端 Render。
  ‧ 預設自動判斷：設定（環境變數或 secrets）指向外部主機 → 雲端 Render；沒設定 → 本機 Docker
    所以筆電上預設本機 Docker，Streamlit Cloud 上預設 Render
  ‧ 可隨時手動切換，下方顯示目前實際連線位址
用法（在專案資料夾執行）：python fix_osrm_switch.py
會備份 app.py.bak8；找不到要改的位置就不改檔。
"""
import shutil, sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = path.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if 'osrm_source_choice' in s:
    sys.exit('已經套用過這個修正，不需要再改。')

new = """        if provider in ('osrm','hybrid'):
            from osrm_client import is_remote as _osrm_is_remote
            _local_osrm = 'http://127.0.0.1:5001'
            _cloud_osrm = 'https://careflow-osrm-motorcycle.onrender.com'
            _configured = os.getenv('CAREFLOW_OSRM_MOTORCYCLE_HOST')
            if not _configured:
                try:
                    _configured = st.secrets.get('CAREFLOW_OSRM_MOTORCYCLE_HOST')
                except Exception:
                    _configured = None
            _configured_remote = bool(_configured) and _osrm_is_remote(str(_configured))
            if _configured_remote:
                _cloud_osrm = str(_configured).rstrip('/')
            _osrm_choices = {'本機 Docker': _local_osrm, '雲端 Render': _cloud_osrm}
            _osrm_pick = st.radio('機車路網', list(_osrm_choices), index=1 if _configured_remote else 0,
                                  horizontal=True, key='osrm_source_choice',
                                  help='在自己電腦執行時預設本機 Docker；部署到 Streamlit Cloud 時預設 Render。雲端版連不到本機 Docker。')
            st.session_state['CAREFLOW_OSRM_MOTORCYCLE_HOST'] = _osrm_choices[_osrm_pick]
            st.caption(f"機車路網連線：{_osrm_choices[_osrm_pick]}（{'外部主機' if _osrm_is_remote(_osrm_choices[_osrm_pick]) else '本機'}）")
"""
candidates = [
    # 已執行過 fix_sidebar.py 的版本
    """        if provider in ('osrm','hybrid'):
            from osrm_client import endpoint as _osrm_endpoint, is_remote as _osrm_is_remote
            _osrm_host = _osrm_endpoint()
            st.caption(f"機車路網連線：{_osrm_host}（{'外部主機' if _osrm_is_remote(_osrm_host) else '本機'}）")
""",
    # 原始版本（輸入欄）
    """        if provider in ('osrm','hybrid'):
            st.text_input('OSRM 機車服務網址', value=os.getenv('CAREFLOW_OSRM_MOTORCYCLE_HOST','http://127.0.0.1:5001'), key='CAREFLOW_OSRM_MOTORCYCLE_HOST')
""",
]
for old in candidates:
    if s.count(old) == 1:
        s = s.replace(old, new)
        break
else:
    sys.exit('找不到側邊欄機車路網設定，app.py 沒有被修改。請把 app.py 傳給我。')

compile(s, str(path), 'exec')
shutil.copy(path, str(path) + '.bak8')
path.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print('完成：側邊欄可切換「本機 Docker／雲端 Render」。備份為 app.py.bak8')
