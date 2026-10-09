# Reproduce the dimple line-scan results

No OSC account or access to the author's computer is required. The examples use Bash on
macOS/Linux, or Bash/WSL with Linux containers on Windows. Use the immutable
commit linked in the [result update in Discussion #4](https://github.com/Driver066/g4optics/discussions/4)
when reproducing this particular publication.

```sh
git clone --branch codex/dimple-study https://github.com/Driver066/g4optics.git
cd g4optics
git checkout --detach 45ed24bbe697606afa88184b689905aa732d0e73
```

All commands below use **new output directories**. Existing runs are preserved
and are not automatically retried or overwritten.

## 1. View or regenerate the figures

The [English results](results/line-v1/README.en.md), PNG/SVG figures and exact
tables are already in the repository. Rendering them again does not run Geant4.

For a small Python environment, use Python 3.10 (the tested version):

```sh
python3.10 -m venv .venv-dimple-review
. .venv-dimple-review/bin/activate
python -m pip install -r tools/dimple_study/requirements-reproduce.txt
python -B tools/dimple_study/plot_line.py \
  --result tools/dimple_study/results/line-v1/result.json \
  --output outputs/replayed-figures
```

Alternatively, build the portable Docker environment once:

```sh
docker build -f tools/dimple_study/docker/Dockerfile \
  -t g4-dimple-reproduce:11.4.2 tools/dimple_study
docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  --mount type=bind,source="$PWD",target=/work \
  g4-dimple-reproduce:11.4.2 \
  python3 -B tools/dimple_study/plot_line.py \
  --result tools/dimple_study/results/line-v1/result.json \
  --output outputs/docker-replayed-figures
```

The Docker build requires internet access for the public base image and Python
packages. Its base is the pinned Geant4 11.4.2 multi-architecture image recorded
in [docker/images.json](docker/images.json); it supports linux/arm64 and
linux/amd64. This recipe builds a new local image, rather than attempting to
pull the original author's private image ID. The direct analysis dependencies
are pinned; record `pip freeze` and your new image ID for your run.

## 2. Recompute statistics without a new simulation

### From the 5,200 public event rows

In the Python environment above:

```sh
python -B tools/dimple_study/reanalyze_events.py --output outputs/csv-replay
```

Or run the same script through the Docker command above. It verifies the
published input hashes and all `(task,event_id)` identities, performs the
100,000-replicate analysis with the published seed, and compares the new
estimates/intervals to `result.json`. Success is `replay-report.json` with
`status: passed`. It also saves a new numeric result and bootstrap arrays.
This route cannot recompute the spatial hit/centroid diagnostics absent from
the CSV, and is not a replacement for the original ROOT audit.

### From the original ROOT files

The repository includes [raw-campaign.tar.gz](results/line-v1/raw-campaign.tar.gz)
(2,842,388 bytes). Its SHA256 is
`c00ab31ac475846a508d0f6e8e00ac2b849355d9a5c2ce2c9bc9a06f5154f7dc`.
The archive contains all 52 original ROOT files, macros, summaries, logs, RNG
states, receipts, audits and the source/runtime records required by the analyzer.
Archive metadata are normalized; the 406 member-file contents are unchanged.
The archived executable is retained as a provenance artifact; rebuilding and
running the simulation is handled separately below.

```sh
mkdir -p outputs/raw-original
tar -xzf tools/dimple_study/results/line-v1/raw-campaign.tar.gz \
  -C outputs/raw-original
python -B tools/dimple_study/analyze_line.py \
  --batch outputs/raw-original/line-v1 \
  --output outputs/raw-replay \
  --manifest-sha256 f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405
python -B tools/dimple_study/validate_line_analysis.py \
  --batch outputs/raw-original/line-v1 --analysis outputs/raw-replay \
  --analysis-script tools/dimple_study/analyze_line.py \
  --manifest-sha256 f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405 \
  --output outputs/raw-replay/independent-validation.json
```

The original 161 MB bootstrap archive is not required for these routes: it is
regenerated. Exact array/file bytes can depend on the recorded NumPy and output
environment; the statistical comparisons use stated numerical tolerances.

## 3. Build the model and run new simulated events

Use the Docker image built in section 1. Install the official Geant4 data in a
new dedicated volume (network access required for the first installation):

```sh
docker volume create g4-dimple-data-11.4.2
docker run --rm \
  --mount type=bind,source="$PWD",target=/work,readonly \
  --mount type=volume,source=g4-dimple-data-11.4.2,target=/g4data \
  g4-dimple-reproduce:11.4.2 \
  bash tools/dimple_study/docker/install_data.sh install
mkdir -p outputs
docker image inspect g4-dimple-reproduce:11.4.2 > outputs/reproduction-image.json
```

The installer checks the official versioned archive checksums, creates a file
checksum receipt, and refuses to overwrite a nonempty unrecognized data directory.
The runner then requires the data receipt to match the published campaign and
checks all data files. Simulation mounts are read-only for the datasets.

Start with the **10-event qualification** (five centre events per geometry):

```sh
docker run --rm --network none --cpus 1 --memory 4g \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  --mount type=bind,source="$PWD",target=/work \
  --mount type=volume,source=g4-dimple-data-11.4.2,target=/g4data,readonly \
  g4-dimple-reproduce:11.4.2 bash -c '
    set -e
    python3 -B tools/dimple_study/reproduce_line.py prepare \
      --output outputs/source-smoke --mode smoke --image-metadata outputs/reproduction-image.json
    python3 -B outputs/source-smoke/runtime/tools/dimple_study/reproduce_line.py build \
      --output outputs/source-smoke
    python3 -B outputs/source-smoke/runtime/tools/dimple_study/reproduce_line.py run \
      --output outputs/source-smoke
  '
```

The program verifies the current C++/CMake file set against the published
simulation source hashes, then builds a new executable from a frozen copy.
No historical binary, author's `optical-v1` directory or Git metadata is needed.
Each task checks geometry, actual source, counts, birth/start accounting and
exit status. Failure produces `STOP.json` and stops without retries.

For the complete **52-task / 5,200-event** design, use the same Docker mounts
and run these three commands in that container:

```sh
python3 -B tools/dimple_study/reproduce_line.py prepare \
  --output outputs/source-full --mode full --image-metadata outputs/reproduction-image.json
python3 -B outputs/source-full/runtime/tools/dimple_study/reproduce_line.py build \
  --output outputs/source-full
python3 -B outputs/source-full/runtime/tools/dimple_study/reproduce_line.py run \
  --output outputs/source-full
```

Full mode regenerates all 52 original macros byte-for-byte and retains the
original registered seed pairs. It records new build/image/runtime identities;
cross-architecture or cross-platform bitwise event equality is not promised.
The original ARM64 serial scan took about 42 minutes, excluding environment
preparation; this is a reference measurement, not a wall-time guarantee.

## What was actually checked before publication

A new source export with **no `.git`, no original outputs, and no old binary**
was mounted into a separately built image from the public recipe. On linux/arm64,
Python 3.10.12 / NumPy 2.0.2 / uproot 5.6.9 / Matplotlib 3.9.4:

- All 22 preparation/audit/mathematical tests passed.
- All three published figures were regenerated and visually inspected.
- CSV replay recomputed 1,711 numerical values with maximum difference **0**.
- The distributed ROOT archive was extracted and fully reanalyzed; comparison
  to 2,101 published values had maximum difference **0**, and independent
  validation of the rebuilt analysis passed.
- The model was rebuilt from source. Its new executable matched the original
  ARM64 executable hash in this tested environment; ten qualification events
  passed and 60 checked event values matched the corresponding published events
  exactly.

The qualification reused an existing checksum-verified data volume, mounted
read-only. A second full 5,200-event simulation, fresh dataset downloads and an
amd64 run were **not** repeated in this publication check. The full macro plan
was prepared and checked; the two architectures' public base manifests were
verified. See [reproduction-validation.json](reproduction-validation.json) for
the recorded evidence and limits.
