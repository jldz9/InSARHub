# -*- coding: utf-8 -*-
import dataclasses
from pathlib import Path

from .registry import Processor, Downloader, Analyzer
from .base import Hyp3Processor


class InSAREngine:
    """Pipeline engine that chains a downloader, processor, and analyzer.

    All components share a single *workdir*. Pass pre-built instances directly
    to ``__init__``, or use ``InSAREngine.build()`` to create components from
    registry names.

    Example — using pre-built instances::

        from insarhub.downloader.s1_slc import S1_SLC
        from insarhub.processor.hyp3_s1 import Hyp3_S1
        from insarhub.analyzer.hyp3_sbas import Hyp3_SBAS
        from insarhub.config import S1_SLC_Config, Hyp3_S1_Config, Hyp3_SBAS_Config

        engine = InSAREngine(
            workdir="~/insar/my_project",
            downloader=S1_SLC(S1_SLC_Config(intersectsWith="POINT(-122 37)")),
            processor=Hyp3_S1(Hyp3_S1_Config(pairs=[("S1A...", "S1B...")])),
            analyzer=Hyp3_SBAS(Hyp3_SBAS_Config()),
        )
        engine.run()

    Example — using registry names::

        engine = InSAREngine.build(
            workdir="~/insar/my_project",
            downloader="S1_SLC",
            processor="Hyp3_S1",
            analyzer="Hyp3_SBAS",
        )
        engine.run()
    """

    def __init__(
        self,
        workdir: str | Path,
        downloader=None,
        processor=None,
        analyzer=None,
    ):
        self.workdir = Path(workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader
        self.processor = processor
        self.analyzer = analyzer

        for component in (downloader, processor, analyzer):
            if component is not None:
                self._sync_workdir(component)

    # ------------------------------------------------------------------ #
    #  Factory                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def build(
        cls,
        workdir: str | Path,
        downloader: str | None = None,
        processor: str | None = None,
        analyzer: str | None = None,
        downloader_config=None,
        processor_config=None,
        analyzer_config=None,
    ) -> "InSAREngine":
        """Instantiate components from registry names, then build the engine.

        All components will share *workdir*. If a config object is provided
        without a ``workdir`` field, the engine workdir is injected before
        the component is instantiated.

        Args:
            workdir: Common working directory for all pipeline stages.
            downloader: Registered downloader name, e.g. ``"S1_SLC"``.
            processor: Registered processor name, e.g. ``"Hyp3_S1"``.
            analyzer: Registered analyzer name, e.g. ``"Hyp3_SBAS"``.
            downloader_config: Config object, or ``None`` to use the class default.
            processor_config: Config object, or ``None`` to use the class default.
            analyzer_config: Config object, or ``None`` to use the class default.

        Returns:
            A fully configured ``InSAREngine`` instance.
        """
        workdir_path = Path(workdir).expanduser().resolve()

        def _inject(config):
            if config is None:
                return None
            if dataclasses.is_dataclass(config) and not isinstance(config, type) and hasattr(config, "workdir"):
                return dataclasses.replace(config, workdir=workdir_path)
            return config

        dl = Downloader.create(downloader, config=_inject(downloader_config)) if downloader else None
        pr = Processor.create(processor, config=_inject(processor_config)) if processor else None
        an = Analyzer.create(analyzer, config=_inject(analyzer_config)) if analyzer else None

        return cls(workdir_path, downloader=dl, processor=pr, analyzer=an)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _sync_workdir(self, component) -> None:
        """Push the engine workdir into a component's config."""
        config = getattr(component, "config", None)
        if config is None:
            return
        if dataclasses.is_dataclass(config) and not isinstance(config, type) and hasattr(config, "workdir"):
            component.config = dataclasses.replace(config, workdir=self.workdir)
        elif not dataclasses.is_dataclass(config) and hasattr(config, "workdir"):
            setattr(config, "workdir", self.workdir)

    def _log(self, msg: str) -> None:
        print(f"[InSAREngine] {msg}")

    # ------------------------------------------------------------------ #
    #  Pipeline                                                            #
    # ------------------------------------------------------------------ #

    def run(
        self,
        skip_download: bool = False,
        skip_process: bool = False,
        skip_analyze: bool = False,
        watch: bool = True,
        refresh_interval: int = 300,
    ) -> None:
        """Run the full pipeline sequentially.

        Stages execute in order: download → process → analyze. A failure in
        any stage raises an exception and stops the pipeline.

        Args:
            skip_download: Skip the download stage even if a downloader is set.
            skip_process: Skip the processing stage even if a processor is set.
            skip_analyze: Skip the analysis stage even if an analyzer is set.
            watch: For HyP3 processors — poll until all jobs finish and
                download results automatically. If ``False``, save job IDs
                and return; call ``processor.watch()`` manually later.
            refresh_interval: Poll interval in seconds when ``watch=True``.
        """
        stages_ran = 0

        # --- Stage 1: Download ---
        if self.downloader is not None and not skip_download:
            self._log(f"Stage 1 — Downloading to {self.workdir}")
            try:
                self.downloader.search()
                self.downloader.download()
            except Exception as exc:
                raise RuntimeError(f"[InSAREngine] Download stage failed: {exc}") from exc
            stages_ran += 1
        elif self.downloader is not None:
            self._log("Stage 1 — Download skipped.")

        # --- Stage 2: Process ---
        if self.processor is not None and not skip_process:
            self._log("Stage 2 — Processing...")
            try:
                if isinstance(self.processor, Hyp3Processor):
                    self.processor.submit()
                    if watch:
                        self.processor.watch(refresh_interval=refresh_interval)
                    else:
                        self.processor.save()
                        self._log(
                            "Jobs submitted and saved. "
                            "Call processor.watch() or processor.download() to retrieve results."
                        )
                else:
                    self.processor.run()
            except Exception as exc:
                raise RuntimeError(f"[InSAREngine] Process stage failed: {exc}") from exc
            stages_ran += 1
        elif self.processor is not None:
            self._log("Stage 2 — Process skipped.")

        # --- Stage 3: Analyze ---
        if self.analyzer is not None and not skip_analyze:
            self._log("Stage 3 — Analyzing...")
            try:
                self.analyzer.run()
            except Exception as exc:
                raise RuntimeError(f"[InSAREngine] Analyze stage failed: {exc}") from exc
            stages_ran += 1
        elif self.analyzer is not None:
            self._log("Stage 3 — Analyze skipped.")

        if stages_ran:
            self._log(f"Pipeline complete. Outputs in {self.workdir}")
        else:
            self._log("No stages ran. Add components or set skip_* to False.")
