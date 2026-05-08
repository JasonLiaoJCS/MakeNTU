/*
 * FRDM patch: Time UART command for GUI Guider / LVGL clock widgets.
 *
 * Jetson sends one compact ASCII line at bridge startup:
 *
 *   Time 20260509,213005,6,+480
 *
 * Format:
 *   yyyymmdd,hhmmss,isoweekday,utc_offset_min
 *
 * Examples:
 *   Time 20260509,213005,6,+480 -> 2026/05/09 21:30:05, Saturday, UTC+8
 *   Time 20260509,133005,6,+0   -> 2026/05/09 13:30:05, Saturday, UTC
 *
 * Add this command to sMonitorFuncList:
 *
 *   { "Time", "<yyyymmdd,hhmmss,weekday,utc_offset_min>", "update GUI time", TimeGui },
 *
 * Then call UpdateTimeWidgets() at the end of setup_scr_Sleep(),
 * setup_scr_Normal(), or immediately after loading a screen with time labels.
 *
 * IMPORTANT:
 * Replace Sleep_time_label / Sleep_date_label below with your real GUI Guider
 * object names. If you only add one label, keep the time label update and
 * remove the date label update block.
 */

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

typedef struct
{
    bool valid;
    int year;
    int month;
    int day;
    int hour;
    int minute;
    int second;
    int iso_weekday;
    int utc_offset_min;
} time_state_t;

static time_state_t g_time_state = {
    .valid = false,
    .year = 0,
    .month = 0,
    .day = 0,
    .hour = 0,
    .minute = 0,
    .second = 0,
    .iso_weekday = 0,
    .utc_offset_min = 0,
};

static const char *WeekdayText(int iso_weekday)
{
    switch (iso_weekday) {
    case 1: return "Mon";
    case 2: return "Tue";
    case 3: return "Wed";
    case 4: return "Thu";
    case 5: return "Fri";
    case 6: return "Sat";
    case 7: return "Sun";
    default: return "";
    }
}

static bool ParseTimePayload(const char *pValue, time_state_t *out_state)
{
    char date_text[9] = {0};
    char time_text[7] = {0};
    int iso_weekday = 0;
    int utc_offset_min = 0;
    int year = 0;
    int month = 0;
    int day = 0;
    int hour = 0;
    int minute = 0;
    int second = 0;
    const char *payload = pValue;

    if (pValue == NULL || out_state == NULL) {
        return false;
    }

    while (*payload == ' ') {
        payload++;
    }

    /* Supports either "20260509,213005,6,+480" or "Time 20260509,213005,6,+480". */
    if (strncmp(payload, "Time", 4) == 0) {
        payload += 4;
        while (*payload == ' ') {
            payload++;
        }
    }

    if (sscanf(payload, " %8[^,],%6[^,],%d,%d", date_text, time_text, &iso_weekday, &utc_offset_min) != 4) {
        return false;
    }

    if (sscanf(date_text, "%4d%2d%2d", &year, &month, &day) != 3) {
        return false;
    }
    if (sscanf(time_text, "%2d%2d%2d", &hour, &minute, &second) != 3) {
        return false;
    }

    if (year < 2020 || year > 2099) return false;
    if (month < 1 || month > 12) return false;
    if (day < 1 || day > 31) return false;
    if (hour < 0 || hour > 23) return false;
    if (minute < 0 || minute > 59) return false;
    if (second < 0 || second > 59) return false;
    if (iso_weekday < 1 || iso_weekday > 7) iso_weekday = 0;
    if (utc_offset_min < -720) utc_offset_min = -720;
    if (utc_offset_min > 840) utc_offset_min = 840;

    memset(out_state, 0, sizeof(*out_state));
    out_state->valid = true;
    out_state->year = year;
    out_state->month = month;
    out_state->day = day;
    out_state->hour = hour;
    out_state->minute = minute;
    out_state->second = second;
    out_state->iso_weekday = iso_weekday;
    out_state->utc_offset_min = utc_offset_min;
    return true;
}

void UpdateTimeWidgets(void)
{
    char time_line[16];
    char date_line[32];

    if (!g_time_state.valid) {
        snprintf(time_line, sizeof(time_line), "--:--");
        snprintf(date_line, sizeof(date_line), "Time syncing...");
    } else {
        snprintf(
            time_line,
            sizeof(time_line),
            "%02d:%02d",
            g_time_state.hour,
            g_time_state.minute
        );
        snprintf(
            date_line,
            sizeof(date_line),
            "%04d/%02d/%02d %s",
            g_time_state.year,
            g_time_state.month,
            g_time_state.day,
            WeekdayText(g_time_state.iso_weekday)
        );
    }

    /*
     * Replace these object names with labels you create in GUI Guider:
     *
     *   lv_label_set_text(guider_ui.Sleep_time_label, time_line);
     *   lv_label_set_text(guider_ui.Sleep_date_label, date_line);
     */
    lv_label_set_text(guider_ui.Sleep_time_label, time_line);
    lv_label_set_text(guider_ui.Sleep_date_label, date_line);
}

void TimeGui(char *pValue)
{
    time_state_t parsed;

    PRINTF("Time raw pValue = [%s]\r\n", pValue ? pValue : "(null)");

    if (!ParseTimePayload(pValue, &parsed)) {
        PRINTF("Time parse failed\r\n");
        return;
    }

    g_time_state = parsed;
    PRINTF(
        "Time = %04d/%02d/%02d %02d:%02d:%02d weekday:%d offset:%d\r\n",
        g_time_state.year,
        g_time_state.month,
        g_time_state.day,
        g_time_state.hour,
        g_time_state.minute,
        g_time_state.second,
        g_time_state.iso_weekday,
        g_time_state.utc_offset_min
    );

    UpdateTimeWidgets();
}
