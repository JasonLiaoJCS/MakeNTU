#include "face_controller.h"
#include <stdio.h>
#include <string.h>

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
    printf("[face_stub] face=%s\n", s_current_face_id);
}

void face_reset_neutral(void)
{
    face_set_face_id("FACE_NEUTRAL");
}

const char *face_get_current_id(void)
{
    return s_current_face_id;
}

