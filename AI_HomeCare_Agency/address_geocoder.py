"""Offline Taiwan doorplate geocoder (CAREFLOW_BUILD = '2026.10.11-v18').

Nothing here talks to the network.  The only data source is the government open
data file「新北市門牌位置數值資料.csv」, compiled once into a compact index
(`data/ntpc_doorplate_index.npz` + `data/ntpc_prefix_index.json.gz`) that ships
with the app, so a hosted deployment never needs the 190 MB CSV.

Lookup levels, best first:
  exact   – the full doorplate (road + lane + alley + number) is in the index
  lane    – house number is masked (O號) but road + lane/alley is known
  village – masked number, road known, 里 known → section of the road inside that 里
  road    – masked number, only the road is known (error radius reported)

Every result carries `radius_m`: the distance that contains ~80 % of the doorplates
under that prefix, i.e. how far the true door can plausibly be from the returned point.
"""
from __future__ import annotations

CAREFLOW_BUILD = '2026.10.11-v18'

import gzip
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parent / 'data'
INDEX_NPZ = DATA_DIR / 'ntpc_doorplate_index.npz'
PREFIX_JSON = DATA_DIR / 'ntpc_prefix_index.json.gz'

AREA_CODES = {
    '65000010': '板橋區', '65000020': '三重區', '65000030': '中和區', '65000040': '永和區',
    '65000050': '新莊區', '65000060': '新店區', '65000070': '樹林區', '65000080': '鶯歌區',
    '65000090': '三峽區', '65000100': '淡水區', '65000110': '汐止區', '65000120': '瑞芳區',
    '65000130': '土城區', '65000140': '蘆洲區', '65000150': '五股區', '65000160': '泰山區',
    '65000170': '林口區', '65000180': '深坑區', '65000190': '石碇區', '65000200': '坪林區',
    '65000210': '三芝區', '65000220': '石門區', '65000230': '八里區', '65000240': '平溪區',
    '65000250': '雙溪區', '65000260': '貢寮區', '65000270': '金山區', '65000280': '萬里區',
    '65000290': '烏來區',
}
MASK_CHARS = 'OoＯｏ○〇◯XxＸｘ＊*某'
_ZH_DIGIT = {'零': 0, '〇': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _zh_to_int(text: str) -> int | None:
    """一→1、十→10、十二→12、二十→20、二十三→23。只處理 1–99。"""
    if not text or any(ch not in _ZH_DIGIT and ch != '十' for ch in text):
        return None
    if '十' not in text:
        return int(''.join(str(_ZH_DIGIT[c]) for c in text)) if len(text) == 1 else None
    head, _, tail = text.partition('十')
    tens = _ZH_DIGIT[head] if head else 1
    ones = _ZH_DIGIT[tail] if tail else 0
    return tens * 10 + ones


def normalize(value) -> str:
    """NFKC（全形→半形）、去空白、臺→台、中文段數→阿拉伯數字。"""
    if value is None:
        return ''
    s = unicodedata.normalize('NFKC', str(value))
    s = re.sub(r'\s+', '', s).replace('臺', '台')
    s = re.sub(r'([一二三四五六七八九十]{1,3})段',
               lambda m: f'{_zh_to_int(m.group(1)) or m.group(1)}段', s)
    return s


def clean_address(value) -> str:
    """去掉郵遞區號、里／鄰、樓層，保留到「…號」；X-Y號 → X之Y號。"""
    s = normalize(value)
    s = re.sub(r'^\d{3,6}(?=(台|新北|桃園|新竹|苗栗|彰化|南投|雲林|嘉義|高雄|屏東|宜蘭|花蓮|澎湖|金門|連江|基隆))', '', s)
    s = re.sub(r'(\d+)-(\d+)號', r'\1之\2號', s)
    m = re.match(r'^(.*?\d+(?:之\d+)?號)', s)
    return m.group(1) if m else s


def _strip_village(s: str) -> tuple[str, str]:
    """（地址去掉里／鄰，里名）。例：中和區景福里和平街 → (中和區和平街, 景福里)。"""
    village = ''
    m = re.search(r'(區|鄉|鎮|市)([^區鄉鎮市路街巷弄大道段]{1,6}[里村])(?=[^里村])', s)
    if m:
        village = m.group(2)
        s = s[:m.start(2)] + s[m.end(2):]
    s = re.sub(r'\d{1,3}鄰', '', s)
    return s, village


def _key_hash(address: str) -> int:
    return int.from_bytes(hashlib.blake2b(address.encode('utf-8'), digest_size=8).digest(), 'big')


# ---------------------------------------------------------------- coordinates

def twd97_to_wgs84(x, y):
    """EPSG:3826 (TWD97 / TM2 zone 121) → WGS84，向量化。回傳 (lon, lat)。"""
    x = np.asarray(x, dtype='float64')
    y = np.asarray(y, dtype='float64')
    a, b = 6378137.0, 6356752.314245
    k0, lon0, dx = 0.9999, math.radians(121.0), 250000.0
    e2 = 1 - (b * b) / (a * a)
    ep2 = e2 / (1 - e2)
    xx = x - dx
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    fp = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * np.sin(6 * mu) + (1097 * e1 ** 4 / 512) * np.sin(8 * mu))
    c1 = ep2 * np.cos(fp) ** 2
    t1 = np.tan(fp) ** 2
    r1 = a * (1 - e2) / (1 - e2 * np.sin(fp) ** 2) ** 1.5
    n1 = a / np.sqrt(1 - e2 * np.sin(fp) ** 2)
    d = xx / (n1 * k0)
    lat = fp - (n1 * np.tan(fp) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                          + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / np.cos(fp)
    return np.degrees(lon), np.degrees(lat)


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dl = p2 - p1, np.radians(lon2) - np.radians(lon1)
    h = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(h))


# ---------------------------------------------------------------- index build

def build_index(csv_path, out_dir=DATA_DIR, chunksize=400_000, progress=print):
    """把官方門牌 CSV 壓成兩個小檔。約 1–3 分鐘、1–2 GB 記憶體。"""
    import pandas as pd
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    total = 0
    for chunk in pd.read_csv(csv_path, dtype=str, encoding='utf-8-sig', chunksize=chunksize, keep_default_na=False):
        chunk.columns = [c.strip().lower() for c in chunk.columns]
        road_col = next(c for c in chunk.columns if c.startswith('street'))
        district = chunk['areacode'].map(AREA_CODES).fillna('')
        road = ('新北市' + district + chunk[road_col]).map(normalize)
        lane = (chunk['area'] + chunk['lane']).map(normalize)
        alley = chunk['alley'].map(normalize)
        full = (road + lane + alley + chunk['number'].map(normalize)).map(clean_address)
        lon, lat = twd97_to_wgs84(pd.to_numeric(chunk['x_3826'], errors='coerce'), pd.to_numeric(chunk['y_3826'], errors='coerce'))
        frames.append(pd.DataFrame({'a': full, 'lat': lat, 'lon': lon, 'road': road, 'lane': lane, 'alley': alley,
                                    'village': chunk['village'].map(normalize)}))
        total += len(chunk)
        progress(f'  讀入 {total:,} 筆')
    df = pd.concat(frames, ignore_index=True)
    df = df[df['lat'].between(21, 26) & df['lon'].between(119, 123) & df['a'].str.endswith('號')].reset_index(drop=True)
    progress(f'有效門牌 {len(df):,} 筆，建立精確比對表…')

    df['h'] = np.fromiter((_key_hash(a) for a in df['a']), dtype='uint64', count=len(df))
    agg = df.groupby('h', sort=True).agg(lat=('lat', 'median'), lon=('lon', 'median'), n=('lat', 'size'),
                                         lat_min=('lat', 'min'), lat_max=('lat', 'max'),
                                         lon_min=('lon', 'min'), lon_max=('lon', 'max'))
    spread = haversine_m(agg['lat_min'].values, agg['lon_min'].values, agg['lat_max'].values, agg['lon_max'].values)
    # 同一個地址字串落在不同里、位置相距甚遠時，另外存「地址@里」版本，使用者有填里名就能分辨。
    ambiguous = agg.index[spread > 150]
    extra = df[df['h'].isin(ambiguous) & (df['village'] != '')]
    extra_agg = extra.groupby(['a', 'village'], sort=False).agg(lat=('lat', 'median'), lon=('lon', 'median'),
                                                                  lat_min=('lat', 'min'), lat_max=('lat', 'max'),
                                                                  lon_min=('lon', 'min'), lon_max=('lon', 'max')).reset_index()
    extra_h = np.fromiter((_key_hash(f'{a}@{v}') for a, v in zip(extra_agg['a'], extra_agg['village'])), dtype='uint64', count=len(extra_agg))
    extra_spread = haversine_m(extra_agg['lat_min'].values, extra_agg['lon_min'].values, extra_agg['lat_max'].values, extra_agg['lon_max'].values)
    all_h = np.concatenate([agg.index.values.astype('uint64'), extra_h])
    all_lat = np.concatenate([agg['lat'].values, extra_agg['lat'].values])
    all_lon = np.concatenate([agg['lon'].values, extra_agg['lon'].values])
    all_spread = np.concatenate([spread, extra_spread])
    order = np.argsort(all_h, kind='stable')
    np.savez_compressed(
        out_dir / 'ntpc_doorplate_index.npz',
        h=all_h[order],
        lat=np.round(all_lat[order] * 1e7).astype('int32'),
        lon=np.round(all_lon[order] * 1e7).astype('int32'),
        spread=np.minimum(all_spread[order], 65000).astype('uint16'),
    )
    progress(f'  精確表 {len(agg):,} 個不同門牌；其中 {len(ambiguous):,} 個地址字串對應到相距 150 公尺以上的多點（另存 {len(extra_h):,} 筆「地址@里」）')

    progress('建立路／巷／弄／里 前綴表…')
    prefixes: dict[str, list] = {}
    main = (df['lane'] == '') & (df['alley'] == '')

    def add(frame, key, tag):
        frame = frame.assign(k=key)
        frame = frame[frame['k'] != '']
        g = frame.groupby('k')
        med = g[['lat', 'lon']].median()
        joined = frame[['lat', 'lon', 'k']].join(med, on='k', rsuffix='_c')
        frame = frame.assign(d=haversine_m(joined['lat'].values, joined['lon'].values,
                                           joined['lat_c'].values, joined['lon_c'].values))
        p80 = frame.groupby('k')['d'].quantile(0.8)
        counts = g.size()
        for k, row in med.iterrows():
            prefixes[f'{tag}|{k}'] = [int(counts[k]), round(float(row['lat']), 6), round(float(row['lon']), 6), int(round(p80[k]))]

    add(df, df['road'], 'R')                                                     # 整條路（含巷弄）
    add(df[main], df.loc[main, 'road'], 'M')                                     # 主線門牌
    add(df[main & (df['village'] != '')], df.loc[main & (df['village'] != ''), 'road'] + '@' + df.loc[main & (df['village'] != ''), 'village'], 'V')
    lane_rows = df['lane'] != ''
    add(df[lane_rows], df.loc[lane_rows, 'road'] + df.loc[lane_rows, 'lane'], 'L')
    alley_rows = df['alley'] != ''
    add(df[alley_rows], df.loc[alley_rows, 'road'] + df.loc[alley_rows, 'lane'] + df.loc[alley_rows, 'alley'], 'A')
    with gzip.open(out_dir / 'ntpc_prefix_index.json.gz', 'wt', encoding='utf-8') as f:
        json.dump(prefixes, f, ensure_ascii=False, separators=(',', ':'))
    progress(f'  前綴表 {len(prefixes):,} 筆。完成。')
    return {'doorplates': int(len(agg)), 'prefixes': len(prefixes)}


# ---------------------------------------------------------------- lookup

@dataclass
class GeoResult:
    status: str                       # 精確門牌 / 巷弄級近似 / 里內路段近似 / 路段級近似 / 找不到 / 需人工確認 / 索引不存在 / 空白地址
    lat: float | None = None
    lon: float | None = None
    radius_m: int = 0                 # 0 = 門牌級；>0 = 約略定位的可能誤差
    matched: str = ''
    level: str = ''                   # exact / alley / lane / village / road
    note: str = ''
    approximate: bool = False         # True → 提醒居督：只是約略位置
    village: str = ''

    @property
    def ok(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_MASK_CLASS = re.escape(MASK_CHARS)
_ROAD_RE = re.compile(r'^(新北市[^市區]{1,3}區.*?(?:路|街|大道)(?:\d+段)?)(.*)$')


def is_masked(address: str) -> bool:
    """門牌號被遮蔽（O號、X號、○號、*號）或根本沒有門牌號。"""
    s = unicodedata.normalize('NFKC', str(address or ''))
    if re.search(rf'[{_MASK_CLASS}]+(?:之[\dOX]+)?\s*號', s):
        return True
    return not re.search(r'\d+(?:之\d+)?\s*號', s)


class Geocoder:
    def __init__(self, npz_path=INDEX_NPZ, prefix_path=PREFIX_JSON):
        self.npz_path, self.prefix_path = Path(npz_path), Path(prefix_path)
        self._h = self._lat = self._lon = self._spread = None
        self._prefix: dict[str, list] = {}
        self.error = ''
        self.load()

    @property
    def ready(self) -> bool:
        return self._h is not None and bool(self._prefix)

    def load(self):
        try:
            data = np.load(self.npz_path)
            self._h, self._lat, self._lon, self._spread = data['h'], data['lat'], data['lon'], data['spread']
            with gzip.open(self.prefix_path, 'rt', encoding='utf-8') as f:
                self._prefix = json.load(f)
        except (OSError, KeyError, ValueError) as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self._h = None

    def stats(self) -> dict:
        return {'doorplates': 0 if self._h is None else int(len(self._h)), 'prefixes': len(self._prefix)}

    def _exact(self, address: str):
        key = np.uint64(_key_hash(address))
        i = int(np.searchsorted(self._h, key))
        if i < len(self._h) and self._h[i] == key:
            return self._lat[i] / 1e7, self._lon[i] / 1e7, int(self._spread[i])
        return None

    def _prefix_hit(self, tag, key):
        row = self._prefix.get(f'{tag}|{key}')
        return row

    def lookup(self, raw_address: str) -> GeoResult:
        raw = str(raw_address or '').strip()
        if not raw:
            return GeoResult('空白地址')
        if not self.ready:
            return GeoResult('索引不存在', note=f'找不到門牌索引（{self.npz_path.name}），請先執行 build_geo_index.py。{self.error}')
        masked = is_masked(raw)
        s = normalize(raw)
        if not s.startswith('新北市'):
            if '新北' in s[:4]:
                s = '新北市' + s[3:]
            elif re.match(r'^[^市縣]{0,3}[市縣]', s):
                return GeoResult('找不到', note='目前內建的是新北市門牌資料；其他縣市請手動輸入經緯度。')
            else:
                s = '新北市' + s
        s, village = _strip_village(s)

        note = ''
        if not masked:
            addr = clean_address(s)
            hit = (self._exact(f'{addr}@{village}') if village else None) or self._exact(addr)
            if hit:
                lat, lon, spread = hit
                if spread > 150:
                    return GeoResult('需人工確認', lat, lon, radius_m=spread, matched=addr, level='exact', approximate=True, village=village,
                                     note=f'此地址在官方資料中對應到相距約 {spread} 公尺的多個位置，已取中間點，請人工確認。')
                return GeoResult('精確門牌', lat, lon, 0, addr, 'exact', village=village)
            s = re.sub(r'\d+(?:之\d+)?號.*$', '', addr)
            note = '官方資料查無這個門牌號，改以同一路段約略定位。'
        else:
            s = re.sub(rf'[{_MASK_CLASS}\d之\-]*號.*$', '', s) if '號' in s else re.sub(rf'[{_MASK_CLASS}]+$', '', s)
            note = '門牌號已遮蔽，以所在路段約略定位。'

        m = _ROAD_RE.match(s)
        if not m:
            return GeoResult('找不到', note='地址裡找不到「路／街／大道」，無法定位。', village=village)
        road, rest = m.group(1), m.group(2)
        lane_m = re.match(r'^([^弄號]*?巷)', rest)
        lane = lane_m.group(1) if lane_m else ''
        rest2 = rest[len(lane):]
        alley_m = re.match(r'^([^號巷]*?弄)', rest2)
        alley = alley_m.group(1) if alley_m else ''

        order = []
        if alley:
            order.append(('A', road + lane + alley, '巷弄級近似', 'alley'))
        if lane:
            order.append(('L', road + lane, '巷弄級近似', 'lane'))
        if not lane and not alley:
            if village:
                order.append(('V', f'{road}@{village}', '里內路段近似', 'village'))
            order.append(('M', road, '路段級近似', 'road'))
        order.append(('R', road, '路段級近似', 'road'))
        for tag, key, label, level in order:
            row = self._prefix_hit(tag, key)
            if row:
                n, lat, lon, p80 = row
                radius = max(int(p80), 40)
                return GeoResult(label, lat, lon, radius_m=radius, matched=f'{key.replace("@", "・")}（{n} 個門牌點）', level=level,
                                 approximate=True, village=village,
                                 note=f'{note}實際位置約在此點 {radius} 公尺範圍內。')
        return GeoResult('找不到', note=f'官方資料中找不到「{road}」。', village=village)


_SHARED: Geocoder | None = None


def get_geocoder() -> Geocoder:
    global _SHARED
    if _SHARED is None or (not _SHARED.ready and INDEX_NPZ.exists()):
        _SHARED = Geocoder()
    return _SHARED
