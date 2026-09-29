"""critical-css: extract above-the-fold CSS with headless Chromium and check it."""

__version__ = "1.0.0"

from .core import DEFAULT_VIEWPORTS, Options, Result, run  # noqa: E402
from .minify import minify_css  # noqa: E402

__all__ = ["__version__", "DEFAULT_VIEWPORTS", "Options", "Result", "run", "minify_css"]
