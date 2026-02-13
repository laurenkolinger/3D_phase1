#!/usr/bin/env python3
"""
Step 0: Frame Extraction

This script extracts frames from video files according to the configuration
specified in analysis_params.yaml.
"""

import os
import cv2
import numpy as np
from pathlib import Path
import logging
import subprocess
import re
import platform
from config import (
    VIDEO_SOURCE_DIRECTORY,
    DIRECTORIES,
    FRAMES_PER_TRANSECT,
    PROJECT_NAME,
    update_tracking,
    get_transect_status,
    initialize_tracking
)
import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(DIRECTORIES["logs"], f"step0_{PROJECT_NAME}.log")),
        logging.StreamHandler()
    ]
)

def extract_frames_ffmpeg(video_path, output_dir, frames_per_transect, video_name, start_number=1):
    """
    Extract frames from a video file using FFmpeg with TIFF format (rgb24) and hardware acceleration.
    
    Args:
        video_path (str): Path to the video file
        output_dir (str): Directory to save frames
        frames_per_transect (int): Number of frames to extract. Must be > 0 if called.
        video_name (str): Base name of the video file for frame naming within output_dir
        start_number (int): Starting frame number for output filenames (default: 1)
    
    Returns:
        tuple: (frames_extracted, extracted_frame_paths, video_length_seconds, total_video_frames)
    """
    # Open video file with OpenCV just to get properties
    logging.info(f"Opening video: {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    # Calculate video length in seconds
    video_length_seconds = total_frames / fps if fps > 0 else 0
    
    logging.info(f"Video properties: {width}x{height}, {fps} fps, {total_frames} frames, {video_length_seconds:.2f} seconds")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Calculate fps value for the extraction
    if frames_per_transect <= 0:
        raise ValueError(f"frames_per_transect must be > 0. Found {frames_per_transect}")
    if video_length_seconds <= 0:
        # This case should ideally be handled by the caller if frames_per_transect > 0
        logging.warning(f"Video {video_name} length is {video_length_seconds:.2f}s. Cannot calculate extraction FPS if frames are requested.")
        # FFmpeg might handle fps=0 or fps=inf, or it might error.
        # If frames_per_transect > 0, this combination is problematic.
        # The calling function (process_transect) should avoid calling this if part_duration is 0 and frames are requested.
        # If it's called with frames_per_transect > 0 and video_length_seconds <=0, let ffmpeg try or fail.
        # For safety, if frames are requested from a zero-length video, it's an issue.
        if frames_per_transect > 0:
             raise ValueError(f"Video length must be > 0 seconds to extract {frames_per_transect} frames. Found {video_length_seconds:.2f}s for {video_name}")
        extract_fps = 0 # Or handle as error, though frames_per_transect should be 0 here.
    else:
        extract_fps = frames_per_transect / video_length_seconds
    
    logging.info(f"Setting fps={extract_fps} to extract {frames_per_transect} frames from {video_length_seconds:.2f}s video for {video_name}")
    
    # Extract frames using FFmpeg with TIFF format
    logging.info(f"Extracting frames using 16-bit TIFF format with rgb48le")
    print(f"Starting 16-bit TIFF frame extraction from {os.path.basename(video_path)}")
    
    # Define output pattern for the frames using video_name and 5-digit counter
    output_pattern = os.path.join(output_dir, f"{video_name}_%05d.tiff")
    
    # Hardware acceleration setup
    system = platform.system()
    if system == 'Darwin':  # macOS
        decoder_args = ['-hwaccel', 'videotoolbox']
        logging.info("Using VideoToolbox hardware acceleration")
    elif system == 'Linux':
        decoder_args = ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda']
        logging.info("Attempting CUDA hardware acceleration")
    else:
        decoder_args = []
    
    # FFmpeg command for TIFF extraction with GPU hardware decoding
    ffmpeg_cmd = [
        'ffmpeg'
    ] + decoder_args + [
        '-i', video_path,
        '-vf', f'fps={extract_fps}',    # Set frames per second for extraction
        '-c:v', 'tiff',                 # Use TIFF codec 
        '-pix_fmt', 'rgb48le',          # 16-bit RGB (little-endian) for better quality
        '-compression_level', '0',      # No compression
        '-start_number', str(start_number),  # Set starting frame number
        '-v', 'info',                   # Show information
        '-stats',                       # Show progress
        output_pattern
    ]
    
    try:
        # Run FFmpeg with output visible to user
        print(f"Running FFmpeg for 16-bit TIFF extraction...")
        
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        # Display FFmpeg output to monitor hardware acceleration
        for line in process.stdout:
            line = line.strip()
            print(line)
            # Look for hardware acceleration confirmation messages
            if 'hwaccel' in line.lower() or 'videotoolbox' in line.lower():
                print(f"HARDWARE ACCELERATION INDICATOR: {line}")
        
        process.wait()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd)
        
        # Get list of extracted frames
        extracted_frame_paths = sorted([
            os.path.join(output_dir, f) for f in os.listdir(output_dir) 
            if f.endswith('.tiff')
        ])
        frames_extracted = len(extracted_frame_paths)
        
        # Check size of first frame
        if frames_extracted > 0:
            size_mb = os.path.getsize(extracted_frame_paths[0]) / (1024 * 1024)
            print(f"First 16-bit TIFF frame size: {size_mb:.2f} MB")
        else:
            print("No frames were extracted!")
    
    except subprocess.CalledProcessError as e:
        error_msg = "Unknown FFmpeg error"
        if hasattr(e, 'output') and e.output:
            error_msg = e.output
        elif hasattr(e, 'stderr') and e.stderr:
            error_msg = e.stderr.decode()
            
        logging.error(f"FFmpeg error: {error_msg}")
        print(f"ERROR: FFmpeg failed: {error_msg}")
        
        # Fall back to software decoding
        print("Attempting with software decoding...")
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'fps={extract_fps}',
            '-c:v', 'tiff',
            '-pix_fmt', 'rgb48le',
            '-compression_level', '0',
            output_pattern
        ]
        
        try:
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
            
            # Get list of extracted frames
            extracted_frame_paths = sorted([
                os.path.join(output_dir, f) for f in os.listdir(output_dir) 
                if f.endswith('.tiff')
            ])
            frames_extracted = len(extracted_frame_paths)
            
        except Exception as e2:
            logging.error(f"Software decoding also failed: {str(e2)}")
            return 0, [], video_length_seconds, total_frames
    
    if frames_extracted == 0:
        logging.error("No frames were extracted!")
        print("ERROR: Failed to extract any frames")
    else:
        logging.info(f"Successfully extracted {frames_extracted} 16-bit TIFF frames")
        print(f"SUCCESS: Extracted {frames_extracted} 16-bit TIFF frames to {output_dir}")
    
    return frames_extracted, extracted_frame_paths, video_length_seconds, total_frames

def extract_frames_ffmpeg_alternative(video_path, output_dir, frames_per_transect, video_name):
    """
    Alternative high-quality extraction method for cinema footage using EXR format.
    
    Args:
        video_path (str): Path to the video file
        output_dir (str): Directory to save frames
        frames_per_transect (int): Number of frames to extract
        video_name (str): Base name of the video file for frame naming
    
    Returns:
        tuple: (frames_extracted, extracted_frame_paths, video_length_seconds, total_video_frames)
    """
    # Open video file with OpenCV just to get properties
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    # Calculate video length in seconds
    video_length_seconds = total_frames / fps if fps > 0 else 0
    
    # Calculate which frames to extract
    if frames_per_transect <= 0:
        raise ValueError(f"frames_per_transect must be > 0. Found {frames_per_transect}")
        
    if frames_per_transect > total_frames:
        logging.warning(f"Requested {frames_per_transect} frames, but video only has {total_frames} frames. Extracting all frames.")
        frame_indices = np.arange(total_frames)
    else:
        frame_indices = np.linspace(0, total_frames - 1, frames_per_transect, dtype=int)
    
    extracted_frame_paths = []
    frames_extracted = 0
    
    print(f"Starting direct extraction of {len(frame_indices)} frames using individual frame method")
    
    # Try each format in order of preference until one works
    formats_to_try = [
        ('.tiff', ['-c:v', 'tiff', '-pix_fmt', 'rgb48le']),   # 16-bit TIFF
    ]
    
    # Hardware acceleration
    system = platform.system()
    if system == 'Darwin':
        decoder_args = ['-hwaccel', 'videotoolbox']
    elif system == 'Linux':
        decoder_args = ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda']
    else:
        decoder_args = []
    
    # Find which format works with this video
    working_format = None
    for ext, params in formats_to_try:
        try:
            test_output = os.path.join(output_dir, f"test_frame{ext}")
            test_cmd = [
                'ffmpeg'
            ] + decoder_args + [
                '-ss', '0',
                '-i', video_path,
                '-vframes', '1'
            ] + params + ['-y', test_output]
            
            subprocess.run(test_cmd, check=True, capture_output=True)
            
            if os.path.exists(test_output):
                working_format = (ext, params)
                os.remove(test_output)
                logging.info(f"Selected {ext} format for extraction.")
                break
        except Exception as e:
            logging.debug(f"Format test failed for {ext}: {e}")
            continue
    
    if not working_format:
        logging.error("Could not find a working high-quality format for this video")
        return 0, [], video_length_seconds, total_frames
    
    ext, params = working_format
    print(f"Using format: {ext} with parameters: {params}")
    
    # Extract each frame using the working format
    for i, frame_idx in enumerate(frame_indices):
        if i % max(1, len(frame_indices) // 10) == 0:
            progress = (i / len(frame_indices)) * 100
            print(f"Extraction progress: {progress:.1f}% ({i}/{len(frame_indices)})")
        
        # Calculate timestamp for this frame
        timestamp = frame_idx / fps if fps > 0 else 0
        
        # Output path using video_name and 1-based counter
        frame_counter = i + 1
        output_path = os.path.join(output_dir, f"{video_name}_{frame_counter:05d}{ext}")
        
        # Extract this specific frame
        frame_cmd = [
            'ffmpeg'
        ] + decoder_args + [
            '-ss', str(timestamp),
            '-i', video_path,
            '-vframes', '1'
        ] + params + [
            '-v', 'error',
            output_path
        ]
        
        try:
            subprocess.run(frame_cmd, check=True, 
                          stdout=subprocess.DEVNULL, 
                          stderr=subprocess.PIPE)
            
            if os.path.exists(output_path):
                extracted_frame_paths.append(output_path)
                frames_extracted += 1
                
                # Check size of first frame
                if frames_extracted == 1:
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    print(f"First frame size: {size_mb:.2f} MB using {ext} format")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error extracting frame {frame_idx}: {e.stderr.decode() if hasattr(e, 'stderr') else str(e)}")
    
    return frames_extracted, extracted_frame_paths, video_length_seconds, total_frames

def extract_frames_ffmpeg_png(video_path, output_dir, frames_per_transect, video_name):
    """
    Extract frames from a video file using FFmpeg for highest quality as PNG (lossless).
    
    Args:
        video_path (str): Path to the video file
        output_dir (str): Directory to save frames
        frames_per_transect (int): Number of frames to extract
        video_name (str): Base name of the video file for frame naming
    
    Returns:
        tuple: (frames_extracted, extracted_frame_paths, video_length_seconds, total_video_frames)
    """
    # Open video file with OpenCV just to get properties
    logging.info(f"Opening video: {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    # Calculate video length in seconds
    video_length_seconds = total_frames / fps if fps > 0 else 0
    
    logging.info(f"Video properties: {width}x{height}, {fps} fps, {total_frames} frames, {video_length_seconds:.2f} seconds")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Calculate which frames to extract
    if frames_per_transect <= 0:
        raise ValueError(f"frames_per_transect must be > 0. Found {frames_per_transect}")
        
    if frames_per_transect > total_frames:
        logging.warning(f"Requested {frames_per_transect} frames, but video only has {total_frames} frames. Extracting all frames.")
        frame_indices = np.arange(total_frames)
    else:
        frame_indices = np.linspace(0, total_frames - 1, frames_per_transect, dtype=int)
    
    extracted_frame_paths = []
    frames_extracted = 0
    
    # Hardware acceleration
    system = platform.system()
    if system == 'Darwin':
        decoder_args = ['-hwaccel', 'videotoolbox']
    elif system == 'Linux':
        decoder_args = ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda']
    else:
        decoder_args = []
    
    print(f"Starting direct PNG extraction of {len(frame_indices)} frames...")
    logging.info(f"Using direct PNG extraction method for highest quality")
    
    # Extract frames directly one by one (slower but better quality)
    for i, frame_idx in enumerate(frame_indices):
        if i % max(1, len(frame_indices) // 10) == 0:
            progress = (i / len(frame_indices)) * 100
            print(f"PNG extraction progress: {progress:.1f}% ({i}/{len(frame_indices)})")
        
        # Calculate exact timestamp for this frame
        timestamp = frame_idx / fps if fps > 0 else 0
        
        # Output file path (PNG for lossless quality)
        frame_counter = i + 1
        output_path = os.path.join(output_dir, f"{video_name}_{frame_counter:05d}.png")
        
        # Extract just this one frame as PNG (lossless)
        frame_cmd = [
            'ffmpeg'
        ] + decoder_args + [
            '-ss', str(timestamp),   # Seek to timestamp
            '-i', video_path,
            '-vframes', '1',         # Extract just one frame
            '-pix_fmt', 'rgb24',     # Use RGB color space
            '-vsync', '0',           # No frame rate conversion
            '-vf', 'format=rgb24',   # Force RGB format
            '-compression_level', '0', # No compression (max quality)
            output_path
        ]
        
        try:
            # Run with minimal output to avoid clutter
            subprocess.run(frame_cmd, check=True, 
                          stdout=subprocess.DEVNULL, 
                          stderr=subprocess.PIPE)
            
            if os.path.exists(output_path):
                extracted_frame_paths.append(output_path)
                frames_extracted += 1
                
                # Check size of first frame
                if frames_extracted == 1:
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    print(f"First PNG frame size: {size_mb:.2f} MB")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error extracting PNG frame {frame_idx}: {e.stderr.decode() if e.stderr else str(e)}")
    
    if frames_extracted == 0:
        logging.error("No PNG frames were extracted!")
        print("ERROR: Failed to extract any PNG frames")
    else:
        logging.info(f"Successfully extracted {frames_extracted} PNG frames")
        print(f"SUCCESS: Extracted {frames_extracted} lossless PNG frames to {output_dir}")
    
    return frames_extracted, extracted_frame_paths, video_length_seconds, total_frames

def extract_frames_png(video_path, output_dir, frames_per_transect, video_name):
    """
    Extract frames as PNG files for lossless quality.
    
    Args:
        video_path (str): Path to the video file
        output_dir (str): Directory to save frames
        frames_per_transect (int): Number of frames to extract
        video_name (str): Base name of the video file for frame naming
    
    Returns:
        tuple: (frames_extracted, extracted_frame_paths, video_length_seconds, total_video_frames)
    """
    # Open video file
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    # Calculate video length in seconds
    video_length_seconds = total_frames / fps if fps > 0 else 0
    
    # Calculate which frames to extract
    if frames_per_transect <= 0:
        raise ValueError(f"frames_per_transect must be > 0. Found {frames_per_transect}")
        
    if frames_per_transect > total_frames:
        logging.warning(f"Requested {frames_per_transect} frames, but video only has {total_frames} frames. Extracting all frames.")
        frame_indices = np.arange(total_frames)
    else:
        frame_indices = np.linspace(0, total_frames - 1, frames_per_transect, dtype=int)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract frames as PNG (lossless)
    frames_extracted = 0
    extracted_frame_paths = []
    
    for i, frame_idx in enumerate(frame_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret:
            # Save as PNG for lossless quality using video_name and 1-based counter
            frame_counter = i + 1
            output_path = os.path.join(output_dir, f"{video_name}_{frame_counter:05d}.png")
            cv2.imwrite(output_path, frame)
            frames_extracted += 1
            extracted_frame_paths.append(output_path)
    
    cap.release()
    return frames_extracted, extracted_frame_paths, video_length_seconds, total_frames

def extract_frames(video_path, output_dir, frames_per_transect, video_name):
    """
    Extract frames from a video file using OpenCV (legacy method).
    
    Args:
        video_path (str): Path to the video file
        output_dir (str): Directory to save frames
        frames_per_transect (int): Number of frames to extract
        video_name (str): Base name of the video file for frame naming
    
    Returns:
        tuple: (frames_extracted, extracted_frame_paths, video_length_seconds, total_video_frames)
    """
    # Open video file
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    # Calculate video length in seconds
    video_length_seconds = total_frames / fps if fps > 0 else 0
    
    if frames_per_transect <= 0:
        raise ValueError(f"frames_per_transect must be greater than 0. Found {frames_per_transect}")
        
    if frames_per_transect > total_frames:
        logging.warning(f"Requested {frames_per_transect} frames, but video only has {total_frames} frames. Extracting all frames.")
        frame_indices = np.arange(total_frames)
    else:
        frame_indices = np.linspace(0, total_frames - 1, frames_per_transect, dtype=int)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract frames
    frames_extracted = 0
    extracted_frame_paths = []
    
    for i, frame_idx in enumerate(frame_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret:
            frame_counter = i + 1 # Use 1-based counter
            output_path = os.path.join(output_dir, f"{video_name}_{frame_counter:05d}.jpg")
            cv2.imwrite(output_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
            frames_extracted += 1
            extracted_frame_paths.append(output_path)
    
    cap.release()
    return frames_extracted, extracted_frame_paths, video_length_seconds, total_frames

def process_transect(transect_id, video_paths_for_transect):
    """
    Process a single transect, which may consist of one or more video files (parts).
    
    Args:
        transect_id (str): The base identifier for the transect (e.g., TCRMP20240215_3ddemo_FLC_T5)
        video_paths_for_transect (list[str]): List of paths to video files for this transect, sorted by part.
        
    Returns:
        str, bool: Transect ID and success status
    """
    output_dir_final = os.path.join(DIRECTORIES["frames"], transect_id)
    
    initialize_tracking(transect_id) 
    
    status = get_transect_status(transect_id)
    if status.get("Step 0 complete", "False") == "True":
        logging.info(f"Transect {transect_id} already processed, skipping...")
        return transect_id, True
        
    try:
        start_time = datetime.datetime.now()
        logging.info(f"Starting frame extraction for transect {transect_id} with {len(video_paths_for_transect)} part(s)")

        part_details = [] 
        total_video_length_seconds_all_parts = 0
        grand_total_video_frames_all_parts = 0

        for video_path_part in video_paths_for_transect:
            logging.info(f"Getting properties for part: {video_path_part}")
            cap = cv2.VideoCapture(video_path_part)
            if not cap.isOpened():
                # Log specific part failure and continue if possible, or raise
                logging.error(f"Could not open video file part: {video_path_part} for transect {transect_id}")
                # Option: skip this part or raise error for whole transect
                raise ValueError(f"Could not open video file part: {video_path_part} for transect {transect_id}")

            part_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            part_fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()
            
            part_duration_seconds = 0
            if part_fps > 0 and part_total_frames > 0 : # Ensure both are positive
                part_duration_seconds = part_total_frames / part_fps
            else:
                logging.warning(f"Video part {video_path_part} has invalid properties: FPS {part_fps}, Total Frames {part_total_frames}. Duration set to 0.")

            if part_duration_seconds <= 0:
                logging.warning(f"Video part {video_path_part} has calculated duration of {part_duration_seconds:.2f}s.")
            
            total_video_length_seconds_all_parts += part_duration_seconds
            grand_total_video_frames_all_parts += part_total_frames
            part_basename = os.path.splitext(os.path.basename(video_path_part))[0]
            part_details.append({
                "path": video_path_part, 
                "duration": part_duration_seconds, 
                "total_frames": part_total_frames,
                "basename": part_basename
            })

        if FRAMES_PER_TRANSECT <= 0:
            logging.info(f"FRAMES_PER_TRANSECT is {FRAMES_PER_TRANSECT}. No frames will be extracted for transect {transect_id}.")
            update_tracking(transect_id, {
                "Status": "No frames requested",
                "Step 0 complete": "True",
                "Video Length (s)": f"{total_video_length_seconds_all_parts:.2f}",
                "Total Video Frames": str(grand_total_video_frames_all_parts),
                "Frames Extracted": "0",
                "Video Source": ", ".join(video_paths_for_transect),
                "Extraction Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Step 0 start time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Step 0 end time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Step 0 processing time (s)": str((datetime.datetime.now() - start_time).total_seconds()),
                "Frames directory": output_dir_final,
                "Notes": f"FRAMES_PER_TRANSECT set to {FRAMES_PER_TRANSECT}. No frames extracted."
            })
            os.makedirs(output_dir_final, exist_ok=True) # Create directory even if no frames
            return transect_id, True

        if total_video_length_seconds_all_parts <= 0:
             error_msg = f"Total video length for transect {transect_id} is zero or negative ({total_video_length_seconds_all_parts:.2f}s). Cannot extract frames."
             logging.error(error_msg)
             raise ValueError(error_msg)

        os.makedirs(output_dir_final, exist_ok=True)
        global_frame_output_counter = 1
        cumulative_frames_extracted_count = 0
        all_final_frame_paths = [] # To store paths of successfully moved frames for logging/verification

        for idx, part_info in enumerate(part_details):
            part_path = part_info["path"]
            part_duration = part_info["duration"]
            part_basename = part_info["basename"]
            
            frames_to_extract_for_this_part = 0
            if part_duration > 0 and total_video_length_seconds_all_parts > 0: # Ensure part_duration is positive for calc
                frames_to_extract_for_this_part = round(FRAMES_PER_TRANSECT * (part_duration / total_video_length_seconds_all_parts))
            
            if frames_to_extract_for_this_part == 0 and part_duration > 0:
                 # If rounding results in 0 frames for a part that has some duration,
                 # and we are aiming to extract frames overall, consider extracting at least one.
                 # This is a policy choice. For now, stick to strict proportionality.
                 # If FRAMES_PER_TRANSECT is very low, some parts might get 0.
                 logging.info(f"Part {part_path} (duration {part_duration:.2f}s) allocated 0 frames due to rounding/proportionality. Total requested: {FRAMES_PER_TRANSECT}.")


            if frames_to_extract_for_this_part <= 0:
                logging.info(f"Skipping frame extraction for part {part_path} as {frames_to_extract_for_this_part} frames are to be extracted.")
                continue

            logging.info(f"Extracting {frames_to_extract_for_this_part} frames from part {part_path} (duration: {part_duration:.2f}s) for transect {transect_id}")
            
            num_actually_extracted, frame_paths, _, _ = extract_frames_ffmpeg(
                part_path,
                output_dir_final,
                frames_to_extract_for_this_part,
                transect_id,
                start_number=global_frame_output_counter
            )

            if num_actually_extracted > 0:
                all_final_frame_paths.extend(sorted(frame_paths))
                global_frame_output_counter += num_actually_extracted
            
            cumulative_frames_extracted_count += num_actually_extracted

        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        extraction_timestamp = end_time.strftime("%Y-%m-%d %H:%M:%S")
        
        update_tracking(transect_id, {
            "Status": "Frames extracted",
            "Step 0 complete": "True",
            "Video Length (s)": f"{total_video_length_seconds_all_parts:.2f}",
            "Total Video Frames": str(grand_total_video_frames_all_parts),
            "Frames Extracted": str(cumulative_frames_extracted_count),
            "Video Source": ", ".join(video_paths_for_transect), 
            "Extraction Timestamp": extraction_timestamp,
            "Step 0 start time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Step 0 end time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Step 0 processing time (s)": f"{processing_time:.2f}",
            "Frames directory": output_dir_final,
            "Notes": f"Extracted {cumulative_frames_extracted_count} frames from {grand_total_video_frames_all_parts} total frames across {len(video_paths_for_transect)} part(s) ({total_video_length_seconds_all_parts:.2f}s total video duration)."
        })
        
        logging.info(f"Successfully extracted {cumulative_frames_extracted_count} frames for transect {transect_id} (total duration: {total_video_length_seconds_all_parts:.2f}s) in {processing_time:.1f} seconds. Frames saved to {output_dir_final}")
        return transect_id, True
        
    except Exception as e:
        error_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_transect_id_for_error = transect_id if 'transect_id' in locals() else "UnknownTransect"
        error_msg = f"Error processing transect {current_transect_id_for_error}: {str(e)}"
        logging.error(error_msg, exc_info=True) 
        
        update_tracking(current_transect_id_for_error, {
            "Status": "Error in frame extraction",
            "Step 0 complete": "False",
            "Step 0 error time": error_time,
            "Notes": f"Error: {str(e)}"
        })
        return current_transect_id_for_error, False

def main():
    """Main function to process all videos in the source directory."""
    # Create only needed subdirectories - This is handled by config.py now
    # os.makedirs(DIRECTORIES["frames"], exist_ok=True) 
    # os.makedirs(DIRECTORIES["reports"], exist_ok=True) # Removed this line causing KeyError
    
    # Get list of video files
    video_files_paths = []
    for ext in ['.mov', '.mp4', '.mkv', '.MOV', '.MP4', '.MKV']:
        video_files_paths.extend(Path(VIDEO_SOURCE_DIRECTORY).glob(f"*{ext}"))
    
    # Filter out macOS metadata files (AppleDouble format) that start with "._"
    video_files_paths = [p for p in video_files_paths if not p.name.startswith('._')]
    
    if not video_files_paths:
        logging.error(f"No video files found in {VIDEO_SOURCE_DIRECTORY}")
        return
    
    logging.info(f"Found {len(video_files_paths)} video file(s) to potentially process.")

    # Group videos by transect ID
    grouped_videos = {}
    # Regex to capture base name and part number.
    # Examples: TCRMP..._FLC_T5_1 -> (TCRMP..._FLC_T5, 1)
    #           RBTEST..._BWR_TRY2_3 -> (RBTEST..._BWR_TRY2, 3)
    #           HYDRUSMAPPING..._DOCK_RUN1_2 -> (HYDRUSMAPPING..._DOCK_RUN1, 2)
    # Allows for optional _partX or _X pattern. Supports T, TRY, and RUN replicates.
    multipart_pattern = re.compile(r"^(.*_(?:T|TRY|RUN)\d+)(?:_part|_)?(\d+)$", re.IGNORECASE)
    # For single files that still conform to a transect naming but without part numbers
    single_transect_pattern = re.compile(r"^(.*_(?:T|TRY|RUN)\d+)$", re.IGNORECASE)

    for video_path_obj in video_files_paths:
        video_stem = video_path_obj.stem # Filename without extension

        # Strip _PROXY suffix (byproduct of encoding process)
        if video_stem.upper().endswith("_PROXY"):
            video_stem = video_stem[:-6]

        base_name_for_group = None
        part_number = 0 # Default for single videos or if base part is not numbered "_1"

        match_multipart = multipart_pattern.match(video_stem)
        if match_multipart:
            base_name_for_group = match_multipart.group(1)
            part_number = int(match_multipart.group(2))
        else:
            match_single_transect = single_transect_pattern.match(video_stem)
            if match_single_transect:
                base_name_for_group = match_single_transect.group(1)
                # part_number remains 0, indicating it's the base or only part
            else:
                # Fallback: use the whole stem if no T# pattern recognized as part of base
                # This treats any other video file as its own transect.
                base_name_for_group = video_stem
                # part_number remains 0

        if base_name_for_group not in grouped_videos:
            grouped_videos[base_name_for_group] = []
        
        # Store path as string, and part number for sorting
        grouped_videos[base_name_for_group].append({'path': str(video_path_obj), 'part': part_number})

    logging.info(f"Grouped into {len(grouped_videos)} transect(s) to process.")
    
    results = []
    transect_count = 0
    for transect_id, parts_data in grouped_videos.items():
        transect_count += 1
        # Sort parts by part number. Part 0 (single/base) comes before numbered parts.
        parts_data.sort(key=lambda x: x['part'])
        
        sorted_video_paths_for_transect = [p['path'] for p in parts_data]
        
        logging.info(f"Processing transect {transect_count}/{len(grouped_videos)}: {transect_id} with {len(sorted_video_paths_for_transect)} part(s): {', '.join(os.path.basename(p) for p in sorted_video_paths_for_transect)}")
        
        # Call the refactored processing function
        processed_transect_id, success = process_transect(transect_id, sorted_video_paths_for_transect)
        results.append((processed_transect_id, success))
    
    # Create summary of results - REMOVED
    # create_frame_summary()
    
    # Report final status
    successful_transects = sum(1 for _, success in results if success)
    logging.info(f"Frame extraction run complete. Successfully processed {successful_transects}/{len(grouped_videos)} transect(s).")
    
    if successful_transects != len(grouped_videos):
        failed_transects = [transect_id for transect_id, success in results if not success]
        if failed_transects:
            logging.warning(f"Failed to process the following transect(s): {', '.join(failed_transects)}")

if __name__ == "__main__":
    main()