"""Named session commands and the shared scripted/interactive input loop."""

from __future__ import annotations

from dataclasses import dataclass
import select
import sys
from typing import Callable, Dict, List, Optional, Type


# A count argument accepts 1 to this many repetitions.
MAX_COMMAND_COUNT = 1_000_000
# How long an interactive session waits for input before advancing playback.
INPUT_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class SessionCommand:
    """One named session command with its usage text and argument count bounds."""

    handler: Callable[[List[str]], object]
    usage: str
    minimum: int = 0
    maximum: int = 0


class CommandRegistry:
    """The named commands of one interactive or scripted session.

    Each session builds its own registry; 'help' and 'quit' are always present.
    Invalid input raises ``error``.
    """

    def __init__(self, error: Type[Exception]) -> None:
        self.error = error
        self._commands: Dict[str, SessionCommand] = {}
        self.add('help', self._help)
        self.add('quit', lambda words: False)

    def add(
        self,
        name: str,
        handler: Callable[[List[str]], object],
        *,
        usage: Optional[str] = None,
        minimum: int = 0,
        maximum: int = 0,
    ) -> None:
        if name in self._commands:
            raise ValueError(f'duplicate session command: {name}')
        self._commands[name] = SessionCommand(handler, usage or name, minimum, maximum)

    @property
    def help_text(self) -> str:
        return 'Commands: ' + ', '.join(command.usage for command in self._commands.values())

    def _help(self, words: List[str]) -> None:
        print(self.help_text, file=sys.stderr)

    def dispatch(self, line: str) -> bool:
        words = line.strip().split()
        if not words:
            return True
        name, *arguments = words
        command = self._commands.get(name)
        if command is None:
            raise self.error(f'unknown command: {name}. {self.help_text}')
        if not command.minimum <= len(arguments) <= command.maximum:
            message = f'{name} takes no arguments' if command.maximum == 0 else command.usage
            raise self.error(message)
        return command.handler(arguments) is not False


def command_count(words: List[str], error: Type[Exception]) -> int:
    count = int(words[0]) if words else 1
    if not 1 <= count <= MAX_COMMAND_COUNT:
        raise error('count must be 1..1000000')
    return count


def run_commands(
    registry: CommandRegistry,
    script: Optional[str],
    *,
    keep_reading: Callable[[], bool],
    is_playing: Callable[[], bool],
    advance: Callable[[], None],
    failed: Callable[[], bool],
) -> None:
    # Scripts can query results after playback completes; interactive execution
    # ends when its run terminates. These are intentionally separate conditions.
    if script:
        for line in script.split(';'):
            if not registry.dispatch(line) or failed():
                break
        return

    print(registry.help_text, file=sys.stderr)
    while keep_reading():
        if is_playing():
            readable, _, _ = select.select([sys.stdin], [], [], INPUT_POLL_SECONDS)
            if not readable:
                advance()
                continue
        line = sys.stdin.readline()
        if not line or not registry.dispatch(line):
            break
