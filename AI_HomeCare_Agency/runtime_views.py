"""Versioned session snapshots: view filters don't rebuild financials or exports."""
import hashlib
import json


def route_parameters(config):
    import os,time
    from caregiver_engine import _get_travel_provider,_osrm_endpoint
    days=max(1,int(os.getenv('CAREFLOW_ROAD_CACHE_DAYS','30')))
    return (config.buffer_mins,config.travel_min_per_km,_get_travel_provider(),
            _osrm_endpoint('機車'),os.getenv('CAREFLOW_OSRM_MAP_VERSION','unspecified'),
            int(time.time()//(days*86400)))


def frame_version(frame):
    return hashlib.sha256(frame.to_json(orient='split',date_format='iso',default_handler=str).encode()).hexdigest()


def snapshot(cache, name, key, factory):
    old = cache.get(name)
    if old is None or old[0]!=key:
        value = factory()
        cache[name] = (key,value)
        return value
    return old[1]


def finance_key(tasks, config):
    prices = (config.hourly_wage,config.sa10_hourly_price,config.service_unit_prices)
    return frame_version(tasks),repr(prices)


def payroll_key(tasks, assignments, caregivers, routes, config):
    parameters = (config.hourly_wage,config.transition_wage,config.buffer_mins,config.normal_daily_hours,
                  config.max_daily_hours,config.max_monthly_overtime_hours,config.overtime_first_multiplier,
                  config.overtime_second_multiplier,config.client_primary_map,config.series_primary_map,
                  config.sa10_hourly_price,config.service_unit_prices)
    return (frame_version(tasks),frame_version(assignments),frame_version(caregivers),
            frame_version(routes),repr(parameters))

