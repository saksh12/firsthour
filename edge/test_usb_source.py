"""USB transport regressions without pyusb or hardware installed."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from edge.sensor_node import UsbCdcSource


class UsbError(OSError):
    def __init__(self, code):
        super().__init__(f"libusb {code}")
        self.backend_error_code = code


@pytest.fixture
def usb(monkeypatch):
    modules = {name: ModuleType(name) for name in (
        "usb", "usb.backend", "usb.backend.libusb1", "usb.core", "usb.util"
    )}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
        if "." in name:
            parent, attr = name.rsplit(".", 1)
            setattr(modules[parent], attr, module)
    usb = modules["usb"]
    usb.backend.libusb1.get_backend = MagicMock()
    usb.core.USBTimeoutError = TimeoutError
    usb.core.USBError = UsbError
    usb.core.find = MagicMock()
    usb.util.ENDPOINT_OUT = 0
    usb.util.endpoint_direction = lambda address: address & 0x80
    usb.util.claim_interface = MagicMock()
    usb.util.dispose_resources = MagicMock()
    return usb


@pytest.fixture
def connection(usb):
    comm = MagicMock(bInterfaceClass=0x02, bInterfaceNumber=0)
    incoming = MagicMock(bEndpointAddress=0x81, wMaxPacketSize=64)
    outgoing = MagicMock(bEndpointAddress=0x01)
    data = MagicMock(bInterfaceClass=0x0A, bInterfaceNumber=1)
    data.__iter__.side_effect = lambda: iter([incoming, outgoing])
    dev = usb.core.find.return_value
    dev.get_active_configuration.return_value = [comm, data]
    dev.is_kernel_driver_active.side_effect = NotImplementedError("QNX")
    return UsbCdcSource(), dev, incoming, outgoing


@pytest.mark.parametrize("driver", [False, True, NotImplementedError("QNX")])
def test_claims_interfaces_when_driver_query_is_unsupported(usb, driver):
    comm = MagicMock(bInterfaceClass=0x02, bInterfaceNumber=0)
    incoming = MagicMock(bEndpointAddress=0x81, wMaxPacketSize=64)
    outgoing = MagicMock(bEndpointAddress=0x01)
    data = MagicMock(bInterfaceClass=0x0A, bInterfaceNumber=1)
    data.__iter__.side_effect = lambda: iter([incoming, outgoing])
    dev = usb.core.find.return_value
    dev.get_active_configuration.return_value = [comm, data]
    if isinstance(driver, Exception):
        dev.is_kernel_driver_active.side_effect = driver
    else:
        dev.is_kernel_driver_active.return_value = driver

    source = UsbCdcSource()

    assert [call.args[1] for call in usb.util.claim_interface.call_args_list] == [0, 1]
    assert dev.detach_kernel_driver.call_count == (2 if driver is True else 0)
    dev.ctrl_transfer.assert_called_once_with(0x21, 0x22, 1, 0)
    incoming.read.side_effect = [b"S,1,20,0,0,1,-1\nACK", TimeoutError(), b",L\nB\n"]
    assert source.readline(0) == "S,1,20,0,0,1,-1"
    incoming.read.assert_called_once_with(64, timeout=1)
    assert source.readline(0.05) is None
    assert source.readline(0.05) == "ACK,L"
    assert source.readline(0.05) == "B"
    source.write("L")
    outgoing.write.assert_called_once_with(b"L\n", timeout=500)


def test_usb_permission_errors_are_not_ignored(usb):
    dev = usb.core.find.return_value
    dev.get_active_configuration.return_value = [
        MagicMock(bInterfaceClass=0x02, bInterfaceNumber=0),
        MagicMock(bInterfaceClass=0x0A, bInterfaceNumber=1),
    ]
    dev.is_kernel_driver_active.side_effect = PermissionError("USB denied")
    with pytest.raises(PermissionError, match="USB denied"):
        UsbCdcSource()
    usb.util.claim_interface.assert_not_called()


@pytest.mark.parametrize("code", [-1, -4, -9])
def test_reset_reclaims_usb_and_discards_partial_lines(usb, connection, monkeypatch, code):
    source, dev, incoming, outgoing = connection
    now = [10.0]
    monkeypatch.setattr("edge.sensor_node.time.monotonic", lambda: now[0])
    source._buf = b"ACK,"
    incoming.read.side_effect = UsbError(code)
    assert source.readline(0.05) is None
    usb.util.dispose_resources.assert_called_once_with(dev)
    assert not source.reconnect()  # bounded retry cadence, no busy loop
    now[0] += 1
    usb.core.find.return_value = None
    assert not source.reconnect()  # board still rebooting
    source.write("U")
    outgoing.write.assert_not_called()
    now[0] += 1
    usb.core.find.return_value = dev
    assert source.reconnect()
    assert not source.reconnect()  # healthy connection is not reopened
    incoming.read.side_effect = [b"S,10,50,0,0,-1,-1\n"]
    assert source.readline(0.05) == "S,10,50,0,0,-1,-1"
    assert usb.util.claim_interface.call_count == 4
    outgoing.write.assert_not_called()  # never replay a queued unlock on reconnect


def test_reset_during_a_write_does_not_retry_the_unlock(usb, connection, monkeypatch):
    source, dev, incoming, outgoing = connection
    monkeypatch.setattr("edge.sensor_node.time.monotonic", lambda: 10.0)
    outgoing.write.side_effect = UsbError(-4)
    source.write("U")
    monkeypatch.setattr("edge.sensor_node.time.monotonic", lambda: 11.0)
    assert source.reconnect()
    assert outgoing.write.call_count == 1


def test_reset_during_reclaim_retries_later(usb, connection, monkeypatch):
    source, dev, incoming, outgoing = connection
    now = [10.0]
    monkeypatch.setattr("edge.sensor_node.time.monotonic", lambda: now[0])
    incoming.read.side_effect = UsbError(-4)
    source.readline(0)
    now[0] += 1
    dev.get_active_configuration.side_effect = UsbError(-4)
    assert not source.reconnect()
    dev.get_active_configuration.side_effect = None
    now[0] += 1
    assert source.reconnect()


@pytest.mark.parametrize("code", [-3, -6])
def test_access_and_busy_errors_still_fail(usb, connection, code):
    source, dev, incoming, outgoing = connection
    incoming.read.side_effect = UsbError(code)
    with pytest.raises(UsbError):
        source.readline(0.05)
    usb.util.dispose_resources.assert_not_called()
