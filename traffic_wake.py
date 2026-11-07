"""交通主機喚醒：在背景叫醒 Render 機車路網（免費方案閒置約 15 分鐘會休眠），畫面不用卡住等。

狀態依主機網址記錄（同一台 Streamlit 主機上的使用者共用，因為叫醒的是同一台路網主機）。
只送台北 101 附近的公開街道座標，不含任何個案或居服員資料。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import threading
import time

import streamlit as st

import osrm_client

AWAKE_MINUTES = 10          # 叫醒後這段時間內視為可用（Render 約閒置 15 分鐘才休眠）
WAKE_DEADLINE = 300         # 免費主機冷啟動＋載入路網有時要 2～4 分鐘，持續嘗試到 5 分鐘才算叫不醒
RETRY_PAUSE = 5
_state = {}
_lock = threading.Lock()


def _now():
    return time.time()


def status(host):
    """{'status': idle|waking|ready|failed|local, 'elapsed', 'since', 'message'}"""
    if not osrm_client.is_remote(host):
        return {'status': 'local', 'message': '本機路網，不需要喚醒'}
    with _lock:
        item = dict(_state.get(host) or {'status': 'idle'})
    if item['status'] == 'waking':
        item['elapsed'] = int(_now() - item['started'])
    if item['status'] == 'ready':
        item['since'] = int((_now() - item['finished']) // 60)
        if item['since'] >= AWAKE_MINUTES:
            item['status'] = 'stale'
    return item


def is_ready(host):
    return status(host)['status'] in ('ready', 'local')


def start(host):
    """在背景開始喚醒；已在喚醒中就不重複。回傳 True 表示這次有開始。"""
    if not osrm_client.is_remote(host):
        return False
    with _lock:
        if (_state.get(host) or {}).get('status') == 'waking':
            return False
        _state[host] = {'status': 'waking', 'started': _now(), 'attempt': 1}

    def work():
        # 一次逾時不代表叫不醒：Render 收到第一個請求才開始開機，開機期間會逾時或回 502/503。
        # 持續重試到 WAKE_DEADLINE，主機一開好就會成功。
        started = _now()
        attempt = 0
        while True:
            attempt += 1
            with _lock:
                if host in _state and _state[host].get('status') == 'waking':
                    _state[host]['attempt'] = attempt
            try:
                result = osrm_client.ensure_ready(host, force=True)
                item = {'status': 'ready', 'finished': _now(),
                        'message': f"回應 {result.get('檢查耗時(秒)', 0)} 秒，共等 {int(_now() - started)} 秒"}
                break
            except Exception as exc:  # noqa: BLE001
                if _now() - started + RETRY_PAUSE >= WAKE_DEADLINE:
                    item = {'status': 'failed', 'finished': _now(),
                            'message': f'等了 {int(_now() - started)} 秒、試了 {attempt} 次仍沒有回應（{str(exc)[:120]}）'}
                    break
                time.sleep(RETRY_PAUSE)
        with _lock:
            _state[host] = item

    threading.Thread(target=work, name='careflow-osrm-wake', daemon=True).start()
    return True


def friendly_error(message):
    """把交通主機的技術錯誤換成居督看得懂的說明；不是交通主機問題時回傳 None。"""
    text = str(message)
    if 'OSRM' not in text and '機車路網' not in text:
        return None
    return ('交通主機目前沒有回應，所以這次沒有排班（系統不會用直線距離亂估）。'
            '請按下方「🔔 喚醒交通主機」，等顯示「✅ 可以排班」後再按一次「開始排班」。'
            '如果等超過 5 分鐘仍叫不醒，請通知工程師檢查 Render 服務是否暫停。')


def panel(host, key, compact=False):
    """喚醒面板：狀態燈＋按鈕；喚醒中每 3 秒自動更新，不會卡住其他操作。"""
    waking = status(host)['status'] == 'waking'

    def body():
        info = status(host)
        state = info['status']
        if waking and state != 'waking':
            st.rerun()          # 喚醒結束：整頁更新一次，停止每 3 秒的自動刷新
        if state == 'local':
            st.caption('🛵 交通主機：本機路網，不需喚醒')
            return
        label = {'idle': '⚪ 尚未確認，排班前建議先喚醒',
                 'stale': f"🟡 {info.get('since', 0)} 分鐘前確認可用，可能又休眠了",
                 'waking': f"⏳ 主機開機中…已等 {info.get('elapsed', 0)} 秒（第 {info.get('attempt', 1)} 次嘗試；免費主機最久約 3～4 分鐘）",
                 'ready': f"✅ 可以排班（{info.get('since', 0)} 分鐘前確認，{info.get('message', '')}）",
                 'failed': '❌ 叫不醒：' + (info.get('message') or '')[:80]}[state]
        if compact:
            st.caption('🛵 交通主機　' + label)
        else:
            box = {'ready': st.success, 'failed': st.error, 'waking': st.info}.get(state, st.warning)
            box('🛵 交通主機（機車路網）：' + label)
        if state != 'waking':
            text = '🔔 喚醒交通主機' if state != 'ready' else '🔄 再確認一次'
            if st.button(text, key=f'osrm_wake_{key}', width='stretch',
                         type='primary' if (state in ('idle', 'stale', 'failed') and not compact) else 'secondary'):
                start(host)
                st.rerun()          # 整頁更新，讓面板改成每 3 秒自動刷新
        if state == 'failed' and not compact:
            st.caption('已連續嘗試 5 分鐘。可能原因：Render 服務被暫停、部署失敗或當月免費時數用完。可再按一次；仍不行請通知工程師檢查 Render 後台。')

    try:
        st.fragment(run_every=3 if waking else None)(body)()
    except Exception:
        body()
