#ifndef FACE_CONTROLLER_H
#define FACE_CONTROLLER_H

#ifdef __cplusplus
extern "C" {
#endif

void face_controller_init(void);
void face_set_face_id(const char *face_id);
void face_reset_neutral(void);
const char *face_get_current_id(void);

#ifdef __cplusplus
}
#endif

#endif

