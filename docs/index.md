

## Welcome to InSARHub ! :tada:
InSARHub is an open-source package designed to support the full InSAR processing pipeline.
The primary goal of this package is to provide a streamlined and user-friendly InSAR processing experience across multiple satellite products, from data search and download through time-series analysis — all accessible via a modern web UI or the command line.

[QuickStart](quickstart/install.md){ .md-button .md-button--lg .md-button--primary}
[Web UI Guide](quickstart/frontend.md){ .md-button .md-button--lg }

![InSARHub Web UI](frontend/fig/overview_light.png#only-light){: .doc-img-wide }
![InSARHub Web UI](frontend/fig/overview_dark.png#only-dark){: .doc-img-wide }
/// caption
InSARHub web UI — search, download, process, and visualize InSAR data in one place.
///


## Supported Satellites

| Satellite | Mode | Download | IFG Generation | Timeseries Analysis |
|-----------|------|----------|----------------|---------------------|
| Sentinel-1 SLC | Mixed / Local / HPC | ✅ | ✅ | ✅ |

*[Mixed]:process pipeline that mixed with cloud processing and local processing

This document will assume you have fundamental understanding of:

- [Python](https://www.w3schools.com/python/)
- [Linux](https://www.geeksforgeeks.org/linux-commands-cheat-sheet/)
- [Conda](https://docs.conda.io/projects/conda/en/4.6.0/_downloads/52a95608c49671267e40c689e0bc00ca/conda-cheatsheet.pdf)




## System Requirement

InSARHub is designed to run on Unix-based systems. Internet connection is required.
Windows users can run InSARHub via WSL2 (Windows Subsystem for Linux 2), though compatibility is still under testing and may not function reliably in all cases.

The package has been tested under Ubuntu 22.04.4 LTS.

*[WSL2]: Windows Subsystem for Linux 2

Test environment:

| Category| Model               |
| ------  | -----------------   |
| CPU     | AMD Ryzen 7 7800x3d |
| GPU     | Nvidia 4070 Super   | 
| Memory  | 64GB DDR5           |
| Operate System| Ubuntu 22.04.4 LTS |

## Disclaimer

This package is provided "as-is" without any warranties, express or implied. The authors and contributors make no guarantees regarding the functionality, reliability, or suitability of the package for any particular purpose. By using this package, you assume all risks associated with its use, and the developers are not liable for any damages or issues that may arise, whether directly or indirectly, from its use.

## Need Help? 

Meanwhile, if you have any question relate to the package or the documentation, please feel free to submit [![GitHub](https://img.shields.io/badge/Issue-%2312100E?logo=github&logoColor=black&color=white)](https://github.com/jldz9/InSARHub/issues), 
join [![Discord](https://img.shields.io/badge/Discord-%235865F2?logo=discord&logoColor=white)](https://discord.gg/RJJM42MBUU), 
or send me [![Email](https://img.shields.io/badge/Email-%23EA4335?logo=gmail&logoColor=white)](mailto:jiaweiliwork@outlook.com)

