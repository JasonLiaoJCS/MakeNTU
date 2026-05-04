#include "motion_controller.h"
#include "safety.h"
#include "servo_driver.h"

static bool s_busy = false;
static int s_current_roll_angle = ROLL_CENTER_DEG;
static int s_current_pitch_angle = PITCH_CENTER_DEG;
static MotionDelayFn s_delay_fn = 0;

static void delay_ms(uint32_t ms)
{
    if (s_delay_fn) {
        s_delay_fn(ms);
    }
}

static int clamp_speed_or_default(int speed, int default_speed)
{
    if (speed <= 0) {
        speed = default_speed;
    }
    if (speed < 1) {
        return 1;
    }
    if (speed > 100) {
        return 100;
    }
    return speed;
}

static int scale_duration_ms(int duration_ms, int speed)
{
    int scaled = (duration_ms * 25) / speed;
    if (scaled < 20) {
        scaled = 20;
    }
    return scaled;
}

void motion_controller_init(void)
{
    servo_init();
    s_current_roll_angle = g_safety_config.roll.center_deg;
    s_current_pitch_angle = g_safety_config.pitch.center_deg;
    s_busy = false;
}

void motion_controller_set_delay_fn(MotionDelayFn delay_fn)
{
    s_delay_fn = delay_fn;
}

bool motion_controller_is_busy(void)
{
    return s_busy;
}

void smooth_move(int roll_offset, int pitch_offset, int duration_ms, int speed)
{
    int target_roll = angle_from_center_offset(SERVO_CHANNEL_ROLL, roll_offset);
    int target_pitch = angle_from_center_offset(SERVO_CHANNEL_PITCH, pitch_offset);
    int scaled_duration = scale_duration_ms(duration_ms, clamp_speed_or_default(speed, 25));
    int steps = scaled_duration / 20;
    if (steps < 1) {
        steps = 1;
    }

    int start_roll = s_current_roll_angle;
    int start_pitch = s_current_pitch_angle;

    for (int i = 1; i <= steps; ++i) {
        int roll = start_roll + ((target_roll - start_roll) * i) / steps;
        int pitch = start_pitch + ((target_pitch - start_pitch) * i) / steps;
        servo_set_angle(SERVO_CHANNEL_ROLL, roll);
        servo_set_angle(SERVO_CHANNEL_PITCH, pitch);
        servo_update();
        delay_ms(20);
    }

    s_current_roll_angle = target_roll;
    s_current_pitch_angle = target_pitch;
}

MotionStatus motion_execute_profile(const MotionProfile *profile,
                                    int roll_bias,
                                    int pitch_bias,
                                    int speed,
                                    int hold_ms)
{
    if (!profile) {
        return MOTION_STATUS_UNKNOWN_MOTION;
    }
    if (s_busy) {
        return MOTION_STATUS_BUSY;
    }
    if (!safety_validate_bias(roll_bias) ||
        !safety_validate_bias(pitch_bias) ||
        !safety_validate_speed(clamp_speed_or_default(speed, profile->default_speed)) ||
        !safety_validate_hold_ms(hold_ms)) {
        return MOTION_STATUS_VALUE_OUT_OF_RANGE;
    }

    s_busy = true;
    int effective_speed = clamp_speed_or_default(speed, profile->default_speed);
    for (size_t i = 0; i < profile->step_count; ++i) {
        const MotionStep *step = &profile->steps[i];
        smooth_move(step->roll + roll_bias, step->pitch + pitch_bias, step->duration_ms, effective_speed);
        if (step->hold_ms > 0U) {
            delay_ms(step->hold_ms);
        }
    }

    if (profile->return_to_center) {
        smooth_move(0, 0, 300, effective_speed);
    }

    int final_hold = hold_ms > 0 ? hold_ms : profile->default_hold_ms;
    if (final_hold > 0) {
        delay_ms((uint32_t)final_hold);
    }
    s_busy = false;
    return MOTION_STATUS_OK;
}

MotionStatus motion_execute_motion_id(const char *motion_id,
                                      int roll_bias,
                                      int pitch_bias,
                                      int speed,
                                      int hold_ms)
{
    const MotionProfile *profile = motion_profile_find_by_motion_id(motion_id);
    return motion_execute_profile(profile, roll_bias, pitch_bias, speed, hold_ms);
}

MotionStatus motion_execute_emotion(const char *emotion)
{
    const MotionProfile *profile = motion_profile_find_by_emotion(emotion);
    if (!profile) {
        return MOTION_STATUS_UNKNOWN_MOTION;
    }
    return motion_execute_profile(profile, 0, 0, profile->default_speed, profile->default_hold_ms);
}

void motion_reset_to_center(void)
{
    s_busy = true;
    smooth_move(0, 0, 350, 25);
    s_busy = false;
}

void motion_emergency_stop(void)
{
    s_busy = false;
    servo_emergency_stop();
}

const char *motion_status_to_string(MotionStatus status)
{
    switch (status) {
    case MOTION_STATUS_OK:
        return "OK";
    case MOTION_STATUS_BUSY:
        return "BUSY";
    case MOTION_STATUS_UNKNOWN_MOTION:
        return "UNKNOWN_MOTION";
    case MOTION_STATUS_VALUE_OUT_OF_RANGE:
        return "VALUE_OUT_OF_RANGE";
    default:
        return "UNKNOWN";
    }
}

