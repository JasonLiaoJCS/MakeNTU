#include "command_parser.h"
#include "face_controller.h"
#include "motion_controller.h"
#include "motion_profiles.h"
#include "uart_protocol.h"
#include <stdbool.h>
#include <stdio.h>

/*
 * This file is intentionally a portable skeleton.
 *
 * TODO for MCUXpresso:
 * - Replace platform_uart_read_line() with your LPUART/USART line receiver.
 * - Replace platform_uart_write_line() with your UART transmit function.
 * - Replace platform_delay_ms() with SDK_DelayAtLeastUs(), SysTick delay,
 *   FreeRTOS vTaskDelay(), or your board timing function.
 * - Replace servo_driver_stub.c with servo_driver_mcxn947.c using CTIMER,
 *   SCTimer/PWM, FlexPWM, or another SDK PWM example available for MCXN947.
 */

static void platform_board_init(void)
{
    printf("[platform] board init placeholder\n");
}

static bool platform_uart_read_line(char *buffer, unsigned buffer_size)
{
    (void)buffer;
    (void)buffer_size;
    /* TODO: fill buffer with one newline-terminated UART command and return true. */
    return false;
}

static void platform_uart_write_line(const char *line)
{
    printf("%s\n", line);
}

static void platform_delay_ms(uint32_t ms)
{
    (void)ms;
    /* TODO: connect to a real delay function on FRDM-MCXN947. */
}

static void send_ack(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_ack(tx, sizeof(tx), seq);
    platform_uart_write_line(tx);
}

static void send_nack(int seq, const char *code, const char *message)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_nack(tx, sizeof(tx), seq, code, message);
    platform_uart_write_line(tx);
}

static void send_pong(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    uart_protocol_build_pong(tx, sizeof(tx), seq);
    platform_uart_write_line(tx);
}

static void send_status(int seq)
{
    char tx[UART_PROTOCOL_MAX_PACKET_LEN];
    char status[96];
    snprintf(status,
             sizeof(status),
             "OK,face=%s,busy=%d",
             face_get_current_id(),
             motion_controller_is_busy() ? 1 : 0);
    uart_protocol_build_status(tx, sizeof(tx), seq, status);
    platform_uart_write_line(tx);
}

static void execute_after_ack(const RobotCommand *cmd)
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

static bool validate_command_before_ack(const RobotCommand *cmd)
{
    if (motion_controller_is_busy() &&
        (cmd->type == COMMAND_TYPE_ACT || cmd->type == COMMAND_TYPE_EMO ||
         cmd->type == COMMAND_TYPE_TEST || cmd->type == COMMAND_TYPE_RESET)) {
        send_nack(cmd->seq, "BUSY", "motion controller is busy");
        return false;
    }

    if (cmd->type == COMMAND_TYPE_ACT || cmd->type == COMMAND_TYPE_TEST) {
        if (!motion_profile_find_by_motion_id(cmd->motion_id)) {
            send_nack(cmd->seq, "UNKNOWN_MOTION", "motion id not found");
            return false;
        }
    }

    if (cmd->type == COMMAND_TYPE_EMO) {
        if (!motion_profile_find_by_emotion(cmd->emotion)) {
            send_nack(cmd->seq, "UNKNOWN_EMOTION", "emotion not found");
            return false;
        }
    }
    return true;
}

int main(void)
{
    char rx_line[UART_PROTOCOL_MAX_PACKET_LEN];
    RobotCommand cmd;

    platform_board_init();
    face_controller_init();
    motion_controller_set_delay_fn(platform_delay_ms);
    motion_controller_init();

    while (1) {
        if (!platform_uart_read_line(rx_line, sizeof(rx_line))) {
            continue;
        }

        ParserResult result = command_parser_parse(rx_line, &cmd);
        if (result != PARSER_OK) {
            send_nack(0, parser_result_to_error_code(result), parser_result_to_message(result));
            continue;
        }

        if (!validate_command_before_ack(&cmd)) {
            continue;
        }

        if (cmd.type == COMMAND_TYPE_PING) {
            send_pong(cmd.seq);
            continue;
        }
        if (cmd.type == COMMAND_TYPE_STATUS) {
            send_status(cmd.seq);
            continue;
        }

        send_ack(cmd.seq);
        execute_after_ack(&cmd);
    }
}

