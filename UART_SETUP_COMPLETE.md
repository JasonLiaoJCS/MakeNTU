# Jetson Orin Nano UART 测试 - 设置完成

## 📋 你的设备上可用的 UART

检测结果显示你的 Jetson Orin Nano 上有以下 UART 可用：

- ✓ **`/dev/ttyTHS1`** (J41 pins 5/7) - UART2
- ✓ **`/dev/ttyTHS2`** (J41 pins 11/13) - UART3

## 📁 已创建的文件

### 1. **[uart_testloopback.py](./Uart/uart_testloopback.py)** - 主测试脚本
完整的 UART 测试工具，支持：
- 自动检测可用UART设备
- Loopback 测试（验证硬件）
- 数据发送测试
- 查看设备信息

### 2. **[UART_GUIDE.md](./UART_GUIDE.md)** - 详细使用指南
包含：
- Jetson Orin Nano UART 完整信息
- J41 40-pin 脚位分布
- Loopback 硬件接线方法
- 常见问题排查
- 多种使用示例

### 3. **[UART_QUICK_REFERENCE.txt](./UART_QUICK_REFERENCE.txt)** - 快速参考卡
包含：
- 常用命令速查
- 硬件接线图
- 脚位对照表
- 权限问题解决

## 🚀 快速开始

### 步骤1: 检查可用UART
```bash
cd ~/MakeNTU/Uart
python3 uart_testloopback.py --detect
```

**你的设备输出：**
```
=== Available UART Devices ===
  ✓ /dev/ttyTHS1
  ✓ /dev/ttyTHS2
```

### 步骤2: 进行 Loopback 测试
准备硬件：用跳线连接 TX 和 RX 脚

**对于 /dev/ttyTHS1 (推荐):**
- J41 Pin 5 (TX) ──── 跳线 ──── J41 Pin 7 (RX)
- J41 Pin 6 or 9 (GND) ──── 接地

```bash
python3 uart_testloopback.py --test --port /dev/ttyTHS1
```

**或对于 /dev/ttyTHS2:**
```bash
python3 uart_testloopback.py --test --port /dev/ttyTHS2
```

### 步骤3: 发送数据
```bash
# 发送简单消息
python3 uart_testloopback.py --send --port /dev/ttyTHS1 --message "Hello Jetson"

# 重复发送
python3 uart_testloopback.py --send --port /dev/ttyTHS1 --message "Test" --repeat 5
```

## 📍 J41 脚位参考

### UART 脚位
| 设备 | TX 脚 | RX 脚 | 文件 |
|------|-------|-------|------|
| UART1 | 8 | 10 | /dev/ttyTHS0 |
| **UART2** | **5** | **7** | **/dev/ttyTHS1** ✓ |
| **UART3** | **11** | **13** | **/dev/ttyTHS2** ✓ |
| UART4 | 32 | 31 | /dev/ttyTHS3 |

**GND 脚**: 6, 9, 14, 20, 25, 30, 34, 39（都相同）

## ⚠️ 常见问题

### Q: Permission denied
```bash
# 解决方案
sudo chmod 666 /dev/ttyTHS1
```

### Q: 收不到数据
- ✓ 检查 TX/RX 是否真的连接
- ✓ 检查 GND 是否连接
- ✓ 检查波特率是否一致（默认 115200）

### Q: 数据乱码
- 尝试较低的波特率：`--baudrate 9600`

## 🔧 集成到你的代码

你现有的 `config.yaml` 配置使用 `/dev/ttyTHS1`：

```yaml
serial:
  port: /dev/ttyTHS1
  baudrate: 115200
  timeout_sec: 1.0
```

这与你的可用设备一致！现在可以直接使用：

```python
from emotion_robot_controller.pc_controller.serial.serial_bridge import SerialBridge
import yaml

with open('emotion_robot_controller/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

bridge = SerialBridge(config)
bridge.send_command("Sleep")
bridge.send_command("MotorPitch 90")
```

## 📚 进阶工具

### 使用 minicom 互动连接
```bash
sudo apt-get install minicom  # 如果还没安装
sudo minicom -D /dev/ttyTHS1 -b 115200
```
按 `Ctrl+A` 然后 `K` 退出

### 使用 screen 连接
```bash
sudo screen /dev/ttyTHS1 115200
```
按 `Ctrl+A` 然后 `\` 退出

### 使用 cat 查看原始数据
```bash
sudo cat /dev/ttyTHS1
```

## ✅ 检查清单

- [ ] 已运行 `python3 uart_testloopback.py --detect` 确认可用设备
- [ ] 准备了跳线进行 loopback 连接
- [ ] 已运行 loopback 测试 `--test` 命令
- [ ] 已测试数据发送 `--send` 命令
- [ ] 已验证权限设置正确
- [ ] 已准备集成到机器人控制代码

## 🎯 下一步

1. **硬件测试**：按照步骤1-3 进行完整的硬件验证
2. **代码集成**：使用现有的 `SerialBridge` 发送实际命令
3. **调试优化**：根据实际需求调整波特率和超时设置

---

**有任何问题，查看详细指南：**
- [UART_GUIDE.md](./UART_GUIDE.md) - 完整教程
- [UART_QUICK_REFERENCE.txt](./UART_QUICK_REFERENCE.txt) - 快速查找

**脚本使用帮助：**
```bash
python3 uart_testloopback.py --help
```
