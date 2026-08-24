"""Блокировка management-команд от параллельного запуска (ТЗ-02 раздел 8)."""
import contextlib
import fcntl
import os
import tempfile


class AlreadyRunning(Exception):
    pass


@contextlib.contextmanager
def command_lock(name: str):
    """Файловая блокировка: повторный запуск той же команды завершается сразу."""
    path = os.path.join(tempfile.gettempdir(), f'bazar_{name}.lock')
    handle = open(path, 'w')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise AlreadyRunning(f'Команда {name} уже выполняется')
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
