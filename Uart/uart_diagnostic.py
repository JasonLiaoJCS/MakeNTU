#!/usr/bin/env python3
"""
Jetson UART 诊断工具 - 检查UART实际是否在工作
"""

import serial
import time
import sys
import subprocess

def check_uart_with_strace(port="/dev/ttyTHS1"):
    """使用 strace 追踪UART操作"""
    print(f"\n=== 使用 strace 追踪UART操作 ===\n")
    
    cmd = [
        'strace', '-e', 'openat,read,write,ioctl',
        'python3', '-c',
        f"""
import serial
import time
ser = serial.Serial('{port}', 115200, timeout=0.5)
print('Opened')
ser.write(b'TEST')
time.sleep(0.1)
data = ser.read(4)
print(f'Got: {{data}}')
ser.close()
"""
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stderr)

def check_uart_loopback_advanced(port="/dev/ttyTHS1"):
    """高级loopback测试 - 检查单向通讯"""
    print(f"\n=== 高级UART诊断测试 ===\n")
    print(f"测试端口: {port}")
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
        print(f"✓ 成功打开 {port}")
        print(f"  Port: {ser.port}")
        print(f"  Baudrate: {ser.baudrate}")
        print(f"  Timeout: {ser.timeout}")
        print(f"  Port settings: {ser.get_settings()}")
        
        # 清除缓冲区
        print("\n[1] 清除缓冲区...")
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.3)
        
        # 检查初始缓冲区
        initial = ser.in_waiting
        print(f"✓ 初始缓冲区字节数: {initial}")
        
        # 测试写入
        print("\n[2] 写入测试数据...")
        test_data = b"LOOPBACK_TEST_123"
        bytes_written = ser.write(test_data)
        ser.flush()
        print(f"✓ 写入了 {bytes_written} 字节: {test_data}")
        
        time.sleep(0.2)
        
        # 检查是否有数据在缓冲区
        bytes_waiting = ser.in_waiting
        print(f"✓ 缓冲区等待字节数: {bytes_waiting}")
        
        if bytes_waiting > 0:
            print(f"✓ 检测到 {bytes_waiting} 字节！")
            response = ser.read(bytes_waiting)
            print(f"✓ 读取数据: {response}")
            
            if response == test_data:
                print("✓✓✓ LOOPBACK 成功！TX/RX连接正常！")
            else:
                print(f"⚠ 数据不匹配")
                print(f"  发送: {test_data}")
                print(f"  接收: {response}")
        else:
            print("✗ 没有数据返回 - 可能原因：")
            print("  1. TX/RX 脚位没有用跳线连接")
            print("  2. 跳线接触不良")
            print("  3. UART 可能被禁用或占用")
        
        ser.close()
        
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()

def check_serial_module():
    """检查 pyserial 版本"""
    print(f"\n=== PySerial 信息 ===\n")
    import serial as ser_module
    print(f"PySerial 版本: {ser_module.VERSION}")
    print(f"PySerial 位置: {ser_module.__file__}")

if __name__ == "__main__":
    check_serial_module()
    check_uart_loopback_advanced()
    # check_uart_with_strace()  # 需要 strace 安装
