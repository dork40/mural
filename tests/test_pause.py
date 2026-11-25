from mural.pause import decide, hypr_fullscreen_monitors, is_game_class, sway_state, x11_fullscreen_screens
from mural import config

CFG = config.clean({})


def test_nothing_pauses_by_default():
    assert decide("eDP-1", CFG) == (False, "")


def test_manual_wins():
    assert decide("eDP-1", CFG, manual=True, game=True) == (True, "manual")


def test_fullscreen_only_pauses_that_screen():
    assert decide("eDP-1", CFG, fullscreen={"eDP-1"}) == (True, "fullscreen")
    assert decide("HDMI-A-1", CFG, fullscreen={"eDP-1"}) == (False, "")


def test_game_pauses_every_screen():
    assert decide("HDMI-A-1", CFG, game=True) == (True, "game")


def test_battery_only_when_enabled():
    assert decide("eDP-1", CFG, on_battery=True) == (False, "")
    assert decide("eDP-1", config.clean({"pause_on_battery": True}), on_battery=True) == (True, "battery")


def test_rules_can_be_turned_off():
    cfg = config.clean({"pause_on_fullscreen": False, "pause_on_game": False, "pause_on_gamemode": False})
    assert decide("eDP-1", cfg, fullscreen={"eDP-1"}, game=True, gamemode=True) == (False, "")


def test_game_classes():
    for cls in ("steam_app_730", "gamescope", "RobloxPlayerBeta.exe", "org.vinegarhq.Sober", "cs2", "Minecraft* 1.21"):
        assert is_game_class(cls), cls
    for cls in ("kitty", "firefox", "steam", "code", None, ""):
        assert not is_game_class(cls), cls


def test_hyprland_fullscreen_per_monitor():
    monitors = [{"name": "eDP-1", "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}},
                {"name": "HDMI-A-1", "activeWorkspace": {"id": 3}, "specialWorkspace": {"id": 0}}]
    workspaces = [{"id": 1, "hasfullscreen": False}, {"id": 3, "hasfullscreen": True}, {"id": 5, "hasfullscreen": True}]
    assert hypr_fullscreen_monitors(monitors, workspaces) == {"HDMI-A-1"}


def test_hyprland_special_workspace_counts():
    monitors = [{"name": "eDP-1", "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": -98}}]
    assert hypr_fullscreen_monitors(monitors, [{"id": -98, "hasfullscreen": True}]) == {"eDP-1"}


def test_hyprland_tolerates_missing_fields():
    assert hypr_fullscreen_monitors([{"name": "eDP-1"}], [{"id": 1}]) == set()


def _ws(name, *nodes):
    return {"type": "workspace", "name": name, "nodes": list(nodes), "floating_nodes": []}


def _win(app_id, fullscreen=0, focused=False):
    return {"type": "con", "app_id": app_id, "fullscreen_mode": fullscreen, "focused": focused,
            "nodes": [], "floating_nodes": []}


def test_sway_fullscreen_and_focus():
    tree = {"type": "root", "nodes": [
        {"type": "output", "name": "eDP-1", "nodes": [_ws("1", _win("kitty")), _ws("2", _win("mpv", fullscreen=1))]},
        {"type": "output", "name": "DP-1", "nodes": [_ws("3", _win("steam_app_730", fullscreen=1, focused=True))]},
    ], "floating_nodes": []}
    outputs = [{"name": "eDP-1", "active": True, "current_workspace": "1"},
               {"name": "DP-1", "active": True, "current_workspace": "3"}]
    full, cls = sway_state(tree, outputs)
    assert full == {"DP-1"}            # workspace 2 is fullscreen but not visible
    assert cls == "steam_app_730"


def test_sway_global_fullscreen_covers_all():
    tree = {"type": "root", "nodes": [
        {"type": "output", "name": "eDP-1", "nodes": [_ws("1", _win("game", fullscreen=2))]},
        {"type": "output", "name": "DP-1", "nodes": [_ws("3")]}], "floating_nodes": []}
    outputs = [{"name": "eDP-1", "active": True, "current_workspace": "1"},
               {"name": "DP-1", "active": True, "current_workspace": "3"}]
    assert sway_state(tree, outputs)[0] == {"eDP-1", "DP-1"}


def test_x11_fullscreen_maps_to_the_right_screen():
    screens = {"eDP-1": (0, 0, 1920, 1080), "HDMI-1": (1920, 0, 2560, 1440)}
    assert x11_fullscreen_screens([(1920, 0, 2560, 1440)], screens) == {"HDMI-1"}
    assert x11_fullscreen_screens([(0, 0, 1920, 1080)], screens) == {"eDP-1"}
    assert x11_fullscreen_screens([], screens) == set()
    assert x11_fullscreen_screens([(0, 0, 1920, 1080), (1920, 0, 2560, 1440)], screens) == {"eDP-1", "HDMI-1"}
