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

output_block() {
    local xrandr_data="$1"
    local output_name="$2"

    printf '%s\n' "$xrandr_data" | awk -v out="$output_name" '
        $1 == out && $2 == "connected" {in_output=1; print; next}
        in_output && /^[^[:space:]]/ {exit}
        in_output {print}
    '
}

print_available_modes() {
    local xrandr_data="$1"
    local output_name="$2"

    log "Available modes for ${output_name}:"
    output_block "$xrandr_data" "$output_name"
}

clean_rate_token() {
    printf '%s' "$1" | tr -cd '0-9.'
}

rate_matches() {
    local actual="$1"
    local requested="$2"

    awk -v actual="$actual" -v requested="$requested" '
        BEGIN {
            if (actual == "" || requested == "") {
                exit 1
            }
            diff = actual - requested
            if (diff < 0) {
                diff = -diff
            }
            exit(diff <= 0.02 ? 0 : 1)
        }
    '
}

find_supported_rate() {
    local xrandr_data="$1"
    local output_name="$2"
    local mode_name="$3"
    local requested_rate="$4"
    local raw_rate
    local clean_rate

    while IFS= read -r raw_rate; do
        clean_rate="$(clean_rate_token "$raw_rate")"
        if rate_matches "$clean_rate" "$requested_rate"; then
            printf '%s\n' "$clean_rate"
            return 0
        fi
    done < <(
        output_block "$xrandr_data" "$output_name" |
            awk -v mode="$mode_name" '$1 == mode {for (i = 2; i <= NF; i++) print $i}'
    )

    return 1
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
    local xrandr_data=""
    local xrandr_status=0
    local connected_outputs=""
    local output_name=""
    local selected_mode=""
    local selected_rate=""
    local candidate=""
    local mode_name=""
    local requested_rate=""
    local matched_rate=""
    local apply_status=0
    local candidates=(
        "1280x720 60"
        "1280x720 59.94"
        "1280x720 50"
        "1920x1080 60"
        "1920x1080 59.94"
    )

    parse_args "$@" || return $?
    if [ "$use_gdm_greeter" -eq 1 ]; then
        log "Using sudo GDM greeter Xauthority fallback for xrandr commands."
    fi

    ensure_display

    if ! have_command xrandr; then
        log "Error: xrandr was not found. Cannot detect or force display modes."
        return 127
    fi

    xrandr_data="$(run_display_command xrandr --query 2>&1)" || xrandr_status=$?
    log "Current xrandr state:"
    printf '%s\n' "$xrandr_data"
    log ""

    if [ "$xrandr_status" -ne 0 ]; then
        log "Error: xrandr failed with status ${xrandr_status}. Check DISPLAY and X session access."
        log "If this says authorization is required, run from the Jetson desktop Terminal or find the correct XAUTHORITY for the logged-in desktop session."
        return "$xrandr_status"
    fi

    connected_outputs="$(printf '%s\n' "$xrandr_data" | detect_connected_outputs)"
    if [ -z "$connected_outputs" ]; then
        log "Error: no connected outputs were reported by xrandr."
        log "If the Hagibis capture card is attached, this points to EDID or hot-plug detection."
        return 2
    fi

    output_name="$(choose_preferred_output "$connected_outputs")"
    log "Selected output: ${output_name}"

    for candidate in "${candidates[@]}"; do
        mode_name="${candidate% *}"
        requested_rate="${candidate##* }"
        matched_rate="$(find_supported_rate "$xrandr_data" "$output_name" "$mode_name" "$requested_rate")" || matched_rate=""

        if [ -n "$matched_rate" ]; then
            selected_mode="$mode_name"
            selected_rate="$matched_rate"
            break
        fi
    done

    if [ -z "$selected_mode" ] || [ -z "$selected_rate" ]; then
        log "Error: none of the preferred capture-card-friendly modes were found."
        print_available_modes "$xrandr_data" "$output_name"
        log "No display mode was forced."
        return 3
    fi

    log "Selected mode: ${selected_mode} @ ${selected_rate} Hz"
    log "Running: xrandr --output ${output_name} --mode ${selected_mode} --rate ${selected_rate}"
    run_display_command xrandr --output "$output_name" --mode "$selected_mode" --rate "$selected_rate" || apply_status=$?

    if [ "$apply_status" -ne 0 ]; then
        log "Error: xrandr failed while applying ${selected_mode} @ ${selected_rate} Hz."
        return "$apply_status"
    fi

    log ""
    log "Mode applied successfully. Updated xrandr state:"
    run_display_command xrandr --query || log "Warning: could not query xrandr after applying the mode."
}

main "$@"
