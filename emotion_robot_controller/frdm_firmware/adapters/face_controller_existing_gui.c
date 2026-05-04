#include "face_controller.h"

#include <string.h>

/*
 * Optional adapter for your current LVGL functions:
 *
 * void SLEEPGui(char* pValue);
 * void NORMALGui(char* pValue);
 *
 * Use this file instead of face_controller.c when you want FACE_SLEEPY and
 * FACE_TIRED to switch to your Sleep screen, and all other face IDs to switch
 * to your Normal screen until you add more screens.
 */

extern void SLEEPGui(char *pValue);
extern void NORMALGui(char *pValue);

static char s_current_face_id[32] = "FACE_NEUTRAL";

void face_controller_init(void)
{
    face_reset_neutral();
}

void face_set_face_id(const char *face_id)
{
    if (!face_id) {
        return;
    }

    strncpy(s_current_face_id, face_id, sizeof(s_current_face_id) - 1U);
    s_current_face_id[sizeof(s_current_face_id) - 1U] = '\0';

    if (strcmp(face_id, "FACE_SLEEPY") == 0 || strcmp(face_id, "FACE_TIRED") == 0) {
        SLEEPGui("");
    } else {
        NORMALGui("");
    }
}

void face_reset_neutral(void)
{
    face_set_face_id("FACE_NEUTRAL");
}

const char *face_get_current_id(void)
{
    return s_current_face_id;
}

