#ifndef UART_PROTOCOL_H
#define UART_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UART_PROTOCOL_MAX_PACKET_LEN 192
#define UART_PROTOCOL_MAX_PAYLOAD_LEN 160

uint8_t uart_protocol_checksum_payload(const char *payload);
bool uart_protocol_parse_checksum(const char *checksum_text, uint8_t *out_value);
bool uart_protocol_validate_packet(const char *line, char *payload_out, size_t payload_out_size);
int uart_protocol_format_packet(char *out, size_t out_size, const char *payload);
int uart_protocol_build_ack(char *out, size_t out_size, int seq);
int uart_protocol_build_nack(char *out, size_t out_size, int seq, const char *error_code, const char *message);
int uart_protocol_build_pong(char *out, size_t out_size, int seq);
int uart_protocol_build_status(char *out, size_t out_size, int seq, const char *status_text);

#ifdef __cplusplus
}
#endif

#endif

