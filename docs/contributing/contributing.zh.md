# 贡献指南

InSARHub 欢迎各类贡献——Bug 修复、新处理器/分析器、文档改进以及前端增强。



## 快速设置

```bash
git clone https://github.com/your-username/InSARHub.git
cd InSARHub
conda env create -f environment.yml
conda activate insarhub
pip install -e ".[dev]"
```

## 项目结构

```
InSARHub/
├── src/insarhub/
│   ├── analyzer/        # 分析器源代码
│   ├── app/
│   │   ├── frontend/    # React + Vite Web 界面（TypeScript）
│   │   └── routes/      # FastAPI 路由处理器
│   ├── cli/             # 命令行界面
│   ├── commands/        # CLI 与 GUI 共享的命令对象
│   ├── config/
│   │   ├── defaultconfig.py  # 各模块的 Dataclass 配置
│   │   └── paths.py          # 集中化工作目录路径布局
│   ├── core/            # 基类、注册表、引擎
│   ├── downloader/      # 下载器源代码
│   ├── processor/       # 处理器源代码
│   └── utils/           # 共享工具
├── docs/                # MkDocs 文档源文件
└── mkdocs.yml
```

## 提交更改

1. 创建功能分支：`git checkout -b feat/my-feature`
2. 保持提交专注 — 每次提交一个逻辑变更。
3. 在 `CHANGELOG.md` 的 `[Unreleased]` 下更新变更记录。
4. 向 `main` 分支发起 Pull Request，说明更改内容和原因。

## 报告 Bug

在 <https://github.com/jldz9/InSARHub/issues> 提交 Issue，包含：

- InSARHub 版本（`insarhub --version`）
- 操作系统和 Python 版本
- 最小复现步骤
- 相关日志或错误堆栈信息

---

详见 [后端贡献](backend.md) 和 [前端贡献](frontend.md)。
