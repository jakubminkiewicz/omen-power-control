#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SERVICE_NAME=omen-ac-power.service
SERVICE_PATH=/etc/systemd/system/$SERVICE_NAME
CONTROLLER_PATH=/usr/local/libexec/omen-ac-power.py
WITH_MODULES=1
REUSE_MODULES=0
START_SERVICE=1
INSTALL_DEPS=1
BUILD_ROOT=

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [options]

Install the HP OMEN AC performance controller and its kernel modules.

Options:
  --no-modules   Install the controller without building modules.
  --reuse-modules  Reuse any OMEN modules already built for this kernel.
  --no-start     Install and enable the service, but do not start it now.
  --skip-deps    Do not install Fedora packages with dnf.
  -h, --help     Show this help.

The installer targets Fedora and HP OMEN Transcend 14 board 8C58.
It changes system packages, /usr/local, kernel modules, and systemd.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'Warning: %s\n' "$*" >&2
}

read_dmi() {
    local name=$1
    cat "/sys/class/dmi/id/$name" 2>/dev/null || true
}

while (($#)); do
    case $1 in
        --no-modules) WITH_MODULES=0 ;;
        --reuse-modules) REUSE_MODULES=1 ;;
        --no-start) START_SERVICE=0 ;;
        --skip-deps) INSTALL_DEPS=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown option: $1" ;;
    esac
    shift
done

[[ $EUID -eq 0 ]] || fail 'run this installer with sudo.'
[[ -f $SOURCE_DIR/omen-ac-power.py ]] || fail "missing source: $SOURCE_DIR/omen-ac-power.py"
[[ -f $SOURCE_DIR/omen-ac-power.service ]] || fail "missing source: $SOURCE_DIR/omen-ac-power.service"
[[ -f $SOURCE_DIR/omen-gpu-recovery.py ]] || fail "missing source: $SOURCE_DIR/omen-gpu-recovery.py"
[[ -f $SOURCE_DIR/cpu-profiles-defaults.json ]] || fail "missing source: $SOURCE_DIR/cpu-profiles-defaults.json"
command -v systemctl >/dev/null || fail 'systemctl is required.'

BOARD=$(read_dmi board_name)
VENDOR=$(read_dmi board_vendor)
SKU=$(read_dmi product_sku)
BIOS=$(read_dmi bios_version)
if [[ $VENDOR != HP || $BOARD != 8C58 || $SKU != '9R293EA#ABU' || $BIOS != F.13 ]]; then
    fail "supported hardware is HP board 8C58, SKU 9R293EA#ABU, BIOS F.13. Found ${VENDOR:-unknown}, ${BOARD:-unknown}, ${SKU:-unknown}, ${BIOS:-unknown}."
fi

prepare_module_build() {
    local kernel
    kernel=$(uname -r)
    local -a packages=(akmods kmodtool mokutil rpm-build gcc make elfutils-libelf-devel)
    if ((INSTALL_DEPS)) && ! rpm -q "${packages[@]}" "kernel-devel-$kernel" >/dev/null 2>&1; then
        command -v dnf >/dev/null || fail 'The module installer needs Fedora DNF.'
        printf 'Installing build tools and development files for kernel %s.\n' "$kernel"
        dnf install -y "${packages[@]}" "kernel-devel-$kernel" ||
            fail "Could not install the build prerequisites. Check that the Fedora repositories offer kernel-devel for $kernel."
    fi
    local command_name
    for command_name in dnf rpmbuild akmods modinfo modprobe mokutil; do
        command -v "$command_name" >/dev/null || fail "$command_name is required; install module build dependencies."
    done
    [[ -r /lib/modules/$kernel/build/Makefile ]] || fail "Kernel development files for $kernel are missing."
}

check_secure_boot() {
    [[ -d /sys/firmware/efi ]] || return 0
    local state certificate=/etc/pki/akmods/certs/public_key.der
    state=$(LC_ALL=C mokutil --sb-state) || fail 'Could not read the Secure Boot state.'
    [[ $state == *'SecureBoot enabled'* ]] || return 0
    if [[ ! -s $certificate ]]; then
        kmodgenca -a || fail 'Could not create the local akmods key. Check the akmods Secure Boot setup.'
    fi
    [[ -s $certificate ]] || fail 'The akmods public certificate is missing after key generation.'
    if ! LC_ALL=C mokutil --test-key "$certificate"; then
        printf '\nEnroll the local akmods signing key, then retry the installation.\n'
        printf 'sudo mokutil --import %s\n' "$certificate"
        exit 75
    fi
}

build_module_package() {
    local name=$1
    shift
    local spec="$SOURCE_DIR/modules/$name-kmod.spec"
    local work="$BUILD_ROOT/$name"
    local package
    local -a module_rpms
    [[ -f $spec ]] || fail "missing module spec: $spec"
    mkdir -p "$work"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    cp "$@" "$work/SOURCES/"
    cp "$spec" "$work/SPECS/"
    rpmbuild -ba \
        --define "_topdir $work" \
        --define "_sourcedir $work/SOURCES" \
        --define "_specdir $work/SPECS" \
        "$work/SPECS/$name-kmod.spec"

    mapfile -t module_rpms < <(find "$work/RPMS" -type f -name "akmod-$name-*.rpm" -print)
    ((${#module_rpms[@]} == 1)) || fail "expected one akmod-$name RPM, found ${#module_rpms[@]}."
    rpmkeys --checksig --nosignature "${module_rpms[0]}"
    package=$(rpm -qp --queryformat '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "${module_rpms[0]}")
    if rpm -q "$package" >/dev/null 2>&1; then
        dnf -y --nogpgcheck --disablerepo='*' reinstall "${module_rpms[0]}"
    else
        dnf -y --nogpgcheck --disablerepo='*' install "${module_rpms[0]}"
    fi
    akmods --force --kernels "$(uname -r)" --akmod "$name"
    depmod -a
}

spg_driver_current() {
    [[ $(modinfo -F version omen_spg 2>/dev/null) == 2 ]]
}

modules_available() {
    modinfo -n omen_ctgp >/dev/null 2>&1 && modinfo -n omen_fan >/dev/null 2>&1 && spg_driver_current
}

load_modules() {
    local module
    for module in omen_ctgp omen_spg omen_fan; do
        modprobe "$module" || return 1
    done
}

if ((WITH_MODULES && REUSE_MODULES)) && modules_available && load_modules; then
    printf 'Reusing the installed OMEN kernel modules.\n'
elif ((WITH_MODULES)); then
    prepare_module_build
    check_secure_boot
    BUILD_ROOT=$(mktemp -d /tmp/omen-rpmbuild.XXXXXX)
    trap 'if [[ -n ${BUILD_ROOT:-} ]]; then rm -rf -- "$BUILD_ROOT"; fi' EXIT
    build_module_package omen-ctgp "$SOURCE_DIR/modules/"{Kbuild,omen_ctgp.c,omen_spg.c}
    build_module_package omen-fan "$SOURCE_DIR/modules/omen_fan.c"
    modules_available || fail 'The built OMEN modules could not be found for the running kernel.'
    load_modules || fail 'The kernel rejected an OMEN module. Check the kernel journal and Secure Boot enrollment.'
fi

# Retire the old guard while its stop hook can still resume nvidia-powerd.
old_guard=/etc/systemd/system/omen-gpu-reconnect.service
if [[ -e $old_guard ]] || systemctl is-active --quiet "${old_guard##*/}"; then
    systemctl stop "${old_guard##*/}"
fi
rm -f -- "$old_guard" "${CONTROLLER_PATH%/*}/omen-gpu-reconnect.py"

install -Dm0755 "$SOURCE_DIR/omen-ac-power.py" "$CONTROLLER_PATH"
install -Dm0644 "$SOURCE_DIR/omen-gpu-recovery.py" "${CONTROLLER_PATH%/*}/omen-gpu-recovery.py"
install -Dm0644 "$SOURCE_DIR/cpu-profiles-defaults.json" "${CONTROLLER_PATH%/*}/cpu-profiles-defaults.json"
install -Dm0644 "$SOURCE_DIR/omen-ac-power.service" "$SERVICE_PATH"

if [[ -d /sys/module/omen_spg ]] && spg_driver_current &&
    [[ $(cat /sys/module/omen_spg/version 2>/dev/null || true) != 2 ]]; then
    warn 'The old Smart Performance Gain driver is in use. The updated driver will load after reboot.'
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
if ((START_SERVICE)); then
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager --full status "$SERVICE_NAME" || true
    printf '\nInstalled and started %s.\n' "$SERVICE_NAME"
else
    printf '\nInstalled and enabled %s; it was not started.\n' "$SERVICE_NAME"
fi

printf 'Check logs with: journalctl -u %s -f\n' "$SERVICE_NAME"
