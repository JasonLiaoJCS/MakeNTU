#ifndef SERVO_DRIVER_H
#define SERVO_DRIVER_H

#include <stdint.h>
#include "safety.h"

#ifdef __cplusplus
extern "C" {
#endif

void servo_init(void);
void servo_set_angle(ServoChannel channel, int angle_deg);
void servo_set_pulse_us(ServoChannel channel, uint16_t pulse_us);
void servo_update(void);
void servo_emergency_stop(void);
void servo_reset_to_center(void);
int servo_get_last_angle(ServoChannel channel);

#ifdef __cplusplus
}
#endif

#endif

