"""USB transport regressions without pyusb or hardware installed."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from edge.sensor_node import UsbCdcSource


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
    usb.core.find = MagicMock()
    usb.util.ENDPOINT_OUT = 0
    usb.util.endpoint_direction = lambda address: address & 0x80
    usb.util.claim_interface = MagicMock()
    return usb


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
    assert source.readline(0.05) == "S,1,20,0,0,1,-1"
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
