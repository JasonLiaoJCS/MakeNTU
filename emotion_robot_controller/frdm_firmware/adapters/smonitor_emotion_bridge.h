#ifndef SMONITOR_EMOTION_BRIDGE_H
#define SMONITOR_EMOTION_BRIDGE_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Use these two functions when your FRDM project already has an
 * SMONITORCOMMAND command table.
 *
 * Add this entry:
 * { "ERobot", "<packet>", "emotion robot packet", EmotionRobotCommand },
 *
 * Then the PC can send:
 * ERobot $PING,1*0D
 */
void EmotionRobotInit(void);
void EmotionRobotCommand(char *pValue);

#ifdef __cplusplus
}
#endif

#endif

