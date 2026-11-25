#!/usr/bin/env bash
# desktop-test.sh <desktop> <ssh-port>
#   desktop: hyprland | sway | kde | xfce | i3 | gnome
# Builds (once) an Arch VM with that desktop logging in automatically, then runs Mural inside the
# real session: a shader, a video, fullscreen auto-pause. Screenshots of the VM screen go to
# $W/shots/mural-<desktop>-*.png and a report to $W/logs/mural-<desktop>.log.
# Uses the hyprland-rice VM harness (~/Projects/hyprland-rice/tests/vm/vm.sh) and its base images.
set -uo pipefail
de=$1 port=$2
W=${WIRE_VMTEST:-$HOME/Projects/wire-vmtest}
RICE=$HOME/Projects/hyprland-rice/tests/vm
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
name=mural-$de
LOG=$W/logs/$name.log
S=(ssh -q -i "$W/id_vm" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -p "$port" rice@127.0.0.1)
say() { echo "### $*" | tee -a "$LOG"; }
vmssh() { "${S[@]}" "$@"; }
waitssh() { for _ in $(seq 1 120); do vmssh true 2>/dev/null && return 0; sleep 5; done; say "no ssh"; return 1; }
stopvm() { local p; p=$(pgrep -f "^qemu-system-x86_64 -name rice-$name ") && kill $p; sleep 3; }
shot() { python3 "$RICE/shot.py" "$name" "$W/shots/$name-$1.png" >/dev/null && say "screenshot $W/shots/$name-$1.png"; }

case $de in
  hyprland) pkgs="hyprland kitty"; proc=Hyprland; session="exec Hyprland" ;;
  sway)     pkgs="sway foot"; proc=sway; session="exec sway" ;;
  kde)      pkgs="plasma-desktop konsole"; proc=plasmashell; session="exec dbus-run-session startplasma-wayland" ;;
  xfce)     pkgs="xfce4 xorg-server xorg-xinit"; proc=xfce4-session; session="exec startx /usr/bin/startxfce4" ;;
  i3)       pkgs="i3-wm xorg-server xorg-xinit xterm"; proc=i3; session="exec startx /usr/bin/i3" ;;
  gnome)    pkgs="gnome-shell gnome-session gnome-console gdm"; proc=gnome-shell; session="" ;;
  *) echo "unknown desktop $de"; exit 2 ;;
esac
common="python python-gobject gtk4 gtk4-layer-shell python-opengl gst-plugin-gtk4 gst-plugins-base gst-plugins-good gst-libav python-xlib python-i3ipc mesa ttf-dejavu"

: > "$LOG"
mkdir -p "$W/shots"
ln -sf "$W/base/arch.qcow2" "$W/base/$name.qcow2"
stopvm
fresh=0
[[ -f $W/run/$name.qcow2 && -f $W/run/$name.ready ]] || { rm -f "$W/run/$name.ready"; fresh=1; }
if ((fresh)); then
  say "building the $de VM (one time)"
  MEM=4096 bash "$RICE/vm.sh" "$name" "$port" > "$W/logs/$name-qemu.log" 2>&1 &
  sleep 3; waitssh || exit 1
  vmssh 'cloud-init status --wait >/dev/null 2>&1; true'
  vmssh "sudo pacman -Syu --noconfirm --needed $pkgs $common" >> "$LOG" 2>&1 || { say "package install FAILED"; exit 1; }
  vmssh "sudo mkdir -p /etc/systemd/system/getty@tty1.service.d && printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin rice --noclear %%I \$TERM\n' | sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null
    printf 'if [ \"\$(tty)\" = /dev/tty1 ]; then $session > ~/session.log 2>&1; fi\n' > ~/.bash_profile
    sudo systemctl set-default multi-user.target >/dev/null 2>&1; true" >/dev/null 2>&1
  if [[ $de == gnome ]]; then   # GNOME wants a proper login: GDM with automatic login
    vmssh "rm -f ~/.bash_profile; sudo rm -rf /etc/systemd/system/getty@tty1.service.d
      printf '[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=rice\nWaylandEnable=true\n' | sudo tee /etc/gdm/custom.conf >/dev/null
      sudo systemctl enable gdm >/dev/null 2>&1; sudo systemctl set-default graphical.target >/dev/null 2>&1" >> "$LOG" 2>&1
  fi
  vmssh "sync" ; sleep 2; stopvm
  touch "$W/run/$name.ready"
fi

say "booting into $de"
REUSE=1 MEM=4096 bash "$RICE/vm.sh" "$name" "$port" > "$W/logs/$name-qemu.log" 2>&1 &
sleep 3; waitssh || exit 1
for _ in $(seq 1 60); do vmssh "pgrep -u rice -x $proc >/dev/null" && break; sleep 3; done
vmssh "pgrep -u rice -x $proc >/dev/null" || { say "$de session did not start"; vmssh 'tail -20 ~/session.log' >> "$LOG"; shot nosession; exit 1; }
sleep 15   # let the desktop finish starting
shot desktop

tar -czf "$W/mural-src.tgz" -C "$REPO" --exclude=.git --exclude=tests/shots mural gnome-extension tests/vm/fullwin.py docs/nebula.jpg testdata/nasa-earth-ocean.mp4
scp -q -i "$W/id_vm" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P "$port" "$W/mural-src.tgz" rice@127.0.0.1:
vmssh 'rm -rf mural-src ~/.local/state/mural && mkdir mural-src && tar -xzf mural-src.tgz -C mural-src'
# run a command inside the desktop session (its display, D-Bus and compositor sockets)
insess() {
  vmssh "pid=\$(pgrep -u rice -x $proc | head -1)
    eval \"\$(tr '\\0' '\\n' < /proc/\$pid/environ | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|HYPRLAND_INSTANCE_SIGNATURE|SWAYSOCK|I3SOCK|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE)=' | sed 's/^/export /; s/=\\(.*\\)\$/=\"\\1\"/')\"
    export XDG_RUNTIME_DIR=\${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}
    # compositors set these for their children, not for themselves: find them
    [ -n \"\${WAYLAND_DISPLAY:-}\" ] || { w=\$(ls \$XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+\$' | head -1); [ -n \"\$w\" ] && export WAYLAND_DISPLAY=\$w; }
    [ -n \"\${HYPRLAND_INSTANCE_SIGNATURE:-}\" ] || { h=\$(ls \$XDG_RUNTIME_DIR/hypr 2>/dev/null | head -1); [ -n \"\$h\" ] && export HYPRLAND_INSTANCE_SIGNATURE=\$h; }
    [ -n \"\${SWAYSOCK:-}\" ] || { s=\$(ls \$XDG_RUNTIME_DIR/sway-ipc.*.sock 2>/dev/null | head -1); [ -n \"\$s\" ] && export SWAYSOCK=\$s I3SOCK=\$s; }
    [ -n \"\${DISPLAY:-}\" ] || { [ -e /tmp/.X11-unix/X0 ] && [ -z \"\${WAYLAND_DISPLAY:-}\" ] && export DISPLAY=:0; }
    export DBUS_SESSION_BUS_ADDRESS=\${DBUS_SESSION_BUS_ADDRESS:-unix:path=\$XDG_RUNTIME_DIR/bus}
    cd ~/mural-src && $*"
}
if [[ $de == gnome ]]; then
  say "installing + enabling the GNOME extension (GNOME only loads new extensions at login)"
  vmssh 'd=~/.local/share/gnome-shell/extensions; mkdir -p $d && cp -r ~/mural-src/gnome-extension/mural@dork40.github.io $d/'
  insess "gsettings set org.gnome.shell enabled-extensions \"['mural@dork40.github.io']\"; gsettings set org.gnome.shell disable-user-extensions false; gsettings set org.gnome.shell welcome-dialog-last-shown-version 999"
  vmssh 'sudo systemctl restart gdm'
  sleep 10; for _ in $(seq 1 40); do vmssh "pgrep -u rice -x $proc >/dev/null" && break; sleep 3; done; sleep 20
  insess "gnome-extensions info mural@dork40.github.io | grep -i state" | tee -a "$LOG"
fi
M="python3 -m mural"

say "shader: aurora"
insess "$M set aurora" >> "$LOG" 2>&1
sleep 12; insess "$M status" | tee -a "$LOG"; shot aurora

say "video: NASA clip"
insess "$M set testdata/nasa-earth-ocean.mp4" >> "$LOG" 2>&1
sleep 10; insess "$M status" | tee -a "$LOG"; shot video

say "image"
insess "$M set docs/nebula.jpg" >> "$LOG" 2>&1
sleep 6; insess "$M status" | tee -a "$LOG"; shot image
insess "$M set aurora" >/dev/null 2>&1; sleep 4

say "fullscreen auto-pause"
insess "setsid -f python3 tests/vm/fullwin.py </dev/null >/tmp/full.log 2>&1"
sleep 6; insess "$M status" | tee -a "$LOG"; shot fullscreen
insess "pkill -f fullwin.py; true"; sleep 4
say "after closing it:"; insess "$M status" | tee -a "$LOG"

say "errors in the Mural log:"
vmssh 'grep -E "ERROR|Traceback|CRITICAL" ~/.local/state/mural/daemon.log | head -20 || true' | tee -a "$LOG"
insess "$M stop" >/dev/null 2>&1
[[ ${KEEP:-} ]] || stopvm
say "done"
