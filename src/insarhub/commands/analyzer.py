# -*- coding: utf-8 -*-
from insarhub.core.base import BaseAnalyzer
from .base import BaseCommand, CommandResult, safe_command


class PrepDataCommand(BaseCommand):
    """
    Wraps analyzer.prep_data() — unzips HyP3 products, collects files,
    clips rasters to common overlap, and sets MintPy load parameters.

    Only applies to analyzers that implement prep_data()
    (e.g. Hyp3_Mintpy_SBAS). Gracefully fails for other analyzer types.
    """

    def __init__(self, analyzer: BaseAnalyzer, progress_callback=None):
        super().__init__(progress_callback)
        self.analyzer = analyzer

    @safe_command
    def run(self) -> CommandResult:
        if not hasattr(self.analyzer, "prep_data"):
            return CommandResult(
                success=False,
                message=f"{type(self.analyzer).__name__} does not support prep_data()",
                errors=[f"{type(self.analyzer).__name__} has no prep_data() method"],
            )
        self.progress("Preparing HyP3 data for MintPy...", 0)
        self.analyzer.prep_data()
        self.progress("Data preparation complete", 100)
        return CommandResult(success=True, message="Data preparation complete")


class AnalyzeCommand(BaseCommand):
    """Wraps analyzer.run() — executes the MintPy SBAS time-series workflow."""

    def __init__(self, analyzer: BaseAnalyzer, steps: list[str] | None = None, progress_callback=None):
        super().__init__(progress_callback)
        self.analyzer = analyzer
        self.steps = steps

    @safe_command
    def run(self) -> CommandResult:
        self.progress("Running MintPy time-series analysis...", 0)
        self.analyzer.run(self.steps)
        self.progress("Analysis complete", 100)
        return CommandResult(success=True, message="Analysis complete")


class PlotCommand(BaseCommand):
    """Wraps analyzer.plot() — (re)generates MintPy's pic/ figures from
    already-computed results. A standalone action distinct from run()'s own
    automatic post-run plotting, which only fires for a bulk multi-step
    run() call — see Mintpy_SBAS_Base_Analyzer.plot() for why."""

    def __init__(self, analyzer: BaseAnalyzer, progress_callback=None):
        super().__init__(progress_callback)
        self.analyzer = analyzer

    @safe_command
    def run(self) -> CommandResult:
        if not hasattr(self.analyzer, "plot"):
            return CommandResult(
                success=False,
                message=f"{type(self.analyzer).__name__} does not support plot()",
                errors=[f"{type(self.analyzer).__name__} has no plot() method"],
            )
        self.progress("Plotting results...", 0)
        self.analyzer.plot()
        self.progress("Plotting complete", 100)
        return CommandResult(success=True, message="Plotting complete")
