I needed a port of XMouseButtonControl for Linux (Ubuntu), and InputRemapper wasn't quite cutting it, particullarly due to chording in my configuration. So this works as a drop-in replacement for **my XMouseButtonControl configuration**, and should be a good starting point for others. You can check the specific configuration in `lxmbc.conf`, but the main features are listed below.

# linuXMouseButtonControl (lxmbc)

XMBC-style button chords, pan and button tricks for any mouse on Linux (X11 or Wayland).

| Action | Result |
|---|---|
| Left + Right (either order) + move | Pan, page follows your hand (tolerates some hand movement and up to 120 ms between fingers) |
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

- Linux with systemd (tested on Ubuntu 24.04, X11 and Wayland)
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

### Uninstall

```
sudo bash uninstall.sh
```

## Configuration

Settings are in `/etc/lxmbc.conf`. Edit it with administrator rights (e.g. `sudoedit /etc/lxmbc.conf`).

| Setting | Default | What it does |
|---|---|---|
| `devices` | empty | Which mice to grab, as comma-separated parts of their names (see `sudo lxmbc --list`). Empty = every mouse, including ones plugged in later. Once set, only listed mice are grabbed. |
| `chord_ms` | `200` | How long (ms) a Left press waits for Right to form the pan chord. `0` = no timeout (press-and-hold becomes a single click). |
| `chord_move_threshold` | `25` | How far the mouse may move while you press the second button of the chord. |
| `move_threshold` | `4` | How far a held Middle can move before it counts as a drag. |
| `pan_speed` | `2.25` | Scroll units per mouse count. Raise if the page lags behind your hand, lower if it runs ahead. |
| `pan_follow_hand` | `yes` | `yes` = page follows your hand, `no` = scrollbar direction. |
| `back_hold_ms` | `200` | How long (ms) to hold Back to open the GNOME overview. |

After editing, restart lxmbc to apply the changes:

```
sudo systemctl restart lxmbc
```

## Files

| In this folder | Installed as | Purpose |
|---|---|---|
| `lxmbc.py` | `/usr/local/bin/lxmbc` | The program |
| `lxmbc.conf` | `/etc/lxmbc.conf` | Settings (the `/etc` copy is the live one) |
| `lxmbc.service` | `/etc/systemd/system/lxmbc.service` | Starts it at boot |
| `test_lxmbc.py` | - | Unit tests for the state machine |
| `install.sh` / `uninstall.sh` | - | Install, update, remove |

## Commands

```
sudo bash install.sh                 # install, or update after editing lxmbc.py (keeps /etc/lxmbc.conf)
sudo bash install.sh --reset-config  # same, but replace /etc/lxmbc.conf with the defaults
sudo lxmbc --list                    # which mice it will grab
sudo lxmbc --debug                   # try it in the foreground; Ctrl+C gives the mouse back
sudo systemctl enable --now lxmbc    # run at every boot
journalctl -u lxmbc -f               # logs
sudoedit /etc/lxmbc.conf && sudo systemctl restart lxmbc   # tune
sudo bash uninstall.sh               # remove (this folder stays)
python3 test_lxmbc.py                # run the tests
```

## How it works

lxmbc grabs the physical mouse through evdev, runs its events through a small
state machine (`Engine`, pure Python, no evdev dependency, unit tested) and
re-emits the result through two virtual devices (uinput): a mouse and a keyboard
for shortcuts. Only one program can grab a mouse at a time, so do not run Input
Remapper on the same mouse, or, if you do, make sure you chain it after or before lxmbc using virtual devices.
