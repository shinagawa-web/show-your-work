COMMON_K6 = dict(ITER_RATE='600', DURATION='25s', SWITCH_AT='8', TENANT_RATE='20', TENANTS_BEFORE='10',
                 TENANTS_AFTER='10', HEAVY_AFTER='0', ADMIN_QUERY='', K6_TIMEOUT='60s')
COMMON_ADMIN = dict(limit=20, delay_light=50, delay_heavy=300, drain=False)
SWITCH, DUR, MEASURE = 8, 25, (14, 25)

_B = ['k6 ADMIN_QUERY:  -> delay_light=120', 'admin delay_light: 50 -> 120']
_A = ['k6 HEAVY_AFTER: 0 -> 0.4']
_C = ['k6 ADMIN_QUERY:  -> limit=8', 'admin limit: 20 -> 8']
_D = ['k6 TENANTS_AFTER: 10 -> 30']
_MC = ['nginx - server app:8081;', 'nginx + server app:8081 max_conns=20;']

CONTROL_QUERY = 'limit=20&delay_light=50&delay_heavy=300'

S = {
    '00-control': dict(kind='control', changes=[f'k6 ADMIN_QUERY:  -> {CONTROL_QUERY}']),
    '01-a-heavy-share': dict(kind='cause', cause='A', changes=_A, mu=20 / 0.15, arrivals=200,
                             onset={'/api/heavy': 1.4, '/api/light': 1.9}, gave_up=504, success=0),
    '02-b-latency': dict(kind='cause', cause='B', changes=_B, mu=20 / 0.12, arrivals=200,
                         onset={'all': 4.4}, gave_up=504, success=0),
    '03-c-capacity': dict(kind='cause', cause='C', changes=_C, mu=8 / 0.05, arrivals=200,
                          onset={'all': 3.8}, gave_up=504, success=0),
    '04-d-tenants': dict(kind='cause', cause='D', changes=_D, mu=20 / 0.05, arrivals=600,
                         onset={'all': 1.9}, gave_up=504, success=0),
    '05-b-front-cuts': dict(kind='cause', cause='B', changes=_B + ['k6 K6_TIMEOUT: 60s -> 1s', 'nginx - proxy_read_timeout 1s;'],
                            mu=20 / 0.12, arrivals=200, onset={'all': 4.4}, gave_up=499, success=0),
    '06-a-max-conns': dict(kind='measure', cause='A', changes=_A + _MC, success=100 * 133 / 200, p99=300),
    '07-b-max-conns': dict(kind='measure', cause='B', changes=_B + _MC, success=100 * 167 / 200, p99=120),
    '08-c-max-conns': dict(kind='measure', cause='C', changes=_C + _MC, success=100 * 160 / 200, p99=125,
                           queue_max=12, no_504=True),
    '09-d-max-conns': dict(kind='measure', cause='D', changes=_D + _MC, success=100 * 400 / 600, p99=50),
    '10-b-read-timeout-500ms': dict(kind='measure', cause='B',
                                    changes=_B + ['nginx - proxy_read_timeout 1s;', 'nginx + proxy_read_timeout 500ms;'],
                                    mu=20 / 0.12, arrivals=200, onset={'all': 1.9}, gave_up=504, success=0),
    '11-b-fixed-key-limit': dict(kind='measure', cause='B',
                                 changes=_B + ['nginx + limit_req_zone all zone=fixed:1m rate=160r/s;',
                                               'nginx + limit_req zone=fixed burst=20 nodelay;'],
                                 pre_429=20, success=80, queue_flat=True),
}
