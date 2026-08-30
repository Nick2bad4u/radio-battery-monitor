from usb.backend.libusb0 import Backend

class USBError(IOError):
    errno: int | None

class Device:
    bus: int | None
    address: int | None

def find(*, idVendor: int, idProduct: int, backend: Backend) -> Device | None: ...

