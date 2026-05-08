/*
 * FRDM patch: Weather UART command for the Sleep screen.
 *
 * Jetson sends one compact ASCII line after startup weather lookup:
 *
 *   Weather daily,23,29,40,61
 *
 * Format:
 *   kind,low_or_temp,high_or_temp,pop_percent,open_meteo_weather_code
 *
 * Examples:
 *   Weather daily,23,29,40,61   -> today 23..29 C, 40% rain, light rain
 *   Weather current,27,27,0,2   -> current 27 C, 0% rain, partly cloudy
 *
 * Add this command to sMonitorFuncList:
 *
 *   { "Weather", "<kind,min,max,pop,code>", "update weather on Sleep", WeatherGui },
 *
 * Then call UpdateSleepWeatherWidgets() at the end of setup_scr_Sleep(), or
 * immediately after loading the Sleep screen.
 *
 * IMPORTANT:
 * Replace Sleep_weather_label / Sleep_weather_icon below with your real
 * GUI Guider object names. If you only add one label, keep the label update
 * and remove the icon update block.
 */

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

typedef struct
{
    bool valid;
    char kind[12];
    int low_c;
    int high_c;
    int rain_percent;
    int weather_code;
} weather_state_t;

static weather_state_t g_weather_state = {
    .valid = false,
    .kind = "daily",
    .low_c = 0,
    .high_c = 0,
    .rain_percent = 0,
    .weather_code = -1,
};

static const char *WeatherCodeText(int code)
{
    switch (code) {
    case 0: return "Sunny";
    case 1: return "Mainly sunny";
    case 2: return "Partly cloudy";
    case 3: return "Cloudy";
    case 45:
    case 48: return "Fog";
    case 51:
    case 53:
    case 55: return "Drizzle";
    case 61:
    case 63:
    case 65: return "Rain";
    case 66:
    case 67: return "Freezing rain";
    case 71:
    case 73:
    case 75:
    case 77: return "Snow";
    case 80:
    case 81:
    case 82: return "Showers";
    case 85:
    case 86: return "Snow showers";
    case 95:
    case 96:
    case 99: return "Thunderstorm";
    default: return "Weather";
    }
}

static bool ParseWeatherPayload(const char *pValue, weather_state_t *out_state)
{
    char kind[12] = {0};
    int low_c = 0;
    int high_c = 0;
    int rain_percent = 0;
    int weather_code = -1;
    const char *payload = pValue;

    if (pValue == NULL || out_state == NULL) {
        return false;
    }

    while (*payload == ' ') {
        payload++;
    }

    /* Supports either "daily,23,29,40,61" or "Weather daily,23,29,40,61". */
    if (strncmp(payload, "Weather", 7) == 0) {
        payload += 7;
        while (*payload == ' ') {
            payload++;
        }
    }

    if (sscanf(payload, " %11[^,],%d,%d,%d,%d", kind, &low_c, &high_c, &rain_percent, &weather_code) != 5) {
        return false;
    }

    if (rain_percent < 0) rain_percent = 0;
    if (rain_percent > 100) rain_percent = 100;
    if (weather_code < -1) weather_code = -1;
    if (weather_code > 999) weather_code = 999;

    memset(out_state, 0, sizeof(*out_state));
    out_state->valid = true;
    strncpy(out_state->kind, kind, sizeof(out_state->kind) - 1);
    out_state->low_c = low_c;
    out_state->high_c = high_c;
    out_state->rain_percent = rain_percent;
    out_state->weather_code = weather_code;
    return true;
}

void UpdateSleepWeatherWidgets(void)
{
    char line[80];

    if (!g_weather_state.valid) {
        snprintf(line, sizeof(line), "Weather updating...");
    } else if (strcmp(g_weather_state.kind, "current") == 0 || strcmp(g_weather_state.kind, "hourly") == 0) {
        snprintf(
            line,
            sizeof(line),
            "%d C  %s  rain %d%%",
            g_weather_state.low_c,
            WeatherCodeText(g_weather_state.weather_code),
            g_weather_state.rain_percent
        );
    } else {
        snprintf(
            line,
            sizeof(line),
            "%d-%d C  %s  rain %d%%",
            g_weather_state.low_c,
            g_weather_state.high_c,
            WeatherCodeText(g_weather_state.weather_code),
            g_weather_state.rain_percent
        );
    }

    /*
     * Replace these object names with the labels you create in GUI Guider:
     *
     *   lv_label_set_text(guider_ui.Sleep_weather_label, line);
     *
     * Optional icon label:
     *
     *   lv_label_set_text(guider_ui.Sleep_weather_icon, WeatherCodeText(g_weather_state.weather_code));
     */
    lv_label_set_text(guider_ui.Sleep_weather_label, line);
}

void WeatherGui(char *pValue)
{
    weather_state_t parsed;

    PRINTF("Weather raw pValue = [%s]\r\n", pValue ? pValue : "(null)");

    if (!ParseWeatherPayload(pValue, &parsed)) {
        PRINTF("Weather parse failed\r\n");
        return;
    }

    g_weather_state = parsed;
    PRINTF(
        "Weather = kind:%s low:%d high:%d rain:%d code:%d\r\n",
        g_weather_state.kind,
        g_weather_state.low_c,
        g_weather_state.high_c,
        g_weather_state.rain_percent,
        g_weather_state.weather_code
    );

    UpdateSleepWeatherWidgets();
}
