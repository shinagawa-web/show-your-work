import pathlib
import subprocess
import sys

results = pathlib.Path(sys.argv[1])
titles = {
    "00-environment.txt": "Environment",
    "01-symptom.txt": "v1 paths",
    "p1-cpu-stat-inside-vs-node.txt": "P1: cpu.stat inside the container and on the node",
    "p2-throttled-key.txt": "P2: throttled_time and throttled_usec",
    "p3-working-set.txt": "P3: memory.stat and container_memory_working_set_bytes",
    "p4-oom-kill.txt": "P4: memory.events across OOM restarts",
    "p5-cpu-weight.txt": "P5: cpu.weight of the Pod slice and the container scope",
    "p6-node-debug-cgroup.txt": "P6: /proc/<pid>/cgroup from kubectl debug node",
    "elapsed.txt": "Elapsed",
}
print("## Predictions\n")
print(subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("checks.py")), str(results)], capture_output=True, text=True).stdout)
for name, title in titles.items():
    f = results / name
    print(f"## {title}\n")
    print(f"`{name}`\n")
    print("```")
    print(f.read_text().rstrip() if f.exists() else "(missing)")
    print("```\n")
