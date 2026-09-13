#!/usr/bin/env python3
"""Actually install InSARHub, in each documented flavour, and verify the result.

Tier 1 of the pytest suite does not install anything -- it inspects whatever is
already importable. Something has to do the installing, and this is it: for each
flavour in docs/quickstart/install.md it builds a wheel, creates a fresh conda
environment, installs the backend stack, installs the wheel, and runs `pytest -m
install` inside the result.

It is the local counterpart of the `install` job in .github/workflows/test.yml,
so the two must describe the same procedure. Run it before a release tag.

    python scripts/test_install.py --flavor base          # ~5 min
    python scripts/test_install.py --flavor all           # ~40 min
    python scripts/test_install.py --flavor all --keep    # leave envs behind
    python scripts/test_install.py --flavor base --from-conda-forge

Flavours follow the docs exactly:

    base           gdal + mintpy
    isce2          + isce2                       (Linux/macOS x86_64 only)
    isce3-dolphin  + isce3 compass sardem dolphin snaphu burst2safe
    gmtsar         GMTSAR built from git         (Linux x86_64 only, ~30 min)

GMTSAR is deliberately not in `all`: it is a source build that takes about half
an hour, so it is opt-in via `--flavor gmtsar`. It is NOT a conda-forge package
-- the name exists there but ships no files.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ── Flavour definitions ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Flavor:
    name: str
    conda_packages: tuple[str, ...]
    #  What test_env_claiming_a_backend_actually_has_it must find afterwards.
    expect: tuple[str, ...]
    linux_only: bool = False
    x86_64_only: bool = False
    build_gmtsar: bool = False
    notes: str = ""


FLAVORS: dict[str, Flavor] = {
    "base": Flavor(
        name="base",
        conda_packages=("gdal", "mintpy"),
        expect=("mintpy",),
    ),
    "isce2": Flavor(
        name="isce2",
        conda_packages=("gdal", "mintpy", "isce2"),
        expect=("isce2", "mintpy"),
        x86_64_only=True,
        notes="ISCE2 is Linux/macOS x86_64 only (not Apple Silicon, not Windows)",
    ),
    "isce3-dolphin": Flavor(
        name="isce3-dolphin",
        conda_packages=(
            "gdal", "isce3", "compass", "sardem", "dolphin", "snaphu", "burst2safe",
        ),
        expect=("isce3", "dolphin"),
        x86_64_only=True,
        notes="ISCE3/COMPASS/dolphin is Linux/macOS x86_64 only",
    ),
    # GMTSAR does not follow the pattern of the others: its own installer
    # CREATES the conda env (named "gmtsar"), and InSARHub + MintPy are then
    # installed into that env. See docs/quickstart/install.md. Handled by
    # install_gmtsar_flavor(), not install_flavor().
    "gmtsar": Flavor(
        name="gmtsar",
        conda_packages=("mintpy",),
        expect=("gmtsar", "mintpy"),
        linux_only=True,
        x86_64_only=True,
        build_gmtsar=True,
        notes="GMTSAR is built from source (~30 min) and creates its own conda "
              "env named 'gmtsar'; conda-forge has no real gmtsar package",
    ),
}

DEFAULT_SET = ("base", "isce2", "isce3-dolphin")


# ── Shell helpers ────────────────────────────────────────────────────────────

class Step(Exception):
    """A step failed; message carries enough output to act on."""


def run(cmd: list[str] | str, *, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 5400, shell: bool = False) -> str:
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"    $ {printable}", flush=True)
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, shell=shell,
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        raise Step(f"exited {proc.returncode}:\n{tail}")
    return proc.stdout


def conda_exe() -> str:
    """Solver front end. mamba where available -- these solves are slow."""
    for candidate in ("mamba", "conda"):
        if shutil.which(candidate):
            return candidate
    raise SystemExit("neither mamba nor conda is on PATH")


def conda_plain() -> str:
    """Always real conda.

    `mamba run` does not accept the same flags as `conda run` (it rejects
    --no-capture-output and mangles anything after a bare --), so every
    exec-in-an-env goes through conda even when mamba is doing the solving.
    """
    if not shutil.which("conda"):
        raise SystemExit("conda is not on PATH")
    return "conda"


def conda_run(env_name: str, *argv: str, timeout: int = 5400) -> str:
    """Run a command inside a named conda env."""
    return run([conda_plain(), "run", "-n", env_name, *argv], timeout=timeout)


# ── The procedure ────────────────────────────────────────────────────────────

def build_wheel(outdir: Path) -> Path:
    print("==> Building wheel")
    # The frontend bundle is package data; if it has never been built the wheel
    # is still valid, and tier 1 skips the frontend check rather than failing.
    if not (REPO / "src/insarhub/app/frontend/dist/index.html").is_file():
        print("    (frontend dist/ absent -- tier 1 will skip the bundle check)")
    run([sys.executable, "-m", "pip", "install", "-q", "build"])
    run([sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)], cwd=REPO)
    wheels = sorted(outdir.glob("*.whl"))
    if not wheels:
        raise Step("build produced no wheel")
    print(f"    built {wheels[-1].name}")
    return wheels[-1]


GMTSAR_ENV = "gmtsar"


def conda_env_exists(name: str) -> bool:
    out = run([conda_plain(), "env", "list", "--json"], timeout=300)
    return any(Path(p).name == name for p in json.loads(out).get("envs", []))


def gmtsar_build_dir(explicit: str | None = None) -> Path:
    """Where to clone and build GMTSAR.

    Deliberately NOT the scratch workdir the other flavours use. That is a temp
    directory, which on most systems is a tmpfs cleared on reboot -- and the
    GMTSAR conda env holds no copy of the tools, it only finds them through
    $GMTSAR/bin on PATH. Building into temp therefore produces a ~4 GB conda env
    that is silently useless after the next reboot: `import insarhub` still
    works, `which p2p_processing` finds nothing.

    Observed for real: a built env survived a restart while its /tmp source did
    not, and has_gmtsar() correctly went False.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("INSARHUB_GMTSAR_DIR", Path.home() / "gmtsar")).resolve()


def build_gmtsar(build_dir: Path) -> Path:
    """Clone and build GMTSAR exactly as docs/quickstart/install.md says.

    Note what this does to the environment: `install.py --system
    conda-linux-full` CREATES a conda env named "gmtsar" itself. We do not make
    one first -- InSARHub and MintPy go into the env GMTSAR built.
    """
    print("==> Building GMTSAR from source (this takes ~30 minutes)")
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    src = build_dir
    if not src.exists():
        # A full clone, not --depth 1: the docs say "the full GMTSAR repo", and
        # the build reads git metadata to stamp its version.
        run(["git", "clone", "https://github.com/gmtsar/gmtsar.git", str(src)],
            timeout=3600)
    run(["python3", "gmtsar/python/install.py", "--system", "conda-linux-full"],
        cwd=src, timeout=7200)
    if not conda_env_exists(GMTSAR_ENV):
        raise Step(
            f"GMTSAR's installer did not create a conda env named {GMTSAR_ENV!r}; "
            "the documented procedure has changed -- update this script and "
            "docs/quickstart/install.md together"
        )
    return src


def install_gmtsar_flavor(flavor: Flavor, wheel: Path, from_conda_forge: bool,
                          workdir: Path, reuse_env: bool,
                          build_dir: Path | None = None) -> dict:
    """The GMTSAR path, which inverts the usual order.

    Everywhere else we create an env and install a backend into it. Here the
    backend's own installer creates the env, so the steps are: build GMTSAR ->
    conda activate gmtsar -> install MintPy + InSARHub -> put GMTSAR/bin on PATH.
    """
    started = time.monotonic()
    record = {"flavor": flavor.name, "python": "(set by GMTSAR installer)",
              "env": GMTSAR_ENV}

    # Never silently take over a GMTSAR env the user built for real work.
    if conda_env_exists(GMTSAR_ENV) and not reuse_env:
        raise Step(
            f"a conda env named {GMTSAR_ENV!r} already exists. This flavour "
            "installs into the env GMTSAR's own installer creates, so it will "
            "not touch an existing one. Pass --reuse-gmtsar-env to install into "
            f"it anyway, or remove it first: conda env remove -n {GMTSAR_ENV}"
        )

    src = build_gmtsar(build_dir or gmtsar_build_dir())
    gmtsar_env = {
        "GMTSAR": str(src),
        "PATH": f"{src / 'bin'}{os.pathsep}{os.environ['PATH']}",
    }

    if from_conda_forge:
        # Exactly the documented command: insarhub and mintpy in one solve.
        # Resolving them separately can land on a different mintpy than a user
        # following the docs would get.
        print(f"==> [gmtsar] conda install insarhub + {' '.join(flavor.conda_packages)}")
        run([conda_exe(), "install", "-y", "-n", GMTSAR_ENV, "-c", "conda-forge",
             "insarhub", *flavor.conda_packages], timeout=5400)
    else:
        print(f"==> [gmtsar] installing {' '.join(flavor.conda_packages)} into env {GMTSAR_ENV!r}")
        run([conda_exe(), "install", "-y", "-n", GMTSAR_ENV,
             "-c", "conda-forge", *flavor.conda_packages], timeout=5400)
        print("==> [gmtsar] installing the locally built wheel")
        conda_run(GMTSAR_ENV, "python", "-m", "pip", "install", str(wheel), timeout=1800)

    conda_run(GMTSAR_ENV, "python", "-m", "pip", "install", "-q",
              "pytest", "httpx", "packaging", timeout=1800)

    # The docs' own verify step, run before tier 1 so a broken GMTSAR build is
    # reported as such rather than as a confusing tier 1 failure.
    print("==> [gmtsar] verifying as docs/quickstart/install.md does")
    run(["bash", "-lc", "which p2p_processing || which p2p_processing.csh"],
        env=gmtsar_env, timeout=300)
    conda_run(GMTSAR_ENV, "python", "-c", "import insarhub, mintpy; print('ok')",
              timeout=600)

    record.update(_run_tier1(GMTSAR_ENV, workdir, flavor, gmtsar_env))
    record["seconds"] = round(time.monotonic() - started)
    return record


def install_flavor(flavor: Flavor, wheel: Path, python: str, env_name: str,
                   from_conda_forge: bool, workdir: Path,
                   ignore_requires_python: bool = False) -> dict:
    """Create the env, install everything, run tier 1. Returns a result record."""
    started = time.monotonic()
    record = {"flavor": flavor.name, "python": python, "env": env_name}

    print(f"\n==> [{flavor.name}] creating env {env_name} (python={python})")
    # Each flavour gets its own environment, always. Installing two flavours
    # into one env would let ISCE2's numpy<2 pin, COMPASS's pin and MintPy's
    # requirements resolve against each other, so a "pass" would say nothing
    # about whether either flavour installs on its own -- which is the entire
    # question this script exists to answer.
    if conda_env_exists(env_name):
        print(f"    (removing a leftover {env_name} from an earlier run)")
        run([conda_plain(), "env", "remove", "-y", "-n", env_name], timeout=900)
    run([conda_exe(), "create", "-y", "-n", env_name, f"python={python}"], timeout=1800)

    print(f"==> [{flavor.name}] installing backends: {' '.join(flavor.conda_packages)}")
    run([conda_exe(), "install", "-y", "-n", env_name,
         "-c", "conda-forge", *flavor.conda_packages], timeout=5400)

    gmtsar_env: dict[str, str] = {}

    if from_conda_forge:
        print(f"==> [{flavor.name}] installing insarhub FROM conda-forge (published path)")
        run([conda_exe(), "install", "-y", "-n", env_name, "-c", "conda-forge", "insarhub"],
            timeout=3600)
    else:
        print(f"==> [{flavor.name}] installing the locally built wheel")
        pip_args = ["python", "-m", "pip", "install"]
        if ignore_requires_python:
            print("    (--ignore-requires-python: bypassing the Requires-Python gate)")
            pip_args.append("--ignore-requires-python")
        conda_run(env_name, *pip_args, str(wheel), timeout=1800)

    conda_run(env_name, "python", "-m", "pip", "install", "-q",
              "pytest", "httpx", "packaging", timeout=1800)

    record.update(_run_tier1(env_name, workdir, flavor, gmtsar_env))
    record["seconds"] = round(time.monotonic() - started)
    return record


def _run_tier1(env_name: str, workdir: Path, flavor: Flavor,
               extra_env: dict[str, str]) -> dict:
    """Run tier 1 inside `env_name` and summarise the outcome."""
    # Run from a scratch dir so the INSTALLED package is imported, not ./src.
    scratch = workdir / f"run-{env_name}"
    scratch.mkdir(parents=True, exist_ok=True)

    print(f"==> [{flavor.name}] running tier 1 against the installed package")
    cmd = [
        conda_plain(), "run", "-n", env_name,
        "python", "-m", "pytest", "-m", "install", "-v",
        "--rootdir", str(REPO),
        "-c", str(REPO / "pyproject.toml"),
        str(REPO / "test" / "tier1_install"),
    ]
    proc = subprocess.run(
        cmd, cwd=str(scratch), capture_output=True, text=True, timeout=3600,
        env={**os.environ, **extra_env,
             "INSARHUB_EXPECT_BACKENDS": ",".join(flavor.expect)},
    )
    output = proc.stdout + proc.stderr
    summary = next(
        (line for line in reversed(output.splitlines())
         if " passed" in line or " failed" in line or " error" in line),
        "(no pytest summary line)",
    )
    record = {
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "summary": summary,
    }
    if proc.returncode != 0:
        record["output_tail"] = "\n".join(output.splitlines()[-40:])
    return record


def _declared_requires_python() -> str | None:
    """The requires-python string from pyproject, read as text.

    tomllib would be cleaner, but this script must run on whatever interpreter
    the user invoked it with, and a regex over one line cannot fail.
    """
    import re

    try:
        text = (REPO / "pyproject.toml").read_text()
    except OSError:
        return None
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _python_in_range(version: str, spec: str) -> bool:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        return Version(version) in SpecifierSet(spec)
    except Exception:
        return True   # can't tell -- don't cry wolf


def supported(flavor: Flavor) -> str | None:
    """Reason this flavour cannot run here, or None."""
    if flavor.linux_only and sys.platform != "linux":
        return f"{flavor.name} is Linux only (this is {sys.platform})"
    if flavor.x86_64_only and platform.machine() not in ("x86_64", "AMD64"):
        return f"{flavor.name} is x86_64 only (this is {platform.machine()})"
    return None


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--flavor", default="base",
        help=f"comma-separated: any of {', '.join(FLAVORS)}; or 'all' for "
             f"{', '.join(DEFAULT_SET)}. Each flavour is built in its own env.",
    )
    parser.add_argument("--python", default="3.12", help="Python version (default 3.12)")
    parser.add_argument("--keep", action="store_true",
                        help="do not remove the conda envs afterwards")
    parser.add_argument("--from-conda-forge", action="store_true",
                        help="install the PUBLISHED insarhub instead of a local wheel, "
                             "to verify the release users actually get")
    parser.add_argument(
        "--ignore-requires-python", action="store_true",
        help="install the wheel even though its Requires-Python excludes this "
             "interpreter. For answering 'could we raise the <3.13 cap?' -- it "
             "tests whether the CODE works, past the metadata gate.",
    )
    parser.add_argument(
        "--gmtsar-dir", metavar="PATH",
        help="where to clone and build GMTSAR (default: $INSARHUB_GMTSAR_DIR or "
             "~/gmtsar). Must be durable storage -- the conda env finds the "
             "tools only through $GMTSAR/bin, so a tmpfs build strands the env "
             "on reboot.",
    )
    parser.add_argument(
        "--reuse-gmtsar-env", action="store_true",
        help="install into an existing conda env named 'gmtsar' instead of "
             "refusing; by default an existing one is never touched",
    )
    parser.add_argument("--json", metavar="PATH", help="write results as JSON")
    args = parser.parse_args()

    if args.flavor == "all":
        names = list(DEFAULT_SET)
    else:
        # Comma-separated so a subset can be run together -- e.g. the two heavy
        # local backends, `--flavor isce2,isce3-dolphin`, each in its own env.
        names = [n.strip() for n in args.flavor.split(",") if n.strip()]
    if not names:
        parser.error("--flavor is empty")
    duplicates = [n for n in set(names) if names.count(n) > 1]
    if duplicates:
        parser.error(f"repeated flavour(s): {sorted(duplicates)}")
    unknown = [n for n in names if n not in FLAVORS]
    if unknown:
        parser.error(f"unknown flavour(s): {unknown}; choose from {list(FLAVORS)}")

    # Assert the isolation rather than assuming it: one env per flavour, and
    # the GMTSAR flavour deliberately targets its installer's own "gmtsar" env.
    planned = {
        name: (GMTSAR_ENV if FLAVORS[name].build_gmtsar
               else f"insarhub-install-{name}-{args.python.replace('.', '')}")
        for name in names
    }
    if len(set(planned.values())) != len(planned):
        parser.error(f"flavours would share an environment: {planned}")
    declared = _declared_requires_python()
    if declared:
        print(f"pyproject declares requires-python = {declared}")
        if not _python_in_range(args.python, declared):
            print(f"  NOTE: --python {args.python} is OUTSIDE that range. This is an "
                  "experiment,\n        not a supported configuration"
                  + ("" if args.ignore_requires_python else
                     ";\n        pip will refuse the wheel without --ignore-requires-python")
                  + ".")

    print("Environment plan (one per flavour):")
    for name, env in planned.items():
        print(f"  {name:<16} -> {env}")

    workdir = Path(tempfile.mkdtemp(prefix="insarhub-install-"))
    results: list[dict] = []
    envs: list[str] = []

    try:
        wheel = None
        if not args.from_conda_forge:
            wheel = build_wheel(workdir / "dist")

        for name in names:
            flavor = FLAVORS[name]
            reason = supported(flavor)
            if reason:
                print(f"\n==> [{name}] SKIP -- {reason}")
                results.append({"flavor": name, "skipped": reason})
                continue
            if flavor.notes:
                print(f"\n    note: {flavor.notes}")
            if flavor.build_gmtsar:
                built = gmtsar_build_dir(args.gmtsar_dir)
                print(f"    note: GMTSAR is built in {built} and kept afterwards -- "
                      "the conda env finds its tools only via $GMTSAR/bin.")
                print("    note: pass --gmtsar-dir to build elsewhere. Do not use "
                      "a tmpfs: a cleared /tmp strands the env.")

            try:
                if flavor.build_gmtsar:
                    # GMTSAR's installer owns the env, so no env name of ours
                    # and no automatic teardown -- see install_gmtsar_flavor.
                    results.append(install_gmtsar_flavor(
                        flavor, wheel, args.from_conda_forge, workdir,
                        args.reuse_gmtsar_env, gmtsar_build_dir(args.gmtsar_dir),
                    ))
                else:
                    env_name = planned[name]
                    envs.append(env_name)
                    results.append(install_flavor(
                        flavor, wheel, args.python, env_name,
                        args.from_conda_forge, workdir,
                        args.ignore_requires_python,
                    ))
            except (Step, subprocess.TimeoutExpired) as exc:
                results.append({"flavor": name, "passed": False, "error": str(exc)})
                print(f"    FAILED: {exc}")
    finally:
        # Note `envs` never contains "gmtsar": that env belongs to GMTSAR's
        # installer (and possibly to the user), so it is never auto-removed.
        built_gmtsar = any(FLAVORS[n].build_gmtsar for n in names)

        if not args.keep:
            for env_name in envs:
                subprocess.run([conda_plain(), "env", "remove", "-y", "-n", env_name],
                               capture_output=True)
            if built_gmtsar:
                # The GMTSAR source lives outside the scratch dir on purpose
                # (see gmtsar_build_dir), so the scratch dir can go -- but the
                # source must not: $GMTSAR and PATH point into it, and deleting
                # it leaves ~4 GB of conda env whose tools no longer exist.
                built = gmtsar_build_dir(args.gmtsar_dir)
                shutil.rmtree(workdir, ignore_errors=True)
                print(f"\nGMTSAR source kept at: {built}")
                print("  It is what $GMTSAR and $PATH point into -- deleting it "
                      "would leave the 'gmtsar' conda env without its tools.")
                print("  To use the env:")
                print(f"    conda activate {GMTSAR_ENV}")
                print(f"    export GMTSAR={built} && export PATH=$GMTSAR/bin:$PATH")
                print("  To remove both:")
                print(f"    conda env remove -n {GMTSAR_ENV} && rm -rf {built}")
            else:
                shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"\nEnvironments kept. Scratch dir: {workdir}")
            if built_gmtsar:
                print(f"  GMTSAR source: {workdir / 'gmtsar'}")
                print(f"  To remove both: conda env remove -n {GMTSAR_ENV} && rm -rf {workdir}")

    # ── Report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("Install matrix")
    print("=" * 72)
    failed = 0
    for record in results:
        if "skipped" in record:
            print(f"  SKIP  {record['flavor']:<16} {record['skipped']}")
            continue
        ok = record.get("passed")
        failed += 0 if ok else 1
        status = "PASS" if ok else "FAIL"
        secs = record.get("seconds", "?")
        print(f"  {status}  {record['flavor']:<16} {secs}s  {record.get('summary', record.get('error', ''))}")
        if not ok and record.get("output_tail"):
            for line in record["output_tail"].splitlines():
                print(f"          {line}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
