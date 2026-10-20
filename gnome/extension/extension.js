import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Cairo from 'cairo';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

import {
    DEFAULT_SKIN,
    clearImageCache,
    effectiveLook,
    iconLayout,
    iconNameFor,
    openSubmenu,
    overlayPlacement,
    paintSubmenu,
    parseColor,
    pillContent,
    pillPlacement,
    resolveSkin,
    submenuKey,
    submenuLayout,
    submenuPlacement,
    submenuSize,
} from './dial.js';
import { DialLayers } from './layers.js';

const BUS_NAME = 'io.github.sinu0.AsusTurnpad.Driver';
const BUS_PATH = '/io/github/sinu0/AsusTurnpad/Driver';
const BUS_IFACE = BUS_NAME;
const CONFIG_DIR = GLib.build_filenamev([GLib.get_user_config_dir(), 'asus-turnpad']);
const CONFIG_NAME = 'config.json';
const SKINS_DIR = GLib.build_filenamev([CONFIG_DIR, 'skins']);
const SKINS_NAME = 'skins.json';
const FADE_IN_MS = 120;
const FADE_OUT_MS = 260;
// Visibility "fade": full this long after a touch, then fading slowly.
const FADE_AFTER_MS = 1000;
const FADE_SLOW_MS = 600;
const PILL_ICON_SIZE = 18;
// A group's list slides out from the dial.
const LIST_OPEN_MS = 180;
const LIST_CLOSE_MS = 120;
const LIST_SLIDE = 24;

const DEFAULT_APPEARANCE = {
    scale: 1.0,
    position: 'lower',
    hide_delay_ms: 1500,
    show_hints: true,
    show_pointer: true,
    accent: '#e7b98d',
    skin: DEFAULT_SKIN,
    skin_light: 'frost',
    follow_system_style: false,
    custom_x: 0.5,
    custom_y: 0.5,
    visibility: 'full',
    fade_opacity: 35,
    pointer_side: 'below-right',
    pointer_gap: 24,
};

export default class DialPadExtension extends Extension {
    enable() {
        this._signalId = 0;
        this._detailsSignalId = 0;
        this._focusHandlerId = 0;
        // The focused window whose title is followed (SetActiveWindow).
        this._titleWindow = null;
        this._titleHandlerId = 0;
        this._monitorsId = 0;
        // The monitor the dial is on and the pointer on it ([x, y] from its
        // corner) when the dial appeared; kept until it is fully hidden.
        this._monitor = null;
        this._pointer = null;
        this._configMonitors = [];
        this._configReloadId = 0;
        this._interfaceSettings = null;
        this._styleChangedId = 0;
        this._overlay = null;
        this._layers = null;
        this._iconLayer = null;
        this._iconKey = '';
        this._hideTimeoutId = 0;
        this._fadeTimeoutId = 0;
        this._shown = false;
        this._shownActor = null;
        this._pill = null;
        this._pillKey = '';
        this._lookKey = '';
        this._list = null;
        this._listKey = '';
        this._listIconKey = '';
        this._listSide = 'right';
        this._listScale = 1;
        this._listOpen = false;
        this._dialPlacement = null;
        this._appearance = { ...DEFAULT_APPEARANCE };
        this._skin = resolveSkin(null, DEFAULT_SKIN);
        this._skinId = '';
        this._state = {
            enabled: false,
            title: '',
            titles: [],
            icons: [],
            menuTitles: [],
            menuIcons: [],
            value: 0,
            valueAngleStart: 0.0,
            unit: '',
            centerPressed: false,
            valueShowOnlyProgress: false,
            touching: false,
        };
        this._details = {
            profile: '',
            mode: 'multi',
            colors: [],
            color: '',
            hints: {},
            hold_returns: true,
            pointer: -1,
            look: {},
            icon: '',
            parent: '',
            submenu: null,
        };

        try {
            this._watchStyle();
            this._loadAppearance();
            this._watchConfig();
            this._connectDBus();
            this._connectFocus();
            this._createOverlay();
            this._fetchDetails();
        } catch (error) {
            log(`turnpad: unable to start overlay: ${error}`);
        }
    }

    disable() {
        this._cancelHide();
        this._cancelFade();
        try { this._unwatchConfig(); } catch (_) {}
        try { this._unwatchStyle(); } catch (_) {}
        try { this._disconnectFocus(); } catch (_) {}
        try { this._disconnectDBus(); } catch (_) {}
        try { this._destroyOverlay(); } catch (_) {}
    }

    // --- appearance from config.json -----------------------------------

    _loadAppearance() {
        const appearance = { ...DEFAULT_APPEARANCE };
        try {
            const file = Gio.File.new_for_path(GLib.build_filenamev([CONFIG_DIR, CONFIG_NAME]));
            const [ok, bytes] = file.load_contents(null);
            if (ok) {
                const overlay = JSON.parse(new TextDecoder().decode(bytes)).overlay || {};
                for (const key of Object.keys(DEFAULT_APPEARANCE)) {
                    if (overlay[key] !== undefined && overlay[key] !== null)
                        appearance[key] = overlay[key];
                }
            }
        } catch (error) {
            // An unreadable file keeps the last good look instead of
            // jumping to the default skin.
            if (this._skinId) {
                log(`turnpad: keeping the current look, config.json unreadable: ${error}`);
                return;
            }
        }
        appearance.scale = Math.max(0.6, Math.min(1.6, Number(appearance.scale) || 1));
        appearance.hide_delay_ms = Math.max(0, Math.min(10000, Number(appearance.hide_delay_ms) || 0));
        this._appearance = appearance;
        this._accent = parseColor(appearance.accent, [0.906, 0.725, 0.553, 1]);
        this._applySkin();
    }

    _applySkin() {
        const appearance = this._appearance;
        const light = appearance.follow_system_style && !this._systemPrefersDark();
        const id = light ? appearance.skin_light : appearance.skin;
        const wanted = String(id || DEFAULT_SKIN);
        const skins = this._loadSkins();
        if (!skins[wanted]) {
            log(`turnpad: skin "${wanted}" not found`);
            // Keep a skin that was shown already (its file may be in the
            // middle of being replaced); a new choice falls back to the default.
            if (this._skinId === wanted)
                return;
        }
        clearImageCache();
        this._skin = resolveSkin(skins, wanted);
        this._skinId = wanted;
    }

    _systemPrefersDark() {
        return this._interfaceSettings?.get_string('color-scheme') === 'prefer-dark';
    }

    // Follows the GNOME light/dark style switch for follow_system_style.
    _watchStyle() {
        try {
            this._interfaceSettings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });
        } catch (_) {
            this._interfaceSettings = null;
            return;
        }
        this._styleChangedId = this._interfaceSettings.connect('changed::color-scheme', () => {
            if (!this._appearance.follow_system_style)
                return;
            this._applySkin();
            this._updateIcons();
            this._layers?.invalidate();
            this._listKey = '';
            this._updateSubmenu();
        });
    }

    _unwatchStyle() {
        if (this._interfaceSettings && this._styleChangedId)
            this._interfaceSettings.disconnect(this._styleChangedId);
        this._interfaceSettings = null;
        this._styleChangedId = 0;
    }

    _readJson(path) {
        try {
            const [ok, bytes] = Gio.File.new_for_path(path).load_contents(null);
            return ok ? JSON.parse(new TextDecoder().decode(bytes)) : null;
        } catch (_) {
            return null;
        }
    }

    // Built-in skins ship as skins.json next to this file; every
    // skins/<id>.json in the config directory adds or replaces one.
    _loadSkins() {
        // "__dir" tells resolveSkin() where a skin's background picture is.
        const builtin = this._readJson(GLib.build_filenamev([this.path, SKINS_NAME])) || {};
        const skins = {};
        for (const [id, skin] of Object.entries(builtin))
            skins[id] = { ...skin, __dir: this.path };
        const directory = Gio.File.new_for_path(SKINS_DIR);
        let enumerator = null;
        try {
            enumerator = directory.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        } catch (_) {
            return skins;
        }
        for (let info = enumerator.next_file(null); info; info = enumerator.next_file(null)) {
            const name = info.get_name();
            if (!name.endsWith('.json'))
                continue;
            const skin = this._readJson(GLib.build_filenamev([SKINS_DIR, name]));
            if (skin && typeof skin === 'object')
                skins[name.slice(0, -5)] = { ...skin, __dir: SKINS_DIR };
        }
        enumerator.close(null);
        return skins;
    }

    _watchConfig() {
        // config.json and the skins directory; a skins directory created
        // later is picked up when it appears.
        this._configMonitors = [];
        this._watchDirectory(CONFIG_DIR, name => name === CONFIG_NAME || name === 'skins');
        this._watchDirectory(SKINS_DIR, name => name.endsWith('.json') || name.endsWith('.png'));
    }

    _watchDirectory(path, relevant) {
        let monitor;
        try {
            monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
        } catch (_) {
            return;
        }
        const id = monitor.connect('changed', (_monitor, file, other) => {
            const names = [file?.get_basename(), other?.get_basename()].filter(Boolean);
            if (names.some(relevant))
                this._scheduleConfigReload();
        });
        this._configMonitors.push([monitor, id]);
    }

    _scheduleConfigReload() {
        if (this._configReloadId)
            GLib.source_remove(this._configReloadId);
        this._configReloadId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 200, () => {
            this._configReloadId = 0;
            this._loadAppearance();
            this._applyGeometry();
            this._iconKey = '';
            this._updateIcons();
            this._listKey = '';
            this._updateSubmenu();
            return GLib.SOURCE_REMOVE;
        });
    }

    _unwatchConfig() {
        if (this._configReloadId) {
            GLib.source_remove(this._configReloadId);
            this._configReloadId = 0;
        }
        for (const [monitor, id] of this._configMonitors || []) {
            monitor.disconnect(id);
            monitor.cancel();
        }
        this._configMonitors = [];
    }

    // --- overlay actors -------------------------------------------------

    _createOverlay() {
        if (!Main.layoutManager?.uiGroup)
            return;

        this._overlay = new St.Widget({ visible: false, reactive: false, opacity: 0 });
        // Painting is split into layers that repaint only on change (layers.js).
        this._layers = new DialLayers(this._overlay, () => this._view());
        this._iconLayer = new St.Widget({ reactive: false });
        this._overlay.add_child(this._iconLayer);
        this._centerIcon = new St.Icon({ style_class: 'dialpad-center-icon', visible: false });
        this._overlay.add_child(this._centerIcon);

        // A group's list beside the dial. A child of the dial, so it shows,
        // fades and hides with it; it may lie outside the dial's box.
        this._list = new St.Widget({ visible: false, reactive: false, opacity: 0 });
        this._listArea = new St.DrawingArea();
        this._listArea.connect('repaint', () => {
            const cr = this._listArea.get_context();
            cr.save();
            cr.setOperator(Cairo.Operator.CLEAR);
            cr.paint();
            cr.restore();
            paintSubmenu(cr, this._view(), this._listSide, this._listScale);
            cr.$dispose();
        });
        this._listIcons = new St.Widget({ reactive: false });
        this._list.add_child(this._listArea);
        this._list.add_child(this._listIcons);
        this._overlay.add_child(this._list);
        Main.layoutManager.uiGroup.add_child(this._overlay);

        // A small label shown instead of the dial (visibility "pill").
        this._pill = new St.BoxLayout({ visible: false, reactive: false, opacity: 0 });
        this._pillIcon = new St.Icon({ y_align: Clutter.ActorAlign.CENTER });
        this._pillTitle = new St.Label({ y_align: Clutter.ActorAlign.CENTER });
        this._pillValue = new St.Label({ y_align: Clutter.ActorAlign.CENTER });
        for (const child of [this._pillIcon, this._pillTitle, this._pillValue])
            this._pill.add_child(child);
        Main.layoutManager.uiGroup.add_child(this._pill);
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => {
            this._monitor = null;
            this._pointer = null;
            this._applyGeometry();
        });
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._applyGeometry();
            return GLib.SOURCE_REMOVE;
        });
    }

    _destroyOverlay() {
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = 0;
        }
        if (!this._overlay)
            return;
        if (this._overlay.get_parent())
            Main.layoutManager.uiGroup.remove_child(this._overlay);
        this._overlay.remove_all_transitions();
        this._overlay.destroy();
        this._overlay = null;
        this._pill?.destroy();
        this._pill = null;
        this._pillKey = '';
        this._shownActor = null;
        this._monitor = null;
        this._pointer = null;
        this._layers?.destroy();
        this._layers = null;
        this._iconLayer = null;
        this._centerIcon = null;
        this._iconKey = '';
        this._list = null;
        this._listArea = null;
        this._listIcons = null;
        this._listKey = '';
        this._listIconKey = '';
        this._listOpen = false;
        this._shown = false;
    }

    // The monitor the dial shows on: the one it was shown on while it is on
    // screen, else the primary one until it is shown.
    _dialMonitor() {
        const monitors = Main.layoutManager.monitors;
        return monitors.includes(this._monitor) ? this._monitor : Main.layoutManager.primaryMonitor;
    }

    // The pointer on the dial's monitor, or null where it is unknown (the
    // dial was not shown since the monitors changed).
    _dialPointer() {
        return this._monitor && this._monitor === this._dialMonitor() ? this._pointer : null;
    }

    // A dial that is fully hidden comes back on the monitor with the
    // pointer, and with position "pointer" next to it. Fading in or out
    // counts as shown: the pointer is not read again then, so the dial
    // never moves under the finger, also when a profile changes its look.
    _followPointer() {
        if (this._overlay?.visible || this._pill?.visible)
            return;
        const monitor = Main.layoutManager.currentMonitor ?? Main.layoutManager.primaryMonitor;
        const [x, y] = global.get_pointer();
        const pointer = monitor ? [x - monitor.x, y - monitor.y] : null;
        const moved = monitor !== this._dialMonitor() ||
            (this._look().position === 'pointer' && String(pointer) !== String(this._dialPointer()));
        this._monitor = monitor;
        this._pointer = pointer;
        if (moved)
            this._applyGeometry();
    }

    _applyGeometry() {
        if (!this._overlay || !this._layers)
            return;
        const monitor = this._dialMonitor();
        if (!monitor)
            return;
        const scale = this._appearance.scale;
        const look = this._look();
        this._lookKey = JSON.stringify(look);
        const { x, y, width, height } = overlayPlacement(monitor, scale, look.position, look.custom,
            this._dialPointer(), look.pointerSide, look.pointerGap);
        // The overlay is only as large as the dial, so fading it composites
        // the dial and not the whole screen.
        this._overlay.set_size(width, height);
        this._layers.setSize(width, height, scale);
        this._iconLayer.set_size(width, width);
        this._overlay.set_position(monitor.x + x, monitor.y + y);
        this._dialOrigin = [0, 0];
        this._dialPlacement = { x, y, width, height };
        this._updateIcons();
        this._updatePill();
        this._listKey = '';
        this._updateSubmenu();
    }

    _look() {
        return effectiveLook(this._appearance, this._details.look);
    }

    // --- pill -------------------------------------------------------------

    _updatePill() {
        const pill = this._pill;
        const monitor = this._dialMonitor();
        if (!pill || !monitor || this._look().visibility !== 'pill')
            return;
        const scale = this._appearance.scale;
        const palette = this._skin.palette;
        const content = pillContent(this._view());
        // The driver signals every few degrees; restyle only on a change.
        const pointer = this._dialPointer();
        const key = JSON.stringify([content, scale, palette.surfaceTop, palette.rim, palette.text, this._lookKey,
            monitor.x, monitor.y, monitor.width, monitor.height, pointer]);
        if (key === this._pillKey)
            return;
        this._pillKey = key;
        const rgba = ([r, g, b, a = 1]) => `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
        const fontSize = Math.round(15 * scale);
        pill.set_style(
            `background-color: ${rgba(palette.surfaceTop)}; border: 1px solid ${rgba(palette.rim)}; ` +
            `border-radius: ${Math.round(24 * scale)}px; padding: ${Math.round(9 * scale)}px ${Math.round(18 * scale)}px; ` +
            `spacing: ${Math.round(10 * scale)}px;`);
        this._pillIcon.gicon = this._gicon(content.title, content.configured);
        this._pillIcon.icon_size = Math.round(PILL_ICON_SIZE * scale);
        this._pillIcon.set_style(this._cssColor(content.color));
        this._pillTitle.text = content.title;
        this._pillTitle.set_style(`color: ${rgba(palette.text)}; font-weight: bold; font-size: ${fontSize}px;`);
        this._pillValue.text = content.value;
        this._pillValue.visible = Boolean(content.value);
        this._pillValue.set_style(`color: ${rgba(content.color)}; font-weight: bold; font-size: ${fontSize}px;`);
        const [, width] = pill.get_preferred_width(-1);
        const [, height] = pill.get_preferred_height(width);
        const { x, y } = pillPlacement(monitor, scale, this._look(), width, height, pointer);
        pill.set_position(monitor.x + x, monitor.y + y);
    }

    // --- group list -----------------------------------------------------

    // Opens, moves, repaints or closes the list of the open group.
    _updateSubmenu() {
        const list = this._list;
        const monitor = this._dialMonitor();
        if (!list || !monitor || !this._dialPlacement)
            return;
        const view = this._view();
        const submenu = openSubmenu(view);
        if (!submenu) {
            this._closeSubmenu();
            return;
        }
        const scale = this._appearance.scale;
        const size = submenuSize(submenu.items.length, this._appearance.show_hints);
        const dial = this._dialPlacement;
        const placement = submenuPlacement(monitor, dial, scale, size, submenu.preferRight);
        const { x, y, width, height, side } = placement;
        this._listSide = side;
        this._listScale = placement.scale;
        // Placed relative to the dial, whose child it is.
        list.set_position(x - dial.x, y - dial.y);
        list.set_size(width, height);
        this._listArea.set_size(width, height);
        this._listIcons.set_size(width, height);
        const key = JSON.stringify([submenuKey(view, side), placement.scale, width, height]);
        if (key !== this._listKey) {
            this._listKey = key;
            this._listArea.queue_repaint();
        }
        this._updateListIcons(view);
        if (!this._listOpen) {
            this._listOpen = true;
            list.remove_all_transitions();
            list.visible = true;
            list.opacity = 0;
            list.translation_x = (side === 'right' ? -1 : 1) * Math.round(LIST_SLIDE * scale);
            list.ease({
                opacity: 255,
                translation_x: 0,
                duration: LIST_OPEN_MS,
                mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
            });
        }
    }

    _closeSubmenu() {
        const list = this._list;
        if (!list || !this._listOpen)
            return;
        this._listOpen = false;
        this._listKey = '';
        list.remove_all_transitions();
        list.ease({
            opacity: 0,
            translation_x: (this._listSide === 'right' ? -1 : 1) * Math.round(LIST_SLIDE * this._appearance.scale / 2),
            duration: LIST_CLOSE_MS,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (!this._listOpen)
                    list.visible = false;
            },
        });
    }

    _updateListIcons(view) {
        const layout = submenuLayout(view, this._listScale);
        const rows = layout ? layout.rows : [];
        const key = JSON.stringify(rows.map(({ title, configured, x, y, size }) => [title, configured, x, y, size]));
        if (key !== this._listIconKey) {
            this._listIconKey = key;
            this._listIcons.destroy_all_children();
            for (const row of rows) {
                const icon = new St.Icon({ gicon: this._gicon(row.title, row.configured), icon_size: row.size });
                icon.set_position(row.x, row.y);
                this._listIcons.add_child(icon);
            }
        }
        const icons = this._listIcons.get_children();
        rows.forEach((row, index) => icons[index]?.set_style(this._cssColor(row.color)));
    }

    // --- D-Bus ----------------------------------------------------------

    _connectDBus() {
        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, BUS_IFACE, 'OverlayState', BUS_PATH, null,
            Gio.DBusSignalFlags.NONE, this._onOverlayState.bind(this));
        this._detailsSignalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, BUS_IFACE, 'OverlayDetails', BUS_PATH, null,
            Gio.DBusSignalFlags.NONE, this._onOverlayDetails.bind(this));
    }

    _disconnectDBus() {
        for (const key of ['_signalId', '_detailsSignalId']) {
            if (this[key]) {
                Gio.DBus.session.signal_unsubscribe(this[key]);
                this[key] = 0;
            }
        }
    }

    _fetchDetails() {
        Gio.DBus.session.call(
            BUS_NAME, BUS_PATH, BUS_IFACE, 'GetOverlayDetails', null,
            new GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 1000, null,
            (connection, result) => {
                try {
                    const [json] = connection.call_finish(result).deepUnpack();
                    this._mergeDetails(json);
                } catch (_) {
                    // Older drivers have no details; the dial still works.
                }
            });
    }

    _connectFocus() {
        if (!global.display)
            return;
        this._focusHandlerId = global.display.connect('notify::focus-window', this._onFocusWindow.bind(this));
    }

    _disconnectFocus() {
        if (this._focusHandlerId) {
            global.display.disconnect(this._focusHandlerId);
            this._focusHandlerId = 0;
        }
        this._disconnectTitle();
    }

    _disconnectTitle() {
        if (this._titleWindow && this._titleHandlerId) {
            try {
                this._titleWindow.disconnect(this._titleHandlerId);
            } catch (e) {
                // The window is gone.
            }
        }
        this._titleWindow = null;
        this._titleHandlerId = 0;
    }

    _mergeDetails(json) {
        try {
            const details = JSON.parse(json);
            if (details && typeof details === 'object')
                Object.assign(this._details, details);
        } catch (_) {}
    }

    _onOverlayDetails(_conn, _sender, _path, _iface, _signal, params) {
        const [json] = params.deepUnpack();
        this._mergeDetails(json);
        if (JSON.stringify(this._look()) !== this._lookKey) {
            this._applyGeometry();
            // Another profile or function may show the pill instead of the
            // dial, or the other way round.
            if (this._shown)
                this._show(false);
        }
        this._layers?.update();
        this._updatePill();
        this._updateSubmenu();
    }

    _onOverlayState(_conn, _sender, _path, _iface, _signal, params) {
        const [enabled, title, titles, icons, value, valueAngleStart, unit, centerPressed, valueShowOnlyProgress, touching] = params.deepUnpack();
        // Drivers without the touching field keep the dial up while enabled.
        const legacyDriver = params.n_children() < 10;
        const wasEnabled = this._state.enabled;
        const wasTouching = this._state.touching;
        this._state.enabled = enabled;
        this._state.title = title || '';
        this._state.titles = titles || [];
        this._state.icons = icons || [];
        if (this._state.titles.some(label => Boolean(label))) {
            this._state.menuTitles = [...this._state.titles];
            this._state.menuIcons = [...this._state.icons];
        }
        this._state.value = value || 0;
        this._state.valueAngleStart = valueAngleStart || 0.0;
        this._state.unit = unit || '';
        this._state.centerPressed = centerPressed || false;
        this._state.valueShowOnlyProgress = valueShowOnlyProgress || false;
        this._state.touching = legacyDriver ? enabled : Boolean(touching);

        this._updateIcons();
        this._updateVisibility(wasEnabled, wasTouching);
        this._layers?.update();
        this._updatePill();
        this._updateSubmenu();
    }

    _onFocusWindow() {
        this._disconnectTitle();
        const focusWindow = global.display?.focus_window;
        const tracker = Shell.WindowTracker?.get_default();
        const app = focusWindow && tracker ? tracker.get_window_app(focusWindow) : null;
        const appId = app?.get_id() || '';
        this._sendActiveWindow(appId, focusWindow?.get_title() || '');
        if (focusWindow) {
            // The driver picks a profile context by the title, which changes
            // while the window stays (a document name, a browser tab).
            this._titleWindow = focusWindow;
            this._titleHandlerId = focusWindow.connect('notify::title', () =>
                this._sendActiveWindow(appId, focusWindow.get_title() || ''));
        }
    }

    _sendActiveWindow(appId, windowTitle) {
        Gio.DBus.session.call(
            BUS_NAME, BUS_PATH, BUS_IFACE, 'SetActiveWindow',
            new GLib.Variant('(ss)', [appId, windowTitle]), null,
            Gio.DBusCallFlags.NONE, -1, null,
            (connection, result) => {
                try {
                    connection.call_finish(result);
                } catch (error) {
                    // An older driver has only SetActiveApplication (no title).
                    try {
                        if (error.matches(Gio.DBusError, Gio.DBusError.UNKNOWN_METHOD))
                            this._sendActiveApplication(appId);
                    } catch (_) {
                        // Driver not running: the next focus change tries again.
                    }
                }
            });
    }

    _sendActiveApplication(appId) {
        Gio.DBus.session.call(
            BUS_NAME, BUS_PATH, BUS_IFACE, 'SetActiveApplication',
            new GLib.Variant('(s)', [appId]), null,
            Gio.DBusCallFlags.NONE, -1, null, null);
    }

    // --- visibility -----------------------------------------------------

    _updateVisibility(wasEnabled, wasTouching) {
        if (!this._overlay)
            return;
        if (!this._state.enabled) {
            this._hide();
            return;
        }
        if (this._state.touching) {
            this._cancelHide();
            this._show(!wasTouching);
            return;
        }
        this._cancelFade();
        // Not touching: flash the dial after activation, and let an
        // already visible dial linger so the final value can be read.
        if (!wasEnabled || this._shown) {
            this._show(false);
            this._scheduleHide();
        }
    }

    // The dial, or the pill in its place.
    _activeActor() {
        return this._look().visibility === 'pill' && this._pill ? this._pill : this._overlay;
    }

    // touchStarted: a new touch brings a faded dial back to full.
    _show(touchStarted) {
        this._followPointer();
        const actor = this._activeActor();
        if (this._shownActor && this._shownActor !== actor) {
            this._shownActor.remove_all_transitions();
            this._shownActor.opacity = 0;
            this._shownActor.visible = false;
        }
        const wasShown = this._shown && this._shownActor === actor;
        this._shown = true;
        this._shownActor = actor;
        if (wasShown && !touchStarted)
            return;
        actor.remove_all_transitions();
        actor.visible = true;
        actor.ease({
            opacity: 255,
            duration: FADE_IN_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
        this._scheduleFade();
    }

    _hide() {
        this._cancelHide();
        this._cancelFade();
        const actor = this._shownActor;
        if (!this._shown || !actor)
            return;
        this._shown = false;
        actor.remove_all_transitions();
        actor.ease({
            opacity: 0,
            duration: FADE_OUT_MS,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (!this._shown || this._shownActor !== actor)
                    actor.visible = false;
            },
        });
    }

    // Visibility "fade": after a moment of touching, fade so what lies
    // underneath shows through.
    _scheduleFade() {
        this._cancelFade();
        const look = this._look();
        if (look.visibility !== 'fade')
            return;
        this._fadeTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FADE_AFTER_MS, () => {
            this._fadeTimeoutId = 0;
            if (this._shown && this._state.touching && this._shownActor) {
                this._shownActor.ease({
                    opacity: Math.round(255 * look.fadeOpacity),
                    duration: FADE_SLOW_MS,
                    mode: Clutter.AnimationMode.EASE_IN_OUT_QUAD,
                });
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelFade() {
        if (this._fadeTimeoutId) {
            GLib.source_remove(this._fadeTimeoutId);
            this._fadeTimeoutId = 0;
        }
    }

    _scheduleHide() {
        this._cancelHide();
        this._hideTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, this._appearance.hide_delay_ms, () => {
            this._hideTimeoutId = 0;
            this._hide();
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelHide() {
        if (this._hideTimeoutId) {
            GLib.source_remove(this._hideTimeoutId);
            this._hideTimeoutId = 0;
        }
    }

    // --- icons ----------------------------------------------------------

    _view() {
        return {
            state: this._state,
            details: this._details,
            appearance: this._appearance,
            accent: this._accent,
            skin: this._skin,
        };
    }

    _gicon(title, configured) {
        // A configured icon wins when it exists: an absolute file path or an
        // icon name from the theme.
        if (configured) {
            if (configured.startsWith('/')) {
                if (GLib.file_test(configured, GLib.FileTest.EXISTS))
                    return Gio.FileIcon.new(Gio.File.new_for_path(configured));
            } else {
                return Gio.ThemedIcon.new(configured);
            }
        }
        return Gio.ThemedIcon.new(iconNameFor(title));
    }

    _cssColor(color) {
        const [r, g, b] = color.map(channel => Math.round(channel * 255));
        return `color: rgba(${r}, ${g}, ${b}, ${color[3] ?? 1});`;
    }

    _updateIcons() {
        if (!this._iconLayer)
            return;
        const { menu, menuVisible, center } = iconLayout(this._view());
        const key = JSON.stringify(menu.map(({ title, configured, x, y, size }) => [title, configured, x, y, size]));

        if (key !== this._iconKey) {
            this._iconKey = key;
            this._iconLayer.destroy_all_children();
            for (const entry of menu) {
                const icon = new St.Icon({
                    gicon: this._gicon(entry.title, entry.configured),
                    icon_size: entry.size,
                    style_class: 'dialpad-segment-icon',
                });
                icon.set_position(entry.x, entry.y);
                this._iconLayer.add_child(icon);
            }
        }

        this._iconLayer.visible = menuVisible;
        const icons = this._iconLayer.get_children();
        menu.forEach((entry, index) => icons[index]?.set_style(this._cssColor(entry.color)));
        this._updateCenterIcon(center);
    }

    _updateCenterIcon(center) {
        const icon = this._centerIcon;
        if (!icon)
            return;
        icon.visible = Boolean(center);
        if (!center || !this._dialOrigin)
            return;
        const [x, y] = this._dialOrigin;
        icon.gicon = this._gicon(center.title, center.configured);
        icon.icon_size = center.size;
        icon.set_style(this._cssColor(center.color));
        icon.set_position(x + center.x, y + center.y);
    }
}
