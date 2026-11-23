Name:           omen-power-control
Version:        %{omen_version}
Release:        1%{?dist}
Summary:        Power and cooling controls for HP OMEN Transcend 14
License:        MIT AND GPL-2.0-only
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  python3
Requires:       dnf
Requires:       kmod
Requires:       polkit
Requires:       python3 >= 3.13
Requires:       python3-pyside6
Requires:       systemd

%description
A Qt desktop app for CPU, fan and GPU controls on the supported HP OMEN
Transcend 14. The package includes the source for the optional system service
and its kernel modules. The app offers service installation on first launch.

%prep
%autosetup

%build

%install
install -Dm0755 app/omen-power-control %{buildroot}%{_bindir}/omen-power-control
install -d %{buildroot}%{_libexecdir}/omen-control/service/modules
install -m0755 app/omen-control-helper %{buildroot}%{_libexecdir}/omen-control/
install -m0644 app/omen-{control,cpu-profiles,fan-curves}.py %{buildroot}%{_libexecdir}/omen-control/
install -m0755 service/{install,uninstall}.sh %{buildroot}%{_libexecdir}/omen-control/service/
install -m0644 service/{omen-ac-power.py,omen-ac-power.service,omen-gpu-recovery.py,cpu-profiles-defaults.json} \
    %{buildroot}%{_libexecdir}/omen-control/service/
install -m0644 service/modules/{Kbuild,*.c,*.spec} %{buildroot}%{_libexecdir}/omen-control/service/modules/
install -Dm0644 app/org.omenpower.Control.desktop %{buildroot}%{_datadir}/applications/org.omenpower.Control.desktop
install -Dm0644 app/org.omenpower.Control.policy %{buildroot}%{_datadir}/polkit-1/actions/org.omenpower.Control.policy

%check
desktop-file-validate app/org.omenpower.Control.desktop
%{_bindir}/python3 -I - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

for path in [*Path('app').glob('*.py'), Path('app/omen-control-helper'), *Path('service').glob('*.py')]:
    compile(path.read_bytes(), str(path), 'exec')
ElementTree.parse('app/org.omenpower.Control.policy')
PY
for script in service/install.sh service/uninstall.sh; do
    bash -n "$script"
done

%files
%license LICENSE LICENSES/GPL-2.0-only.txt
%doc README.md docs/release.md
%{_bindir}/omen-power-control
%{_libexecdir}/omen-control/
%{_datadir}/applications/org.omenpower.Control.desktop
%{_datadir}/polkit-1/actions/org.omenpower.Control.policy
