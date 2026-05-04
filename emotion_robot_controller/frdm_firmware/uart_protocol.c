#include "uart_protocol.h"
#include <stdio.h>
#include <string.h>

uint8_t uart_protocol_checksum_payload(const char *payload)
{
    uint8_t value = 0U;
    while (*payload) {
        value ^= (uint8_t)(*payload);
        ++payload;
    }
    return value;
}

bool uart_protocol_parse_checksum(const char *checksum_text, uint8_t *out_value)
{
    unsigned value = 0U;
    if (!checksum_text || strlen(checksum_text) < 2U) {
        return false;
    }
    if (checksum_text[2] != '\0' && checksum_text[2] != '\r' && checksum_text[2] != '\n') {
        return false;
    }
    if (sscanf(checksum_text, "%2x", &value) != 1) {
        return false;
    }
    if (value > 0xFFU) {
        return false;
    }
    *out_value = (uint8_t)value;
    return true;
}

bool uart_protocol_validate_packet(const char *line, char *payload_out, size_t payload_out_size)
{
    if (!line || !payload_out || payload_out_size == 0U) {
        return false;
    }
    if (line[0] != '$') {
        return false;
    }

    const char *star = strchr(line, '*');
    if (!star) {
        return false;
    }
    size_t payload_len = (size_t)(star - line - 1);
    if (payload_len == 0U || payload_len >= payload_out_size) {
        return false;
    }

    uint8_t received = 0U;
    if (!uart_protocol_parse_checksum(star + 1, &received)) {
        return false;
    }

    memcpy(payload_out, line + 1, payload_len);
    payload_out[payload_len] = '\0';

    uint8_t expected = uart_protocol_checksum_payload(payload_out);
    return received == expected;
}

int uart_protocol_format_packet(char *out, size_t out_size, const char *payload)
{
    uint8_t checksum = uart_protocol_checksum_payload(payload);
    return snprintf(out, out_size, "$%s*%02X", payload, checksum);
}

int uart_protocol_build_ack(char *out, size_t out_size, int seq)
{
    char payload[64];
    snprintf(payload, sizeof(payload), "ACK,%d,OK", seq);
    return uart_protocol_format_packet(out, out_size, payload);
}

int uart_protocol_build_nack(char *out, size_t out_size, int seq, const char *error_code, const char *message)
{
    char payload[128];
    snprintf(payload, sizeof(payload), "NACK,%d,%s,%s", seq, error_code, message);
    return uart_protocol_format_packet(out, out_size, payload);
}

int uart_protocol_build_pong(char *out, size_t out_size, int seq)
{
    char payload[64];
    snprintf(payload, sizeof(payload), "PONG,%d,OK", seq);
    return uart_protocol_format_packet(out, out_size, payload);
}

int uart_protocol_build_status(char *out, size_t out_size, int seq, const char *status_text)
{
    char payload[128];
    snprintf(payload, sizeof(payload), "STATUS,%d,%s", seq, status_text);
    return uart_protocol_format_packet(out, out_size, payload);
}
