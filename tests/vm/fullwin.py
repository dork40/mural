#!/usr/bin/env python3
"""Open one fullscreen window (stands in for a game or video player in the VM tests)."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title="fullscreen test")
    win.fullscreen()
    win.present()


app = Gtk.Application(application_id="io.github.dork40.MuralTestFull")
app.connect("activate", activate)
app.run()
