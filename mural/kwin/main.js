// Mural helper for KWin: tells Mural when a fullscreen window is showing, so it can pause.
// Loaded by the Mural daemon on KDE Plasma (Wayland); unloaded when Mural stops.
var last = null;

function check() {
    var any = false;
    var wins = workspace.windowList ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < wins.length; i++) {
        var w = wins[i];
        if (w.fullScreen && !w.minimized && w.onCurrentDesktop !== false)
            any = true;
    }
    if (any !== last) {
        last = any;
        callDBus("io.github.dork40.Mural.Daemon", "/io/github/dork40/Mural/Daemon",
                 "io.github.dork40.Mural.Pause", "SetFullscreen", any);
    }
}

function watch(w) {
    if (w.fullScreenChanged) w.fullScreenChanged.connect(check);
    if (w.minimizedChanged) w.minimizedChanged.connect(check);
}

var all = workspace.windowList ? workspace.windowList() : workspace.clientList();
for (var i = 0; i < all.length; i++) watch(all[i]);
(workspace.windowAdded || workspace.clientAdded).connect(function (w) { watch(w); check(); });
(workspace.windowRemoved || workspace.clientRemoved).connect(check);
(workspace.currentDesktopChanged).connect(check);
check();
