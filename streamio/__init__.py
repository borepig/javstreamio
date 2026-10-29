"""StreamIO: stream scraped JAV records through pluggable output formats."""
from .core import Plugin, available_plugins, register, run_plugin
from . import plugins  # noqa: F401  (importing registers the built-in plugins)

__all__ = ["Plugin", "register", "available_plugins", "run_plugin"]
