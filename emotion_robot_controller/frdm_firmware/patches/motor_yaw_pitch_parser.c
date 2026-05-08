/*
 * FRDM patch: combined yaw + pitch UART command.
 *
 * Jetson sends:
 *
 *   MotorYawPitch 120 90
 *
 * Format:
 *   MotorYawPitch <yaw> <pitch>
 *
 * yaw   : 0..180, 90=center
 * pitch : 65..115, 90=center
 *
 * Add this command to sMonitorFuncList:
 *
 *   { "MotorYawPitch", "<yaw> <pitch>", "control yaw and pitch together", MotorControlYawPitch },
 *
 * Keep MotorPitch and MotorYaw too; the Jetson still supports them for manual
 * single-axis tests, but natural head motion now prefers MotorYawPitch.
 */

#include <stdbool.h>
#include <stdio.h>

static bool ParseYawPitchAngles(const char *pValue, int *out_yaw, int *out_pitch)
{
    int yaw = 90;
    int pitch = 90;

    if (pValue == NULL || out_yaw == NULL || out_pitch == NULL) {
        return false;
    }

    /*
     * Supports either:
     *   "120 90"
     *   "MotorYawPitch 120 90"
     */
    if (sscanf(pValue, " %d %d", &yaw, &pitch) == 2 ||
        sscanf(pValue, " %*s %d %d", &yaw, &pitch) == 2) {
        *out_yaw = yaw;
        *out_pitch = pitch;
        return true;
    }

    return false;
}

void MotorControlYawPitch(char *pValue)
{
    int yaw = 90;
    int pitch = 90;

    PRINTF("Motor YawPitch raw pValue = [%s]\r\n", pValue ? pValue : "(null)");

    if (!ParseYawPitchAngles(pValue, &yaw, &pitch)) {
        PRINTF("Motor YawPitch parse failed\r\n");
        return;
    }

    if (yaw < 0) yaw = 0;
    if (yaw > 180) yaw = 180;
    if (pitch < 65) pitch = 65;
    if (pitch > 115) pitch = 115;

    robot.motorY = yaw;
    robot.motorP = pitch;

    PRINTF("Motor YawPitch = yaw:%d pitch:%d\r\n", yaw, pitch);
    Servo_SetYaw(yaw);
    Servo_SetPitch(pitch);
}
