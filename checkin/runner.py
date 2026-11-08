"""讓打卡網頁跟著 CareFlow 一起啟動，並決定要給居服員的打卡網址。

居督不需要另外雙擊 start_checkin.bat、也不用查 IP：
- CareFlow 開啟時在背景啟動打卡網頁（同一台電腦、port 8600）。如果已經有人用 start_checkin.bat 開著，就直接沿用。
- 打卡網址依序取：資訊人員在 04 頁設定的固定網址 → 環境變數 CAREFLOW_CHECKIN_PUBLIC_URL → 這台電腦在辦公室網路的位址。
  只有 https 的固定網址手機才會給定位；辦公室位址只能在同一個 Wi-Fi 試用。

設 CAREFLOW_CHECKIN_AUTOSTART=0 可以關掉自動啟動（例如打卡網頁另外架在別台主機）。
"""
from __future__ import annotations

import io
import os
import socket
import threading
import urllib.request
from pathlib import Path

from checkin import store

PORT = int(os.getenv('CAREFLOW_CHECKIN_PORT', '8600'))
URL_KEY = 'public_url'
_lock = globals().get('_lock') or threading.Lock()          # 模組重新載入時保留
_started = globals().get('_started', {})


def autostart_enabled():
    return os.getenv('CAREFLOW_CHECKIN_AUTOSTART', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def is_up(url, timeout=1.5):
    """打卡網頁有沒有在回應（打 /healthz）。"""
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/healthz', timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_running(port=PORT):
    """背景啟動打卡網頁；已經在跑（本程式或 start_checkin.bat）就不重複開。回傳 (是否在跑, 說明)。"""
    local = f'http://127.0.0.1:{port}'
    if is_up(local):
        return True, '打卡網頁運作中'
    if not autostart_enabled():
        return False, '自動啟動已關閉（CAREFLOW_CHECKIN_AUTOSTART=0）'
    with _lock:
        thread = _started.get(port)
        if thread is None or not thread.is_alive():
            from checkin.server import create_app
            app = create_app()

            def serve():
                try:
                    from waitress import serve as waitress_serve
                    waitress_serve(app, host='0.0.0.0', port=port, threads=8, _quiet=True)
                except OSError:
                    pass          # port 被占用：通常是 start_checkin.bat 已經開著
                except ImportError:
                    app.run(host='0.0.0.0', port=port, use_reloader=False)

            thread = threading.Thread(target=serve, name='careflow-checkin', daemon=True)
            thread.start()
            _started[port] = thread
    for _ in range(20):
        if is_up(local, timeout=0.5):
            return True, '打卡網頁運作中'
        threading.Event().wait(0.15)
    return False, f'打卡網頁沒有啟動成功（port {port} 可能被其他程式占用）'


def lan_ip():
    """這台電腦在辦公室網路的 IP（不會真的送出封包）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return '127.0.0.1'


def checkin_url(path=None):
    """回傳 (網址, 種類)。種類：'固定網址'、'外部網址'（都是 https，外面也能打、可定位）或 '辦公室網路'（只能同 Wi-Fi、不能定位）。"""
    fixed = store.get_setting(URL_KEY, path=path) or os.getenv('CAREFLOW_CHECKIN_PUBLIC_URL', '')
    fixed = fixed.strip().rstrip('/')
    if fixed:
        return fixed, '固定網址'
    if tunnel_url():
        return tunnel_url(), '外部網址'
    return office_url(path), '辦公室網路'


def save_public_url(url, path=None):
    url = (url or '').strip().rstrip('/')
    if url and not url.startswith('https://'):
        raise ValueError('固定網址要用 https:// 開頭，手機才會給定位。')
    store.set_setting(URL_KEY, url, path=path)
    return url


def qr_png(url, scale=10):
    """網址的 QR Code（PNG bytes）；居服員用手機相機掃描就能開打卡網頁。"""
    import segno
    buf = io.BytesIO()
    segno.make(url, error='m').save(buf, kind='png', scale=scale, border=2, dark='#123f3c')
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 缺少元件時：不讓畫面壞掉，並提供一鍵安裝（裝在執行 CareFlow 的同一個 Python）
# ---------------------------------------------------------------------------
REQUIRED = (('flask', 'flask>=3.0,<4'), ('waitress', 'waitress>=3.0,<4'), ('segno', 'segno>=1.5,<2'))


def missing_packages():
    import importlib.util
    return [spec for module, spec in REQUIRED if importlib.util.find_spec(module) is None]


def qr_available():
    return 'segno>=1.5,<2' not in missing_packages()


def install_missing(timeout=300):
    """回傳 (成功, 訊息)。用 pip 安裝 requirements.txt 裡打卡需要、但這台電腦還沒有的套件。"""
    import importlib
    import subprocess
    import sys
    packages = missing_packages()
    if not packages:
        return True, '打卡需要的元件都已安裝。'
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', *packages],
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f'安裝沒有完成：{exc}'
    importlib.invalidate_caches()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip().splitlines()[-3:]
        return False, '安裝失敗（可能是這台電腦連不上網路）：' + ' '.join(detail)
    still = missing_packages()
    if still:
        return False, '已下載，但要把 CareFlow 關掉重開一次才會生效。'
    return True, '已安裝：' + '、'.join(p.split('>')[0] for p in packages)


# ---------------------------------------------------------------------------
# 辦公室網路：電腦可能有好幾張網卡（Wi-Fi、乙太網路、VPN、WSL／Hyper-V／Docker 虛擬網卡），
# 要挑手機連得到的那一張。Windows 讀 ipconfig；其他系統退回 lan_ip()。
# ---------------------------------------------------------------------------
import re as _re

_VIRTUAL = ('vethernet', 'wsl', 'hyper-v', 'virtualbox', 'vmware', 'docker', 'loopback', 'bluetooth', '藍牙',
            'tap-', 'tailscale', 'zerotier', 'vpn', 'openvpn', 'wireguard', 'pseudo', 'teredo', 'isatap')
_IPV4 = _re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
CHOSEN_IP_KEY = 'lan_ip'


def parse_ipconfig(text):
    """把 Windows `ipconfig` 的輸出（中文或英文）整理成 [{名稱, IP, 有閘道, 虛擬網卡}]。"""
    adapters, current = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if not raw.startswith((' ', '\t')) and line.endswith(':'):
            name = line[:-1].strip()
            for word in ('介面卡', '網路卡', 'adapter'):
                if word in name:
                    name = name.split(word, 1)[1].strip()
            current = {'名稱': name, 'IP': '', '有閘道': False}
            adapters.append(current)
            continue
        if current is None:
            continue
        found = _IPV4.search(line)
        if 'IPv4' in line and found and not current['IP']:
            current['IP'] = found.group(1)
        elif ('預設閘道' in line or 'Default Gateway' in line) and found:
            current['有閘道'] = True
    out = []
    for a in adapters:
        if not a['IP'] or a['IP'].startswith(('127.', '169.254.')):
            continue
        a['虛擬網卡'] = any(word in a['名稱'].lower() for word in _VIRTUAL)
        out.append(a)
    # 真的網卡、有閘道（連得上路由器）、名稱像 Wi-Fi／乙太網路的排前面
    def rank(a):
        lower = a['名稱'].lower()
        physical = any(w in lower for w in ('wi-fi', 'wifi', 'wireless', '無線', 'wlan', '乙太網路', 'ethernet'))
        return (a['虛擬網卡'], not a['有閘道'], not physical)
    return sorted(out, key=rank)


def lan_candidates():
    """這台電腦可以給手機連的位址清單；第一個是最可能的。"""
    import platform
    import subprocess
    if platform.system() == 'Windows':
        try:
            raw = subprocess.run(['ipconfig'], capture_output=True, timeout=8).stdout
            for encoding in ('utf-8', 'cp950', 'mbcs'):
                try:
                    text = raw.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                text = raw.decode('utf-8', 'replace')
            found = parse_ipconfig(text)
            if found:
                return found
        except (OSError, subprocess.SubprocessError):
            pass
    ip = lan_ip()
    return [{'名稱': '這台電腦', 'IP': ip, '有閘道': True, '虛擬網卡': False}] if not ip.startswith('127.') else []


def office_url(path=None, candidates=None):
    """辦公室網路網址：督導選過的網卡優先，否則用清單第一個。"""
    candidates = lan_candidates() if candidates is None else candidates
    chosen = store.get_setting(CHOSEN_IP_KEY, path=path)
    ips = [c['IP'] for c in candidates]
    ip = chosen if chosen in ips else (ips[0] if ips else lan_ip())
    return f'http://{ip}:{PORT}'


# ---------------------------------------------------------------------------
# 外部網址（Cloudflare 免費臨時通道）：居服員在外面用行動網路也能打卡，而且是 https、手機會給定位。
# 不用設防火牆、不用管筆電連哪個 Wi-Fi。限制：這台電腦要開著且連網；重開後網址會變。
# ---------------------------------------------------------------------------
import platform as _platform
import shutil as _shutil
import subprocess as _subprocess

CLOUDFLARED_DOWNLOADS = {
    ('Windows', 'amd64'): 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe',
    ('Windows', 'x86'): 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-386.exe',
    ('Linux', 'amd64'): 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64',
    ('Linux', 'arm64'): 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64',
}
_TUNNEL_RE = _re.compile(r'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com')   # 排除錯誤訊息裡的 api.trycloudflare.com
_tunnel = globals().get('_tunnel') or {'proc': None, 'url': '', 'log': []}   # 模組重新載入時保留，不會弄丟開著的外部網址


def _arch():
    machine = _platform.machine().lower()
    if machine in ('amd64', 'x86_64'):
        return 'amd64'
    if machine in ('arm64', 'aarch64'):
        return 'arm64'
    return 'x86'


def cloudflared_target():
    name = 'cloudflared.exe' if _platform.system() == 'Windows' else 'cloudflared'
    return store.ROOT / 'output_results' / 'bin' / name


def cloudflared_path():
    """已經有的 cloudflared：專案內下載過的，或電腦本來就裝好的。"""
    target = cloudflared_target()
    if target.exists() and target.stat().st_size > 1_000_000:
        return target
    found = _shutil.which('cloudflared')
    return Path(found) if found else None


def download_cloudflared(timeout=180):
    """從 Cloudflare 官方 GitHub 下載 cloudflared（約 50MB）到 output_results/bin。"""
    url = CLOUDFLARED_DOWNLOADS.get((_platform.system(), _arch()))
    if not url:
        raise RuntimeError(f'這台電腦（{_platform.system()} {_arch()}）沒有可自動下載的版本，請資訊人員手動安裝 cloudflared。')
    target = cloudflared_target()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(partial, 'wb') as out:
        _shutil.copyfileobj(resp, out)
    if partial.stat().st_size < 1_000_000:
        raise RuntimeError('下載的檔案不完整，請確認網路後再試一次。')
    os.replace(partial, target)
    if _platform.system() != 'Windows':
        target.chmod(0o755)
    return target


def tunnel_url():
    """外部網址還開著就回傳網址，否則空字串。"""
    proc = _tunnel['proc']
    return _tunnel['url'] if proc is not None and proc.poll() is None and _tunnel['url'] else ''


def start_tunnel(port=PORT, wait=45):
    """啟動外部網址；回傳 (成功, 網址或錯誤說明)。已經開著就直接回傳原網址。"""
    if tunnel_url():
        return True, _tunnel['url']
    exe = cloudflared_path() or download_cloudflared()
    flags = getattr(_subprocess, 'CREATE_NO_WINDOW', 0)
    proc = _subprocess.Popen([str(exe), 'tunnel', '--no-autoupdate', '--url', f'http://localhost:{port}'],
                             stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
                             encoding='utf-8', errors='replace', creationflags=flags)
    _tunnel.update(proc=proc, url='', log=[])
    found = threading.Event()

    def read():
        for line in proc.stdout:
            _tunnel['log'] = (_tunnel['log'] + [line.rstrip()])[-40:]
            hit = _TUNNEL_RE.search(line)
            if hit and not _tunnel['url']:
                _tunnel['url'] = hit.group(0)
                found.set()
    threading.Thread(target=read, name='careflow-tunnel', daemon=True).start()
    found.wait(wait)
    if _tunnel['url']:
        return True, _tunnel['url']
    stop_tunnel()
    errors = [l for l in _tunnel['log'] if 'failed' in l.lower() or ' ERR ' in l]
    tail = (errors or _tunnel['log'])[-1][-200:] if _tunnel['log'] else ''
    return False, '沒有取得外部網址（可能是這台電腦的網路擋住 Cloudflare）。' + (f'訊息：{tail}' if tail else '')


def stop_tunnel():
    proc = _tunnel['proc']
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except _subprocess.TimeoutExpired:
            proc.kill()
    _tunnel.update(proc=None, url='')
