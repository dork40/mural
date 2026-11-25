// Mural for GNOME Shell: puts Mural's wallpaper windows behind the desktop.
//
// GNOME has no layer-shell, so the Mural daemon opens ordinary windows titled
// "@mural!x,y,w,h" (the screen they belong to). This extension spots those windows,
// moves them into the background layer (where GNOME draws its own wallpaper), keeps
// them out of the overview, Alt+Tab and the dock, and tells Mural when an app goes
// fullscreen so it can pause.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as AltTab from 'resource:///org/gnome/shell/ui/altTab.js';
import * as Workspace from 'resource:///org/gnome/shell/ui/workspace.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const TITLE = /^@mural!(-?\d+),(-?\d+),(\d+),(\d+)$/;
const DAEMON = 'io.github.dork40.Mural.Daemon';
const DAEMON_PATH = '/io/github/dork40/Mural/Daemon';

function isMural(metaWindow) {
    return metaWindow && TITLE.test(metaWindow.get_title() ?? '');
}

export default class MuralExtension extends Extension {
    enable() {
        this._injections = new InjectionManager();
        this._windows = new Map();          // MetaWindow -> {actor, signals}
        this._signals = [];
        this._fullscreen = false;

        // never show our windows in the overview or the window switchers
        this._injections.overrideMethod(Workspace.Workspace.prototype, '_isOverviewWindow', orig =>
            function (window) { return isMural(window) ? false : orig.call(this, window); });
        for (const cls of [AltTab.WindowSwitcherPopup, AltTab.AppSwitcherPopup]) {
            if (cls?.prototype?._getWindowList)
                this._injections.overrideMethod(cls.prototype, '_getWindowList', orig =>
                    function (...args) { return orig.apply(this, args).filter(w => !isMural(w)); });
        }
        const tracker = Meta.Display.prototype;
        this._injections.overrideMethod(tracker, 'get_tab_list', orig =>
            function (...args) { return orig.apply(this, args).filter(w => !isMural(w)); });

        this._signals.push([global.display, global.display.connect('window-created', (_d, w) => this._watch(w))]);
        this._signals.push([global.display, global.display.connect('in-fullscreen-changed', () => this._checkFullscreen())]);
        for (const actor of global.get_window_actors())
            this._watch(actor.meta_window);
    }

    disable() {
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        for (const [w, info] of this._windows)
            this._release(w, info);
        this._windows.clear();
        this._injections.clear();
        this._injections = null;
        this._setPaused(false);
    }

    _watch(metaWindow) {
        if (!metaWindow || this._windows.has(metaWindow))
            return;
        // the title can arrive a moment after the window does
        const tryTake = () => {
            if (!this._windows.has(metaWindow) && isMural(metaWindow))
                this._take(metaWindow);
        };
        const id = metaWindow.connect('notify::title', tryTake);
        metaWindow.connect('unmanaged', () => metaWindow.disconnect(id));
        tryTake();
    }

    _take(metaWindow) {
        const actor = metaWindow.get_compositor_private();
        if (!actor) {
            const later = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
                this._take(metaWindow);
                return false;
            });
            return later;
        }
        const [, x, y, w, h] = TITLE.exec(metaWindow.get_title()).map(Number);
        metaWindow.stick();
        metaWindow.make_below?.();
        metaWindow.move_resize_frame(false, x, y, w, h);

        // move the window's picture from the normal window stack into the background layer
        const parent = actor.get_parent();
        parent?.remove_child(actor);
        const bg = Main.layoutManager._backgroundGroup;
        bg.add_child(actor);
        bg.set_child_above_sibling(actor, null);   // above GNOME's own wallpaper
        actor.set_position(x, y);
        actor.reactive = false;

        const signals = [
            metaWindow.connect('position-changed', () => actor.set_position(x, y)),
            metaWindow.connect('unmanaged', () => this._windows.delete(metaWindow)),
        ];
        this._windows.set(metaWindow, {actor, signals, parent});
    }

    _release(metaWindow, {actor, signals, parent}) {
        for (const id of signals)
            metaWindow.disconnect(id);
        if (actor.get_parent() === Main.layoutManager._backgroundGroup) {
            Main.layoutManager._backgroundGroup.remove_child(actor);
            (parent ?? global.window_group).add_child(actor);
        }
    }

    _checkFullscreen() {
        const n = global.display.get_n_monitors();
        let any = false;
        for (let i = 0; i < n; i++)
            any ||= global.display.get_monitor_in_fullscreen(i);
        if (any !== this._fullscreen)
            this._setPaused(any);
    }

    _setPaused(paused) {
        this._fullscreen = paused;
        Gio.DBus.session.call(DAEMON, DAEMON_PATH, 'io.github.dork40.Mural.Pause', 'SetFullscreen',
            new GLib.Variant('(b)', [paused]), null, Gio.DBusCallFlags.NO_AUTO_START, -1, null, null);
    }
}
