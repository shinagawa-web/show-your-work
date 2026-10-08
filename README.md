# show-your-work

Each folder is one verification. `./run-all.sh` in the folder runs all of it on a Linux host and writes `results/`, which is not committed: CI keeps it as an artifact of the run and puts the summary in the job summary. On macOS, `scripts/vm-run.sh <folder> [args ...]` runs the same inside the Lima VM defined by `<folder>/lima.yaml`.

Results are read from a run, not written into READMEs or pull requests, because every run produces them again.
