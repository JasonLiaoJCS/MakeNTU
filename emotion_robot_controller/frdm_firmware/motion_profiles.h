#ifndef MOTION_PROFILES_H
#define MOTION_PROFILES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * roll and pitch are logical offsets from center in degrees.
 * roll:  negative = tilt left, positive = tilt right.
 * pitch: negative = look up, positive = look down.
 * If the mechanism is reversed, set g_safety_config.<axis>.invert = true.
 */
typedef struct {
    int roll;
    int pitch;
    uint16_t duration_ms;
    uint16_t hold_ms;
} MotionStep;

typedef struct {
    const char *emotion;
    const char *face_id;
    const char *motion_id;
    const MotionStep *steps;
    size_t step_count;
    uint8_t default_speed;
    uint16_t default_hold_ms;
    bool return_to_center;
} MotionProfile;

const MotionProfile *motion_profile_find_by_emotion(const char *emotion);
const MotionProfile *motion_profile_find_by_motion_id(const char *motion_id);
const MotionProfile *motion_profiles_all(size_t *count);

#ifdef __cplusplus
}
#endif

#endif

