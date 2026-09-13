# InSARHub test system

Four tiers, each answering a different question. They are separate because they
have different costs and different failure meanings: tier 2 failing means the
code is wrong, tier 3 failing might mean ASF is down.

| Tier | Question | Needs | Runtime |
|---|---|---|---|
| 1 `tier1_install` | Is this installation wired up correctly? | an installed InSARHub | seconds |
| 2 `tier2_basic` | Do imports, the CLI and the GUI behave as promised? | nothing | ~1 min |
| 3 `tier3_e2e` | Does each workflow actually run on real data? | backends, credentials, network | hours |
| 4 `tier4_regression` | Are previously-fixed bugs still fixed? | nothing | seconds |

## Running

```bash
pip install -e '.[test]'

pytest                      # tiers 1, 2 and 4 -- the everyday loop
pytest -m install           # one tier
pytest -m "basic or regression"
pytest -m e2e               # tier 3, opt-in (excluded by addopts)
pytest -m e2e -k isce2      # one workflow
```

Tier 3 is excluded from a bare `pytest` on purpose. It downloads real Sentinel-1
data and runs real processing, so it is a deliberate act, never a side effect of
typing `pytest`.

```bash
# Keep downloaded scenes between runs instead of re-fetching several GB.
INSARHUB_E2E_DIR=/data/insarhub-e2e pytest -m e2e
```

## Tier 1 — install

**Tier 1 does not install anything.** It runs against whatever InSARHub is
importable and verifies the result — that distinction matters, because something
else has to do the installing:

| | What installs | What verifies |
|---|---|---|
| Locally | `python scripts/test_install.py --flavor all` | tier 1, inside each env it builds |
| In CI | the `install` / `install-gmtsar` jobs | tier 1, inside each env they build |

`scripts/test_install.py` is the real thing: for each flavour in
`docs/quickstart/install.md` it builds a wheel, creates a fresh conda
environment, installs the backend stack, installs the wheel, and runs
`pytest -m install` inside the result. Run it before a release tag.

```bash
python scripts/test_install.py --flavor base                  # ~5 min
python scripts/test_install.py --flavor isce2,isce3-dolphin   # a subset, each in its own env
python scripts/test_install.py --flavor all                   # base + isce2 + isce3-dolphin
python scripts/test_install.py --flavor gmtsar                # source build, ~30 min
python scripts/test_install.py --flavor base --from-conda-forge   # the published package
```

`--flavor` takes a comma-separated list, and every flavour named gets its own
environment. The plan is printed before anything is installed:

```
Environment plan (one per flavour):
  isce2            -> insarhub-install-isce2-312
  isce3-dolphin    -> insarhub-install-isce3-dolphin-312
```

`--from-conda-forge` installs the *released* `insarhub` instead of a local
wheel, which is the only way to check what users actually get.

**Every flavour gets its own environment**, named `insarhub-install-<flavour>-<py>`.
This is not cosmetic: installing two flavours into one env would let ISCE2's
`numpy<2` pin, COMPASS's pin and MintPy's requirements resolve against each
other, so a pass would say nothing about whether either flavour installs on its
own — the only question the script exists to answer. The driver prints its
environment plan before doing anything and refuses to run if two flavours would
share a name. CI does the same: each matrix entry uses
`insarhub-<flavour>-py<version>`, never a shared `test-env`.

### Verified install matrix

Results from real installs via `scripts/test_install.py` on Linux x86_64,
each flavour in its own conda environment:

| Flavour | 3.11 | 3.12 | 3.13 |
|---|---|---|---|
| `base` | PASS | PASS | PASS¹ |
| `isce2` | PASS | PASS | blocked — no py3.13 build on conda-forge |
| `isce3-dolphin` | PASS | PASS | blocked — COMPASS pins `scipy <1.13`² |
| `gmtsar` | PASS (installer picks the Python) | | |

¹ Only with `--ignore-requires-python`. The *code* runs on 3.13; what stops a
normal install is `requires-python = ">=3.11,<3.13"` in pyproject.

² Worth knowing because metadata alone gives the wrong answer here. COMPASS is
a `noarch` package declaring `python >=3.9`, which looks like it supports 3.13 —
but it pins `scipy >=1.0,<1.13`, and the lowest scipy with a py3.13 build is
1.14.1. The solve fails. A noarch package can still be pinned out of a Python
version by its dependencies, which is why this table comes from running the
installs rather than reading package indexes.

So the `<3.13` cap is justified: two of the four flavours cannot install on
3.13 today. To re-check after upstream moves:

```bash
python scripts/test_install.py --flavor isce3-dolphin --python 3.13 --ignore-requires-python
```

### GMTSAR is the odd one out

Every other flavour creates an environment and installs a backend into it.
GMTSAR inverts that, and both `scripts/test_install.py` and the CI job follow
`docs/quickstart/install.md` exactly:

1. `python3 gmtsar/python/install.py --system conda-linux-full` — **the GMTSAR
   installer creates the conda env itself, named `gmtsar`**
2. `conda activate gmtsar` → install MintPy and InSARHub *into that env*
3. `export GMTSAR=$(pwd)` and put `$GMTSAR/bin` on `PATH`

So there is no env of ours to create, and the tests must run inside `gmtsar` —
its tools link against that env's GMT libraries and will not run from another
one. It is also **not a conda-forge package** (the name exists there but ships
zero files), which is why it is a source build and why it is excluded from
`--flavor all`: it takes about half an hour.

Because that env may be one you built for real work, `scripts/test_install.py`
**refuses to touch an existing `gmtsar` env** and never removes it. Pass
`--reuse-gmtsar-env` to install into one anyway.

Two consequences worth knowing before you run it:

- The env is ~4 GB and is **not** cleaned up by `--keep`'s absence, by design.
  Remove it yourself with `conda env remove -n gmtsar` when you are done.
- The GMTSAR *source tree* (which `$GMTSAR` and `$PATH` point into) is cloned
  into a scratch directory. Delete that and the env's InSARHub still imports but
  the GMTSAR tools vanish from `PATH`. For a GMTSAR you intend to keep, follow
  the docs by hand and clone somewhere permanent.

Verified on Linux x86_64: the build takes ~30 min, `install.py --system
conda-linux-full` does create the `gmtsar` env, and the installed driver is
`p2p_processing` — **not** `p2p_processing.csh`, which is why `has_gmtsar()`
accepts both spellings.

Running tier 1 by itself (`pytest -m install`) only describes the environment you
are already in.

It checks the things that break *packaging* rather than logic — entry points
that point at a function that does not exist, data files present in git but
absent from the wheel, a stale install whose version disagrees with the source.

`test_backend_report` prints a capability table and never fails.
`INSARHUB_EXPECT_BACKENDS=isce2,mintpy` turns that into an assertion, so a CI job
that was supposed to install ISCE2 fails loudly instead of silently skipping
every ISCE2 test for the rest of time.

## Tier 2 — basic

The everyday safety net, and the tier to extend first.

- `test_imports.py` — every module imports, in-process and in a fresh
  interpreter. This matters more here than in most projects: registration is an
  import side effect, so a processor that fails to import does not raise, it
  silently disappears from the registry and from the GUI.
- `test_cli_contract.py` — the full command tree, every flag, every `--help`.
- `test_api_contract.py` — every route the GUI calls, checked from both sides:
  the routes the backend serves, and the `/api/...` literals the frontend
  actually references. A rename on either side fails here instead of 404-ing in
  someone's browser.
- `test_registry_and_config.py` — registry integrity, legacy aliases, and the
  config metadata both front ends are generated from.

Tier 2 must stay hermetic. No network, no backends, no writes outside
`tmp_path`. The root `conftest.py` enforces the last one by `chdir`-ing every
test into `tmp_path`.

## Tier 3 — end-to-end

One test per shipped workflow, each running search → download → process →
analyze through the real `insarhub` console script, then asserting the GUI can
reload what the CLI produced.

| Workflow | Downloader → Processor → Analyzer | Needs |
|---|---|---|
| `hyp3_s1` | S1_SLC → Hyp3_S1 → Hyp3_Mintpy_SBAS | MintPy, HyP3 credits |
| `isce2_s1` | S1_SLC → ISCE2_S1 → ISCE2_Mintpy_SBAS | ISCE2, MintPy |
| `gmtsar_s1` | S1_SLC → GMTSAR_S1 → GMTSAR_Mintpy_SBAS | GMTSAR, MintPy |
| `isce3_burst` | S1_Burst → ISCE3_Burst → ISCE3_Dolphin_S1_PL | ISCE3, COMPASS, dolphin |
| `isce3_nisar` | NISAR_GSLC → ISCE3_NISAR → ISCE3_Dolphin_NISAR_PL | ISCE3, dolphin |

The inputs are the smallest that still exercise every stage — a ~10 km AOI over
Ridgecrest and a 6-week window, giving 3–4 scenes. Three scenes is the floor:
two produce a single interferogram, which every SBAS inversion rejects as an
underdetermined network, so the run would pass without testing anything.

A workflow whose backend or credentials are missing **skips with the reason**,
because almost no machine has ISCE2 *and* GMTSAR *and* ISCE3 at once.

## Tier 4 — regressions

One file per previously-fixed bug. See `tier4_regression/README.md` for the
convention and for how known-but-unfixed bugs are recorded.

## Conventions

- **A test that cannot fail is worse than no test.** Assert against a value that
  is not the default — most CLI dests have one, so `-N Hyp3_S1` →
  `processor_name == "Hyp3_S1"` passes whether or not the flag is wired up.
- **Skip, don't fail, for a missing backend.** A developer without GMTSAR should
  see "skipped: gmtsar not installed", never a red suite they cannot fix.
- **Known bugs are `xfail(strict=True)`**, never a deleted assertion. Strict
  means the test fails once the bug is fixed, so the marker cannot outlive it.
- **Markers come from the directory**, applied in `conftest.py`. A new file in
  `tier4_regression/` is a regression test automatically.
