### Install Locally

=== "Minimal"

    ??? note "Install InSARHub in a fresh environment "

        ```bash
        conda create -n insarhub python=3.12
        conda activate insarhub
        ```

    ??? note "Limit Windows support"
        Currently InSARHub only test running under Python version 3.11 in Windows environment 

    ```bash
    conda install insarhub -c conda-forge
    ```

    Or from pip (GDAL must be installed via conda first):

    ```bash
    conda install gdal
    pip install insarhub
    ```

=== "ISCE2 Processor"

    Adds local interferogram processing via ISCE2 `stackSentinel`.

    ??? note "No Windows Support"
        ISCE2 is only available on Linux and macOS (x86_64). Not available for Windows or Apple Silicon natively — use WSL2 or a Linux virtual machine.
    ??? note "Restricted numpy version"
        ISCE2 currently pins `numpy<2`

    Install InSARHub first, then add ISCE2 into the same environment:

    ```bash
    conda install insarhub -c conda-forge
    conda install isce2 -c conda-forge
    ```

    Via pip:

    ```bash
    
    conda install gdal isce2
    pip install insarhub
    ```

    Verify ISCE2 installed correctly:

    ```bash
    python -c "import isce; print(isce.__version__)"
    ```

=== "ISCE3 + Dolphin Processor"

    Adds local burst processing via ISCE3 / COMPASS (geocoded CSLCs) and dolphin (phase linking + time series)

    ??? note "No Windows Support"
        The ISCE3 / COMPASS / dolphin stack is Linux and macOS (x86_64) only — use WSL2 or a Linux virtual machine on other platforms.
        
    ??? note "Restricted numpy version"
        COMPASS currently pins `numpy<2`

    ```bash
    conda create -n isce3_dolphin python=3.12
    conda activate isce3_dolphin
    conda install -c conda-forge insarhub "isce3=*=*cpu*" compass sardem dolphin snaphu burst2safe gdal
    ```

    Verify the stack imports:

    ```bash
    python -c "import isce3, compass, dolphin; print('ok')"
    ```

=== "GMTSAR Processor"

    Adds local interferogram processing via GMTSAR plus MintPy time-series.
    ??? note "No Windows Support"
        `--system conda-linux-full` is **Linux x86_64 only**, use WSL2 or a Linux virtual environment on other platforms.

    ```bash
    # 1. Clone the full GMTSAR repo and build it (creates a conda env named "gmtsar")
    git clone https://github.com/gmtsar/gmtsar.git
    cd gmtsar
    python3 gmtsar/python/install.py --system conda-linux-full

    # 2. Add InSARHub + MintPy into that same env
    conda activate gmtsar
    conda install -c conda-forge insarhub mintpy

    # 3. Put GMTSAR's binaries on PATH (add to your shell profile to persist)
    export GMTSAR=$(pwd)
    export PATH=$GMTSAR/bin:$PATH
    ```

    Verify:

    ```bash
    which p2p_processing && gmt --version
    python -c "import insarhub, mintpy; print('ok')"
    ```

---

### Use Container

InSARHub support container via **Docker**,  check the [official guide](https://docs.docker.com/get-started/get-docker/) to install Docker on you machine. 

User may choose to run inside container or install base `InSARHub` locally and run each processor/analyzer via `--container` 

Currently InSARHub support:

=== "Hyp3 + Mintpy"
    Default InSARHub container that support sentinel-1 processing via Hyp3 and time-series analysis via Mintpy

    ```bash
    ghcr.io/jldz9/insarhub-base:dev

    ```

=== "ISCE2 + MintPy"

    Covers the `ISCE2_S1` processor and `ISCE2_Mintpy_SBAS` analyzer 

    ```bash
    ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    ```

=== "ISCE3 + Dolphin"

    Covers `ISCE3_Burst` (Sentinel-1 bursts) and `ISCE3_NISAR` (NISAR GSLC), plus the `ISCE3_Dolphin_PL` analyzer 
    
    ```bash
    ghcr.io/jldz9/insarhub-isce3-dolphin:dev

    ```

=== "GMTSAR + Mintpy"

    Covers the `GMTSAR_S1` processor and GMTSAR analyzers 

    ```bash
    ghcr.io/jldz9/insarhub-gmtsar-mintpy:dev
    ```
---

### Development Setup

=== "Default"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    pip install -e .
    ```

    !!! note "Windows: use Python 3.11"
        `environment.yml` allows Python 3.11 or 3.12, but only 3.11 is currently supported on Windows. If the solve picks 3.12, edit the `python` line in `environment.yml` to `python=3.11` before running `conda env create`.

=== "ISCE2 Processor"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    conda install -c conda-forge "numpy<2.0" isce2
    pip install -e .
    ```

=== "ISCE3 + Dolphin Processor"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda create -n isce3_dolphin python=3.12
    conda activate isce3_dolphin
    conda install -c conda-forge "isce3=*=*cpu*" compass sardem dolphin snaphu burst2safe gdal
    pip install -e .
    ```

=== "GMTSAR Processor"

    ```bash
    # Build GMTSAR from source into a conda env named "gmtsar"
    git clone https://github.com/gmtsar/gmtsar.git
    cd gmtsar
    python3 gmtsar/python/install.py --system conda-linux-full
    export GMTSAR=$(pwd) && export PATH=$GMTSAR/bin:$PATH

    # Add MintPy + InSARHub (editable) into that env
    conda activate gmtsar
    conda install -c conda-forge mintpy
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    pip install -e .
    ```

??? note "Using mamba for faster solves"

    Replace `conda` with `mamba` in any of the above commands if you have [mamba](https://mamba.readthedocs.io/en/latest/installation/mamba-installation.html) installed.
