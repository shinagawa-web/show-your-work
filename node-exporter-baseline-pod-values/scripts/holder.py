import resource
import signal
import socket
import sys

n = int(sys.argv[1])
ready = sys.argv[2]
limit = 2 * n + 256
resource.setrlimit(resource.RLIMIT_NOFILE, (limit, limit))
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(4096)
port = srv.getsockname()[1]
held = []
for _ in range(n):
    held.append(socket.create_connection(("127.0.0.1", port)))
    held.append(srv.accept()[0])
with open(ready, "w") as f:
    f.write(f"connections={n} port={port}\n")
signal.pause()
