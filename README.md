# GPD Win 2 CPU & Fan Control Suite

> **Automatic GPD Win 2 CPU & Fan control for Debian using NBFC**

A unified, hardware-gated thermal governor, early-boot initramfs power watchdog, and battery optimization toolkit designed specifically for the **GPD Win 2** (Intel Core m3-7Y30 and m3-8100Y revisions) running **Debian GNU/Linux** (Trixie/Sid, 6.12+ kernels, UEFI GRUB/systemd-boot, and LUKS+LVM).

---

## ⚡ The Problem

The GPD Win 2 is a compact handheld PC that suffers from distinct thermal and power management challenges under Linux:
1. **BIOS 5.0W MSR Lock**: Intel Running Average Power Limit (RAPL) MSR registers are hardware-locked in firmware; standard tools like `undervolt` or `throttled` cannot dynamically scale PL1/PL2 power envelopes.
2. **NBFC "Critical Mode" Fan Latch**: If CPU temperatures spike past 70°C (especially while fast-charging over USB-PD), the Embedded Controller (EC) permanently latches the fan to an un-resettable 100% emergency state until full system power dissipation.
3. **The Unencrypted LUKS Boot Battery Leak**: Sitting at the early-boot ramdisk passphrase prompt leaves all 4 CPU threads at 100% frequency with Turbo Boost permitted, pulling 3.5W to 5.0W and draining the battery if turned on accidentally in a bag.
4. **Embedded Controller I/O Collisions**: Early-boot shell scripts attempting to poll sysfs or `/proc/interrupts` flood the shared ACPI/LPC/eSPI bus, crashing the motherboard's EC microcontroller.

---

## 🚀 The Solution

This suite provides an all-in-one, self-installing architecture:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. Early Boot Ramdisk (Initramfs init-top)                                              │
│    • gpd-win-2-power-watchdog (C Micro-Daemon)                                          │
│      - 0.00% CPU kernel poll() on /dev/input/event* (Zero EC bus traffic)               │
│      - Automatically enforces 1-core 1.6GHz max & 300MHz GPU during boot                │
│      - 30s Inactivity ──► Dims display to step floor (75 / 1%)                          │
│      - 60s Inactivity ──► S3 Sleep (echo mem > /sys/power/state)                        │
│      - Power Button Tap ──► Instant ACPI S5 Hardware Shutdown (reboot(RB_POWER_OFF))    │
│      - Hotkey Brightness ──► 20-step GNOME-identical brightness curve with persistence  │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │ (LUKS Decryption Handover via init-bottom)
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. Post-Boot Runtime (Systemd Userland)                                                 │
│    • gpd-win-2-governor.service (Python Staged Dynamic Governor)                        │
│      - Stage 1 (62°C): Disables Intel Turbo Boost globally via intel_pstate             │
│      - Stage 2 (68°C): Cuts power to Core 1 (parks cpu1/cpu3) + blasts fan to 100%      │
│      - Stage 2 Recovery (<= 59°C for 15s): Restores Core 1 + restores auto fan curve    │
│      - Eco Automation: Single-core 30% perf ceiling if load < 0.20 or battery <= 10%    │
│      - Self-Healing Watchdog: Supervises and auto-revives nbfc_service if it crashes    │
│    • gpd-win-2-lowpower [on|off]                                                        │
│      - Manual override clamping draw to ~1.5W baseline (>15 hours predicted runtime)    │
│    • Multi-Machine Safe Guard                                                           │
│      - Validates CPU signature (m3-7Y30 / m3-8100Y); sleeps dormant on other PCs        │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Upstream Dependencies & Credits

This project integrates and builds upon the following open-source utilities:

* **[nbfc-linux/nbfc-linux](https://github.com/nbfc-linux/nbfc-linux)**: Lightweight, C-based implementation of NoteBook FanControl for Linux. Used for direct Embedded Controller (EC) fan register manipulation.
* **[Linux Kernel `intel_pstate`](https://www.kernel.org/doc/html/latest/admin-guide/pm/intel_pstate.html)**: Kernel scaling driver used to dynamically clamp frequency ceilings and toggle Turbo Boost without touching locked MSRs.
* **[Linux Kernel Input Subsystem (`evdev`)](https://www.kernel.org/doc/html/latest/input/input.html)**: Provides non-blocking, interrupt-driven event streams for keyboard, lid switch, and power button monitoring.
* **[Debian `initramfs-tools`](https://wiki.debian.org/initramfs-tools)**: The modular initramfs generation framework used to inject early power hooks into the boot ramdisk.

---

## 📦 Compatibility

| Hardware Model | CPU Architecture | Supported | Notes |
| :--- | :--- | :---: | :--- |
| **GPD Win 2 (Rev 1)** | Intel Core m3-7Y30 | ✅ | Full support (HD Graphics 615, 7500 max backlight) |
| **GPD Win 2 (Rev 2)** | Intel Core m3-8100Y | ✅ | Full support (UHD Graphics 615, 7500 max backlight) |
| **Other PCs / Laptops** | Any Non-Win 2 CPU | 🛡️ *Safe* | Multi-boot USB safe. Scripts detect foreign CPU and exit with 0% CPU overhead. |

---

## 📥 Quickstart Installation

Clone the repository and run the automated installer. It automatically detects missing dependencies, compiles the C micro-daemon, configures `nbfc-linux`, injects initramfs hooks, updates the ramdisk, and activates systemd services.

```bash
# 1. Clone repository
git clone https://github.com/f-fix/gpd-win-2-governor.git
cd gpd-win-2-governor

# 2. Run the all-in-one installer (auto-elevates with sudo)
chmod +x gpd-win-2-governor.py
./gpd-win-2-governor.py --install
```

---

## 💻 CLI Usage

### 1. Manual "Ultimate Low Power" Mode
Force the system down to single-core, non-turbo, 30% performance ceiling, and 300 MHz GPU clock:

```bash
# Enter low-power mode (~1.5W baseline)
gpd-win-2-lowpower on

# Restore adaptive performance mode
gpd-win-2-lowpower off
```
*(No need to type `sudo`; the script auto-elevates if run by a regular user).*

---

### 2. Service Management & Health Checks

Check the dynamic governor daemon:
```bash
systemctl status gpd-win-2-governor.service
```

Check NoteBook FanControl status:
```bash
nbfc status
```

Inspect early-boot initramfs watchdog logs:
```bash
# Read dedicated ramdisk log
cat /run/gpd_win_2_power.log

# Inspect kernel ring buffer logs
sudo dmesg | grep gpd-win-2-watchdog
```

---

## ⚙️ How It Works (Technical Details)

### 1. The GNOME-Matched Backlight Curve
The C watchdog dynamically computes brightness levels using GNOME's 20-step formula against the panel's maximum range (`7500`):

* `min = ⌊max / 100⌋ = 75`
* `step_size = ⌊(max - min) / 20⌋ = 371`
* `brightness(N) = min + (N × step_size)` for steps 1 through 19
* `brightness(0) = min = 75` (0% slider / Dim Mode)
* `brightness(20) = max = 7500` (100% slider)

#### Brightness Scale:
* **Normal Baseline**: `1930` (Step 5 / 25% slider)
* **Dim Level**: `75` (Step 0 / 1% floor)
* **Hotkeys**: Pressing Brightness Up/Down adjusts brightness in exact 5% increments during the LUKS prompt, and persists across S3 suspend/resume loops.

### 2. Instant ACPI Poweroff
In the initramfs stage, `systemd-logind` is not yet running. The watchdog reads directly from `/dev/input/event*` and intercepts `KEY_POWER` (116). Upon detection, it blanks the screen, issues `sync()`, and immediately triggers the `reboot(RB_POWER_OFF)` kernel syscall, cutting the S5 power rail in < 50ms.

### 3. Portable USB Multi-Boot Safety
If you install Debian onto an external SSD or USB drive and boot another PC (e.g. desktop, ThinkPad, AMD machine):
* **Initramfs**: `/bin/gpd-win-2-power-watchdog` scans `/proc/cpuinfo` for `7Y30` / `8100Y`. If not found, it exits in 0.001s without altering backlight, power states, or CPU settings.
* **Systemd**: `gpd-win-2-governor.service` checks CPU signatures and halts cleanly.
* **CLI**: `gpd-win-2-lowpower` aborts with a clear warning if invoked on foreign hardware.

---

## 📂 File Layout

```text
/usr/local/bin/
├── gpd-win-2-governor          # The primary staged thermal governor & installer
├── gpd-win-2-governor.py       # Symlink for python compatibility
├── gpd-win-2-lowpower          # CLI manual low-power toggle
└── gpd-win-2-power-watchdog    # Compiled C early-boot binary

/usr/local/src/
└── gpd-win-2-power-watchdog.c  # Source code for the ramdisk C watchdog

/etc/initramfs-tools/
├── hooks/gpd_win_2_power       # Copies C watchdog into initrd cpio
├── scripts/init-top/gpd_win_2_power    # Launches watchdog post-udev
└── scripts/init-bottom/gpd_win_2_power # Sends SIGTERM to watchdog upon LUKS unlock

/etc/systemd/system/
├── gpd-win-2-governor.service  # Systemd governor supervision unit
└── nbfc_service.service        # Systemd NBFC fan control unit
```

## Note on the code and the tools used to write it
Parts of this code were written (including some initial ones that began in other, separate projects) with assistance from LLM-integrated coding tools. If you don't like it, feel free to use other software or rewrite parts you dislike. PRs are welcome!
