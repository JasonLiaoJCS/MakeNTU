#ifndef COMMAND_PARSER_H
#define COMMAND_PARSER_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define COMMAND_FIELD_LEN 32
#define COMMAND_MODE_LEN 16
#define COMMAND_ERROR_MESSAGE_LEN 64

typedef enum {
    COMMAND_TYPE_NONE = 0,
    COMMAND_TYPE_ACT,
    COMMAND_TYPE_EMO,
    COMMAND_TYPE_TEST,
    COMMAND_TYPE_RESET,
    COMMAND_TYPE_STATUS,
    COMMAND_TYPE_PING
} CommandType;

typedef enum {
    PARSER_OK = 0,
    PARSER_BAD_CHECKSUM,
    PARSER_UNKNOWN_CMD,
    PARSER_BAD_FIELD_COUNT,
    PARSER_VALUE_OUT_OF_RANGE
} ParserResult;

typedef struct {
    CommandType type;
    int seq;
    char mode[COMMAND_MODE_LEN];
    char face_id[COMMAND_FIELD_LEN];
    char motion_id[COMMAND_FIELD_LEN];
    char emotion[COMMAND_FIELD_LEN];
    int roll_bias;
    int pitch_bias;
    int speed;
    int hold_ms;
} RobotCommand;

ParserResult command_parser_parse(const char *line, RobotCommand *out_cmd);
const char *parser_result_to_error_code(ParserResult result);
const char *parser_result_to_message(ParserResult result);

#ifdef __cplusplus
}
#endif

#endif

