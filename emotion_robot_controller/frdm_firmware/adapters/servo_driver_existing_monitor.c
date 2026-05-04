#include "servo_driver.h"

#include <stdio.h>

/*
 * Optional adapter for your current FRDM monitor functions:
 *
 * void MotorControlPitch(char* pValue);
 * void MotorControlYaw(char* pValue);
 *
 * Use this file instead of servo_driver_stub.c when those functions already
 * control your PWM outputs. In this project, "roll" maps to your existing
 * "Yaw" function because that is the name currently present in your firmware.
 */

extern void MotorControlPitch(char *pValue);
extern void MotorControlYaw(char *pValue);

static int s_last_roll_angle = ROLL_CENTER_DEG;
static int s_last_pitch_angle = PITCH_CENTER_DEG;
static bool s_enabled = false;

static void call_existing_motor_command(ServoChannel channel, int angle_deg)
{
    char value_text[12];
    snprintf(value_text, sizeof(value_text), "%d", angle_deg);

    if (channel == SERVO_CHANNEL_PITCH) {
        MotorControlPitch(value_text);
    } else {
        MotorControlYaw(value_text);
    }
}

void servo_init(void)
{
    s_enabled = true;
    servo_reset_to_center();
}

void servo_set_angle(ServoChannel channel, int angle_deg)
{
    if (!s_enabled) {
        return;
    }

    int safe_angle = clamp_angle(channel, angle_deg);
    if (channel == SERVO_CHANNEL_PITCH) {
        s_last_pitch_angle = safe_angle;
    } else {
        s_last_roll_angle = safe_angle;
    }
    call_existing_motor_command(channel, safe_angle);
}

void servo_set_pulse_us(ServoChannel channel, uint16_t pulse_us)
{
    /*
     * Your current public function accepts angles, not pulse widths.
     * Keep this no-op unless you expose a lower-level PWM pulse API later.
     */
    (void)channel;
    (void)pulse_us;
}

void servo_update(void)
{
}

void servo_emergency_stop(void)
{
    s_enabled = false;
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

