#include "servo_driver.h"
#include <stdio.h>

static int s_last_roll_angle = ROLL_CENTER_DEG;
static int s_last_pitch_angle = PITCH_CENTER_DEG;
static bool s_enabled = false;

void servo_init(void)
{
    s_enabled = true;
    s_last_roll_angle = g_safety_config.roll.center_deg;
    s_last_pitch_angle = g_safety_config.pitch.center_deg;
    printf("[servo_stub] init: PWM=%uHz pulse=%u..%uus\n",
           (unsigned)SERVO_PWM_HZ,
           (unsigned)g_safety_config.pulse_min_us,
           (unsigned)g_safety_config.pulse_max_us);
    servo_reset_to_center();
}

void servo_set_angle(ServoChannel channel, int angle_deg)
{
    if (!s_enabled) {
        return;
    }
    int safe_angle = clamp_angle(channel, angle_deg);
    uint16_t pulse = angle_to_pulse_us(safe_angle);
    if (channel == SERVO_CHANNEL_PITCH) {
        s_last_pitch_angle = safe_angle;
    } else {
        s_last_roll_angle = safe_angle;
    }
    servo_set_pulse_us(channel, pulse);
}

void servo_set_pulse_us(ServoChannel channel, uint16_t pulse_us)
{
    const char *name = (channel == SERVO_CHANNEL_PITCH) ? "pitch" : "roll";
    printf("[servo_stub] %s pulse=%uus angle=%d\n",
           name,
           (unsigned)pulse_us,
           servo_get_last_angle(channel));
}

void servo_update(void)
{
    /* Real MCUXpresso driver may latch buffered PWM updates here. */
}

void servo_emergency_stop(void)
{
    s_enabled = false;
    printf("[servo_stub] emergency stop: PWM outputs disabled placeholder\n");
}

void servo_reset_to_center(void)
{
    s_enabled = true;
    servo_set_angle(SERVO_CHANNEL_ROLL, g_safety_config.roll.center_deg);
    servo_set_angle(SERVO_CHANNEL_PITCH, g_safety_config.pitch.center_deg);
}

int servo_get_last_angle(ServoChannel channel)
{
    return (channel == SERVO_CHANNEL_PITCH) ? s_last_pitch_angle : s_last_roll_angle;
}

