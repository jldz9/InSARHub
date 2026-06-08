---
title: 'InSARHub: A Modular Python Framework for End to End InSAR and Time-Series Processing with GUI Support'
tags:
  - Python
  - InSAR
  - Sentinel-1
  - remote sensing
  - geodesy
  - ground deformation
  - groundwater
  - time-series analysis
authors:
  - name: Jiawei Li
    orcid: 0000-0001-6678-4045
    affiliation: 1
  - name: Sayyed Mohammad Javad Mirzadeh
    orcid: 0000-0002-3809-882X
    affiliation: 1
  - name: Ryan Smith
    orcid: 0000-0002-3747-6868
    affiliation: 1
  - name: Zaichen Xiang
    orcid: 0000-0003-3565-9131
    affiliation: 1 
  - name: Yunyi Guan
    orcid: 0009-0003-2302-3212
    affiliation: 2
affiliations:
  - name: Department of Civil and Environmental Engineering, Colorado State University, Fort Collins, CO, USA
    index: 1
  - name: Department of Earth Sciences and Engineering, Missouri University of Science and Technology, Rolla, MO, USA
    index: 2
date: 8 June 2026
bibliography: paper.bib
---

# Summary

Interferometric Synthetic Aperture Radar (InSAR) is a remote sensing technique
that measures ground surface deformation at millimeter precision by comparing the phase of radar signals acquired on repeat satellite passes [@hanssen2001radar]. The full InSAR processing chain, from satellite data acquisition through interferogram generation to time-series inversion, involves multiple specialized software packages that must be manually configured and interoperably integrated. This complexity limits InSAR adoption among domain scientists who are not radar remote sensing specialists.

`InSARHub` is an open-source Python framework that automates the full InSAR processing chain through a Python application programming interface (API), command-line interface (CLI), and built-in web graphical user interface (GUI). The built-in pipelines drive each processing stage in sequence, storing all intermediate and final products in an organized directory structure for full reproducibility. Processing runs on either cloud services or local workstations and SLURM HPC clusters. This framework targets geoscientists, hydrologists, and civil engineers who need InSAR-derived displacement map and time-series data without spending time learning and maintaining multi-tool processing pipelines for different InSAR products.

# Statement of Need

InSAR has been widely applied to monitor and explore earthquakes, volcanic deformation, landslides, and groundwater extraction induced land subsidence for decades [@bürgmann2000synthetic; @amelung1999sensing]. The launch of ESA’s Sentinel-1 in 2014 substantially expanded free access to high quality SAR data, enabling large-scale InSAR studies that were previously limited by data availability and cost.

The standard InSAR processing workflow spans at least four distinct stages, each historically handled by separate tools and packages. For Sentinel-1 as an example:

(1) SLC data search and download (i.e. Alaska Satellite Facility (ASF) Vertex [@beck2019asf])

(2) interferogram generation (i.e. 
InSAR Scientific Computing Environment (ISCE2) [@rosen2012isce],Hyp3 [@hogenson2020hyp3], GMTSAR [@sandwell2011gmtsar])

(3) atmospheric correction (i.e. Python-based Atmospheric Phase Screen (PyAPS)
[@jolivet2011corrections], Generic Atmospheric Correction Online Service (GACOS) [@yu2018gacos])

(4) time-series inversion  (i.e. Miami InSAR time-series software in Python (MintPy) [@zhang2019mintpy], Stanford Method for Persistent Scatterers (StaMPS) [@hooper2007new],  Dolphin [@dolphin2024])

Similar fragmented toolchains exist for other SAR missions: ALOS-2 PALSAR stack processing relies on ISCE2 [@rosen2012isce] or GAMMA [@wegmuller1998gamma]; historical archives of SAR missions such as ERS-1/2, ENVISAT, ALOS-1 and RADARSAT-1/2 are supported by ISCE2, SNAP [@esa2024snap], or GMTSAR [@sandwell2011gmtsar]; and the recently launched NISAR mission is expected to be supported by ISCE3 [@isce3_2024] and Dolphin [@dolphin2024]. Each of these tools is well-suited to its stage. However, none of these packages provide support through the full chain of processing steps.

Connecting several packages into a working pipeline requires substantial scripting effort and familiarity with multiple software environments, which is a barrier that limits InSAR adoption among domain scientists who are not radar remote sensing specialists. `InSARHub` has been developed to address this gap by providing a unified, open source framework that automates the full InSAR processing chain from satellite data search and pair selection through interferogram generation and small baseline subset (SBAS) time-series analysis, accessible through a Python API, CLI, and web GUI.

# State of the Field

Several open-source tools address individual stages of the InSAR workflow:

- ISCE2 [@rosen2012isce] and ISCE3 [@isce3_2024] are authoritative interferogram processors maintained by JPL but require expert configuration of environment variables and setting, run-files, and auxiliary data, and produce no time-series output. 
- HyP3 [@hogenson2020hyp3] offers cloud-based interferogram generation through a web API but does not perform time-series analysis and requires users to manage data transfer, file organization, and downstream processing manually. 
- MintPy [@zhang2019mintpy] is the community standard for SBAS time-series inversion but accepts only pre-formatted interferogram stacks and provides no mechanism for data acquisition or interferogram generation. 
- LiCSBAS [@morishita2020licsbas] automates time-series analysis from the LiCSAR product catalog but is limited to that catalog and cannot ingest locally processed interferograms. 
- ARIA-tools [@agram2013new] processes ARIA standard products derived from Geocoded Unwrapped Interferogram (GUNW) files, covering one segment of the chain but not raw SLC acquisition or local HPC processing. 
- MiaplPy [@mirzaee2023miaplpy] and Dolphin [@dolphin2024] extend time-series analysis to distributed scatterers but likewise require pre-processed input data.

However, none of these packages spans the full chain from scene search to time-series output. Rather than adding workflow orchestration to any of them, `InSARHub` is built as an orchestration layer that treats each existing tool as an interchangeable backend, and is therefore a distinct and complementary contribution to the InSAR software ecosystem.

# Software Design

## Plugin Registry Architecture

`InSARHub` is organized around a plugin registry pattern and includes three core modules including `Downloader`, `Processor`, and `Analyzer`, each contain specifically designed classes that serve as pipelines for different satellite products and processing tools. For example, `Downloader` contains `S1_SLC` for Sentinel-1 scene retrieval; `Processor` contains `Hyp3_S1` for cloud-based interferogram generation and `ISCE_S1` for local HPC processing; `Analyzer` contains `Hyp3_SBAS` and `ISCE_SBAS` for InSAR time-series analysis using Mintpy connected with `Hyp3_S1` and `ISCE_S1`. Each class registers itself automatically on import, and the `InSAREngine` orchestrator chains the selected components into a reproducible pipeline based on user selections (\autoref{fig:architecture}).

![InSARHub package architecture showing the five-level hierarchy from core package to SBAS workflows.\label{fig:architecture}](docs/advanced/fig/InSARHub_workflow.png)

The motivation to choose plugin registry architecture is because the InSAR software ecosystem evolves quickly: new processing backends emerge (e.g., ISCE3 for NISAR), cloud APIs change, and different research groups require different tool combinations. A plugin registry lets new backends be added without modifying engine or orchestration code, and the same `InSAREngine` chains any valid downloader–processor–analyzer combination to build a processing pipeline.This extensibility means additional packages, such as Dolphin for NISAR time-series analysis, can be contributed later by the core developers or the wider community, registering as new plugins without changes to the engine or existing workflows.

## Integrated Job Manager

`InSARHub` supports two execution environments behind the same configuration. The HyP3 backend submits interferogram jobs to ASF's cloud infrastructure, requiring no local SAR processor installation; The ISCE2 backend runs the full `stackSentinel` workflow locally, giving researchers direct control over processing parameters and intermediate products.

To allow similiar user experience across different processing backends, `InSARHub` includes a built-in job manager that tracks and controls processing jobs across both cloud and HPC backends through a unified interface. For HyP3 cloud jobs, the manager handles batch submission, credit accounting across EarthData accounts, status polling, and result download. For ISCE2 local execution, it wraps each of the sixteen `stackSentinel` processing steps in an independent SLURM `sbatch` script with configurable per-step resource allocations and dependency chaining. 

Each job is assigned a discrete status (PENDING, RUNNING, SUCCEEDED, FAILED) that is persisted to disk and surfaced in real time. Failed jobs can be retried individually from the point of failure without resubmitting completed steps, and interrupted workflows can be resumed by reloading the saved job state. Every parameter is serialized to `insarhub_config.json` alongside the output data. All Python API, CLI, and web GUI read and write the same config file, allowing workflow started in the GUI be inspected or continued programmatically via API or CLI, and vice versa.

## Graphical User Interface

In addition to command-line and python scripting, `InSARHub` provides a browser-based modern web GUI that covers every stage of the InSAR processing workflow without requiring command-line interaction (\autoref{fig:gui_overview}). From a single browser tab, users can search and filter Sentinel-1 scenes; select interferometric pairs; configure and submit processing jobs to either the HyP3 cloud or a local ISCE2 installation; monitor job status in real time; run SBAS analysis using MintPy; and visualize displacement results including velocity maps and time-series plots. 

![The `InSARHub` web GUI search interface showing Sentinel-1 scene footprints overlaid on an interactive map. Users define a bounding box, select date range, orbit, and beam mode, then search and browse available scenes directly in the browser before initiating download.\label{fig:gui_overview}](docs/frontend/fig/overview_light.png)

## Graph-Based Pair Selection with Quality Scoring

`InSARHub` constructs the pair network as a directed graph using temporal and perpendicular baseline constraints (\autoref{fig:network}), enforces minimum-degree connectivity, and optionally scores candidate pairs using snow cover fraction, accumulated precipitation, NDVI, and land-cover type to exclude pairs likely to decorrelate before interferogram generation. The interactive network editor provided by the GUI (\autoref{fig:gui_network}) allows users to freely toggle individual pairs, drag nodes to add connections, and override automated selections before confirming, enabling expert adjustment without modifying any configuration files.

![Interferometric pair network generated by `InSARHub` for Sentinel-1 track P100/F466 (30 scenes, 65 pairs). Nodes represent acquisitions; edge color encodes temporal baseline; node shade encodes network degree. The bar chart shows per-scene connection count, confirming minimum-degree connectivity across all acquisitions.\label{fig:network}](docs/quickstart/fig/ifgs_network.png)

![The `InSARHub` web GUI pair network editor showing 60 of 65 active pairs with coherence-based quality coloring (green: good, orange: risky, red: bad, dashed: removed). Users can interactively toggle individual pairs, drag nodes to add connections, and confirm the edited network before submission.\label{fig:gui_network}](docs/frontend/fig/analyzer_edit_network_graph_light.png)

# Research Impact Statement

`InSARHub` is actively used in the U.S. Army Engineer Research and Development Center (ERDC)-funded project *Improved Characterization of Groundwater Resources in Transboundary Watersheds Using Satellite Data and Integrated Models*. In this project, `InSARHub` drives simultaneous Sentinel-1 time-series processing across multiple aquifer systems around the globle, including basins in Iran, South America, China, and the western United States, generating InSAR-derived land subsidence time-series used to constrain groundwater storage change estimates. Processing three 10-year Sentinel-1 tracks required less than one month using `InSARHub`, compared to an estimated 1–2 months of sequential manual effort by experts.

`InSARHub` v0.3.0 is publicly available on PyPI (`pip install insarhub`), Conda-forge (`conda install insarhub -c conda-forge`) and GitHub (https://github.com/jldz9/InSARHub) under the MIT license, with full API documentation(https://jldz9.github.io/InSARHub/v0.3.0/), a quickstart tutorial with reproducible example data, and a test suite of 155 unit tests covering all major components. The package is designed to support community extension: new processor and analyzer backends register automatically without modifying core code, and the configuration file format is stable and version-tracked to support long-term reproducibility of published results.

# AI Usage Statement

The authors used generative AI to assist with manuscript wording improvements, software documentation generation, translation, and GUI design. All AI-suggested edits were reviewed and confirmed by the authors. No generative AI tools were used in software core architecture design decisions and development.

# Acknowledgements

The author thanks the U.S. Army Engineer Research and Development Center (ERDC) for supporting the groundwater characterization research (Grant W912HZ25C0016) that motivated the development of this package. The author also acknowledges the developers of MintPy [@zhang2019mintpy], HyP3 [@hogenson2020hyp3], ISCE2 [@rosen2012isce], and the Alaska Satellite Facility search API [@beck2019asf], whose software forms the computational backbone of the InSARHub processing pipeline.

# References
