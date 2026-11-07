"""Local, versioned road-leg cache; no transit dates or identifiers are stored.

Cached durations are directional, expire, and are invalidated by map version or
endpoint changes. This database is runtime data and is excluded from releases.
"""
from pathlib import Path
import json
import os
import sqlite3
import time


def cache_path():
    folder = Path(os.getenv('CAREFLOW_CACHE_DIR',str(Path(__file__).parent/'output_results'/'cache')))
    folder.mkdir(parents=True,exist_ok=True)
    return folder/'road_legs.sqlite3'


def read_legs(keys):
    encoded = {json.dumps(key,separators=(',',':'),ensure_ascii=True):key for key in keys}
    if not encoded:
        return {}
    result = {}
    try:
        with sqlite3.connect(cache_path(),timeout=3) as db:
            db.execute('CREATE TABLE IF NOT EXISTS road_legs (key TEXT PRIMARY KEY, minutes REAL NOT NULL, updated REAL NOT NULL)')
            items = list(encoded)
            cutoff = time.time()-max(1,int(os.getenv('CAREFLOW_ROAD_CACHE_DAYS','30')))*86400
            for i in range(0,len(items),400):
                batch = items[i:i+400]
                query = 'SELECT key,minutes FROM road_legs WHERE updated>=? AND key IN ('+','.join('?' for _ in batch)+')'
                for key,minutes in db.execute(query,[cutoff,*batch]):
                    result[encoded[key]] = float(minutes)
    except (OSError,sqlite3.Error,ValueError):
        # Cache failure only means a fresh real-road request; it never supplies
        # a guessed route or makes a missing duration appear valid.
        return {}
    return result


def write_legs(values):
    if not values:
        return
    try:
        now = time.time()
        with sqlite3.connect(cache_path(),timeout=3) as db:
            db.execute('CREATE TABLE IF NOT EXISTS road_legs (key TEXT PRIMARY KEY, minutes REAL NOT NULL, updated REAL NOT NULL)')
            db.executemany('INSERT OR REPLACE INTO road_legs VALUES(?,?,?)',
                [(json.dumps(key,separators=(',',':'),ensure_ascii=True),float(minutes),now) for key,minutes in values.items()])
    except (OSError,sqlite3.Error,ValueError):
        pass

