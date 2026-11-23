import datetime
import json
import subprocess
import sys
import threading
import time

dns, sg, plan_dir, plan_file, terraform, out, host_ip, account = sys.argv[1:9]
WATCH_AFTER_APPLY = 90
INTERVAL = 2
attempts = []
stop = threading.Event()
t0 = time.time()


def stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def curl():
    started = time.time()
    r = subprocess.run(
        ["curl", "-sS", "-k", "--max-time", "10", "-w", r"\n%{http_code} %{exitcode}", f"https://{dns}/"],
        capture_output=True, text=True,
    )
    attempts.append((started, time.time(), r.stdout.rsplit("\n", 1)[-1], "reached app" in r.stdout))


def loop():
    while not stop.is_set():
        threading.Thread(target=curl).start()
        time.sleep(INTERVAL)


def rules():
    r = subprocess.run(
        ["aws", "ec2", "describe-security-group-rules", "--filters", f"Name=group-id,Values={sg}", "--query",
         "SecurityGroupRules[].{Egress:IsEgress,From:FromPort,To:ToPort,Cidr:CidrIpv4}", "--output", "json"],
        capture_output=True, text=True,
    )
    return f"{stamp(time.time())} {json.dumps(json.loads(r.stdout)) if r.returncode == 0 else r.stderr}"


lines = [f"curl to the ALB every {INTERVAL} s (--max-time 10), before, during and after an apply that replaces the allowed CIDR",
         "", "Security group rules before: " + rules()]
worker = threading.Thread(target=loop)
worker.start()
time.sleep(6)
applied = []
p = subprocess.Popen([terraform, f"-chdir={plan_dir}", "apply", "-no-color", plan_file],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
destroyed = None
for line in p.stdout:
    now = time.time()
    if "security_group_ingress_rule" in line or "Apply complete" in line or "Error" in line:
        applied.append(f"{stamp(now)} {line.rstrip()}")
    if "Destruction complete" in line:
        destroyed = now
rc = p.wait()
end = time.time()
lines.append(f"terraform apply exit code {rc} at {stamp(end)}")
lines += applied
lines.append("Security group rules right after the apply: " + rules())
time.sleep(WATCH_AFTER_APPLY)
stop.set()
worker.join()
time.sleep(11)
lines.append("Security group rules at the end: " + rules())
lines += ["", "Attempts (start time, seconds from the end of the rule destruction to the start of the attempt, http_code curl_exit, app body returned)"]
attempts.sort()
base = destroyed or end
for s, e, code, reached in attempts:
    lines.append(f"{stamp(s)} {s - base:+7.1f}s {code} {reached}")
reached = [s for s, _, _, ok in attempts if ok]
blocked = [s for s, _, _, ok in attempts if not ok]
last = max(reached) if reached else None
after = [s for s in blocked if last is None or s > last]
lines.append("")
if last is not None:
    lines.append(f"Last attempt that reached the app started {last - base:+.1f} s from the rule destruction, {last - end:+.1f} s from the end of the apply")
if after:
    lines.append(f"First attempt of the run that did not reach it and was never followed by one that did: {after[0] - base:+.1f} s from the rule destruction, {after[0] - end:+.1f} s from the end of the apply; {len(after)} attempts in a row")
text = "\n".join(lines) + "\n"
text = text.replace(host_ip, "198.51.100.1").replace(account, "111122223333")
open(out, "w").write(text)
print("\n".join(text.splitlines()[-4:]))
sys.exit(rc)
