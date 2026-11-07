"""A restart reuses real-road durations; direction/version/TTL remain distinct."""
import sqlite3
import time
import caregiver_engine as engine
from persistent_cache import cache_path, read_legs, write_legs


def test_road_cache_restart_direction_and_version(tmp_path,monkeypatch):
    monkeypatch.setenv('CAREFLOW_CACHE_DIR',str(tmp_path))
    monkeypatch.setenv('CAREFLOW_OSRM_MAP_VERSION','test-map-1')
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    calls=[]
    monkeypatch.setattr(engine,'ensure_ready',lambda *a:None)
    def request(*args,**kwargs):
        calls.append(args);return {'code':'Ok','routes':[{'duration':300}]}
    monkeypatch.setattr(engine,'request_json',request)
    coordinates=(25.03,121.53,25.04,121.54)
    assert engine.get_osrm_travel_time(*coordinates)==5
    engine._OSRM_TRAVEL_TIME_CACHE.clear()
    assert engine.get_osrm_travel_time(*coordinates)==5 and len(calls)==1
    assert engine.get_osrm_travel_time(25.04,121.54,25.03,121.53)==5 and len(calls)==2
    monkeypatch.setenv('CAREFLOW_OSRM_MAP_VERSION','test-map-2')
    assert engine.get_osrm_travel_time(*coordinates)==5 and len(calls)==3
    engine._OSRM_TRAVEL_TIME_CACHE.clear()


def test_expired_sqlite_entry_is_a_cache_miss(tmp_path,monkeypatch):
    monkeypatch.setenv('CAREFLOW_CACHE_DIR',str(tmp_path))
    monkeypatch.setenv('CAREFLOW_ROAD_CACHE_DAYS','30')
    key=('endpoint','motorcycle','機車','map',0,25.03,121.53,25.04,121.54)
    write_legs({key:5})
    assert read_legs([key])=={key:5}
    with sqlite3.connect(cache_path()) as db:
        db.execute('UPDATE road_legs SET updated=?',(time.time()-31*86400,))
    assert read_legs([key])=={}
