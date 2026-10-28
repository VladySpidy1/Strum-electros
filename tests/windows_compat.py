"""Early pytest plugin: run the Home Assistant test harness on Windows.

Loaded with ``-p tests.windows_compat`` (see pytest.ini) before the
pytest-homeassistant-custom-component plugin. On any other OS it does nothing.

Home Assistant does not support Windows, and its test plugin does not start there:

* ``homeassistant.runner`` imports ``fcntl`` (and other code imports ``resource``
  and ``termios``), which exist only on POSIX. They are replaced with empty stub
  modules. The tests never call them.
* The plugin calls ``pytest_socket.disable_socket()`` before every test. On Windows
  the asyncio event loop needs ``socket.socketpair()`` (an AF_INET pair on
  127.0.0.1) for its self-pipe, so a blocked ``socket.socket`` breaks every test.
  Only that call is turned off. ``socket_allow_hosts(["127.0.0.1"])`` stays in
  force, so ``socket.connect`` to anything other than 127.0.0.1 still fails.
  tests/conftest.py also replaces ``asyncio.open_connection`` and the cloud login
  with guards, so the integration cannot reach the network.
"""

from __future__ import annotations

import sys
import types


def _stub_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__doc__ = f"Windows test stub for the POSIX-only module {name!r}."

    def _noop(*_args: object, **_kwargs: object) -> int:
        return 0

    def __getattr__(attr: str) -> object:  # noqa: N807
        if attr.startswith("__"):
            raise AttributeError(attr)
        return _noop

    module.__getattr__ = __getattr__  # type: ignore[method-assign]
    return module


if sys.platform == "win32":
    for _name in ("fcntl", "resource", "termios"):
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = _stub_module(_name)

    try:
        import pytest_socket
    except ImportError:  # plain Python without the HA test harness
        pass
    else:

        def _keep_socket_creation(*_args: object, **_kwargs: object) -> None:
            """socket.socket stays usable; connects stay limited to 127.0.0.1."""

        pytest_socket.disable_socket = _keep_socket_creation
