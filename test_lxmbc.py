"""Unit tests for the lxmbc state machine. Run: python3 test_lxmbc.py"""
import importlib.util
import os
import re
import tempfile
import unittest

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lxmbc", os.path.join(_here, "lxmbc.py"))
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

M, K = mc.MOUSE, mc.KBD
PRESS, RELEASE = 1, 0


def nosyn(events):
    return [e for e in events if e[1] != mc.EV_SYN]


class EngineTest(unittest.TestCase):
    def setUp(self):
        # Pin every setting the tests rely on, so changing the defaults in lxmbc.py
        # or lxmbc.conf never breaks the tests.
        self.cfg = mc.load_config(None)
        self.cfg.chord_ms = 120
        self.cfg.chord_move_threshold = 25
        self.cfg.move_threshold = 4
        self.cfg.pan_speed = 2.25
        self.cfg.pan_follow_hand = True
        self.cfg.back_hold_ms = 200
        self.cfg.pan_mode = "wheel"
        self.cfg.touchpad_speed = 1.0
        self.cfg.pan_move_cursor = False
        self.e = mc.Engine(self.cfg)
        self.t = 0.0

    def key(self, code, value, dt=0.0):
        self.t += dt
        return nosyn(self.e.feed(mc.EV_KEY, code, value, self.t))

    def rel(self, code, value):
        return nosyn(self.e.feed(mc.EV_REL, code, value, self.t))

    def tick(self, dt):
        self.t += dt
        return nosyn(self.e.tick(self.t))

    # --- left ---
    def test_left_quick_click(self):
        self.assertEqual(self.key(mc.BTN_LEFT, PRESS), [])
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE, 0.02),
                         [(M, mc.EV_KEY, mc.BTN_LEFT, 1), (M, mc.EV_KEY, mc.BTN_LEFT, 0)])

    def test_left_hold_commits_after_window(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.assertEqual(self.tick(0.1), [])                    # still inside 120 ms
        self.assertEqual(self.tick(0.05), [(M, mc.EV_KEY, mc.BTN_LEFT, 1)])
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE, 0.1), [(M, mc.EV_KEY, mc.BTN_LEFT, 0)])

    def test_left_drag_commits_on_motion(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.assertEqual(self.rel(mc.REL_X, 10), [])            # buffered, cursor pauses
        self.assertEqual(self.rel(mc.REL_Y, 5), [])
        # crossing chord_move_threshold (25): press first, at the original point, then the motion
        self.assertEqual(self.rel(mc.REL_X, 15),
                         [(M, mc.EV_KEY, mc.BTN_LEFT, 1),
                          (M, mc.EV_REL, mc.REL_X, 25), (M, mc.EV_REL, mc.REL_Y, 5)])
        self.assertEqual(self.rel(mc.REL_X, 3), [(M, mc.EV_REL, mc.REL_X, 3)])

    def test_left_click_after_small_move_clicks_at_press_point(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.rel(mc.REL_X, 6)
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE, 0.05),
                         [(M, mc.EV_KEY, mc.BTN_LEFT, 1), (M, mc.EV_KEY, mc.BTN_LEFT, 0),
                          (M, mc.EV_REL, mc.REL_X, 6)])

    def test_left_timeout_flushes_buffered_motion(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.rel(mc.REL_Y, -7)
        self.assertEqual(self.tick(0.13),
                         [(M, mc.EV_KEY, mc.BTN_LEFT, 1), (M, mc.EV_REL, mc.REL_Y, -7)])

    def test_chord_ms_zero_waits_for_release(self):
        self.e.cfg.chord_ms = 0
        self.key(mc.BTN_LEFT, PRESS)
        self.assertIsNone(self.e.deadline())
        self.assertEqual(self.tick(5.0), [])                    # held still for 5 s: nothing sent
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE),
                         [(M, mc.EV_KEY, mc.BTN_LEFT, 1), (M, mc.EV_KEY, mc.BTN_LEFT, 0)])

    # --- pan ---
    def _pan_frames(self):
        down = self.rel(mc.REL_Y, 100)
        right = self.rel(mc.REL_X, 100)
        return down, right

    def test_pan_left_then_right(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.assertEqual(self.key(mc.BTN_RIGHT, PRESS, 0.01), [])
        down, right = self._pan_frames()
        # mouse down -> content follows -> scroll up (positive wheel)
        self.assertIn((M, mc.EV_REL, mc.REL_WHEEL_HI_RES, 225), down)
        self.assertIn((M, mc.EV_REL, mc.REL_WHEEL, 1), down)
        # mouse right -> scroll left (negative hwheel)
        self.assertIn((M, mc.EV_REL, mc.REL_HWHEEL_HI_RES, -225), right)
        self.assertFalse(any(e[2] in (mc.REL_X, mc.REL_Y) for e in down + right))
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE), [])
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE), [])
        self.assertFalse(self.e.pan)

    def test_pan_right_then_left_after_hand_wobble(self):
        # From the log: right pressed, hand moved a bit, then left -> must still pan
        self.key(mc.BTN_RIGHT, PRESS)
        self.rel(mc.REL_X, 12)
        self.rel(mc.REL_Y, 8)
        self.assertEqual(self.key(mc.BTN_LEFT, PRESS, 0.2), [])
        self.assertTrue(self.e.pan)

    def test_pan_left_then_right_after_80ms_and_wobble(self):
        # From the log: left pressed, right landed later than 40 ms -> must still pan
        self.key(mc.BTN_LEFT, PRESS)
        self.rel(mc.REL_X, 10)
        self.assertEqual(self.tick(0.08), [])
        self.assertEqual(self.key(mc.BTN_RIGHT, PRESS), [])
        self.assertTrue(self.e.pan)
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE), [])   # buffered motion discarded, no click

    def test_pan_right_then_left(self):
        self.key(mc.BTN_RIGHT, PRESS)
        self.assertEqual(self.key(mc.BTN_LEFT, PRESS, 0.3), [])
        self.assertTrue(self.e.pan)
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE), [])
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE), [])

    def test_no_chord_after_window(self):
        self.key(mc.BTN_LEFT, PRESS)
        self.tick(0.15)                     # left press already sent
        self.key(mc.BTN_RIGHT, PRESS)
        self.assertFalse(self.e.pan)

    def test_pan_scrollbar_direction(self):
        self.e.cfg.pan_follow_hand = False
        self.key(mc.BTN_RIGHT, PRESS)
        self.key(mc.BTN_LEFT, PRESS)
        self.assertIn((M, mc.EV_REL, mc.REL_WHEEL_HI_RES, -225), self.rel(mc.REL_Y, 100))

    # --- right ---
    def test_right_tap_clicks_on_release(self):
        self.assertEqual(self.key(mc.BTN_RIGHT, PRESS), [])
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE, 0.5),
                         [(M, mc.EV_KEY, mc.BTN_RIGHT, 1), (M, mc.EV_KEY, mc.BTN_RIGHT, 0)])

    def test_right_drag(self):
        self.key(mc.BTN_RIGHT, PRESS)
        self.assertEqual(self.rel(mc.REL_X, 20), [(M, mc.EV_REL, mc.REL_X, 20)])
        self.assertEqual(self.rel(mc.REL_X, 6),
                         [(M, mc.EV_KEY, mc.BTN_RIGHT, 1), (M, mc.EV_REL, mc.REL_X, 6)])
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE), [(M, mc.EV_KEY, mc.BTN_RIGHT, 0)])

    def test_right_wheel_zoom_no_menu(self):
        self.key(mc.BTN_RIGHT, PRESS)
        self.assertEqual(self.rel(mc.REL_WHEEL_HI_RES, 120), [])
        self.assertEqual(self.rel(mc.REL_WHEEL, 1),
                         [(K, mc.EV_KEY, mc.KEY_LEFTCTRL, 1), (K, mc.EV_KEY, mc.KEY_KPPLUS, 1),
                          (K, mc.EV_KEY, mc.KEY_KPPLUS, 0), (K, mc.EV_KEY, mc.KEY_LEFTCTRL, 0)])
        out = self.rel(mc.REL_WHEEL, -1)
        self.assertIn((K, mc.EV_KEY, mc.KEY_KPMINUS, 1), out)
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE), [])

    # --- middle ---
    def test_middle_wheel_horizontal(self):
        self.key(mc.BTN_MIDDLE, PRESS)
        self.assertEqual(self.rel(mc.REL_WHEEL, -1), [(M, mc.EV_REL, mc.REL_HWHEEL, 1)])
        self.assertEqual(self.rel(mc.REL_WHEEL_HI_RES, 120), [(M, mc.EV_REL, mc.REL_HWHEEL_HI_RES, -120)])
        self.assertEqual(self.key(mc.BTN_MIDDLE, RELEASE), [])

    def test_middle_drag_for_box_select(self):
        self.key(mc.BTN_MIDDLE, PRESS)
        out = self.rel(mc.REL_Y, 8)
        self.assertEqual(out, [(M, mc.EV_KEY, mc.BTN_MIDDLE, 1), (M, mc.EV_REL, mc.REL_Y, 8)])
        self.assertEqual(self.key(mc.BTN_MIDDLE, RELEASE), [(M, mc.EV_KEY, mc.BTN_MIDDLE, 0)])

    def test_middle_tap(self):
        self.key(mc.BTN_MIDDLE, PRESS)
        self.assertEqual(self.key(mc.BTN_MIDDLE, RELEASE),
                         [(M, mc.EV_KEY, mc.BTN_MIDDLE, 1), (M, mc.EV_KEY, mc.BTN_MIDDLE, 0)])

    # --- forward ---
    def test_forward_wheel_tabs(self):
        self.key(mc.BTN_EXTRA, PRESS)
        up = self.rel(mc.REL_WHEEL, 1)
        self.assertEqual([e[2] for e in up if e[3] == 1],
                         [mc.KEY_LEFTCTRL, mc.KEY_LEFTSHIFT, mc.KEY_TAB])
        down = self.rel(mc.REL_WHEEL, -2)
        self.assertEqual(sum(1 for e in down if e[2] == mc.KEY_TAB and e[3] == 1), 2)
        self.assertNotIn(mc.KEY_LEFTSHIFT, [e[2] for e in down])
        self.assertEqual(self.key(mc.BTN_EXTRA, RELEASE), [])

    def test_forward_tap_even_after_moving(self):
        self.key(mc.BTN_EXTRA, PRESS)
        self.rel(mc.REL_X, 50)
        self.assertEqual(self.key(mc.BTN_EXTRA, RELEASE),
                         [(M, mc.EV_KEY, mc.BTN_EXTRA, 1), (M, mc.EV_KEY, mc.BTN_EXTRA, 0)])

    # --- back ---
    def test_back_tap(self):
        self.key(mc.BTN_SIDE, PRESS)
        self.assertEqual(self.tick(0.1), [])
        self.assertEqual(self.key(mc.BTN_SIDE, RELEASE),
                         [(M, mc.EV_KEY, mc.BTN_SIDE, 1), (M, mc.EV_KEY, mc.BTN_SIDE, 0)])

    def test_back_hold_overview(self):
        self.key(mc.BTN_SIDE, PRESS)
        self.assertEqual(self.tick(0.25),
                         [(K, mc.EV_KEY, mc.KEY_LEFTMETA, 1), (K, mc.EV_KEY, mc.KEY_LEFTMETA, 0)])
        self.assertEqual(self.key(mc.BTN_SIDE, RELEASE), [])

    # --- passthrough & misc ---
    def test_plain_wheel_and_motion_pass_through(self):
        self.assertEqual(self.rel(mc.REL_WHEEL, 1), [(M, mc.EV_REL, mc.REL_WHEEL, 1)])
        self.assertEqual(self.rel(mc.REL_X, 5), [(M, mc.EV_REL, mc.REL_X, 5)])

    def test_release_all_after_unplug(self):
        self.key(mc.BTN_RIGHT, PRESS)
        self.rel(mc.REL_X, 30)              # right press sent (drag)
        self.assertEqual(nosyn(self.e.release_all()), [(M, mc.EV_KEY, mc.BTN_RIGHT, 0)])
        self.assertIsNone(self.e.held[mc.BTN_RIGHT])

    def test_deadline(self):
        self.assertIsNone(self.e.deadline())
        self.key(mc.BTN_LEFT, PRESS)
        self.assertAlmostEqual(self.e.deadline(), 0.12)

    # --- touchpad pan mode ---
    def _tp_pan(self):
        self.e.cfg.pan_mode = "touchpad"
        self.key(mc.BTN_RIGHT, PRESS)
        self.key(mc.BTN_LEFT, PRESS)

    @staticmethod
    def _tp(events):
        return [e for e in events if e[0] == mc.TP]

    def _pos(self, events):
        """First-finger (x, y) from the last frame in events."""
        x = y = None
        slot = None
        for _, t, c, v in events:
            if c == mc.ABS_MT_SLOT:
                slot = v
            elif slot == 0 and c == mc.ABS_MT_POSITION_X:
                x = v
            elif slot == 0 and c == mc.ABS_MT_POSITION_Y:
                y = v
        return x, y

    def test_touchpad_no_touch_until_movement(self):
        self._tp_pan()
        self.assertFalse(self.e.touching)
        self.assertEqual(self.key(mc.BTN_LEFT, RELEASE), [])   # chord without moving: nothing at all
        self.assertEqual(self.tick(1.0), [])

    def test_touchpad_two_finger_drag_follows_mouse(self):
        self._tp_pan()
        out = self.e.feed(mc.EV_REL, mc.REL_Y, 30, self.t)
        tp = self._tp(out)
        self.assertTrue(self.e.touching)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 1), tp)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOOL_DOUBLETAP, 1), tp)
        self.assertEqual(sum(1 for e in tp if e[2] == mc.ABS_MT_TRACKING_ID and e[3] >= 0), 2)
        self.assertEqual(self._pos(tp), (mc.TP_CENTER, mc.TP_CENTER + 30))
        self.assertFalse(any(e[0] == M for e in out))           # cursor stays put by default
        out = self.e.feed(mc.EV_REL, mc.REL_X, -12, self.t)
        self.assertEqual(self._pos(out), (mc.TP_CENTER - 12, mc.TP_CENTER + 30))

    def test_touchpad_second_finger_moves_with_first(self):
        self._tp_pan()
        out = self.e.feed(mc.EV_REL, mc.REL_X, 7, self.t)
        xs = [e[3] for e in out if e[2] == mc.ABS_MT_POSITION_X]
        self.assertEqual(xs[-1] - xs[-2], mc.TP_GAP)

    def test_touchpad_lift_is_delayed_no_fling_no_tap(self):
        self._tp_pan()
        self.rel(mc.REL_Y, 10)
        self.assertEqual(self.key(mc.BTN_RIGHT, RELEASE, 0.05), [])
        self.assertTrue(self.e.touching)
        self.assertEqual(self.tick(0.1), [])                    # < 250 ms since touch down
        out = self.e.tick(self.t + 0.2)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 0), out)
        self.assertEqual(sum(1 for e in out if e[2] == mc.ABS_MT_TRACKING_ID and e[3] == -1), 2)
        self.assertFalse(self.e.touching)

    def test_touchpad_quick_repan_keeps_fingers_down(self):
        self._tp_pan()
        self.rel(mc.REL_Y, 10)
        self.key(mc.BTN_RIGHT, RELEASE)
        self.key(mc.BTN_LEFT, RELEASE)
        self.key(mc.BTN_RIGHT, PRESS, 0.03)
        self.key(mc.BTN_LEFT, PRESS)
        out = self.e.feed(mc.EV_REL, mc.REL_Y, 5, self.t)
        self.assertNotIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 1), out)   # no new touch down
        self.assertEqual(self._pos(out), (mc.TP_CENTER, mc.TP_CENTER + 15))
        self.assertIsNone(self.e.lift_deadline)

    def test_touchpad_regrip_near_edge(self):
        self._tp_pan()
        self.rel(mc.REL_Y, 1)
        out = self.e.feed(mc.EV_REL, mc.REL_Y, mc.TP_MAX, self.t)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 0), out)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 1), out)
        self.assertTrue(self.e.touching)

    def test_touchpad_scrollbar_direction(self):
        self._tp_pan()
        self.e.cfg.pan_follow_hand = False
        out = self.e.feed(mc.EV_REL, mc.REL_Y, 30, self.t)
        self.assertEqual(self._pos(out), (mc.TP_CENTER, mc.TP_CENTER - 30))

    def test_touchpad_release_all_lifts_fingers(self):
        self._tp_pan()
        self.rel(mc.REL_X, 10)
        self.assertIn((mc.TP, mc.EV_KEY, mc.BTN_TOUCH, 0), self.e.release_all())

    def test_pan_move_cursor(self):
        self.e.cfg.pan_move_cursor = True
        self.key(mc.BTN_RIGHT, PRESS)
        self.key(mc.BTN_LEFT, PRESS)
        out = self.rel(mc.REL_X, 9)
        self.assertIn((M, mc.EV_REL, mc.REL_X, 9), out)
        self.assertTrue(any(e[2] == mc.REL_HWHEEL_HI_RES for e in out))

    def test_bad_pan_mode_rejected(self):
        with self.assertRaises(ValueError):
            mc._pan_mode("trackpad")


class ConfigTest(unittest.TestCase):
    """The example config and the program's DEFAULTS must never drift apart."""

    def setUp(self):
        with open(os.path.join(_here, "lxmbc.conf.example")) as f:
            self.example = f.read()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text):
        path = os.path.join(self.tmp.name, "lxmbc.conf")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_example_documents_every_default_exactly(self):
        found = dict(re.findall(r"^#(\w+) =[ ]?(.*)$", self.example, flags=re.M))
        self.assertEqual(found, mc.DEFAULTS)

    def test_example_has_no_active_settings(self):
        active = [l for l in self.example.splitlines()
                  if l.strip() and not l.startswith("#") and l.strip() != "[lxmbc]"]
        self.assertEqual(active, [])
        self.assertIn(mc.CONFIG_FORMAT_MARKER, self.example)

    def test_example_as_config_gives_defaults(self):
        cfg = mc.load_config(self._write(self.example))
        self.assertEqual(cfg.overrides, {})
        self.assertEqual(cfg, mc.load_config(None))

    def test_missing_file_gives_defaults(self):
        cfg = mc.load_config(os.path.join(self.tmp.name, "nope.conf"))
        self.assertEqual(cfg.chord_ms, float(mc.DEFAULTS["chord_ms"]))

    def test_override_and_unknown(self):
        cfg = mc.load_config(self._write("[lxmbc]\npan_mode = touchpad\nchord_mss = 50\n"))
        self.assertEqual(cfg.pan_mode, "touchpad")
        self.assertEqual(cfg.overrides, {"pan_mode": "touchpad"})
        self.assertEqual(cfg.unknown, ["chord_mss"])

    def test_upgrade_keeps_only_non_defaults(self):
        old = self._write("[lxmbc]\ndevices =\nchord_ms = 200\nchord_move_threshold = 25\n"
                          "move_threshold = 4\npan_mode = touchpad\npan_speed = 2.25\n"
                          "touchpad_speed = 1.5\npan_follow_hand = true\n"
                          "pan_move_cursor = no\nback_hold_ms = 200.0\n")
        text, kept = mc.upgrade_config(old, self.example)
        self.assertEqual(kept, {"pan_mode": "touchpad", "touchpad_speed": "1.5"})
        self.assertIn("\npan_mode = touchpad\n", text)
        self.assertIn("\ntouchpad_speed = 1.5\n", text)
        self.assertIn("\n#chord_ms = 200\n", text)          # default stays commented
        cfg = mc.load_config(self._write(text))
        self.assertEqual(cfg.overrides, kept)
        self.assertEqual(cfg.touchpad_speed, 1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)