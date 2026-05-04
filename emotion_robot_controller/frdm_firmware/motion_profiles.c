#include "motion_profiles.h"
#include <string.h>

static const MotionStep CENTER_STEPS[] = {
    {0, 0, 300, 200},
};

static const MotionStep HAPPY_NOD_SWAY_STEPS[] = {
    {0, -3, 240, 60},
    {5, 4, 260, 60},
    {-5, -2, 260, 60},
    {4, 3, 220, 80},
    {0, 0, 300, 180},
};

static const MotionStep EXCITED_FAST_NOD_STEPS[] = {
    {0, -8, 160, 40},
    {4, 5, 170, 40},
    {-4, -7, 160, 40},
    {3, 4, 170, 40},
    {0, 0, 240, 120},
};

static const MotionStep SAD_LOWER_HEAD_STEPS[] = {
    {-2, 4, 450, 150},
    {-5, 12, 700, 450},
};

static const MotionStep TIRED_DROOP_STEPS[] = {
    {0, 3, 700, 180},
    {0, 10, 1100, 700},
};

static const MotionStep ANGRY_SHORT_SHAKE_STEPS[] = {
    {-7, 6, 120, 20},
    {7, 6, 120, 20},
    {-6, 5, 110, 20},
    {6, 5, 110, 40},
    {0, 3, 220, 120},
};

static const MotionStep SURPRISED_POP_UP_STEPS[] = {
    {0, -15, 140, 250},
    {0, -6, 180, 100},
    {0, 0, 260, 160},
};

static const MotionStep CURIOUS_TILT_STEPS[] = {
    {10, -4, 420, 550},
    {8, -2, 260, 250},
    {0, 0, 360, 180},
};

static const MotionStep CONFUSED_DOUBLE_TILT_STEPS[] = {
    {-9, 3, 360, 300},
    {9, 2, 380, 300},
    {0, 0, 360, 160},
};

static const MotionStep THINKING_LOOK_DOWN_UP_STEPS[] = {
    {-4, 7, 650, 400},
    {-3, -2, 520, 300},
    {0, 0, 420, 180},
};

static const MotionStep CONCERNED_SOFT_NOD_STEPS[] = {
    {2, 5, 500, 260},
    {2, 1, 420, 180},
    {1, 5, 420, 380},
};

static const MotionStep SLEEPY_BREATH_STEPS[] = {
    {0, 5, 900, 220},
    {0, 9, 1100, 300},
    {0, 6, 1000, 500},
};

static const MotionStep ROLL_LEFT_STEPS[] = {
    {-12, 0, 400, 500},
    {0, 0, 400, 100},
};

static const MotionStep ROLL_RIGHT_STEPS[] = {
    {12, 0, 400, 500},
    {0, 0, 400, 100},
};

static const MotionStep PITCH_UP_STEPS[] = {
    {0, -12, 400, 500},
    {0, 0, 400, 100},
};

static const MotionStep PITCH_DOWN_STEPS[] = {
    {0, 12, 400, 500},
    {0, 0, 400, 100},
};

#define PROFILE(emotion_name, face, motion, speed, hold, recenter, steps_array) \
    {emotion_name, face, motion, steps_array, sizeof(steps_array) / sizeof((steps_array)[0]), speed, hold, recenter}

static const MotionProfile PROFILES[] = {
    PROFILE("neutral", "FACE_NEUTRAL", "CENTER", 20, 800, true, CENTER_STEPS),
    PROFILE("happy", "FACE_HAPPY", "HAPPY_NOD_SWAY", 30, 1200, true, HAPPY_NOD_SWAY_STEPS),
    PROFILE("excited", "FACE_EXCITED", "EXCITED_FAST_NOD", 45, 900, true, EXCITED_FAST_NOD_STEPS),
    PROFILE("sad", "FACE_SAD", "SAD_LOWER_HEAD", 15, 1600, false, SAD_LOWER_HEAD_STEPS),
    PROFILE("tired", "FACE_TIRED", "TIRED_DROOP", 12, 1800, false, TIRED_DROOP_STEPS),
    PROFILE("angry", "FACE_ANGRY", "ANGRY_SHORT_SHAKE", 42, 800, true, ANGRY_SHORT_SHAKE_STEPS),
    PROFILE("surprised", "FACE_SURPRISED", "SURPRISED_POP_UP", 50, 700, true, SURPRISED_POP_UP_STEPS),
    PROFILE("curious", "FACE_CURIOUS", "CURIOUS_TILT", 25, 1300, true, CURIOUS_TILT_STEPS),
    PROFILE("confused", "FACE_CONFUSED", "CONFUSED_DOUBLE_TILT", 24, 1100, true, CONFUSED_DOUBLE_TILT_STEPS),
    PROFILE("thinking", "FACE_THINKING", "THINKING_LOOK_DOWN_UP", 18, 1400, true, THINKING_LOOK_DOWN_UP_STEPS),
    PROFILE("concerned", "FACE_CONCERNED", "CONCERNED_SOFT_NOD", 18, 1600, false, CONCERNED_SOFT_NOD_STEPS),
    PROFILE("sleepy", "FACE_SLEEPY", "SLEEPY_BREATH", 10, 2000, false, SLEEPY_BREATH_STEPS),
    PROFILE("test", "FACE_NEUTRAL", "ROLL_LEFT", 25, 500, true, ROLL_LEFT_STEPS),
    PROFILE("test", "FACE_NEUTRAL", "ROLL_RIGHT", 25, 500, true, ROLL_RIGHT_STEPS),
    PROFILE("test", "FACE_NEUTRAL", "PITCH_UP", 25, 500, true, PITCH_UP_STEPS),
    PROFILE("test", "FACE_NEUTRAL", "PITCH_DOWN", 25, 500, true, PITCH_DOWN_STEPS),
};

const MotionProfile *motion_profile_find_by_emotion(const char *emotion)
{
    for (size_t i = 0; i < sizeof(PROFILES) / sizeof(PROFILES[0]); ++i) {
        if (strcmp(PROFILES[i].emotion, emotion) == 0) {
            return &PROFILES[i];
        }
    }
    return 0;
}

const MotionProfile *motion_profile_find_by_motion_id(const char *motion_id)
{
    for (size_t i = 0; i < sizeof(PROFILES) / sizeof(PROFILES[0]); ++i) {
        if (strcmp(PROFILES[i].motion_id, motion_id) == 0) {
            return &PROFILES[i];
        }
    }
    return 0;
}

const MotionProfile *motion_profiles_all(size_t *count)
{
    if (count) {
        *count = sizeof(PROFILES) / sizeof(PROFILES[0]);
    }
    return PROFILES;
}

