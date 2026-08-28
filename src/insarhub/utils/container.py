# -*- coding: utf-8 -*-
"""
Wrap a shell command to run inside a user-provided container.

``container`` is either a path to an existing Apptainer/Singularity ``.sif``
image file, or a Docker image reference (name[:tag]) — detected by checking
whether it resolves to an existing file on disk. No new dependency is added:
this shells out to the ``docker``/``apptainer``/``singularity`` CLI via
``subprocess``, matching how this codebase already shells out to
``sbatch``/``squeue``/``scancel`` rather than using an SDK.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import time

from colorama import Fore, Style

# When each image's status was last announced. Used to collapse the burst of
# identical calls a single operation makes (e.g. GMTSAR wraps one command per
# stage/pair up front) into one line, WITHOUT permanently silencing later
# submits: the app backend is a long-lived process, so a plain "once per
# process" flag would suppress the status for every submit after the first --
# even after the user deletes the image and expects to see it download again.
_STATUS_REPORTED: dict[str, float] = {}
_REPORT_WINDOW_S = 10.0


def report_container_status(container: str) -> None:
    """Print whether the container is reused from local storage or has to be
    downloaded first. De-duplicated only within a short time window, so a burst
    of wraps in one operation reports once but independent submits always do.

    A first-use ``docker run`` silently blocks while it pulls a multi-GB image
    with no line of its own in the log, which reads as a hang. This says up
    front which case you are in.
    """
    now = time.monotonic()
    last = _STATUS_REPORTED.get(container)
    if last is not None and (now - last) < _REPORT_WINDOW_S:
        return
    _STATUS_REPORTED[container] = now

    # Apptainer/Singularity images are a local .sif file on disk. flush=True on
    # every line: in the app backend this runs in a forked child that has
    # redirected stdout to executor.log and exits via os._exit(), which does not
    # flush Python's buffers -- without it the line is lost before docker's own
    # output reaches the log.
    if Path(container).expanduser().exists():
        print(f"{Fore.CYAN}[container] using local image file "
              f"{container}{Style.RESET_ALL}", flush=True)
        return

    # Docker image reference: is it already pulled?
    present = False
    try:
        present = subprocess.run(
            ["docker", "image", "inspect", container],
            capture_output=True, text=True).returncode == 0
    except Exception:                                            # noqa: BLE001
        pass  # docker missing/unreachable -- let the actual run surface it

    if present:
        print(f"{Fore.CYAN}[container] reusing local image "
              f"{container}{Style.RESET_ALL}", flush=True)
    else:
        print(f"{Fore.YELLOW}[container] image {container} not present locally "
              f"-- downloading it now (first run can take a few minutes)"
              f"{Style.RESET_ALL}", flush=True)

# Host credential files made visible inside the container so network-bound
# stages authenticate the same way they would on the host:
#   .netrc       Earthdata (CDDIS IONEX / TEC), CDSE, ASF
#   .credit_pool HyP3 Earthdata credential pool
#   .cdsapirc    Copernicus CDS (ERA5 troposphere)
# Every consumer reads these from ``~/<name>`` with no env-var override, so they
# have to appear at the container's $HOME rather than be redirected per-tool.
_CRED_FILES = (".netrc", ".credit_pool", ".cdsapirc")


def _stage_credentials() -> Path | None:
    """Copy the host's credential files into a fresh, user-owned staging dir to
    be mounted as the container's $HOME. Returns the dir, or None if the host
    has none of them.

    Why a copy into a separate dir rather than mounting the files directly:
    - $HOME must be WRITABLE by the container's numeric --user (insarhub's dask
      setup writes ``$HOME/.dask``), so it cannot be the read-only real home,
      and over-mounting the whole real home would expose every file in it.
    - Bind-mounting a file into the already-bind-mounted workdir leaves a stray
      root-owned stub at that path ON THE HOST (docker creates the mountpoint),
      polluting the user's workdir.
    A throwaway dir owned by the invoking user sidesteps both: it is writable by
    the matching --user uid, exposes only these three files, and lives outside
    the workdir. The caller removes it after the run.
    """
    present = [f for f in _CRED_FILES if (Path.home() / f).is_file()]
    if not present:
        return None
    staging = Path(tempfile.mkdtemp(prefix="insarhub-cred-"))
    os.chmod(staging, 0o700)
    for f in present:
        shutil.copy(Path.home() / f, staging / f)
    return staging


def wrap_container_cmd(
    container: str,
    cmd: str,
    bind_dir: Path,
    *,
    workdir_in_container: str | None = None,
) -> str:
    """Return ``cmd`` wrapped to execute inside ``container``.

    ``bind_dir`` (typically the processing workdir) is bind-mounted into the
    container at the same path, so files written by the wrapped command are
    visible on the host at the identical path afterward.
    """
    bind_dir = Path(bind_dir)
    wd = workdir_in_container or str(bind_dir)
    quoted_cmd = shlex.quote(cmd)

    if Path(container).expanduser().exists():
        # Apptainer/Singularity bind-mount the invoking user's $HOME by default
        # and run as that user, so ~/.netrc etc. are already visible -- no
        # credential staging needed.
        runtime = shutil.which("apptainer") or shutil.which("singularity")
        if not runtime:
            raise RuntimeError(
                f"apptainer/singularity not found on PATH -- install one to run "
                f"the image file '{container}'")
        report_container_status(container)
        return (
            f"{runtime} exec --bind {bind_dir}:{bind_dir} --pwd {wd} "
            f"{container} bash -c {quoted_cmd}"
        )

    if not shutil.which("docker"):
        # Exit 127 later ("docker: not found") would otherwise be blamed on the
        # image -- fail fast with the real cause.
        raise RuntimeError(
            f"docker not found on PATH -- is Docker installed and its CLI on "
            f"your PATH? (needed to run image '{container}')")

    report_container_status(container)

    user_flag = f"--user {os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else ""

    # --user's numeric UID/GID has no /etc/passwd entry in the image unless it
    # happens to match one baked in at build time, so HOME resolves to "/" --
    # anything writing to $HOME (e.g. insarhub's own dask temp-dir setup) fails
    # with a permission error against root's home. HOME therefore has to be a
    # dir the user can write. When the host has credential files, HOME is a
    # user-owned staging dir holding copies of them (so ~/.netrc etc. resolve
    # inside the container); otherwise it falls back to the bind-mounted workdir.
    staging = _stage_credentials()
    if staging is not None:
        home = str(staging)
        home_vol = f"-v {staging}:{staging} "
        # Remove the staging dir (credential copies + any dask temp under it)
        # once the container exits, preserving the container's exit code.
        cleanup = f" ; _rc=$? ; rm -rf {shlex.quote(str(staging))} ; exit $_rc"
    else:
        home = str(bind_dir)
        home_vol = ""
        cleanup = ""

    # No --pid=host: it doesn't actually help on Docker Desktop (Windows/Mac/
    # WSL2) since those run containers inside Docker Desktop's own VM, sharing
    # a PID namespace with *that* VM rather than the user's real host shell --
    # so a PID recorded inside the container would still be unrelated to
    # anything in the host's own process table. See isce2_base.py's
    # INSARHUB_HOST_PID (in _reinvoke_via_container/_step_executor) for how
    # container-run step liveness is actually tracked instead.
    run = (
        f"docker run --rm {user_flag} -e HOME={home} "
        f"{home_vol}-v {bind_dir}:{bind_dir} -w {wd} {container} bash -c {quoted_cmd}"
    ).replace("  ", " ")
    return run + cleanup
