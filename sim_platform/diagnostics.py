"""Diagnostic logging for the platform process.

Every module logs through ``logging.getLogger(__name__)``, so all records sit under the
``sim_platform`` logger. As a library the package installs only a ``NullHandler``: nothing
is printed unless the application configures logging. The CLI and Desktop call
``configure_logging()``; Python users can call it too or attach their own handlers.

Logged records are diagnostics for maintainers (handled failures with their traceback,
lifecycle problems). They are not the experiment record: orders, fills and Agent activity
stay in the database and operation logs. Records never include credentials.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional, TextIO

ROOT_LOGGER = 'sim_platform'
LEVEL_ENVIRONMENT = 'MARKET_LAB_LOG_LEVEL'
FORMAT_ENVIRONMENT = 'MARKET_LAB_LOG_FORMAT'
LEVELS = ('debug', 'info', 'warning', 'error', 'critical')


class _PlatformHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """The handler configure_logging() owns, so a second call can replace it."""


class JsonFormatter(logging.Formatter):
    """Format one record per line as a JSON object for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            'time': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'level': record.levelname.lower(),
            'logger': record.name,
            'message': record.getMessage(),
            'thread': record.threadName,
        }
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(
    level: Optional[str] = None,
    *,
    json_format: Optional[bool] = None,
    stream: Optional[TextIO] = None,
) -> logging.Logger:
    """Send platform diagnostics to ``stream`` (standard error by default).

    Args:
        level: One of ``LEVELS``. None reads ``MARKET_LAB_LOG_LEVEL``, else ``warning``.
        json_format: One JSON object per line when True. None reads
            ``MARKET_LAB_LOG_FORMAT`` (``json`` or ``text``), else text.
        stream: Destination; standard error when omitted.

    Returns:
        The ``sim_platform`` logger. Calling again replaces the handler it added before.

    Raises:
        ValueError: If the level or format name is not recognised.
    """
    name = (level or os.environ.get(LEVEL_ENVIRONMENT) or 'warning').lower()
    if name not in LEVELS:
        raise ValueError('log level must be one of: ' + ', '.join(LEVELS))
    if json_format is None:
        style = os.environ.get(FORMAT_ENVIRONMENT, 'text').lower()
        if style not in ('json', 'text'):
            raise ValueError('log format must be json or text')
        json_format = style == 'json'
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        if isinstance(handler, _PlatformHandler):
            logger.removeHandler(handler)
    handler = _PlatformHandler(stream or sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if json_format
        else logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    )
    logger.addHandler(handler)
    logger.setLevel(name.upper())
    return logger
