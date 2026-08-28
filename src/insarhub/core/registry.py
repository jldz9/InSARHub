import dataclasses
import inspect
from copy import deepcopy

class Registry:
    def __init__(self):
        self._registry = {}
        self._aliases = {}          # legacy name -> canonical name

    def register(self, cls):
        self._registry[cls.name] = cls
        # Legacy names (e.g. an analyzer renamed *_SBAS -> *_TS) stay resolvable
        # so old saved insarhub_config.json files and CLI commands keep working,
        # but are hidden from available() so the UI/CLI list only canonical
        # names. `aliases` is an optional tuple class attribute.
        for alias in getattr(cls, "aliases", ()) or ():
            self._registry[alias] = cls
            self._aliases[alias] = cls.name
        return cls

    def create(self, name, config=None, **overrides):
        if name not in self._registry:
            raise ValueError(f"{name} not registered")
        cls = self._registry[name]

        if config is not None:
            final_config = deepcopy(config)
        elif hasattr(cls, "default_config"):
            final_config = cls.default_config()
        else:
            final_config = {}

        # Some processors (ISCE2_S1, GMTSAR_S1) take `pairs` as its own
        # constructor argument, separate from the config dataclass entirely
        # -- unlike Hyp3_S1, where pairs happens to also be a config field.
        # Route any override matching a real __init__ parameter (other than
        # config) straight to the constructor instead of treating it as a
        # config-field override -- otherwise dataclasses.replace() below
        # fails with "unexpected keyword argument" for any such processor.
        try:
            sig_params = set(inspect.signature(cls.__init__).parameters) - {"self", "config"}
        except (TypeError, ValueError):
            sig_params = set()
        ctor_kwargs = {k: v for k, v in overrides.items() if k in sig_params}
        overrides = {k: v for k, v in overrides.items() if k not in sig_params}

        if overrides:
            if dataclasses.is_dataclass(final_config):
                try:
                    final_config = dataclasses.replace(final_config, **overrides)
                except TypeError as e:
                    raise ValueError(f"Invalid override parameters: {e}")
            elif isinstance(final_config, dict):
                final_config.update(overrides)
            else:
                for key, value in overrides.items():
                    if hasattr(final_config, key):
                        setattr(final_config, key, value)
                    else:
                        raise AttributeError(f"'{type(final_config).__name__}' has no field '{key}'")
        return cls(config=final_config, **ctor_kwargs)

    def available(self):
        return [n for n in self._registry if n not in self._aliases]

Downloader = Registry()
Processor = Registry()
Analyzer = Registry()