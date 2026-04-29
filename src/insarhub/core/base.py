from abc import ABC, abstractmethod
from typing import List, Union, Optional, Any, Type

class BaseDownloader(ABC):
    """Abstract base class for all content downloaders.

    Subclasses must define a unique `name` class attribute. Upon subclass definition,
    the class is automatically registered in the global `Downloader` registry
    if `name` is set.

    Attributes:
        name (str): Unique identifier for this downloader. Used as the registry key.
        default_config (Optional[Type]): A config class instantiated with defaults
            when no config is provided to ``__init__``.

    Example:
        >>> class MyDownloader(BaseDownloader):
        ...     name = "my_downloader"
        ...     def search(self, query): ...
        ...     def download(self, item): ...
    """

    name: str
    default_config: Optional[Type] = None

    def __init__(self, config=None):
        """Initializes the downloader with an optional config.

        If no config is supplied and ``default_config`` is defined, an instance
        of ``default_config`` is created automatically.

        Args:
            config (Optional[Any]): A configuration object. If ``None`` and
                ``default_config`` is set, ``default_config()`` is used instead.
        """
        if config is None and self.default_config:
            self.config = self.default_config()
        else:
            self.config = config

    def __init_subclass__(cls, **kwargs):
        """Auto-registers named subclasses in the global Downloader registry.

        Args:
            **kwargs: Passed through to ``super().__init_subclass__``.
        """
        super().__init_subclass__(**kwargs)
        from .registry import Downloader
        if hasattr(cls, "name") and cls.name:
            Downloader.register(cls)
    
    # ------------------------------------------------------------------ #
    #  Abstract interface                                                #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def search(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def download(self, *args, **kwargs) -> Any:
        pass
    
    @abstractmethod
    def filter(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def footprint(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def summary(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def reset(self, *args, **kwargs) -> Any:
        pass

    def pipeline(self) -> None:
        """Print the full compatible processor → analyzer tree for this downloader.

        Example::

            a = Downloader.create('S1_SLC')
            a.pipeline()
            # S1_SLC
            # └─ Hyp3_S1
            #    ├─ Hyp3_SBAS
            #    └─ Mintpy_SBAS_Base_Analyzer
        """
        from .registry import Processor, Analyzer

        dl_name = type(self).name
        procs = [
            (n, c) for n, c in Processor._registry.items()
            if getattr(c, 'compatible_downloader', None) in (None, 'all', dl_name)
        ]

        lines = [dl_name]
        for pi, (pname, _) in enumerate(procs):
            last_proc = pi == len(procs) - 1
            proc_prefix = '└─' if last_proc else '├─'
            proc_indent = '   ' if last_proc else '│  '
            lines.append(f"{proc_prefix} {pname}")

            anals = [
                n for n, c in Analyzer._registry.items()
                if getattr(c, 'compatible_processor', None) in (None, 'all', pname)
            ]
            for ai, aname in enumerate(anals):
                last_anal = ai == len(anals) - 1
                anal_prefix = '└─' if last_anal else '├─'
                lines.append(f"{proc_indent}{anal_prefix} {aname}")

        if len(lines) == 1:
            lines.append('└─ (no compatible processors registered)')

        print('\n'.join(lines))


class LocalProcessor(ABC):
    """Abstract base class for local (on-machine) processing backends.

    Mirrors the Hyp3Processor interface so local processors feel identical
    from the user's perspective: submit jobs, refresh status, retry failures,
    watch until done, save/load job state, collect outputs.

    Subclasses that define a non-empty ``name`` attribute are automatically
    registered in the Processor registry.
    """

    name: str
    default_config: Optional[Type] = None

    def __init__(self, config=None):
        if config is None and self.default_config:
            self.config = self.default_config()
        else:
            self.config = config

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from .registry import Processor
        if hasattr(cls, "name") and cls.name:
            Processor.register(cls)

    @abstractmethod
    def submit(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def refresh(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def retry(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def watch(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def save(self, *args, **kwargs) -> Any:
        pass

class Hyp3Processor(ABC):
    """Abstract base class for HyP3 processing backends.

    This class defines the required interface for implementing a HyP3
    processor. Subclasses must implement all abstract methods to support
    job submission, monitoring, downloading, retrying, and credit checks.

    Subclasses that define a non-empty `name` attribute will be
    automatically registered in the Processor registry.

    Attributes:
        name (str): Unique identifier for this downloader. Used as the registry key.
        default_config (Optional[Type]): A config class instantiated with defaults
            when no config is provided to ``__init__``.
    """
    name: str
    default_config: Optional[Type] = None

    def __init__(self, config=None):
        if config is None and self.default_config:
            self.config = self.default_config()
        else:
            self.config = config

    def __init_subclass__(cls, **kwargs):
        """Automatically register subclasses in the Processor registry.

        Any subclass that defines a non-empty `name` attribute will be
        registered upon class creation.
        """
        super().__init_subclass__(**kwargs)
        # 1. Handle Registration (The Recruiter)
        from .registry import Processor
        if hasattr(cls, "name") and cls.name:
            Processor.register(cls)

    @abstractmethod
    def submit(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def refresh(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def download(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def retry(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def watch(self, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def save(self, *args, **kwargs) -> Any:
        pass
    
    @abstractmethod
    def check_credits(self, *args, **kwargs) -> Any:
        pass

class BaseAnalyzer(ABC):
    name: str
    default_config: Optional[Type] = None
    def __init__(self, config=None):
        if config is None and self.default_config:
            self.config = self.default_config()
        else:
            self.config = config

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 1. Handle Registration (The Recruiter)
        from .registry import Analyzer
        if hasattr(cls, "name") and cls.name:
            Analyzer.register(cls)

    @abstractmethod
    def run(self) -> Any:
        pass