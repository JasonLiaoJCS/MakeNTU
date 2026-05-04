#ifndef MOTION_CONTROLLER_H
#define MOTION_CONTROLLER_H

#include <stdbool.h>
#include <stdint.h>
#include "motion_profiles.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*MotionDelayFn)(uint32_t ms);

typedef enum {
    MOTION_STATUS_OK = 0,
    MOTION_STATUS_BUSY,
    MOTION_STATUS_UNKNOWN_MOTION,
    MOTION_STATUS_VALUE_OUT_OF_RANGE
} MotionStatus;

void motion_controller_init(void);
void motion_controller_set_delay_fn(MotionDelayFn delay_fn);
bool motion_controller_is_busy(void);
MotionStatus motion_execute_profile(const MotionProfile *profile,
                                    int roll_bias,
                                    int pitch_bias,
                                    int speed,
                                    int hold_ms);
MotionStatus motion_execute_motion_id(const char *motion_id,
                                      int roll_bias,
                                      int pitch_bias,
                                      int speed,
                                      int hold_ms);
MotionStatus motion_execute_emotion(const char *emotion);
void smooth_move(int roll_offset, int pitch_offset, int duration_ms, int speed);
void motion_reset_to_center(void);
void motion_emergency_stop(void);
const char *motion_status_to_string(MotionStatus status);

#ifdef __cplusplus
}
#endif

#endif

