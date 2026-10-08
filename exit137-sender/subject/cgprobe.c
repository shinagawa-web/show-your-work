#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <time.h>
#include <unistd.h>

static long long ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static int slurp(const char *path, char *out, size_t n)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -errno;
	ssize_t r = read(fd, out, n - 1);
	int e = errno;
	close(fd);
	if (r < 0)
		return -e;
	out[r] = 0;
	for (char *p = out; *p; p++)
		if (*p == '\n')
			*p = ' ';
	return 0;
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: cgprobe <cgroup dir>\n");
		return 2;
	}
	setvbuf(stdout, NULL, _IOLBF, 0);
	char mev[4096], cev[4096];
	snprintf(mev, sizeof mev, "%s/memory.events", argv[1]);
	snprintf(cev, sizeof cev, "%s/cgroup.events", argv[1]);
	int in = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);
	int wm = inotify_add_watch(in, mev, IN_MODIFY);
	int wc = inotify_add_watch(in, cev, IN_MODIFY);
	if (wm < 0 || wc < 0) {
		printf("%lld watch failed: %s\n", ns(), strerror(errno));
		return 1;
	}
	printf("%lld watching %s\n", ns(), argv[1]);
	char last_m[4096] = "", last_c[4096] = "", m[4096], c[4096];
	int gone = 0;
	while (gone < 2) {
		struct pollfd p = { in, POLLIN, 0 };
		int pr = poll(&p, 1, 1);
		if (pr > 0) {
			char buf[4096] __attribute__((aligned(8)));
			ssize_t r;
			while ((r = read(in, buf, sizeof buf)) > 0) {
				for (char *q = buf; q < buf + r;) {
					struct inotify_event *e = (struct inotify_event *)q;
					long long t = ns();
					int rm = slurp(mev, m, sizeof m), rc = slurp(cev, c, sizeof c);
					printf("%lld inotify wd=%s mask=0x%x | memory.events%s%s | cgroup.events%s%s\n", t,
					       e->wd == wm ? "memory.events" : e->wd == wc ? "cgroup.events" : "?", e->mask,
					       rm ? " err=" : ": ", rm ? strerror(-rm) : m, rc ? " err=" : ": ", rc ? strerror(-rc) : c);
					if (e->mask & IN_IGNORED)
						gone++;
					q += sizeof *e + e->len;
				}
			}
		}
		int rm = slurp(mev, m, sizeof m), rc = slurp(cev, c, sizeof c);
		if (rm || rc) {
			printf("%lld poll: memory.events%s cgroup.events%s\n", ns(), rm ? " gone" : " ok", rc ? " gone" : " ok");
			if (rm && rc)
				break;
			continue;
		}
		if (strcmp(m, last_m) || strcmp(c, last_c)) {
			printf("%lld poll change | memory.events: %s| cgroup.events: %s\n", ns(), m, c);
			strcpy(last_m, m);
			strcpy(last_c, c);
		}
	}
	printf("%lld done\n", ns());
	return 0;
}
