# FRDM-MCXN947 Firmware Notes

這個資料夾是「可移植核心邏輯 + FRDM/MCUXpresso 整合骨架」。

核心 parser、checksum、motion profiles、角度 clamp、smooth interpolation 都已經寫好；真正跟 MCXN947 SDK 綁定的 UART RX/TX、delay、PWM 輸出仍然用 stub/TODO 包起來，避免把不確定的 SDK API 寫死。

## 檔案分工

| 檔案 | 功能 |
| --- | --- |
| `main.c` | 主迴圈骨架、ACK/NACK、平台 TODO |
| `uart_protocol.c/.h` | `$PAYLOAD*CS` checksum、ACK/NACK/PONG/STATUS 封包 |
| `command_parser.c/.h` | 解析 ACT/EMO/TEST/RESET/STATUS/PING |
| `motion_profiles.c/.h` | 12 種情緒動作與 TEST 動作 |
| `motion_controller.c/.h` | smooth_move、sequence player、bias/speed/hold |
| `safety.c/.h` | MG995 角度範圍、pulse mapping、clamp |
| `servo_driver.h` | Servo HAL 介面 |
| `servo_driver_stub.c` | printf stub，先看 log，不會真的輸出 PWM |
| `face_controller.c/.h` | face_id placeholder |
| `adapters/smonitor_emotion_bridge.c/.h` | 接到你既有 `SMONITORCOMMAND` 的橋接層 |
| `adapters/servo_driver_existing_monitor.c` | 用你現有 `MotorControlPitch/MotorControlYaw` 控制馬達 |
| `adapters/face_controller_existing_gui.c` | 用你現有 `SLEEPGui/NORMALGui` 切 LVGL 畫面 |

## 接到你目前的 SMONITORCOMMAND

你目前 FRDM 已有這些 function：

```c
void SLEEPGui(char* pValue);
void NORMALGui(char* pValue);
void MotorControlPitch(char* pValue);
void MotorControlYaw(char* pValue);
```

以及：

```c
SMONITORCOMMAND sMonitorFuncList[] = {
    { "Sleep",      "<var 1> <var 2>", "switch to SLEEP",       SLEEPGui },
    { "Normal",     "<var 1> <var 2>", "switch to NORMAL",      NORMALGui },
    { "ShowNum",    "<var 1> <var 2>", "Print the input numbers", ShowNumber },
    { "MotorPitch", "<var 1> <var 2>", "control motor P",       MotorControlPitch },
    { "MotorYaw",   "<var 1> <var 2>", "control motor Y",       MotorControlYaw },
    { 0, 0, 0, 0 }
};
```

保留它，新增一個 `ERobot` 指令即可：

```c
#include "smonitor_emotion_bridge.h"

SMONITORCOMMAND sMonitorFuncList[] = {
    { "Sleep",      "<var 1> <var 2>", "switch to SLEEP",       SLEEPGui },
    { "Normal",     "<var 1> <var 2>", "switch to NORMAL",      NORMALGui },
    { "ShowNum",    "<var 1> <var 2>", "Print the input numbers", ShowNumber },
    { "MotorPitch", "<var 1> <var 2>", "control motor P",       MotorControlPitch },
    { "MotorYaw",   "<var 1> <var 2>", "control motor Y",       MotorControlYaw },
    { "ERobot",     "<packet>",        "emotion robot packet",  EmotionRobotCommand },
    { 0, 0, 0, 0 }
};
```

在你的 board / app 初始化完成後呼叫一次：

```c
EmotionRobotInit();
```

然後 PC 端 `config.yaml` 設：

```yaml
serial:
  command_prefix: ERobot
```

此時 Python 實際送到 FRDM 的內容會像這樣：

```text
ERobot $PING,1*0D
ERobot $EMO,2,happy*05
```

FRDM 仍會回：

```text
$PONG,1,OK*checksum
$ACK,2,OK*checksum
```

如果你之後改成 raw UART line parser，能直接吃 `$PING,1*0D`，就把 `command_prefix` 留空。

## 用現有 GUI 和馬達函式

如果要直接使用你現有的 `SLEEPGui/NORMALGui`：

1. 加入 `adapters/face_controller_existing_gui.c`。
2. 從 build 排除原本的 `face_controller.c`。

目前 mapping：

```text
FACE_SLEEPY, FACE_TIRED -> SLEEPGui("")
其他 face_id             -> NORMALGui("")
```

未來你新增 Happy/Sad/Angry 畫面時，只要改 `face_controller_existing_gui.c` 的 `face_set_face_id()`。

如果要直接使用你現有的 `MotorControlPitch/MotorControlYaw`：

1. 加入 `adapters/servo_driver_existing_monitor.c`。
2. 從 build 排除原本的 `servo_driver_stub.c`。

目前 mapping：

```text
SERVO_CHANNEL_PITCH -> MotorControlPitch("angle")
SERVO_CHANNEL_ROLL  -> MotorControlYaw("angle")
```

注意：這個專案的需求是 roll / pitch；你現有函式名是 `MotorYaw`，我暫時把它當成 roll 軸使用。如果你的機構真的是 yaw，不是 roll，請把函式改名為 `MotorControlRoll` 或在 adapter 裡改 mapping。

另外，你貼的 `MotorControlPitch()` 和 `MotorControlYaw()` 目前都呼叫同一個 `Servo_GotoAngle(value)`。如果這代表兩個指令其實控制同一顆伺服，端到端時只能動一軸；你需要在底層提供兩路獨立 PWM，例如：

```c
ServoPitch_GotoAngle(value);
ServoRoll_GotoAngle(value);
```

或讓 `Servo_GotoAngle(channel, value)` 支援不同 channel。

## MCUXpresso 匯入方式

1. 安裝 MCUXpresso IDE 與 FRDM-MCXN947 對應 SDK。
2. 建立一個新的 FRDM-MCXN947 C project。
3. 先選一個有 UART console 的範例或 hello_world 範例，比較容易保留 clock/board init。
4. 把本資料夾中的核心 `.c` 和 `.h` 加入專案 Source。
5. 若使用既有 `SMONITORCOMMAND`，額外加入 `adapters/smonitor_emotion_bridge.c/.h`。
6. 先保留 `servo_driver_stub.c`，讓程式只印 log，不控制馬達。
7. 若你使用獨立 `main.c`，修改 `main.c` 的三個平台函式：
   - `platform_uart_read_line()`
   - `platform_uart_write_line()`
   - `platform_delay_ms()`
8. 若你沿用現有 monitor，不需要使用本資料夾的 `main.c`，改用 `EmotionRobotInit()` 和 `EmotionRobotCommand()`。
9. Serial parser 正常後，再新增 `servo_driver_mcxn947.c` 或使用 `adapters/servo_driver_existing_monitor.c`，並從 build 中排除 `servo_driver_stub.c`。

## UART TODO

`main.c` 目前的 `platform_uart_read_line()` 永遠回傳 `false`。在 MCUXpresso 中你需要接到實際 UART：

- 若使用 SDK debug console，可先把 USB CDC/Virtual COM 的輸入接成 line buffer。
- 若使用 LPUART/USART driver，建議做一個簡單 ring buffer，收到 `\n` 後丟給 `command_parser_parse()`。
- 傳給 parser 的字串可以包含或不包含 `\r\n`，checksum parser 已容忍 checksum 後面的 CR/LF。

Serial 設定預設：

```text
baudrate: 115200
data bits: 8
parity: none
stop bits: 1
flow control: none
line ending: LF
```

## PWM TODO

MG995 是一般 RC servo 控制：

```text
PWM frequency: 50 Hz
period: 20 ms
default pulse range: 500 us .. 2500 us
logical angle: 0 .. 180 deg
safe angle: roll/pitch 55 .. 125 deg
center: 90 deg
```

請在 MCUXpresso SDK 裡找 FRDM-MCXN947 可用的 PWM 範例，常見方向包含：

- CTIMER match/PWM example
- SCTimer/PWM example
- FlexPWM example，如果該板與 pin mux 支援

你要實作的是 `servo_driver.h` 的這些函式：

```c
void servo_init(void);
void servo_set_angle(ServoChannel channel, int angle_deg);
void servo_set_pulse_us(ServoChannel channel, uint16_t pulse_us);
void servo_update(void);
void servo_emergency_stop(void);
void servo_reset_to_center(void);
int servo_get_last_angle(ServoChannel channel);
```

`motion_controller` 已經會呼叫 `servo_set_angle()`，所以底層 driver 只需要把角度轉成 pulse，再更新對應 PWM duty。

## 安全角度與方向

預設安全角度在 `safety.h`：

```c
#define ROLL_MIN_DEG 55
#define ROLL_CENTER_DEG 90
#define ROLL_MAX_DEG 125
#define PITCH_MIN_DEG 55
#define PITCH_CENTER_DEG 90
#define PITCH_MAX_DEG 125
```

如果方向相反，不要立刻改 motion profile。先在初始化時設定：

```c
g_safety_config.roll.invert = true;
g_safety_config.pitch.invert = true;
```

如果機構會撞到，先縮小 `MIN/MAX`，例如 70 到 110，再慢慢放大。

## 不接馬達先測 parser

1. 只使用 `servo_driver_stub.c`。
2. 燒錄後開 Tera Term/PuTTY。
3. baudrate 設 115200。
4. 從 PC 端執行：

```bash
python run_manual_test.py
```

5. 選 `ping`，應看到 `$PONG,...`。
6. 選 `happy`，應看到 `$ACK,...`，板端 console 看到 motion/servo stub log。

## 接單顆馬達測試

1. 先只接 roll servo。
2. 外部 5V~6V 電源供應 MG995，FRDM GND 與外部電源 GND 必須共地。
3. FRDM PWM signal 接 MG995 signal。
4. 執行：

```bash
python run_manual_test.py
```

5. 測 `CENTER`、`ROLL_LEFT`、`ROLL_RIGHT`。
6. 若方向相反，設定 roll invert。
7. 若撞機構，縮小 `ROLL_MIN_DEG` / `ROLL_MAX_DEG`。

## 接兩顆馬達測試

1. roll 穩定後再接 pitch。
2. 測 `PITCH_UP`、`PITCH_DOWN`。
3. 測 `reset` 確認兩顆都回 90 度中心附近。
4. 最後才測 12 種 emotion。

## 重要供電提醒

MG995 電流很大，兩顆伺服不可直接吃 FRDM 開發板 5V 腳。

- 使用外部 5V~6V 大電流電源，建議至少 3A，機構負載大時更高。
- 外部電源 GND 必須和 FRDM GND 共地。
- 若馬達抖動、板子重開、Serial 斷線，優先檢查供電、共地、線徑與電源瞬間電流。
