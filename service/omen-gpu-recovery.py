"""Reconcile the NVIDIA 620.73.04 short CPU TDP request on the OMEN 8C58."""

from contextlib import contextmanager
import ctypes
import fcntl
import os
from pathlib import Path
import runpy
import struct
import sys


POWER = runpy.run_path(str(Path(__file__).with_name('omen-ac-power.py')))
ENABLED = POWER['GPU_RECOVERY_ENABLED']
OWNED = POWER['GPU_RECOVERY_OWNED']
SourcePending = POWER['GpuPowerSourcePending']
DRIVER_VERSION = '620.73.04'
PCI_ADDRESS = '0000:01:00.0'
PCI = Path('/sys/bus/pci/devices') / PCI_ADDRESS
RUNTIME_STATUS = PCI / 'power/runtime_status'
DMI = Path('/sys/class/dmi/id')
TARGET_MW = 15_000
INVALID_ID = 0xffffffff

SHORT_TDP_INDEX = 1
SHORT_TDP_MASK = 1 << SHORT_TDP_INDEX
SHORT_TDP_SOURCE = 247
POLICY_INFO_SIZE = 0x924
POLICY_SOURCE_SIZE = 0x328
POLICY_VALUE_OFFSET = 44 + 12 * SHORT_TDP_INDEX
POLICY_SOURCE_OFFSET = POLICY_VALUE_OFFSET + 4
GET_POLICY_INFO = 0x2080a630
GET_POLICY_SOURCE = 0x2080a632
SET_SHORT_TDP = 0x2080e633


# NVOS21_PARAMETERS and NVOS54_PARAMETERS in the matching NVIDIA nvos.h.
class RmAlloc(ctypes.Structure):
    _fields_ = [('client', ctypes.c_uint32), ('parent', ctypes.c_uint32),
                ('object', ctypes.c_uint32), ('object_class', ctypes.c_uint32),
                ('params', ctypes.c_uint64), ('size', ctypes.c_uint32), ('status', ctypes.c_uint32)]


class RmControl(ctypes.Structure):
    _fields_ = [('client', ctypes.c_uint32), ('object', ctypes.c_uint32),
                ('command', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                ('params', ctypes.c_uint64), ('size', ctypes.c_uint32), ('status', ctypes.c_uint32)]


def rm_ioctl(fd, operation, request):
    # _IOWR('F', NV_ESC_RM_*, request type) on x86_64 Linux.
    ioctl = 0xc0004600 | (ctypes.sizeof(request) << 16) | operation
    fcntl.ioctl(fd, ioctl, request)
    if request.status:
        raise RuntimeError(f'NVIDIA RM returned status 0x{request.status:x}')


class ShortTdpPolicy:
    def __init__(self, fd):
        self.fd = fd
        self.root = 0
        self.root = self.alloc(0, 0x41)  # NV01_ROOT_CLIENT
        ids = self.control(self.root, 0x201, bytearray(32 * 4))  # GET_ATTACHED_IDS
        gpu_id, *others = struct.unpack('<32I', ids)
        if gpu_id == INVALID_ID or any(value != INVALID_ID for value in others):
            raise RuntimeError('Expected a single attached NVIDIA GPU')
        info = self.control(self.root, 0x205, bytearray(struct.pack('<I28x', gpu_id)))  # GET_ID_INFO_V2
        device_id, subdevice_id = struct.unpack_from('<II', info, 8)
        device = self.alloc(self.root, 0x80, bytearray(struct.pack('<I52x', device_id)))  # NV0080
        self.subdevice = self.alloc(device, 0x2080, bytearray(struct.pack('<I', subdevice_id)))

        info = self.control(self.subdevice, GET_POLICY_INFO, bytearray(POLICY_INFO_SIZE))
        entry = 36 * SHORT_TDP_INDEX
        if (struct.unpack_from('<I', info, 4)[0] != 3 or info[entry + 0x26] != 3
                or struct.unpack_from('<III', info, entry + 0x28) != (1000, TARGET_MW, TARGET_MW)):
            raise RuntimeError('Unexpected NVIDIA short CPU TDP policy layout')

    def alloc(self, parent, object_class, data=None):
        buffer = ctypes.c_char.from_buffer(data) if data is not None else None
        params = RmAlloc(self.root, parent, 0, object_class,
                         ctypes.addressof(buffer) if buffer is not None else 0,
                         len(data) if data is not None else 0)
        rm_ioctl(self.fd, 0x2b, params)  # NV_ESC_RM_ALLOC
        return params.object

    def control(self, handle, command, data):
        buffer = ctypes.c_char.from_buffer(data)
        params = RmControl(self.root, handle, command, 0, ctypes.addressof(buffer), len(data))
        rm_ioctl(self.fd, 0x2a, params)  # NV_ESC_RM_CONTROL
        return data

    def source_params(self, value):
        data = bytearray(POLICY_SOURCE_SIZE)
        struct.pack_into('<I', data, 4, SHORT_TDP_MASK)
        struct.pack_into('<I', data, POLICY_VALUE_OFFSET, value)
        data[POLICY_SOURCE_OFFSET] = SHORT_TDP_SOURCE
        return data

    def read(self):
        data = self.control(self.subdevice, GET_POLICY_SOURCE, self.source_params(INVALID_ID))
        value = struct.unpack_from('<I', data, POLICY_VALUE_OFFSET)[0]
        if (struct.unpack_from('<I', data, 4)[0] != SHORT_TDP_MASK or value == INVALID_ID
                or data[POLICY_SOURCE_OFFSET] != SHORT_TDP_SOURCE):
            raise RuntimeError('NVIDIA did not return the short CPU TDP source value')
        return value

    def write(self, value):
        self.control(self.subdevice, SET_SHORT_TDP, self.source_params(value))
        if self.read() != value:
            raise RuntimeError(f'NVIDIA short CPU TDP did not read back as {value} mW')


def verify_hardware():
    if os.uname().machine != 'x86_64' or any(path.read_text().strip() != expected for path, expected in (
        (DMI / 'board_name', '8C58'), (DMI / 'bios_version', 'F.13'), (DMI / 'product_sku', '9R293EA#ABU'),
        (PCI / 'vendor', '0x10de'), (PCI / 'device', '0x28a0'),
    )):
        raise RuntimeError('GPU recovery requires the tested OMEN 8C58/F.13 and RTX 4060')
    version = Path('/sys/module/nvidia/version').read_text().strip()
    if version != DRIVER_VERSION:
        raise RuntimeError(f'GPU recovery supports NVIDIA {DRIVER_VERSION}. Loaded driver is {version}.')
    gpu = (Path('/proc/driver/nvidia/gpus') / PCI_ADDRESS / 'information').read_text()
    if 'Device Minor:' not in gpu or gpu.split('Device Minor:', 1)[1].splitlines()[0].strip() != '0':
        raise RuntimeError('Expected the internal NVIDIA GPU at /dev/nvidia0')


@contextmanager
def open_policy():
    with open('/dev/nvidia0', 'r+b', buffering=0), open('/dev/nvidiactl', 'r+b', buffering=0) as ctl:
        yield ShortTdpPolicy(ctl.fileno())


def release():
    if not OWNED.exists():
        return False
    verify_hardware()
    with open_policy() as policy:
        if policy.read() == TARGET_MW:
            policy.write(0)
    OWNED.unlink(missing_ok=True)
    return True


def apply(policy):
    original = policy.read()
    if original == TARGET_MW and OWNED.exists():
        return 'Active · 15 W request verified'
    if original != 0:
        OWNED.unlink(missing_ok=True)
        return f'Existing NVIDIA request · {original / 1000:g} W'

    # An interrupted write can still be undone by the next service run or ExecStopPost.
    OWNED.touch(mode=0o600)
    policy.write(TARGET_MW)
    return 'Active · 15 W request verified'


def require_rear_ac():
    POWER['require_nvidia_ac']()
    if POWER['read_power_source']() != ('1', '1'):
        raise SourcePending('rear charger disconnected')


def reconcile():
    if not ENABLED.is_file():
        release()
        return 'Off'
    if POWER['read_power_source']() != ('1', '1'):
        release()
        return 'Waiting for sufficient rear AC'
    verify_hardware()
    if RUNTIME_STATUS.read_text().strip() in ('suspended', 'suspending', 'resuming'):
        return 'Waiting for the NVIDIA GPU to wake'
    try:
        with open_policy() as policy:
            require_rear_ac()
            if ENABLED.is_file():
                return apply(policy)
    except SourcePending as error:
        release()
        return f'Waiting for sufficient rear AC · {error}'
    release()
    return 'Off'


def main():
    if sys.argv[1:] not in ([], ['--release']):
        raise SystemExit('Usage: omen-gpu-recovery.py [--release]')
    if os.geteuid() != 0:
        raise SystemExit('GPU recovery requires administrator privileges')
    try:
        if sys.argv[1:]:
            if release():
                print('Dynamic Boost recovery: released the service request', flush=True)
        else:
            print(reconcile(), flush=True)
    except (OSError, RuntimeError, ValueError) as error:
        print(f'NVIDIA policy unavailable · {error}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
