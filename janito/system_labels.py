"""System-prompt section labels with no intra-package dependencies.

Leaf module holding the labels that describe where the ``start`` section of
the system prompt came from (issue #86).  The constants used to live in
:mod:`janito.system_prompt`, which forced :mod:`janito.config_loaders` (the
module that *builds* the ``(config) ...`` labels) into a lazy import cycle
with it (issue #110).  Both modules now import from here instead.
"""

LABEL_BUILTIN = "built-in"
LABEL_CLI = "-S"
LABEL_CONFIG_PREFIX = "(config) "
