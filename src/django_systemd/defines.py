import typing as t
from pathlib import Path

from enum_properties import StrEnumProperties


class SystemdUnitType(StrEnumProperties):
    description: str

    # fmt: off
    SERVICE   = "service",   "Manages system services and daemons, including their startup, shutdown, and runtime behavior."
    SOCKET    = "socket",    "Implements socket-based activation, starting a service when traffic arrives on a specific socket."
    TARGET    = "target",    "Groups other units together to define synchronization points or system states (e.g., multi-user.target, graphical.target)"
    TIMER     = "timer",     "Functions as a cron-like job scheduler, activating a service unit on a real-time or monotonic timer."
    PATH      = "path",      "Monitors files or directories and can activate a service unit when a change or access occurs."
    MOUNT     = "mount",     "Manages filesystem mount points."
    AUTOMOUNT = "automount", "Provides on-demand mounting of filesystems, similar to traditional automounters."
    SWAP      = "swap",      "Manages swap devices and files."
    DEVICE    = "device",    "Controls access to kernel-recognized devices and can activate other units when a specific device becomes available."
    SCOPE     = "scope",     "Manages externally created processes that were not started by systemd itself (e.g., user sessions from a login manager)."
    SNAPSHOT  = "snapshot",  "Saves the current state of the systemd manager and all running units, allowing the system to be restored to that state later."
    SLICE     = "slice",     "Organizes units into a hierarchical tree for resource management (CPU, memory, etc.) using control groups (cgroups)."
    # fmt: on


class SystemdStartupType(StrEnumProperties):
    description: str

    # fmt: off
    SIMPLE        = "simple", "systemd considers the service started immediately after the main process is forked."
    EXEC          = "exec", "Similar to simple, but systemd waits until the main service binary has successfully executed before proceeding."
    FORKING       = "forking", "For traditional UNIX daemons that fork into the background; systemd waits for the parent process to exit and the child to become the main process."
    ONESHOT       = "oneshot", "A service that runs a single command and then exits. Often used with RemainAfterExit=yes for actions that change system state."
    DBUS          = "dbus", "The service is considered started when it acquires a specific name on the D-Bus system bus."
    NOTIFY        = "notify", "Similar to exec, but the service sends a notification message to systemd when it is ready."
    NOTIFY_RELOAD = "notify-reload", "Similar to notify, but the service also notifies systemd when a reload operation is complete."
    IDLE          = "idle", "Delays execution of the service binary until all other active jobs are dispatched, primarily to improve console output readability. "
    # fmt: on


class SystemdRestartType(StrEnumProperties):
    description: str

    # fmt: off
    NO           = "no",          "No automatic restarts."
    ON_SUCCESS   = "on-success",  "Restarts only if the service exits cleanly (exit code 0 or specific signals)."
    ON_FAILURE   = "on-failure",  "Restarts on non-zero exit code, termination by certain signals, or timeout. This is a common setting for general services."
    ON_ABNORMAL  = "on-abnormal", "Restarts if terminated by a signal or timeout, but not for normal error exit."
    ON_WATCHDOG  = "on-watchdog", "Restarts only if the watchdog timeout is triggered."
    ON_ABORT     = "on-abort",    "Restarts on exit due to an uncaught signal not defined as clean."
    ALWAYS       = "always",      "Restarts regardless of exit status, signal termination, or timeout. "
    # fmt: on


class SystemdScope(StrEnumProperties):
    location: t.List[Path]
    description: str

    # fmt: off
    USER   = "user",   [Path("~/.config/systemd/user")], "Units are installed for the current user session."
    SYSTEM = "system", [Path("/etc/systemd/system")], "Units are installed system-wide."
    # fmt: on
