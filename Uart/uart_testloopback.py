#!/usr/bin/env python3
"""
Jetson Orin Nano UART Loopback Test Script

Available UART on Jetson Orin Nano:
- /dev/ttyTHS0 (UART1 - on J41 pins 8/10) - PRIMARY RECOMMENDED
- /dev/ttyTHS1 (UART2 - on J41 pins 5/7) - SECONDARY
- /dev/ttyTHS2 (UART3 - on J41 pins 11/13)
- /dev/ttyTHS3 (UART4 - on J41 pins 32/31)

J41 40-pin Header Layout:
- Pin 8: UART1_TX (/dev/ttyTHS0)
- Pin 10: UART1_RX (/dev/ttyTHS0)
- Pin 5: UART2_TX (/dev/ttyTHS1)
- Pin 7: UART2_RX (/dev/ttyTHS1)
- Pin 11: UART3_TX (/dev/ttyTHS2)
- Pin 13: UART3_RX (/dev/ttyTHS2)
- Pin 32: UART4_TX (/dev/ttyTHS3)
- Pin 31: UART4_RX (/dev/ttyTHS3)
- Pin 6, 9, 14, 20, 25, 39: GND (Ground)

For Loopback Test: Connect TX and RX pins together on one UART port
For a same-board loopback, you do not need to add a separate GND wire. GND matters when wiring an external device.
"""

import serial
import time
import sys
import os
import glob
import subprocess
import argparse


def detect_available_uarts():
    """Detect available UART devices on Jetson Orin Nano"""
    available = []
    for device in glob.glob("/dev/ttyTHS*"):
        try:
            # Try to open each device to check if it's available
            ser = serial.Serial(device, 115200, timeout=0.1)
            ser.close()
            available.append(device)
        except Exception as e:
            print(f"Device {device} not available: {e}")
    
    return available


def check_uart_device_info():
    """Display detailed UART device information"""
    print("\n=== Jetson Orin Nano UART Device Information ===\n")
    
    # Get information about UART devices
    result = subprocess.run(
        ["ls", "-la", "/dev/ttyTHS*"],
        capture_output=True,
        text=True,
        shell=True
    )
    
    if result.stdout:
        print("Available UART Devices:")
        print(result.stdout)
    
    # Check pinmux configuration
    print("\n=== UART Pin Configuration ===")
    pinmux_path = "/sys/kernel/debug/tegra_pinctrl_utils/pinmux"
    if os.path.exists(pinmux_path):
        print("Checking pinmux configuration...")
        result = subprocess.run(
            ["grep", "-i", "uart", pinmux_path],
            capture_output=True,
            text=True
        )
        if result.stdout:
            print(result.stdout)
    else:
        print("Pinmux info not available (requires elevated privileges)")


def loopback_test(port="/dev/ttyTHS0", baudrate=115200, num_bytes=10):
    """
    Perform loopback test on specified UART port
    
    For loopback: Connect TX pin to RX pin on the same UART port
    
    Args:
        port: UART device path (e.g., /dev/ttyTHS0)
        baudrate: Baud rate (default: 115200)
        num_bytes: Number of bytes to send (default: 10)
    """
    print(f"\n=== Starting Loopback Test on {port} ===")
    print(f"Baudrate: {baudrate}")
    print(f"Number of test bytes: {num_bytes}")
    print(f"\nIMPORTANT: Make sure TX and RX pins are connected together!")
    print(f"For a same-board loopback, GND is not required.")
    print(f"For {port}:")
    
    # Print pin information
    port_to_pins = {
        "/dev/ttyTHS0": "J41 pins 8 (TX) and 10 (RX)",
        "/dev/ttyTHS1": "J41 pins 5 (TX) and 7 (RX)",
        "/dev/ttyTHS2": "J41 pins 11 (TX) and 13 (RX)",
        "/dev/ttyTHS3": "J41 pins 32 (TX) and 31 (RX)",
    }
    print(f"Pins: {port_to_pins.get(port, 'Unknown')}")
    print()
    
    try:
        # Open serial port
        ser = serial.Serial(port, baudrate, timeout=1.0)
        print(f"✓ Successfully opened {port}")
        
        # Clear buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.5)
        
        # Test data patterns
        test_patterns = [
            (b"Hello_UART", "String test"),
            (bytes(range(num_bytes)), "Byte sequence test"),
            (b"\x55" * 5 + b"\xAA" * 5, "Pattern test (0x55 and 0xAA)"),
        ]
        
        success_count = 0
        
        for test_data, description in test_patterns:
            print(f"\nTest: {description}")
            print(f"Sending: {test_data}")
            
            # Send data
            ser.write(test_data)
            ser.flush()
            time.sleep(0.2)
            
            # Read back data
            response = ser.read(len(test_data))
            print(f"Received: {response}")
            
            if response == test_data:
                print("✓ PASS - Loopback successful!")
                success_count += 1
            else:
                print("✗ FAIL - Data mismatch!")
                if len(response) == 0:
                    print("  (No data received - check TX/RX connection)")
                else:
                    print(f"  Expected {len(test_data)} bytes, got {len(response)} bytes")
        
        ser.close()
        print(f"\n=== Test Summary ===")
        print(f"Passed: {success_count}/{len(test_patterns)}")
        return success_count == len(test_patterns)
        
    except serial.SerialException as e:
        print(f"✗ Error: {e}")
        print("  - Make sure you have permission to access the serial port")
        print("  - Try: sudo chmod 666 {port}")
        return False


def send_data_test(port="/dev/ttyTHS0", baudrate=115200, message="Hello Jetson", repeat=1):
    """
    Send data through UART
    
    Args:
        port: UART device path
        baudrate: Baud rate
        message: Message to send
        repeat: Number of times to send
    """
    print(f"\n=== Sending Data Test ===")
    print(f"Port: {port}")
    print(f"Baudrate: {baudrate}")
    print(f"Message: {message}")
    print(f"Repeat: {repeat} times")
    
    try:
        ser = serial.Serial(port, baudrate, timeout=1.0)
        print(f"✓ Opened {port}")
        
        for i in range(repeat):
            data_to_send = f"{message} [{i+1}]\n"
            print(f"Sending [{i+1}/{repeat}]: {data_to_send.strip()}")
            
            ser.write(data_to_send.encode())
            ser.flush()
            
            # Try to read response
            response = ser.readline()
            if response:
                print(f"Received: {response.decode(errors='ignore').strip()}")
            
            time.sleep(0.5)
        
        ser.close()
        print("✓ Done")
        return True
        
    except serial.SerialException as e:
        print(f"✗ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Jetson Orin Nano UART Loopback Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Detect available UARTs
  python3 uart_testloopback.py --detect
  
  # Run loopback test on /dev/ttyTHS0 (default)
  python3 uart_testloopback.py --test
  
  # Run loopback test on /dev/ttyTHS1
  python3 uart_testloopback.py --test --port /dev/ttyTHS1
  
  # Send data
  python3 uart_testloopback.py --send --message "Hello UART"
  
  # Show device info
  python3 uart_testloopback.py --info
        """
    )
    
    parser.add_argument(
        "--detect",
        action="store_true",
        help="Detect available UART devices"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run loopback test"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send test data (no loopback required)"
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show UART device information"
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyTHS0",
        help="UART device path (default: /dev/ttyTHS0)"
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)"
    )
    parser.add_argument(
        "--message",
        default="Hello from Jetson Orin Nano",
        help="Message to send"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to send message"
    )
    
    args = parser.parse_args()
    
    # If no argument provided, show help
    if not any([args.detect, args.test, args.send, args.info]):
        parser.print_help()
        return
    
    # Show available UARTs
    if args.detect:
        print("\n=== Available UART Devices ===")
        uarts = detect_available_uarts()
        if uarts:
            for uart in uarts:
                print(f"  ✓ {uart}")
        else:
            print("  No UART devices found")
    
    # Show device info
    if args.info:
        check_uart_device_info()
    
    # Run loopback test
    if args.test:
        loopback_test(args.port, args.baudrate)
    
    # Send data
    if args.send:
        send_data_test(args.port, args.baudrate, args.message, args.repeat)


if __name__ == "__main__":
    main()
