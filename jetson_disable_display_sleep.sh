#!/usr/bin/env bash
set -u

use_gdm_greeter=0

have_command() {
    command -v "$1" >/dev/null 2>&1
}

log() {
    printf '%s\n' "$*"
}

find_xorg_auth_file() {
    if ! have_command ps; then
        return 1
    fi

    ps -ef 2>/dev/null |
        awk '
            /[X]org/ {
                for (i = 1; i <= NF; i++) {
                    if ($i == "-auth" && (i + 1) <= NF) {
                        print $(i + 1)
                        exit
                    }
                }
            }
        '
}

run_display_command() {
    local auth_file
    local display_name="${DISPLAY:-:0}"

    if [ "$use_gdm_greeter" -eq 1 ]; then
        if ! have_command sudo; then
            log "sudo was not found; cannot use the GDM greeter Xauthority fallback."
            return 127
        fi

        auth_file="$(find_xorg_auth_file)"
        if [ -z "$auth_file" ]; then
            log "Could not find an Xorg -auth file for the GDM greeter fallback."
            return 1
        fi

        sudo env DISPLAY="$display_name" XAUTHORITY="$auth_file" "$@"
        return $?
    fi

    "$@"
}

read_proc_env_value() {
    local env_file="$1"
    local key="$2"

    tr '\0' '\n' <"$env_file" 2>/dev/null |
        awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2); exit}'
}

ensure_display() {
    local user_name="${USER:-}"
    local pid
    local env_file
    local env_display
    local env_xauthority
    local env_dbus
    local candidate

    if [ -z "$user_name" ]; then
        user_name="$(id -un 2>/dev/null || printf '')"
    fi

    if [ -z "${DISPLAY:-}" ]; then
        if [ -n "$user_name" ] && have_command pgrep; then
            while IFS= read -r pid; do
                env_file="/proc/${pid}/environ"
                [ -r "$env_file" ] || continue

                env_display="$(read_proc_env_value "$env_file" DISPLAY)"
                [ -n "$env_display" ] || continue

                env_xauthority="$(read_proc_env_value "$env_file" XAUTHORITY)"
                env_dbus="$(read_proc_env_value "$env_file" DBUS_SESSION_BUS_ADDRESS)"

                export DISPLAY="$env_display"
                [ -n "$env_xauthority" ] && export XAUTHORITY="$env_xauthority"
                [ -n "$env_dbus" ] && export DBUS_SESSION_BUS_ADDRESS="$env_dbus"

                log "Detected X11 environment from process ${pid}."
                log "Using DISPLAY=${DISPLAY}"
                log "Using XAUTHORITY=${XAUTHORITY:-}"
                return 0
            done < <(
                pgrep -u "$user_name" -f 'gnome-session|gnome-shell|xfce4-session|lxsession|mate-session|cinnamon-session|unity-session|x-terminal|gnome-terminal|Xorg|Xwayland' 2>/dev/null
            )
        fi

        export DISPLAY=:0
        log "DISPLAY was empty; using DISPLAY=:0"
    else
        log "Using DISPLAY=${DISPLAY}"
    fi

    if [ -z "${XAUTHORITY:-}" ]; then
        for candidate in \
            "/run/user/$(id -u 2>/dev/null)/gdm/Xauthority" \
            "/run/user/$(id -u 2>/dev/null)/Xauthority" \
            "${HOME:-}/.Xauthority"; do
            if [ -n "$candidate" ] && [ -r "$candidate" ]; then
                export XAUTHORITY="$candidate"
                log "Using XAUTHORITY=${XAUTHORITY}"
                break
            fi
        done
    fi
}

run_xset_command() {
    if ! have_command xset; then
        log "xset was not found; skipping X11 screen saver and DPMS commands."
        return 0
    fi

    log "Running: xset s off"
    run_display_command xset s off || log "Warning: xset s off failed."

    log "Running: xset -dpms"
    run_display_command xset -dpms || log "Warning: xset -dpms failed."

    log "Running: xset s noblank"
    run_display_command xset s noblank || log "Warning: xset s noblank failed."
}

gsettings_schema_has_key() {
    local schema="$1"
    local key="$2"

    gsettings list-schemas 2>/dev/null | grep -qx "$schema" || return 1
    gsettings list-keys "$schema" 2>/dev/null | grep -qx "$key"
}

set_gsetting_if_available() {
    local schema="$1"
    local key="$2"
    local value="$3"

    if ! have_command gsettings; then
        log "gsettings was not found; skipping GNOME idle and lock settings."
        return 0
    fi

    if ! gsettings_schema_has_key "$schema" "$key"; then
        log "Skipping missing gsettings key: ${schema} ${key}"
        return 0
    fi

    log "Running: gsettings set ${schema} ${key} ${value}"
    gsettings set "$schema" "$key" "$value" || log "Warning: could not set ${schema} ${key}."
}

disable_gnome_idle_settings() {
    set_gsetting_if_available org.gnome.desktop.session idle-delay 0
    set_gsetting_if_available org.gnome.desktop.screensaver lock-enabled false
    set_gsetting_if_available org.gnome.desktop.screensaver idle-activation-enabled false
}

write_autostart_entry() {
    local home_dir="${HOME:-}"
    local autostart_dir
    local autostart_file

    if [ -z "$home_dir" ]; then
        log "HOME is not set; cannot create the autostart entry."
        return 1
    fi

    autostart_dir="${home_dir}/.config/autostart"
    autostart_file="${autostart_dir}/disable-display-sleep.desktop"

    mkdir -p "$autostart_dir" || {
        log "Could not create ${autostart_dir}."
        return 1
    }

    cat >"$autostart_file" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Disable Display Sleep
Comment=Disable X11 screen saver blanking and DPMS after login
Exec=sh -c 'xset s off; xset -dpms; xset s noblank'
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP

    log "Autostart entry written to ${autostart_file}"
}

print_xset_status() {
    local xset_output=""

    if ! have_command xset; then
        return 0
    fi

    log ""
    log "Current xset q status:"
    xset_output="$(run_display_command xset q 2>&1)" || {
        printf '%s\n' "$xset_output"
        log "Warning: xset q failed."
        log "If this says authorization is required, run this from the Jetson desktop Terminal or make sure XAUTHORITY points to the logged-in desktop user's Xauthority file."
        return 0
    }

    printf '%s\n' "$xset_output"

    if printf '%s\n' "$xset_output" | grep -qi 'DPMS is Disabled'; then
        log "Verification: DPMS is disabled."
    else
        log "Verification: DPMS does not appear disabled. Check DISPLAY and X session access."
    fi
}

parse_args() {
    local arg

    for arg in "$@"; do
        case "$arg" in
            --use-gdm-greeter)
                use_gdm_greeter=1
                ;;
            -h|--help)
                log "Usage: $0 [--use-gdm-greeter]"
                return 0
                ;;
            *)
                log "Unknown option: ${arg}"
                log "Usage: $0 [--use-gdm-greeter]"
                return 2
                ;;
        esac
    done
}

main() {
    parse_args "$@" || return $?
    if [ "$use_gdm_greeter" -eq 1 ]; then
        log "Using sudo GDM greeter Xauthority fallback for xset commands."
    fi
    ensure_display
    run_xset_command
    disable_gnome_idle_settings
    write_autostart_entry
    print_xset_status
    log ""
    log "Done. These settings are safe to run repeatedly."
}

main "$@"
