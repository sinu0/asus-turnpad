"""The USB side of the SpacePilot Pro's screen: frames, backlight, screen keys.

Interface 0 only (a vendor class interface without a kernel driver); interface 1
belongs to spacenavd, never touch it. pyusb is imported when the device is
opened, so the rest of the package works without it.
"""

from __future__ import annotations

import errno

from spacemouse_devices import PresentScreen, screens_present


class UsbMissing(Exception):
    """pyusb (or libusb) is not installed."""


class DeviceGone(Exception):
    """The screen cannot be reached any more (unplugged, no permission, I/O error)."""


# 0x41 = host to device, vendor request, interface; from the 3dxdisp-pro project.
BRIGHTNESS_REQUEST = 0x0A
BRIGHTNESS_TAIL = bytes([0xE2, 0x12, 0x00, 0x8C, 0x11, 0x00, 0x10, 0x00])
# Measured on a SpacePilot Pro: the device takes raw values up to 255 without
# error and the backlight keeps getting brighter above 100, brightest around
# 200 (not linear; 255 is not brighter than 200). 0 = backlight off. The
# config's percentages 0-100 are scaled onto 0..RAW_MAX.
RAW_MAX = 200


def raw_brightness(level: int) -> int:
    """The device's raw value for a brightness of 0 to 100 percent."""
    level = max(0, min(100, int(level)))
    return round(level * RAW_MAX / 100)
WRITE_TIMEOUT_MS = 2000
CONTROL_TIMEOUT_MS = 1000


def _usb():
    try:
        import usb.core
        import usb.util
    except ImportError as error:
        raise UsbMissing("pyusb is not installed (package python-pyusb, python3-usb or python3-pyusb)") from error
    return usb


class ScreenDevice:
    """An open screen; calls raise DeviceGone on a USB error."""

    def __init__(self, handle, present: PresentScreen, usb):
        self._dev = handle
        self._usb = usb
        self.present = present
        self.screen = present.model.screen
        self.closed = False

    @classmethod
    def open(cls, present: PresentScreen | None = None) -> "ScreenDevice":
        """Open the first present screen (or ``present``): detach a kernel
        driver from interface 0 and claim it."""
        usb = _usb()
        if present is None:
            found = screens_present()
            if not found:
                raise DeviceGone("no SpaceMouse screen found")
            present = found[0]
        screen = present.model.screen
        vendor, product = present.model.usb_ids[0]
        try:
            handle = None
            for candidate in usb.core.find(find_all=True, idVendor=vendor, idProduct=product):
                if candidate.bus == present.busnum and candidate.address == present.devnum:
                    handle = candidate
                    break
        except usb.core.NoBackendError as error:
            raise UsbMissing("libusb is not installed") from error
        except (usb.core.USBError, ValueError, OSError) as error:
            raise DeviceGone(f"cannot list USB devices: {error}") from error
        if handle is None:
            raise DeviceGone("the screen disappeared before it was opened")
        try:
            if handle.is_kernel_driver_active(screen.usb_interface):
                handle.detach_kernel_driver(screen.usb_interface)
            usb.util.claim_interface(handle, screen.usb_interface)
        except (usb.core.USBError, NotImplementedError, ValueError) as error:
            raise DeviceGone(f"cannot claim the screen: {error}") from error
        return cls(handle, present, usb)

    def _guard(self, call, *args, **kwargs):
        if self.closed:
            raise DeviceGone("the screen is closed")
        try:
            return call(*args, **kwargs)
        except self._usb.core.USBError as error:
            raise DeviceGone(str(error)) from error
        except (ValueError, OSError) as error:
            raise DeviceGone(str(error)) from error

    def send(self, frame: bytes) -> None:
        """One frame (screen.canvas.frame_bytes())."""
        self._guard(self._dev.write, self.screen.endpoint, frame, WRITE_TIMEOUT_MS)

    def set_brightness(self, level: int) -> None:
        """Backlight 0 to 100 percent (0 = off), sent as raw_brightness()."""
        self._guard(self._dev.ctrl_transfer, 0x41, BRIGHTNESS_REQUEST, 0, 0,
                    bytes([raw_brightness(level)]) + BRIGHTNESS_TAIL, CONTROL_TIMEOUT_MS)

    def read_keys(self, timeout_ms: int = 200) -> int | None:
        """The mask of the screen keys held (0 = all let go), None on a timeout."""
        if self.closed:
            raise DeviceGone("the screen is closed")
        try:
            data = bytes(self._dev.read(self.screen.keys_endpoint, 2, timeout_ms))
        except self._usb.core.USBTimeoutError:
            return None
        except self._usb.core.USBError as error:
            if error.errno in (errno.ETIMEDOUT, 110):  # older pyusb: a plain USBError
                return None
            raise DeviceGone(str(error)) from error
        except (ValueError, OSError) as error:
            raise DeviceGone(str(error)) from error
        if len(data) < 2:
            return None
        return data[0] | (data[1] << 8)

    def close(self) -> None:
        """Release interface 0; safe to call twice."""
        if self.closed:
            return
        self.closed = True
        try:
            self._usb.util.release_interface(self._dev, self.screen.usb_interface)
        except Exception:
            pass
        try:
            self._usb.util.dispose_resources(self._dev)
        except Exception:
            pass
