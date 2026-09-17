# GPD Win 2 CPU & Fan Control Suite

> **Automatic GPD Win 2 CPU & Fan control for Debian using NBFC**

A unified, hardware-gated thermal governor, early-boot initramfs power watchdog, and battery optimization toolkit designed specifically for the **GPD Win 2** (Intel Core m3-7Y30 and m3-8100Y revisions) running **Debian GNU/Linux** (the only actually confirmed supported OS so far is **Debian forky/sid (unstable)**, with Linux 6.12+ kernels, UEFI GRUB/systemd-boot, and LUKS+LVM).

---

## ⚡ The Problem

The GPD Win 2 is a compact handheld PC that suffers from distinct thermal and power management challenges under Linux:
1. **BIOS 5.0W MSR Lock**: Intel Running Average Power Limit (RAPL) MSR registers are hardware-locked in firmware; standard tools like `undervolt` or `throttled` cannot dynamically scale PL1/PL2 power envelopes.
2. **NBFC "Critical Mode" Fan Latch**: If CPU temperatures spike past 70°C (especially while fast-charging over USB-PD), the Embedded Controller (EC) permanently latches the fan to an un-resettable 100% emergency state until full system power dissipation.
3. **The Unencrypted LUKS Boot Battery Leak**: Sitting at the early-boot ramdisk passphrase prompt leaves all 4 CPU threads at 100% frequency with Turbo Boost permitted, pulling 3.5W to 5.0W and draining the battery if turned on accidentally in a bag.
4. **Premature Initramfs Dimming/Sleeping**: Unconditional timers in early boot can dim or put the machine to sleep during disk checks (`fsck`), pre-prompt initialization, or post-passphrase LVM2 setup.
5. **CPU Hotplug Freezes during Hibernation**: Parking secondary CPU cores (`cpu1`, `cpu3`) causes the kernel hibernation engine (`disable_nonboot_cpus()`) to deadlock with offline DTS listeners before writing snapshot state to swap.

---

## 🚀 The Solution

This suite provides an all-in-one, self-installing architecture:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. Early Boot Ramdisk (Initramfs init-top)                                              │
│    • gpd-win-2-power-watchdog (C Micro-Daemon)                                          │
│      - Prompt-Gated Timer: Inactivity dim (30s) / sleep (60s) ONLY runs during prompts  │
│      - Zero Dimming during fsck: Auto-detects running fsck and inhibits idle timers     │
│      - Safe ACPI Poweroff: Defers power button shutdown until active fsck completes     │
│      - Hotkey Brightness: 20-step GNOME-identical brightness curve (always active)      │
│      - Hardware Baseline: Enforces 1-core 30% max perf & 300MHz GPU during boot         │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │ (LUKS Decryption Handover via init-bottom)
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. Post-Boot Runtime (Systemd Userland)                                                 │
│    • gpd-win-2-governor.service (Python Staged Dynamic Governor)                        │
│      - Stage 1 (62°C): Disables Intel Turbo Boost globally via intel_pstate             │
│      - Stage 2 (68°C): Cuts power to Core 1 (parks cpu1/cpu3) + boosts fan to 100%      │
│      - Stage 2 Recovery (<= 59°C for 15s): Restores Core 1 + restores auto fan curve    │
│      - Eco Automation: Single-core 30% perf ceiling if load < 0.20 or battery <= 10%    │
│      - Self-Healing Watchdog: Supervises and auto-revives nbfc_service if it crashes    │
│    • gpd-win-2-lowpower [on|off|status]                                                 │
│      - Persistent Low-Power Mode: Saved across reboots (/etc/gpd-win-2-lowpower.state) │
│    • Suspend & Hibernate Quiescence Hook (/lib/systemd/system-sleep/gpd-win-2-sleep)   │
│      - Automatically un-parks all cores before sleep/hibernate to prevent CPU lockups   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Upstream Dependencies & Credits

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

## 📥 Installation

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

### 1. Persistent "Ultimate Low Power" Mode
Force the system down to single-core, non-turbo, 30% performance ceiling, and 300 MHz GPU clock (persists across reboots):

```bash
# Check current power profile and persistent configuration state
gpd-win-2-lowpower status

# Enter low-power mode (~1.5W baseline, saved across reboots)
gpd-win-2-lowpower on

# Restore adaptive dynamic governor mode (saved across reboots)
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

## 🔍 Hibernation Debugging & Troubleshooting Guide

If hibernation fails intermittently on the GPD Win 2 (fans running, screen frozen, no image written to swap), follow this step-by-step procedure:

### 1. Isolated Subsystem Testing (`pm_test`)
Test hibernation stages sequentially without writing to swap or cutting power:

```bash
# Step 1: Test userspace process freezer (freezes processes for 5s and thaws)
sudo sh -c 'echo freezer > /sys/power/pm_test && systemctl hibernate'

# Step 2: Test device drivers (i915, audio, wifi, pci, ec)
sudo sh -c 'echo devices > /sys/power/pm_test && systemctl hibernate'

# Step 3: Test ACPI platform suspend
sudo sh -c 'echo platform > /sys/power/pm_test && systemctl hibernate'

# Step 4: Test CPU hotplug / secondary core shutdown
sudo sh -c 'echo processors > /sys/power/pm_test && systemctl hibernate'

# Step 5: Test full core snapshot creation
sudo sh -c 'echo core > /sys/power/pm_test && systemctl hibernate'

# Reset pm_test back to normal when finished:
sudo sh -c 'echo none > /sys/power/pm_test'
```

### 2. Kernel Console Debugging
To see the exact driver callback or CPU thread hanging during hibernation:

1. Edit `/etc/default/grub` and add to `GRUB_CMDLINE_LINUX_DEFAULT`:
   ```bash
   no_console_suspend initcall_debug pm_debug_messages ignore_loglevel
   ```
2. Run `sudo update-grub` and reboot.
   * `no_console_suspend`: Prevents display console blanking so error traces stay visible on screen.
   * `initcall_debug`: Logs every device driver's suspend & resume timing and return status.
   * `pm_debug_messages`: Enables detailed kernel power-management transitions.

### 3. Inspect Previous Failed Boot Journal
```bash
journalctl -b -1 -u systemd-hibernate.service -k --no-pager | tail -n 100
```

---

## 📂 File Layout

```text
/usr/local/bin/
├── gpd-win-2-governor          # The primary staged thermal governor & installer
├── gpd-win-2-governor.py       # Symlink for python compatibility
├── gpd-win-2-lowpower          # CLI persistent low-power switcher
├── gpd-win-2-nbfc-prestart     # NBFC DTS sensor & core un-park prestart helper
└── gpd-win-2-power-watchdog    # Compiled C early-boot binary

/usr/local/src/
└── gpd-win-2-power-watchdog.c  # Source code for the ramdisk C watchdog

/lib/systemd/system-sleep/
└── gpd-win-2-sleep             # Pre-suspend/hibernate CPU un-park & EC sync hook

/etc/initramfs-tools/
├── hooks/gpd_win_2_power       # Copies C watchdog into initrd cpio
├── scripts/init-top/gpd_win_2_power    # Launches watchdog post-udev
└── scripts/init-bottom/gpd_win_2_power # Hands off to systemd upon LUKS unlock

/etc/systemd/system/
├── gpd-win-2-governor.service  # Systemd governor supervision unit
└── nbfc_service.service        # Systemd NBFC fan control unit
```

## 🤖 Note on the code and the tools used to write it
Parts of this code were written (including some initial ones that began in other, separate projects) with assistance from LLM-integrated coding tools. If you don't like it, feel free to use other software or rewrite parts you dislike. PRs are welcome!
