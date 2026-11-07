"""所有地方都顯示「本機 Docker／雲端 Render」切換，並在無法使用本機 Docker 時直接提醒（請先執行過 fix_osrm_default.py）。
  ‧ 你的筆電（secrets.toml 設 127.0.0.1）→ 預設本機 Docker
  ‧ Streamlit Cloud、工程師電腦 → 預設雲端 Render；切到本機 Docker 時顯示提醒，說明需自架 Docker
  ‧ 不論是否執行過 fix_osrm_hide.py 都可以用（會還原成顯示開關）
用法（在專案資料夾執行）：python fix_osrm_show.py
會備份 app.py.bak11；找不到要改的位置就不改檔。
"""
import re, shutil, sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
raw = path.read_bytes().decode('utf-8')
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')
if '本機 Docker 需要在自己電腦自架' in s:
    sys.exit('已經套用過這個修正，不需要再改。')
if '_configured_local' not in s:
    sys.exit('請先執行 fix_osrm_default.py，再執行這支。')

new = """            _osrm_pick = st.radio('機車路網', list(_osrm_choices), index=0 if _configured_local else 1,
                                  horizontal=True, key='osrm_source_choice',
                                  help='預設雲端 Render。機構內部正式使用時，可用 Docker 自架機車路網（見 osrm_motorcycle 資料夾），案家座標不會傳到外部。')
            if _osrm_pick == '本機 Docker' and not _configured_local:
                st.warning('本機 Docker 需要在自己電腦自架機車路網（安裝 Docker 後執行 osrm_motorcycle 資料夾內的腳本）。'
                           '雲端版或尚未架設時請選「雲端 Render」，否則交通時間會算不出來。')
"""
patterns = [
    # 執行過 fix_osrm_hide.py 的版本
    re.compile(r"            if _configured_local:\n                _osrm_pick = st\.radio\(.*?\n            else:\n                _osrm_pick = '雲端 Render'[^\n]*\n", re.S),
    # 只執行到 fix_osrm_default.py 的版本
    re.compile(r"            _osrm_pick = st\.radio\('機車路網'.*?key='osrm_source_choice',\n.*?\)\n", re.S),
]
for pat in patterns:
    found = pat.findall(s)
    if len(found) == 1:
        m = pat.search(s)
        s = s[:m.start()] + new + s[m.end():]
        break
else:
    sys.exit('找不到機車路網切換開關，app.py 沒有被修改。請把 app.py 傳給我。')

compile(s, str(path), 'exec')
shutil.copy(path, str(path) + '.bak11')
path.write_bytes((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
print('完成：所有地方都顯示切換開關；無法使用本機 Docker 時會直接提醒。備份為 app.py.bak11')
