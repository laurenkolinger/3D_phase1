#!/usr/bin/env python3
"""
File Naming Utilities for Step 3 Exports

Provides standardized file naming and directory structure for consistent 
exports across both step3.py and step3_manualScale.py
"""

import os

def get_export_paths(model_id, base_output_dir):
    """
    Get standardized export paths for a model.
    
    Args:
        model_id (str): The model ID (e.g., "TCRMP20241014_3D_BWR_T2")
        base_output_dir (str): Base output directory path
        
    Returns:
        dict: Dictionary containing all export paths
    """
    # Create subdirectories for orthomosaics and models, but not reports
    orthomosaic_dir = os.path.join(base_output_dir, "output", "orthomosaics", model_id)
    model_dir = os.path.join(base_output_dir, "output", "models", model_id)
    report_dir = os.path.join(base_output_dir, "output", "reports")  # Flat directory
    
    # Ensure directories exist
    os.makedirs(orthomosaic_dir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)
    
    return {
        'orthomosaic': {
            'dir': orthomosaic_dir,
            'file': os.path.join(orthomosaic_dir, f"{model_id}.tif")
        },
        'model': {
            'dir': model_dir,
            'file': os.path.join(model_dir, f"{model_id}.obj")
        },
        'report': {
            'dir': report_dir,
            'file': os.path.join(report_dir, f"{model_id}.pdf")  # Flat in reports/
        }
    }

def clean_model_id(chunk_label):
    """
    Clean and validate model ID from chunk label.
    
    Args:
        chunk_label (str): The chunk label from Metashape
        
    Returns:
        str: Cleaned model ID
    """
    # Remove any whitespace and ensure it's a valid model ID
    model_id = str(chunk_label).strip()
    
    # Could add validation here if needed
    # e.g., check format matches TCRMP20241014_3D_BWR_T2
    
    return model_id 