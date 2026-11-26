"""The "Login screen" tab: put a Mural wallpaper behind the SDDM login box."""
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import library, lock, login  # noqa: E402

KIND_LABEL = {"shader": "Animated", "video": "Video", "image": "Image"}
QUALITY_KEYS = ["sharp", "balanced", "light"]


class LoginPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.walls = []
        self.preview_view = None
        self.busy = False

        if not login.sddm_installed():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(Adw.StatusPage(icon_name="system-lock-screen-symbolic", vexpand=not lock.hyprlock_installed(),
                                      title="Your login screen isn't SDDM",
                                      description="Mural can change the login screen on systems that use SDDM "
                                                  "(KDE, most Arch and Hyprland setups). Yours uses something else."))
            if lock.hyprlock_installed():
                lock_page = Adw.PreferencesPage()
                lock_page.add(self._lock_group())
                box.append(lock_page)
            self.append(box)
            return

        page = Adw.PreferencesPage()
        self.append(page)

        # -- a live preview, with a sketch of the login box over it ----------------------------
        top = Adw.PreferencesGroup()
        self.preview_frame = Adw.Bin(height_request=300, css_classes=["login-preview"])
        self.preview_frame.set_overflow(Gtk.Overflow.HIDDEN)
        overlay = Gtk.Overlay(child=self.preview_frame)
        sketch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.START,
                         valign=Gtk.Align.CENTER, margin_start=36, can_target=False, css_classes=["login-sketch"])
        self.clock = Gtk.Label(xalign=0, css_classes=["sketch-clock"])
        sketch.append(self.clock)
        sketch.append(Gtk.Box(css_classes=["sketch-field"], width_request=190, height_request=24))
        sketch.append(Gtk.Box(css_classes=["sketch-field", "focus"], width_request=190, height_request=24))
        overlay.add_overlay(sketch)
        top.add(overlay)
        self.now_label = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"], margin_top=10)
        top.add(self.now_label)
        page.add(top)
        self._tick_clock()
        GLib.timeout_add_seconds(20, self._tick_clock)

        # -- choices ---------------------------------------------------------------------------
        bg = Adw.PreferencesGroup(title="Background",
                                  description="Animated ones are drawn live, so they run at your screen's full "
                                              "refresh rate. Videos never make a sound.")
        self.choice = Adw.ComboRow(title="Wallpaper")
        self.choice.connect("notify::selected", lambda *_: self._on_choice())
        bg.add(self.choice)
        self.quality = Adw.ComboRow(title="Quality", subtitle="Animated backgrounds are soft, so Balanced looks "
                                                              "the same as Sharp for less GPU",
                                    model=Gtk.StringList.new(["Sharp", "Balanced", "Light"]))
        self.quality.set_selected(1)
        bg.add(self.quality)
        page.add(bg)

        disp = Adw.PreferencesGroup(title="Screen",
                                    description="SDDM normally starts the login screen at 60 Hz. Pick what it "
                                                "should use instead.")
        self.modes = login.screen_modes()
        best = self.modes[0] if self.modes else None
        res_names = ["Leave it to SDDM",
                     f"Best for this screen ({best[0][0]}×{best[0][1]}, {best[1][0]} Hz)" if best else
                     "Best for this screen"]
        res_names += [f"{w}×{h}" for (w, h), _ in self.modes]
        self.res_row = Adw.ComboRow(title="Resolution", model=Gtk.StringList.new(res_names))
        self.rate_row = Adw.ComboRow(title="Refresh rate")
        self.res_row.connect("notify::selected", lambda *_: self._on_res())
        disp.add(self.res_row)
        disp.add(self.rate_row)
        page.add(disp)
        self._load_mode(login.login_display())

        if lock.hyprlock_installed():
            page.add(self._lock_group())

        actions = Adw.PreferencesGroup()
        box = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        self.restore_btn = Gtk.Button(label="Put my old login screen back", css_classes=["flat"])
        self.restore_btn.connect("clicked", lambda *_: self.restore())
        self.preview_btn = Gtk.Button(label="Preview", css_classes=["pill"])
        self.preview_btn.set_tooltip_text("Show the login screen in a window, exactly as SDDM draws it "
                                          "(press Esc to close it). Doesn't change anything.")
        self.preview_btn.connect("clicked", lambda *_: self.preview())
        self.apply_btn = Gtk.Button(label="Use on login screen", css_classes=["suggested-action", "pill"])
        self.apply_btn.connect("clicked", lambda *_: self.apply())
        box.append(self.restore_btn)
        box.append(self.preview_btn)
        box.append(self.apply_btn)
        actions.add(box)
        page.add(actions)

        self.reload()

    # -- screen mode ---------------------------------------------------------------------------
    def _rates(self):
        i = self.res_row.get_selected() - 2
        return self.modes[i][1] if 0 <= i < len(self.modes) else []

    def _on_res(self):
        rates = self._rates()
        self.rate_row.set_visible(bool(rates))
        self.rate_row.set_model(Gtk.StringList.new(["Highest"] + [f"{r} Hz" for r in rates]))
        self.rate_row.set_selected(0)

    def _load_mode(self, cur):
        if cur == "off":
            self.res_row.set_selected(0)
        elif cur == "auto":
            self.res_row.set_selected(1)
        else:
            w, h, rate = cur
            i = next((n for n, (wh, _) in enumerate(self.modes) if wh == (w, h)), None)
            if i is None:
                self.res_row.set_selected(1)
            else:
                self.res_row.set_selected(i + 2)
                self._on_res()
                rates = self._rates()
                if rate != "max" and rate in rates:
                    self.rate_row.set_selected(rates.index(rate) + 1)
        self._on_res() if self.res_row.get_selected() < 2 else None

    def _mode(self):
        """The choice as the helper takes it: off | auto | 1920x1200@165 | 1920x1200@max."""
        sel = self.res_row.get_selected()
        if sel == 0:
            return "off"
        if sel == 1 or not self._rates():
            return "auto"
        (w, h), rates = self.modes[sel - 2]
        r = self.rate_row.get_selected()
        return f"{w}x{h}@{'max' if r == 0 else rates[r - 1]}"

    @staticmethod
    def mode_text(cur):
        if cur == "off":
            return ""
        if cur == "auto":
            return ", best resolution and refresh rate"
        w, h, rate = cur
        return f", {w}×{h} at {'the highest refresh rate' if rate == 'max' else f'{rate} Hz'}"

    # -- data ----------------------------------------------------------------------------------
    def reload(self):
        if not hasattr(self, "choice"):
            return
        state = login.current_state()
        self.walls = [w for w in library.list_all() if w.kind == "shader" or w.kind in ("video", "image")]
        names = [f"{w.name}  ·  {KIND_LABEL[w.kind]}" for w in self.walls]
        self.choice.set_model(Gtk.StringList.new(names))
        pick = 0
        if state["wallpaper"]:
            pick = next((i for i, w in enumerate(self.walls) if w.name == state["wallpaper"]), 0)
        self.choice.set_selected(pick)
        self._show_state(state)
        self._on_choice()

    def _show_state(self, state):
        if state["mural"]:
            text = f"Your login screen now: {state['wallpaper'] or 'Mural'}"
            text += self.mode_text(login.login_display())
        else:
            text = f"Your login screen now: the “{state['theme']}” theme. Pick a background and press Use."
        if not login.sddm_active():
            text += "\nSDDM is installed but isn't what starts at boot, so you won't see this until it is."
        self.now_label.set_label(text)
        self.restore_btn.set_visible(state["mural"])

    def _selected(self):
        i = self.choice.get_selected()
        return self.walls[i] if 0 <= i < len(self.walls) else None

    def _on_choice(self):
        wp = self._selected()
        self.quality.set_visible(bool(wp and wp.kind == "shader"))
        if self.preview_view is not None:
            self.preview_view.stop() if hasattr(self.preview_view, "stop") else None
        self.preview_view = None
        if wp is None:
            self.preview_frame.set_child(None)
            return
        from .render import ImageView, ShaderView, VideoView
        try:
            if wp.kind == "shader":
                view = ShaderView(wp.path.read_text(), self.window.palette, fps=60)
            elif wp.kind == "video":
                view = VideoView(wp.path)
            else:
                view = ImageView(wp.path)
        except Exception:  # noqa: BLE001 - a broken file just shows no preview
            view = None
        self.preview_view = view
        self.preview_frame.set_child(view)
        self.set_previewing(self.get_mapped())

    def set_previewing(self, on):
        """Only animate the preview while this tab is showing."""
        v = self.preview_view
        if v is None:
            return
        (v.resume if on else v.pause)()

    def _tick_clock(self):
        self.clock.set_label(GLib.DateTime.new_now_local().format("%-I:%M"))
        return True

    # -- applying ------------------------------------------------------------------------------
    def _set_busy(self, on, label=None):
        self.busy = on
        self.apply_btn.set_sensitive(not on)
        self.restore_btn.set_sensitive(not on)
        self.preview_btn.set_sensitive(not on)
        self.apply_btn.set_label(label or "Use on login screen")

    def apply(self):
        wp = self._selected()
        if wp is None or self.busy:
            return
        if wp.kind == "shader" and not login.qsb_path():
            self.window.toast("Animated login screens need Qt's shader tool (qt6-shadertools)")
            return
        self._set_busy(True, "Preparing…")
        quality = QUALITY_KEYS[self.quality.get_selected()]
        mode = self._mode()
        pal = self.window.palette

        def work():
            try:
                theme = login.build_theme(wp, pal, quality)
                cmd = login.apply_command(theme, mode)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._done, False, str(e))
                return
            GLib.idle_add(self._run, cmd, f"{wp.name} is your login screen now")
        threading.Thread(target=work, daemon=True).start()

    def preview(self):
        """Build the chosen theme and open it in SDDM's own greeter, in a window. No root, no changes."""
        wp = self._selected()
        if wp is None or self.busy:
            return
        if wp.kind == "shader" and not login.qsb_path():
            self.window.toast("Animated login screens need Qt's shader tool (qt6-shadertools)")
            return
        if login.greeter_path() is None:
            self.window.toast("SDDM's greeter isn't installed, so there's nothing to preview with")
            return
        self.preview_btn.set_sensitive(False)
        self.preview_btn.set_label("Preparing…")
        quality = QUALITY_KEYS[self.quality.get_selected()]
        pal = self.window.palette
        from . import const

        def work():
            try:
                theme = login.build_theme(wp, pal, quality, dest=const.CACHE_DIR / "preview-theme")
                cmd = login.preview_command(theme)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._preview_done, str(e))
                return
            GLib.idle_add(self._launch_preview, cmd)
        threading.Thread(target=work, daemon=True).start()

    def _launch_preview(self, cmd):
        try:
            Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            self._preview_done(e.message)
            return False
        self._preview_done(None)
        self.window.toast("Preview open — press Esc to close it")
        return False

    def _preview_done(self, err):
        self.preview_btn.set_sensitive(True)
        self.preview_btn.set_label("Preview")
        if err:
            self.window.toast(err)
        return False

    # -- lock screen (hyprlock) ----------------------------------------------------------------
    def _lock_group(self):
        self._lock_busy = False
        g = Adw.PreferencesGroup(
            title="Lock screen (hyprlock)",
            description="hyprlock shows a still image, so Mural keeps a snapshot of your wallpaper on "
                        "the lock screen and refreshes it whenever you change wallpaper. Your blur, "
                        "clock and password box stay exactly as they are.")
        self.lock_switch = Adw.SwitchRow(title="Use my wallpaper on the lock screen")
        self.lock_switch.set_active(lock.current_state()["managed"])
        self.lock_switch.connect("notify::active", lambda *_: self._on_lock_toggle())
        g.add(self.lock_switch)
        self.lock_update_row = Adw.ActionRow(title="Update the lock image now",
                                             subtitle="Match it to what's on your screen this second")
        btn = Gtk.Button(label="Update", valign=Gtk.Align.CENTER, css_classes=["flat"])
        btn.connect("clicked", lambda *_: self._refresh_lock_image(True))
        self.lock_update_row.add_suffix(btn)
        self.lock_update_row.set_activatable_widget(btn)
        g.add(self.lock_update_row)
        self.lock_update_row.set_visible(self.lock_switch.get_active())
        return g

    def _snapshot_now(self):
        """Ask the daemon for a fresh snapshot and wait for it (runs on a worker thread)."""
        from . import cli
        if cli.daemon_running():
            cli.daemon_action("snapshot")
            for _ in range(20):
                if lock.snapshot_files():
                    break
                time.sleep(0.1)

    def _on_lock_toggle(self):
        if self._lock_busy:
            return
        on = self.lock_switch.get_active()
        self.lock_update_row.set_visible(on)
        self.lock_switch.set_sensitive(False)

        def work():
            err = None
            try:
                if on:
                    self._snapshot_now()
                    lock.save_lock_image()
                    lock.enable()
                else:
                    lock.disable()
                from . import cli, config
                cfg = config.load()
                cfg["lock_screen"] = on
                config.save(cfg)
                if cli.daemon_running():
                    cli.daemon_action("reload")
            except Exception as e:  # noqa: BLE001
                err = str(e)
            GLib.idle_add(self._lock_toggle_done, on, err)
        threading.Thread(target=work, daemon=True).start()

    def _lock_toggle_done(self, on, err):
        self.lock_switch.set_sensitive(True)
        if err:
            self._lock_busy = True
            self.lock_switch.set_active(not on)
            self.lock_update_row.set_visible(not on)
            self._lock_busy = False
            self.window.toast(err)
        else:
            self.window.toast("Your wallpaper is on the lock screen now" if on
                              else "Lock screen back to how it was")
        return False

    def _refresh_lock_image(self, toast=False):
        def work():
            try:
                self._snapshot_now()
                ok = lock.save_lock_image()
            except Exception:  # noqa: BLE001
                ok = False
            if toast:
                GLib.idle_add(self.window.toast,
                              "Lock image updated" if ok else "Couldn't update it — is Mural running?")
        threading.Thread(target=work, daemon=True).start()

    def restore(self):
        if self.busy:
            return
        try:
            cmd = login.restore_command()
        except RuntimeError as e:
            self.window.toast(str(e))
            return
        self._set_busy(True, "Working…")
        self._run(cmd, "Your old login screen is back")

    def _run(self, cmd, ok_text):
        self._set_busy(True, "Waiting for your password…")
        try:
            proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self._done(False, e.message)
            return False

        def finished(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                self._done(False, e.message)
                return
            code = p.get_exit_status()
            if code == 0:
                self._done(True, ok_text)
            elif code in (126, 127):         # pkexec: dialog dismissed / not authorised
                self._done(False, "Nothing changed: the password wasn't given")
            else:
                self._done(False, (out or "").strip().splitlines()[-1] if out and out.strip() else "It didn't work")
        proc.communicate_utf8_async(None, None, finished)
        return False

    def _done(self, ok, text):
        self._set_busy(False)
        self.window.toast(text)
        self._show_state(login.current_state())
        return False
