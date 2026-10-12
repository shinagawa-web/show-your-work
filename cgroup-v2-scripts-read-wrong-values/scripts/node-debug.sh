k() { echo "\$ $1"; sh -c "$1" 2>&1; echo "[exit $?]"; }
k 'cat /proc/self/cgroup'
k 'cat /proc/@PID@/cgroup'
p=$(sed -n 's/^0:://p' /proc/@PID@/cgroup)
k "ls -d /sys/fs/cgroup$p"
k "ls -d /host/sys/fs/cgroup$p"
k 'grep cgroup /proc/self/mountinfo'
k 'find /host/sys/fs/cgroup -type d -name cri-containerd-@CID@.scope'
d=$(find /host/sys/fs/cgroup -type d -name cri-containerd-@CID@.scope | head -n1)
k "cat $d/cpu.weight"
k "cat $d/memory.events"
