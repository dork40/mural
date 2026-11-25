#!/usr/bin/env bash
# Mural installer:  curl -fsSL https://raw.githubusercontent.com/dork40/mural/main/install.sh | bash
#
# Installs what Mural needs from your distro's own repos (Arch, Fedora, Debian/Ubuntu, openSUSE),
# puts Mural in ~/.local, starts it with your desktop, and on GNOME adds the Mural extension.
# Nothing outside your home folder is touched apart from the distro packages.
#   --uninstall   remove Mural (keeps your config and wallpapers)
#   --no-deps     skip the package install
set -euo pipefail

REPO=${MURAL_REPO:-https://github.com/dork40/mural}
BRANCH=${MURAL_BRANCH:-main}
APP=${XDG_DATA_HOME:-$HOME/.local/share}/mural/app
BIN=$HOME/.local/bin
AUTOSTART=${XDG_CONFIG_HOME:-$HOME/.config}/autostart/mural.desktop
EXT_UUID=mural@dork40.github.io
EXT_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$EXT_UUID

b=$'\e[1m' d=$'\e[2m' r=$'\e[0m'
say() { printf '%s==>%s %s\n' "$b" "$r" "$*"; }
note() { printf '    %s%s%s\n' "$d" "$*" "$r"; }
die() { printf '%serror:%s %s\n' "$b" "$r" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "run this as your normal user (it asks for sudo when it needs it)"

if [[ ${1:-} == --uninstall ]]; then
    "$BIN/mural" stop >/dev/null 2>&1 || true
    rm -rf "$APP" "$BIN/mural" "$AUTOSTART" "$EXT_DIR"
    say "Mural removed. Your settings are still in ~/.config/mural and ~/.local/share/mural."
    exit 0
fi

# -- packages -------------------------------------------------------------------------
. /etc/os-release 2>/dev/null || true
family=""
for id in ${ID:-} ${ID_LIKE:-}; do
    case $id in
        arch|cachyos|endeavouros|manjaro) family=arch; break ;;
        fedora|rhel|centos|nobara|bazzite) family=fedora; break ;;
        debian|ubuntu|linuxmint|pop) family=debian; break ;;
        opensuse*|suse) family=suse; break ;;
    esac
done

install_deps() {
    case $family in
    arch)
        sudo pacman -S --needed --noconfirm python python-gobject gtk4 gtk4-layer-shell python-opengl \
            gst-plugin-gtk4 gst-plugins-base gst-plugins-good gst-libav gst-plugin-va python-xlib python-i3ipc ;;
    fedora)
        local p="python3-gobject gtk4 gtk4-layer-shell python3-pyopengl gstreamer1-plugins-base gstreamer1-plugins-good
                 gstreamer1-plugin-gtk4 gstreamer1-plugin-libav gstreamer1-plugins-bad-free python3-xlib python3-i3ipc"
        if [[ -e /run/ostree-booted ]]; then
            note "atomic system (Silverblue/Bazzite/...): layering with rpm-ostree, a reboot finishes it"
            # shellcheck disable=SC2086
            sudo rpm-ostree install --idempotent --allow-inactive $p
        else
            # shellcheck disable=SC2086
            sudo dnf install -y $p
        fi ;;
    debian)
        sudo apt-get update -q
        local p="python3 python3-gi gir1.2-gtk-4.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 python3-opengl
                 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav gstreamer1.0-gtk4
                 python3-xlib python3-i3ipc"
        # layer-shell and VA-API decoding are newer packages: add them when the release has them
        for opt in libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 gstreamer1.0-plugins-bad; do
            apt-cache show "$opt" >/dev/null 2>&1 && p+=" $opt"
        done
        # shellcheck disable=SC2086
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $p ;;
    suse)
        sudo zypper -n install python3-gobject typelib-1_0-Gtk-4_0 gtk4-layer-shell typelib-1_0-Gtk4LayerShell-1_0 \
            python3-PyOpenGL gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugin-gtk4 gstreamer-plugins-libav \
            python3-python-xlib python3-i3ipc ;;
    *)
        die "don't know how to install packages on '${PRETTY_NAME:-this system}'. Install by hand:
  PyGObject, GTK 4, gtk4-layer-shell, PyOpenGL, GStreamer (base, good, libav, gtk4 sink), python-xlib
then run again with --no-deps" ;;
    esac
}

if [[ ${1:-} != --no-deps ]]; then
    say "Installing what Mural needs (${PRETTY_NAME:-$family})"
    install_deps
fi

# -- Mural itself -----------------------------------------------------------------------
src=""
here=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)
if [[ -n $here && -f $here/mural/daemon.py ]]; then
    src=$here                                         # running from a checkout
else
    say "Downloading Mural"
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$tmp" --strip-components=1
    src=$tmp
fi

say "Installing to $APP"
"$BIN/mural" stop >/dev/null 2>&1 || true
rm -rf "$APP"; mkdir -p "$APP" "$BIN"
cp -r "$src/mural" "$APP/"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +
cat > "$BIN/mural" <<EOF
#!/bin/sh
# Mural launcher (installed by install.sh)
PYTHONPATH="$APP\${PYTHONPATH:+:\$PYTHONPATH}" exec /usr/bin/python3 -m mural "\$@"
EOF
chmod +x "$BIN/mural"

mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Mural
Comment=Live wallpaper
Exec=$BIN/mural start
X-GNOME-Autostart-Phase=Applications
NoDisplay=true
EOF

desk=${XDG_CURRENT_DESKTOP:-}
if [[ ${desk,,} == *gnome* ]] || command -v gnome-shell >/dev/null; then
    say "Adding the Mural GNOME Shell extension"
    rm -rf "$EXT_DIR"; mkdir -p "$(dirname "$EXT_DIR")"
    cp -r "$src/gnome-extension/$EXT_UUID" "$EXT_DIR"
    gnome-extensions enable "$EXT_UUID" 2>/dev/null ||
        note "log out and back in, then run: gnome-extensions enable $EXT_UUID"
fi

# -- check it runs ----------------------------------------------------------------------
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Gst","1.0"); from gi.repository import Gtk, Gst; import OpenGL' \
    || die "a dependency is missing (see the error above)"

case :$PATH: in *":$BIN:"*) ;; *) note "add ~/.local/bin to your PATH to use the 'mural' command" ;; esac

if [[ -n ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]]; then
    "$BIN/mural" start >/dev/null 2>&1 && say "Mural is running." || note "start it with: mural start"
fi

cat <<EOF

${b}Mural is installed.${r}
    mural list                  see the wallpapers
    mural set aurora            a built-in one
    mural set ~/Videos/rain.mp4 a video (always silent) or any picture
    mural pause / resume        (it also pauses by itself for games and fullscreen apps)

    It starts with your desktop. On Hyprland, Sway, i3 and other window managers without
    autostart, add this to your config instead:   exec-once = mural start   (Sway/i3: exec mural start)
EOF
