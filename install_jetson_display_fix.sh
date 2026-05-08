#!/usr/bin/env bash
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"

usage() {
    printf 'Usage: %s [--force-720p] [--use-gdm-greeter]\n' "$0"
}

log() {
    printf '%s\n' "$*"
}

make_scripts_executable() {
    local script_name
    local script_path
    local scripts=(
        jetson_display_diagnose.sh
        jetson_disable_display_sleep.sh
        jetson_force_display_mode.sh
        install_jetson_display_fix.sh
    )

    for script_name in "${scripts[@]}"; do
        script_path="${script_dir}/${script_name}"
        if [ -f "$script_path" ]; then
            chmod +x "$script_path" && log "Made executable: ${script_name}"
        else
            log "Warning: missing script: ${script_path}"
        fi
    done
}

run_script_if_present() {
    local script_name="$1"
    local script_path="${script_dir}/${script_name}"
    local status=0
    shift

    if [ ! -f "$script_path" ]; then
        log "Error: cannot run missing script ${script_path}"
        return 1
    fi

    log ""
    log "Running ${script_name} $*"
    "$script_path" "$@" || status=$?
    if [ "$status" -ne 0 ]; then
        log "Warning: ${script_name} exited with status ${status}."
    fi
    return "$status"
}

print_next_steps() {
    log ""
    log "Next steps:"
    log "1. Run diagnosis with the real monitor connected:"
    log "   ./jetson_display_diagnose.sh"
    log "2. Force a capture-friendly 720p mode if the monitor/capture path is connected:"
    log "   ./jetson_force_display_mode.sh"
    log "3. Shut down the Jetson:"
    log "   sudo shutdown now"
    log "4. Connect Jetson to the Hagibis capture card."
    log "5. Boot the Jetson."
    log "6. In OBS, set the Hagibis Video Capture Device to custom 1280x720, 30 FPS, MJPEG first, then try YUY2 if black."
}

main() {
    local force_720p=0
    local use_gdm_greeter=0
    local script_args=()
    local arg

    for arg in "$@"; do
        case "$arg" in
            --force-720p)
                force_720p=1
                ;;
            --use-gdm-greeter)
                use_gdm_greeter=1
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                log "Unknown option: ${arg}"
                usage
                return 2
                ;;
        esac
    done

    if [ "$use_gdm_greeter" -eq 1 ]; then
        script_args+=(--use-gdm-greeter)
        log "Using GDM greeter fallback. This may prompt for sudo for xrandr/xset access."
    fi

    make_scripts_executable
    run_script_if_present jetson_disable_display_sleep.sh "${script_args[@]}" || true
    run_script_if_present jetson_display_diagnose.sh "${script_args[@]}" || true

    if [ "$force_720p" -eq 1 ]; then
        run_script_if_present jetson_force_display_mode.sh "${script_args[@]}" || true
    else
        log ""
        log "Skipping force mode. Pass --force-720p to run jetson_force_display_mode.sh from the installer."
    fi

    print_next_steps
}

main "$@"
