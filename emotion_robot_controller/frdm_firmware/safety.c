#include "safety.h"

SafetyConfig g_safety_config = {
    .roll = {
        .min_deg = ROLL_MIN_DEG,
        .center_deg = ROLL_CENTER_DEG,
        .max_deg = ROLL_MAX_DEG,
        .invert = false,
    },
    .pitch = {
        .min_deg = PITCH_MIN_DEG,
        .center_deg = PITCH_CENTER_DEG,
        .max_deg = PITCH_MAX_DEG,
        .invert = false,
    },
    .pulse_min_us = SERVO_PULSE_MIN_US,
    .pulse_max_us = SERVO_PULSE_MAX_US,
};

static const ServoSafetyConfig *get_channel_config(ServoChannel channel)
{
    return (channel == SERVO_CHANNEL_PITCH) ? &g_safety_config.pitch : &g_safety_config.roll;
}

int clamp_angle(ServoChannel channel, int angle_deg)
{
    const ServoSafetyConfig *cfg = get_channel_config(channel);
    if (angle_deg < cfg->min_deg) {
        return cfg->min_deg;
    }
    if (angle_deg > cfg->max_deg) {
        return cfg->max_deg;
    }
    return angle_deg;
}

int angle_from_center_offset(ServoChannel channel, int offset_deg)
{
    const ServoSafetyConfig *cfg = get_channel_config(channel);
    int signed_offset = cfg->invert ? -offset_deg : offset_deg;
    return clamp_angle(channel, cfg->center_deg + signed_offset);
}

uint16_t angle_to_pulse_us(int angle_deg)
{
    if (angle_deg < SERVO_ANGLE_MIN_DEG) {
        angle_deg = SERVO_ANGLE_MIN_DEG;
    }
    if (angle_deg > SERVO_ANGLE_MAX_DEG) {
        angle_deg = SERVO_ANGLE_MAX_DEG;
    }

    uint32_t span = (uint32_t)g_safety_config.pulse_max_us - (uint32_t)g_safety_config.pulse_min_us;
    uint32_t pulse = (uint32_t)g_safety_config.pulse_min_us + (span * (uint32_t)angle_deg) / 180U;
    return (uint16_t)pulse;
}

bool safety_validate_bias(int bias_deg)
{
    return bias_deg >= -20 && bias_deg <= 20;
}

bool safety_validate_speed(int speed)
{
    return speed >= 1 && speed <= 100;
}

bool safety_validate_hold_ms(int hold_ms)
{
    return hold_ms >= 0 && hold_ms <= 5000;
}

