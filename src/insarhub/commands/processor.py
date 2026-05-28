# -*- coding: utf-8 -*-
from pathlib import Path

from insarhub.core.base import Hyp3Processor
from .base import BaseCommand, CommandResult


class SubmitCommand(BaseCommand):
    """Wraps processor.submit() — submits jobs to HyP3."""

    def __init__(self, processor: Hyp3Processor, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor

    def run(self) -> CommandResult:
        try:
            self.progress("Submitting jobs to HyP3...", 0)
            batchs = self.processor.submit()
            total = sum(len(b) for b in batchs.values())
            self.progress(f"Submitted {total} jobs", 100)
            return CommandResult(
                success=True,
                message=f"Submitted {total} jobs",
                data=batchs,
            )
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class RefreshCommand(BaseCommand):
    """Wraps processor.refresh() — fetches latest job statuses from HyP3."""

    def __init__(self, processor: Hyp3Processor, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor

    def run(self) -> CommandResult:
        try:
            self.progress("Refreshing job statuses...", 0)
            batchs = self.processor.refresh()
            total = sum(len(b) for b in batchs.values())
            failed = len(getattr(self.processor, "failed_jobs", []))
            self.progress(f"Refreshed {total} jobs", 100)
            msg = f"Refreshed {total} jobs"
            if failed:
                msg += f" ({failed} failed)"
            return CommandResult(success=True, message=msg, data=batchs)
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class DownloadResultsCommand(BaseCommand):
    """Wraps processor.download() — downloads all succeeded HyP3 job outputs."""

    def __init__(self, processor: Hyp3Processor, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor

    def run(self) -> CommandResult:
        try:
            self.progress("Downloading HyP3 results...", 0)
            output_dir, dl_stats = self.processor.download()
            self.progress("Download complete", 100)
            return CommandResult(
                success=True,
                message=f"Results saved to {output_dir}",
                data={"output_dir": str(output_dir), **dl_stats},
                output_files=[output_dir] if output_dir else [],
            )
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class RetryCommand(BaseCommand):
    """Wraps processor.retry() — resubmits all failed jobs."""

    def __init__(self, processor: Hyp3Processor, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor

    def run(self) -> CommandResult:
        try:
            self.progress("Retrying failed jobs...", 0)
            batchs = self.processor.retry()
            if batchs is None:
                return CommandResult(success=True, message="No failed jobs to retry")
            total = sum(len(b) for b in batchs.values())
            self.progress(f"Resubmitted {total} jobs", 100)
            return CommandResult(
                success=True,
                message=f"Resubmitted {total} failed jobs",
                data=batchs,
            )
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class WatchCommand(BaseCommand):
    """
    Wraps processor.watch() — polls HyP3 until all jobs complete,
    downloading results as they succeed.

    This command blocks until all jobs are done (or the user interrupts).
    In the Panel frontend, run this in a background thread.
    """

    def __init__(self, processor: Hyp3Processor, refresh_interval: int = 300, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor
        self.refresh_interval = refresh_interval

    def run(self) -> CommandResult:
        try:
            self.progress(f"Watching jobs (refresh every {self.refresh_interval}s)...", 0)
            self.processor.watch(refresh_interval=self.refresh_interval)
            self.progress("All jobs complete", 100)
            return CommandResult(success=True, message="All jobs completed and downloaded")
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class SaveJobsCommand(BaseCommand):
    """Wraps processor.save() — persists job IDs to JSON for later resumption."""

    def __init__(self, processor: Hyp3Processor, save_path: Path | str | None = None, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor
        self.save_path = save_path

    def run(self) -> CommandResult:
        try:
            self.progress("Saving job IDs...", 0)
            path = self.processor.save(self.save_path)
            self.progress(f"Saved to {path}", 100)
            return CommandResult(
                success=True,
                message=f"Job IDs saved to {path}",
                data=path,
                output_files=[path],
            )
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])


class CheckCreditsCommand(BaseCommand):
    """Wraps processor.check_credits() — prints remaining HyP3 credits for all users."""

    def __init__(self, processor: Hyp3Processor, progress_callback=None):
        super().__init__(progress_callback)
        self.processor = processor

    def run(self) -> CommandResult:
        try:
            self.progress("Checking HyP3 credits...", 0)
            self.processor.check_credits()
            self.progress("Done", 100)
            return CommandResult(success=True, message="Credits checked (see output above)")
        except Exception as e:
            return CommandResult(success=False, message=str(e), errors=[str(e)])
