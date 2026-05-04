#include "smonitor_emotion_bridge.h"

#include "command_parser.h"
#include "face_controller.h"
#include "motion_controller.h"
#include "motion_profiles.h"
#include "uart_protocol.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef PRINTF
#define PRINTF printf
#endif

static void bridge_delay_ms(uint32_t ms)
{
    /*
     * TODO for MCUXpresso:
     * Replace this with SDK_DelayAtLeastUs(ms * 1000U, SDK_DEVICE_MAXIMUM_CPU_CLOCK_FREQUENCY)
     * or your RTOS delay. Leaving it empty keeps parser tests non-blocking.
     */
    (void)ms;
}

static char *trim_space(char *text)
{
    while (*text == ' ' || *text == '\t') {
        ++text;
    }

    size_t len = strlen(text);
    while (len > 0U) {
        char ch = text[len - 1U];
        if (ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') {
            break;
        }
        text[len - 1U] = '\0';
        --len;
    }

    return text;
}

static void bridge_print_packet(const char *line)
{
    PRINTF("%s\r\n", line);
}

static void bridge_send_ack(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_ack(tx, sizeof(tx), seq);
    bridge_print_packet(tx);
}

static void bridge_send_nack(int seq, const char *code, const char *message)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_nack(tx, sizeof(tx), seq, code, message);
    bridge_print_packet(tx);
}

static void bridge_send_pong(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_pong(tx, sizeof(tx), seq);
    bridge_print_packet(tx);
}

static void bridge_send_status(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    char status[96];
    snprintf(status,
             sizeof(status),
             "OK,face=%s,busy=%d",
             face_get_current_id(),
             motion_controller_is_busy() ? 1 : 0);
    uart_protocol_build_status(tx, sizeof(tx), seq, status);
    bridge_print_packet(tx);
}

static bool bridge_validate_command_before_ack(const RobotCommand *cmd)
{
    if (motion_controller_is_busy() &&
        (cmd->type == COMMAND_TYPE_ACT || cmd->type == COMMAND_TYPE_EMO ||
         cmd->type == COMMAND_TYPE_TEST || cmd->type == COMMAND_TYPE_RESET)) {
        bridge_send_nack(cmd->seq, "BUSY", "motion controller is busy");
        return false;
    }

    if (cmd->type == COMMAND_TYPE_ACT || cmd->type == COMMAND_TYPE_TEST) {
        if (!motion_profile_find_by_motion_id(cmd->motion_id)) {
            bridge_send_nack(cmd->seq, "UNKNOWN_MOTION", "motion id not found");
            return false;
        }
    }

    if (cmd->type == COMMAND_TYPE_EMO) {
        if (!motion_profile_find_by_emotion(cmd->emotion)) {
            bridge_send_nack(cmd->seq, "UNKNOWN_EMOTION", "emotion not found");
            return false;
        }
    }

    return true;
}

static void bridge_execute_after_ack(const RobotCommand *cmd)
{
    switch (cmd->type) {
    case COMMAND_TYPE_ACT:
        face_set_face_id(cmd->face_id);
        motion_execute_motion_id(cmd->motion_id,
                                 cmd->roll_bias,
                                 cmd->pitch_bias,
                                 cmd->speed,
                                 cmd->hold_ms);
        break;
    case COMMAND_TYPE_EMO:
        {
            const MotionProfile *profile = motion_profile_find_by_emotion(cmd->emotion);
            if (profile) {
                face_set_face_id(profile->face_id);
                motion_execute_profile(profile, 0, 0, profile->default_speed, profile->default_hold_ms);
            }
        }
        break;
    case COMMAND_TYPE_TEST:
        face_reset_neutral();
        motion_execute_motion_id(cmd->motion_id, 0, 0, 25, 500);
        break;
    case COMMAND_TYPE_RESET:
        face_reset_neutral();
        motion_reset_to_center();
        break;
    default:
        break;
    }
}

void EmotionRobotInit(void)
{
    face_controller_init();
    motion_controller_set_delay_fn(bridge_delay_ms);
    motion_controller_init();
    PRINTF("[EmotionRobot] init done\r\n");
}

void EmotionRobotCommand(char *pValue)
{
    RobotCommand cmd;
    char *packet = pValue ? trim_space(pValue) : 0;

    if (!packet || packet[0] == '\0') {
        bridge_send_nack(0, "BAD_FIELD_COUNT", "missing packet after ERobot");
        return;
    }

    ParserResult result = command_parser_parse(packet, &cmd);
    if (result != PARSER_OK) {
        bridge_send_nack(0, parser_result_to_error_code(result), parser_result_to_message(result));
        return;
    }

    if (!bridge_validate_command_before_ack(&cmd)) {
        return;
    }

    if (cmd.type == COMMAND_TYPE_PING) {
        bridge_send_pong(cmd.seq);
        return;
    }

    if (cmd.type == COMMAND_TYPE_STATUS) {
        bridge_send_status(cmd.seq);
        return;
    }

    bridge_send_ack(cmd.seq);
    bridge_execute_after_ack(&cmd);
}
