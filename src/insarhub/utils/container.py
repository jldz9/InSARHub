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
from pathlib import Path


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
        runtime = shutil.which("apptainer") or shutil.which("singularity") or "apptainer"
        return (
            f"{runtime} exec --bind {bind_dir}:{bind_dir} --pwd {wd} "
            f"{container} bash -c {quoted_cmd}"
        )

    user_flag = f"--user {os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else ""
    # --user's numeric UID/GID has no /etc/passwd entry in the image unless it
    # happens to match one baked in at build time, so HOME resolves to "/" --
    # anything writing to $HOME (e.g. insarhub's own dask temp-dir setup)
    # fails with a permission error against root's home. Point HOME at the
    # bind-mounted dir instead, which the user can always write to.
    #
    # No --pid=host: it doesn't actually help on Docker Desktop (Windows/Mac/
    # WSL2) since those run containers inside Docker Desktop's own VM, sharing
    # a PID namespace with *that* VM rather than the user's real host shell --
    # so a PID recorded inside the container would still be unrelated to
    # anything in the host's own process table. See isce2_base.py's
    # INSARHUB_HOST_PID (in _reinvoke_via_container/_step_executor) for how
    # container-run step liveness is actually tracked instead.
    return (
        f"docker run --rm {user_flag} -e HOME={bind_dir} "
        f"-v {bind_dir}:{bind_dir} -w {wd} {container} bash -c {quoted_cmd}"
    ).replace("  ", " ")
