%global debug_package %{nil}
%undefine _auto_set_build_flags

Name:           omen-fan-kmod
Version:        1.0
Release:        1%{?dist}
Summary:        HP OMEN Transcend 14 fixed fan speed control
License:        GPL-2.0-only
ExclusiveArch:  x86_64
Source0:        omen_fan.c
BuildRequires:  kmodtool

%if 0%{?kernels:1}
%{expand:%(kmodtool --target %{_target_cpu} --repo rpmfusion --kmodname %{name} --for-kernels "%{kernels}")}
%else
%package -n akmod-omen-fan
Summary:        Locally rebuildable manual fan module for HP OMEN 8C58
Requires:       akmods, kmodtool
Provides:       omen-fan-kmod-common = %{version}-%{release}

%description -n akmod-omen-fan
Kernel module source used by Fedora akmods to build and sign omen_fan.
%files -n akmod-omen-fan
%{_usrsrc}/akmods/%{name}-%{version}-%{release}.src.rpm
%{_usrsrc}/akmods/%{name}.latest
%endif

%description
Board-specific manual fan speed for HP OMEN Transcend 14, with automatic
firmware cooling restored when its userspace request expires.

%prep
%{?kmodtool_check}
%setup -q -T -c

%build
for kernel in %{?kernel_versions}; do
    module_dir="$PWD/build_${kernel%%___*}"
    mkdir -p "$module_dir"
    cp %{SOURCE0} "$module_dir/"
    printf '%s\n' 'obj-m := omen_fan.o' > "$module_dir/Makefile"
    make %{?_smp_mflags} -C "${kernel##*___}" M="$module_dir" modules
done

%install
%if 0%{?kernels:1}
for kernel in %{?kernel_versions}; do
    version=${kernel%%___*}
    install -Dm0644 "build_$version/omen_fan.ko" \
        "%{buildroot}%{kmodinstdir_prefix}$version%{kmodinstdir_postfix}/omen_fan.ko"
done
%else
dest="%{buildroot}%{_usrsrc}/akmods"
mkdir -p "$dest"
rpmbuild -bs --nodeps --define "_sourcedir %{_sourcedir}" --define "_srcrpmdir $dest" "%{_specdir}/%{name}.spec"
ln -s "%{name}-%{version}-%{release}.src.rpm" "$dest/%{name}.latest"
%endif
