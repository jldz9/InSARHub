# Container Execution

The local processors and analyzers (ISCE2, GMTSAR, ISCE3, MintPy) each need heavy SAR software installed. Rather than requiring that on the host, set `container` (CLI `--container`, or the config field / GUI "Run in Container" checkbox) to a `.sif`/Apptainer image or a Docker image reference, and InSARHub runs the pipeline **inside** that image instead.

## Re-invocation, not a library call

The host process does *not* import ISCE2/GMTSAR/dolphin. When `container` is set it re-invokes **the same `insarhub` command** inside the image (`_reinvoke_via_container` for processors, `_run_via_container` for analyzers): roughly

```
docker run --rm -e INSARHUB_CONTAINER_CHILD=1 -v <workdir>:<workdir> <image> \
    insarhub <processor|analyzer> ... <same args>
```

The workdir is **bind-mounted at the identical path**, so every output the child writes lands on the host exactly where a native run would put it — the host and the container agree on all paths.

## Why it never nests (`INSARHUB_CONTAINER_CHILD`)

The child is stamped with the env var `INSARHUB_CONTAINER_CHILD=1`. Every place that decides whether to containerize checks *both* `config.container` **and** the absence of that var, so:

- **host** (`container` set, var unset) → launch the container (once), and
- **child** (var set) → run the actual work **directly**, even though `container` is still set in its config.

Without that check the child — which still sees `container` in the persisted config — would try to launch another `docker run` (docker-in-docker), but the image has no docker; that was the classic *"docker not found"* failure. The same guard also makes the child run **synchronously** in-process (no fork+detach), so `docker run --rm` doesn't tear the container down before the pipeline finishes.

## Host vs. image requirements

The **host** running the CLI/app needs a container runtime (`docker`, or `apptainer`/`singularity` for a `.sif`) on its `PATH`. The **image** does not — it only needs `insarhub` installed alongside the SAR stack. The `docker/Dockerfile.*` files build the official images:

| Image | Stack |
|---|---|
| `insarhub-isce2-mintpy` | ISCE2 `stackSentinel` + MintPy |
| `insarhub-gmtsar-mintpy` | GMTSAR + MintPy |
| `insarhub-isce3-dolphin` | ISCE3 / COMPASS + dolphin |
| `insarhub-base` | InSARHub + MintPy (HyP3 analyzer) |

## Persistence

A `container` you choose **is** written to the workdir's `insarhub_config.json`, so a later `retry`/`refresh`/`cancel` re-enters the same image without re-passing it. A bare `--container` (no value) resolves to the processor/analyzer's `container_default` — a fixed per-backend suggestion (the image the GUI's "Run in Container" checkbox pre-fills) that is itself **never** persisted; only your actual `container` choice is.

## Usage

=== "CLI"

    Pass `--container <path-or-image>` on each call. It is a per-invocation flag on the CLI (like `--dry-run`), so repeat it on `submit`/`refresh`/`retry`/`watch`/`cancel`:

    ```bash
    insarhub processor submit  -N ISCE2_S1 -w /data/p100_f466 \
        --bbox 33.0 38.0 -120.0 -115.0 \
        --container ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    insarhub processor refresh -N ISCE2_S1 -w /data/p100_f466 \
        --container ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    ```

    Analyzers take `--container` **after** the `run` action; a bare `--container` uses the default image:

    ```bash
    insarhub analyzer -N ISCE2_Mintpy_SBAS -w /data/p100_f466 run --container
    ```

=== "Python API"

    Set `container` on the config; `submit()` / `run()` re-invoke inside it:

    ```python
    cfg = ISCE2_S1_Config(
        workdir='/data/p100_f466',
        bbox=[37.74, 38.00, -113.05, -112.68],
        container='ghcr.io/jldz9/insarhub-isce2-mintpy:dev',
    )
    processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
    processor.submit()   # runs inside the container
    ```

## HPC mode

When both `container` and `hpc_mode` are set, only each **stage's child jobs** run inside the container; the sbatch manager scaffolding (submit/poll loop, chain-submission) stays on the host. See [HPC (SLURM)](hpc.md).
