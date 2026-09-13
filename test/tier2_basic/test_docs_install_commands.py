"""Tier 2 -- the install automation still matches the install docs.

`scripts/test_install.py` and the CI jobs claim to follow
`docs/quickstart/install.md`. Nothing enforced that, and the two drifted once
already: the automation created a conda environment and built GMTSAR into it,
while the docs have GMTSAR's own installer create the env named "gmtsar" and
InSARHub installed into that.

That drift is invisible -- both versions "work" until someone follows the docs
by hand and gets a different result from CI. These tests pin the handful of
commands that define each procedure, so changing one without the other fails
here.

They read the docs as text on purpose. The point is not to test markdown; it is
that the doc is the specification, and the automation is the implementation.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_DOC = REPO / "docs" / "quickstart" / "install.md"
DRIVER = REPO / "scripts" / "test_install.py"
WORKFLOW = REPO / ".github" / "workflows" / "test.yml"
PYPROJECT = REPO / "pyproject.toml"


@pytest.fixture(scope="module")
def doc() -> str:
    if not INSTALL_DOC.is_file():
        pytest.skip("install docs not present")
    return INSTALL_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def driver() -> str:
    return DRIVER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


# ── GMTSAR ───────────────────────────────────────────────────────────────────

def test_docs_still_build_gmtsar_from_source(doc):
    """If GMTSAR ever ships a real conda package, this is where we find out.

    conda-forge has a `gmtsar` name that contains zero files, so
    `conda install gmtsar` silently fails to provide anything -- which is why
    both the docs and the automation build from git.
    """
    assert "github.com/gmtsar/gmtsar.git" in doc
    assert "install.py --system conda-linux-full" in doc


def test_docs_still_say_the_installer_creates_the_env(doc):
    """The whole shape of the GMTSAR automation depends on this sentence."""
    assert re.search(r"creates a conda env(ironment)? named [\"'`]?gmtsar", doc), (
        "install.md no longer says GMTSAR's installer creates the 'gmtsar' env. "
        "If that changed, scripts/test_install.py and the install-gmtsar CI job "
        "both need restructuring -- they install into that env rather than "
        "creating one."
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "github.com/gmtsar/gmtsar.git",
        "gmtsar/python/install.py",
        "--system",
        "conda-linux-full",
    ],
)
def test_driver_runs_the_documented_gmtsar_commands(driver, fragment):
    """The driver builds argv as a list, so the documented one-liner appears as
    separate tokens rather than a single string -- each is checked on its own."""
    assert fragment in driver, (
        f"scripts/test_install.py no longer references `{fragment}`, which the "
        "install docs specify"
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "github.com/gmtsar/gmtsar.git",
        "install.py --system conda-linux-full",
    ],
)
def test_ci_runs_the_documented_gmtsar_commands(workflow, fragment):
    assert fragment in workflow


def test_driver_does_not_create_the_gmtsar_env_itself(driver):
    """Creating it first is precisely the bug this guards against."""
    assert not re.search(r'"create",\s*"-y",\s*"-n",\s*GMTSAR_ENV', driver), (
        "the driver creates the gmtsar env itself; the documented procedure has "
        "GMTSAR's installer create it"
    )


def test_gmtsar_env_is_never_auto_removed(driver):
    """That env may be one the user built for real work."""
    assert "--reuse-gmtsar-env" in driver
    assert "never auto-removed" in driver or "never removes it" in driver.lower()


def test_gmtsar_is_not_built_into_a_temp_directory(driver):
    """The GMTSAR build must default to durable storage.

    The conda env holds no copy of the tools -- it finds them only through
    $GMTSAR/bin on PATH. Building into tempfile.mkdtemp() (i.e. /tmp, a tmpfs on
    most systems) therefore produces a ~4 GB env that is silently useless after
    a reboot: `import insarhub` still works, `which p2p_processing` finds
    nothing, and has_gmtsar() goes False. Observed for real once.
    """
    assert "def gmtsar_build_dir" in driver, (
        "the driver no longer has a dedicated GMTSAR build location; it must "
        "not fall back to the scratch/temp workdir"
    )
    assert "INSARHUB_GMTSAR_DIR" in driver
    # The build must be handed an explicit durable dir, never the scratch one.
    assert "build_gmtsar(workdir)" not in driver, (
        "build_gmtsar is being given the temp scratch dir again"
    )


def test_gmtsar_source_tree_is_kept_with_its_env(driver):
    """The env and its source tree must be kept or removed together.

    $GMTSAR and PATH point into the cloned source, so deleting the scratch
    directory while leaving the conda env behind strands ~4 GB of environment
    whose GMTSAR tools no longer exist on PATH -- an install that looks present
    and cannot run.
    """
    assert "built_gmtsar" in driver, (
        "the cleanup path no longer distinguishes a GMTSAR run, so the scratch "
        "directory (containing the source the gmtsar env needs) may be deleted "
        "while the env is kept"
    )
    # The unconditional teardown must be guarded, not reachable for GMTSAR.
    assert re.search(
        r"if built_gmtsar:.*?else:\s*\n\s*shutil\.rmtree\(workdir",
        driver, re.S,
    ), "shutil.rmtree(workdir) is not guarded by the GMTSAR branch"


# ── ISCE3 / dolphin ──────────────────────────────────────────────────────────

def test_isce3_package_set_matches_the_docs(doc, driver):
    """The docs install sardem, snaphu and burst2safe alongside isce3/compass/
    dolphin. Omitting them produced an env that imported but could not run."""
    match = re.search(r"conda install[^\n]*isce3[^\n]*", doc)
    assert match, "install.md no longer documents an isce3 conda install line"
    documented = {
        pkg for pkg in
        ("isce3", "compass", "sardem", "dolphin", "snaphu", "burst2safe")
        if pkg in match.group(0)
    }
    flavor = re.search(r'"isce3-dolphin": Flavor\((.*?)\n    \),', driver, re.S)
    assert flavor, "isce3-dolphin flavour not found in the driver"
    missing = sorted(pkg for pkg in documented if pkg not in flavor.group(1))
    assert not missing, (
        f"the docs install {sorted(documented)} for isce3+dolphin but the "
        f"driver's flavour omits {missing}"
    )


# ── Isolation ────────────────────────────────────────────────────────────────

def test_ci_gives_each_install_flavor_its_own_env(workflow):
    """A shared env name would let one flavour's pins satisfy another's.

    ISCE2 and COMPASS both pin numpy<2, so a shared environment could resolve
    happily while neither flavour installs on its own.
    """
    assert "activate-environment: test-env" not in workflow, (
        "the install matrix shares one env name across flavours"
    )
    assert "insarhub-${{ matrix.flavor }}-py${{ matrix.python }}" in workflow


# ── The fast job's hand-curated dependency list ──────────────────────────────

# Every runtime dependency the `fast` job deliberately does NOT pip-install,
# with the reason. Anything else missing from that job is drift, not a choice.
FAST_JOB_OMISSIONS = {
    # Stubbed in test/conftest.py (_STUBS), so tier 2 never imports the real
    # thing -- and all three are conda-first packages that are slow or fragile
    # to pip-install on a bare runner.
    "mintpy": "stubbed by conftest",
    "cdsapi": "stubbed by conftest",
    "dask": "stubbed by conftest",
    # Not stubbed itself, but only reachable through mintpy, which is.
    "pyaps3": "only imported via mintpy, which is stubbed",
    # Pulls the `gdal` PyPI package, which builds from source and dies with
    # "FileNotFoundError: 'gdal-config'" on a runner without libgdal. InSARHub
    # imports it lazily, inside the S1_Burst .SAFE assembly functions, so no
    # tier-2 import needs it.
    "burst2safe": "pulls the gdal PyPI package, which needs gdal-config",
}


def _requirement_name(spec: str) -> str:
    """`rasterio>=1.4` -> `rasterio`."""
    return re.split(r"[<>=!~\[ ]", spec, 1)[0].strip().lower()


def _fast_job_pip_specs(workflow: str) -> dict[str, str]:
    """The quoted requirements the `fast` job pip-installs, by name.

    Comment lines are stripped first: that step's comments quote a pip error
    message, and a naive scan for quoted strings picks it up as a requirement.
    """
    import yaml

    steps = yaml.safe_load(workflow)["jobs"]["fast"]["steps"]
    run = next(s["run"] for s in steps if s.get("name") == "Install")
    code = "\n".join(l for l in run.splitlines() if not l.strip().startswith("#"))
    code = code.replace("\\\n", " ")
    line = next(l for l in code.splitlines() if "pip install" in l and '"' in l)
    return {_requirement_name(q): q for q in re.findall(r'"([^"]+)"', line)}


def test_ci_windows_jobs_declare_a_bash_shell(workflow):
    """Any job that can land on a Windows runner must pin a bash shell.

    GitHub defaults Windows steps to PowerShell, where ``\\`` is not a line
    continuation. A perfectly ordinary multi-line

        pip install "a" "b" \\
                    "c" "d"

    then dies at parse time with *"Unexpected token"* before installing
    anything -- and only on Windows, so it looks like a dependency problem
    rather than a shell problem. Every runner has bash (Git Bash on Windows),
    so pinning it costs nothing.

    This is checked for every Windows job, not just ones that currently use a
    continuation, because the failure appears the moment someone wraps a long
    line -- a change that looks purely cosmetic.
    """
    import yaml

    jobs = yaml.safe_load(workflow)["jobs"]
    offenders = []
    for name, job in jobs.items():
        matrix = job.get("strategy", {}).get("matrix", {}) or {}
        oses = set()
        if isinstance(matrix.get("os"), list):
            oses |= set(matrix["os"])
        for inc in matrix.get("include", []) or []:
            if "os" in inc:
                oses.add(inc["os"])
        if not oses:
            oses = {str(job.get("runs-on", ""))}
        if not any("windows" in str(o) for o in oses):
            continue
        shell = job.get("defaults", {}).get("run", {}).get("shell", "")
        if "bash" not in str(shell):
            offenders.append((name, shell or None))

    assert not offenders, (
        "these jobs can run on Windows without pinning a bash shell, so their "
        f"steps run under PowerShell: {offenders}. Add\n"
        "    defaults:\n      run:\n        shell: bash\n"
        "to the job."
    )


def test_ci_dependency_list_matches_pyproject(workflow):
    """The `fast` job's pip list must be pyproject's dependencies minus the
    documented omissions.

    That job installs with ``--no-deps`` and then names every requirement by
    hand, because the real dependency set drags in the whole conda geo stack.
    The list is therefore a second copy of pyproject's, and it has already
    drifted once: an earlier version omitted contextily and sentineleof, and
    every tier-2 module failed to import.

    The failure mode is what makes this worth pinning -- a dependency added to
    pyproject and forgotten here does not fail as "missing dependency". It
    fails as an ImportError inside an unrelated test, on CI only, on a module
    that looks fine locally.
    """
    declared = {
        _requirement_name(d): d
        for d in tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    }
    installed = _fast_job_pip_specs(workflow)

    expected = set(declared) - set(FAST_JOB_OMISSIONS)

    missing = sorted(expected - set(installed))
    assert not missing, (
        f"pyproject declares {missing} but the `fast` job does not install "
        "them. Add them to that job's pip install line, or, if they are "
        "deliberately left out, to FAST_JOB_OMISSIONS with the reason."
    )

    unexpected = sorted(set(installed) - set(declared))
    assert not unexpected, (
        f"the `fast` job installs {unexpected}, which pyproject does not "
        "declare as a dependency."
    )

    stale = sorted(n for n in FAST_JOB_OMISSIONS if n not in declared)
    assert not stale, (
        f"FAST_JOB_OMISSIONS still excuses {stale}, which pyproject no longer "
        "declares. Drop the entry."
    )


def test_ci_dependency_versions_match_pyproject(workflow):
    """A pin in one place and not the other is the subtler half of the drift.

    numpy is the live example: pyproject says ``numpy<2.0``, and an unpinned
    install on the runner pulled 2.x, which pip then reported as
    "insarhub requires numpy<2.0, but you have numpy 2.5.3".
    """
    declared = {
        _requirement_name(d): d
        for d in tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    }
    installed = _fast_job_pip_specs(workflow)

    mismatched = {
        n: (declared[n], installed[n])
        for n in set(declared) & set(installed)
        if declared[n].replace(" ", "") != installed[n].replace(" ", "")
    }
    assert not mismatched, (
        "these requirements are spelled differently in pyproject and in the "
        f"`fast` job (pyproject, CI): {mismatched}"
    )


def test_ci_install_matrix_stays_on_supported_platforms(workflow):
    """The install matrix may only schedule platform/flavour pairs that install.

    Two rules, for two different reasons:

    * **Only `base` runs off Linux.** ISCE2 ships linux-64/osx-64 and the ISCE3
      stack is x86_64 too, while GitHub's macos-14 runners are arm64; GMTSAR's
      installer takes `--system conda-linux-full`. `base` pulls no SAR backend.

    * **Windows rows must be Python 3.11.** InSARHub needs mintpy, which needs
      pysolid. pysolid shipped win-64 builds through 0.3.2 and dropped Windows
      at 0.3.3, so a Windows solve back-solves to 0.3.2 -- which predates
      cpython 3.12 and has no py3.12 build. win-64 + py3.12 fails with
      "requires pysolid, but none of the providers can be installed", an error
      that names pysolid and explains nothing about why only 3.11 works.

    Widening the top-level `os` list breaks both at once: every flavour would
    fan out across every OS. Non-Linux platforms belong in `include` entries,
    where the flavour and the Python version are visible.
    """
    import yaml

    matrix = yaml.safe_load(workflow)["jobs"]["install"]["strategy"]["matrix"]

    assert matrix.get("os") == ["ubuntu-latest"], (
        f"the install matrix's top-level `os` is {matrix.get('os')!r}; only "
        "ubuntu-latest belongs there. Add other platforms as `include` entries "
        "naming flavor: base."
    )

    includes = [inc for inc in matrix.get("include", []) if "os" in inc]

    offenders = [
        inc for inc in includes
        if inc["os"] != "ubuntu-latest" and inc.get("flavor") != "base"
    ]
    assert not offenders, (
        f"these install matrix entries put a SAR backend on a non-Linux "
        f"runner: {offenders}"
    )

    bad_python = [
        inc for inc in includes
        if "windows" in str(inc["os"]) and str(inc.get("python")) != "3.11"
    ]
    assert not bad_python, (
        f"these Windows install rows are not pinned to Python 3.11: "
        f"{bad_python}. The last pysolid build for win-64 is 0.3.2, which has "
        "no py3.12 build, so the conda solve fails."
    )


def test_ci_gives_each_e2e_workflow_its_own_env(workflow):
    targets = re.findall(r"target_env:\s*(\S+)", workflow)
    assert targets, "no target_env entries found in the e2e matrix"
    assert len(targets) == len(set(targets)), (
        f"e2e workflows share an environment: {targets}"
    )
