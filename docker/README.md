# Container images

InSARHub can run a processor or analyzer **inside** a container instead of
requiring the SAR toolchain on the host — see
[docs/advanced/container.md](../docs/advanced/container.md) for how that
re-invocation works. This directory builds the official images.

## `dev/` vs `release/`

The two trees build the *same four images* from the *same recipes*. The only
difference is where InSARHub itself comes from, and that difference is the whole
point:

|  | `dev/` | `release/` |
|---|---|---|
| InSARHub source | your working tree (`COPY src`) | conda-forge, pinned |
| Version | whatever is checked out, uncommitted included | `--build-arg INSARHUB_VERSION=x.y.z` |
| Tag | `:dev` | `:x.y.z` |
| Third-party pins | tracks moving refs (GMTSAR `master`, MintPy `main`) | pinned tags / released builds |
| Reproducible | no — rebuilds follow your tree | yes — rebuilds from this file alone |
| Use for | iterating on InSARHub itself | what users actually pull |

A `dev/` image is the right thing when you are changing InSARHub and want the
change in a container without publishing anything. A `release/` image is the
right thing for everything else, because a user who reports a bug against
`:0.4.0` must be running code you can check out.

`dev/Dockerfile.isce2-mintpy` additionally swaps MintPy for its **git
development version**, to test unreleased MintPy changes ahead of time. The
release image deliberately does not.

## The four images

| Image | Stack | Serves |
|---|---|---|
| `insarhub-base` | InSARHub + MintPy | `Hyp3_S1` → `Hyp3_Mintpy_SBAS` |
| `insarhub-isce2-mintpy` | ISCE2 `topsStack` + MintPy | `ISCE2_S1` → `ISCE2_Mintpy_SBAS` |
| `insarhub-gmtsar-mintpy` | GMTSAR (from source) + MintPy | `GMTSAR_S1` → `GMTSAR_Mintpy_SBAS` |
| `insarhub-isce3-dolphin` | ISCE3 / COMPASS + dolphin | `ISCE3_Burst`, `ISCE3_NISAR` → dolphin analyzers |

Each config's `container_default` names one of these. All builds run from the
**repo root**, not from this directory:

```bash
# dev
docker build -f docker/dev/Dockerfile.base -t ghcr.io/jldz9/insarhub-base:dev .
docker push ghcr.io/jldz9/insarhub-base:dev

# release
docker build -f docker/release/Dockerfile.base \
    --build-arg INSARHUB_VERSION=0.4.0 \
    -t ghcr.io/jldz9/insarhub-base:0.4.0 .
docker push ghcr.io/jldz9/insarhub-base:0.4.0
```

## Release order

A `release/` image installs InSARHub from conda-forge, so it can only be built
**after** that version exists there. The full chain:

1. Tag `vX.Y.Z`, then build and **upload the sdist and wheel to PyPI**.
   `.github/workflows/publish.yml` builds and smoke-tests the wheel on every
   platform but does *not* upload it, so this step is manual today.
2. The conda-forge feedstock bot opens a PR from the PyPI sdist → merge →
   **conda-forge**.
3. Build and push the four `release/` images with
   `--build-arg INSARHUB_VERSION=X.Y.Z`.
4. Point the `container_default` fields in
   `src/insarhub/config/defaultconfig.py` at `:X.Y.Z`.

Steps 1–2 are what gate everything; a `dev/` image needs none of them.

### Patch releases may reuse the series' images

Steps 3–4 are **optional for a patch release**. Because a `release/` image can
only be built once conda-forge has the version, `:X.Y.Z` does not exist on the
day `vX.Y.Z` is tagged — so requiring it would mean shipping a release whose
`container_default` names an image nobody can pull. A patch release may instead
keep pointing at the series' existing images (0.4.1 ships pointing at
`:0.4.0`), and `test_container_default_tag_is_this_release_series` allows
exactly that: a patch-level lag inside one `X.Y` series, never across a minor or
major bump, and never a tag ahead of `__version__`.

Do run steps 3–4 for a patch anyway when the release changes code that executes
*inside* a container — anything under `processor/`, `analyzer/` or the shared
core. A fix confined to the web API, the CLI or the docs does not, since the
container only ever runs the processor/analyzer re-invocation.

Each release Dockerfile asserts `insarhub.__version__ == INSARHUB_VERSION` at
build time, so a feedstock that has not caught up fails the build here rather
than shipping a mislabelled image.

---

## Shared rationale

Hard-won details that several of these Dockerfiles depend on. They are recorded
once here rather than repeated in eight files.

### Why no ENTRYPOINT

None of these images set `ENTRYPOINT` or `CMD`. `wrap_container_cmd()`
(`src/insarhub/utils/container.py`) already runs
`docker run ... <image> bash -c '<command>'` itself. An `ENTRYPOINT` would get
that `CMD` appended to it (`bash -c` + `bash -c '<command>'`), launching a nested
interactive bash that just exits on EOF stdin without ever running the command.

### Keeping numpy below 2

InSARHub, ISCE2, MintPy and COMPASS's s1reader all need numpy < 2 — their
compiled extensions break against the 2.x ABI. Getting that pin to *stick*
needs more than declaring it:

Solving `isce2 + insarhub + "numpy<2.0"` in **one** mamba call let the solver
quietly violate the ceiling anyway (numpy landed at 2.5.1), apparently because
conda-forge's insarhub build wants numpy ≥ 2.0 and the joint solve traded the
pin away to satisfy it. Installing insarhub first, then isce2 as its own
follow-up call — **each with an explicit `numpy<2.0`** — keeps each solve small
enough that the pin holds. Every other combination tried let numpy drift.

So: separate `conda install` calls, explicit pin on each, and a build-time
`assert numpy.__version__ < '2'` so a regression fails the build.

### Two meanings of ISCE_HOME

InSARHub discovers ISCE2 through two functions that want *different* bases for
the same conda env:

- `_check_isce2()` wants `ISCE_HOME` to be the isce **package** dir
  (`.../site-packages/isce`) — ISCE2's own documented convention, since it looks
  for `$ISCE_HOME/applications/topsApp.py`.
- `_find_topsstack()` wants `ISCE_HOME` to be the conda **env root** — it looks
  for `$ISCE_HOME/share/isce2/topsStack/stackSentinel.py`, where conda-forge's
  isce2 package actually ships topsStack.

Symlinking `applications/` up to the env root satisfies both at once with
`ISCE_HOME` set to the env root.

### The libstdc++ trap

With `LD_LIBRARY_PATH` empty, the loader picks the **system**
`/lib/x86_64-linux-gnu/libstdc++.so.6` (6.0.28), which lacks `GLIBCXX_3.4.29`.
Importing pandas — pulled in by MintPy and by dem_stitcher for the DEM download
— then dies with *"GLIBCXX_3.4.29 not found"*, stalling the `dem` stage. The
conda env's libstdc++ has the symbol, so every image sets
`LD_LIBRARY_PATH=/opt/conda/envs/<env>/lib`. GMT and the GMTSAR C binaries
(built with the same conda toolchain) work fine against it too.

### The GHCR label

`LABEL org.opencontainers.image.source="https://github.com/jldz9/InSARHub"`
links the image to the repo on GHCR even when pushed **manually** from a
command line rather than by a GitHub Actions workflow, which would link it
automatically. See
[GitHub's docs](https://docs.github.com/en/packages/learn-github-packages/connecting-a-repository-to-a-package).

### Why GMTSAR is built from source

conda-forge has a `gmtsar` package name that contains **zero files**, so
`conda install gmtsar` silently provides nothing. Both images therefore clone
the repo and run GMTSAR's own
`gmtsar/python/install.py --system conda-linux-full`, which provisions the
toolchain + GMT into a conda env named `gmtsar` and builds into `/opt/gmtsar`.
That path is LINUX x86_64 ONLY — the installer refuses anything else.
