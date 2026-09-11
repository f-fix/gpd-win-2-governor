#include <stdio.h>
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
    log_msg("Caught termination signal (%d). Exiting cleanly.", sig);
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

    log_msg("Handoff to rootfs: Preserving user brightness (%d) and closing descriptors.", user_brightness);
    set_brightness(user_brightness);
    for (int i = 0; i < num_fds; i++) {
        if (fds[i].fd >= 0) close(fds[i].fd);
    }
    if (log_fd >= 0) close(log_fd);
    if (kmsg_fd >= 0) close(kmsg_fd);
    return 0;
}
