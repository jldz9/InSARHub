# InSARHub + ISCE2 processing container.
#
# Lets ISCE2_S1 (and the MintPy-family analyzers) run via `--container
# /path/to/image` instead of requiring ISCE2/MintPy installed on the host —
# see src/insarhub/utils/container.py (wrap_container_cmd) for how InSARHub
# re-invokes itself inside this image, bind-mounting the workdir at the same
# path so output files land on the host exactly like a native run.
#
# Build:
#   docker build -t insarhub-isce2:latest .
#
# Use:
#   insarhub processor -N ISCE2_S1 -w /path/to/workdir submit --container insarhub-isce2:latest
#   insarhub analyzer  -N ISCE2_Mintpy_SBAS -w /path/to/workdir run  --container insarhub-isce2:latest
#
# Publish to GHCR:
#   docker tag insarhub-isce2:latest ghcr.io/jldz9/insarhub-isce2:latest
#   docker push ghcr.io/jldz9/insarhub-isce2:latest

FROM condaforge/miniforge3:24.9.2-0

# Links this image to the jldz9/InSARHub repo on GHCR even when pushed
# manually from the command line (rather than via a GitHub Actions workflow,
# which would link it automatically) -- see
# https://docs.github.com/en/packages/learn-github-packages/connecting-a-repository-to-a-package
LABEL org.opencontainers.image.source="https://github.com/jldz9/InSARHub"

COPY environment.yml /opt/environment.yml

# mamba's solver is far faster than plain conda for this dependency graph;
# environment.yml pins numpy<2.0 at the top-level conda dependency list (not
# just under pip:) so conda's own solver honors the ceiling when resolving
# gdal/mintpy -- avoids the numpy 2.x ABI mismatch that breaks their
# compiled extensions.
RUN mamba env create -f /opt/environment.yml \
    && mamba clean -afy

# ISCE2 and insarhub itself aren't in environment.yml (that file is shared
# with the plain, ISCE-less install) -- add both as separate steps into the
# same env, one at a time rather than solved together: solving isce2 +
# insarhub + "numpy<2.0" in one mamba call let the solver quietly violate the
# numpy pin anyway (numpy ended up at 2.5.1), apparently because conda-forge's
# insarhub build wants numpy>=2.0 and the joint solve traded the pin away to
# satisfy it. Installing insarhub first, then isce2 as its own follow-up
# call (each with an explicit numpy<2.0 request), keeps each solve smaller
# and makes the pin actually stick.
RUN mamba install -n insarhub -c conda-forge "numpy<2.0" insarhub \
    && mamba clean -afy
RUN mamba install -n insarhub -c conda-forge "numpy<2.0" isce2 \
    && mamba clean -afy

# insarhub's ISCE2_S1 processor discovers ISCE2 via two functions that expect
# different bases for the same conda env: _check_isce2() wants ISCE_HOME to
# be the isce *package* dir (.../site-packages/isce, ISCE2's own documented
# convention -- it looks for $ISCE_HOME/applications/topsApp.py), while
# _find_topsstack() wants ISCE_HOME to be the conda *env root* (it looks for
# $ISCE_HOME/share/isce2/topsStack/stackSentinel.py, where conda-forge's
# isce2 package actually ships topsStack). Symlink applications/ up to the
# env root so ISCE_HOME=env root satisfies both at once.
ENV ISCE_HOME=/opt/conda/envs/insarhub
RUN ln -s /opt/conda/envs/insarhub/lib/python3.12/site-packages/isce/applications \
          /opt/conda/envs/insarhub/applications

ENV PATH=/opt/conda/envs/insarhub/bin:$PATH

# No custom ENTRYPOINT/CMD: wrap_container_cmd() (src/insarhub/utils/container.py)
# already runs `docker run ... <image> bash -c '<command>'` itself -- an
# ENTRYPOINT here would get the CMD appended to it (`bash -c` + `bash -c
# '<command>'`), launching a nested interactive bash that just exits on EOF
# stdin without ever running <command>.
