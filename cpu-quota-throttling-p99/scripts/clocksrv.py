import socket, sys, time

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
while True:
    c, _ = s.accept()
    f = c.makefile("rb")
    for _ in f:
        c.sendall(b"%d\n" % time.time_ns())
    c.close()
