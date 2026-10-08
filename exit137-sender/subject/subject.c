#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void msleep(long ms)
{
	struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
	while (nanosleep(&ts, &ts) != 0)
		;
}

int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc == 4 && strcmp(argv[1], "exit") == 0) {
		printf("pid %d started\n", (int)getpid());
		msleep(atol(argv[3]));
		printf("returning %s\n", argv[2]);
		return atoi(argv[2]);
	}
	if (argc < 6 || strcmp(argv[1], "alloc") != 0) {
		fprintf(stderr, "usage: %s alloc <MiB|-1> <step_MiB> <step_ms> <start_ms> [marker]\n"
				"       %s exit <code> <delay_ms>\n", argv[0], argv[0]);
		return 2;
	}
	long total = atol(argv[2]), step = atol(argv[3]), step_ms = atol(argv[4]);
	printf("pid %d started\n", (int)getpid());
	msleep(atol(argv[5]));
	if (argc >= 7) {
		if (access(argv[6], F_OK) == 0) {
			printf("marker %s exists, not allocating\n", argv[6]);
			total = 0;
		} else {
			int fd = open(argv[6], O_CREAT | O_WRONLY, 0644);
			if (fd >= 0)
				close(fd);
			printf("marker %s created\n", argv[6]);
		}
	}
	long done = 0;
	while (total < 0 || done < total) {
		size_t n = (size_t)step << 20;
		char *p = malloc(n);
		if (!p) {
			printf("malloc failed at %ld MiB\n", done);
			break;
		}
		memset(p, 1, n);
		done += step;
		if (done % 64 == 0)
			printf("allocated %ld MiB\n", done);
		if (step_ms)
			msleep(step_ms);
	}
	printf("holding %ld MiB\n", done);
	for (;;)
		pause();
}
