# InSARHub development container.
#
# Builds the same conda environment the repo documents for contributors --
# environment.yml at the repo root -- plus Node for the React frontend and the
# test tooling CI installs. InSARHub itself is NOT installed here: post-create.sh
# does an editable install from the mounted workspace, so the source you edit is
# the source that runs.
#
# Build context is the REPO ROOT, not this directory (see devcontainer.json's
# "build.context"), so environment.yml can be copied in.

# Deliberately NEWER than the condaforge/miniforge3:24.9.2-0 that docker/dev and
# docker/release pin. Those build artifacts users pull, where reproducibility
# beats freshness; this is a developer shell, where an Ubuntu 24.04 base (vs
# 24.9.2-0's 20.04) means a current git, glibc and toolchain. The two are
# allowed to drift -- what must NOT drift is environment.yml, and that is shared.
FROM condaforge/miniforge3:25.3.1-0

LABEL org.opencontainers.image.source="https://github.com/jldz9/InSARHub"

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID
ARG CONDA_ENV=insarhub

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# git for the repo, build-essential because a few pip deps still compile, procps
# because the VS Code server shells out to `ps`, and sudo so a contributor can
# apt-install something without rebuilding the image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        less \
        nano \
        openssh-client \
        procps \
        sudo \
        unzip \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 ships an `ubuntu` account already holding uid/gid 1000, which is
# the uid VS Code wants the container user to have so bind-mounted workspace
# files keep their ownership. Rename that account rather than picking a
# different uid -- a mismatched uid makes every file in /workspaces root-owned.
RUN if getent group "$USER_GID" >/dev/null; then \
        groupmod -n "$USERNAME" "$(getent group "$USER_GID" | cut -d: -f1)"; \
    else \
        groupadd --gid "$USER_GID" "$USERNAME"; \
    fi \
    && if getent passwd "$USER_UID" >/dev/null; then \
        usermod -l "$USERNAME" -d "/home/$USERNAME" -m \
                "$(getent passwd "$USER_UID" | cut -d: -f1)"; \
    else \
        useradd --uid "$USER_UID" --gid "$USER_GID" -m "$USERNAME"; \
    fi \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > "/etc/sudoers.d/$USERNAME" \
    && chmod 0440 "/etc/sudoers.d/$USERNAME"

# THE dev environment definition, shared with contributors installing on bare
# metal. Copied on its own so editing unrelated files does not re-solve conda.
COPY environment.yml /tmp/environment.yml
RUN mamba env create -f /tmp/environment.yml \
    && rm /tmp/environment.yml \
    && mamba clean -afy

# Node for the React frontend under src/insarhub/app/frontend. Pinned to 20 to
# match .github/workflows/test.yml's actions/setup-node, so a frontend build
# that works here works in CI. Vite 7 needs >=20.19, which the 20.x conda-forge
# builds satisfy. Installed into the env rather than via apt/NodeSource to keep
# one toolchain to reason about.
RUN mamba install -n "$CONDA_ENV" -c conda-forge -y "nodejs=20" \
    && mamba clean -afy

# Test tooling, at build time so post-create.sh stays quick. pytest/pytest-cov/
# httpx are pyproject's [test] extra; packaging and pyyaml are imported directly
# by tier 2 (test_imports.py, test_docs_install_commands.py) without being
# declared there, and .github/workflows/test.yml installs them for the same
# reason.
RUN /opt/conda/envs/$CONDA_ENV/bin/python -m pip install --no-cache-dir \
        pytest>=8 pytest-cov httpx packaging pyyaml

# post-create.sh runs `pip install -e .` as $USERNAME, which writes the .pth and
# egg-link into the env -- so the env has to be writable by that user.
RUN chown -R "$USERNAME:$USER_GID" "/opt/conda/envs/$CONDA_ENV"

ENV CONDA_ENV=${CONDA_ENV}
ENV PATH=/opt/conda/envs/${CONDA_ENV}/bin:$PATH
# With LD_LIBRARY_PATH empty the loader picks the SYSTEM libstdc++, which lacks
# GLIBCXX_3.4.29, and importing pandas -- pulled in by MintPy and by
# dem_stitcher's DEM download -- dies with "GLIBCXX_3.4.29 not found". The env's
# own libstdc++ has the symbol. See docker/README.md, "The libstdc++ trap".
ENV LD_LIBRARY_PATH=/opt/conda/envs/${CONDA_ENV}/lib

USER $USERNAME

# Interactive shells land in the env: `conda init` wires up the hook, the
# `conda activate` line picks the env rather than base.
RUN conda init bash \
    && echo "conda activate ${CONDA_ENV}" >> "/home/$USERNAME/.bashrc"

# No ENTRYPOINT/CMD -- the devcontainer runtime supplies its own.
