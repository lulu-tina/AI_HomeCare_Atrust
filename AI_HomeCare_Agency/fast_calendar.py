"""A compact route calendar using the shared eligibility and workload rules.

Only road-model routes are reused across dates. Transit is keyed by the exact
date and clock. A cached route never bypasses eligibility or monthly overtime.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import math

import pandas as pd

from agency_rules import clean, finite, leave_overlap
from dispatch_workflow import blocked_reason
from pending_clients import pattern_token
from schedule_checks import workload_error


class FastCalendar:
    def __init__(self, tasks, caregivers, config):
        import caregiver_engine as engine
        self.engine, self.config = engine, config
        self.tasks = {str(t['任務ID']):dict(t) for t in tasks.to_dict('records')}
        self.people = {str(p['居服員ID']):dict(p) for p in caregivers.to_dict('records')}
        self.profile_rows = {w:pd.Series(p) for w,p in self.people.items()}
        self.tokens = {tid:pattern_token(t) for tid,t in self.tasks.items()}
        self.eligibility_keys = {}
        for tid,task in self.tasks.items():
            requirements = set(clean(task.get('服務碼必要證照')).split('、'))-{''}
            from care_policy import DEFAULT_CODE_CERTS
            requirements.update(DEFAULT_CODE_CERTS[c] for c,u in engine.iter_service_codes(task) if u>0 and c in DEFAULT_CODE_CERTS)
            fields = ('時間窗_開始','時間窗_結束','服務歷時(分鐘)','指定居服員性別',
                      '是否失智','失智症個案(0/1)','特殊照護需求','需重度移位協助(0/1)',
                      '案家環境特徵','任務資料異常')
            # Source row, client ID and billable units don't alter eligibility.
            self.eligibility_keys[tid] = (pd.Timestamp(task['日期']).isoweekday(),
                tuple((k,clean(task.get(k))) for k in fields),tuple(sorted(requirements)),
                (math.isfinite(finite(task.get('服務地點_緯度'))),math.isfinite(finite(task.get('服務地點_經度'))),
                 20<=finite(task.get('服務地點_緯度'))<=27.5,118<=finite(task.get('服務地點_經度'))<=123.5),
                str(task.get('案家ID')) if config.continuity_policy=='strict' else '')
        self.clocks = {}
        for task in self.tasks.values():
            for key in ('時間窗_開始','時間窗_結束'):
                value = str(task[key])
                self.clocks.setdefault(value, datetime.strptime(value,'%H:%M'))
        self.busy = engine._build_cg_busy_blocks(caregivers)
        self.date_limits = {}
        for worker,person in self.people.items():
            dates = [pd.to_datetime(person.get(k),errors='coerce') for k in ('到職日','長照服務人員到期日')]
            self.date_limits[worker] = tuple(d.date().isoformat() if pd.notna(d) else '' for d in dates)
        self.routes, self.minutes, self.month_totals = {}, {}, defaultdict(float)
        self.hard_cache, self.route_cache, self.leg_cache = {}, {}, {}
        self.state_cache = {}
        self.route_checks = self.route_hits = self.candidate_expansions = 0
        config.primary_profiles = self.people

    def clone(self):
        other = object.__new__(type(self))
        other.__dict__ = self.__dict__.copy()
        other.routes = {k:list(v) for k,v in self.routes.items()}
        other.minutes = dict(self.minutes)
        other.month_totals = defaultdict(float,self.month_totals)
        return other

    def hard(self, tid, worker):
        task,person = self.tasks[tid],self.people[worker]
        day = task['日期']
        joined,expiry = self.date_limits[worker]
        state_key = (worker,day,task['時間窗_開始'],task['時間窗_結束'])
        if state_key not in self.state_cache:
            self.state_cache[state_key] = (bool(joined and day<joined),bool(expiry and day>expiry),
                day in clean(person.get('請假或不排班日期')).split(','),
                leave_overlap(task,person),blocked_reason(task,person) or '')
        state = self.state_cache[state_key]
        key = self.eligibility_keys[tid],worker,state
        if key not in self.hard_cache:
            self.hard_cache[key] = self.engine._check_hard_constraints(
                task,person,self.config,None,
                clean(task.get('歷史首選居服員ID'))==worker)
        return self.hard_cache[key]

    def leg(self, worker, day, first, second=None):
        person = self.people[worker]
        mode = person.get('常用交通工具','機車')
        if second is None:
            target = self.tasks[first]
            coordinates = (person['服務起點_緯度(家)'],person['服務起點_經度(家)'],
                           target['服務地點_緯度'],target['服務地點_經度'])
            clock = target['時間窗_開始']
            kind = 'arrival'
        else:
            left,right = self.tasks[first],self.tasks[second]
            if str(left['案家ID'])==str(right['案家ID']):
                return 0.0
            coordinates = (left['服務地點_緯度'],left['服務地點_經度'],
                           right['服務地點_緯度'],right['服務地點_經度'])
            clock = (self.clocks[left['時間窗_結束']]+timedelta(minutes=self.config.buffer_mins)).strftime('%H:%M:%S')
            kind = 'departure'
        key = (mode,coordinates,day,clock,kind) if mode=='大眾運輸' else (mode,coordinates)
        if key not in self.leg_cache:
            at = pd.Timestamp(f'{day} {clock}')
            self.leg_cache[key] = self.engine.calc_travel_minutes(*coordinates,self.config,
                transport_mode=mode,**{kind+'_time':at})
        raw = float(self.leg_cache[key])
        return raw + (self.config.buffer_mins if second is not None else 0.0)

    def route(self, worker, day, ids):
        """Validate a complete day's actual sequence; monthly checks are separate."""
        ids = sorted(ids,key=lambda t:(self.tasks[t]['時間窗_開始'],self.tasks[t]['時間窗_結束'],t))
        for tid in ids:
            error = self.hard(tid,worker)
            if error:
                return dict(available=False,detail=error,reason='hard_constraint',conflict_ids=[tid])
        person = self.people[worker]
        cache_key = (worker,tuple(self.tokens[t] for t in ids),day if person.get('常用交通工具')=='大眾運輸' else '')
        if cache_key in self.route_cache:
            self.route_hits += 1
            # Return current IDs in diagnostic fields, not last week's IDs.
            cached = dict(self.route_cache[cache_key])
            patterns = set(cached.get('conflict_patterns',[]))
            cached['conflict_ids'] = [t for t in ids if self.tokens[t] in patterns] or ids
            return cached
        self.route_checks += 1
        slots = [(self.clocks[self.tasks[t]['時間窗_開始']],self.clocks[self.tasks[t]['時間窗_結束']]) for t in ids]
        occupied = max(0.0,finite(person.get('今日已佔用工時(小時)'),0)*60)
        busy = self.busy.get(worker,[])
        occupied = max(0.0,occupied-sum((b-a).total_seconds()/60 for a,b,_,_ in busy))
        if not ids:
            return dict(available=True,detail='沒有新增服務',reason='',
                working_minutes=occupied+sum((b-a).total_seconds()/60 for a,b,_,_ in busy),
                transfer_minutes=0.0,conflict_ids=[])
        cap = min(12.0,self.config.max_daily_hours,finite(person.get('每日工時上限(小時)'),self.config.max_daily_hours))
        if busy:
            # Legacy fixed busy blocks use the same authoritative evaluator.
            frame = pd.DataFrame([self.tasks[t] for t in ids])
            result = self.engine._evaluate_reassignment(ids[-1],worker,frame,
                pd.DataFrame([{'任務ID':t,'派單居服員':worker} for t in ids[:-1]]),
                pd.DataFrame(self.people.values()),self.config)
            result['conflict_ids'] = ids
            return result
        error = workload_error(slots,cap,occupied,self.config.continuous_work_limit_mins,self.config.mandatory_break_mins)
        transfers = {}
        conflicts = ids
        if not error:
            for i in range(1,len(ids)):
                gap = (slots[i][0]-slots[i-1][1]).total_seconds()/60
                travel = self.leg(worker,day,ids[i-1],ids[i])
                if not math.isfinite(travel) or travel<0:
                    error = '無法取得實際交通，尚不能確認可派。'
                    conflicts = [ids[i-1],ids[i]]
                    break
                if travel>gap+1e-7:
                    error = f'轉場空檔不足：只有 {gap:g} 分鐘，交通加緩衝需 {travel:.2f} 分鐘。'
                    conflicts = [ids[i-1],ids[i]]
                    break
                transfers[i] = travel
        if not error:
            error = workload_error(slots,cap,occupied,self.config.continuous_work_limit_mins,
                                   self.config.mandatory_break_mins,transfers)
        if error and '休息不足' in error:
            continuous,chain = 0.0,[]
            for i,(a,b) in enumerate(slots):
                if i:
                    transfer = transfers.get(i,0.0)
                    if (a-slots[i-1][1]).total_seconds()/60-transfer>=self.config.mandatory_break_mins:
                        continuous,chain = 0.0,[]
                    else:
                        continuous += transfer
                continuous += (b-a).total_seconds()/60
                chain.append(ids[i])
                if continuous>self.config.continuous_work_limit_mins+1e-7:
                    conflicts = chain
                    break
        if not error and ids and not math.isfinite(self.leg(worker,day,ids[0])):
            error = '無法確認服務起點到第一案的交通。'
        minutes = sum((b-a).total_seconds()/60 for a,b in slots)+occupied+sum(transfers.values())
        result = dict(available=not error,detail=error or '資格、時段、交通、休息及每日工時通過',
                      reason='route_constraint' if error else '',working_minutes=minutes,
                      transfer_minutes=sum(transfers.values()),conflict_ids=conflicts,
                      conflict_patterns=[self.tokens[t] for t in conflicts])
        # Do not memoize an unavailable transit/network response across requests.
        if not error or not any(w in error for w in ('無法取得','無法確認')):
            self.route_cache[cache_key] = dict(result)
        return result

    def monthly(self, worker, day, minutes):
        old = self.minutes.get((worker,day),0.0)
        normal = self.config.normal_daily_hours*60
        baseline = max(0.0,finite(self.people[worker].get('排班期外本月加班時數'),0)*60)
        total = baseline+self.month_totals[(worker,day[:7])]-max(0.0,old-normal)+max(0.0,minutes-normal)
        if total>self.config.max_monthly_overtime_hours*60+1e-7:
            return f'每月加班工時超標：{day[:7]} 加入後 {total/60:.2f} 小時，上限 {self.config.max_monthly_overtime_hours:g} 小時。'
        return ''

    def evaluate(self, worker, day, ids, monthly=True):
        result = self.route(worker,day,ids)
        if result['available'] and monthly:
            error = self.monthly(worker,day,result['working_minutes'])
            if error:
                return {**result,'available':False,'detail':error,'reason':'monthly_overtime'}
        return result

    def put(self, worker, day, ids, result):
        old = self.minutes.get((worker,day),0.0)
        normal = self.config.normal_daily_hours*60
        self.month_totals[(worker,day[:7])] += max(0.0,result['working_minutes']-normal)-max(0.0,old-normal)
        self.minutes[(worker,day)] = result['working_minutes']
        self.routes[(worker,day)] = list(ids)

    def add(self, tid, worker, monthly=True):
        day = self.tasks[tid]['日期']
        ids = self.routes.get((worker,day),[])+[tid]
        checked = self.evaluate(worker,day,ids,monthly)
        if checked['available']:
            self.put(worker,day,ids,checked)
        return checked

    def assignment_map(self):
        return {tid:worker for (worker,day),ids in self.routes.items() for tid in ids}

