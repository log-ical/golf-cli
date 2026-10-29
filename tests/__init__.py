"""The tests never touch the network: a connection anywhere fails at once, as
if offline, so a test that forgets to stub a download can't quietly reach a
real server (and take seconds over it)."""

import socket


def _offline(*args, **kwargs):
    raise OSError("no network in the tests")


socket.socket.connect = _offline
socket.socket.connect_ex = _offline
socket.create_connection = _offline
