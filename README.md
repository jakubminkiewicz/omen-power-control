# OMEN Power Control

A Qt app for managing CPU power limits, fan curves and GPU boost on the HP OMEN Transcend 14 running Fedora. The app configures a systemd service so the selected settings apply while the app is closed.

This project aims to reach feature parity to how the laptop performs under Windows. There are a number of fixes in the service which address D3 state fixes for the NVIDIA GPU, battery optimisations, 'Smart Performance Gain' implementations, correct performance profile mappings, and a fix to a bug within the EC/NVIDIA stack which limits GPU wattage when re-connecting the power adapter.


## Supported hardware

The firmware controls have been tested on Fedora 45 x86-64 (KDE Plasma) with HP board `8C58`, product SKU `9R293EA#ABU` and BIOS `F.13`. The installer checks all three identifiers because the fan and GPU requests are firmware-specific. Check your laptop before installing the service.

```bash
cat /sys/class/dmi/id/{board_name,product_sku,bios_version}
```

## Install from GitHub Releases

1. Download the `omen-power-control-*.noarch.rpm` and `SHA256SUMS` from the same release. In the download folder, run `sha256sum -c SHA256SUMS`.
2. Install the RPM with `sudo dnf install ./omen-power-control-*.noarch.rpm`. Fedora resolves the Qt and polkit dependencies.
3. Open **OMEN Power Control** from the app menu. When the app offers to install the service, choose **Install Service** and authenticate. The installer reuses working OMEN modules or builds them for your running kernel, then enables and starts the service. **Later** dismisses the prompt. You can start setup from the **Service** tab at any time.

The installer needs a `kernel-devel` package that matches the running kernel when building the modules. If Fedora no longer offers that package, update Fedora and boot its current kernel before retrying. [Fedora akmods package](https://packages.fedoraproject.org/pkgs/akmods/akmods/)

### Secure Boot

Akmods signs locally built modules with a key on your laptop. If the app reports that enrollment is required, run the command shown in its dialog and choose a one-time password.

```bash
sudo mokutil --import /etc/pki/akmods/certs/public_key.der
```

Reboot, select **Enroll MOK** at the firmware prompt and enter that password. Open the app and choose **Install Service** again. The local signing key can be reused for later kernel builds. See the `README.secureboot` included with akmods for details.

## Controls

| Tab | Settings |
|---|---|
| **CPU Controls** | Assign CPU power profiles to Cool, Balanced and Performance. Performance uses the adaptive `powersave` governor by default with TuneD. The checkbox below Performance requests maximum clocks. |
| **Fan Curves** | Assign firmware automatic, maximum or a custom curve to each EC mode. |
| **GPU Controls** | Control cTGP and Smart Performance Gain on rear AC in Performance. The default gain is +15 W. Higher gain settings are experimental. |
| **Fixes** | Apply the TuneD Power Saver mapping for EC Cool, or enable the optional 15 W Dynamic Boost recovery on sufficient rear AC. That recovery is version-checked against NVIDIA 620.73.04. |
| **Service** | Install, update or restart the controller and inspect its activity. |

## Uninstall

With the app package still installed, remove the service and its locally built modules, then remove the app.

```bash
sudo /usr/libexec/omen-control/service/uninstall.sh
sudo dnf remove omen-power-control
```

## Project layout

- `app/` holds the Qt interface, desktop launcher and privileged helper.
- `service/` holds the systemd service, controller, defaults and kernel module sources.
- `packaging/` builds the Fedora RPM. [Build and GitHub release instructions](docs/release.md)

The GUI, userspace service and project documentation are covered by the [MIT license](LICENSE). The kernel modules are [GPL-2.0-only](LICENSES/GPL-2.0-only.txt) as marked in their source.
