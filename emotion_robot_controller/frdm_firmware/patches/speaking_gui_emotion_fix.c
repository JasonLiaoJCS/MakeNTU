/*
 * Drop-in FRDM patch for SpeakingGui emotion parsing.
 *
 * Your current UART log shows:
 *   FRDM UART RX: Speaking 4
 *   FRDM UART RX: switch to SPEAKINGemotion: neutral
 *
 * That means SpeakingGui() is being called, but sscanf(pValue, "%u", &value)
 * is not seeing a clean "4". Some SMONITORCOMMAND variants pass only the
 * argument, some pass the whole line, and some pass a pointer into the command
 * buffer after a fixed offset. This parser extracts the first unsigned number
 * anywhere in pValue and clamps it to the 0..5 SpeakingGui range.
 *
 * Required includes in the FRDM source file:
 *   #include <stdbool.h>
 *   #include <ctype.h>
 *   #include <stdlib.h>
 */

#include <stdbool.h>
#include <ctype.h>
#include <stdlib.h>

static bool ParseSpeakingEmotion(const char *pValue, unsigned int *out_value)
{
    const char *p = pValue;
    char *end = NULL;
    unsigned long parsed = 0;

    if (pValue == NULL || out_value == NULL) {
        return false;
    }

    while (*p != '\0' && !isdigit((unsigned char)*p)) {
        p++;
    }
    if (*p == '\0') {
        return false;
    }

    parsed = strtoul(p, &end, 10);
    if (end == p) {
        return false;
    }

    if (parsed > 5UL) {
        parsed = 5UL;
    }

    *out_value = (unsigned int)parsed;
    return true;
}

/*
 * Replace this block in SpeakingGui():
 *
 *     unsigned int value = 0;
 *     if (pValue != NULL)
 *     {
 *         sscanf(pValue, "%u", &value);
 *     }
 *     robot.emotionstatus = value;
 *
 * with this:
 */

static unsigned int ReadSpeakingEmotionOrNeutral(char *pValue)
{
    unsigned int value = 0;

    PRINTF("Speaking raw pValue = [%s]\r\n", pValue ? pValue : "(null)");
    if (!ParseSpeakingEmotion(pValue, &value)) {
        PRINTF("Speaking emotion parse failed, using neutral\r\n");
        value = 0;
    }

    PRINTF("Speaking emotion code = %u\r\n", value);
    return value;
}

/*
 * Then inside SpeakingGui(), use:
 *
 *     unsigned int value = ReadSpeakingEmotionOrNeutral(pValue);
 *     robot.emotionstatus = value;
 *
 * Also hide the optional emotion decoration objects before the switch, so old
 * sad/happy marks do not remain visible when returning to another emotion:
 *
 *     lv_obj_add_flag(guider_ui.Speaking_emoL3, LV_OBJ_FLAG_HIDDEN);
 *     lv_obj_add_flag(guider_ui.Speaking_emoR3, LV_OBJ_FLAG_HIDDEN);
 *     lv_obj_add_flag(guider_ui.Speaking_emo4L, LV_OBJ_FLAG_HIDDEN);
 *     lv_obj_add_flag(guider_ui.Speaking_emo4R, LV_OBJ_FLAG_HIDDEN);
 *
 * The expected UART code table is:
 *   Speaking 0 -> neutral
 *   Speaking 1 -> concerned
 *   Speaking 2 -> angry
 *   Speaking 3 -> sad
 *   Speaking 4 -> happy
 *   Speaking 5 -> confused
 */
