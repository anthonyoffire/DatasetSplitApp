# DatasetSplitApp

DatasetSplit is a Python desktop application that splits a dataset into training, validation, and test folders. It scans a source directory, optionally filters files by extension, shuffles the dataset, and copies files into output folders based on customizable split percentages. It is non-destructive, using copy rather than move operations.

## Features

- Split a dataset into train, validation, and test sets
- Custom train/validation/test percentages
- Optional minimum and maximum file counts for each split
- File extension filtering (all files or a custom comma-separated list)
- Optional recursive directory scanning
- Optional retention of subdirectory structure
- Output summary file with split totals
- Overwrite, skip, and abort handling for existing files
- Retry, skip, and abort handling for copy errors
- Optional random seed for reproducibility
- Settings persistence

## Requirements

- Python 3.10 or later

## Usage

1. Run the app:
   ```bash
   python dataset_split_app.py
   ```
