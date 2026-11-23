%global debug_package %{nil}
%undefine _auto_set_build_flags

Name:           omen-ctgp-kmod
Version:        1.2
Release:        1%{?dist}
Summary:        HP OMEN Transcend 14 GPU boost control
License:        GPL-2.0-only
ExclusiveArch:  x86_64
Source0:        omen_ctgp.c
Source1:        Kbuild
Source2:        omen_spg.c
BuildRequires:  kmodtool

%if 0%{?kernels:1}
%{expand:%(kmodtool --target %{_target_cpu} --repo rpmfusion --kmodname %{name} --for-kernels "%{kernels}")}
%else
%package -n akmod-omen-ctgp
Summary:        Locally rebuildable GPU boost module for HP OMEN 8C58
Requires:       akmods, kmodtool
Provides:       omen-ctgp-kmod-common = %{version}-%{release}

%description -n akmod-omen-ctgp
Kernel module sources used by Fedora akmods to build and sign omen_ctgp and omen_spg.
%files -n akmod-omen-ctgp
%{_usrsrc}/akmods/%{name}-%{version}-%{release}.src.rpm
%{_usrsrc}/akmods/%{name}.latest
%endif

%description
Board-specific interfaces to HP firmware for cTGP and Smart Performance Gain.

%prep
%{?kmodtool_check}
%setup -q -T -c

%build
for kernel in %{?kernel_versions}; do
    module_dir="$PWD/build_${kernel%%___*}"
    mkdir -p "$module_dir"
    cp %{SOURCE0} %{SOURCE1} %{SOURCE2} "$module_dir/"
    make %{?_smp_mflags} -C "${kernel##*___}" M="$module_dir" modules
done

%install
%if 0%{?kernels:1}
for kernel in %{kernel_versions}; do
    version=${kernel%%___*}
    for module in omen_ctgp omen_spg; do
        install -Dm0644 "build_$version/$module.ko" \
            "%{buildroot}%{kmodinstdir_prefix}$version%{kmodinstdir_postfix}$module.ko"
    done
done
%else
dest="%{buildroot}%{_usrsrc}/akmods"
mkdir -p "$dest"
rpmbuild -bs --nodeps --define "_sourcedir %{_sourcedir}" --define "_srcrpmdir $dest" "%{_specdir}/%{name}.spec"
ln -s "%{name}-%{version}-%{release}.src.rpm" "$dest/%{name}.latest"
%endif
