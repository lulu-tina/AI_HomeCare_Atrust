"""歷史首選最佳化：替每個案家挑「整月排得進去、又不把人排爆」的固定居服員。

為什麼需要：固定單一居服員時，一位居服員接的所有案家會搶同一個人的時間。
若首選分配不均（有人接 8 個案家、有人 0 個），超載者的後段案家整批排不進去，
也會讓系統一直重試備案而變慢。本模組用與排班引擎相同的規則（可服務星期／證照／
請假、時段不重疊、轉場＋緩衝、連續工作 240 分／休息 30 分、每日工時上限、
月加班上限）在記憶體內快速模擬「某居服員接某一組案家」整月能排進幾筆，
再用局部搜尋把案家在居服員間搬移／交換，直到整體排得進的筆數最大。

模擬與引擎採同樣的先到先得順序（同一天依開始時間），最後仍以真正的排班引擎驗證。
"""
from __future__ import annotations

import math
import random
import time
from collections import defaultdict

import numpy as np
import pandas as pd


def _hm(text) -> int:
    text = str(text)
    return int(text[:2]) * 60 + int(text[3:5])


def day_error(blocks, cap_min, transfers, continuous_limit=240.0, rest=30.0):
    """與 schedule_checks.workload_error 同規則（分鐘版）。blocks 已依開始時間排序。"""
    total = 0.0
    for a, b, *_ in blocks:
        if b <= a:
            return 'bad_time'
        total += b - a
    total += sum(transfers)
    if total > cap_min + 1e-7:
        return 'hour_cap'
    continuous = 0.0
    for i, blk in enumerate(blocks):
        if i:
            gap = blk[0] - blocks[i - 1][1]
            if gap < 0:
                return 'overlap'
            t = transfers[i - 1]
            if gap - t >= rest:
                continuous = 0.0
            else:
                continuous += t
        continuous += blk[1] - blk[0]
        if continuous > continuous_limit + 1e-7:
            return 'rest'
    return None


class PrimaryModel:
    """把「任務 × 居服員」的靜態資格與「同人同日」的動態衝突整理成可快速重算的模型。"""

    def __init__(self, tasks: pd.DataFrame, caregivers: pd.DataFrame, config, travel,
                 travel_margin: float = 1.0):
        import caregiver_engine as e
        self.config = config
        self.margin = float(travel_margin)
        self.travel_fn = travel
        self.buffer = float(config.buffer_mins)
        self.workers = [str(w) for w in caregivers['居服員ID']]
        self.prof = {str(r['居服員ID']): r for r in caregivers.to_dict('records')}
        self.mode = {w: str(self.prof[w].get('常用交通工具', '機車')).strip() or '機車' for w in self.workers}
        self.cap = {w: min(float(self.prof[w].get('每日工時上限(小時)') or config.max_daily_hours),
                           config.max_daily_hours, 12.0) * 60 for w in self.workers}
        self.ot_base = {}
        for w in self.workers:
            try:
                self.ot_base[w] = max(0.0, float(self.prof[w].get('排班期外本月加班時數') or 0.0))
            except (TypeError, ValueError):
                self.ot_base[w] = 0.0

        self.visit = {}
        self.client_visits = defaultdict(list)
        records = tasks.sort_values(['日期', '時間窗_開始', '任務ID']).to_dict('records')
        for t in records:
            tid = str(t['任務ID'])
            self.visit[tid] = dict(
                tid=tid, client=str(t['案家ID']), date=str(t['日期'])[:10],
                s=_hm(t['時間窗_開始']), e=_hm(t['時間窗_結束']),
                lat=float(t['服務地點_緯度']), lon=float(t['服務地點_經度']))
            self.client_visits[str(t['案家ID'])].append(tid)
        # 靜態資格：以引擎同一個 _check_hard_constraints 判斷
        cg_series = {w: pd.Series(self.prof[w]) for w in self.workers}
        self.static_ok = {w: set() for w in self.workers}
        self.static_reason = {}
        cache = {}
        for t in records:
            tid = str(t['任務ID'])
            ts = None
            for w in self.workers:
                prof = self.prof[w]
                date_sensitive = bool(prof.get('請假紀錄')) or bool(prof.get('不排班日期')) \
                    or pd.notna(prof.get('到職日')) or pd.notna(prof.get('長照服務人員到期日'))
                key = (str(t['案家ID']), pd.Timestamp(t['日期']).isoweekday(), t['時間窗_開始'], t['時間窗_結束'],
                       w, str(t['日期']) if date_sensitive else '')
                if key not in cache:
                    if ts is None:
                        # 評估「任何人當首選」的資格，因此要移除任務上既有的首選欄位，避免觸發代班限制
                        ts = pd.Series({**t, '歷史首選居服員ID': '', '歷史首選': ''})
                    cache[key] = e._check_hard_constraints(ts, cg_series[w], config)
                reason = cache[key]
                if reason:
                    self.static_reason[(tid, w)] = reason
                else:
                    self.static_ok[w].add(tid)
        self._leg = {}
        self._day_memo = {}
        self._sim_memo = {}

    # ---- 交通 -----------------------------------------------------------------
    def leg(self, a, b, w):
        """前後兩案之間「車程＋緩衝」（同案家 0）。"""
        if a['client'] == b['client']:
            return 0.0
        key = (self.mode[w], round(a['lat'], 5), round(a['lon'], 5), round(b['lat'], 5), round(b['lon'], 5))
        if key not in self._leg:
            self._leg[key] = self.travel_fn(a['lat'], a['lon'], b['lat'], b['lon'], self.mode[w]) * self.margin + self.buffer
        return self._leg[key]

    # ---- 單日模擬 ---------------------------------------------------------------
    def _sim_day(self, w, vids):
        """vids 已依 (開始, 任務ID) 排序；回傳 (被接受的 tid 清單, 每次接受後的當日工時分鐘)。"""
        accepted, minutes_after = [], []
        for tid in vids:
            v = self.visit[tid]
            if tid not in self.static_ok[w]:
                continue
            trial = sorted(accepted + [tid], key=lambda x: (self.visit[x]['s'], x))
            blocks = [(self.visit[x]['s'], self.visit[x]['e']) for x in trial]
            transfers = [self.leg(self.visit[trial[i]], self.visit[trial[i + 1]], w) for i in range(len(trial) - 1)]
            if any(t > blocks[i + 1][0] - blocks[i][1] for i, t in enumerate(transfers)):
                continue
            if day_error(blocks, self.cap[w], transfers, self.config.continuous_work_limit_mins,
                         self.config.mandatory_break_mins):
                continue
            accepted = trial
            minutes_after.append(sum(b - a for a, b in blocks) + sum(transfers))
        return accepted, minutes_after

    def simulate(self, w, clients):
        """某居服員固定接 clients 的整月結果：{案家: 排進筆數}、被接受 tid 集合。"""
        key = (w, frozenset(clients))
        if key in self._sim_memo:
            return self._sim_memo[key]
        by_date = defaultdict(list)
        for c in clients:
            for tid in self.client_visits.get(c, ()):
                by_date[self.visit[tid]['date']].append(tid)
        normal = self.config.normal_daily_hours * 60
        limit = self.config.max_monthly_overtime_hours
        month_ot = defaultdict(float)
        accepted_all = set()
        for date in sorted(by_date):
            vids = sorted(by_date[date], key=lambda x: (self.visit[x]['s'], x))
            mkey = (w, tuple((self.visit[x]['client'], self.visit[x]['s'], self.visit[x]['e'], x in self.static_ok[w],
                              round(self.visit[x]['lat'], 5), round(self.visit[x]['lon'], 5)) for x in vids))
            if mkey not in self._day_memo:
                got, mins = self._sim_day(w, vids)
                self._day_memo[mkey] = ([vids.index(t) for t in got], mins)
            idx, minutes_after = self._day_memo[mkey]
            accepted = [vids[i] for i in idx]  # 記憶的是位置，不是任務ID（不同日期同型態會共用）
            month = date[:7]
            if accepted:
                ot = max(0.0, minutes_after[-1] - normal) / 60
                if self.ot_base[w] + month_ot[month] + ot > limit + 1e-7:
                    # 月加班上限卡住：逐筆放入、超過就略過（與引擎先到先得一致）
                    accepted, minutes_after = self._sim_day_with_ot(w, vids, month_ot[month], normal, limit)
                    ot = max(0.0, (minutes_after[-1] if minutes_after else 0) - normal) / 60
                month_ot[month] += ot
            accepted_all.update(accepted)
        per_client = defaultdict(int)
        for tid in accepted_all:
            per_client[self.visit[tid]['client']] += 1
        out = (dict(per_client), accepted_all)
        self._sim_memo[key] = out
        return out

    def _sim_day_with_ot(self, w, vids, used_ot, normal, limit):
        accepted, minutes_after = [], []
        for tid in vids:
            if tid not in self.static_ok[w]:
                continue
            a, m = self._sim_day(w, sorted(accepted + [tid], key=lambda x: (self.visit[x]['s'], x)))
            if len(a) != len(accepted) + 1:
                continue
            if self.ot_base[w] + used_ot + max(0.0, m[-1] - normal) / 60 > limit + 1e-7:
                continue
            accepted, minutes_after = a, m
        return accepted, minutes_after

    def served(self, w, clients):
        return sum(self.simulate(w, clients)[0].values())


def evaluate(model: PrimaryModel, owners: dict):
    """owners: 案家 -> 居服員。回傳 (排進筆數, 總筆數, 各案家排進筆數)。"""
    by_worker = defaultdict(set)
    for c, w in owners.items():
        if w:
            by_worker[w].add(c)
    per_client = {}
    for w, cs in by_worker.items():
        per_client.update(model.simulate(w, cs)[0])
    total = sum(len(model.client_visits.get(c, ())) for c in owners)
    return sum(per_client.values()), total, per_client


def optimize_primaries(model: PrimaryModel, current: dict, clients=None, seconds: float = 120.0,
                       keep_bonus: float = 3.0, balance_weight: float = 0.002, seed: int = 7,
                       protected=None, progress=None):
    """局部搜尋。回傳 (新首選 dict, 紀錄)。

    目標：排進筆數最大 → 其次盡量少動現有首選（keep_bonus 每個保留案家）→ 再平衡負荷。
    protected：不准更動的案家集合。
    """
    rnd = random.Random(seed)
    clients = [c for c in (clients or model.client_visits) if model.client_visits.get(c)]
    protected = set(protected or ())
    owners = {c: current.get(c) or None for c in clients}
    workers = model.workers
    members = defaultdict(set)
    for c, w in owners.items():
        if w:
            members[w].add(c)
    minutes = {c: sum(model.visit[t]['e'] - model.visit[t]['s'] for t in model.client_visits[c]) for c in clients}
    load = defaultdict(float)
    for w, cs in members.items():
        load[w] = sum(minutes[c] for c in cs)

    def worker_score(w, cs):
        return model.served(w, cs) if cs else 0

    served = {w: worker_score(w, members[w]) for w in workers}
    start_served = sum(served.values())
    changed = lambda c: bool(current.get(c)) and owners[c] != current.get(c)

    def penalty():
        # 變更懲罰 + 負荷平方（偏好均衡）
        p = sum(keep_bonus for c in clients if changed(c))
        p += balance_weight * sum((v / 60.0) ** 2 for v in load.values()) / 100.0
        return p

    deadline = time.time() + seconds
    history = [(0.0, start_served)]
    stale = 0
    sweeps = 0
    while time.time() < deadline and stale < 3:
        sweeps += 1
        improved = False
        per_client = {}
        for w in workers:
            per_client.update(model.simulate(w, members[w])[0])
        order = [c for c in clients if per_client.get(c, 0) < len(model.client_visits[c])]
        rnd.shuffle(order)
        order += [c for c in clients if c not in order and rnd.random() < 0.25]
        for c in order:
            if time.time() > deadline:
                break
            if c in protected:
                continue
            w0 = owners[c]
            cand = [w for w in workers if w != w0 and any(t in model.static_ok[w] for t in model.client_visits[c])]
            base_pen = penalty()
            base_total = sum(served.values())
            best = None
            rest0 = (members[w0] - {c}) if w0 else set()
            s0_after = worker_score(w0, rest0) if w0 else 0
            for w1 in cand:
                s1_after = worker_score(w1, members[w1] | {c})
                # 單純搬移
                gain = (s0_after + s1_after) - ((served[w0] if w0 else 0) + served[w1])
                owners[c] = w1
                if w0:
                    load[w0] -= minutes[c]
                load[w1] += minutes[c]
                score = gain - (penalty() - base_pen)
                owners[c] = w0
                if w0:
                    load[w0] += minutes[c]
                load[w1] -= minutes[c]
                if best is None or score > best[0]:
                    best = (score, 'move', w1, None, s0_after, s1_after)
                # 與 w1 的某案家互換（只在搬移仍不夠時）
                if gain <= 0 and w0 and len(members[w1]) > 0:
                    for d in list(members[w1]):
                        if d in protected:
                            continue
                        a_cs = (members[w0] - {c}) | {d}
                        b_cs = (members[w1] - {d}) | {c}
                        sa, sb = worker_score(w0, a_cs), worker_score(w1, b_cs)
                        g2 = (sa + sb) - (served[w0] + served[w1])
                        if g2 > 0:
                            owners[c], owners[d] = w1, w0
                            load[w0] += minutes[d] - minutes[c]
                            load[w1] += minutes[c] - minutes[d]
                            sc2 = g2 - (penalty() - base_pen)
                            owners[c], owners[d] = w0, w1
                            load[w0] -= minutes[d] - minutes[c]
                            load[w1] -= minutes[c] - minutes[d]
                            if sc2 > best[0]:
                                best = (sc2, 'swap', w1, d, sa, sb)
            if best and best[0] > 1e-9:
                score, kind, w1, d, sa, sb = best
                if kind == 'move':
                    if w0:
                        members[w0].discard(c)
                        load[w0] -= minutes[c]
                        served[w0] = s0_after
                    members[w1].add(c)
                    load[w1] += minutes[c]
                    served[w1] = sb if False else worker_score(w1, members[w1])
                    owners[c] = w1
                else:
                    members[w0].discard(c); members[w0].add(d)
                    members[w1].discard(d); members[w1].add(c)
                    load[w0] += minutes[d] - minutes[c]
                    load[w1] += minutes[c] - minutes[d]
                    owners[c], owners[d] = w1, w0
                    served[w0], served[w1] = sa, sb
                improved = True
        total_now = sum(served.values())
        history.append((time.time() - (deadline - seconds), total_now))
        if progress:
            progress(sweeps, total_now)
        stale = 0 if improved else stale + 1
    return owners, {'sweeps': sweeps, 'start_served': start_served, 'end_served': sum(served.values()),
                    'history': history}


def lns_optimize(model: PrimaryModel, current: dict, owners: dict | None = None, seconds: float = 120.0,
                 keep_bonus: float = 2.0, balance_weight: float = 0.002, seed: int = 11,
                 destroy=(4, 9), protected=None, progress=None):
    """大鄰域搜尋（拆掉幾個案家再依難度重排）：能跳出單純搬移的局部最佳。

    每輪：以「沒排滿的案家＋與其搶時間的案家」為主隨機拆 destroy 個案家 → 依難度（可接人數少、
    筆數多者優先）逐一放回「邊際排進筆數最大」的居服員 → 總排進筆數不降（含少量退火）才接受。
    """
    rnd = random.Random(seed)
    clients = [c for c in model.client_visits if model.client_visits[c]]
    protected = set(protected or ())
    workers = model.workers
    owners = dict(owners or {c: current.get(c) for c in clients})
    for c in clients:
        owners.setdefault(c, current.get(c))
    minutes = {c: sum(model.visit[t]['e'] - model.visit[t]['s'] for t in model.client_visits[c]) for c in clients}
    elig = {c: [w for w in workers if any(t in model.static_ok[w] for t in model.client_visits[c])] for c in clients}

    def members_of(own):
        m = defaultdict(set)
        for c, w in own.items():
            if w:
                m[w].add(c)
        return m

    def objective(own, mem):
        s = sum(model.served(w, cs) for w, cs in mem.items() if cs)
        chg = sum(1 for c in clients if current.get(c) and own.get(c) != current.get(c))
        load = defaultdict(float)
        for c, w in own.items():
            if w:
                load[w] += minutes[c]
        bal = balance_weight * sum((v / 60.0) ** 2 for v in load.values()) / 100.0
        return s - keep_bonus * chg - bal, s

    mem = members_of(owners)
    f, served = objective(owners, mem)
    best = (f, served, dict(owners))
    deadline = time.time() + seconds
    it = 0
    temp0 = 1.5
    while time.time() < deadline:
        it += 1
        temp = temp0 * max(0.0, (deadline - time.time()) / seconds)
        per = {}
        for w, cs in mem.items():
            per.update(model.simulate(w, cs)[0])
        weak = [c for c in clients if per.get(c, 0) < len(model.client_visits[c]) and c not in protected]
        pool = [c for c in clients if c not in protected]
        if not pool:
            break
        k = min(rnd.randint(*destroy), len(pool))
        picked = set()
        if weak and rnd.random() < 0.8:
            seed_c = rnd.choice(weak)
            picked.add(seed_c)
            # 同一位首選手上其他案家（會一起搶時間）＋隨機另一位居服員的案家
            w0 = owners.get(seed_c)
            mates = [c for c in mem.get(w0, ()) if c != seed_c and c not in protected]
            rnd.shuffle(mates)
            picked.update(mates[:max(1, k // 2)])
        while len(picked) < k:
            picked.add(rnd.choice(pool))
        new_owner = dict(owners)
        new_mem = defaultdict(set, {w: set(cs) for w, cs in mem.items()})
        for c in picked:
            w = new_owner.get(c)
            if w:
                new_mem[w].discard(c)
            new_owner[c] = None
        order = sorted(picked, key=lambda c: (len(elig[c]), -len(model.client_visits[c]), rnd.random()))
        for c in order:
            top = None
            for w in elig[c]:
                base = model.served(w, new_mem[w]) if new_mem[w] else 0
                gain = model.served(w, new_mem[w] | {c}) - base
                keep = keep_bonus if current.get(c) == w else 0.0
                lightly = -0.0005 * sum(minutes[x] for x in new_mem[w]) / 60.0
                key = (gain + keep + lightly + rnd.random() * 0.01, w)
                if top is None or key > top:
                    top = key
            if top:
                new_owner[c] = top[1]
                new_mem[top[1]].add(c)
        nf, ns = objective(new_owner, new_mem)
        if nf >= f - 1e-9 or rnd.random() < math.exp((nf - f) / max(temp, 1e-6)):
            owners, mem, f, served = new_owner, new_mem, nf, ns
            if f > best[0] + 1e-9:
                best = (f, served, dict(owners))
        if progress and it % 200 == 0:
            progress(it, best[1])
    return best[2], {'iterations': it, 'served': best[1], 'objective': best[0]}


def prune_changes(model: PrimaryModel, current: dict, owners: dict, protected=None):
    """把「改了但沒帶來任何好處」的案家改回原首選（排進筆數不降才還原），減少不必要的更動。"""
    owners = dict(owners)
    protected = set(protected or ())

    def total(own):
        by = defaultdict(set)
        for c, w in own.items():
            if w:
                by[w].add(c)
        return sum(model.served(w, cs) for w, cs in by.items())

    base = total(owners)
    changed = [c for c in owners if current.get(c) and owners[c] != current[c] and c not in protected]
    progress = True
    while progress:
        progress = False
        for c in list(changed):
            trial = dict(owners)
            trial[c] = current[c]
            got = total(trial)
            if got >= base:
                owners, base = trial, got
                changed.remove(c)
                progress = True
    return owners
