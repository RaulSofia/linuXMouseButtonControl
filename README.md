# linuXMouseButtonControl (lxmbc)

XMBC-style button chords, pan and button tricks for any mouse on Linux (X11 or Wayland).

I needed a port of X-Mouse Button Control for Linux (Ubuntu), and Input Remapper wasn't quite cutting it, particularly due to the chording in my configuration. So this works as a drop-in replacement for **my X-Mouse Button Control configuration**, and should be a good starting point for others. The mappings are built into `lxmbc.py`, and their timings and options can be tuned (see [Configuration](#configuration)). The main features are listed below.

| Action | Result |
|---|---|
| Left + Right (either order) + move | Pan, page follows your hand (tolerates some hand movement and up to 200 ms between fingers) |
| Right tap | Right click, sent on release (like Windows) |
| Right hold + move | Normal right-drag (PyMOL, 3D apps) |
| Right + wheel | Zoom (Ctrl+Numpad+ / Ctrl+Numpad-), no context menu afterwards |
| Middle tap / hold + move | Normal middle click / middle-drag (VS Code box select, PDF pan) |
| Middle + wheel | Horizontal scroll |
| Forward + wheel | Previous / next tab (Ctrl+Shift+Tab / Ctrl+Tab) |
| Back tap / hold 200 ms | Back / GNOME overview |

Everything else passes through unchanged.

## Installation

### Requirements

- Linux with systemd (tested on Ubuntu 24.04 with X11; Wayland should work but is untested)
- Python 3 and `python3-evdev` (the installer adds it on Debian/Ubuntu)

### Install

```
git clone https://github.com/RaulSofia/linuXMouseButtonControl.git
cd linuXMouseButtonControl
sudo bash install.sh
sudo lxmbc --list                    # check your mouse is detected
sudo lxmbc --debug                   # try it; Ctrl+C gives the mouse back
sudo systemctl enable --now lxmbc    # run at every boot
```

### Update

```
git pull && sudo bash install.sh
```

Updating never overwrites your `/etc/lxmbc.conf`.

### Uninstall

```
sudo bash uninstall.sh               # keeps /etc/lxmbc.conf for a later reinstall
sudo bash uninstall.sh --purge       # also deletes /etc/lxmbc.conf
```

## Configuration

Settings are in `/etc/lxmbc.conf`, created on the first install from `lxmbc.conf.example`. Edit it with administrator rights (e.g. `sudoedit /etc/lxmbc.conf`).
Every setting is commented out at its default: remove the leading `#` and edit the value to change one. Settings left commented keep their defaults.

| Setting | Default | What it does |
|---|---|---|
| `devices` | empty | Which mice to grab, as comma-separated parts of their names (see `sudo lxmbc --list`). Empty = every mouse, including ones plugged in later. Once set, only listed mice are grabbed. |
| `chord_ms` | `200` | How long (ms) a Left press waits for Right to form the pan chord. `0` = no timeout (press-and-hold becomes a single click). |
| `chord_move_threshold` | `25` | How far the mouse may move while you press the second button of the chord. |
| `move_threshold` | `4` | How far a held Middle can move before it counts as a drag. |
| `pan_mode` | `wheel` | `wheel` = pan as wheel scrolling (works everywhere, can feel soft). `touchpad` = experimental, pan as a two-finger drag on a virtual touchpad (precise, 1:1). |
| `pan_speed` | `2.25` | Pan speed in wheel mode. Raise if the page lags behind your hand, lower if it runs ahead. |
| `touchpad_speed` | `1.0` | Pan speed in touchpad mode. |
| `pan_follow_hand` | `yes` | `yes` = page follows your hand, `no` = scrollbar direction. |
| `pan_move_cursor` | `no` | `yes` = the cursor keeps moving during a pan. Scrolling goes to the window under the cursor. |
| `back_hold_ms` | `200` | How long (ms) to hold Back to open the GNOME overview. |

After editing, restart lxmbc to apply the changes:

```
sudo systemctl restart lxmbc
```

## Files

| In this folder | Installed as | Purpose |
|---|---|---|
| `lxmbc.py` | `/usr/local/bin/lxmbc` | The program (and the only place defaults are defined) |
| `lxmbc.conf.example` | `/etc/lxmbc.conf` (first install only) | Example config; the `/etc` copy is the live one |
| `lxmbc.service` | `/etc/systemd/system/lxmbc.service` | Starts it at boot |
| `test_lxmbc.py` | - | Unit tests for the state machine and config |
| `install.sh` / `uninstall.sh` | - | Install, update, remove |

## Commands

```
sudo bash install.sh                 # install, or update after editing lxmbc.py (keeps /etc/lxmbc.conf)
sudo lxmbc --list                    # which mice it will grab
sudo lxmbc --debug                   # try it in the foreground; Ctrl+C gives the mouse back
sudo systemctl enable --now lxmbc    # run at every boot
journalctl -u lxmbc -f               # logs
sudoedit /etc/lxmbc.conf && sudo systemctl restart lxmbc   # tune
sudo bash uninstall.sh               # remove (this folder and /etc/lxmbc.conf stay)
sudo bash uninstall.sh --purge       # remove, including /etc/lxmbc.conf
python3 test_lxmbc.py                # run the tests
```

## How it works

lxmbc grabs the physical mouse through evdev, runs its events through a small
state machine (`Engine`, pure Python, no evdev dependency, unit tested) and
re-emits the result through virtual devices (uinput): a mouse, a keyboard for
shortcuts and, in touchpad pan mode, a touchpad. Only one program can grab a mouse
at a time, so do not run Input Remapper on the same mouse, or, if you do, chain it
before or after lxmbc using virtual devices.