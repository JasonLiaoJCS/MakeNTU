# Jetson Orin Nano UART 可用脚位与测试指南

## Jetson Orin Nano UART 可用脚位

Jetson Orin Nano有4个可用的UART（Universal Asynchronous Receiver-Transmitter）接口，都在J41 40-pin连接器上。

### UART 设备列表

| 设备 | 脚位 | TX | RX | 说明 |
|------|------|----|----|------|
| `/dev/ttyTHS0` | 8, 10 | Pin 8 | Pin 10 | **推荐使用** - 主要UART |
| `/dev/ttyTHS1` | 5, 7 | Pin 5 | Pin 7 | 备用UART |
| `/dev/ttyTHS2` | 11, 13 | Pin 11 | Pin 13 | 备用UART |
| `/dev/ttyTHS3` | 32, 31 | Pin 32 | Pin 31 | 备用UART |

### J41 40-pin 连接器引脚图

```
            ┌─────────────────────┐
            │   J41 40-pin        │
            │   Header            │
┌───────────┼─────────────────────┼───────────┐
│ 1   3V3   │  2   5V             │           │
│ 3   I2C   │  4   5V             │           │
│ 5  UART2_TX│ 6   GND    ⭐       │           │
│ 7  UART2_RX│ 8  UART1_TX        │           │
│ 9   GND   │ 10  UART1_RX ⭐     │           │
│11 UART3_TX│ 12   GPIO           │           │
│13 UART3_RX│ 14   GND            │           │
│15   SPI   │ 16   SPI            │           │
│17   3V3   │ 18   SPI            │           │
│19   SPI   │ 20   GND            │           │
│21   SPI   │ 22   GPIO           │           │
│23   SPI   │ 24   SPI            │           │
│25   GND   │ 26   GPIO           │           │
│27  I2C    │ 28   I2C            │           │
│29  GPIO   │ 30   GND            │           │
│31 UART4_RX│ 32  UART4_TX        │           │
│33  GPIO   │ 34   GND            │           │
│35  SPI    │ 36   GPIO           │           │
│37  GPIO   │ 38   SPI            │           │
│39  GND    │ 40   SPI            │           │
└───────────┴─────────────────────┴───────────┘
```

### 注意事项

1. **地线（GND）**: Pin 6, 9, 14, 20, 25, 30, 34, 39
2. **同板 loopback 只需要把 TX 和 RX 短接**；如果是连外部设备，才需要把 GND 一起接上
3. **电压**: 3.3V逻辑电平（不是5V！）
4. **默认波特率**: 115200 bps

---

## Loopback 测试方法

### 什么是 Loopback 测试？

Loopback（环回测试）用来验证UART硬件是否正常工作。将UART的TX（发送）和RX（接收）脚位直接连接，发送的数据会被立即接收回来。

### 使用跳线进行 Loopback 连接

```
For /dev/ttyTHS0 (推荐):
- 用跳线连接 J41 Pin 8 (UART1_TX) 和 Pin 10 (UART1_RX)
- 同板 loopback 不需要再额外接 GND

╔════════════════════╗
║  J41 Header        ║
║   Pin 8  (TX) ────╲    ╱──── (RX) Pin 10
║                    ╲  /
║      (Jumper wire)  ╲/
║                     ╱╲
║                    /  ╲
║  Pin 6  (GND) 仅在接外部设备时需要
╚════════════════════╝
```

---

## 使用脚本进行测试

### 1. 检测可用的UART设备

```bash
cd /home/asrlab-yian/MakeNTU/Uart
python3 uart_testloopback.py --detect
```

输出示例：
```
=== Available UART Devices ===
  ✓ /dev/ttyTHS0
  ✓ /dev/ttyTHS1
```

### 2. 查看UART设备信息

```bash
python3 uart_testloopback.py --info
```

### 3. 运行 Loopback 测试

**基础用法（测试 /dev/ttyTHS0）:**
```bash
# 需要确保 TX 和 RX 已用跳线连接
python3 uart_testloopback.py --test

# 输出类似：
# ✓ Successfully opened /dev/ttyTHS0
# Test: String test
# Sending: b'Hello_UART'
# Received: b'Hello_UART'
# ✓ PASS - Loopback successful!
```

**测试其他UART:**
```bash
python3 uart_testloopback.py --test --port /dev/ttyTHS1
python3 uart_testloopback.py --test --port /dev/ttyTHS2
python3 uart_testloopback.py --test --port /dev/ttyTHS3
```

**自定义波特率:**
```bash
python3 uart_testloopback.py --test --baudrate 9600
python3 uart_testloopback.py --test --baudrate 115200  # 默认
python3 uart_testloopback.py --test --baudrate 460800
```

### 4. 发送数据测试（不需要 Loopback）

**发送单条消息:**
```bash
python3 uart_testloopback.py --send --message "Hello Jetson"
```

**重复发送:**
```bash
python3 uart_testloopback.py --send --message "Test" --repeat 5
```

**到指定端口发送:**
```bash
python3 uart_testloopback.py --send --port /dev/ttyTHS0 --message "Data Test"
```

---

## 权限问题处理

如果遇到权限错误：

```bash
# 方法1: 给予权限
sudo chmod 666 /dev/ttyTHS0

# 方法2: 将用户添加到 dialout 组（推荐）
sudo usermod -a -G dialout $USER
# 然后重新登录或运行：
newgrp dialout

# 方法3: 用 sudo 运行脚本
sudo python3 uart_testloopback.py --test
```

---

## 完整的数据发送示例

### 方法 1: 使用脚本

```bash
# 发送简单消息
python3 uart_testloopback.py --send --port /dev/ttyTHS0 --message "Init\r\n"

# 循环发送命令
python3 uart_testloopback.py --send --port /dev/ttyTHS0 --message "Sleep" --repeat 10
```

### 方法 2: 使用 Python 代码

```python
import serial
import time

# 打开串口
ser = serial.Serial('/dev/ttyTHS0', 115200, timeout=1.0)

try:
    # 发送数据
    ser.write(b'Sleep\n')
    time.sleep(0.1)
    
    # 读取响应
    response = ser.readline()
    print(f"Response: {response}")
    
finally:
    ser.close()
```

### 方法 3: 使用现有的代码

根据你的配置文件，可以使用现有的 `SerialBridge`:

```python
from emotion_robot_controller.pc_controller.serial.serial_bridge import SerialBridge
import yaml

# 加载配置
with open('emotion_robot_controller/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# 创建串口桥接
bridge = SerialBridge(config)

# 发送命令
bridge.send_command("Sleep")
bridge.send_command("MotorPitch 90")
```

---

## 常见问题排查

### Q1: 收不到任何数据
- ✓ 确认 TX/RX 跳线是否正确连接
- ✓ 检查 GND 是否连接
- ✓ 检查波特率设置是否一致
- ✓ 尝试: `sudo cat /dev/ttyTHS0` 查看原始数据

### Q2: 数据不匹配或乱码
- ✓ 检查波特率是否设置正确
- ✓ 检查电路连接是否松动
- ✓ 尝试较低的波特率（9600）进行测试

### Q3: Permission denied
- ✓ 运行: `sudo chmod 666 /dev/ttyTHS0`
- ✓ 或添加用户到 dialout 组

### Q4: Device not found
- ✓ 检查设备是否真的存在: `ls /dev/ttyTHS*`
- ✓ 可能需要启用UART在系统配置中

---

## 进阶用法

### 使用 strace 调试

```bash
strace -e openat,read,write python3 uart_testloopback.py --test 2>&1 | grep ttyTHS
```

### 使用 minicom 手动通信

```bash
sudo apt-get install minicom
sudo minicom -D /dev/ttyTHS0 -b 115200
```

### 使用 screen 连接

```bash
sudo screen /dev/ttyTHS0 115200
```

按 `Ctrl+A` 然后 `K` 退出。

---

## 配置参考

当前你的 `config.yaml` 配置：

```yaml
serial:
  port: /dev/ttyTHS1          # 使用 UART2
  baudrate: 115200
  timeout_sec: 1.0
  ack_timeout_sec: 1.0
  retry_count: 2
  line_ending: "\n"
```

要改为使用 UART1 (推荐)，修改为：

```yaml
serial:
  port: /dev/ttyTHS0          # 改为 UART1
  baudrate: 115200
  ...
```

---

## 硬件连接检查清单

- [ ] 是否连接了 TX 和 RX 跳线？
- [ ] 是否连接了 GND？
- [ ] 跳线是否接触良好（没有松动）？
- [ ] 使用的是 3.3V 设备（不是 5V）？
- [ ] 波特率设置是否正确？
- [ ] 用户是否有权限访问 `/dev/ttyTHSx`？

---

## 下一步

1. 运行 `python3 uart_testloopback.py --detect` 确认可用设备
2. 准备跳线进行 loopback 连接
3. 运行 `python3 uart_testloopback.py --test` 进行测试
4. 根据测试结果调整配置
5. 集成到你的机器人控制代码中

有任何问题可以查看脚本的详细输出！
