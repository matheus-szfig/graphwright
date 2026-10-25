"""graphwright — graph-driven browser automation.

Workflows are YAML graphs of recognizable page states (``engine``), observed through
an isolated-world page reader (``observe``), driven by a stealth Playwright browser
(``browser``) inside a Celery worker (``queue``, ``tasks``, ``worker``).
"""

__version__ = "0.1.0"
