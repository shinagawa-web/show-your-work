# g1-heap-not-returned

Runs the same Java program on Eclipse Temurin JDK 8, 11, 17, 21 and 25 with the default garbage collector, in a container limited to 2 GB of memory and 2 CPUs, and records how much heap the JVM keeps committed and how much memory the container holds after a load peak. The program allocates short-lived objects at a high rate for a fixed time (the peak) and then, for a fixed time, either keeps allocating at a lower rate or stops allocating.

The conditions are in `conditions.tsv`: the JDK version, extra `docker run` and `java` options, what the program does after the peak, whether a run is the full length or only long enough to read the GC the JVM selected at startup, and the job that runs it.

## Run

`.github/workflows/g1-heap-not-returned.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job is named after the section of the article it supplies, and runs one condition from `conditions.tsv` three times with `./run-all.sh <section>:<name>`.

| Job | What it runs |
|---|---|
| `s1 (<name>)` | Sections 1 and 4: JDK 8, 11, 17, 21 and 25 with the default GC |
| `s2 (<name>)` | Section 2: the GC selected at startup with 1 GB of memory or 1 CPU |
| `s3 (<name>)` | Section 3: JDK 25 with a periodic GC interval, with and without load after the peak |
| `s5 (<name>)` | Section 5: JDK 11 with `-XX:+UseParallelGC` |
| `report` | Merges the results of the other jobs into `results/summary.md` |

`results/summary.md` goes to the job summary of each job, with one part per section and each value labelled with the job that produced it. Each job uploads `results/` as an artifact named `g1-heap-not-returned-<section>-<name>`, and the `report` job writes the merged summary to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `g1-heap-not-returned-summary` artifact. A job fails when a run did not produce what the condition needs (no GC selection in the output, or the program did not finish).

## What is recorded

Each run writes `results/<name>/run<k>/`:

- `settings.txt`: the image, `docker run` options, `java` options and program arguments
- `java-version.txt`: the output of `java -version` in the image
- `stdout.txt`: the JVM flags (`-XX:+PrintCommandLineFlags`), the program's phase changes, and once a second the committed size of each heap memory pool as the JVM reports it
- `gc.log`: the GC log. JDK 11 and later use `-Xlog:gc*` with `gc+ergo+heap=debug`, and JDK 17 and later also `gc+periodic=debug` (JDK 11 has no such tag). JDK 8 uses `-XX:+PrintGCDetails -XX:+PrintAdaptiveSizePolicy -XX:AdaptiveSizePolicyOutputInterval=1`
- `memory.tsv`: `memory.current` and `anon` from `memory.stat` of the container's cgroup, read once a second from the host
- `cgroup.txt`: `memory.max`, `memory.swap.max` and `cpu.max` of the container's cgroup when it started
- `summary.json`: the values `scripts/summarize.py` reads from the files above

The program is `app/Alloc.java`. Its arguments (allocation rates, object size and object lifetime) are set in `scripts/run.sh`. Each object is kept in a ring buffer for a fixed time, so the amount of live data follows the allocation rate.

## Pinned versions

`scripts/versions.env`.
