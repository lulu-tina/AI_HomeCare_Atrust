"""Joint recurring-team planning followed by date-aware extension and repair.

Historical primaries remain recorded and receive first reservation. A service
series can use a different stable worker; paid leave is a date-only substitution.
CP-SAT proposes assignments; complete road/rest/workload checks validate them.
The validated greedy incumbent is retained on timeout or an invalid proposal.
"""
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
import math
import time

import pandas as pd
from ortools.sat.python import cp_model
from ortools.linear_solver import pywraplp

from agency_rules import clean, finite, leave_overlap
from fast_calendar import FastCalendar
from recurring_schedule import representative_days
from source_fields import source_primary_map, seniority_score, resolve_worker


def _order(calendar, tid, owners, team=None, preferred=None):
    """Distance orders checks, never excludes a qualified worker."""
    task = calendar.tasks[tid]
    client = str(task['案家ID'])
    day = task['日期']
    main = owners.get(client,'')
    ranked = []
    for worker,person in calendar.people.items():
        if calendar.hard(tid,worker):
            continue
        existing = calendar.routes.get((worker,day),[])
        before = [t for t in existing if calendar.tasks[t]['時間窗_結束']<=task['時間窗_開始']]
        if before:
            previous = calendar.tasks[max(before,key=lambda t:calendar.tasks[t]['時間窗_結束'])]
            origin = previous['服務地點_緯度'],previous['服務地點_經度']
        else:
            origin = person['服務起點_緯度(家)'],person['服務起點_經度(家)']
        distance = calendar.engine.calc_distance_km(*origin,task['服務地點_緯度'],task['服務地點_經度'])
        _,seniority = seniority_score(person,day,calendar.config)
        score = (200 if worker==preferred else 0)+(100 if worker==main else 0)
        score += 25 if worker in (team or {}).get(client,set()) else 0
        score += seniority-finite(distance,1000)*0.3-len(existing)*0.05
        ranked.append((-score,worker))
    return [worker for score,worker in sorted(ranked)]


def _greedy_template(base, grouped, owners):
    calendar = base.clone()
    teams = defaultdict(set)
    for tid,worker in calendar.assignment_map().items():
        teams[str(calendar.tasks[tid]['案家ID'])].add(worker)
    remaining = list(grouped)
    # Qualified-candidate scarcity and service multiplicity determine order.
    qualified = {p:{w for tid in ids for w in calendar.people if not calendar.hard(tid,w)} for p,ids in grouped.items()}
    remaining.sort(key=lambda p:(len(qualified[p]),-sum(float(calendar.tasks[t].get('_template_occurrences',1)) for t in grouped[p]),p))
    for pattern in remaining:
        ids = sorted(grouped[pattern],key=lambda t:(calendar.tasks[t]['日期'],calendar.tasks[t]['時間窗_開始'],t))
        exemplar = ids[0]
        ranked = _order(calendar,exemplar,owners,teams)
        # A worker ineligible on the exemplar may be eligible on a later date.
        ranked += sorted(qualified[pattern]-set(ranked))
        best, best_weight = None,0.0
        total_weight = sum(float(calendar.tasks[t].get('_template_occurrences',1)) for t in ids)
        limits = sorted({min(len(ranked),max(1,calendar.config.candidate_initial_k)),
                         min(len(ranked),max(1,calendar.config.candidate_expanded_k)),len(ranked)})
        examined = 0
        for limit in limits:
            if examined and limit>examined:
                calendar.candidate_expansions += 1
            for worker in ranked[examined:limit]:
                trial = calendar.clone()
                weight = 0.0
                for tid in ids:
                    checked = trial.add(tid,worker,monthly=False)
                    if checked['available']:
                        weight += float(calendar.tasks[tid].get('_template_occurrences',1))
                if weight>best_weight:
                    best,best_weight = trial,weight
                if weight>=total_weight:
                    break
            examined = limit
            if best_weight>=total_weight:
                break
        if best:
            calendar = best
            for tid in ids:
                worker = calendar.assignment_map().get(tid)
                if worker:
                    teams[str(calendar.tasks[tid]['案家ID'])].add(worker)
    return calendar


def _joint_template(base, grouped, greedy, owners, month_weights,partition=True):
    """Choose all residual series together; route cuts retain a feasible draft."""
    if not grouped or base.config.team_solver_seconds<=0:
        return greedy,dict(solver='skipped',solver_seconds=0.0,proposal_cuts=0)
    if partition:
        pieces = defaultdict(dict)
        for pattern,ids in grouped.items():
            pieces[pd.Timestamp(base.tasks[ids[0]]['日期']).isoweekday()][pattern] = ids
        if len(pieces)>1:
            calendar = base.clone()
            original_assignment = greedy.assignment_map()
            summary = dict(solver='weekday_components',solver_seconds=0.0,model_build_seconds=0.0,
                           proposal_cuts=0,joint_variables=0,weekday_models=len(pieces),
                           greedy_template_services=_coverage(greedy))
            for weekday,subset in sorted(pieces.items(),key=lambda item:len(item[1])):
                incumbent = calendar.clone()
                for ids in subset.values():
                    for tid in sorted(ids,key=lambda t:base.tasks[t]['日期']):
                        worker = original_assignment.get(tid)
                        if worker:
                            incumbent.add(tid,worker,monthly=False)
                component_base = calendar.clone()
                component_base.config = replace(base.config,team_solver_seconds=max(.15,base.config.team_solver_seconds/len(pieces)))
                candidate,info = _joint_template(component_base,subset,incumbent,owners,month_weights,partition=False)
                calendar = candidate
                for key in ('solver_seconds','model_build_seconds','proposal_cuts','joint_variables'):
                    summary[key] += info.get(key,0)
            calendar.config = base.config
            # A component timeout must never lower the validated original draft.
            if _coverage(calendar)<_coverage(greedy):
                calendar = greedy
            summary['joint_template_services'] = _coverage(calendar)
            return calendar,summary
    started = time.perf_counter()
    deadline = None
    model = cp_model.CpModel()
    x,z,by_worker_day,by_pattern_worker = {},{},{},defaultdict(list)
    all_candidates = {}
    for pattern,ids in grouped.items():
        qualified = []
        for worker in base.people:
            applicable = []
            for tid in ids:
                day = base.tasks[tid]['日期']
                checked = base.evaluate(worker,day,base.routes.get((worker,day),[])+[tid],monthly=False)
                if checked['available']:
                    applicable.append(tid)
            if applicable:
                qualified.append(worker)
                x[(pattern,worker)] = model.new_bool_var(f'series_{len(x)}')
                for tid in applicable:
                    var = z[(tid,worker)] = model.new_bool_var(f'visit_{len(z)}')
                    model.add(var<=x[(pattern,worker)])
                    by_worker_day.setdefault((worker,base.tasks[tid]['日期']),[]).append((tid,var))
                    by_pattern_worker[(pattern,worker)].append(var)
                model.add(x[(pattern,worker)]<=sum(by_pattern_worker[(pattern,worker)]))
        all_candidates[pattern] = qualified
        if qualified:
            model.add(sum(x[(pattern,w)] for w in qualified)<=1)
    if not z:
        return greedy,dict(solver='no_candidates',solver_seconds=round(time.perf_counter()-started,3),proposal_cuts=0)
    for tid in {tid for tid,worker in z}:
        model.add(sum(var for (other,worker),var in z.items() if other==tid)<=1)
    overtime = defaultdict(list)
    for (worker,day),variables in by_worker_day.items():
        variables.sort(key=lambda pair:base.tasks[pair[0]]['時間窗_開始'])
        for i,(left,a) in enumerate(variables):
            left_task = base.tasks[left]
            for right,b in variables[i+1:]:
                gap = (base.clocks[base.tasks[right]['時間窗_開始']]-base.clocks[left_task['時間窗_結束']]).total_seconds()/60
                if gap>=90:
                    break
                if gap<0:
                    model.add(a+b<=1)
                elif gap<base.config.buffer_mins:
                    travel = base.leg(worker,day,left,right)
                    if not math.isfinite(travel) or travel>gap+1e-7:
                        model.add(a+b<=1)
        base_minutes = base.minutes.get((worker,day),0.0)
        expression = int(math.ceil(base_minutes))+sum(int(round(base.tasks[tid]['服務歷時(分鐘)']))*var for tid,var in variables)
        cap = min(12.0,base.config.max_daily_hours,finite(base.people[worker].get('每日工時上限(小時)'),base.config.max_daily_hours))
        model.add(expression<=int(cap*60+1e-7))
        extra = model.new_int_var(0,720,'ot_'+str(len(overtime)))
        model.add(extra>=expression-int(base.config.normal_daily_hours*60))
        for month,count in month_weights.get(day,{day[:7]:1}).items():
            overtime[(worker,month)].append(extra*count)
    # Routes without free choices also use overtime budget in each calendar month.
    for (worker,day),minutes in base.minutes.items():
        if (worker,day) in by_worker_day:
            continue
        for month,count in month_weights.get(day,{day[:7]:1}).items():
            overtime[(worker,month)].append(int(math.ceil(max(0,minutes-base.config.normal_daily_hours*60)))*count)
    for (worker,month),terms in overtime.items():
        baseline = max(0,finite(base.people[worker].get('排班期外本月加班時數'),0))*60
        model.add(sum(terms)<=max(0,int(base.config.max_monthly_overtime_hours*60-baseline)))
    used,case_variables = {},defaultdict(list)
    for (pattern,worker),var in x.items():
        client = str(base.tasks[grouped[pattern][0]]['案家ID'])
        case_variables[(client,worker)].append(var)
    for key,variables in case_variables.items():
        used[key] = model.new_bool_var('team_'+str(len(used)))
        model.add_max_equality(used[key],variables)
    primary = {}
    for client in {client for client,worker in used if client not in owners}:
        workers = [worker for c,worker in used if c==client]
        any_work = model.new_bool_var('case_'+str(len(primary)))
        model.add_max_equality(any_work,[used[(client,w)] for w in workers])
        for worker in workers:
            primary[(client,worker)] = model.new_bool_var('primary_'+str(len(primary)))
            model.add(primary[(client,worker)]<=used[(client,worker)])
        model.add(sum(primary[(client,w)] for w in workers)==any_work)
    objectives = []
    incumbent = greedy.assignment_map()
    primary_hint = _choose_primaries(greedy,owners)
    for (tid,worker),var in z.items():
        task = base.tasks[tid]
        weight = int(round(float(task.get('_template_occurrences',1))))
        _,seniority = seniority_score(base.people[worker],task['日期'],base.config)
        main = owners.get(str(task['案家ID']))
        objectives.append(var*(weight*1000000+int(seniority*10)+(600 if worker==main else 0)))
        model.add_hint(var,int(incumbent.get(tid)==worker))
        if (str(task['案家ID']),worker) in primary:
            both = model.new_bool_var('main_visit_'+str(len(objectives)))
            model.add(both<=var)
            model.add(both<=primary[(str(task['案家ID']),worker)])
            model.add(both>=var+primary[(str(task['案家ID']),worker)]-1)
            objectives.append(both*500*weight)
    for (pattern,worker),var in x.items():
        model.add_hint(var,int(any(incumbent.get(t)==worker for t in grouped[pattern])))
    for (client,worker),var in primary.items():
        model.add_hint(var,int(primary_hint.get(client)==worker))
    objectives += [-3000*var for var in used.values()]
    model.maximize(sum(objectives))
    # Model construction is measured separately; a usable search budget remains.
    build_seconds = time.perf_counter()-started
    deadline = time.perf_counter()+base.config.team_solver_seconds
    best = greedy
    best_weight = _coverage(best)
    cuts = 0
    status_name = 'time_limit'
    while time.perf_counter()<deadline:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(.02,deadline-time.perf_counter())
        solver.parameters.num_search_workers = 4
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        status_name = solver.status_name(status)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            break
        selected = {tid:worker for (tid,worker),var in z.items() if solver.value(var)}
        trial = base.clone()
        violations = []
        groups = defaultdict(list)
        for tid,worker in selected.items():
            groups[(worker,base.tasks[tid]['日期'])].append(tid)
        for (worker,day),ids in sorted(groups.items()):
            combined = base.routes.get((worker,day),[])+ids
            checked = trial.evaluate(worker,day,combined,monthly=False)
            if checked['available']:
                trial.put(worker,day,combined,checked)
            else:
                conflicts = set(checked.get('conflict_ids',combined))
                variables = [z[(tid,worker)] for tid in ids if tid in conflicts]
                if not variables:
                    variables = [z[(tid,worker)] for tid in ids]
                violations.append(variables)
                # Retain the feasible portion of a global proposal. A single
                # bad day's route must not discard improvements on other days.
                for tid in sorted(ids,key=lambda t:(-float(base.tasks[t].get('_template_occurrences',1)),
                    base.tasks[t]['時間窗_開始'],t)):
                    trial.add(tid,worker,monthly=False)
        weight = _coverage(trial)
        if weight>best_weight or (not violations and weight==best_weight):
            best,best_weight = trial,weight
        if not violations:
            break
        for variables in violations:
            model.add(sum(variables)<=len(variables)-1)
            cuts += 1
    return best,dict(solver=status_name,solver_seconds=round(time.perf_counter()-started,3),
                     model_build_seconds=round(build_seconds,3),
                     proposal_cuts=cuts,joint_variables=len(x)+len(z)+len(primary),
                     greedy_template_services=_coverage(greedy),joint_template_services=best_weight)


def _coverage(calendar):
    return sum(float(calendar.tasks[t].get('_template_occurrences',1)) for t in calendar.assignment_map())


def _choose_primaries(calendar, original):
    owners = dict(original)
    counts = defaultdict(Counter)
    for tid,worker in calendar.assignment_map().items():
        task = calendar.tasks[tid]
        counts[str(task['案家ID'])][worker] += float(task.get('_template_occurrences',1))
    for client,workers in counts.items():
        if client not in owners:
            owners[client] = sorted(workers,key=lambda w:(-workers[w],w))[0]
    return owners


def _repair(calendar, pending, protected, owners, preferred):
    """Move at most two unconfirmed conflicting services, only on affected days."""
    deadline = time.perf_counter()+calendar.config.team_repair_seconds
    repaired = 0
    timed_out = False
    for tid in list(pending):
        if tid in calendar.config.frozen_task_assignments:
            continue
        if time.perf_counter()>deadline:
            timed_out = True
            break
        task = calendar.tasks[tid]
        day = task['日期']
        for worker in _order(calendar,tid,owners,preferred=preferred.get(calendar.tokens[tid])):
            existing = calendar.routes.get((worker,day),[])
            # Include nearby transitions and rests, not only exact overlaps.
            a,b = calendar.clocks[task['時間窗_開始']],calendar.clocks[task['時間窗_結束']]
            blockers = [t for t in existing if t not in protected and
                calendar.clocks[calendar.tasks[t]['時間窗_開始']] < b+timedelta(minutes=60) and
                calendar.clocks[calendar.tasks[t]['時間窗_結束']] > a-timedelta(minutes=60)]
            if not blockers or len(blockers)>2:
                continue
            trial = calendar.clone()
            left = [t for t in existing if t not in blockers]
            released = trial.evaluate(worker,day,left,monthly=False)
            if not released['available']:
                continue
            trial.put(worker,day,left,released)
            if not trial.add(tid,worker)['available']:
                continue
            ok = True
            for moved in blockers:
                choices = [w for w in _order(trial,moved,owners,preferred=preferred.get(calendar.tokens[moved])) if w!=worker]
                for other in choices:
                    if trial.add(moved,other)['available']:
                        break
                else:
                    ok = False
                    break
            if ok:
                calendar = trial
                pending.pop(tid,None)
                repaired += 1
                break
    return calendar,repaired,timed_out


def _repair_series(calendar, owners, frozen_patterns):
    """Exchange complete recurring series; primary identities stay unchanged."""
    deadline = time.perf_counter()+calendar.config.template_repair_seconds
    pending = defaultdict(list)
    assigned = calendar.assignment_map()
    for tid in calendar.tasks:
        if tid not in assigned:
            pending[calendar.tokens[tid]].append(tid)
    changes = 0
    for token,targets in sorted(pending.items(),key=lambda item:-sum(float(calendar.tasks[t].get('_template_occurrences',1)) for t in item[1])):
        if time.perf_counter()>deadline:
            break
        exemplar = targets[0]
        workers = _order(calendar,exemplar,owners)
        for worker in workers:
            if time.perf_counter()>deadline:
                break
            blockers = set()
            for tid in targets:
                task = calendar.tasks[tid]
                a,b = calendar.clocks[task['時間窗_開始']],calendar.clocks[task['時間窗_結束']]
                for existing in calendar.routes.get((worker,task['日期']),[]):
                    row = calendar.tasks[existing]
                    if calendar.clocks[row['時間窗_開始']]<b+timedelta(minutes=35) and calendar.clocks[row['時間窗_結束']]>a-timedelta(minutes=35):
                        blockers.add(calendar.tokens[existing])
            blockers.discard(token)
            if not blockers or len(blockers)>2 or blockers & frozen_patterns:
                continue
            move_groups = {p:[t for t in assigned if calendar.tokens[t]==p] for p in blockers}
            if not all(move_groups.values()):
                continue
            trial = calendar.clone()
            released = {t for ids in move_groups.values() for t in ids}
            good = True
            for (current,day),ids in list(trial.routes.items()):
                if not released.intersection(ids):
                    continue
                left = [t for t in ids if t not in released]
                checked = trial.evaluate(current,day,left,monthly=False)
                if not checked['available']:
                    good = False
                    break
                trial.put(current,day,left,checked)
            if not good:
                continue
            for tid in targets:
                if not trial.add(tid,worker,monthly=False)['available']:
                    good = False
                    break
            if not good:
                continue
            for pattern,ids in move_groups.items():
                rehomed = None
                for other in _order(trial,ids[0],owners):
                    if other==worker:
                        continue
                    move = trial.clone()
                    if all(move.add(tid,other,monthly=False)['available'] for tid in ids):
                        rehomed = move
                        break
                if rehomed is None:
                    good = False
                    break
                trial = rehomed
            if good and _coverage(trial)>_coverage(calendar):
                calendar = trial
                assigned = calendar.assignment_map()
                changes += 1
                break
    return calendar,changes


def run_team_period_dispatch(tasks,caregivers,clients,config,primary_map=None,progress=None,routine_templates=None):
    import caregiver_engine as engine
    from monthly_scheduler import MATCH_COLUMNS, RESULT_COLUMNS, failure_kind
    started = time.perf_counter()
    tasks = engine.merge_task_client_tables(tasks,clients).copy()
    tasks['日期'] = pd.to_datetime(tasks['日期'],errors='coerce').dt.strftime('%Y-%m-%d')
    engine.validate_dispatch_inputs(tasks,caregivers)
    tasks = tasks.sort_values(['日期','時間窗_開始','任務ID']).reset_index(drop=True)
    workers = set(caregivers['居服員ID'].astype(str))
    original = source_primary_map(clients,workers,primary_map)
    config.client_primary_map = dict(original)
    motor = caregivers.loc[caregivers['常用交通工具'].eq('機車')]
    if not motor.empty:
        sites = tasks[['服務地點_緯度','服務地點_經度']].drop_duplicates()
        engine.prefetch_osrm_travel_times(motor[['服務起點_緯度(家)','服務起點_經度(家)']].drop_duplicates().itertuples(index=False,name=None),sites.itertuples(index=False,name=None),config.travel_min_per_km)
        engine.prefetch_osrm_travel_times(sites.itertuples(index=False,name=None),sites.itertuples(index=False,name=None),config.travel_min_per_km)
    if progress:
        progress(.05,'整理週序列與固定服務團隊…')
    basis = representative_days(tasks,caregivers)
    day_representatives = basis.attrs.get('day_representatives',{})
    month_weights = defaultdict(Counter)
    for day,representative in day_representatives.items():
        month_weights[representative][day[:7]] += 1
    base = FastCalendar(basis,caregivers,config)
    timing = {'prepare_seconds':time.perf_counter()-started}
    seeds = dict(getattr(config,'series_primary_map',{}))
    seeds.update((routine_templates or {}).get('series_primary_map',{}))
    # The portable Excel column takes precedence over draft local templates.
    for tid,task in base.tasks.items():
        source_worker = resolve_worker(task.get('週序列居服員'),workers)
        if source_worker in workers:
            seeds[base.tokens[tid]] = source_worker
    seeds = {token:worker for token,worker in seeds.items() if worker in workers}
    base_groups = defaultdict(list)
    for tid,task in base.tasks.items():
        preferred = seeds.get(base.tokens[tid]) or original.get(str(task['案家ID']))
        if preferred in workers:
            base_groups[(preferred,task['日期'])].append(tid)
    for (worker,day),ids in sorted(base_groups.items()):
        checked = base.evaluate(worker,day,ids,monthly=False)
        if checked['available']:
            base.put(worker,day,ids,checked)
        else:
            for tid in sorted(ids,key=lambda t:(base.tasks[t]['時間窗_開始'],t)):
                base.add(tid,worker,monthly=False)
    reserved = base.assignment_map()
    timing['reserve_seconds'] = time.perf_counter()-started-sum(timing.values())
    grouped = defaultdict(list)
    for tid in base.tasks:
        if tid not in reserved:
            grouped[base.tokens[tid]].append(tid)
    if progress:
        progress(.15,f'聯合安排 {len(grouped)} 個待補週序列…')
    greedy = _greedy_template(base,grouped,original)
    timing['greedy_seconds'] = time.perf_counter()-started-sum(timing.values())
    # A complete feasible recurring incumbent needs no further search for coverage.
    if len(greedy.assignment_map())==len(base.tasks):
        planned,stats = greedy,dict(solver='complete_greedy',solver_seconds=0.0,proposal_cuts=0)
    else:
        planned,stats = _joint_template(base,grouped,greedy,original,month_weights)
    timing['joint_seconds'] = time.perf_counter()-started-sum(timing.values())
    from pending_clients import pattern_token
    frozen_patterns = {pattern_token(row) for row in tasks.loc[tasks['任務ID'].astype(str).isin(config.frozen_task_assignments)].to_dict('records')}
    planned,series_exchanges = _repair_series(planned,original,frozen_patterns)
    timing['series_exchange_seconds'] = time.perf_counter()-started-sum(timing.values())
    owners = _choose_primaries(planned,original)
    config.client_primary_map = owners
    series_votes = defaultdict(Counter)
    leave_routines = {}
    template_days = defaultdict(dict)
    for tid,worker in planned.assignment_map().items():
        token = planned.tokens[tid]
        task = planned.tasks[tid]
        routine = seeds.get(token) or original.get(str(task['案家ID']))
        if routine in planned.people and routine!=worker and leave_overlap(task,planned.people[routine]):
            # Even an entire month's leave must not promote a backup to a
            # permanent weekday worker or erase the leave label.
            leave_routines[token] = routine
            template_days[task['日期']][token] = routine
        else:
            series_votes[token][worker] += float(task.get('_template_occurrences',1))
            template_days[task['日期']][token] = worker
    series_map = dict(seeds)
    series_map.update(leave_routines)
    for token,votes in series_votes.items():
        series_map[token] = sorted(votes,key=lambda w:(-votes[w],w))[0]
    config.series_primary_map = series_map
    if progress:
        progress(.6,'延展週序列，核對日期例外與月加班…')
    calendar = FastCalendar(tasks,caregivers,config)
    calendar.hard_cache = dict(planned.hard_cache)
    calendar.route_cache = dict(planned.route_cache)
    calendar.leg_cache = dict(planned.leg_cache)
    pending,preferred,protected = {},{},set()
    actual_days = defaultdict(list)
    for tid,task in calendar.tasks.items():
        actual_days[task['日期']].append(tid)
    for i,(day,ids) in enumerate(actual_days.items()):
        if progress and i%7==0:
            progress(.6+.22*i/max(1,len(actual_days)),f'延展班表 {i+1}/{len(actual_days)} 日')
        desired_groups = defaultdict(list)
        shape = template_days.get(day_representatives.get(day,''),{})
        for tid in ids:
            task = calendar.tasks[tid]
            regular = config.frozen_task_assignments.get(tid) or shape.get(calendar.tokens[tid]) or series_map.get(calendar.tokens[tid]) or owners.get(str(task['案家ID']))
            preferred[tid] = regular
            if regular in workers:
                desired_groups[regular].append(tid)
            else:
                pending[tid] = '週序列尚無可行人選'
        for worker,group in sorted(desired_groups.items()):
            group.sort(key=lambda t:(t not in config.frozen_task_assignments,
                str(calendar.tasks[t]['案家ID']) not in original,calendar.tasks[t]['時間窗_開始'],t))
            eligible = []
            for tid in group:
                error = calendar.hard(tid,worker)
                if error:
                    pending[tid] = error
                else:
                    eligible.append(tid)
            checked = calendar.evaluate(worker,day,eligible)
            if checked['available']:
                calendar.put(worker,day,eligible,checked)
                for tid in eligible:
                    if config.frozen_task_assignments.get(tid)==worker:
                        protected.add(tid)
            else:
                for tid in eligible:
                    individual = calendar.add(tid,worker)
                    if individual['available']:
                        if config.frozen_task_assignments.get(tid)==worker:
                            protected.add(tid)
                    else:
                        pending[tid] = individual['detail']
    teams = defaultdict(set)
    timing['extend_seconds'] = time.perf_counter()-started-sum(timing.values())
    for tid,worker in calendar.assignment_map().items():
        teams[str(calendar.tasks[tid]['案家ID'])].add(worker)
    exceptions = dict(pending)
    diagnostics = {}
    for tid in sorted(pending,key=lambda t:(calendar.tasks[t]['日期'],calendar.tasks[t]['時間窗_開始'],t)):
        if tid in config.frozen_task_assignments:
            diagnostics[tid] = {config.frozen_task_assignments[tid]:'已確認人選遇到限制，保留待處理，需由居督確認異動'}
            continue
        reasons = {}
        candidates = _order(calendar,tid,owners,teams,preferred=preferred.get(tid))
        examined = 0
        done = False
        limits = sorted({min(len(candidates),config.candidate_initial_k),min(len(candidates),config.candidate_expanded_k),len(candidates)})
        for limit in limits:
            if examined and limit>examined:
                calendar.candidate_expansions += 1
            for worker in candidates[examined:limit]:
                checked = calendar.add(tid,worker)
                if checked['available']:
                    teams[str(calendar.tasks[tid]['案家ID'])].add(worker)
                    pending.pop(tid,None)
                    done = True
                    break
                reasons[worker] = checked['detail']
            examined = limit
            if done:
                break
        if not done:
            for worker in workers-set(candidates):
                reasons[worker] = calendar.hard(tid,worker) or '候選資料待核對'
            diagnostics[tid] = reasons
    if progress:
        progress(.85,f'局部調整 {len(pending)} 次受阻服務…')
    timing['fallback_seconds'] = time.perf_counter()-started-sum(timing.values())
    calendar,repaired,repair_timeout = _repair(calendar,pending,protected,owners,series_map)
    timing['repair_seconds'] = time.perf_counter()-started-sum(timing.values())
    # Diagnose the final draft, since local exchanges can change a candidate's
    # blockers. A search timeout is metadata, not a fabricated failure cause.
    for tid in pending:
        if tid in config.frozen_task_assignments:
            worker = config.frozen_task_assignments[tid]
            checked = calendar.evaluate(worker,calendar.tasks[tid]['日期'],calendar.routes.get((worker,calendar.tasks[tid]['日期']),[])+[tid]) if worker in workers else {'detail':'已確認居服員不在本次員工資料'}
            diagnostics[tid] = {worker:checked['detail']+'；已確認人選須由居督確認異動'}
        else:
            diagnostics[tid] = {worker:calendar.evaluate(worker,calendar.tasks[tid]['日期'],
                calendar.routes.get((worker,calendar.tasks[tid]['日期']),[])+[tid])['detail'] for worker in sorted(workers)}
    assigned = calendar.assignment_map()
    # New cases without a template owner may still gain one through repair.
    owners = _choose_primaries(calendar,owners)
    config.client_primary_map,config.work_day_minutes = owners,dict(calendar.minutes)
    config.primary_profiles = calendar.people
    financial = engine.calculate_task_revenue_and_salary(tasks,config).set_index('任務ID').to_dict('index')
    rows,matches,notes = [],[],[]
    for tid,task in calendar.tasks.items():
        client = str(task['案家ID'])
        if tid not in assigned:
            reasons = diagnostics.get(tid,{})
            detail = '；'.join(f'{w}：{reason}' for w,reason in reasons.items()) or pending.get(tid,'待核對可用人選')
            common = Counter(failure_kind(r)[0] for r in reasons.values()).most_common(1)
            kind = common[0][0] if common else failure_kind(detail)[0]
            notes.append({'任務ID':tid,'案家ID':client,'來源列':task.get('來源列'),'失敗階段':'團隊派班核對',
                          '問題分類':kind,'原因碼':{'必要證照':'qualification','資格日期':'qualification_date',
                              '時段重疊':'time_overlap','休息不足':'rest','轉場空檔不足':'travel_gap',
                              '可服務星期':'off_day','工時上限':'work_hours','交通資料':'travel_data'}.get(kind,'other_constraint'),
                          '局部搜尋達時限':repair_timeout,
                          '候選數':sum(not calendar.hard(tid,w) for w in workers),'剔除原因':detail,
                          '逐人排班原因':reasons,'建議處理':'核對可調整時段、固定搭配人員及逐人原因；必要時延長局部搜尋'})
            continue
        worker = assigned[tid]
        regular = preferred.get(tid) or series_map.get(calendar.tokens[tid]) or owners.get(client)
        absence = leave_overlap(task,calendar.people.get(regular)) if regular!=worker else ''
        source = ('請假代班' if absence else '歷史首選' if original.get(client)==worker else
                  '主責居服員' if owners.get(client)==worker else '固定搭配' if regular==worker else '時段支援')
        if not absence and regular!=worker:
            explanation = exceptions.get(tid,'為補足受阻服務，改由合格團隊人員承接')
        else:
            explanation = absence
        _,seniority = seniority_score(calendar.people[worker],task['日期'],config)
        score = config.base_score+seniority+(config.preferred_caregiver_bonus if owners.get(client)==worker else 0)
        fin = financial[tid]
        rows.append({'任務ID':tid,'案家ID':client,'派單居服員':worker,'適配分數':score,'預估車程(分)':float('nan'),
                     '服務時段':f"{task['時間窗_開始']}-{task['時間窗_結束']}",'任務優先級':task.get('任務優先級','一般'),
                     '地點緯度':task.get('服務地點_緯度'),'地點經度':task.get('服務地點_經度'),
                     '預估服務營收':fin['預估服務營收'],'預估居服員薪資':fin['預估居服員薪資'],
                     '原首選替換原因':explanation,'排班來源':source,'排班日期':task['日期'],
                     '固定居服員ID':owners.get(client,''),'主責居服員ID':owners.get(client,''),
                     '服務序列居服員ID':regular,'是否固定搭配':bool(regular==worker and worker!=owners.get(client)),
                     '是否請假代班':bool(absence),'代班原因':absence})
        matches.append({'任務ID':tid,'案家ID':client,'居服員ID':worker,'適配度分數':score,
                        '預估交通時間(分)':float('nan'),'地點緯度':task.get('服務地點_緯度'),'地點經度':task.get('服務地點_經度'),
                        '任務開始時間':task['時間窗_開始'],'任務結束時間':task['時間窗_結束'],'優先級':task.get('任務優先級','一般'),
                        '是否歷史首選':owners.get(client)==worker,
                        '具備失智症20小時認證(0/1)':int(engine.caregiver_has_cert(calendar.people[worker],'失智症照顧專長')),
                        '常用交通工具':calendar.people[worker].get('常用交通工具')})
    result = pd.DataFrame(rows,columns=list(dict.fromkeys(RESULT_COLUMNS+['主責居服員ID','服務序列居服員ID','是否固定搭配'])))
    match_frame = pd.DataFrame(matches,columns=MATCH_COLUMNS)
    match_frame.attrs['matching_diagnostics'] = notes
    if progress:
        progress(.94,'整理已派路段與團隊服務序列…')
    timing['output_seconds'] = time.perf_counter()-started-sum(timing.values())
    routes = engine.calculate_schedule_travel(result,tasks,caregivers,config) if not result.empty else result.copy()
    if not result.empty:
        result['預估車程(分)'] = result['任務ID'].map(routes.set_index('任務ID')['預估車程(分)'])
    timing['route_seconds'] = time.perf_counter()-started-sum(timing.values())
    team_counts = tasks[['任務ID','案家ID']].merge(result[['任務ID','派單居服員']],on='任務ID').groupby('案家ID')['派單居服員'].nunique()
    stats.update(historical_clients=len(original),new_clients=len(set(tasks['案家ID'].astype(str))-set(original)),
                 template_service_count=len(basis),template_series_count=len(set(base.tokens.values())),
                 template_day_count=basis['日期'].nunique(),period_service_count=len(tasks),
                 route_cache_hits=calendar.route_hits,route_checks=calendar.route_checks,
                 candidate_expansions=calendar.candidate_expansions,local_repairs=repaired,
                 series_exchanges=series_exchanges,
                 repair_time_limit=repair_timeout,client_team_sizes=team_counts.value_counts().sort_index().to_dict(),
                 core_seconds=round(time.perf_counter()-started,3),phase_seconds={k:round(v,3) for k,v in timing.items()})
    config.dispatch_cache = {}
    if progress:
        progress(1.0,f'派出 {len(result)}／{len(tasks)} 次服務')
    return {'df_tasks':tasks,'df_result':result,'df_matches':match_frame,'route_result':routes,
            'client_primary_map':dict(owners),'series_primary_map':dict(series_map),
            'status':pywraplp.Solver.FEASIBLE,'assigned_count':len(result),'df_valid':pd.DataFrame(),
            'pending_details':diagnostics,'daily_counts':dict(Counter(tasks.loc[tasks['任務ID'].isin(result['任務ID']),'日期'])),
            'diagnostics':pd.DataFrame(notes),'_day_templates':{'series_primary_map':dict(series_map)},
            'dispatch_stats':stats}

