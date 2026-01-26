"""File utilities for PyNCBI.

This module provides utilities for working with IDAT files and sample sheets.
"""

from __future__ import annotations

import os
from pathlib import Path

import methylprep
import pandas as pd
from tqdm.auto import tqdm

from PyNCBI.Constants import SAMPLE_SHEET_COLUMNS
from PyNCBI.logging import get_logger

logger = get_logger(__name__)


def check_for_sample_sheet(path: str | Path) -> bool:
    """Check if a valid sample sheet exists in the path.

    Checks that the given path contains a csv file named "sample_sheet" and that
    it has all needed columns (Sample_Name, Sentrix_ID, Sentrix_Position, etc).

    Note:
        Depending on the situation not all columns need values.
        The two required columns are Sentrix_ID and Sentrix_Position.

    Args:
        path: Directory path to check

    Returns:
        True if a valid sample sheet exists
    """
    path = Path(path)
    sample_sheet_path = path / "sample_sheet.csv"

    if not sample_sheet_path.exists():
        return False

    sample_sheet = pd.read_csv(sample_sheet_path)
    has_all_columns = (
        set(sample_sheet.columns) & set(SAMPLE_SHEET_COLUMNS)
    ) == set(SAMPLE_SHEET_COLUMNS)
    has_required = (
        "Sentrix_ID" in sample_sheet.columns
        and "Sentrix_Position" in sample_sheet.columns
    )

    return bool(has_all_columns or has_required)


def generate_sample_sheet(path: str | Path) -> None:
    """Generate a sample sheet from IDAT files in a directory.

    Iterates over the files in the folder and generates a sample sheet.
    The folder should only contain red/green IDAT files.

    Note:
        If file names have format {GSM_NUMBER}_{SENTRIX_ID}_{SENTRIX_POS}_{Grn/Red}.idat,
        files will be renamed to {SENTRIX_ID}_{SENTRIX_POS}_{Grn/Red}.idat
        and the GSM ID will be saved in the sample sheet.

    Args:
        path: Directory containing IDAT files
    """
    path = Path(path)
    rows = []
    files_in_path = list(path.iterdir())

    # Remove _Grn.idat/_Red.idat suffix and deduplicate
    base_names = {f.name[:-9] for f in files_in_path if f.suffix == ".idat"}

    for file in base_names:
        components = file.split("_")

        if len(components) == 2:
            sentrix_id, sentrix_position = components
            new_row = dict.fromkeys(SAMPLE_SHEET_COLUMNS, "")
            new_row["Sentrix_ID"] = sentrix_id
            new_row["Sentrix_Position"] = sentrix_position
            rows.append(new_row)

        elif len(components) == 3:
            gsm, sentrix_id, sentrix_position = components
            new_row = dict.fromkeys(SAMPLE_SHEET_COLUMNS, "")
            new_row["Sample_Name"] = gsm
            new_row["Sentrix_ID"] = sentrix_id
            new_row["Sentrix_Position"] = sentrix_position
            rows.append(new_row)

            # Rename files to remove GSM prefix
            new_base = f"{sentrix_id}_{sentrix_position}"
            old_grn = path / f"{file}_Grn.idat"
            old_red = path / f"{file}_Red.idat"

            if old_grn.exists():
                old_grn.rename(path / f"{new_base}_Grn.idat")
            if old_red.exists():
                old_red.rename(path / f"{new_base}_Red.idat")

            logger.debug("Renamed %s files to %s", gsm, new_base)

        else:
            raise ValueError(f"Unexpected file name format: {file}")

    sample_sheet = pd.DataFrame(rows, columns=SAMPLE_SHEET_COLUMNS)
    sample_sheet.to_csv(path / "sample_sheet.csv", index=False)
    logger.info("Generated sample sheet with %d samples", len(rows))


def parse_idat_files(path: str | Path, array_type: str) -> None:
    """Parse IDAT files using methylprep.

    Uses methylprep to parse IDAT files and save beta values.
    If a sample sheet is not present, one will be generated.

    A file called "parsed_beta_values.parquet" will be created containing
    N columns (one per sample) and K rows (one per probe that passed QC).

    Args:
        path: Directory containing IDAT files and sample_sheet
        array_type: Array type - 'custom', '450k', 'epic', or 'epic+'
    """
    path = Path(path)

    # Check for sample sheet
    if not check_for_sample_sheet(path):
        logger.info("No sample sheet found, generating one")
        generate_sample_sheet(path)

    # Read sample sheet
    sample_sheet = pd.read_csv(path / "sample_sheet.csv")

    # Map sentrix IDs to sample names
    sample_name_map = {}
    if "Sample_Name" in sample_sheet.columns:
        for _, row in sample_sheet.iterrows():
            sentrix_key = f"{row['Sentrix_ID']}_{row['Sentrix_Position']}"
            sample_name = str(row["Sample_Name"]).strip()
            if pd.notna(row["Sample_Name"]) and sample_name:
                sample_name_map[sentrix_key] = sample_name
            else:
                sample_name_map[sentrix_key] = sentrix_key

    logger.info("Processing %d IDAT files with methylprep", len(sample_sheet))

    # Run methylprep pipeline
    dataframe = methylprep.run_pipeline(
        str(path),
        array_type=array_type,
        export=False,
        betas=True,
        save_control=False,
        meta_data_frame=False,
    )

    # Rename columns to sample names
    dataframe.columns = [sample_name_map.get(i, i) for i in dataframe.columns]

    # Save results
    output_path = path / "parsed_beta_values.parquet"
    dataframe.to_parquet(output_path)
    logger.info("Saved beta values to %s", output_path)


def merge_gsm_data_files(path: str | Path) -> None:
    """Merge individual GSM CSV files into a single parquet file.

    Aggregates all CSV files containing methylation data into one parquet file
    with N columns (one per GSM).

    Each input CSV file must have:
    - A 'probe' column with CpG identifiers
    - A 'GSMXXXXXX' column with methylation values

    Args:
        path: Directory containing GSM CSV files

    Raises:
        ValueError: If CSV file format is invalid
    """
    path = Path(path)

    # Get all CSV files
    csv_files = [f for f in path.iterdir() if f.suffix == ".csv"]
    logger.info("Merging %d GSM data files", len(csv_files))

    individual_gsm_data = []
    for file in tqdm(csv_files, desc="Reading GSM files"):
        gsm_data = pd.read_csv(file)

        # Find GSM column
        data_column = [c for c in gsm_data.columns if "GSM" in c]

        if len(data_column) != 1 or "probe" not in gsm_data.columns:
            raise ValueError(
                f"Invalid format in {file.name}: expected 'probe' and one 'GSM*' column"
            )

        data_column = data_column[0]
        individual_gsm_data.append(gsm_data[["probe", data_column]].set_index("probe"))

    merged = pd.concat(individual_gsm_data, axis=1)
    output_path = path / "merged_gsms.parquet"
    merged.to_parquet(output_path)
    logger.info("Saved merged data to %s", output_path)
