"""CPU profiles, cooling and rear-AC GPU boost for the HP OMEN Transcend 14."""

import ctypes
import errno
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event


CAP = Path('/sys/class/powercap')
ZONES = ('intel-rapl:0', 'intel-rapl-mmio:0')
LIMITS = {'pl1': 'long_term', 'pl2': 'short_term', 'pl4': 'peak_power'}
LIMIT_REGISTERS = {f'{zone}/constraint_{index}_power_limit_uw': limit
                   for index, limit in enumerate(LIMITS) for zone in ZONES}
CPU_DEFAULTS = Path(__file__).with_name('cpu-profiles-defaults.json')
CPU_PROFILE_CONFIG = Path('/etc/omen-ac-power/cpu-profiles.json')
CPU_MAX_WATTS = 1000
AC = Path('/sys/class/power_supply/ADP1/online')
REAR = Path('/sys/class/power_supply/ucsi-source-psy-USBC000:002/online')
PROFILE = Path('/sys/firmware/acpi/platform_profile')
STATE = Path('/run/omen-ac-power/original.json')
FAN_OVERRIDE = STATE.with_name('max-fan')
CONTROLS = STATE.with_name('controls.json')
CONTROL_STATUS = STATE.with_name('control-status.json')
CONTROL_VERSION = 8
GPU_RECOVERY_ENABLED = Path('/etc/omen-ac-power/dynamic-boost-recovery')
GPU_RECOVERY_OWNED = STATE.with_name('dynamic-boost-recovery-owned')
GPU_RECOVERY_WORKER = Path(__file__).with_name('omen-gpu-recovery.py')
GAIN_STEPS = (5, 10, 15, 20, 25, 30)
DEFAULT_GAIN_WATTS = 15
SPG_MODULE_VERSION = '2'
DEFAULT_CONTROLS = dict(request='', ctgp=True, gain=True, gain_watts=DEFAULT_GAIN_WATTS, reapply=False)
LEGACY_CONTROL_KEYS = {'cpu', 'fan', 'fan_percent'}
MIN_FAN_PERCENT = 35
FAN_PROFILE_CONFIG = Path('/etc/omen-ac-power/fan-curves.json')
EC_PROFILES = ('cool', 'balanced', 'performance')
DEFAULT_PERFORMANCE = 'default-performance'
FAN_ASSIGNMENTS = ('automatic', DEFAULT_PERFORMANCE, 'maximum')
DEFAULT_CURVE_INTERVAL = 3
MAX_CURVE_INTERVAL = 60
MAX_CURVE_POINTS = 20
MIN_CURVE_TEMP, MAX_CURVE_TEMP = 20, 100
MANUAL_FAN = Path('/sys/module/omen_fan/parameters/fan_percent')
HP_HWMON = Path('/sys/devices/platform/hp-wmi/hwmon')
THERMAL = Path('/sys/class/thermal')
MAX_FAN, AUTO_FAN = 0, 2
FAN_HOT_TEMP, FAN_COOL_TEMP = 82_000, 70_000
FAN_HOT_SECONDS, FAN_COOL_SECONDS = 8, 25
FAN_SAMPLE_GAP = 2.5


def with_cpu_defaults(config, defaults):
    assignments = {mode: name if name is not None else defaults['assignments'][mode]
                   for mode, name in config['assignments'].items()}
    return dict(config, assignments=assignments)


def validate_cpu_profiles(config, *, defaults=None):
    if not isinstance(config, dict) or config.keys() != {'profiles', 'assignments'}:
        raise ValueError('Invalid CPU profile settings')
    profiles = config['profiles']
    if not isinstance(profiles, dict):
        raise ValueError('CPU profiles must have names')
    for name, watts in profiles.items():
        if not isinstance(name, str) or not name or name.strip() != name or len(name) > 48 or not name.isprintable():
            raise ValueError('A CPU profile name must be between 1 and 48 printable characters')
        if (not isinstance(watts, dict) or watts.keys() != LIMITS.keys()
                or any(type(value) is not int or not 1 <= value <= CPU_MAX_WATTS for value in watts.values())):
            raise ValueError(f'{name} needs PL1, PL2 and PL4 in whole watts between 1 and {CPU_MAX_WATTS}')
    assignments = config['assignments']
    if (not isinstance(assignments, dict) or assignments.keys() != set(EC_PROFILES)
            or any(name is not None and (not isinstance(name, str) or name not in profiles)
                   for name in assignments.values())):
        raise ValueError('Each EC mode needs a valid CPU profile assignment')
    if defaults is not None:
        for name, watts in defaults['profiles'].items():
            if profiles.get(name) != watts:
                raise ValueError(f'{name} is a factory CPU profile. Create a new profile to change its limits.')
        return with_cpu_defaults(config, defaults)
    return config


def default_cpu_profiles():
    defaults = validate_cpu_profiles(json.loads(CPU_DEFAULTS.read_text()))
    if any(name is None for name in defaults['assignments'].values()):
        raise ValueError('Each EC mode needs a named factory CPU profile')
    return defaults


def read_cpu_profiles(defaults=None):
    if defaults is None:
        defaults = default_cpu_profiles()
    try:
        saved = validate_cpu_profiles(json.loads(CPU_PROFILE_CONFIG.read_text()))
    except FileNotFoundError:
        return defaults

    profiles = saved['profiles'] | defaults['profiles']
    assignments = saved['assignments'].copy()
    for name, factory in defaults['profiles'].items():
        previous = saved['profiles'].get(name)
        if previous is None or previous == factory:
            continue
        if name == 'Cool' and previous == {'pl1': 20, 'pl2': 50, 'pl4': 100}:
            continue  # Upgrade the original Cool preset to the new factory limit.
        # Older versions allowed editing a factory profile. Keep those edits as a custom profile.
        custom = f'{name} (custom)'
        suffix = 2
        while custom in profiles:
            custom = f'{name} (custom {suffix})'
            suffix += 1
        profiles[custom] = previous
        for mode, selected in assignments.items():
            if selected == name:
                assignments[mode] = custom
    return with_cpu_defaults({'profiles': profiles, 'assignments': assignments}, defaults)


def default_fan_profiles():
    return {'curves': {}, 'interval': DEFAULT_CURVE_INTERVAL,
            'assignments': dict.fromkeys(EC_PROFILES, 'automatic')}


def validate_fan_profiles(config):
    if not isinstance(config, dict) or config.keys() != {'curves', 'interval', 'assignments'}:
        raise ValueError('Invalid fan profile settings')
    if type(config['interval']) is not int or not 1 <= config['interval'] <= MAX_CURVE_INTERVAL:
        raise ValueError(f'Curve interval must be between 1 and {MAX_CURVE_INTERVAL} seconds')
    curves = config['curves']
    if not isinstance(curves, dict):
        raise ValueError('Fan curves must have named profiles')
    for name, points in curves.items():
        if not isinstance(name, str) or not name or name.strip() != name or len(name) > 48 or not name.isprintable():
            raise ValueError('A curve name must be between 1 and 48 printable characters')
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_CURVE_POINTS:
            raise ValueError(f'{name} needs 2 to {MAX_CURVE_POINTS} points')
        last = MIN_CURVE_TEMP - 1
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(type(value) is not int for value in point):
                raise ValueError(f'{name} has an invalid point')
            temp, speed = point
            if not last < temp <= MAX_CURVE_TEMP or not MIN_FAN_PERCENT <= speed <= 100:
                raise ValueError(f'{name} needs distinct, increasing temperatures from {MIN_CURVE_TEMP} to '
                                 f'{MAX_CURVE_TEMP}°C and fan speeds from {MIN_FAN_PERCENT}% to 100%')
            last = temp
    assignments = config['assignments']
    choices = {*FAN_ASSIGNMENTS, *(f'curve:{name}' for name in curves)}
    if not isinstance(assignments, dict) or assignments.keys() != set(EC_PROFILES):
        raise ValueError('Every EC profile needs a valid fan assignment')
    assignments = assignments.copy()
    for profile, selection in assignments.items():
        if selection == 'managed':
            assignments[profile] = DEFAULT_PERFORMANCE if profile == 'performance' else 'automatic'
        elif not isinstance(selection, str) or selection not in choices:
            raise ValueError('Every EC profile needs a valid fan assignment')
    return dict(config, assignments=assignments)


def read_fan_profiles():
    try:
        return validate_fan_profiles(json.loads(FAN_PROFILE_CONFIG.read_text()))
    except FileNotFoundError:
        return default_fan_profiles()


def curve_speed(points, millidegrees):
    temperature = millidegrees / 1000
    if temperature <= points[0][0]:
        return points[0][1]
    for (left, speed), (right, next_speed) in zip(points, points[1:]):
        if temperature <= right:
            return round(speed + (next_speed - speed) * (temperature - left) / (right - left))
    return points[-1][1]


class GpuPowerSourcePending(Exception):
    """The HP GPU boost must wait for the power-source transition."""


def nvidia_power_source():
    # Release NVML after a sample so an idle GPU can enter runtime D3.
    try:
        nv = ctypes.CDLL('libnvidia-ml.so.1')
        status = nv.nvmlInit_v2()
        if status == 0:
            try:
                handle = ctypes.c_void_p()
                status = nv.nvmlDeviceGetHandleByPciBusId_v2(b'0000:01:00.0', ctypes.byref(handle))
                if status == 0:
                    source = ctypes.c_uint()
                    status = nv.nvmlDeviceGetPowerSource(handle, ctypes.byref(source))
                    if status == 0:
                        return source.value
            finally:
                nv.nvmlShutdown()
    except (OSError, AttributeError) as exc:
        raise GpuPowerSourcePending(f'NVML unavailable: {exc}') from exc
    raise GpuPowerSourcePending(f'NVML power source unavailable (status {status})')


def require_nvidia_ac():
    # The USB-C port can be online several seconds before NVIDIA leaves battery.
    source = nvidia_power_source()
    if source != 0:  # NVML_POWER_SOURCE_AC
        label = {1: 'battery', 2: 'undersized adapter'}.get(source, str(source))
        raise GpuPowerSourcePending(f'NVIDIA reports {label}')


def require_gpu_ac():
    if not eligible():
        raise GpuPowerSourcePending('rear AC Performance is no longer selected')
    require_nvidia_ac()


class DynamicBoostRecovery:
    def __init__(self):
        self.status = 'Off'
        self.process = None
        self.process_condition = None
        self.condition = None
        self.next_check = 0
        self.deadline = 0

    def report(self, status):
        if status != self.status:
            self.status = status
            print(f'Dynamic Boost recovery: {status}', flush=True)

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def poll(self, enabled, rear_ac, *, resumed=False):
        now = time.monotonic()
        condition = enabled, rear_ac
        changed = condition != self.condition or resumed
        self.condition = condition
        if changed:
            self.next_check = 0

        # NVIDIA I/O runs in a child so a slow GPU cannot delay fan or CPU control.
        if self.process is not None:
            result = self.process.poll()
            if result is None:
                if self.deadline and now >= self.deadline:
                    self.cancel()
                    self.deadline = 0
                    self.report('NVIDIA policy query timed out. Waiting to retry.')
                    self.next_check = now + 10
                return
            output, _ = self.process.communicate()
            self.process = None
            if condition == self.process_condition and self.deadline:
                lines = output.strip().splitlines()
                self.report(lines[-1] if lines else 'NVIDIA policy check failed')
                if result:
                    self.next_check = now + 10

        if not (enabled and rear_ac) and not GPU_RECOVERY_OWNED.exists():
            self.report('Waiting for sufficient rear AC' if enabled else 'Off')
        elif now >= self.next_check:
            self.next_check = now + 3
            try:
                self.process = subprocess.Popen(
                    [sys.executable, '-I', str(GPU_RECOVERY_WORKER)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
            except OSError as error:
                self.report(f'NVIDIA policy check failed ({error})')
                self.next_check = now + 10
            else:
                self.process_condition = condition
                self.deadline = now + 3


class GpuBoost:
    def __init__(self, module, parameter, label):
        self.module = module
        self.control = Path('/sys/module') / module / 'parameters' / parameter
        self.label = label
        self.request = None
        self.next_check = 0
        self.error = None

    def write_request(self, value):
        if value:
            require_gpu_ac()
        self.control.write_text(f'{value}\n')

    def poll(self, value, *, refresh=False):
        now = time.monotonic()
        changed = self.request != value
        if not changed and not refresh and now < self.next_check:
            return
        self.request = value
        try:
            if value and not self.control.exists():
                subprocess.run(['/usr/sbin/modprobe', self.module], check=True, timeout=5)
            if value and not eligible():
                self.request = None
                return
            if value or self.control.exists():
                self.write_request(value)
        except GpuPowerSourcePending as exc:
            if str(exc) != self.error:
                print(f'{self.label}: waiting for confirmed NVIDIA AC ({exc})', flush=True)
            self.error = str(exc)
            self.next_check = now + 1
            return
        except (OSError, subprocess.SubprocessError) as exc:
            if str(exc) != self.error:
                print(f'{self.label}: {exc}. Retrying in 15 seconds.', flush=True)
            self.error = str(exc)
            self.next_check = now + 15
            return
        if changed or self.error:
            print(f'{self.label}: ' + ('AC Performance enabled (NVIDIA AC confirmed)' if value else 'released'), flush=True)
        self.error = None
        self.next_check = now + 60 if value else float('inf')


class SmartPerformanceGain(GpuBoost):
    def write_request(self, watts):
        if not watts:
            return super().write_request(0)
        version = self.control.parent.parent / 'version'
        if not version.is_file() or version.read_text().strip() != SPG_MODULE_VERSION:
            raise OSError(errno.EOPNOTSUPP, 'Load the updated Smart Performance Gain driver to use adjustable gain')
        try:
            super().write_request(watts)
        except BlockingIOError:
            pass  # F.13 can reset the EC profile while Linux still reports Performance.
        else:
            if self.control.read_text().strip() == str(watts):
                return

        require_gpu_ac()
        PROFILE.write_text('performance\n')
        super().write_request(watts)
        if self.control.read_text().strip() != str(watts):
            # The EC can retain the request but drop the effective budget. The
            # module's release only clears the request it owns.
            require_gpu_ac()
            super().write_request(0)
            super().write_request(watts)
            if self.control.read_text().strip() != str(watts):
                raise OSError(errno.EIO, 'HP did not apply the requested GPU gain')
        print(f'{self.label} restored after reapplying HP Performance', flush=True)


class FanPolicy:
    def __init__(self):
        self.applied_mode = None
        self.control = None
        self.sensor = None
        self.pending = None
        self.since = 0
        self.last_sample = None
        self.error = None
        self.applied_speed = None
        self.renew_at = 0
        self.retry_at = 0
        self.requested_speed = None
        self.curve_selection = None
        self.curve_deadline = 0

    def fan_mode_path(self):
        if self.control is None:
            self.control = next(HP_HWMON.glob('hwmon*/pwm1_enable'), None)
            if self.control is None:
                raise FileNotFoundError('HP fan control unavailable')
        return self.control

    def temperature(self):
        if self.sensor is None:
            self.sensor = next((p.with_name('temp') for p in THERMAL.glob('thermal_zone*/type')
                                if p.read_text().strip() == 'x86_pkg_temp'), None)
            if self.sensor is None:
                raise FileNotFoundError('CPU package temperature unavailable')
        return int(self.sensor.read_text())

    def set_mode(self, mode):
        path = self.fan_mode_path()
        path.write_text(f'{mode}\n')
        if int(path.read_text()) != mode:
            raise OSError(f'HP fan mode {mode} failed readback')
        if mode == AUTO_FAN:
            FAN_OVERRIDE.unlink(missing_ok=True)

    def update_default_performance(self, renew):
        temp = self.temperature()
        actual = int(self.fan_mode_path().read_text())
        if actual not in (AUTO_FAN, MAX_FAN):
            raise ValueError(f'Unexpected HP fan mode {actual}')
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        if self.last_sample is None or now - self.last_sample > FAN_SAMPLE_GAP:
            self.pending = None  # A polling gap cannot count as continuous exposure.
        self.last_sample = now

        owned = FAN_OVERRIDE.exists()
        maxed = owned or actual == MAX_FAN
        target = None
        if not maxed and temp > FAN_HOT_TEMP:
            target = MAX_FAN
        elif maxed and temp < FAN_COOL_TEMP:
            target = AUTO_FAN
        if target != self.pending:
            self.pending, self.since = target, now
        if target is not None and now - self.since >= (FAN_HOT_SECONDS if target == MAX_FAN else FAN_COOL_SECONDS):
            if target == MAX_FAN:
                FAN_OVERRIDE.touch()  # Record ownership before writing, including across restarts.
            self.set_mode(target)
            self.pending = None
            reason = 'Max Fan after 8 s over 82°C' if target == MAX_FAN else 'automatic after 25 s below 70°C'
            print(f'Fans: {reason} (CPU package {temp / 1000:g}°C)', flush=True)
        elif owned and (actual != MAX_FAN or renew):
            self.set_mode(MAX_FAN)

    def apply_curve(self, curve, interval, renew):
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        self.pending = self.last_sample = None
        selection = (curve, interval)
        if selection != self.curve_selection or now >= self.curve_deadline:
            try:
                self.requested_speed = curve_speed(curve, self.temperature())
            except (OSError, ValueError):
                self.release_curve()
                if FAN_OVERRIDE.exists():
                    self.set_mode(AUTO_FAN)
                raise
            self.curve_selection = selection
            self.curve_deadline = now + interval
        if self.requested_speed != self.applied_speed or renew or now >= self.renew_at:
            self.renew_at = now + 3
            if not MANUAL_FAN.exists():
                subprocess.run(['/usr/sbin/modprobe', 'omen_fan'], check=True, timeout=5)
            MANUAL_FAN.write_text(f'{self.requested_speed}\n')
            if int(MANUAL_FAN.read_text()) != self.requested_speed:
                raise OSError('Fan curve request failed readback')
            self.applied_speed = self.requested_speed
            FAN_OVERRIDE.unlink(missing_ok=True)

    def release_curve(self):
        if MANUAL_FAN.exists() and int(MANUAL_FAN.read_text()):
            MANUAL_FAN.write_text('0\n')
        self.applied_speed = self.requested_speed = self.curve_selection = None

    def poll(self, selection=None, *, renew=False, curve=None, interval=DEFAULT_CURVE_INTERVAL):
        if curve is not None and not renew and time.monotonic() < self.retry_at:
            return
        try:
            if curve is not None:
                self.apply_curve(curve, interval, renew)
                self.retry_at = 0
            else:
                self.release_curve()
                if selection == DEFAULT_PERFORMANCE:
                    if self.applied_mode == 'maximum':
                        self.set_mode(AUTO_FAN)
                    self.update_default_performance(renew)
                elif selection is not None:
                    self.pending = self.last_sample = None
                    mode = MAX_FAN if selection == 'maximum' else AUTO_FAN
                    if mode == MAX_FAN and not FAN_OVERRIDE.exists():
                        FAN_OVERRIDE.touch()
                    actual = int(self.fan_mode_path().read_text())
                    if actual != mode or renew or (mode == AUTO_FAN and FAN_OVERRIDE.exists()):
                        self.set_mode(mode)
                elif FAN_OVERRIDE.exists():
                    self.set_mode(AUTO_FAN)
                    print('Fans: returned to automatic on service stop', flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Rediscover sysfs devices on retry. Fan I/O must not stop power management.
            self.control = self.sensor = self.pending = None
            if curve is not None:
                self.applied_speed = None
                self.retry_at = time.monotonic() + 5
            if self.error != str(exc):
                self.error = str(exc)
                print(f'Fan control: {exc}', flush=True)
        else:
            self.applied_mode = selection
            if self.error:
                print('Fan control available again', flush=True)
            self.error = None


def cpu_register_targets(watts):
    return {key: watts[limit] * 1_000_000 for key, limit in LIMIT_REGISTERS.items()}


def at_cpu_target(watts):
    try:
        return all(int((CAP / key).read_text()) == value for key, value in cpu_register_targets(watts).items())
    except (OSError, ValueError):
        return False


class CpuPackagePower:
    def __init__(self):
        self.previous = None

    def sample(self, *, reset=False):
        for zone in ZONES:
            path = CAP / zone
            try:
                if (path / 'name').read_text().strip() != 'package-0':
                    continue
                energy = int((path / 'energy_uj').read_text())
                maximum = int((path / 'max_energy_range_uj').read_text())
            except (OSError, ValueError):
                continue
            if maximum > 0 and 0 <= energy <= maximum:
                break
        else:
            self.previous = None
            return None

        now = time.monotonic()
        previous, self.previous = self.previous, (zone, maximum, energy, now)
        if reset or previous is None or previous[:2] != (zone, maximum):
            return None
        delta = energy - previous[2]
        if delta < 0:
            delta += maximum
        elapsed = now - previous[3]
        if elapsed <= 0:
            return None
        watts = delta / 1_000_000 / elapsed
        return round(watts, 1) if watts <= CPU_MAX_WATTS else None


def saved_cpu_limits():
    try:
        saved = json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(saved, dict) or saved.keys() - LIMIT_REGISTERS.keys():
        raise ValueError('Invalid saved CPU power-limit state')
    if any(type(value) is int for value in saved.values()):
        defaults = default_cpu_profiles()
        legacy = cpu_register_targets(defaults['profiles'][defaults['assignments']['performance']])
        saved = {key: [value, legacy[key]] if type(value) is int else value for key, value in saved.items()}
    if any(not isinstance(value, list) or len(value) != 2 or any(type(watts) is not int for watts in value)
           for value in saved.values()):
        raise ValueError('Invalid saved CPU power-limit readbacks')
    return saved


class CpuPowerPolicy:
    def __init__(self):
        self.saved = saved_cpu_limits()
        self.plan = None
        self.pending = True
        self.retry_at = 0
        self.error = None

    def persist(self):
        if self.saved:
            temporary = STATE.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.saved))
            temporary.replace(STATE)
        else:
            STATE.unlink(missing_ok=True)

    def release(self):
        error = None
        for key in reversed(LIMIT_REGISTERS):
            if key not in self.saved:
                continue
            original, written = self.saved[key]
            path = CAP / key
            try:
                if int(path.read_text()) == written and written != original:
                    path.write_text(f'{original}\n')
                    if int(path.read_text()) != original:
                        raise RuntimeError(f'Could not restore {key}')
            except (OSError, RuntimeError, ValueError) as exc:
                error = error or exc
            else:
                del self.saved[key]
        self.persist()
        if error is not None:
            raise error

    def apply(self, watts):
        for zone in ZONES:
            path = CAP / zone
            if (path / 'name').read_text().strip() != 'package-0' or (path / 'enabled').read_text().strip() != '1':
                raise RuntimeError(f'{zone} is not an enabled package-0 power zone')
            for index, name in enumerate(LIMITS.values()):
                if (path / f'constraint_{index}_name').read_text().strip() != name:
                    raise RuntimeError(f'Unexpected constraint {index} on {zone}')
        target = cpu_register_targets(watts)
        current = {key: int((CAP / key).read_text()) for key in target}
        lower = [key for key in target if target[key] < current[key]]
        raise_limits = [key for key in reversed(target) if target[key] > current[key]]
        for key in lower + raise_limits:
            path = CAP / key
            original = int(path.read_text())
            if original == target[key]:
                continue
            # Record ownership before writing so a restart can roll back a partial change.
            self.saved[key] = [original, target[key]]
            self.persist()
            path.write_text(f'{target[key]}\n')
        if not at_cpu_target(watts):
            raise RuntimeError('CPU power-limit readbacks differ from the selected target')

    def poll(self, scope, watts, *, refresh=False):
        plan = scope, watts
        now = time.monotonic()
        if plan != self.plan or refresh:
            self.plan = plan
            self.pending = True
            self.retry_at = 0
        if not self.pending or now < self.retry_at:
            return
        try:
            self.release()
            if watts is not None:
                self.apply(watts)
        except (OSError, RuntimeError, ValueError) as exc:
            message = str(exc)
            try:
                self.release()
            except (OSError, RuntimeError, ValueError) as rollback:
                message += f'. Rollback pending ({rollback})'
            if message != self.error:
                print(f'CPU limits: {message}', flush=True)
            self.error = message
            self.pending = isinstance(exc, FileNotFoundError) or bool(self.saved)
            self.retry_at = now + 5
        else:
            self.error = None
            self.pending = False
            if watts is not None:
                values = '/'.join(str(watts[limit]) for limit in LIMITS)
                print(f'CPU profile {scope[1]}: MSR and MMIO verified at {values} W', flush=True)


def read_power_source():
    source = []
    for path in (AC, REAR):
        try:
            source.append(path.read_text().strip())
        except OSError:
            source.append(None)
    return tuple(source)


def read_ec_profile():
    try:
        return PROFILE.read_text().strip()
    except OSError:
        return None


def eligible():
    return read_power_source() == ('1', '1') and read_ec_profile() == 'performance'


def read_controls():
    try:
        values = json.loads(CONTROLS.read_text())
    except FileNotFoundError:
        return DEFAULT_CONTROLS.copy()
    if not isinstance(values, dict) or values.keys() - DEFAULT_CONTROLS.keys() - LEGACY_CONTROL_KEYS:
        raise ValueError('Invalid OMEN GPU settings')
    values = {key: values.get(key, default) for key, default in DEFAULT_CONTROLS.items()}
    if not isinstance(values['request'], str) or any(type(values[key]) is not bool for key in ('ctgp', 'gain', 'reapply')):
        raise ValueError('Invalid OMEN GPU settings')
    if type(values['gain_watts']) is not int or values['gain_watts'] not in GAIN_STEPS:
        raise ValueError('Smart Performance Gain needs 5 to 30 W in 5 W steps')
    return values


def write_control_json(path, values):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def run():
    if Path('/sys/class/dmi/id/board_name').read_text().strip() != '8C58':
        raise RuntimeError('Unsupported board')
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    cpu = CpuPowerPolicy()
    power_meter = CpuPackagePower()
    fans = FanPolicy()
    gpu = GpuBoost('omen_ctgp', 'ctgp', 'GPU cTGP')
    gain = SmartPerformanceGain('omen_spg', 'gain_watts', 'Smart Performance Gain')
    recovery = DynamicBoostRecovery()
    previous_sleep = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
    refresh = 0
    previous_controls = None
    controls = DEFAULT_CONTROLS
    controls_error = None
    cpu_profiles = {'profiles': {}, 'assignments': {}}
    cpu_config_error = None
    fan_profiles = default_fan_profiles()
    fan_config_error = None
    previous_fan_selection = previous_status = None
    try:
        while not stop.is_set():
            try:
                controls = read_controls()
            except (OSError, ValueError) as exc:
                if controls_error != str(exc):
                    print(f'GPU controls: {exc}', flush=True)
                controls_error = str(exc)
            else:
                controls_error = None
            try:
                cpu_profiles = read_cpu_profiles()
            except (OSError, ValueError) as exc:
                if cpu_config_error != str(exc):
                    print(f'CPU profiles: {exc}', flush=True)
                cpu_config_error = str(exc)
            else:
                cpu_config_error = None
            try:
                fan_profiles = read_fan_profiles()
            except (OSError, ValueError) as exc:
                if fan_config_error != str(exc):
                    print(f'Fan profiles: {exc}', flush=True)
                fan_config_error = str(exc)
            else:
                fan_config_error = None
            changed = controls != previous_controls
            reapply = changed and controls['reapply']
            now = time.monotonic()
            sleep = time.clock_gettime(time.CLOCK_BOOTTIME) - now
            resumed = sleep - previous_sleep > 0.5
            previous_sleep = sleep
            if resumed:
                fans.last_sample = None
                fans.curve_deadline = 0
            profile = read_ec_profile()
            power_source = read_power_source()
            on_ac_performance = profile == 'performance' and power_source == ('1', '1')
            cpu_name = cpu_profiles['assignments'].get(profile)
            cpu_target = cpu_profiles['profiles'].get(cpu_name)
            cpu_scope = (profile, cpu_name, power_source) if cpu_target is not None else None
            renew_fans = on_ac_performance and bool(refresh) and now >= refresh
            refresh_profile = renew_fans and cpu_target is not None and at_cpu_target(cpu_target)
            if on_ac_performance and (reapply or refresh_profile) and eligible():
                try:
                    PROFILE.write_text('performance\n')
                except OSError as exc:
                    print(f'Could not refresh rear-AC Performance ({exc})', flush=True)
            if not on_ac_performance:
                refresh = 0
            elif not refresh or reapply or resumed or renew_fans:
                refresh = now + 60
            cpu.poll(cpu_scope, cpu_target, refresh=resumed or reapply)
            fan_selection = fan_profiles['assignments'].get(profile, 'automatic')
            curve = (fan_profiles['curves'][fan_selection[6:]] if fan_selection.startswith('curve:')
                     else None)
            selection = profile, fan_selection
            fans.poll(fan_selection, renew=renew_fans or resumed or reapply or selection != previous_fan_selection,
                      curve=curve, interval=fan_profiles['interval'])
            previous_fan_selection = selection
            refresh_gpu = resumed or renew_fans or reapply
            gpu.poll(int(on_ac_performance and controls['ctgp']), refresh=refresh_gpu)
            bonus = controls['gain_watts'] if on_ac_performance and controls['gain'] else 0
            gain.poll(bonus, refresh=refresh_gpu)
            recovery.poll(GPU_RECOVERY_ENABLED.is_file(), power_source == ('1', '1'), resumed=resumed)
            if changed and controls['request']:
                level = f"+{controls['gain_watts']} W" if controls['gain'] else 'off'
                print(f"GPU controls: cTGP {controls['ctgp']}, Smart Performance Gain {level}", flush=True)
            previous_controls = controls
            status = {
                'version': CONTROL_VERSION,
                'pid': os.getpid(),
                'request': controls['request'],
                'cpu_profile': cpu_name,
                'cpu_target': cpu_target,
                'cpu_pending': cpu.pending,
                'cpu_error': cpu.error,
                'cpu_power_watts': power_meter.sample(reset=resumed),
                'fan_selection': fan_selection,
                'fan_requested': fans.requested_speed,
                'gpu_recovery': recovery.status,
                'errors': [error for error in (cpu.error, cpu_config_error, fans.error, gpu.error, gain.error,
                                                fan_config_error) if error],
            }
            if status != previous_status:
                write_control_json(CONTROL_STATUS, status)
                previous_status = status
            stop.wait(1)
    finally:
        recovery.cancel()
        gain.poll(0, refresh=True)
        gpu.poll(0, refresh=True)
        fans.poll()
        cpu.release()


if __name__ == '__main__':
    run()
