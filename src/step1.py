"""
Step 1: Isolated 3D Processing

Each batch is completely processed and saved before moving to the next batch.
Documents are properly closed between batches to prevent interference.
"""

import os
import logging
import Metashape
import datetime
import math
import pandas as pd
import time
import sys
import traceback
from config import (
    DIRECTORIES,
    PROJECT_NAME,
    METASHAPE_DEFAULTS,
    USE_GPU,
    PARAMS,
    update_tracking,
    get_transect_status,
    TIMESTAMP
)

# Print all directory paths for debugging
print("DEBUG: Directory paths:")
for key, path in DIRECTORIES.items():
    print(f"  {key}: {path}")
    # Check if directory exists
    if os.path.exists(path):
        print(f"    [EXISTS]")
    else:
        print(f"    [DOES NOT EXIST]")
        try:
            os.makedirs(path, exist_ok=True)
            print(f"    [CREATED]")
        except Exception as e:
            print(f"    [FAILED TO CREATE: {str(e)}]")

# Try to create each directory explicitly
print("DEBUG: Attempting to create all directories:")
for key, path in DIRECTORIES.items():
    try:
        print(f"Creating directory: {path}")
        os.makedirs(path, exist_ok=True)
        print(f"  Success!")
    except Exception as e:
        print(f"  Error creating {path}: {str(e)}")
        traceback.print_exc()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(DIRECTORIES["logs"], f"step1_isolated_{PROJECT_NAME}_{TIMESTAMP}.log")),
        logging.StreamHandler()
    ]
)

# Maximum number of chunks per PSX file
MAX_CHUNKS_PER_PSX = PARAMS['processing'].get('max_chunks_per_psx', 5)

def enumerate_gpus():
    """
    Enumerate available GPUs and log their details.
    
    Returns:
        list: List of available GPU devices
    """
    logging.info("Enumerating available GPU devices...")
    gpu_devices = Metashape.app.enumGPUDevices()
    
    if not gpu_devices:
        logging.warning("No GPU devices detected by Metashape")
        return []
        
    for i, device in enumerate(gpu_devices):
        if isinstance(device, dict):
            device_info = []
            for key, value in device.items():
                device_info.append(f"{key}: {value}")
            logging.info(f"GPU {i}: {', '.join(device_info)}")
        else:
            logging.info(f"GPU {i}: {device}")
    
    return gpu_devices

def setup_gpu(gpu_devices=None):
    """
    Configure GPU processing based on available devices.
    
    Args:
        gpu_devices (list, optional): List of available GPU devices
        
    Returns:
        bool: Whether GPU processing was successfully enabled
    """
    if not USE_GPU:
        logging.info("GPU processing disabled in config")
        return False
        
    # Enumerate GPUs if not provided
    if gpu_devices is None:
        gpu_devices = enumerate_gpus()
    
    if not gpu_devices:
        logging.warning("GPU processing requested but no devices available")
        return False
    
    # Set GPU mask to enable all available GPUs
    # Each bit in the mask corresponds to a GPU
    gpu_mask = 0
    for i in range(len(gpu_devices)):
        gpu_mask |= (1 << i)  # Set the corresponding bit
    
    Metashape.app.gpu_mask = gpu_mask
    
    # Enable GPU for depth maps and mesh generation
    Metashape.app.cpu_enable = False
    
    logging.info(f"GPU acceleration enabled with mask: {gpu_mask} (binary: {bin(gpu_mask)})")
    logging.info(f"Using {len(gpu_devices)} GPU device(s)")
    
    return True

def process_transect(transect_id, chunk, doc, psx_path):
    """
    Process a single transect through initial 3D reconstruction.
    
    Args:
        transect_id (str): The transect identifier
        chunk (Metashape.Chunk): The chunk to process
        doc (Metashape.Document): The document containing the chunk
        psx_path (str): The path to save the PSX file
        
    Returns:
        bool: Success or failure
    """
    try:
        start_time = datetime.datetime.now()
        
        # Set up GPU processing
        gpu_devices = enumerate_gpus()
        gpu_enabled = setup_gpu(gpu_devices)
        
        # Set chunk label
        chunk.label = transect_id
        
        # Add photos from frames directory
        frames_dir = os.path.join(DIRECTORIES["frames"], transect_id)
        if not os.path.exists(frames_dir):
            raise ValueError(f"Frames directory not found: {frames_dir}")
        
        # Get list of frame files
        frame_files = [f for f in os.listdir(frames_dir) if f.lower().endswith(('.jpg', '.jpeg', '.tif', '.tiff'))]
        if not frame_files:
            raise ValueError(f"No image files found in {frames_dir}")
        
        # Add photos to chunk
        logging.info(f"Adding {len(frame_files)} photos for model {transect_id}")
        chunk.addPhotos([os.path.join(frames_dir, f) for f in frame_files])
        
        # Match photos and align cameras
        logging.info(f"Matching photos for model {transect_id}")
        chunk.matchPhotos(
            downscale=METASHAPE_DEFAULTS["downscale"],
            keypoint_limit=METASHAPE_DEFAULTS["keypoint_limit"],
            tiepoint_limit=METASHAPE_DEFAULTS["tiepoint_limit"],
            generic_preselection=METASHAPE_DEFAULTS["generic_preselection"],
            reference_preselection=METASHAPE_DEFAULTS["reference_preselection"],
            filter_stationary_points=METASHAPE_DEFAULTS["filter_stationary_points"]
        )
        chunk.alignCameras(adaptive_fitting=METASHAPE_DEFAULTS["adaptive_fitting"])
        
        # Attempt to align any unaligned cameras
        unaligned_cameras = [camera for camera in chunk.cameras if not camera.transform]
        for camera in unaligned_cameras:
            camera.transform = None
        chunk.alignCameras(cameras=unaligned_cameras, reset_alignment=False)
        
        # Reset the region
        chunk.resetRegion()
        
        # Filter points and optimize cameras
        logging.info("Filtering points and optimizing cameras")
        f1 = Metashape.TiePoints.Filter()
        f1.init(chunk, Metashape.TiePoints.Filter.ReconstructionUncertainty)
        f1.removePoints(METASHAPE_DEFAULTS["reconstruction_uncertainty"])
        
        chunk.optimizeCameras(
            fit_k4=METASHAPE_DEFAULTS["fit_k4"],
            adaptive_fitting=METASHAPE_DEFAULTS["adaptive_fitting"]
        )
        
        f2 = Metashape.TiePoints.Filter()
        f2.init(chunk, Metashape.TiePoints.Filter.ReprojectionError)
        f2.removePoints(METASHAPE_DEFAULTS["reprojection_error"])
        
        f3 = Metashape.TiePoints.Filter()
        f3.init(chunk, Metashape.TiePoints.Filter.ProjectionAccuracy)
        f3.removePoints(METASHAPE_DEFAULTS["projection_accuracy"])
        
        # Rotate coordinate system to bounding box
        logging.info("Rotating coordinate system to bounding box")
        R = chunk.region.rot     # Bounding box rotation matrix
        C = chunk.region.center  # Bounding box center vector
        
        if chunk.transform.matrix:
            T = chunk.transform.matrix
            s = math.sqrt(T[0, 0] ** 2 + T[0, 1] ** 2 + T[0, 2] ** 2)  # scaling
            S = Metashape.Matrix().Diag([s, s, s, 1])                  # scale matrix
        else:
            S = Metashape.Matrix().Diag([1, 1, 1, 1])
            
        T = Metashape.Matrix([[R[0, 0], R[0, 1], R[0, 2], C[0]],
                             [R[1, 0], R[1, 1], R[1, 2], C[1]],
                             [R[2, 0], R[2, 1], R[2, 2], C[2]],
                             [     0,      0,      0,    1]])
                             
        chunk.transform.matrix = S * T.inv()  # resulting chunk transformation matrix
        
        # Build depth maps
        logging.info(f"Building depth maps for model {transect_id}")
        chunk.buildDepthMaps(
            downscale=METASHAPE_DEFAULTS["depth_downscale"],
            filter_mode=getattr(Metashape, METASHAPE_DEFAULTS["depth_filter_mode"]),
            reuse_depth=False,
            max_neighbors=METASHAPE_DEFAULTS.get("max_neighbors", 16),
            subdivide_task=True  # Split into subtasks for better GPU utilization
        )
        
        # Build model
        logging.info(f"Building model for {transect_id}")
        chunk.buildModel(
            source_data=Metashape.DepthMapsData,
            surface_type=getattr(Metashape, METASHAPE_DEFAULTS["surface_type"]),
            face_count=getattr(Metashape, METASHAPE_DEFAULTS["face_count"]),
            interpolation=getattr(Metashape, METASHAPE_DEFAULTS["interpolation"]),
            vertex_colors=METASHAPE_DEFAULTS["vertex_colors"],
            subdivide_task=True  # Split into subtasks for better GPU utilization
        )
        
        # Verify model exists
        if chunk.model:
            logging.info(f"Model built successfully with {len(chunk.model.faces)} faces.")
        else:
            logging.error(f"Model build step completed but chunk.model is None for {transect_id}!")
            # Optionally raise an error here or just log and continue depending on desired behavior
            # raise RuntimeError(f"Model build failed for {transect_id}")
        
        # Smooth model
        logging.info(f"Smoothing model for {transect_id}")
        chunk.smoothModel(
            strength=METASHAPE_DEFAULTS["smooth_strength"],
            apply_to_selection=False,
            fix_borders=METASHAPE_DEFAULTS.get("fix_borders", False),
            preserve_edges=METASHAPE_DEFAULTS.get("preserve_edges", False)
        )
        
        # Build UV
        logging.info(f"Building UV for model {transect_id}")
        chunk.buildUV(
            mapping_mode=getattr(Metashape, METASHAPE_DEFAULTS["mapping_mode"]),
            texture_size=METASHAPE_DEFAULTS["texture_size"],
            page_count=METASHAPE_DEFAULTS.get("page_count", 1)
        )
        
        # Build texture
        logging.info(f"Building texture for model {transect_id}")
        
        # Check if we should use GPU for texture generation
        enable_texture_gpu = METASHAPE_DEFAULTS.get("enable_texture_gpu", False)
        
        if not enable_texture_gpu:
            # Save current GPU state
            saved_gpu_mask = Metashape.app.gpu_mask
            saved_cpu_enable = Metashape.app.cpu_enable
            
            # Temporarily disable GPU for texture building
            Metashape.app.gpu_mask = 0
            Metashape.app.cpu_enable = True
            logging.info("GPU disabled for texture building (using CPU only)")
        
        # Build texture without gpu_mask parameter
        chunk.buildTexture(
            texture_size=METASHAPE_DEFAULTS["texture_size"],
            texture_type=getattr(Metashape.Model, METASHAPE_DEFAULTS["texture_type"]),
            blending_mode=getattr(Metashape, METASHAPE_DEFAULTS["blending_mode"]),
            ghosting_filter=METASHAPE_DEFAULTS.get("ghosting_filter", True),
            fill_holes=METASHAPE_DEFAULTS.get("fill_holes", True)
        )
        
        if not enable_texture_gpu:
            # Restore GPU state for subsequent operations
            Metashape.app.gpu_mask = saved_gpu_mask
            Metashape.app.cpu_enable = saved_cpu_enable
            logging.info("GPU re-enabled after texture building")
        
        # Verify texture exists
        if chunk.model and chunk.model.textures:
             logging.info(f"Texture built successfully with {len(chunk.model.textures)} texture(s).")
        elif chunk.model:
             logging.warning(f"Texture build step completed but chunk.model.textures is empty for {transect_id}!")
        else: 
             # Model didn't exist, so texture couldn't be built
             logging.warning(f"Texture build skipped because model does not exist for {transect_id}.")
        
        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # Update tracking file
        update_tracking(transect_id, {
            "Status": "Step 1 complete",
            "Step 1 complete": "True",
            "Step 1 start time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Step 1 end time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Step 1 processing time (s)": str(processing_time),
            "Aligned cameras": str(len([c for c in chunk.cameras if c.transform])),
            "Total cameras": str(len(chunk.cameras))
        })
        
        logging.info(f"Successfully processed model {transect_id} in {processing_time:.1f} seconds")
        Metashape.app.update() # Added update after model build
        return True
        
    except Exception as e:
        error_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_msg = f"Error processing model {transect_id}: {str(e)}"
        logging.error(error_msg)
        update_tracking(transect_id, {
            "Status": "Error in Step 1",
            "Step 1 complete": "False",
            "Step 1 error time": error_time,
            "Notes": error_msg
        })
        return False

def process_batch(transects, batch_num, timestamp):
    """
    Process a single batch of transects and completely close it before returning.
    
    Args:
        transects (list): List of transect IDs to process
        batch_num (int): Batch number
        timestamp (str): Timestamp string
        
    Returns:
        dict: Mapping of processed transects to their PSX file
    """
    if not transects:
        return {}
    
    # Create psxraw directory if it doesn't exist
    os.makedirs(DIRECTORIES["psxraw"], exist_ok=True)
    
    # Use transect name as filename if only 1 transect per PSX
    if len(transects) == 1 and MAX_CHUNKS_PER_PSX == 1:
        psx_filename = f"{transects[0]}_{timestamp}.psx"
    else:
        psx_filename = f"psx_{batch_num}_{timestamp}.psx"
    
    # Create PSX file path
    psx_path = os.path.join(DIRECTORIES["psxraw"], psx_filename)
    
    # Create a new document for this batch
    doc = Metashape.Document()
    
    # Results tracking
    results = {}
    
    # Process each transect in the batch
    for i, transect_id in enumerate(transects):
        # Skip if already processed
        status = get_transect_status(transect_id)
        if status.get("Step 1 complete", "False") == "True":
            logging.info(f"Model {transect_id} already processed, skipping...")
            continue
        
        logging.info(f"Processing model {transect_id} ({i+1}/{len(transects)})")
        
        # Create a new chunk for this transect
        chunk = doc.addChunk()
        
        # Process the transect
        success = process_transect(transect_id, chunk, doc, psx_path)
        
        if success:
            results[transect_id] = psx_path
            # Update tracking with the PSX path
            update_tracking(transect_id, {"PSX file": psx_path})
            
            # Create report for this transect
            try:
                # Use processing/reports_initial for step 1 reports
                reports_initial_dir = os.path.join(DIRECTORIES["processing_root"], "reportsraw")
                os.makedirs(reports_initial_dir, exist_ok=True)
                
                # Generate report
                report_file_path = os.path.join(reports_initial_dir, f"{transect_id}_step1.pdf")
                chunk.exportReport(report_file_path, title=f"Model {transect_id} - Step 1 Report")
                
                # Update tracking with report path
                update_tracking(transect_id, {"Report file": report_file_path})
                
                logging.info(f"Report generated: {report_file_path}")
            except Exception as e:
                logging.error(f"Error generating report for {transect_id}: {str(e)}")
            
        # Save the document after each transect
        logging.info(f"Saving document to {psx_path} after processing {transect_id}")
        Metashape.app.update() # Keep update BEFORE saving in process_batch
        doc.save(psx_path)
    
    # Final save of the document
    logging.info(f"Final save of batch {batch_num} to {psx_path}")
    Metashape.app.update() # Keep update BEFORE final save in process_batch
    doc.save(psx_path)
    
    # Important: Clear the document reference to fully release it
    doc = None
    
    return {psx_path: list(results.keys())}

def main():
    """Process transects in completely isolated batches."""
    # Get list of transect directories with frames
    transect_dirs = []
    frames_dir = DIRECTORIES["frames"]
    if os.path.exists(frames_dir):
        transect_dirs = [d for d in os.listdir(frames_dir) 
                        if os.path.isdir(os.path.join(frames_dir, d))]
    
    if not transect_dirs:
        logging.error(f"No model directories found in {frames_dir}")
        return
    
    # Filter for unprocessed transects
    unprocessed_transects = []
    for transect_id in transect_dirs:
        status = get_transect_status(transect_id)
        if status.get("Step 1 complete", "False") != "True":
            unprocessed_transects.append(transect_id)
    
    if not unprocessed_transects:
        logging.info("All models have already been processed")
        return
    
    logging.info(f"Found {len(unprocessed_transects)} models to process")
    
    # Process in completely isolated batches
    timestamp = datetime.datetime.now().strftime("%Y%m%d")
    
    # Split transects into batches
    batches = []
    current_batch = []
    
    for transect_id in unprocessed_transects:
        if len(current_batch) >= MAX_CHUNKS_PER_PSX:
            batches.append(current_batch)
            current_batch = []
        current_batch.append(transect_id)
    
    if current_batch:
        batches.append(current_batch)
    
    # Process each batch in complete isolation
    batch_mapping = {}
    
    for i, batch in enumerate(batches):
        batch_num = i + 1  # Start with batch 1
        logging.info(f"Starting batch {batch_num} of {len(batches)}")
        
        # Process the batch (completely isolated from other batches)
        batch_results = process_batch(batch, batch_num, timestamp)
        
        # Merge results
        batch_mapping.update(batch_results)
        
        # Force garbage collection
        import gc
        gc.collect()
    
    logging.info("Step 1 isolated processing complete")

if __name__ == "__main__":
    main() 