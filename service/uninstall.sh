#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME=omen-ac-power.service
KEEP_MODULES=0

usage() {
    cat <<'EOF'
Usage: sudo ./uninstall.sh [--keep-modules]

Stop and remove the OMEN AC performance service.
The locally built akmod package is removed unless --keep-modules is used.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'Warning: %s\n' "$*" >&2
}

while (($#)); do
    case $1 in
        --keep-modules) KEEP_MODULES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown option: $1" ;;
    esac
    shift
done

[[ $EUID -eq 0 ]] || fail 'run this script with sudo.'
command -v systemctl >/dev/null || fail 'systemctl is required.'

old_guard=/etc/systemd/system/omen-gpu-reconnect.service
if [[ -e $old_guard ]] || systemctl is-active --quiet "${old_guard##*/}"; then
    systemctl stop "${old_guard##*/}"
fi
rm -f -- "$old_guard" /usr/local/libexec/omen-gpu-reconnect.py

if systemctl list-unit-files "$SERVICE_NAME" --no-legend 2>/dev/null | grep -q .; then
    systemctl disable --now "$SERVICE_NAME"
fi
rm -f /etc/systemd/system/$SERVICE_NAME
rm -f /usr/local/libexec/omen-ac-power.py
rm -f /usr/local/libexec/omen-gpu-recovery.py
systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

if ((KEEP_MODULES == 0)); then
    for module in omen_fan omen_spg omen_ctgp; do
        if lsmod | grep -q "^${module} "; then
            modprobe -r "$module" || warn "could not unload $module; it can be unloaded after reboot."
        fi
    done
    packages=()
    for package in akmod-omen-ctgp akmod-omen-fan; do
        if rpm -q "$package" >/dev/null 2>&1; then
            packages+=("$package")
        fi
    done
    if ((${#packages[@]})); then
        command -v dnf >/dev/null || fail 'dnf is required to remove the OMEN module packages.'
        dnf -y remove "${packages[@]}"
    fi
    depmod -a
fi

printf 'OMEN AC performance service removed. Reboot if a kernel module could not be unloaded.\n'
