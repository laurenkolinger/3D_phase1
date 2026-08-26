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
import fcntl
import shutil
import socket
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

# Minimum free space on the temp volume (TMPDIR or /tmp) before we start.
# Metashape spills depth_maps_pyramids here; running out mid-build is the
# original FLC T6 failure mode (see _DOCS/archive/incidents/INCIDENT_2026-05_parallel_step1_flc_t6.md).
MIN_TEMP_FREE_GB = int(os.environ.get("STEP1_MIN_TEMP_FREE_GB", "50"))

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

# Cooperative-pause contract (shared across step1/step2/step3 + run_phaseN.py +
# the VICARIUS runner). When the UI / queue requests a pause it drops a sentinel
# file in the project root; we check it only at safe model/batch boundaries
# (after the tracking CSV is updated and the PSX is saved), finish nothing
# mid-flight, and exit with PAUSE_EXIT_CODE so the orchestrator knows this was a
# clean pause (not a failure). Re-running the module resumes: completed models
# are skipped (idempotent). The exclusive project lock is released automatically
# when the process exits, so a paused run frees the GPU + the lock.
PAUSE_EXIT_CODE = 42
PAUSE_SENTINEL = ".pause_requested"


def pause_requested():
    """True when a pause sentinel file exists in the project root."""
    return os.path.exists(os.path.join(DIRECTORIES["base"], PAUSE_SENTINEL))


def checkpoint_pause(where, save_fn=None):
    """At a safe boundary: if a pause was requested, persist + exit cleanly.

    save_fn (optional) is called to flush in-memory document state to disk
    BEFORE exiting, so the work matching the tracking CSV is durable. The
    project lock (if held) is released by process exit.
    """
    if not pause_requested():
        return
    logging.info(f"PAUSE requested - stopping cleanly at boundary: {where}")
    if save_fn is not None:
        try:
            save_fn()
        except Exception as exc:  # pragma: no cover - best-effort flush
            logging.warning(f"pause: save before exit failed: {exc}")
    logging.info(
        "Paused. Re-run this module on the same project to resume "
        "(completed models are skipped)."
    )
    sys.exit(PAUSE_EXIT_CODE)


def acquire_project_lock(step_name):
    """Take an exclusive flock on <project>/.processing.lock.

    Refuses to start if another step is already running for this project.
    Caller must keep the returned file object alive — closing it releases
    the lock. Stamps the file with PID, step name, hostname, and start time
    so the holder is visible if a future run is blocked.
    """
    lock_path = os.path.join(DIRECTORIES["base"], ".processing.lock")
    lock_fp = open(lock_path, "w")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fp.close()
        try:
            with open(lock_path, "r") as f:
                holder = f.read().strip() or "(unknown holder)"
        except Exception:
            holder = "(unknown holder)"
        raise RuntimeError(
            f"Another VICARIUS 3D step is already running for this project.\n"
            f"  Lock file: {lock_path}\n"
            f"  Held by:   {holder}\n"
            f"  Refusing to launch {step_name}. If you are certain no other run is "
            f"active, delete the lock file and retry."
        )
    stamp = (
        f"pid={os.getpid()} step={step_name} "
        f"host={socket.gethostname()} "
        f"started={datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    lock_fp.write(stamp)
    lock_fp.flush()
    logging.info(f"Acquired project lock at {lock_path} ({stamp.strip()})")
    return lock_fp


def check_temp_free_space(min_gb=None):
    """Refuse to start if TMPDIR (or /tmp) has less than min_gb free.

    Metashape's depth_maps_pyramids intermediates land in TMPDIR. Running
    out of space mid-build leaves a half-saved PSX (the original 2026-05
    incident). This is a coarse preflight only — it does not guarantee
    enough space for the full run, just that we are not starting empty.
    """
    if min_gb is None:
        min_gb = MIN_TEMP_FREE_GB
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    try:
        free_bytes = shutil.disk_usage(tmpdir).free
    except OSError as e:
        raise RuntimeError(f"Cannot stat TMPDIR={tmpdir}: {e}")
    free_gb = free_bytes / (1024 ** 3)
    logging.info(f"Temp volume free space: {free_gb:.1f} GB at {tmpdir}")
    if free_gb < min_gb:
        raise RuntimeError(
            f"Refusing to start: only {free_gb:.1f} GB free at {tmpdir}, "
            f"need at least {min_gb} GB for Metashape intermediates. "
            f"Free space or point TMPDIR at a larger volume "
            f"(export TMPDIR=/path/with/space before launching)."
        )


def verify_psx_chunk(psx_path, transect_id):
    """Open psx_path in a fresh Document and confirm the chunk is real.

    Returns True only when a chunk with label==transect_id exists with a
    built model AND at least one texture. Anything less means the save did
    not actually land. We do this in a throwaway Document so the active
    one is untouched.
    """
    if not os.path.exists(psx_path):
        logging.error(f"Verify: PSX file does not exist: {psx_path}")
        return False
    try:
        verify_doc = Metashape.Document()
        try:
            verify_doc.open(psx_path, read_only=True, ignore_lock=True)
        except Exception as e:
            logging.error(f"Verify: could not reopen {psx_path}: {e}")
            return False
        try:
            chunks_by_label = {c.label: c for c in verify_doc.chunks}
            chunk = chunks_by_label.get(transect_id)
            if chunk is None:
                logging.error(
                    f"Verify: no chunk labeled {transect_id} in {psx_path}. "
                    f"Found: {sorted(chunks_by_label.keys())}"
                )
                return False
            if not chunk.model:
                logging.error(f"Verify: chunk {transect_id} has no model in {psx_path}")
                return False
            if not chunk.model.textures:
                logging.error(f"Verify: chunk {transect_id} has model but no textures in {psx_path}")
                return False
            logging.info(
                f"Verify: {transect_id} confirmed in {psx_path} "
                f"({len(chunk.model.faces)} faces, {len(chunk.model.textures)} texture(s))"
            )
            return True
        finally:
            verify_doc = None
    except Exception as e:
        logging.error(f"Verify: unexpected error checking {psx_path} for {transect_id}: {e}")
        return False


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
        
        # Verify model exists. We raise here (rather than just logging)
        # because the old "log and continue" path let step 1 mark a chunk
        # as complete with no usable mesh. See the 2026-05 FLC T6 incident.
        if not chunk.model:
            raise RuntimeError(
                f"buildModel returned but chunk.model is None for {transect_id}. "
                f"Treating as failed and refusing to advance."
            )
        logging.info(f"Model built successfully with {len(chunk.model.faces)} faces.")
        
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
        
        # Verify texture exists. Same reasoning as the model check above:
        # a textureless chunk is not a usable Step 1 output. Raise rather
        # than silently marking Step 1 complete.
        if not chunk.model:
            raise RuntimeError(f"Texture build skipped: model missing for {transect_id}")
        if not chunk.model.textures:
            raise RuntimeError(
                f"buildTexture completed but chunk.model.textures is empty for {transect_id}"
            )
        logging.info(f"Texture built successfully with {len(chunk.model.textures)} texture(s).")
        
        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # Record the build-time facts now, but DO NOT mark Step 1 complete
        # here — that flag flips only after the PSX save is verified on disk
        # (see process_batch). The 2026-05 FLC T6 incident wrote complete=True
        # before doc.save() landed, leaving an unrecoverable orphan chunk.
        update_tracking(transect_id, {
            "Status": "Step 1 build complete (awaiting save verification)",
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
        # Pause boundary: stop before starting a new model. Prior models in
        # this batch were already saved+verified per-transect below, so we
        # flush the doc only if it actually holds processed chunks.
        checkpoint_pause(
            f"before model {transect_id} (batch {batch_num})",
            save_fn=(lambda: doc.save(psx_path)) if results else None,
        )

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

            # Save the document after this chunk, then re-open it in a
            # throwaway Document to confirm the chunk landed with model +
            # texture before flipping "Step 1 complete" to True. Without
            # this gate, a doc.save() that fails mid-write (e.g. parallel
            # process, full disk, crash) leaves a tracking row that says
            # "complete" but points at unusable bytes.
            logging.info(f"Saving document to {psx_path} after processing {transect_id}")
            Metashape.app.update()
            doc.save(psx_path)

            if verify_psx_chunk(psx_path, transect_id):
                update_tracking(transect_id, {
                    "Status": "Step 1 complete",
                    "Step 1 complete": "True",
                })
                logging.info(f"Step 1 verified for {transect_id} in {psx_path}")
            else:
                error_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_tracking(transect_id, {
                    "Status": "Step 1 save verification failed",
                    "Step 1 complete": "False",
                    "Step 1 error time": error_time,
                    "Notes": (
                        f"doc.save returned but reopen could not find chunk "
                        f"{transect_id} with model+texture in {psx_path}. "
                        f"Treat as needs-rerun."
                    ),
                })
                logging.error(
                    f"Step 1 save verification FAILED for {transect_id} — "
                    f"row left as Step 1 complete=False"
                )
        else:
            # process_transect already marked the row as failed; just save
            # the doc so any partial chunk artifacts are persisted for
            # forensic inspection.
            logging.info(f"Saving document to {psx_path} after FAILED processing of {transect_id}")
            Metashape.app.update()
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
    # Preflight: confirm temp volume has room for Metashape intermediates,
    # then take an exclusive project lock so a second step1/step2 cannot
    # race against this one (the 2026-05 FLC T6 incident root cause).
    # The lock_fp must stay open for the lifetime of main(); we bind it
    # to a local so it is released on return / exception.
    check_temp_free_space()
    lock_fp = acquire_project_lock("step1")  # noqa: F841 -- holds the flock

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

        # Pause boundary: the previous batch's document is fully saved + closed
        # here, so this is the cleanest place to stop. No open doc to flush.
        checkpoint_pause(f"before batch {batch_num} of {len(batches)}")

        logging.info(f"Starting batch {batch_num} of {len(batches)}")

        # Process the batch (completely isolated from other batches)
        batch_results = process_batch(batch, batch_num, timestamp)
        
        # Merge results
        batch_mapping.update(batch_results)
        
        # Force garbage collection
        import gc
        gc.collect()

    # Post-batch sweep: walk the tracking CSV one more time and re-verify
    # every row that claims Step 1 complete. This catches any drift between
    # the CSV and the PSX files on disk (the 2026-05 FLC T6 mode).
    logging.info("Running Step 1 completeness sweep against tracking CSV...")
    sweep_failures = []
    for transect_id in transect_dirs:
        status = get_transect_status(transect_id)
        if status.get("Step 1 complete", "False") != "True":
            continue
        psx_path = (status.get("PSX file") or "").strip()
        if not psx_path:
            sweep_failures.append((transect_id, "no PSX file recorded"))
            continue
        if not verify_psx_chunk(psx_path, transect_id):
            sweep_failures.append((transect_id, f"verify failed for {psx_path}"))
            update_tracking(transect_id, {
                "Status": "Step 1 sweep failed",
                "Step 1 complete": "False",
                "Notes": (
                    f"Post-batch sweep could not verify {transect_id} in "
                    f"{psx_path}; row reset to needs-rerun."
                ),
            })
    if sweep_failures:
        logging.error(
            f"Step 1 completeness sweep flagged {len(sweep_failures)} model(s); "
            f"their tracking rows were reset:"
        )
        for tid, reason in sweep_failures:
            logging.error(f"  {tid}: {reason}")
    else:
        logging.info("Step 1 completeness sweep: all complete rows verified on disk")

    logging.info("Step 1 isolated processing complete")

if __name__ == "__main__":
    main()