#!/usr/bin/env python
"""
GPU Enumeration Script for Metashape

This script can be run independently to test GPU detection and
configuration in Metashape before running the full processing pipeline.
"""

import Metashape
import sys

def enumerate_gpus():
    """Enumerate and display all available GPU devices detected by Metashape."""
    print("Enumerating GPU devices available to Metashape...")
    
    try:
        gpu_devices = Metashape.app.enumGPUDevices()
        
        if not gpu_devices:
            print("No GPU devices detected by Metashape.")
            return []
            
        print(f"Found {len(gpu_devices)} GPU device(s):")
        for i, device in enumerate(gpu_devices):
            # Handle device as a dictionary instead of an object
            if isinstance(device, dict):
                device_info = []
                for key, value in device.items():
                    device_info.append(f"{key}: {value}")
                print(f"  GPU {i}: {', '.join(device_info)}")
            else:
                # Fallback in case it's not a dictionary
                print(f"  GPU {i}: {device}")
            
        # Print the current GPU mask
        print(f"\nCurrent GPU mask: {Metashape.app.gpu_mask}")
        print(f"Binary representation: {bin(Metashape.app.gpu_mask)}")
        
        return gpu_devices
    except Exception as e:
        print(f"Error enumerating GPUs: {e}")
        import traceback
        traceback.print_exc()
        return []

def test_gpu_settings():
    """Test various GPU settings and report their effects."""
    gpu_devices = enumerate_gpus()
    
    if not gpu_devices:
        print("Cannot test GPU settings without available devices.")
        return
    
    print("\n--- Testing GPU Configurations ---")
    
    # Test enabling all GPUs
    gpu_mask = 0
    for i in range(len(gpu_devices)):
        gpu_mask |= (1 << i)
    
    print(f"\nEnabling all GPUs with mask: {gpu_mask} (binary: {bin(gpu_mask)})")
    try:
        Metashape.app.gpu_mask = gpu_mask
        print(f"Updated GPU mask: {Metashape.app.gpu_mask}")
    except Exception as e:
        print(f"Error setting GPU mask: {e}")
    
    # CPU/GPU settings
    try:
        print(f"\nCPU enable status: {Metashape.app.cpu_enable}")
        print("Setting CPU enable to False (to prioritize GPU)...")
        Metashape.app.cpu_enable = False
        print(f"Updated CPU enable status: {Metashape.app.cpu_enable}")
    except Exception as e:
        print(f"Error configuring CPU settings: {e}")

if __name__ == "__main__":
    print("Metashape GPU Enumeration Tool")
    print("-----------------------------")
    print(f"Metashape Version: {Metashape.app.version}")
    
    gpus = enumerate_gpus()
    
    if "--test" in sys.argv:
        test_gpu_settings()
    
    print("\nTo test GPU settings, run with --test flag")
    print("Example: python enumerate_gpus.py --test") 