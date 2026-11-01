"""Raw USB access to the ANT USB stick (bulk endpoints, 64-byte packets).

On Windows the Dynastream driver exposes the stick through libusb-win32, so
pyusb is pointed at libusb0.dll explicitly.
"""
import os

import usb.core
import usb.util

VID = 0x0FCF
PIDS = (0x1008, 0x1009)  # USB Stick 2, USB-m
LIBUSB0_DLL = os.environ.get("LIBUSB0_DLL", r"C:\Windows\System32\libusb0.dll")


class AntUsbError(RuntimeError):
    pass


def _backend():
    if os.name != "nt" or not os.path.exists(LIBUSB0_DLL):
        return None  # let pyusb pick its default backend
    from usb.backend import libusb0

    return libusb0.get_backend(find_library=lambda _name: LIBUSB0_DLL)


class AntUsb:
    def __init__(self):
        backend = _backend()
        dev = None
        for pid in PIDS:
            dev = usb.core.find(idVendor=VID, idProduct=pid, backend=backend)
            if dev is not None:
                break
        if dev is None:
            raise AntUsbError(
                "No ANT USB stick found (VID 0FCF, PID 1008/1009). Check that it is "
                "plugged in and listed in Device Manager."
            )
        self.dev = dev
        try:
            dev.set_configuration()
        except usb.core.USBError as exc:
            raise AntUsbError(
                f"Cannot configure the stick: {exc}. Another program (Garmin ANT Agent, "
                "Zwift, a previous run of this tool) may hold it, or the driver is wrong."
            ) from exc

        intf = dev.get_active_configuration()[(0, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN,
        )
        if self.ep_out is None or self.ep_in is None:
            raise AntUsbError("Stick has no bulk IN/OUT endpoints - unexpected device.")

    @property
    def info(self) -> dict:
        def safe(attr):
            try:
                return getattr(self.dev, attr)
            except Exception:
                return None

        return {
            "vid": f"{self.dev.idVendor:04X}",
            "pid": f"{self.dev.idProduct:04X}",
            "product": safe("product"),
            "manufacturer": safe("manufacturer"),
            "bcdDevice": f"{self.dev.bcdDevice:04X}",
        }

    def write(self, data: bytes) -> None:
        self.ep_out.write(bytes(data), timeout=1000)

    def read(self, timeout_ms: int = 100) -> bytes:
        try:
            return bytes(self.ep_in.read(64, timeout=timeout_ms))
        except usb.core.USBError as exc:
            # The libusb-win32 (libusb0) backend reports an idle-read timeout as a
            # plain USBError("... timeout error") with errno None, not USBTimeoutError.
            if isinstance(exc, usb.core.USBTimeoutError) or "timeout" in str(exc).lower():
                return b""
            raise

    def close(self) -> None:
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
