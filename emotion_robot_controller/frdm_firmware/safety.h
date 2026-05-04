#ifndef SAFETY_H
#define SAFETY_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SERVO_PWM_HZ 50U
#define SERVO_PULSE_MIN_US 500U
#define SERVO_PULSE_MAX_US 2500U
#define SERVO_ANGLE_MIN_DEG 0
#define SERVO_ANGLE_MAX_DEG 180

#define ROLL_MIN_DEG 55
#define ROLL_CENTER_DEG 90
#define ROLL_MAX_DEG 125

#define PITCH_MIN_DEG 55
#define PITCH_CENTER_DEG 90
#define PITCH_MAX_DEG 125

typedef enum {
    SERVO_CHANNEL_ROLL = 0,
    SERVO_CHANNEL_PITCH = 1
} ServoChannel;

typedef struct {
    int min_deg;
    int center_deg;
    int max_deg;
    bool invert;
} ServoSafetyConfig;

typedef struct {
    ServoSafetyConfig roll;
    ServoSafetyConfig pitch;
    uint16_t pulse_min_us;
    uint16_t pulse_max_us;
} SafetyConfig;

extern SafetyConfig g_safety_config;

int clamp_angle(ServoChannel channel, int angle_deg);
int angle_from_center_offset(ServoChannel channel, int offset_deg);
uint16_t angle_to_pulse_us(int angle_deg);
bool safety_validate_bias(int bias_deg);
bool safety_validate_speed(int speed);
bool safety_validate_hold_ms(int hold_ms);

#ifdef __cplusplus
}
#endif

#endif

