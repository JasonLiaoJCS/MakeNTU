#include "command_parser.h"
#include "safety.h"
#include "uart_protocol.h"
#include <stdlib.h>
#include <string.h>

static void clear_command(RobotCommand *cmd)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->type = COMMAND_TYPE_NONE;
}

static int parse_int(const char *text, int *out_value)
{
    char *end = 0;
    long value = strtol(text, &end, 10);
    if (end == text || *end != '\0') {
        return 0;
    }
    if (value < -32768L || value > 32767L) {
        return 0;
    }
    *out_value = (int)value;
    return 1;
}

static void copy_field(char *dst, size_t dst_size, const char *src)
{
    if (dst_size == 0U) {
        return;
    }
    strncpy(dst, src, dst_size - 1U);
    dst[dst_size - 1U] = '\0';
}

ParserResult command_parser_parse(const char *line, RobotCommand *out_cmd)
{
    char payload[UART_PROTOCOL_MAX_PAYLOAD_LEN];
    char *tokens[12];
    int token_count = 0;

    if (!out_cmd) {
        return PARSER_VALUE_OUT_OF_RANGE;
    }
    clear_command(out_cmd);

    if (!uart_protocol_validate_packet(line, payload, sizeof(payload))) {
        return PARSER_BAD_CHECKSUM;
    }

    char *tok = strtok(payload, ",");
    while (tok && token_count < (int)(sizeof(tokens) / sizeof(tokens[0]))) {
        tokens[token_count++] = tok;
        tok = strtok(0, ",");
    }

    if (token_count < 2) {
        return PARSER_BAD_FIELD_COUNT;
    }
    if (!parse_int(tokens[1], &out_cmd->seq) || out_cmd->seq < 0) {
        return PARSER_VALUE_OUT_OF_RANGE;
    }

    if (strcmp(tokens[0], "ACT") == 0) {
        if (token_count != 9) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_ACT;
        copy_field(out_cmd->mode, sizeof(out_cmd->mode), tokens[2]);
        copy_field(out_cmd->face_id, sizeof(out_cmd->face_id), tokens[3]);
        copy_field(out_cmd->motion_id, sizeof(out_cmd->motion_id), tokens[4]);
        if (!parse_int(tokens[5], &out_cmd->roll_bias) ||
            !parse_int(tokens[6], &out_cmd->pitch_bias) ||
            !parse_int(tokens[7], &out_cmd->speed) ||
            !parse_int(tokens[8], &out_cmd->hold_ms)) {
            return PARSER_VALUE_OUT_OF_RANGE;
        }
        if (!safety_validate_bias(out_cmd->roll_bias) ||
            !safety_validate_bias(out_cmd->pitch_bias) ||
            !safety_validate_speed(out_cmd->speed) ||
            !safety_validate_hold_ms(out_cmd->hold_ms)) {
            return PARSER_VALUE_OUT_OF_RANGE;
        }
        return PARSER_OK;
    }

    if (strcmp(tokens[0], "EMO") == 0) {
        if (token_count != 3) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_EMO;
        copy_field(out_cmd->emotion, sizeof(out_cmd->emotion), tokens[2]);
        return PARSER_OK;
    }

    if (strcmp(tokens[0], "TEST") == 0) {
        if (token_count != 3) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_TEST;
        copy_field(out_cmd->motion_id, sizeof(out_cmd->motion_id), tokens[2]);
        return PARSER_OK;
    }

    if (strcmp(tokens[0], "RESET") == 0) {
        if (token_count != 2) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_RESET;
        return PARSER_OK;
    }

    if (strcmp(tokens[0], "STATUS") == 0) {
        if (token_count != 2) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_STATUS;
        return PARSER_OK;
    }

    if (strcmp(tokens[0], "PING") == 0) {
        if (token_count != 2) {
            return PARSER_BAD_FIELD_COUNT;
        }
        out_cmd->type = COMMAND_TYPE_PING;
        return PARSER_OK;
    }

    return PARSER_UNKNOWN_CMD;
}

const char *parser_result_to_error_code(ParserResult result)
{
    switch (result) {
    case PARSER_BAD_CHECKSUM:
        return "BAD_CHECKSUM";
    case PARSER_UNKNOWN_CMD:
        return "UNKNOWN_CMD";
    case PARSER_BAD_FIELD_COUNT:
        return "BAD_FIELD_COUNT";
    case PARSER_VALUE_OUT_OF_RANGE:
        return "VALUE_OUT_OF_RANGE";
    case PARSER_OK:
        return "OK";
    default:
        return "UNKNOWN_CMD";
    }
}

const char *parser_result_to_message(ParserResult result)
{
    switch (result) {
    case PARSER_BAD_CHECKSUM:
        return "checksum mismatch or malformed packet";
    case PARSER_UNKNOWN_CMD:
        return "unknown command";
    case PARSER_BAD_FIELD_COUNT:
        return "wrong number of fields";
    case PARSER_VALUE_OUT_OF_RANGE:
        return "field value out of range";
    case PARSER_OK:
        return "ok";
    default:
        return "unknown parser error";
    }
}

