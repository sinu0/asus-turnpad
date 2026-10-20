# Turnpad as an RPM package (Fedora). Build it from a clone with
#   bash packaging/rpm/build.sh
# which makes the source tarball with git archive and sets turnpad_version
# (0.r<commits>.<hash>, like the Arch package's pkgver).

%{!?turnpad_version: %global turnpad_version 0}
%global extension_uuid asus-turnpad@sinu0.github.io
%global app_id io.github.sinu0.AsusTurnpad

Name:           asus-turnpad
Version:        %{turnpad_version}
Release:        1%{?dist}
Summary:        Dial for ASUS DialPads, any touchpad, a mouse or a 3Dconnexion SpaceMouse
License:        GPL-2.0-only
URL:            https://github.com/sinu0/asus-turnpad
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils

Requires:       python3
Requires:       python3-libevdev
Requires:       python3-gobject
Requires:       python3-cairo
# cairo-1.0.typelib, which the Gtk 4 typelib needs; gtk4 and python3-gobject do not pull it in.
Requires:       gobject-introspection
Requires:       libxkbcommon
Requires:       gtk4
Requires:       libadwaita
Requires:       i2c-tools
Requires:       systemd
Recommends:     python3-pywayland
Recommends:     python3-systemd
Recommends:     gnome-shell
# The on-screen dial on Wayland desktops other than GNOME.
Recommends:     gtk4-layer-shell
# The screen of a 3Dconnexion SpacePilot Pro.
Recommends:     python3-pyusb

%description
Turnpad turns the DialPad of ASUS laptops, any other touchpad, a mouse
with a wheel or a 3Dconnexion SpaceMouse into a dial: a radial menu of
functions, profiles per application, actions on turning and tapping, and an
on-screen dial for GNOME Shell 45 or newer and for Wayland compositors with
layer-shell (KDE Plasma 6, Hyprland, sway, niri, COSMIC).

A SpaceMouse needs spacenavd (not installed by this package); the colour
screen of a SpacePilot Pro is drawn when python3-pyusb is installed.

After installing, each user who wants the dial runs asus-turnpad-setup once
and logs out and back in.

%prep
%autosetup -n %{name}-%{version}

%build
# Plain files, nothing to build.

%install
# The file list is shared with the Arch and Debian packages.
bash packaging/install-files.sh %{buildroot} --no-bytecode
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop

%post
# Groups i2c and uinput of the udev rules; device access and the kernel
# modules now rather than after the next boot (fails harmlessly in containers).
systemd-sysusers %{name}.conf >/dev/null 2>&1 || :
%udev_rules_update
udevadm trigger --subsystem-match=misc --subsystem-match=i2c-dev --subsystem-match=hidraw >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=usb --attr-match=idVendor=046d --attr-match=idProduct=c629 >/dev/null 2>&1 || :
modprobe -a uinput i2c-dev >/dev/null 2>&1 || :
%systemd_user_post %{name}.service %{name}-overlay.service
if [ "$1" -eq 1 ]; then
    echo
    echo "==> Finish the setup once as your user (not root):"
    echo "      asus-turnpad-setup"
    echo "    then log out and back in."
    echo
else
    echo "==> Restart the driver with 'systemctl --user restart asus-turnpad'; a new GNOME extension loads at the next login."
fi

%preun
%systemd_user_preun %{name}.service %{name}-overlay.service
if [ "$1" -eq 0 ]; then
    echo "==> Users of Turnpad: run 'systemctl --user disable --now asus-turnpad asus-turnpad-overlay' and, with a SpaceMouse screen, 'systemctl --user stop asus-turnpad-screen' (settings in ~/.config/asus-turnpad stay)."
fi

%postun
%udev_rules_update

%files
%license LICENSE.md
%doc %{_docdir}/%{name}
%{_datadir}/%{name}
%dir %{_datadir}/gnome-shell
%dir %{_datadir}/gnome-shell/extensions
%{_datadir}/gnome-shell/extensions/%{extension_uuid}
%{_datadir}/applications/%{app_id}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_userunitdir}/%{name}.service
%{_userunitdir}/%{name}-overlay.service
%{_userunitdir}/%{name}-screen.service
%{_udevrulesdir}/70-%{name}.rules
%{_modulesloaddir}/%{name}.conf
%{_sysusersdir}/%{name}.conf
%{_bindir}/%{name}-setup
