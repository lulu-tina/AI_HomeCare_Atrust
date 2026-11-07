"""機車路網批次查詢：雙向打包、同時送出、硬碟快取；每一組起訖都要拿到正確時間。"""
import random
import threading

import caregiver_engine as engine


def _fake_server(monkeypatch, calls):
    lock = threading.Lock()

    def request(host, path, params=None, **kwargs):
        coords = [tuple(map(float, c.split(','))) for c in path.split('/')[-1].split(';')]
        assert len(coords) <= engine.table_limit(host)
        src = [int(i) for i in params['sources'].split(';')] if 'sources' in params else range(len(coords))
        dst = [int(i) for i in params['destinations'].split(';')] if 'destinations' in params else range(len(coords))
        with lock:
            calls.append(len(coords))
        # 時間＝經緯度差 × 固定值（方向不同時間不同，才驗得出去回程沒弄反）
        return {'code': 'Ok', 'durations': [[abs(coords[i][0] - coords[j][0]) * 6000 + abs(coords[i][1] - coords[j][1]) * 3000
                                              + (60 if coords[i] < coords[j] else 0) for j in dst] for i in src]}
    monkeypatch.setattr(engine, 'request_json', request)
    monkeypatch.setattr(engine, 'ensure_ready', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_get_travel_provider', lambda: 'osrm')


def test_all_pairs_filled_with_fewer_requests_and_restart_uses_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('CAREFLOW_CACHE_DIR', str(tmp_path))
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    calls = []
    _fake_server(monkeypatch, calls)
    rnd = random.Random(1)
    places = [(round(25 + rnd.random() / 10, 6), round(121.4 + rnd.random() / 10, 6)) for _ in range(150)]
    homes = places[:5] + [(round(25 + rnd.random() / 10, 6), round(121.4 + rnd.random() / 10, 6)) for _ in range(25)]
    engine.prefetch_osrm_travel_times(homes, places)
    engine.prefetch_osrm_travel_times(places, places)
    limit = engine.table_limit(engine._osrm_endpoint()[0])
    old_style = (-(-30 // (limit // 2))) * (-(-150 // (limit // 2))) + (-(-150 // (limit // 2))) ** 2
    assert len(calls) < old_style * 0.75
    for o, d in [(o, d) for o in homes for d in places if o != d] + [(o, d) for o in places for d in places if o != d]:
        key = engine._osrm_cache_key(*o, *d, '機車')
        assert key in engine._OSRM_TRAVEL_TIME_CACHE
    # 方向正確：每一組都等於假路網「該方向」的時間（座標較小的一端出發多 1 分鐘）
    def expected(o, d):
        lo, ld = (o[1], o[0]), (d[1], d[0])
        return (abs(lo[0] - ld[0]) * 6000 + abs(lo[1] - ld[1]) * 3000 + (60 if lo < ld else 0)) / 60
    for o, d in [(o, d) for o in homes for d in places if o != d] + [(o, d) for o in places[:40] for d in places if o != d]:
        assert abs(engine._OSRM_TRAVEL_TIME_CACHE[engine._osrm_cache_key(*o, *d, '機車')] - expected(o, d)) < 1e-9
    a, b = places[0], places[1]
    ab = engine._OSRM_TRAVEL_TIME_CACHE[engine._osrm_cache_key(*a, *b, '機車')]
    # 重新啟動（記憶體清空）：直接讀硬碟快取，不再查路網
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    before = len(calls)
    engine.prefetch_osrm_travel_times(places, places)
    assert len(calls) == before
    assert engine._OSRM_TRAVEL_TIME_CACHE[engine._osrm_cache_key(*a, *b, '機車')] == ab
    engine._OSRM_TRAVEL_TIME_CACHE.clear()


def test_single_case_packs_both_directions(tmp_path, monkeypatch):
    monkeypatch.setenv('CAREFLOW_CACHE_DIR', str(tmp_path))
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    calls = []
    _fake_server(monkeypatch, calls)
    rnd = random.Random(2)
    places = [(round(25 + rnd.random() / 10, 6), round(121.4 + rnd.random() / 10, 6)) for _ in range(190)]
    mine = [places[7]]
    engine.prefetch_osrm_travel_times(mine, places)
    n = len(calls)
    engine.prefetch_osrm_travel_times(places, mine)      # 回程已在同一批請求拿到
    assert len(calls) == n
    limit = engine.table_limit(engine._osrm_endpoint()[0])
    assert n == -(-189 // (limit - 1))
    engine._OSRM_TRAVEL_TIME_CACHE.clear()


def test_parallel_requests_use_the_host_chosen_in_this_session(tmp_path, monkeypatch):
    """背景執行緒讀不到畫面設定時，也要用這位使用者選的主機（例如本機 Docker），不能退回預設的雲端主機。"""
    import osrm_client
    monkeypatch.setenv('CAREFLOW_CACHE_DIR', str(tmp_path))
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    main = threading.main_thread()
    monkeypatch.setattr(engine, 'osrm_endpoint',
                        lambda: 'http://127.0.0.1:5001' if threading.current_thread() is main else 'https://default.example')
    hosts, calls = [], []
    _fake_server(monkeypatch, calls)
    fake = engine.request_json
    monkeypatch.setattr(engine, 'request_json', lambda host, path, params=None, **k: hosts.append(host) or fake(host, path, params, **k))
    rnd = random.Random(5)
    places = [(round(25 + rnd.random() / 10, 6), round(121.4 + rnd.random() / 10, 6)) for _ in range(80)]
    engine.prefetch_osrm_travel_times(places, places)
    assert len(hosts) > 1 and set(hosts) == {'http://127.0.0.1:5001'}
    key = engine._osrm_cache_key(*places[0], *places[1], '機車')
    assert key[0] == 'http://127.0.0.1:5001' and key in engine._OSRM_TRAVEL_TIME_CACHE
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
