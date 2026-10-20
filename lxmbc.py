#!/usr/bin/env python3
"""linuXMouseButtonControl (lxmbc) - XMBC-style button chords, pan and button tricks for Linux mice.

Grabs physical mice through evdev, runs their events through a small state
machine (Engine) and re-emits the result through virtual devices (uinput):
a mouse, a keyboard (for shortcuts such as Ctrl+Tab) and, with
pan_mode = touchpad, a touchpad.

Pan modes:
  wheel     the pan is sent as (hi-res) wheel scrolling. Works everywhere, but apps
            animate wheel scrolling, so it can feel soft.
  touchpad  EXPERIMENTAL. The pan is sent as a two-finger drag on a virtual touchpad.
            Apps treat touchpad scrolling as precise (1:1, no smoothing), like
            dragging the page directly.

Default behaviour:
  Right then Left, or Left then Right within chord_ms, + move  -> pan (page follows hand)
    (the mouse may move up to chord_move_threshold while the second button goes down)
  Right tap               -> right click (sent on release, like Windows)
  Right hold + move       -> normal right-drag (PyMOL, 3D apps)
  Right + wheel           -> zoom (Ctrl+KP_Add / Ctrl+KP_Subtract)
  Middle tap              -> middle click (sent on release)
  Middle hold + move      -> normal middle-drag (VS Code box select, PDF pan)
  Middle + wheel          -> horizontal scroll
  Forward tap             -> forward
  Forward + wheel         -> previous / next tab (Ctrl+Shift+Tab / Ctrl+Tab)
  Back tap                -> back
  Back hold (back_hold_ms)-> Super (GNOME overview, the "task view")
  Everything else         -> passed through unchanged

The Engine is pure Python with no evdev dependency, so it can be unit tested
and later ported (e.g. into Input Remapper). The I/O layer lives in main().
"""

import argparse
import configparser
import os
import re
import select
import signal
import sys
import time
from dataclasses import dataclass, field

# --- Linux input event codes (linux/input-event-codes.h) ---------------------
EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_SIDE, BTN_EXTRA = 0x113, 0x114          # back, forward
BTN_TASK = 0x117                            # last standard mouse button

REL_X, REL_Y = 0x00, 0x01
REL_HWHEEL, REL_WHEEL = 0x06, 0x08
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C

KEY_TAB, KEY_LEFTCTRL, KEY_LEFTSHIFT = 15, 29, 42
KEY_KPMINUS, KEY_KPPLUS, KEY_LEFTMETA = 74, 78, 125

# Touchpad (pan_mode = touchpad)
EV_ABS = 0x03
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOOL_FINGER, BTN_TOUCH = 0x145, 0x14A
BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP = 0x14D, 0x14E
INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD = 0x00, 0x02
TP_RES = 40                     # units per mm (~1000 dpi, so 1 mouse count ~ 1 touchpad unit)
TP_MAX = 20000                  # 500 mm square: plenty of room before a re-grip
TP_CENTER = TP_MAX // 2
TP_GAP = 400                    # 10 mm between the two fingers
TP_MARGIN = 1000                # re-grip (lift and touch down again) this close to an edge
TP_HOLD_S = 0.06                # keep fingers still before lifting, so apps see no fling
TP_MIN_TOUCH_S = 0.25           # never lift sooner than this, so it can't count as a 2-finger tap

HI_RES_PER_NOTCH = 120

MOUSE, KBD, TP = "mouse", "keyboard", "touchpad"   # output targets
HELD = (BTN_RIGHT, BTN_MIDDLE, BTN_EXTRA)   # buttons held back until release/move/wheel
NAMES = {BTN_LEFT: "left", BTN_RIGHT: "right", BTN_MIDDLE: "middle",
         BTN_SIDE: "back", BTN_EXTRA: "forward"}


# --- Configuration -------------------------------------------------------------
# The ONLY place defaults are defined. /etc/lxmbc.conf holds just the settings you
# change; anything missing there falls back to these values.
CONFIG_FORMAT_MARKER = "lxmbc-config-format: 2"
DEFAULTS = {
    "devices": "",
    "chord_ms": "200",
    "chord_move_threshold": "25",
    "move_threshold": "4",
    "pan_mode": "wheel",
    "pan_speed": "2.25",
    "touchpad_speed": "1.0",
    "pan_follow_hand": "yes",
    "pan_move_cursor": "no",
    "back_hold_ms": "200",
}


@dataclass
class Config:
    devices: list
    chord_ms: float
    chord_move_threshold: int
    move_threshold: int
    pan_mode: str
    pan_speed: float
    touchpad_speed: float
    pan_follow_hand: bool
    pan_move_cursor: bool
    back_hold_ms: float
    debug: bool = False
    overrides: dict = field(default_factory=dict)   # settings changed in the config file
    unknown: list = field(default_factory=list)     # unrecognised names (typos)


def _pan_mode(value):
    value = value.strip().lower()
    if value not in ("wheel", "touchpad"):
        raise ValueError(f"pan_mode must be 'wheel' or 'touchpad', not {value!r}")
    return value


_CONVERTERS = {
    "devices": lambda v: [d.strip() for d in v.split(",") if d.strip()],
    "chord_ms": float, "chord_move_threshold": int, "move_threshold": int,
    "pan_mode": _pan_mode, "pan_speed": float, "touchpad_speed": float,
    "pan_follow_hand": lambda v: _bool(v), "pan_move_cursor": lambda v: _bool(v),
    "back_hold_ms": float,
}


def _bool(value):
    states = configparser.ConfigParser.BOOLEAN_STATES
    if value.strip().lower() not in states:
        raise ValueError(f"expected yes/no, not {value!r}")
    return states[value.strip().lower()]


def _read_file(path):
    """Settings actually written in a config file (no defaults mixed in)."""
    cp = configparser.ConfigParser()
    if path and os.path.exists(path):
        cp.read(path)
    return dict(cp["lxmbc"]) if cp.has_section("lxmbc") else {}


def load_config(path=None, debug=False):
    in_file = _read_file(path)
    cp = configparser.ConfigParser()
    cp.read_dict({"lxmbc": DEFAULTS})
    cp.read_dict({"lxmbc": {k: v for k, v in in_file.items() if k in DEFAULTS}})
    s = cp["lxmbc"]
    return Config(
        devices=[d.strip() for d in s.get("devices").split(",") if d.strip()],
        chord_ms=s.getfloat("chord_ms"),
        chord_move_threshold=s.getint("chord_move_threshold"),
        move_threshold=s.getint("move_threshold"),
        pan_mode=_pan_mode(s.get("pan_mode")),
        pan_speed=s.getfloat("pan_speed"),
        touchpad_speed=s.getfloat("touchpad_speed"),
        pan_follow_hand=s.getboolean("pan_follow_hand"),
        pan_move_cursor=s.getboolean("pan_move_cursor"),
        back_hold_ms=s.getfloat("back_hold_ms"),
        debug=debug,
        overrides={k: v for k, v in in_file.items() if k in DEFAULTS},
        unknown=sorted(k for k in in_file if k not in DEFAULTS),
    )


def upgrade_config(old_path, example_text):
    """Convert an old-style config (every setting written out) into the new style:
    the example file with only the settings that differ from DEFAULTS uncommented.
    Returns (new_text, kept) where kept maps setting -> value."""
    old = _read_file(old_path)
    kept = {}
    for key, value in old.items():
        if key in DEFAULTS and _CONVERTERS[key](value) != _CONVERTERS[key](DEFAULTS[key]):
            kept[key] = value
    text = example_text
    for key, value in kept.items():
        text, n = re.subn(rf"^#\s*{re.escape(key)}\s*=.*$", f"{key} = {value}", text,
                          count=1, flags=re.M)
        if not n:
            raise ValueError(f"{key} not found in the example config")
    return text, kept


# --- State machine -------------------------------------------------------------
class Engine:
    """Per-mouse state machine.

    feed()/tick() take input events and return a list of output events, each
    (target, type, code, value) with target MOUSE or KBD. An EV_SYN entry means
    "send a SYN_REPORT on that target now"; the I/O layer also syncs at the end
    of each input frame.
    """

    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log or (lambda msg: None)
        self.left = None            # None | "pending" | "down"
        self.left_deadline = 0.0
        self.left_move = 0
        self.left_buf = [0, 0]      # motion held back while left is pending (x, y)
        self.held = {c: None for c in HELD}     # None | "pending" | "used" | "down"
        self.held_move = {c: 0 for c in HELD}
        self.side = None            # None | "pending" | "used"
        self.side_deadline = 0.0
        self.pan = False
        self.swallow = set()        # buttons whose next release must be eaten
        self._rem = [0.0, 0.0]      # fractional hi-res scroll remainder (v, h)
        self._acc = [0, 0]          # hi-res units accumulated toward a legacy notch
        self.now = 0.0
        # virtual touchpad (pan_mode = touchpad)
        self.touching = False
        self.tp_pos = [TP_CENTER, TP_CENTER]   # first finger (x, y); second is TP_GAP to the right
        self.tp_rem = [0.0, 0.0]
        self.touch_time = 0.0
        self.lift_deadline = None
        self.tid = 0

    # -- output helpers --
    @staticmethod
    def _btn(code, value):
        return [(MOUSE, EV_KEY, code, value)]

    @staticmethod
    def _click(code):
        return [(MOUSE, EV_KEY, code, 1), (MOUSE, EV_SYN, SYN_REPORT, 0),
                (MOUSE, EV_KEY, code, 0), (MOUSE, EV_SYN, SYN_REPORT, 0)]

    @staticmethod
    def _combo(*keys):
        out = [(KBD, EV_KEY, k, 1) for k in keys]
        out.append((KBD, EV_SYN, SYN_REPORT, 0))
        out += [(KBD, EV_KEY, k, 0) for k in reversed(keys)]
        out.append((KBD, EV_SYN, SYN_REPORT, 0))
        return out

    @staticmethod
    def _forward(etype, code, value):
        if etype == EV_REL:
            return [(MOUSE, etype, code, value)]
        if etype == EV_KEY:
            if BTN_LEFT <= code <= BTN_TASK:
                return [(MOUSE, etype, code, value)]
            if code < 0x100:
                return [(KBD, etype, code, value)]
        return []

    # -- commits: give up holding a button back and send its press --
    def _flush_left_buf(self):
        out = []
        for code, v in ((REL_X, self.left_buf[0]), (REL_Y, self.left_buf[1])):
            if v:
                out.append((MOUSE, EV_REL, code, v))
        self.left_buf = [0, 0]
        return out

    def _commit_left(self):
        """Send the held-back left press, then the motion buffered meanwhile, so a
        drag (text selection, file drag) starts exactly where the button went down."""
        if self.left == "pending":
            self.left = "down"
            self.log("left: press sent")
            return (self._btn(BTN_LEFT, 1) + [(MOUSE, EV_SYN, SYN_REPORT, 0)]
                    + self._flush_left_buf())
        return []

    def _commit_held(self, code):
        if self.held[code] == "pending":
            self.held[code] = "down"
            self.log(f"{NAMES[code]}: press sent (drag)")
            return self._btn(code, 1)
        return []

    def _commit_others(self, except_code=None):
        out = self._commit_left()
        for c in HELD:
            if c != except_code:
                out += self._commit_held(c)
        return out

    # -- public API --
    def feed(self, etype, code, value, now):
        self.now = now
        if etype == EV_KEY:
            if value == 2:          # autorepeat, not meaningful for mouse buttons
                return []
            return self._press(code, now) if value == 1 else self._release(code)
        if etype == EV_REL:
            if code in (REL_X, REL_Y):
                return self._motion(code, value)
            if code in (REL_WHEEL, REL_WHEEL_HI_RES):
                return self._wheel(code, value)
            return self._forward(etype, code, value)
        return []                   # EV_MSC (scan codes) etc. are not forwarded

    def tick(self, now):
        self.now = now
        out = []
        if self.touching and self.lift_deadline is not None and now >= self.lift_deadline:
            out += self._tp_up()
        if self.left == "pending" and now >= self.left_deadline:
            out += self._commit_left()
        if self.side == "pending" and now >= self.side_deadline:
            self.side = "used"
            self.log("back: held -> overview (Super)")
            out += self._combo(KEY_LEFTMETA)
        return out

    def deadline(self):
        ds = []
        if self.left == "pending" and self.left_deadline != float("inf"):
            ds.append(self.left_deadline)
        if self.side == "pending":
            ds.append(self.side_deadline)
        if self.touching and self.lift_deadline is not None:
            ds.append(self.lift_deadline)
        return min(ds) if ds else None

    def release_all(self):
        """Release anything we told the system is pressed (device unplugged)."""
        out = []
        if self.left == "down":
            out += self._btn(BTN_LEFT, 0)
        for c, st in self.held.items():
            if st == "down":
                out += self._btn(c, 0)
        if self.touching:
            out += self._tp_up()
        self.__init__(self.cfg, self.log)
        return out

    # -- buttons --
    def _start_pan(self):
        self.left = None
        self.left_buf = [0, 0]
        self.held[BTN_RIGHT] = None
        self.pan = True
        self.swallow.update((BTN_LEFT, BTN_RIGHT))
        self._rem = [0.0, 0.0]
        self._acc = [0, 0]
        self.tp_rem = [0.0, 0.0]
        if self.touching:               # fingers still down from the previous pan: carry on
            self.lift_deadline = None
        self.log("pan: start")
        return []

    def _press(self, code, now):
        if code == BTN_LEFT:
            if self.pan:
                self.swallow.add(code)
                return []
            if self.held[BTN_RIGHT] == "pending":
                return self._start_pan()
            out = self._commit_others()
            self.left = "pending"
            # chord_ms = 0 means no timeout: wait for release or movement only
            self.left_deadline = (now + self.cfg.chord_ms / 1000.0 if self.cfg.chord_ms > 0
                                  else float("inf"))
            self.left_move = 0
            self.left_buf = [0, 0]
            self.log("left: pending")
            return out

        if code == BTN_RIGHT:
            if self.left == "pending":
                return self._start_pan()
            if self.pan:
                self.swallow.add(code)
                return []

        if code in HELD:
            out = self._commit_others(except_code=code)
            self.held[code] = "pending"
            self.held_move[code] = 0
            self.log(f"{NAMES[code]}: pending")
            return out

        if code == BTN_SIDE:
            out = self._commit_left()
            self.side = "pending"
            self.side_deadline = now + self.cfg.back_hold_ms / 1000.0
            self.log("back: pending")
            return out

        return self._commit_others() + self._forward(EV_KEY, code, 1)

    def _release(self, code):
        if code in self.swallow:
            self.swallow.discard(code)
            if self.pan and code in (BTN_LEFT, BTN_RIGHT):
                self.pan = False
                self.log(f"pan: end ({NAMES[code]} released)")
                if self.touching:
                    self.lift_deadline = max(self.now + TP_HOLD_S,
                                             self.touch_time + TP_MIN_TOUCH_S)
            return []

        if code == BTN_LEFT:
            st, self.left = self.left, None
            if st == "pending":
                self.log("left: click")
                return self._click(BTN_LEFT) + self._flush_left_buf()
            self.log("left: release")
            return self._btn(BTN_LEFT, 0)

        if code in HELD:
            st, self.held[code] = self.held[code], None
            if st == "pending":
                self.log(f"{NAMES[code]}: click")
                return self._click(code)
            if st == "down":
                self.log(f"{NAMES[code]}: release (drag end)")
                return self._btn(code, 0)
            self.log(f"{NAMES[code]}: release (wheel action, no click)")
            return []               # "used": a wheel action happened, eat the click

        if code == BTN_SIDE:
            st, self.side = self.side, None
            if st == "pending":
                self.log("back: click")
                return self._click(BTN_SIDE)
            return []

        return self._forward(EV_KEY, code, 0)

    # -- motion --
    def _motion(self, code, value):
        if self.pan:
            out = (self._tp_move(code, value) if self.cfg.pan_mode == "touchpad"
                   else self._pan(code, value))
            if self.cfg.pan_move_cursor:
                out.append((MOUSE, EV_REL, code, value))
            return out
        out = []
        # Right gets the generous chord tolerance (your hand moves while reaching for Left);
        # Middle keeps a tight threshold so VS Code box select starts right away.
        # Forward stays held back while moving.
        for c, thr in ((BTN_RIGHT, self.cfg.chord_move_threshold),
                       (BTN_MIDDLE, self.cfg.move_threshold)):
            if self.held[c] == "pending":
                self.held_move[c] += abs(value)
                if self.held_move[c] > thr:
                    out += self._commit_held(c)
        if self.left == "pending":
            # Hold the motion back (cursor pauses briefly) so a later left-drag
            # still starts at the press point; released on commit or click.
            self.left_buf[0 if code == REL_X else 1] += value
            self.left_move += abs(value)
            if self.left_move > self.cfg.chord_move_threshold:
                out += self._commit_left()
            return out
        out.append((MOUSE, EV_REL, code, value))
        return out

    def _pan(self, code, value):
        sign = 1 if self.cfg.pan_follow_hand else -1
        if code == REL_Y:
            # Mouse down (+y) with follow-hand = content moves down = scroll up (+wheel)
            idx, hi, lo, delta = 0, REL_WHEEL_HI_RES, REL_WHEEL, sign * value
        else:
            # Mouse right (+x) with follow-hand = content moves right = scroll left (-hwheel)
            idx, hi, lo, delta = 1, REL_HWHEEL_HI_RES, REL_HWHEEL, -sign * value
        self._rem[idx] += delta * self.cfg.pan_speed
        units = int(self._rem[idx])         # truncates toward zero
        if units == 0:
            return []
        self._rem[idx] -= units
        out = [(MOUSE, EV_REL, hi, units)]
        self._acc[idx] += units
        notches = int(self._acc[idx] / HI_RES_PER_NOTCH)
        if notches:
            self._acc[idx] -= notches * HI_RES_PER_NOTCH
            out.append((MOUSE, EV_REL, lo, notches))
        return out

    # -- virtual touchpad: the pan as a two-finger drag --
    def _tp_frame(self):
        x, y = self.tp_pos
        return [(TP, EV_ABS, ABS_MT_SLOT, 0),
                (TP, EV_ABS, ABS_MT_POSITION_X, x), (TP, EV_ABS, ABS_MT_POSITION_Y, y),
                (TP, EV_ABS, ABS_MT_SLOT, 1),
                (TP, EV_ABS, ABS_MT_POSITION_X, x + TP_GAP), (TP, EV_ABS, ABS_MT_POSITION_Y, y),
                (TP, EV_ABS, ABS_X, x), (TP, EV_ABS, ABS_Y, y)]

    def _tp_down(self):
        self.tid = (self.tid + 2) % 65534
        self.tp_pos = [TP_CENTER, TP_CENTER]
        self.touching = True
        self.touch_time = self.now
        self.lift_deadline = None
        self.log("pan: two fingers down on virtual touchpad")
        return ([(TP, EV_ABS, ABS_MT_SLOT, 0), (TP, EV_ABS, ABS_MT_TRACKING_ID, self.tid),
                 (TP, EV_ABS, ABS_MT_SLOT, 1), (TP, EV_ABS, ABS_MT_TRACKING_ID, self.tid + 1)]
                + self._tp_frame()
                + [(TP, EV_KEY, BTN_TOUCH, 1), (TP, EV_KEY, BTN_TOOL_DOUBLETAP, 1),
                   (TP, EV_SYN, SYN_REPORT, 0)])

    def _tp_up(self):
        self.touching = False
        self.lift_deadline = None
        self.log("pan: fingers lifted")
        return [(TP, EV_ABS, ABS_MT_SLOT, 0), (TP, EV_ABS, ABS_MT_TRACKING_ID, -1),
                (TP, EV_ABS, ABS_MT_SLOT, 1), (TP, EV_ABS, ABS_MT_TRACKING_ID, -1),
                (TP, EV_KEY, BTN_TOUCH, 0), (TP, EV_KEY, BTN_TOOL_DOUBLETAP, 0),
                (TP, EV_SYN, SYN_REPORT, 0)]

    def _tp_move(self, code, value):
        out = []
        if not self.touching:
            out += self._tp_down()      # touch down lazily, on the first movement
        self.lift_deadline = None
        # Fingers follow the mouse. With GNOME's touchpad natural scrolling (on by
        # default) that makes the page follow your hand.
        sign = 1 if self.cfg.pan_follow_hand else -1
        idx = 0 if code == REL_X else 1
        self.tp_rem[idx] += sign * value * self.cfg.touchpad_speed
        units = int(self.tp_rem[idx])
        if units == 0:
            return out
        self.tp_rem[idx] -= units
        new = self.tp_pos[idx] + units
        hi = TP_MAX - TP_MARGIN - (TP_GAP if idx == 0 else 0)
        if not TP_MARGIN <= new <= hi:  # near an edge: lift and grab again at the centre
            out += self._tp_up() + self._tp_down()
            new = self.tp_pos[idx] + units
        self.tp_pos[idx] = new
        return out + self._tp_frame() + [(TP, EV_SYN, SYN_REPORT, 0)]

    # -- wheel --
    def _wheel(self, code, value):
        legacy = code == REL_WHEEL
        for btn, kind in ((BTN_RIGHT, "zoom"), (BTN_EXTRA, "tabs"), (BTN_MIDDLE, "hscroll")):
            if self.held[btn] not in ("pending", "used"):
                continue
            self.held[btn] = "used"
            if kind == "hscroll":
                # wheel up -> scroll left, wheel down -> scroll right
                return [(MOUSE, EV_REL, REL_HWHEEL if legacy else REL_HWHEEL_HI_RES, -value)]
            if not legacy:          # shortcuts act on whole notches only
                return []
            if kind == "zoom":
                keys = (KEY_LEFTCTRL, KEY_KPPLUS) if value > 0 else (KEY_LEFTCTRL, KEY_KPMINUS)
            else:
                keys = ((KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_TAB) if value > 0
                        else (KEY_LEFTCTRL, KEY_TAB))
            self.log(f"{NAMES[btn]} + wheel {'up' if value > 0 else 'down'} -> {kind}")
            out = []
            for _ in range(abs(value)):
                out += self._combo(*keys)
            return out
        return self._commit_left() + [(MOUSE, EV_REL, code, value)]


# --- I/O layer -----------------------------------------------------------------
VIRTUAL_PREFIXES = ("lxmbc", "mousechord", "input-remapper")


def log(msg):
    print(msg, flush=True)


def is_mouse(dev):
    caps = dev.capabilities()
    rel = caps.get(EV_REL, [])
    return REL_X in rel and REL_Y in rel and BTN_LEFT in caps.get(EV_KEY, [])


def wanted(dev, cfg):
    name = dev.name or ""
    if name.startswith(VIRTUAL_PREFIXES) or not is_mouse(dev):
        return False
    return not cfg.devices or any(d.lower() in name.lower() for d in cfg.devices)


def main():
    ap = argparse.ArgumentParser(description="XMBC-style chords and pan for Linux mice.")
    ap.add_argument("--config", default="/etc/lxmbc.conf")
    ap.add_argument("--debug", action="store_true", help="log every state change")
    ap.add_argument("--list", action="store_true", help="list input devices and exit")
    ap.add_argument("--upgrade-config", metavar="EXAMPLE", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.upgrade_config:         # used by install.sh, once, on old-style configs
        with open(args.upgrade_config) as f:
            example = f.read()
        with open(args.config) as f:
            if CONFIG_FORMAT_MARKER in f.read():
                print(f"{args.config} is already in the current format, nothing to do.")
                return
        text, kept = upgrade_config(args.config, example)
        os.replace(args.config, args.config + ".bak")
        with open(args.config, "w") as f:
            f.write(text)
        os.chmod(args.config, 0o644)
        changed = ", ".join(f"{k} = {v}" for k, v in kept.items()) or "none (all defaults)"
        print(f"Converted {args.config} to the new format (old file: {args.config}.bak).")
        print(f"Your settings that differ from the defaults: {changed}")
        return

    cfg = load_config(args.config, args.debug)

    try:
        import evdev
    except ImportError:
        sys.exit("python3-evdev is missing: sudo apt install -y python3-evdev")

    if args.list:
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            tag = "MOUSE (would grab)" if wanted(dev, cfg) else ("mouse (filtered)" if is_mouse(dev) else "")
            print(f"{path:20} {dev.name:50} {tag}")
            dev.close()
        return

    mouse_ui = evdev.UInput(
        {EV_KEY: list(range(BTN_LEFT, BTN_TASK + 1)),
         EV_REL: [REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES]},
        name="lxmbc virtual mouse")
    kbd_ui = evdev.UInput({EV_KEY: list(range(1, 249))}, name="lxmbc virtual keyboard")
    outputs = {MOUSE: mouse_ui, KBD: kbd_ui}
    if cfg.pan_mode == "touchpad":
        ai = evdev.AbsInfo
        outputs[TP] = evdev.UInput(
            {EV_KEY: [BTN_LEFT, BTN_TOOL_FINGER, BTN_TOUCH, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP],
             EV_ABS: [(ABS_X, ai(TP_CENTER, 0, TP_MAX, 0, 0, TP_RES)),
                      (ABS_Y, ai(TP_CENTER, 0, TP_MAX, 0, 0, TP_RES)),
                      (ABS_MT_SLOT, ai(0, 0, 1, 0, 0, 0)),
                      (ABS_MT_POSITION_X, ai(TP_CENTER, 0, TP_MAX, 0, 0, TP_RES)),
                      (ABS_MT_POSITION_Y, ai(TP_CENTER, 0, TP_MAX, 0, 0, TP_RES)),
                      (ABS_MT_TRACKING_ID, ai(0, 0, 65535, 0, 0, 0))]},
            name="lxmbc virtual touchpad",
            input_props=[INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD])
    dirty = {t: False for t in outputs}

    def emit(events):
        for target, etype, code, value in events:
            if etype == EV_SYN:
                outputs[target].syn()
                dirty[target] = False
            else:
                outputs[target].write(etype, code, value)
                dirty[target] = True

    def flush():
        for target, is_dirty in dirty.items():
            if is_dirty:
                outputs[target].syn()
                dirty[target] = False

    devices = {}        # fd -> (InputDevice, Engine)
    rejected = set()    # paths already checked and not wanted

    def scan():
        present = set(evdev.list_devices())
        rejected.intersection_update(present)
        known = {dev.path for dev, _ in devices.values()}
        for path in present - known - rejected:
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            if not wanted(dev, cfg):
                rejected.add(path)
                dev.close()
                continue
            try:
                dev.grab()
            except OSError as e:
                log(f"cannot grab {dev.name} ({path}): {e}")
                rejected.add(path)
                dev.close()
                continue
            name = dev.name
            eng_log = (lambda m, n=name: log(f"[{n}] {m}")) if cfg.debug else None
            devices[dev.fd] = (dev, Engine(cfg, eng_log))
            log(f"grabbed {name} ({path})")

    def drop(fd):
        dev, eng = devices.pop(fd)
        emit(eng.release_all())
        flush()
        try:
            dev.close()
        except OSError:
            pass
        log(f"released {dev.name}")

    def on_term(*_):
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    if not os.path.exists(args.config):
        log(f"no config file at {args.config}, using defaults")
    else:
        changed = ", ".join(f"{k} = {v}" for k, v in cfg.overrides.items()) or "none"
        log(f"config {args.config}: changed from defaults: {changed}")
    for key in cfg.unknown:
        log(f"WARNING: unknown setting {key!r} in {args.config} (typo?), ignored")
    log(f"lxmbc running: chord_ms={cfg.chord_ms} chord_move_threshold={cfg.chord_move_threshold} "
        f"move_threshold={cfg.move_threshold} "
        f"pan_mode={cfg.pan_mode} pan_speed={cfg.pan_speed} touchpad_speed={cfg.touchpad_speed} "
        f"pan_follow_hand={cfg.pan_follow_hand} pan_move_cursor={cfg.pan_move_cursor} "
        f"back_hold_ms={cfg.back_hold_ms} devices={cfg.devices or 'all mice'}")

    next_scan = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_scan:
                scan()
                next_scan = now + 2.0
            deadlines = [d for d in (e.deadline() for _, e in devices.values()) if d is not None]
            timeout = max(0.0, min(deadlines + [next_scan]) - now)
            ready, _, _ = select.select(list(devices), [], [], timeout)
            for fd in ready:
                dev, eng = devices[fd]
                try:
                    for ev in dev.read():
                        if ev.type == EV_SYN:
                            if ev.code == SYN_REPORT:
                                flush()
                            continue
                        emit(eng.feed(ev.type, ev.code, ev.value, time.monotonic()))
                except BlockingIOError:
                    pass
                except OSError:
                    drop(fd)
            now = time.monotonic()
            for _, eng in list(devices.values()):
                out = eng.tick(now)
                if out:
                    emit(out)
            flush()
    except KeyboardInterrupt:
        pass
    finally:
        for fd in list(devices):
            drop(fd)
        for ui in outputs.values():
            ui.close()
        log("lxmbc stopped, mice released")


if __name__ == "__main__":
    main()