#!/usr/bin/env python3
"""
Wake Bridge + Jarvis IR remote integration.

This script deliberately uses frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
as the runtime backbone. It imports the original bridge, adds IR-specific command
line options, and patches the local-tool routing layer so transcripts can learn
and replay IR remote buttons before falling through to weather/music/general AI.

The original frdm_uart_context_sender files are not modified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
FRDM_BRIDGE_DIR = PROJECT_ROOT / "frdm_uart_context_sender"

if str(FRDM_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(FRDM_BRIDGE_DIR))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import wake_voice_chat_frdm_bridge as wake_bridge  # noqa: E402
import jarvis_ir_remote as ir_remote  # noqa: E402


CLIENT_VERSION_SUFFIX = "+ir_remote_v1"
DEFAULT_IR_CODES_PATH = Path(os.getenv("JARVIS_IR_CODES_PATH", str(THIS_DIR / "ir_codes.json")))


_original_add_wake_args = wake_bridge.add_wake_args
_original_handle_wake_chat_response = wake_bridge.handle_wake_chat_response
_original_run_self_test = wake_bridge.run_self_test
_original_run_wake_voice_loop = wake_bridge.run_wake_voice_loop
_original_validate_runtime_args = wake_bridge.validate_runtime_args


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def add_ir_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = _original_add_wake_args(parser)
    group = parser.add_argument_group("IR remote local tool")
    group.add_argument("--no-ir-remote", action="store_true", help="Disable Jarvis IR learn/send routing.")
    group.add_argument("--ir-self-test", action="store_true", help="Run IR parser/store self-test after the Wake Bridge self-test.")
    group.add_argument("--ir-codes-path", type=Path, default=DEFAULT_IR_CODES_PATH, help="JSON file used to store learned IR codes.")
    group.add_argument("--ir-rx-pin", type=int, default=_env_int("JARVIS_IR_RX_PIN", ir_remote.DEFAULT_RX_PIN), help="IR receiver GPIO pin.")
    group.add_argument("--ir-tx-pin", type=int, default=_env_int("JARVIS_IR_TX_PIN", ir_remote.DEFAULT_TX_PIN), help="IR transmitter GPIO pin.")
    group.add_argument("--ir-pin-mode", default=os.getenv("JARVIS_IR_PIN_MODE", ir_remote.DEFAULT_PIN_MODE), help="Jetson.GPIO pin mode: BOARD, BCM, TEGRA_SOC, or CVM.")
    group.add_argument("--ir-rx-active-high", action="store_true", help="Use this if the IR receiver idles low and pulses high.")
    group.add_argument("--ir-tx-active-low", action="store_true", help="Use this if the IR transmitter input turns on when driven low.")
    group.add_argument("--ir-frequency-hz", type=int, default=_env_int("JARVIS_IR_FREQUENCY_HZ", ir_remote.DEFAULT_FREQUENCY_HZ), help="IR carrier frequency.")
    group.add_argument("--ir-duty-cycle", type=float, default=_env_float("JARVIS_IR_DUTY_CYCLE", ir_remote.DEFAULT_DUTY_CYCLE), help="IR carrier duty cycle.")
    group.add_argument("--ir-timeout-sec", type=float, default=_env_float("JARVIS_IR_CAPTURE_TIMEOUT_SEC", ir_remote.DEFAULT_CAPTURE_TIMEOUT_SEC), help="Seconds to wait for a remote button during learning.")
    group.add_argument("--ir-idle-gap-us", type=int, default=_env_int("JARVIS_IR_IDLE_GAP_US", ir_remote.DEFAULT_IDLE_GAP_US), help="Idle gap that ends one IR capture.")
    group.add_argument("--ir-max-capture-ms", type=int, default=_env_int("JARVIS_IR_MAX_CAPTURE_MS", ir_remote.DEFAULT_MAX_CAPTURE_MS), help="Hard limit for one IR capture.")
    group.add_argument("--ir-min-timings", type=int, default=_env_int("JARVIS_IR_MIN_TIMINGS", ir_remote.DEFAULT_MIN_TIMINGS), help="Minimum raw timing entries for a valid learned button.")
    group.add_argument("--ir-repeats", type=int, default=_env_int("JARVIS_IR_REPEATS", 1), help="Default transmit repeats for voice-triggered sends.")
    group.add_argument("--ir-dry-run", action="store_true", help="Resolve voice IR send commands without touching GPIO.")
    group.add_argument("--ir-learn-overwrite", action="store_true", help="Allow voice learning to replace an existing label.")
    group.add_argument("--ir-no-learn-beep", action="store_true", help="Do not play the second cue beep before IR capture.")
    group.add_argument("--ir-debug", action="store_true", help="Print IR routing details.")
    return parser


def validate_runtime_args(args: argparse.Namespace) -> bool:
    if not _original_validate_runtime_args(args):
        return False
    if getattr(args, "no_ir_remote", False):
        return True
    if int(getattr(args, "ir_frequency_hz", 0) or 0) <= 0:
        print("ERROR: --ir-frequency-hz must be > 0.")
        return False
    if not (0.01 <= float(getattr(args, "ir_duty_cycle", 0.0) or 0.0) <= 0.99):
        print("ERROR: --ir-duty-cycle must be between 0.01 and 0.99.")
        return False
    if float(getattr(args, "ir_timeout_sec", 0.0) or 0.0) <= 0.0:
        print("ERROR: --ir-timeout-sec must be > 0.")
        return False
    if int(getattr(args, "ir_min_timings", 0) or 0) < 2:
        print("ERROR: --ir-min-timings must be >= 2.")
        return False
    if int(getattr(args, "ir_repeats", 0) or 0) < 1:
        print("ERROR: --ir-repeats must be >= 1.")
        return False
    return True


def build_ir_controller(args: argparse.Namespace) -> ir_remote.IrController:
    store = ir_remote.SignalStore(Path(getattr(args, "ir_codes_path", DEFAULT_IR_CODES_PATH)))
    backend = ir_remote.GpioIrBackend(pin_mode=str(getattr(args, "ir_pin_mode", ir_remote.DEFAULT_PIN_MODE)))
    return ir_remote.IrController(
        store=store,
        backend=backend,
        rx_pin=int(getattr(args, "ir_rx_pin", ir_remote.DEFAULT_RX_PIN)),
        tx_pin=int(getattr(args, "ir_tx_pin", ir_remote.DEFAULT_TX_PIN)),
        receiver_active_low=not bool(getattr(args, "ir_rx_active_high", False)),
        transmitter_active_high=not bool(getattr(args, "ir_tx_active_low", False)),
        frequency_hz=int(getattr(args, "ir_frequency_hz", ir_remote.DEFAULT_FREQUENCY_HZ)),
        duty_cycle=float(getattr(args, "ir_duty_cycle", ir_remote.DEFAULT_DUTY_CYCLE)),
    )


def should_try_ir_route(transcript: str, args: argparse.Namespace) -> tuple[bool, ir_remote.TextIntent]:
    intent = ir_remote.detect_text_intent(transcript)
    if getattr(args, "ir_debug", False):
        print(f"IR intent candidate: {json.dumps(intent.to_json(), ensure_ascii=False)}")
    if intent.action in {"learn", "list"}:
        return True, intent
    if intent.action != "send" or not intent.label:
        return False, intent
    try:
        controller = build_ir_controller(args)
        return controller.store.resolve(intent.label) is not None, intent
    except Exception as exc:
        if getattr(args, "ir_debug", False):
            print(f"IR route resolve skipped: {exc}")
        return False, intent


def ir_reply_from_result(result: dict[str, Any]) -> str:
    action = str(result.get("action") or "")
    ok = bool(result.get("ok", False))
    label = str(result.get("label") or result.get("matched_label") or "").strip()
    if action == "learn":
        if ok:
            if label:
                return f"好，我已經記住「{label}」這個紅外線按鈕。下次你可以直接叫我控制它。"
            return "好，我已經記住這個紅外線按鈕了。"
        return "我剛剛沒有成功讀到紅外線訊號。請把遙控器對準接收模組，再試一次。"
    if action == "send":
        if ok:
            if result.get("dry_run"):
                return f"我找到「{label}」的紅外線訊號了，目前是 dry-run，還沒有真的發射。"
            return f"好，我已經送出「{label}」的紅外線訊號。"
        return "我還沒有學過這個紅外線按鈕。你可以先說：這個按鈕是控制電風扇的。"
    if action == "list":
        signals = result.get("signals") if isinstance(result.get("signals"), list) else []
        labels = [str(item.get("label") or item.get("id") or "").strip() for item in signals if isinstance(item, dict)]
        labels = [item for item in labels if item]
        if labels:
            return "目前我學過：" + "、".join(labels) + "。"
        return "目前我還沒有學過任何紅外線按鈕。"
    return str(result.get("reply") or "").strip() or "IR 指令已處理。"


def execute_ir_route(transcript: str, args: argparse.Namespace, intent: ir_remote.TextIntent) -> dict[str, Any]:
    controller = build_ir_controller(args)
    if intent.action == "learn":
        return controller.learn(
            transcript,
            label=intent.label,
            overwrite=bool(getattr(args, "ir_learn_overwrite", False)),
            beep=not bool(getattr(args, "ir_no_learn_beep", False)),
            beep_device=getattr(args, "beep_device", None),
            timeout_sec=float(getattr(args, "ir_timeout_sec", ir_remote.DEFAULT_CAPTURE_TIMEOUT_SEC)),
            idle_gap_us=int(getattr(args, "ir_idle_gap_us", ir_remote.DEFAULT_IDLE_GAP_US)),
            max_capture_ms=int(getattr(args, "ir_max_capture_ms", ir_remote.DEFAULT_MAX_CAPTURE_MS)),
            min_timings=int(getattr(args, "ir_min_timings", ir_remote.DEFAULT_MIN_TIMINGS)),
        )
    if intent.action == "list":
        return {"ok": True, "action": "list", "handled": True, **controller.store.summary()}
    if intent.action == "send":
        return controller.send(
            transcript,
            label=intent.label,
            repeats=max(1, int(getattr(args, "ir_repeats", 1) or 1)),
            dry_run=bool(getattr(args, "ir_dry_run", False)),
        )
    return {"ok": False, "handled": False, "action": intent.action, "error": "unsupported IR action"}


def handle_ir_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: wake_bridge.RobotUartController,
    timing: wake_bridge.TimingLogger | None,
) -> bool | None:
    if getattr(args, "no_ir_remote", False):
        return None

    transcript = str(response.get("transcript", "") or "").strip()
    if not transcript:
        return None

    should_try, intent = should_try_ir_route(transcript, args)
    if not should_try:
        return None

    try:
        result = execute_ir_route(transcript, args, intent)
    except Exception as exc:
        if intent.action == "send":
            return None
        result = {
            "ok": False,
            "handled": True,
            "action": intent.action,
            "label": intent.label,
            "error": str(exc),
        }

    if not bool(result.get("handled", True)):
        return None

    ok = bool(result.get("ok", False))
    action = str(result.get("action") or intent.action)
    emotion = "happy" if ok else "confused"
    head_motion = "nod" if ok else "shake"
    if action == "list":
        emotion = "curious"
        head_motion = "look_around"

    control = {
        "persistent_state": "unchanged",
        "screen_mode": "unchanged",
        "emotion": emotion,
        "head_motion": head_motion,
        "reason": f"local IR remote {action}",
    }
    reply = ir_reply_from_result(result)
    response["reply"] = reply
    response["control"] = control
    response["emotion"] = wake_bridge.emotion_summary_from_control(control)
    response["ir_remote"] = result

    if getattr(args, "quiet_dialog", False):
        wake_bridge.print_quiet_turn_summary(response)
    else:
        print()
        print("IR remote:")
        print(f"  action : {action}")
        print(f"  ok     : {ok}")
        print(f"  label  : {result.get('label') or result.get('matched_label') or intent.label}")
        print(f"  store  : {result.get('store') or getattr(args, 'ir_codes_path', '')}")
        if result.get("error"):
            print(f"  error  : {result.get('error')}")
        wake_bridge.print_control_summary(control)
        print(f"parsed reply: {reply}")
        print(f"parsed control: {json.dumps(control, ensure_ascii=False)}")
        wake_bridge.voice_chat.print_result(response, verbose_debug=args.debug)

    robot.send_speaking_and_emotion(emotion)
    head_thread, head_stop = robot.start_speaking_head_motion(head_motion)
    if timing is not None:
        timing.mark("IR remote command handled")

    try:
        tts_ok = wake_bridge.speak_reply_and_wait(response, args)
    finally:
        robot.stop_speaking_head_motion(head_thread, head_stop, reason="speaking_head_motion IR stop reset")
    if timing is not None:
        timing.mark("TTS finished or estimated finished")

    wake_bridge.set_post_reply_screen(args, robot, timing, control=control, reason="IR remote reply complete")
    return tts_ok or not getattr(args, "require_tts", False)


def handle_wake_chat_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: wake_bridge.RobotUartController,
    timing: wake_bridge.TimingLogger | None,
    focus_manager: wake_bridge.FocusModeManager | None = None,
    todo_manager: wake_bridge.TodoListManager | None = None,
) -> bool:
    if todo_manager is not None:
        handled = wake_bridge.handle_todo_response(response, args, robot, timing, todo_manager)
        if handled is not None:
            return handled

    if focus_manager is not None:
        handled = wake_bridge.handle_focus_mode_response(response, args, robot, timing, focus_manager)
        if handled is not None:
            return handled

    handled = handle_ir_response(response, args, robot, timing)
    if handled is not None:
        return handled

    return _original_handle_wake_chat_response(response, args, robot, timing, None, None)


def run_self_test() -> int:
    base_status = _original_run_self_test()
    if base_status != 0:
        return base_status
    ir_status = ir_remote.run_self_test()
    if ir_status != 0:
        return ir_status
    with tempfile.TemporaryDirectory() as temp_dir:
        args = argparse.Namespace(
            no_ir_remote=False,
            ir_debug=False,
            ir_codes_path=Path(temp_dir) / "ir_codes.json",
            ir_pin_mode="BOARD",
            ir_rx_pin=18,
            ir_tx_pin=32,
            ir_rx_active_high=False,
            ir_tx_active_low=False,
            ir_frequency_hz=38_000,
            ir_duty_cycle=0.33,
            ir_timeout_sec=10.0,
            ir_idle_gap_us=35_000,
            ir_max_capture_ms=800,
            ir_min_timings=8,
            ir_repeats=1,
            ir_dry_run=True,
            ir_learn_overwrite=False,
            ir_no_learn_beep=False,
            beep_device=None,
        )
        controller = build_ir_controller(args)
        controller.store.add(
            ir_remote.IrSignal(
                id=ir_remote.text_key("電風扇"),
                label="電風扇",
                aliases=ir_remote.build_aliases("電風扇"),
                timings_us=ir_remote.sample_nec_timings(),
            ),
            overwrite=True,
        )
        should_route, send_intent = should_try_ir_route("幫我開電風扇", args)
        if not should_route or send_intent.action != "send":
            raise AssertionError(f"IR send route not detected: {send_intent}")
        normal_route, normal_intent = should_try_ir_route("講個笑話", args)
        if normal_route:
            raise AssertionError(f"general chat should not route to IR: {normal_intent}")
        result = execute_ir_route("幫我開電風扇", args, send_intent)
        if not result.get("ok") or result.get("matched_label") != "電風扇" or not result.get("dry_run"):
            raise AssertionError(f"IR dry-run send failed: {result}")
    print("Wake Bridge + IR integration self-test OK")
    return 0


def run_wake_voice_loop(args: argparse.Namespace) -> int:
    if not getattr(args, "no_ir_remote", False):
        print(
            "IR remote: "
            f"enabled, codes={getattr(args, 'ir_codes_path', DEFAULT_IR_CODES_PATH)}, "
            f"rx={getattr(args, 'ir_rx_pin', ir_remote.DEFAULT_RX_PIN)}, "
            f"tx={getattr(args, 'ir_tx_pin', ir_remote.DEFAULT_TX_PIN)}, "
            f"mode={getattr(args, 'ir_pin_mode', ir_remote.DEFAULT_PIN_MODE)}, "
            f"dry_run={bool(getattr(args, 'ir_dry_run', False))}"
        )
    else:
        print("IR remote: disabled")
    return _original_run_wake_voice_loop(args)


def install_patches() -> None:
    if not str(getattr(wake_bridge, "CLIENT_VERSION", "")).endswith(CLIENT_VERSION_SUFFIX):
        wake_bridge.CLIENT_VERSION = str(getattr(wake_bridge, "CLIENT_VERSION", "wake_bridge")) + CLIENT_VERSION_SUFFIX
    wake_bridge.add_wake_args = add_ir_args
    wake_bridge.validate_runtime_args = validate_runtime_args
    wake_bridge.handle_wake_chat_response = handle_wake_chat_response
    wake_bridge.run_self_test = run_self_test
    wake_bridge.run_wake_voice_loop = run_wake_voice_loop


def main() -> int:
    install_patches()
    if "--ir-self-test" in sys.argv[1:] and "--self-test" not in sys.argv[1:]:
        return ir_remote.run_self_test()
    return wake_bridge.main()


if __name__ == "__main__":
    raise SystemExit(main())
