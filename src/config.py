"""
TCRMP 3D Processing Configuration

This file loads configuration parameters from a YAML file and provides
access to them throughout the processing workflow.
"""

import os
import yaml
import datetime
import sys
import csv
import glob
from pathlib import Path


def load_yaml(yaml_path):
    """Load and validate YAML file."""
    try:
        with open(yaml_path, 'r') as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Could not find analysis_params.yaml at: {yaml_path}")
    except Exception as e:
        # If yaml module is not available, provide a helpful error message
        if 'yaml' in str(e) and 'No module named' in str(e):
            print("Error: The PyYAML module is not installed.")
            print("Please install it using: pip install pyyaml")
            print("Or for Metashape's Python environment:")
            print("/Applications/MetashapePro.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/pip3 install pyyaml")
            print("Alternatively, modify src/config.py to work without yaml dependency.")
            raise ImportError("PyYAML module required")
        else:
            raise e
    
    # Validate required sections
    required_sections = ['project', 'processing'] # Removed 'directories'
    for section in required_sections:
        if section not in params:
            raise ValueError(f"Missing required section '{section}' in {yaml_path}")
    
    # Validate required processing parameters for all steps
    required_processing = [
        'frames_per_transect',                    # Step 0
        'chunk_size', 'use_gpu', 'metashape',     # Step 1
        'chunk_management',                       # Step 2
        'model_processing',                       # Step 3
        'final_exports'                           # Step 4
    ]
    
    for param in required_processing:
        if param not in params['processing']:
            print(f"Warning: Missing recommended parameter '{param}' under 'processing' in {yaml_path}")
    
    return params

def get_project_dir_path():
    """Get the path to the project directory containing analysis_params.yaml."""
    # First try to get from command line
    if len(sys.argv) > 1:
        project_dir = sys.argv[1]
        # Remove potential surrounding single or double quotes and trailing slashes
        project_dir = project_dir.strip('\'\"').rstrip('/')
        # Verify directory exists
        if not os.path.isdir(project_dir):
             raise FileNotFoundError(f"Project directory not found: {project_dir}")
        return project_dir

    # Prompt user for project directory
    print("Please enter the absolute path to your project directory.")
    print("Example: /Users/yourname/examples/sample_project or \'/Users/yourname/examples/sample_project\'")
    project_dir = input("Project directory: ").strip()
    
    # Remove potential surrounding single or double quotes
    project_dir = project_dir.strip('\'\"')
    
    # Remove any trailing slashes
    project_dir = project_dir.rstrip('/')
    
    # Verify directory exists
    if not os.path.isdir(project_dir):
        raise FileNotFoundError(f"Project directory not found: {project_dir}")
    
    return project_dir

# Get the directory name from a path (last component)
def get_dir_name(path):
    """Extract the directory name (last component) from a path."""
    return os.path.basename(path.rstrip('/'))

# --- Configuration Loading ---

# Get the project directory path
PROJECT_DIR = get_project_dir_path()

# Construct path to YAML file within the project directory
YAML_PATH = os.path.join(PROJECT_DIR, "analysis_params.yaml")

# Load YAML configuration
PARAMS = load_yaml(YAML_PATH)

# Derive project name and ID from the directory path
PROJECT_NAME = get_dir_name(PROJECT_DIR)
PROJECT_ID = PROJECT_NAME # Use the derived name as the ID

# --- Directory Definitions (Derived from PROJECT_DIR) ---
BASE_DIRECTORY = PROJECT_DIR
PROCESSING_DIRECTORY = os.path.join(PROJECT_DIR, "processing")
OUTPUT_DIRECTORY = os.path.join(PROJECT_DIR, "output")
VIDEO_SOURCE_DIRECTORY = os.path.join(PROJECT_DIR, "video_source")

# Define standard subdirectories relative to the project
DIRECTORIES = {
    "base": BASE_DIRECTORY,
    "processing_root": PROCESSING_DIRECTORY,
    "output_root": OUTPUT_DIRECTORY,
    "video_source": VIDEO_SOURCE_DIRECTORY, # Added video source here
    "frames": os.path.join(PROCESSING_DIRECTORY, "frames"),  # Step 0 output (processing)
    "frames_output": os.path.join(OUTPUT_DIRECTORY, "frames"),  # Step 3+ (moved completed)
    "logs": os.path.join(OUTPUT_DIRECTORY, "logs"),
    "psxraw": os.path.join(PROCESSING_DIRECTORY, "psxraw"),
    "orthomosaics": os.path.join(OUTPUT_DIRECTORY, "orthomosaics"),
    "models": os.path.join(OUTPUT_DIRECTORY, "models"),
    "reports": os.path.join(OUTPUT_DIRECTORY, "reports"), # Added reports directory
    "psx_output": os.path.join(OUTPUT_DIRECTORY, "psx"), # Renamed from psx_consolidated
    # "final_outputs": os.path.join(OUTPUT_DIRECTORY, "final")  # DEPRECATED - not used
    # Removed adobe_presets, metashape_presets, scripts, config
}

# --- Processing Parameters ---

# Number of frames to extract per transect
FRAMES_PER_TRANSECT = PARAMS['processing']['frames_per_transect']

# Project metadata from YAML
PROJECT_NOTES = PARAMS['project'].get('notes', '')

# Metashape processing parameters
METASHAPE_DEFAULTS = PARAMS['processing']['metashape']['defaults']
CHUNK_SIZE = PARAMS['processing']['chunk_size']
USE_GPU = PARAMS['processing']['use_gpu']
MAX_CHUNKS_PER_PSX = PARAMS['processing'].get('max_chunks_per_psx', 5)

# --- Static Paths (Relative to Script Location or Assumed Structure) ---
# Get the directory where this config.py script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR) # Assumes src is one level down from root

# Define preset paths relative to repository root
DIRECTORIES["adobe_presets"] = os.path.join(REPO_ROOT, "presets/lightroom")
DIRECTORIES["metashape_presets"] = os.path.join(REPO_ROOT, "presets/metashape") # Changed from premiere

# --- Runtime Variables ---
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# --- Logging Configuration ---
LOG_FILE = os.path.join(DIRECTORIES["logs"], f"processing_{PROJECT_NAME}.log")

# --- Helper Functions ---

def create_directories():
    """Create required subdirectories within the project folder."""
    # Only create directories defined within the project (processing/output subfolders)
    for dir_name, dir_path in DIRECTORIES.items():
        if dir_path.startswith(PROJECT_DIR): # Check if path is within the project base
             # Exclude frames_output from automatic creation (created dynamically in Step 3)
            if dir_name == "frames_output":
                continue
                
             # Ensure the specific paths for logs and reports are created
            # Simplify: Just attempt to create all other project-relative dirs
            # if dir_name in ["logs", "reports", "frames", "psxraw", "orthomosaics", "models", "psx_output"]:
            try:
                os.makedirs(dir_path, exist_ok=True)
            except OSError as e:
                 print(f"Warning: Could not create directory {dir_path}: {e}")

def ensure_parent_directory(filepath):
    """Ensure the parent directory of a file exists."""
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir): # Check if exists before creating
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as e:
            print(f"Warning: Could not create parent directory {parent_dir} for {filepath}: {e}")

def get_tracking_file(model_id=None): # model_id is not used here anymore
    """Get tracking file path for the project (now directly in project dir)."""
    # Use project ID in the filename to create a unique tracking file per project
    # Place it directly in the project directory (BASE_DIRECTORY)
    return os.path.join(DIRECTORIES["base"], f"status_{PROJECT_ID}.csv")

def get_tracking_files():
    """Get list of all tracking files for this project (always returns one path)."""
    tracking_file = get_tracking_file()
    return [tracking_file] if os.path.exists(tracking_file) else []

def initialize_tracking(model_id):
    """Initialize tracking CSV file if not exists, and add a row for the model."""
    tracking_file = get_tracking_file()
    
    # Ensure parent directory exists (should be project dir, usually exists)
    ensure_parent_directory(tracking_file)
    
    # **FIXED: Simplified headers to prevent CSV corruption**
    headers = ["Model ID", "Status", "Step 0 complete", "Video Length (s)", "Total Video Frames", "Frames Extracted", "Video Source", "Extraction Timestamp", "Step 0 start time", "Step 0 end time", "Step 0 processing time (s)", "Frames directory", "Step 1 complete", "Step 1 start time", "Step 1 end time", "Step 1 processing time (s)", "Aligned cameras", "Total cameras", "PSX file", "Report file", "Step 1 error time", "Step 2 complete", "Step 2 site", "Step 2 consolidation time", "Step 3 complete", "Step 3 scale method", "Step 3 scale applied", "Step 3 ortho exported", "Step 3 model exported", "Step 3 processing time", "Step 4 complete", "Step 4 web published", "Sketchfab URL", "Step 4 high-res exported", "Step 4 processing time", "Notes", "Scale", "Scale Error (m)", "Cameras Removed"]

    file_exists = os.path.exists(tracking_file)
    rows = []
    current_header = []
    
    if file_exists:
        try:
            with open(tracking_file, 'r', newline='') as csvfile:
                reader = csv.reader(csvfile)
                rows = list(reader)
                if rows:
                    current_header = rows[0]
                    # **FIXED: Detect corrupted CSV (header split across lines)**
                    if len(rows) > 1 and len(rows[1]) == len(current_header) and rows[1][0] == "Model ID":
                        print(f"🔧 DETECTED CORRUPTED CSV: Header split across lines in {tracking_file}. Fixing...")
                        file_exists = False  # Force recreation
        except Exception as e:
            print(f"Warning: Could not read existing tracking file {tracking_file}: {e}. Will recreate.")
            file_exists = False # Treat as non-existent if unreadable

    # Check if file needs header or if header is incomplete/incorrect
    needs_header = not file_exists or not rows or current_header != headers
    
    if needs_header:
        try:
            with open(tracking_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(headers)
            rows = [headers] # Reset rows to just the header
            current_header = headers
            print(f"Initialized/updated tracking file: {tracking_file}")
        except Exception as e:
            print(f"Error: Could not write headers to tracking file {tracking_file}: {e}")
            return tracking_file # Return path even if write failed

    # Check if model_id already exists in the file
    model_exists = False
    if len(rows) > 1: # Check only if there are data rows
        try:
            id_index = current_header.index("Model ID")
            for row in rows[1:]:
                if row and len(row) > id_index and row[id_index] == model_id:
                    model_exists = True
                    break
        except ValueError:
            print(f"Warning: 'Model ID' column not found in {tracking_file}.")
            # If header is broken, we might have already rewritten it, but double check
            if current_header != headers:
                 print("Attempting to fix header again.")
                 # This case should be rare if header check above worked
                 try:
                     with open(tracking_file, 'w', newline='') as csvfile:
                         writer = csv.writer(csvfile)
                         writer.writerow(headers)
                     rows = [headers]
                     current_header = headers
                 except Exception as e:
                     print(f"Error: Could not fix header for {tracking_file}: {e}")

    # If model doesn't exist, add it
    if not model_exists:
        new_row = [""] * len(headers)
        try:
            id_index = headers.index("Model ID")
            new_row[id_index] = model_id
            status_index = headers.index("Status")
            new_row[status_index] = "Initialized"
            notes_index = headers.index("Notes")
            new_row[notes_index] = f"Tracking initialized {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        except (ValueError, IndexError) as e:
            print(f"Error preparing new row data: {e}")
            # Fallback if columns not found or index issue
            new_row = [model_id, "Initialized"] + [""] * (len(headers) - 3) + [f"Tracking initialized {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"]
            if len(new_row) < len(headers): # Pad if necessary
                 new_row.extend([""] * (len(headers) - len(new_row)))
            elif len(new_row) > len(headers): # Truncate if necessary
                 new_row = new_row[:len(headers)]

        # Append the new row to the existing data (or just after header if no data)
        rows.append(new_row)
        
        # Write the updated data
        try:
            with open(tracking_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerows(rows)
            print(f"Added model '{model_id}' to tracking file.")
        except Exception as e:
            print(f"Error: Could not write new model row to {tracking_file}: {e}")
            
    return tracking_file


def update_tracking(model_id, data):
    """Update tracking file with new data for the specified model."""
    tracking_file = get_tracking_file()
    
    # Initialize tracking if file doesn't exist or is empty
    if not os.path.exists(tracking_file):
        initialize_tracking(model_id) # This ensures header exists
    
    rows = []
    header = []
    try:
        with open(tracking_file, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
            if rows:
                header = rows[0]
            else: # If file exists but is empty
                 initialize_tracking(model_id) # Re-initialize to get header
                 # Reread after initialization
                 with open(tracking_file, 'r', newline='') as csvfile:
                      reader = csv.reader(csvfile)
                      rows = list(reader)
                      if rows:
                           header = rows[0]

    except Exception as e:
        print(f"Error reading tracking file {tracking_file} before update: {e}. Attempting to initialize.")
        initialize_tracking(model_id) # Attempt to create/fix it
        # Reread after initialization attempt
        try:
            with open(tracking_file, 'r', newline='') as csvfile:
                 reader = csv.reader(csvfile)
                 rows = list(reader)
                 if rows:
                     header = rows[0]
        except Exception as e2:
             print(f"Error: Could not read tracking file even after initialization: {e2}. Update aborted.")
             return tracking_file # Return path, but update failed

    if not header:
        print(f"Error: Tracking file {tracking_file} has no header. Update aborted.")
        return tracking_file

    model_row_index = -1
    id_index = -1
    
    # Find the Model ID column index
    try:
        id_index = header.index("Model ID")
    except ValueError:
        print(f"Error: 'Model ID' column missing in header of {tracking_file}. Update aborted.")
        return tracking_file
    
    # Find the row for this model_id
    for i, row in enumerate(rows):
        if i == 0: continue # Skip header row
        if row and len(row) > id_index and row[id_index] == model_id:
            model_row_index = i
            break
            
    # If model doesn't exist in the file, add a new row using initialize logic
    if model_row_index == -1:
        print(f"Model '{model_id}' not found in tracking file. Adding it now.")
        initialize_tracking(model_id) # Add the row
        # Reread file to get the newly added row
        try:
             with open(tracking_file, 'r', newline='') as csvfile:
                  reader = csv.reader(csvfile)
                  rows = list(reader)
                  if rows:
                      header = rows[0] # Reconfirm header
                  # Find the new row index
                  for i, row in enumerate(rows):
                      if i == 0: continue
                      if row and len(row) > id_index and row[id_index] == model_id:
                           model_row_index = i
                           break
        except Exception as e:
             print(f"Error rereading tracking file after adding model: {e}. Update may be incomplete.")
             
        if model_row_index == -1: # If still not found after adding
             print(f"Error: Failed to add or find model '{model_id}' row after initialization. Update aborted.")
             return tracking_file

    # Update values in the model's row
    updated = False
    current_row = rows[model_row_index]
    for key, value in data.items():
        try:
            col_index = header.index(key)
            # Ensure row has enough columns, pad with empty strings if necessary
            while len(current_row) <= col_index:
                current_row.append("")
            # Update only if value is different
            if current_row[col_index] != str(value):
                 current_row[col_index] = str(value) # Ensure value is string
                 updated = True
        except ValueError:
            # FIXED: No more auto-column adding! This was causing CSV corruption.
            # If column doesn't exist, FAIL FAST instead of silently corrupting the CSV.
            print(f"ERROR: Column '{key}' does not exist in tracking file {tracking_file}")
            print(f"Available columns: {header}")
            print(f"ABORTING to prevent CSV corruption. Fix the script to use only existing columns.")
            return tracking_file
            
    # Write updated data only if changes were made
    if updated:
        try:
            # Ensure parent directory exists (redundant check, but safe)
            ensure_parent_directory(tracking_file)
            
            with open(tracking_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerows(rows)
            # print(f"Updated tracking data for model '{model_id}'.") # Reduce verbosity
        except Exception as e:
            print(f"Error writing updates to tracking file {tracking_file}: {e}")
    
    return tracking_file


def get_transect_status(model_id):
    """Get the current status for a model from the tracking file."""
    tracking_file = get_tracking_file()
    
    if not os.path.exists(tracking_file):
        return {} # Return empty dict if file doesn't exist
    
    try:
        with open(tracking_file, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
    except Exception as e:
        print(f"Error reading tracking file {tracking_file} for status check: {e}")
        return {}

    if len(rows) < 2: # Need header + at least one data row
        return {} 
    
    header = rows[0]
    
    # **FIXED: Detect and handle corrupted CSV**
    if len(rows) > 1 and len(rows[1]) == len(header) and rows[1][0] == "Model ID":
        print(f"🔧 CORRUPTED CSV detected in get_transect_status. Recreating...")
        initialize_tracking(model_id)
        return {"Status": "Initialized"}  # Return basic status
    
    id_index = -1
    
    # Find the Model ID column index
    try:
        id_index = header.index("Model ID")
    except ValueError:
        print(f"Warning: 'Model ID' column missing in header of {tracking_file} during status check.")
        return {}
        
    # Find the row for this model_id
    for row in rows[1:]:
        if row and len(row) > id_index and row[id_index] == model_id:
            # Create dictionary of column names to values
            status = {}
            for i, col_name in enumerate(header):
                 # Ensure row has a value for this column, default to empty string
                 status[col_name] = row[i] if i < len(row) else ""
            return status
            
    # If we get here, the model wasn't found
    return {}


# Create all directories on import
create_directories()
print(f"Configuration loaded for project: {PROJECT_NAME} in {PROJECT_DIR}")
print(f"Tracking file location: {get_tracking_file()}")
# Optionally print all defined directories
# print("Defined directories:")
# for key, path in DIRECTORIES.items():
#     print(f"  {key}: {path}") 