"""The tests never touch the network: a connection anywhere fails at once, as
if offline, so a test that forgets to stub a download can't quietly reach a
real server (and take seconds over it). And the maps are drawn in-process."""

import socket


def _offline(*args, **kwargs):
    raise OSError("no network in the tests")


def _local_only(real):
    """socket.connect for this machine's own sockets (the processes that draw the maps talk over them) only."""
    def connect(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            return real(self, address)
        return _offline()
    return connect


socket.socket.connect = _local_only(socket.socket.connect)
socket.socket.connect_ex = _local_only(socket.socket.connect_ex)
socket.create_connection = _offline

# The maps are drawn on the worker thread, where the tests can watch (and
# mock) it; the processes have a test of their own.
from golf import camera  # noqa: E402

camera.WORKERS = 0
