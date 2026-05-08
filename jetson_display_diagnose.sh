#!/usr/bin/env bash
set -u

use_gdm_greeter=0

timestamp="$(date +"%Y%m%d_%H%M%S" 2>/dev/null || echo "unknown_time")"
log_file="jetson_display_log_${timestamp}.txt"

have_command() {
    command -v "$1" >/dev/null 2>&1
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
            printf 'sudo was not found; cannot use the GDM greeter Xauthority fallback.\n'
            return 127
        fi

        auth_file="$(find_xorg_auth_file)"
        if [ -z "$auth_file" ]; then
            printf 'Could not find an Xorg -auth file for the GDM greeter fallback.\n'
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

discover_x11_environment() {
    local original_display="${DISPLAY:-}"
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

    if [ -z "${DISPLAY:-}" ] && [ -n "$user_name" ] && have_command pgrep; then
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

            printf 'Detected X11 environment from process %s.\n' "$pid"
            printf 'Original DISPLAY=%s\n' "$original_display"
            printf 'Effective DISPLAY=%s\n' "${DISPLAY:-}"
            printf 'Effective XAUTHORITY=%s\n' "${XAUTHORITY:-}"
            return 0
        done < <(
            pgrep -u "$user_name" -f 'gnome-session|gnome-shell|xfce4-session|lxsession|mate-session|cinnamon-session|unity-session|x-terminal|gnome-terminal|Xorg|Xwayland' 2>/dev/null
        )
    fi

    if [ -z "${DISPLAY:-}" ]; then
        export DISPLAY=:0
    fi

    if [ -z "${XAUTHORITY:-}" ]; then
        for candidate in \
            "/run/user/$(id -u 2>/dev/null)/gdm/Xauthority" \
            "/run/user/$(id -u 2>/dev/null)/Xauthority" \
            "${HOME:-}/.Xauthority"; do
            if [ -n "$candidate" ] && [ -r "$candidate" ]; then
                export XAUTHORITY="$candidate"
                break
            fi
        done
    fi

    printf 'Original DISPLAY=%s\n' "$original_display"
    printf 'Effective DISPLAY=%s\n' "${DISPLAY:-}"
    printf 'Effective XAUTHORITY=%s\n' "${XAUTHORITY:-}"
}

print_x11_access_diagnostics() {
    local uid
    local candidate
    local xorg_auth_paths=""

    uid="$(id -u 2>/dev/null || printf '')"
    if have_command ps; then
        xorg_auth_paths="$(
            ps -ef 2>/dev/null |
                awk '
                    /[X]org/ {
                        for (i = 1; i <= NF; i++) {
                            if ($i == "-auth" && (i + 1) <= NF) {
                                print $(i + 1)
                            }
                        }
                    }
                ' |
                sort -u
        )"
    fi

    printf 'User: %s\n' "$(id -un 2>/dev/null || printf 'unknown')"
    printf 'UID: %s\n' "${uid:-unknown}"

    printf '\nPossible Xauthority files:\n'
    for candidate in \
        "${XAUTHORITY:-}" \
        "/run/user/${uid}/gdm/Xauthority" \
        "/run/user/${uid}/Xauthority" \
        "${HOME:-}/.Xauthority" \
        "/var/run/lightdm/root/:0"; do
        [ -n "$candidate" ] || continue
        if [ -e "$candidate" ]; then
            ls -l "$candidate" 2>/dev/null || printf '%s exists but is not listable\n' "$candidate"
        else
            printf '%s not found\n' "$candidate"
        fi
    done

    if [ -n "$xorg_auth_paths" ]; then
        printf '\nXorg -auth files discovered from running Xorg processes:\n'
        while IFS= read -r candidate; do
            [ -n "$candidate" ] || continue
            if [ -e "$candidate" ]; then
                ls -l "$candidate" 2>/dev/null || printf '%s exists but is not listable by this user\n' "$candidate"
            else
                printf '%s not found\n' "$candidate"
            fi
        done <<EOF
$xorg_auth_paths
EOF
    fi

    printf '\nX server socket files:\n'
    ls -l /tmp/.X11-unix 2>/dev/null || printf '/tmp/.X11-unix not found or not readable\n'

    printf '\nXorg/Xwayland processes:\n'
    if have_command ps; then
        ps -ef | awk '/[X]org|[X]wayland|[X]wayland|[g]nome-session|[g]nome-shell|[l]ightdm|[g]dm/ {print}'
    else
        printf 'ps was not found.\n'
    fi

    printf '\nLogin sessions:\n'
    if have_command loginctl; then
        loginctl list-sessions 2>/dev/null || printf 'loginctl list-sessions failed.\n'
    else
        printf 'loginctl was not found.\n'
    fi

    printf '\nX11 access note:\n'
    if have_command ps && ps -ef | grep -q '[g]nome-session.*gdm/greeter'; then
        printf 'A GDM greeter session appears to be running. That is the login screen, not necessarily the user desktop.\n'
    fi
    if have_command ps && ! ps -u "$(id -u 2>/dev/null)" -f 2>/dev/null | grep -q '[g]nome-session\|[g]nome-shell\|[x]fce4-session\|[l]xsession'; then
        printf 'No obvious graphical desktop session owned by this user was found. SSH/TTY shells usually cannot run xrandr without the desktop session authority.\n'
    fi
}

print_section() {
    printf '\n==== %s ====\n' "$1"
}

detect_connected_outputs() {
    awk '$2 == "connected" {print $1}'
}

choose_preferred_output() {
    local connected_outputs="$1"

    if printf '%s\n' "$connected_outputs" | grep -qx 'DP-1'; then
        printf 'DP-1\n'
        return 0
    fi

    printf '%s\n' "$connected_outputs" | awk 'NF {print; exit}'
}

print_modes_for_output() {
    local xrandr_data="$1"
    local output_name="$2"

    if [ -z "$output_name" ]; then
        printf 'No preferred output was found, so no mode list can be shown.\n'
        return 0
    fi

    printf '%s\n' "$xrandr_data" | awk -v out="$output_name" '
        $1 == out && $2 == "connected" {in_output=1; print; next}
        in_output && /^[^[:space:]]/ {exit}
        in_output {print}
    '
}

print_drm_status() {
    local status_file
    local found=0

    shopt -s nullglob
    for status_file in /sys/class/drm/*/status; do
        found=1
        printf '%s: ' "$status_file"
        if ! cat "$status_file" 2>/dev/null; then
            printf 'unreadable\n'
        fi
    done
    shopt -u nullglob

    if [ "$found" -eq 0 ]; then
        printf 'No /sys/class/drm/*/status files were found.\n'
    fi
}

print_xset_status() {
    if ! have_command xset; then
        printf 'xset was not found; cannot report DPMS or screen saver status.\n'
        return 0
    fi

    if ! run_display_command xset q; then
        printf 'xset q failed. DISPLAY may be wrong or the X session may not be available.\n'
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
                printf 'Usage: %s [--use-gdm-greeter]\n' "$0"
                return 0
                ;;
            *)
                printf 'Unknown option: %s\n' "$arg"
                printf 'Usage: %s [--use-gdm-greeter]\n' "$0"
                return 2
                ;;
        esac
    done
}

main() {
    local xrandr_output=""
    local xrandr_status=0
    local connected_outputs=""
    local preferred_output=""

    printf 'Jetson display diagnosis log\n'
    printf 'Log file: %s\n' "$log_file"

    parse_args "$@" || return $?
    if [ "$use_gdm_greeter" -eq 1 ]; then
        printf 'Using sudo GDM greeter Xauthority fallback for xrandr/xset commands.\n'
    fi

    print_section "Date And Time"
    date

    print_section "Environment"
    discover_x11_environment

    print_section "X11 Access Diagnostics"
    print_x11_access_diagnostics

    print_section "xrandr --query"
    if have_command xrandr; then
        xrandr_output="$(run_display_command xrandr --query 2>&1)" || xrandr_status=$?
        printf '%s\n' "$xrandr_output"
    else
        xrandr_status=127
        printf 'xrandr was not found; cannot query X11 display outputs.\n'
    fi

    print_section "Connected Outputs"
    if [ "$xrandr_status" -eq 0 ]; then
        connected_outputs="$(printf '%s\n' "$xrandr_output" | detect_connected_outputs)"
        if [ -n "$connected_outputs" ]; then
            printf '%s\n' "$connected_outputs"
        else
            printf 'No connected outputs reported by xrandr.\n'
        fi
    else
        printf 'Could not detect connected outputs because xrandr failed with status %s.\n' "$xrandr_status"
    fi

    print_section "Preferred Output"
    if [ -n "$connected_outputs" ]; then
        preferred_output="$(choose_preferred_output "$connected_outputs")"
        printf 'Preferred output: %s\n' "$preferred_output"
        if [ "$preferred_output" = "DP-1" ]; then
            printf 'Reason: DP-1 is connected and is preferred for this Jetson.\n'
        else
            printf 'Reason: DP-1 is not connected; using the first connected output.\n'
        fi
    else
        printf 'Preferred output: none\n'
    fi

    print_section "Supported Modes For Preferred Output"
    print_modes_for_output "$xrandr_output" "$preferred_output"

    print_section "DRM Connector Status"
    print_drm_status

    print_section "DPMS And Screen Saver Status"
    print_xset_status

    print_section "Display Connection Summary"
    if [ "$xrandr_status" -eq 0 ] && [ -n "$connected_outputs" ]; then
        printf 'Display appears CONNECTED to X11. Preferred output is %s.\n' "$preferred_output"
        printf 'If OBS is still black through the capture card, try disabling display sleep and forcing 1280x720.\n'
    elif [ "$xrandr_status" -eq 0 ]; then
        printf 'Display appears DISCONNECTED to X11.\n'
        printf 'If this happens only with the Hagibis capture card attached, suspect EDID or hot-plug detection.\n'
    else
        printf 'Display connection could not be determined because xrandr did not run successfully.\n'
        printf 'If you see "Authorization required", run this from the Jetson desktop Terminal or use the correct DISPLAY/XAUTHORITY for the logged-in desktop session.\n'
    fi
}

if have_command tee; then
    main "$@" 2>&1 | tee "$log_file"
else
    main "$@"
    printf '\ntee was not found, so output was not saved to %s.\n' "$log_file"
fi
