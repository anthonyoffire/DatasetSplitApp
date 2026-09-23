# DatasetSplitApp

DatasetSplitApp is a Python desktop application that splits a dataset into training, validation, and test folders. It scans a source directory, optionally filters files by extension, shuffles the dataset, and copies files into output folders based on customizable split percentages.

## Features

- Split a dataset into train, validation, and test sets
- Custom train/validation/test percentages
- Optional minimum and maximum file counts for each split
- File extension filtering (all files or a custom comma-separated list)
- Recursive directory scanning
- Optional retention of subdirectory structure
- Output summary file with split totals
- Overwrite, skip, and abort handling for existing files
- Retry, skip, and abort handling for copy errors

## Requirements

- Python 3.10 or later
- A source dataset folder and an output directory
- No third-party Python packages are required; the app uses only the Python standard library, including `tkinter` for the graphical interface

## Usage

1. Run the app:
   ```bash
   python dataset_split_app.py
   ```
2. Select a source directory.
3. Choose output folders for training, validation, and test data.
4. Adjust split percentages and optional limits if needed.
5. Click "Generate split" to create the dataset partitions.
