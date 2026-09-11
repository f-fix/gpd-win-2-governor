#!/usr/bin/env python3
"""
GPD Win 2 (Intel Core m3-7Y30 / m3-8100Y) Universal Governor & All-in-One Installer
-----------------------------------------------------------------------------------
Namespaced strictly for GPD Win 2 portable/multi-boot installations.

Usage:
  gpd-win-2-governor.py --install   # Full system deploy (auto-elevates with sudo)
  gpd-win-2-governor.py             # Run governor daemon directly
"""

import os
import sys
import time
import subprocess
import shutil

# =====================================================================
# PRIVILEGE ELEVATION & HARDWARE VALIDATION
# =====================================================================
def ensure_root():
    """Auto-elevate to root using sudo if run by an unprivileged user."""
    if os.geteuid() != 0:
        try:
            args = ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
            os.execvp("sudo", args)
        except Exception as e:
            print(f"[ERROR] Failed to auto-elevate with sudo: {e}")
            sys.exit(1)

def is_target_hardware():
    """
    Validates that the host CPU is a GPD Win 2 (Core m3-7Y30 or m3-8100Y).
    Ensures safe, dormant execution when a portable USB drive boots on other PCs.
    """
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read()
        if any(sig in cpuinfo for sig in ["7Y30", "m3-7Y30", "8100Y", "m3-8100Y"]):
            return True
    except Exception:
        pass
    return False

# =====================================================================
# PART 1: THERMAL GOVERNOR RUNTIME
# =====================================================================
TURBO_DROP_TEMP = 62.0
TURBO_RESTORE_TEMP = 58.0
CORE_DROP_TEMP = 68.0
CORE_RESTORE_TEMP = 59.0
THROTTLE_START_TEMP = 55.0

IDLE_LOAD_THRESHOLD = 0.20
HEAVY_LOAD_THRESHOLD = 0.65

PCT_MAX_PERF = 100
PCT_SILENT_LOW = 40
PCT_CHARGING_CAP = 55

def get_cpu_thermal_zone_path():
    base_path = "/sys/class/thermal"
    try:
        for zone in os.listdir(base_path):
            if zone.startswith("thermal_zone"):
                with open(os.path.join(base_path, zone, "type"), "r") as f:
                    if f.read().strip() == "x86_pkg_temp":
                        return os.path.join(base_path, zone, "temp")
    except Exception:
        pass
    return "/sys/class/thermal/thermal_zone6/temp"

def set_core1_state(online):
    for cpu in (1, 3):
        path = f"/sys/devices/system/cpu/cpu{cpu}/online"
        if os.path.exists(path):
            try:
                with open(path, "w") as f:
                    f.write("1" if online else "0")
            except IOError:
                pass

def check_core1_hardware_state():
    try:
        with open("/sys/devices/system/cpu/cpu1/online", "r") as f:
            return f.read().strip() == "1"
    except Exception:
        return True

def apply_pstate_limit(percentage):
    try:
        path = "/sys/devices/system/cpu/intel_pstate/max_perf_pct"
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(str(int(percentage)))
    except Exception:
        pass

def set_turbo_state(enabled):
    pstate_no_turbo = "/sys/devices/system/cpu/intel_pstate/no_turbo"
    if os.path.exists(pstate_no_turbo):
        try:
            with open(pstate_no_turbo, "w") as f:
                f.write("1" if not enabled else "0")
        except IOError:
            pass

def get_battery_metrics():
    capacity = 100
    is_charging = False
    try:
        if os.path.exists("/sys/class/power_supply/BAT0/capacity"):
            with open("/sys/class/power_supply/BAT0/capacity", "r") as f:
                capacity = int(f.read().strip())
        if os.path.exists("/sys/class/power_supply/AC/online"):
            with open("/sys/class/power_supply/AC/online", "r") as f:
                is_charging = f.read().strip() == "1"
    except Exception:
        pass
    return capacity, is_charging

def restart_nbfc():
    """Brings cores online temporarily to guarantee coretemp sensor re-binding."""
    saved_state = check_core1_hardware_state()
    set_core1_state(True)
    subprocess.run(["modprobe", "coretemp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    subprocess.run(["systemctl", "restart", "nbfc_service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not saved_state:
        time.sleep(0.5)
        set_core1_state(False)

def set_nbfc_fan(mode):
    cmd = ["/usr/bin/nbfc", "set", "-s", "100"] if mode == "100" else ["/usr/bin/nbfc", "set", "-a"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            restart_nbfc()
            time.sleep(0.5)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        restart_nbfc()

def run_governor():
    ensure_root()

    if not is_target_hardware():
        print("[gpd-win-2-governor] Non-GPD Win 2 hardware detected. Halting safely with 0% overhead.")
        sys.exit(0)

    print("GPD Win 2 Dynamic Thermal Governor Online.")
    CPU_TEMP_PATH = get_cpu_thermal_zone_path()

    core1_is_online = check_core1_hardware_state()
    turbo_is_enabled = True
    last_core_drop_time = 0
    last_nbfc_check = 0
    forced_emergency_core_drop = False

    while True:
        try:
            current_time = time.time()
            if current_time - last_nbfc_check > 15:
                res = subprocess.run(["/usr/bin/nbfc", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0:
                    restart_nbfc()
                last_nbfc_check = current_time

            with open(CPU_TEMP_PATH, "r") as f:
                temp = float(f.read().strip()) / 1000.0
                
            capacity, is_charging = get_battery_metrics()
            load1, _, _ = os.getloadavg()
            normalized_load = load1 / os.cpu_count()

            is_critically_low = (not is_charging and capacity <= 10) or (is_charging and capacity <= 5)

            if is_critically_low:
                if core1_is_online:
                    set_core1_state(False)
                    core1_is_online = False
                set_turbo_state(False)
                turbo_is_enabled = False
                apply_pstate_limit(30)
                time.sleep(2)
                continue

            if temp >= CORE_DROP_TEMP and core1_is_online:
                set_core1_state(False)
                core1_is_online = False
                forced_emergency_core_drop = True
                last_core_drop_time = current_time
                set_nbfc_fan("100")
                print(f"[{time.strftime('%H:%M:%S')}] STAGE 2 BREAK: Temp {temp}C. Disabling Core 1 + Fan Boost.")
                time.sleep(0.5)
                continue
            
            if forced_emergency_core_drop and temp <= CORE_RESTORE_TEMP and (current_time - last_core_drop_time > 15):
                set_core1_state(True)
                core1_is_online = True
                forced_emergency_core_drop = False
                set_nbfc_fan("auto")
                print(f"[{time.strftime('%H:%M:%S')}] STAGE 2 SAFE: Temp {temp}C. Core 1 Restored.")

            if not forced_emergency_core_drop:
                if normalized_load <= IDLE_LOAD_THRESHOLD:
                    if core1_is_online:
                        set_core1_state(False)
                        core1_is_online = False
                    if turbo_is_enabled:
                        set_turbo_state(False)
                        turbo_is_enabled = False
                    apply_pstate_limit(30)
                else:
                    if not core1_is_online:
                        set_core1_state(True)
                        core1_is_online = True
                    
                    if temp >= TURBO_DROP_TEMP and turbo_is_enabled:
                        set_turbo_state(False)
                        turbo_is_enabled = False
                        print(f"[{time.strftime('%H:%M:%S')}] STAGE 1 CUTOFF: Temp {temp}C. Disabling Turbo.")
                    elif temp <= TURBO_RESTORE_TEMP and not turbo_is_enabled:
                        set_turbo_state(True)
                        turbo_is_enabled = True
                        print(f"[{time.strftime('%H:%M:%S')}] STAGE 1 RESTORE: Temp {temp}C. Turbo Allowed.")

                    max_possible_pct = PCT_CHARGING_CAP if is_charging else PCT_MAX_PERF
                    if normalized_load >= HEAVY_LOAD_THRESHOLD:
                        current_max_pct = max_possible_pct
                    else:
                        scale = (normalized_load - IDLE_LOAD_THRESHOLD) / (HEAVY_LOAD_THRESHOLD - IDLE_LOAD_THRESHOLD)
                        current_max_pct = PCT_SILENT_LOW + (scale * (max_possible_pct - PCT_SILENT_LOW))

                    if temp >= THROTTLE_START_TEMP:
                        overshoot = (temp - THROTTLE_START_TEMP) / (CORE_DROP_TEMP - THROTTLE_START_TEMP)
                        reduction = overshoot * (current_max_pct - PCT_SILENT_LOW)
                        target_pct = max(PCT_SILENT_LOW, current_max_pct - reduction)
                    else:
                        target_pct = current_max_pct

                    apply_pstate_limit(target_pct)
                
        except Exception:
            pass
        time.sleep(0.5)

# =====================================================================
# PART 2: EMBEDDED NAMESPACED PAYLOADS
# =====================================================================

NBFC_PRESTART_PAYLOAD = r"""#!/bin/sh
# GPD Win 2 NBFC Pre-flight Sensor & Socket Initializer
rm -f /run/nbfc_service.socket /run/nbfc_service.pid /var/run/nbfc_service.socket /var/run/nbfc_service.pid

# Un-park cores so Intel coretemp DTS sensors are active
for c in /sys/devices/system/cpu/cpu1/online /sys/devices/system/cpu/cpu3/online; do
    if [ -f "$c" ]; then
        echo 1 > "$c" 2>/dev/null || true
    fi
done

modprobe coretemp 2>/dev/null || true

# Wait up to 3 seconds for coretemp to appear in /sys/class/hwmon
count=0
while [ $count -lt 15 ]; do
    for h in /sys/class/hwmon/hwmon*/name; do
        if [ -f "$h" ]; then
            if [ "$(cat "$h" 2>/dev/null)" = "coretemp" ]; then
                exit 0
            fi
        fi
    done
    sleep 0.2
    count=$((count + 1))
done

exit 0
"""

C_WATCHDOG_PAYLOAD = r"""#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <glob.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/reboot.h>
#include <sys/ioctl.h>
#include <linux/input.h>

#define BACKLIGHT_PATH "/sys/class/backlight/intel_backlight"
#define POWER_STATE_PATH "/sys/power/state"
#define LOG_FILE "/run/gpd_win_2_power.log"
#define TOTAL_STEPS 20

static volatile sig_atomic_t keep_running = 1;
static int max_brightness = 7500;
static int min_brightness = 75;
static int step_size = 371;
static int current_step = 5; // Step 5 = 25% = 1930
static int user_brightness = 1930;
static int log_fd = -1;
static int kmsg_fd = -1;

void log_init(void) {
    log_fd = open(LOG_FILE, O_WRONLY | O_CREAT | O_TRUNC | O_SYNC, 0644);
    kmsg_fd = open("/dev/kmsg", O_WRONLY);
}

void log_msg(const char *fmt, ...) {
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (log_fd >= 0) dprintf(log_fd, "[gpd-win-2-watchdog] %s\n", buf);
    if (kmsg_fd >= 0) dprintf(kmsg_fd, "<4>[gpd-win-2-watchdog] %s\n", buf);
}

int is_target_hardware(void) {
    FILE *f = fopen("/proc/cpuinfo", "r");
    if (!f) return 0;
    char line[256];
    int match = 0;
    while (fgets(line, sizeof(line), f)) {
        if (strstr(line, "7Y30") || strstr(line, "8100Y")) {
            match = 1;
            break;
        }
    }
    fclose(f);
    return match;
}

void handle_signal(int sig) {
    log_msg("Caught termination signal (%d). Restoring cores for systemd handoff.", sig);
    keep_running = 0;
}

static int write_sysfs(const char *path, const char *val) {
    int fd = open(path, O_WRONLY);
    if (fd >= 0) {
        ssize_t w = write(fd, val, strlen(val));
        close(fd);
        return (w > 0) ? 0 : -1;
    }
    return -1;
}

int calculate_brightness(int step) {
    if (step <= 0) return min_brightness;
    if (step >= TOTAL_STEPS) return max_brightness;
    return min_brightness + (step * step_size);
}

void set_brightness(int val) {
    char path[128];
    snprintf(path, sizeof(path), "%s/brightness", BACKLIGHT_PATH);
    int fd = open(path, O_WRONLY);
    if (fd >= 0) {
        dprintf(fd, "%d\n", val);
        close(fd);
        log_msg("Hardware brightness set to %d", val);
    }
}

void init_backlight(void) {
    char path[128];
    snprintf(path, sizeof(path), "%s/max_brightness", BACKLIGHT_PATH);
    FILE *f = fopen(path, "r");
    if (f) {
        if (fscanf(f, "%d", &max_brightness) == 1 && max_brightness > 0) {
            min_brightness = (max_brightness >= 100) ? (max_brightness / 100) : 1;
            step_size = (max_brightness - min_brightness) / TOTAL_STEPS;
        }
        fclose(f);
    }
    user_brightness = calculate_brightness(current_step);
    log_msg("Backlight initialized: Max=%d, Min=%d, StepSize=%d, Normal=%d (Step %d/%d)", 
            max_brightness, min_brightness, step_size, user_brightness, current_step, TOTAL_STEPS);
    set_brightness(user_brightness);
}

void brightness_step_up(void) {
    if (current_step < TOTAL_STEPS) {
        current_step++;
        user_brightness = calculate_brightness(current_step);
        log_msg("Hotkey UP -> %d (Step %d/%d, %d%%)", 
                user_brightness, current_step, TOTAL_STEPS, current_step * 5);
    }
    set_brightness(user_brightness);
}

void brightness_step_down(void) {
    if (current_step > 0) {
        current_step--;
        user_brightness = calculate_brightness(current_step);
        log_msg("Hotkey DOWN -> %d (Step %d/%d, %d%%)", 
                user_brightness, current_step, TOTAL_STEPS, current_step * 5);
    }
    set_brightness(user_brightness);
}

void apply_lowpower_profile(void) {
    log_msg("Applying GPD Win 2 low-power profile...");
    write_sysfs("/sys/devices/system/cpu/intel_pstate/no_turbo", "1\n");
    write_sysfs("/sys/devices/system/cpu/intel_pstate/max_perf_pct", "30\n");
    for (int i = 0; i < 4; i++) {
        char path[128];
        snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_governor", i);
        write_sysfs(path, "powersave\n");
    }
    write_sysfs("/sys/devices/system/cpu/cpu1/online", "0\n");
    write_sysfs("/sys/devices/system/cpu/cpu3/online", "0\n");
    write_sysfs("/sys/class/drm/card0/gt_max_freq_mhz", "300\n");
    log_msg("Low-power baseline enforced (1 Core, 30%% max, 300MHz GPU).");
}

void restore_full_cores(void) {
    write_sysfs("/sys/devices/system/cpu/cpu1/online", "1\n");
    write_sysfs("/sys/devices/system/cpu/cpu3/online", "1\n");
}

int scan_inputs(struct pollfd *fds, int max_fds) {
    for (int i = 0; i < max_fds; i++) {
        if (fds[i].fd >= 0) {
            close(fds[i].fd);
            fds[i].fd = -1;
        }
    }
    glob_t g;
    int num_fds = 0;
    if (glob("/dev/input/event*", 0, NULL, &g) == 0) {
        for (size_t i = 0; i < g.gl_pathc && num_fds < max_fds; i++) {
            int fd = open(g.gl_pathv[i], O_RDONLY | O_NONBLOCK);
            if (fd >= 0) {
                char name[128] = "Unknown Device";
                ioctl(fd, EVIOCGNAME(sizeof(name)), name);
                log_msg("  Discovered input: %s (%s)", g.gl_pathv[i], name);
                fds[num_fds].fd = fd;
                fds[num_fds].events = POLLIN;
                num_fds++;
            }
        }
        globfree(&g);
    }
    return num_fds;
}

void power_off_immediate(void) {
    log_msg("CRITICAL: Power button pressed! Blanking screen and issuing ACPI Poweroff...");
    set_brightness(0);
    sync();
    reboot(RB_POWER_OFF);
    exit(0);
}

int main(void) {
    log_init();

    if (!is_target_hardware()) {
        log_msg("Foreign hardware detected. Bailing out cleanly with 0% CPU.");
        if (log_fd >= 0) close(log_fd);
        if (kmsg_fd >= 0) close(kmsg_fd);
        return 0;
    }

    log_msg("=== GPD Win 2 Watchdog Active ===");

    signal(SIGTERM, handle_signal);
    signal(SIGINT, handle_signal);

    apply_lowpower_profile();

    for (int i = 0; i < 15 && access(BACKLIGHT_PATH "/brightness", W_OK) != 0; i++) {
        usleep(200000);
    }
    init_backlight();

    struct pollfd fds[32];
    for (int i = 0; i < 32; i++) fds[i].fd = -1;
    int num_fds = scan_inputs(fds, 32);

    int idle_seconds = 0;
    enum { STATE_NORMAL, STATE_DIM, STATE_SUSPEND } state = STATE_NORMAL;

    while (keep_running) {
        if (num_fds == 0) {
            sleep(2);
            num_fds = scan_inputs(fds, 32);
            continue;
        }

        int ret = poll(fds, num_fds, 1000);

        if (ret > 0) {
            int user_activity = 0;
            struct input_event ev;

            for (int i = 0; i < num_fds; i++) {
                if (fds[i].revents & POLLIN) {
                    while (read(fds[i].fd, &ev, sizeof(ev)) == sizeof(ev)) {
                        if (ev.type == EV_KEY && (ev.value == 1 || ev.value == 2)) {
                            if (ev.code == KEY_POWER || ev.code == KEY_POWER2) {
                                power_off_immediate();
                            } else if (ev.code == KEY_BRIGHTNESSUP) {
                                brightness_step_up();
                                state = STATE_NORMAL;
                                user_activity = 1;
                                continue;
                            } else if (ev.code == KEY_BRIGHTNESSDOWN) {
                                brightness_step_down();
                                state = STATE_NORMAL;
                                user_activity = 1;
                                continue;
                            }
                        }
                        user_activity = 1;
                    }
                }
            }

            if (user_activity) {
                idle_seconds = 0;
                if (state != STATE_NORMAL) {
                    log_msg("Activity detected: Restoring user brightness (%d).", user_brightness);
                    set_brightness(user_brightness);
                    state = STATE_NORMAL;
                }
            }
        } else if (ret == 0) {
            idle_seconds++;

            if (idle_seconds >= 60) {
                log_msg("60s idle reached. Suspending to RAM (S3)...");
                state = STATE_SUSPEND;
                write_sysfs(POWER_STATE_PATH, "mem\n");
                log_msg("Resumed from S3 suspend. Restoring user brightness (%d).", user_brightness);
                set_brightness(user_brightness);
                idle_seconds = 0;
                state = STATE_NORMAL;
            } else if (idle_seconds >= 30 && state == STATE_NORMAL) {
                log_msg("30s idle reached. Dimming display to minimum (%d)...", min_brightness);
                set_brightness(min_brightness);
                state = STATE_DIM;
            }
        }
    }

    log_msg("Handoff to rootfs: Restoring full CPU cores for coretemp/systemd.");
    restore_full_cores();
    set_brightness(user_brightness);
    for (int i = 0; i < num_fds; i++) {
        if (fds[i].fd >= 0) close(fds[i].fd);
    }
    if (log_fd >= 0) close(log_fd);
    if (kmsg_fd >= 0) close(kmsg_fd);
    return 0;
}
"""

LOWPOWER_PAYLOAD = r"""#!/usr/bin/env python3
import sys
import os
import time
import subprocess

def ensure_root():
    if os.geteuid() != 0:
        try:
            args = ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
            os.execvp("sudo", args)
        except Exception as e:
            print(f"[ERROR] Failed to auto-elevate with sudo: {e}")
            sys.exit(1)

def is_target_hardware():
    try:
        with open("/proc/cpuinfo", "r") as f:
            c = f.read()
            return any(k in c for k in ["7Y30", "m3-7Y30", "8100Y", "m3-8100Y"])
    except Exception:
        return False

ensure_root()

if not is_target_hardware():
    print("[ERROR] This command is strictly hardware-locked to GPD Win 2 (m3-7Y30 / m3-8100Y).")
    print("        Refusing execution on non-target hardware.")
    sys.exit(1)

def apply_pstate_limit(percentage):
    try:
        path = "/sys/devices/system/cpu/intel_pstate/max_perf_pct"
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(str(percentage))
    except Exception:
        pass

def set_scaling_governor(governor_string):
    try:
        for cpu in range(os.cpu_count()):
            path = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor"
            if os.path.exists(path):
                with open(path, "w") as f:
                    f.write(governor_string)
    except Exception:
        pass

def set_turbo_state(enabled):
    pstate_no_turbo = "/sys/devices/system/cpu/intel_pstate/no_turbo"
    if os.path.exists(pstate_no_turbo):
        try:
            with open(pstate_no_turbo, "w") as f:
                f.write("1" if not enabled else "0")
        except IOError:
            pass

def set_core1_state(online):
    for cpu in (1, 3):
        path = f"/sys/devices/system/cpu/cpu{cpu}/online"
        if os.path.exists(path):
            try:
                with open(path, "w") as f:
                    f.write("1" if online else "0")
            except IOError:
                pass

def ensure_nbfc_running():
    try:
        res = subprocess.run(["/usr/bin/nbfc", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            print("NBFC unresponsive. Bringing cores online to re-bind coretemp sensors...")
            set_core1_state(True)
            subprocess.run(["modprobe", "coretemp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.3)
            subprocess.run(["systemctl", "restart", "nbfc_service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["/usr/bin/nbfc", "set", "-a"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

if len(sys.argv) < 2 or sys.argv[1].lower() not in ["on", "off"]:
    print("Usage: gpd-win-2-lowpower [on|off]")
    sys.exit(1)

action = sys.argv[1].lower()

if action == "on":
    print("Entering Ultimate Low Power Mode (GPD Win 2)...")
    subprocess.run(["systemctl", "stop", "gpd-win-2-governor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "gpd-win-2-governor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ensure_nbfc_running()
    set_turbo_state(False)
    set_scaling_governor("powersave")
    apply_pstate_limit(30)
    set_core1_state(False)
    if os.path.exists("/sys/class/drm/card0/gt_max_freq_mhz"):
        with open("/sys/class/drm/card0/gt_max_freq_mhz", "w") as f:
            f.write("300")
    print("System clamped to single physical core (30% max perf, 300MHz GPU). Thermals minimized.")

elif action == "off":
    print("Restoring Adaptive Performance Mode (GPD Win 2)...")
    set_core1_state(True)
    ensure_nbfc_running()
    if os.path.exists("/sys/class/drm/card0/gt_max_freq_mhz"):
        with open("/sys/class/drm/card0/gt_max_freq_mhz", "w") as f:
            f.write("850")
    set_turbo_state(True)
    apply_pstate_limit(100)
    subprocess.run(["systemctl", "start", "gpd-win-2-governor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Adaptive governor restored via systemd.")
"""

GPD_GOVERNOR_SERVICE = """[Unit]
Description=GPD Win 2 Dynamic Thermal Governor & Watchdog
After=multi-user.target nbfc_service.service
Wants=nbfc_service.service

[Service]
Type=simple
ExecStart=/usr/local/bin/gpd-win-2-governor
Restart=always
RestartSec=3s

[Install]
WantedBy=multi-user.target
"""

# =====================================================================
# PART 3: AUTOMATED INSTALLER ENGINE (--install)
# =====================================================================

def run_install():
    ensure_root()

    print("\n====================================================================")
    print(" GPD Win 2 (m3-7Y30 / m3-8100Y) Universal Stack Installer           ")
    print("====================================================================\n")

    if not is_target_hardware():
        print("[WARNING] Host hardware is NOT a GPD Win 2.")
        print("          Proceeding with installation for portable USB drive deployment.")
        print("          (Hardware guards will ensure these scripts remain dormant on other PCs).\n")

    # Step 1: Install build prerequisites only if missing
    print("[1/8] Verifying build dependencies...")
    pkgs = ["build-essential", "git", "gcc", "make", "pkg-config", "libev-dev"]
    needs_apt = any(shutil.which(p) is None for p in ["gcc", "make", "git", "pkg-config"])
    if needs_apt:
        print("  Installing missing build packages via apt...")
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        subprocess.run(["apt-get", "install", "-y", "-qq"] + pkgs, check=True)
    else:
        print("  [OK] Build toolchain already present.")

    # Step 2: Handle NBFC-Linux & Systemd Service
    print("[2/8] Setting up nbfc-linux & systemd unit...")
    if not shutil.which("nbfc"):
        print("  Cloning and building nbfc-linux from source...")
        tmp_dir = "/tmp/nbfc-linux-build"
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        subprocess.run(["git", "clone", "https://github.com/nbfc-linux/nbfc-linux.git", tmp_dir], check=True)
        subprocess.run(["make", "-C", tmp_dir], check=True)
        subprocess.run(["make", "-C", tmp_dir, "install"], check=True)
        shutil.rmtree(tmp_dir)
        print("  [OK] nbfc-linux compiled and installed.")
    else:
        print("  [OK] nbfc binary already installed.")

    # Ensure all cores are online for coretemp binding
    set_core1_state(True)
    subprocess.run(["modprobe", "coretemp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open("/etc/modules-load.d/coretemp.conf", "w") as f:
        f.write("coretemp\n")

    # Install dedicated prestart helper script
    prestart_bin = "/usr/local/bin/gpd-win-2-nbfc-prestart"
    with open(prestart_bin, "w") as f:
        f.write(NBFC_PRESTART_PAYLOAD)
    os.chmod(prestart_bin, 0o755)

    nbfc_bin = shutil.which("nbfc") or "/usr/bin/nbfc"
    nbfc_service_bin = shutil.which("nbfc_service") or "/usr/bin/nbfc_service"
    nbfc_service_content = f"""[Unit]
Description=NoteBook FanControl service (nbfc-linux)
After=syslog.target network.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStartPre={prestart_bin}
ExecStart={nbfc_service_bin}
ExecStopPost=/bin/rm -f /run/nbfc_service.socket /run/nbfc_service.pid /var/run/nbfc_service.socket /var/run/nbfc_service.pid
Restart=always
RestartSec=3s

[Install]
WantedBy=multi-user.target
"""
    with open("/etc/systemd/system/nbfc_service.service", "w") as f:
        f.write(nbfc_service_content)

    for svc in ["nbfc.service.d", "nbfc_service.service.d"]:
        dpath = f"/etc/systemd/system/{svc}"
        os.makedirs(dpath, exist_ok=True)
        with open(f"{dpath}/restart.conf", "w") as f:
            f.write("[Service]\nRestart=always\nRestartSec=3s\n")

    subprocess.run(["systemctl", "daemon-reload"], check=True)

    if is_target_hardware():
        subprocess.run(["systemctl", "enable", "--now", "nbfc_service.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        res = subprocess.run([nbfc_bin, "config", "--set", "GPD Win 2 (8100y)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run([nbfc_bin, "config", "--set", "GPD Win 2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 3: Write & Compile Early Power Watchdog C binary
    print("[3/8] Compiling namespaced early_power_watchdog C micro-daemon...")
    os.makedirs("/usr/local/src", exist_ok=True)
    c_src = "/usr/local/src/gpd-win-2-power-watchdog.c"
    with open(c_src, "w") as f:
        f.write(C_WATCHDOG_PAYLOAD)
    
    bin_target = "/usr/local/bin/gpd-win-2-power-watchdog"
    subprocess.run(["gcc", "-O2", c_src, "-o", bin_target], check=True)
    os.chmod(bin_target, 0o755)
    print("  [OK] /usr/local/bin/gpd-win-2-power-watchdog ready.")

    # Step 4: Write Namespaced Initramfs Hooks
    print("[4/8] Installing Initramfs early power management hooks...")
    
    # 4a. Binary Hook
    hook_path = "/etc/initramfs-tools/hooks/gpd_win_2_power"
    with open(hook_path, "w") as f:
        f.write("#!/bin/sh\nPREREQ=\"\"\nprereqs() { echo \"$PREREQ\"; }\ncase \"$1\" in prereqs) prereqs; exit 0;; esac\n. /usr/share/initramfs-tools/hook-functions\nif [ -f /usr/local/bin/gpd-win-2-power-watchdog ]; then\n    copy_exec /usr/local/bin/gpd-win-2-power-watchdog /bin\nfi\nexit 0\n")
    os.chmod(hook_path, 0o755)

    # 4b. Init-top Script
    top_path = "/etc/initramfs-tools/scripts/init-top/gpd_win_2_power"
    with open(top_path, "w") as f:
        f.write("#!/bin/sh\nPREREQ=\"udev\"\nprereqs() { echo \"$PREREQ\"; }\ncase \"$1\" in prereqs) prereqs; exit 0;; esac\n. /scripts/functions\nif [ -x /bin/gpd-win-2-power-watchdog ]; then\n    /bin/gpd-win-2-power-watchdog &\n    echo \"$!\" > /run/gpd_win_2_watchdog.pid\nfi\n")
    os.chmod(top_path, 0o755)

    # 4c. Init-bottom Script
    bottom_path = "/etc/initramfs-tools/scripts/init-bottom/gpd_win_2_power"
    with open(bottom_path, "w") as f:
        f.write("#!/bin/sh\nPREREQ=\"\"\nprereqs() { echo \"$PREREQ\"; }\ncase \"$1\" in prereqs) prereqs; exit 0;; esac\n. /scripts/functions\nPIDFILE=\"/run/gpd_win_2_watchdog.pid\"\nif [ -f \"$PIDFILE\" ]; then\n    PID=$(cat \"$PIDFILE\")\n    if [ -n \"$PID\" ]; then\n        kill -TERM \"$PID\" 2>/dev/null || true\n    fi\n    rm -f \"$PIDFILE\"\nfi\n")
    os.chmod(bottom_path, 0o755)

    # Step 5: Enforce Initramfs Modules
    print("[5/8] Validating /etc/initramfs-tools/modules...")
    required_modules = ["i915", "button", "i8042", "evdev", "intel_lpss_pci", "coretemp"]
    modules_file = "/etc/initramfs-tools/modules"
    existing = ""
    if os.path.exists(modules_file):
        with open(modules_file, "r") as f:
            existing = f.read()

    with open(modules_file, "a") as f:
        for mod in required_modules:
            if mod not in existing:
                f.write(f"{mod}\n")
                print(f"  Appended module: {mod}")

    # Step 6: Rebuild Ramdisk
    print("[6/8] Rebuilding Initramfs...")
    subprocess.run(["update-initramfs", "-u"], check=True)

    # Step 7: Install namespaced lowpower utility
    print("[7/8] Installing /usr/local/bin/gpd-win-2-lowpower...")
    lp_path = "/usr/local/bin/gpd-win-2-lowpower"
    with open(lp_path, "w") as f:
        f.write(LOWPOWER_PAYLOAD)
    os.chmod(lp_path, 0o755)

    # Step 8: Install Governor and Systemd Service
    print("[8/8] Installing gpd-win-2-governor and systemd service...")
    src_file = os.path.abspath(__file__)
    dest_gov = "/usr/local/bin/gpd-win-2-governor"
    
    if os.path.abspath(src_file) != os.path.abspath(dest_gov):
        shutil.copyfile(src_file, dest_gov)
    os.chmod(dest_gov, 0o755)

    dest_symlink = "/usr/local/bin/gpd-win-2-governor.py"
    if os.path.abspath(src_file) != os.path.abspath(dest_symlink):
        if os.path.exists(dest_symlink) or os.path.islink(dest_symlink):
            os.remove(dest_symlink)
        try:
            os.symlink(dest_gov, dest_symlink)
        except Exception:
            pass

    svc_path = "/etc/systemd/system/gpd-win-2-governor.service"
    with open(svc_path, "w") as f:
        f.write(GPD_GOVERNOR_SERVICE)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if is_target_hardware():
        subprocess.run(["systemctl", "enable", "--now", "gpd-win-2-governor.service"], check=True)

    print("\n[SUCCESS] Universal GPD Win 2 Stack Installed!")
    print("--------------------------------------------------------------------")
    print(" * NBFC Service:      nbfc_service.service (Active & Supervised)")
    print(" * Watchdog Binary:   /usr/local/bin/gpd-win-2-power-watchdog")
    print(" * Governor Binary:   /usr/local/bin/gpd-win-2-governor")
    print(" * Low-Power Toggle:  gpd-win-2-lowpower [on|off]")
    print(" * Systemd Unit:      gpd-win-2-governor.service")
    print(" * Sensor Linkage:    Auto-restores topology for coretemp binding")
    print("--------------------------------------------------------------------\n")

# =====================================================================
# ENTRY POINT ROUTER
# =====================================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        run_install()
    else:
        run_governor()
