=== "Default"

    ??? note "Create a fresh environment (recommended)"

        ```bash
        conda create -n insarhub python=3.12
        conda activate insarhub
        ```

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

    !!! note "Platform availability"
        ISCE2 is only available on Linux and macOS (x86_64). Not available for Windows or Apple Silicon natively — use WSL2 or a Linux HPC cluster.

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

=== "ISCE2 Processor"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment-isce2.yml -n insar_dev
    conda activate insar_dev
    pip install -e .
    ```

??? note "Using mamba for faster solves"

    Replace `conda` with `mamba` in any of the above commands if you have [mamba](https://mamba.readthedocs.io/en/latest/installation/mamba-installation.html) installed.
