import random
import subprocess
import sys
import time

name, alias = sys.argv[1], sys.argv[2]
fmt = '{{if .Config.Healthcheck}}{{print .State.Health.Status}}{{end}}'


def inspect():
    r = subprocess.run(['docker', 'inspect', '--format=' + fmt, name], capture_output=True, text=True)
    return (r.stdout.splitlines() or [''])[0].strip()


def backoff_ms(attempt):
    delta = random.randrange(int(2000 * 0.8), int(2000 * 1.2))
    additional = (2.0 ** attempt - 1.0) * delta
    return min(2000 + additional, 32000)


def out(kind, text):
    print(f'{time.time():.6f}\t{kind}\t{text}', flush=True)


out('log', 'Waiting for all services to be ready')
status = inspect()
out('inspect', status)
attempt = 0
while status.lower() == 'starting':
    ms = backoff_ms(attempt)
    out('log', f'{alias} service is starting, waiting {int(ms // 1000)} seconds before checking again.')
    time.sleep(ms / 1000)
    status = inspect()
    out('inspect', status)
    attempt += 1
if status == '' or status.lower() == 'healthy':
    out('log', f'{alias} service is healthy.')
    sys.exit(0)
out('log', f'Service container {alias} failed.')
sys.exit(1)
