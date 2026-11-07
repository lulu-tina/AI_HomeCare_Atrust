"""機車路網預設改為「雲端 Render」（請先執行過 fix_osrm_switch.py）。
  ‧ 沒有任何設定（工程師直接下載程式執行、Streamlit Cloud）→ 預設雲端 Render
  ‧ 只有在 secrets.toml 設定 CAREFLOW_OSRM_MOTORCYCLE_HOST = "http://127.0.0.1:5001" 的電腦 → 預設本機 Docker
  ‧ 任何電腦都能在側邊欄手動切換
用法（在專案資料夾執行）：python fix_osrm_default.py
會備份 app.py.bak9；找不到要改的位置就不改檔。
"""
import shutil, sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = path.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if '_configured_local' in s:
    sys.exit('已經套用過這個修正，不需要再改。')
if 'osrm_source_choice' not in s:
    sys.exit('請先執行 fix_osrm_switch.py，再執行這支。')

def swap(what, old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f'找不到「{what}」要修改的位置，app.py 沒有被修改。請把 app.py 傳給我。')
    s = s.replace(old, new)

swap('判斷設定', """            _configured_remote = bool(_configured) and _osrm_is_remote(str(_configured))
""", """            _configured_remote = bool(_configured) and _osrm_is_remote(str(_configured))
            _configured_local = bool(_configured) and not _configured_remote
""")
swap('預設選項', """index=1 if _configured_remote else 0,""", """index=0 if _configured_local else 1,""")
swap('說明文字', """help='在自己電腦執行時預設本機 Docker；部署到 Streamlit Cloud 時預設 Render。雲端版連不到本機 Docker。')""",
     """help='預設雲端 Render。只有在 secrets.toml 設定 CAREFLOW_OSRM_MOTORCYCLE_HOST = "http://127.0.0.1:5001" 的電腦才預設本機 Docker。雲端版連不到本機 Docker。')""")

compile(s, str(path), 'exec')
shutil.copy(path, str(path) + '.bak9')
path.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print('完成：機車路網預設改為雲端 Render。備份為 app.py.bak9')
