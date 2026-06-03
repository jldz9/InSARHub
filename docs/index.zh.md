## 欢迎使用 InSARHub！:tada:
InSARHub 是一个开源软件包，旨在支持完整的 InSAR 处理流程。
该软件包的主要目标是提供流畅、易用的 InSAR 处理体验，涵盖多种卫星产品，从数据搜索和下载到时序分析，均可通过现代化的 Web UI 或命令行访问。

[快速开始](quickstart/install.md){ .md-button .md-button--lg .md-button--primary}
[Web UI 指南](quickstart/frontend.md){ .md-button .md-button--lg }

![InSARHub Web UI](frontend/fig/overview_light.png#only-light){: .doc-img-wide }
![InSARHub Web UI](frontend/fig/overview_dark.png#only-dark){: .doc-img-wide }
/// caption
InSARHub Web UI — 在同一平台上搜索、下载、处理和可视化 InSAR 数据。
///

## 支持的卫星

| 卫星 | 模式 | 下载 | 干涉图生成 | 时序分析 |
|------|------|------|-----------|---------|
| Sentinel-1 SLC | 混合 / 本地 / HPC | ✅ | ✅ | ✅ |

*[混合]:结合云端处理与本地处理的流程

本文档假设您对以下内容有基本了解：

- [Python](https://www.w3schools.com/python/)
- [Linux](https://www.geeksforgeeks.org/linux-commands-cheat-sheet/)
- [Conda](https://docs.conda.io/projects/conda/en/4.6.0/_downloads/52a95608c49671267e40c689e0bc00ca/conda-cheatsheet.pdf)


## 系统要求

InSARHub 设计运行于基于 Unix 的系统上，需要网络连接。Windows 用户可通过 WSL2（Windows Subsystem for Linux 2）运行 InSARHub，但兼容性仍在测试中，可能并非在所有情况下均能正常运行。

本软件包已在 Ubuntu 22.04.4 LTS 下测试。

*[WSL2]: Windows Subsystem for Linux 2

测试环境：

| 类别 | 配置 |
|------|------|
| CPU | AMD Ryzen 7 7800x3d |
| GPU | Nvidia 4070 Super |
| 内存 | 64GB DDR5 |
| 操作系统 | Ubuntu 22.04.4 LTS |

## 免责声明

本软件包按"现状"提供，不附带任何明示或暗示的保证。作者和贡献者不对软件包的功能、可靠性或适用性提供任何保证。使用本软件包即表示您承担与其使用相关的所有风险，开发者不对因使用本软件包而直接或间接产生的任何损害或问题负责。

## 需要帮助？

如有任何问题，欢迎提交 [![GitHub](https://img.shields.io/badge/Issue-%2312100E?logo=github&logoColor=black&color=white)](https://github.com/jldz9/InSARHub/issues)，
加入 [![Discord](https://img.shields.io/badge/Discord-%235865F2?logo=discord&logoColor=white)](https://discord.gg/RJJM42MBUU)，
或发送 [![Email](https://img.shields.io/badge/Email-%23EA4335?logo=gmail&logoColor=white)](mailto:jiaweiliwork@outlook.com)
