from __future__ import annotations

import math
import json
import os
import random
import shutil
import threading
import tkinter as tk
from queue import Empty, Queue
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

CONFIG_PATH = Path(__file__).with_name("dataset_split_config.json")


def normalize_extensions(value: str) -> set[str]:
    return {
        f".{extension.strip().lstrip('.').lower()}"
        for extension in value.split(",")
        if extension.strip().lstrip(".")
    }

def extensions_are_valid(value: str) -> bool:
    entries = value.split(",")
    return bool(value.strip()) and all(entry.strip().lstrip(".") for entry in entries)


def load_app_config() -> dict:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            value = json.load(config_file)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_app_config(values: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def parse_percentages(*values: str) -> tuple[float, float, float]:
    try:
        percentages = tuple(float(value) for value in values)
    except ValueError as exc:
        raise ValueError("Percent values must be numeric.") from exc

    if (
        not all(math.isfinite(value) for value in percentages)
        or any(value < 0 for value in percentages)
    ):
        raise ValueError("Percent values cannot be negative.")
    if sum(percentages) <= 0:
        raise ValueError("Split percentages must add to a positive value.")

    return percentages


def parse_limits(
        minimum_values: tuple[str, str, str],
        maximum_values: tuple[str, str, str],
) -> tuple[dict[str, int], dict[str, int | None]]:
    names = ("train", "val", "test")
    minimums = {name: int(value) if value else 0 for name, value in zip(names, minimum_values)}
    maximums = {name: int(value) if value else None for name, value in zip(names, maximum_values)}
    return minimums, maximums


def cancel_preview_timer(root: tk.Tk, timer: list[int | None]) -> None:
    if timer[0] is not None:
        root.after_cancel(timer[0])
        timer[0] = None


def ask_file_action(
    parent: tk.Misc,
    title: str,
    message: str,
    actions: tuple[str, ...],
    checkbox_text: str | None = None,
) -> tuple[str, bool]:
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.grab_set()
    tk.Label(dialog, text=message, justify="left", wraplength=500, padx=20, pady=15).pack()
    apply_all = tk.BooleanVar(value=False)
    if checkbox_text is not None:
        ttk.Checkbutton(dialog, text=checkbox_text, variable=apply_all).pack(anchor="w", padx=20, pady=(0, 10))
    result = {"action": actions[-1]}

    def choose(action: str):
        result["action"] = action
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(padx=20, pady=(0, 15))
    for action in actions:
        tk.Button(button_frame, text=action, command=lambda choice=action: choose(choice)).pack(side="left", padx=4)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(actions[-1]))
    parent.wait_window(dialog)
    return result["action"], apply_all.get()


def count_files(
    root: Path | str | None,
    extensions: set[str] | None = None,
    recursive: bool = True,
) -> int:
    if root is None:
        return 0

    root = Path(root)
    if not root.exists():
        return 0

    total = 0
    stack = [root]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if recursive and entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file() and (
                        extensions is None or Path(entry.name).suffix.lower() in extensions
                    ):
                        total += 1
        except OSError:
            continue

    return total


def split_counts_by_percentage(total_files: int, train_pct: float, val_pct: float, test_pct: float):
    percentages = (train_pct, val_pct, test_pct)
    if not all(math.isfinite(value) for value in percentages) or any(value < 0 for value in percentages):
        raise ValueError("Percent values cannot be negative.")

    total_pct = train_pct + val_pct + test_pct
    if total_pct <= 0:
        raise ValueError("Split percentages must add to a positive value.")

    normalized = {
        "train": (train_pct / total_pct) * total_files,
        "val": (val_pct / total_pct) * total_files,
        "test": (test_pct / total_pct) * total_files,
    }

    counts = {name: int(math.floor(value)) for name, value in normalized.items()}
    remainder = total_files - sum(counts.values())

    order = sorted(
        normalized,
        key=lambda name: (normalized[name] - counts[name], normalized[name]),
        reverse=True,
    )

    for name in order[:remainder]:
        counts[name] += 1

    return counts


def apply_optional_limits(total_files: int, counts: dict[str, int], min_counts: dict[str, int], max_counts: dict[str, int]):
    for name in counts:
        minimum = min_counts.get(name, 0)
        maximum = max_counts.get(name)
        if minimum < 0 or (maximum is not None and maximum < 0):
            raise ValueError("File limits cannot be negative.")
        if maximum is not None and minimum > maximum:
            raise ValueError(f"Minimum cannot exceed maximum for {name}.")

        if counts[name] < minimum:
            counts[name] = minimum

        if maximum is not None and counts[name] > maximum:
            counts[name] = maximum

    total_after_limits = sum(counts.values())
    if total_after_limits > total_files:
        over = total_after_limits - total_files
        for name in sorted(counts, key=lambda n: counts[n], reverse=True):
            if over == 0:
                break
            removable = max(0, counts[name] - (min_counts.get(name, 0)))
            amount = min(removable, over)
            counts[name] -= amount
            over -= amount

    if total_after_limits < total_files:
        leftover = total_files - sum(counts.values())
        for name in sorted(counts, key=lambda n: (max_counts.get(n) is None, counts[n]), reverse=True):
            if leftover == 0:
                break
            maximum = max_counts.get(name)
            if maximum is not None:
                room = max(0, maximum - counts[name])
            else:
                room = leftover
            amount = min(room, leftover)
            counts[name] += amount
            leftover -= amount

    return counts

def create_dataset_split(
        src_root: str | Path,
        train_root: str | Path,
        test_root: str | Path,
        val_root: str | Path,
        train_pct: float = 80,
        val_pct: float = 10,
        test_pct: float = 10,
        min_counts: dict[str, int] | None = None,
        max_counts: dict[str, int] | None = None,
        seed: int | None = 42,
        copy_files: bool = True,
        image_extensions: set[str] | None = None,
        retain_subdirectory_structure: bool = False,
        parse_all_subdirectories: bool = True,
        write_split_info_file: bool = True,
        overwrite_handler=None,
        copy_failure_handler=None,
        progress_callback=None,
        cancel_event=None,
        cancelled_state=None,
) -> dict[str, int]:
    src_root = Path(src_root)
    train_root = Path(train_root)
    test_root = Path(test_root)
    val_root = Path(val_root)

    if not src_root.exists():
        raise FileNotFoundError(f"Source root does not exist: {src_root}")

    for root, label in [
        (train_root, "Train root"),
        (test_root, "Test root"),
        (val_root, "Validation root"),
    ]:
        if not root.exists() and not root.parent.exists():
            raise ValueError(f"{label} parent does not exist: {root.parent}")

    file_paths = src_root.rglob("*") if parse_all_subdirectories else src_root.iterdir()
    files = [
        p for p in file_paths
        if p.is_file() and (image_extensions is None or p.suffix.lower() in image_extensions)
    ]

    if not files:
        raise FileNotFoundError(f"No files were found in {src_root}")

    if not retain_subdirectory_structure:
        file_names = [path.name for path in files]
        if len(file_names) != len(set(file_names)):
            raise ValueError(
                "Duplicate file names cannot be flattened safely. "
                "Enable Retain subdirectory structure."
            )

    total_files = len(files)
    mins = min_counts or {}
    maxs = max_counts or {}

    raw_counts = split_counts_by_percentage(total_files, train_pct, val_pct, test_pct)
    counts = apply_optional_limits(total_files, raw_counts.copy(), mins, maxs)

    if sum(counts.values()) > total_files:
        raise ValueError("The final split totals exceed the number of files in the source directory.")

    rng = random.Random(seed)
    rng.shuffle(files)

    split_map = {
        "train": files[:counts["train"]],
        "val": files[counts["train"]:counts["train"] + counts["val"]],
        "test": files[
            counts["train"] + counts["val"]:
            counts["train"] + counts["val"] + counts["test"]
        ],
    }

    successful_counts = {"train": 0, "val": 0, "test": 0}
    overwrite_action = None
    failure_action = None
    processed_files = 0
    total_to_process = sum(len(group) for group in split_map.values())

    def mark_processed() -> None:
        nonlocal processed_files
        processed_files += 1
        if progress_callback:
            progress_callback(processed_files, total_to_process)

    for split_name, dst_root in {
        "train": train_root,
        "val": val_root,
        "test": test_root,
    }.items():
        if cancel_event and cancel_event.is_set():
            if cancelled_state is not None:
                cancelled_state[0] = True
            break
        dst_root.mkdir(parents=True, exist_ok=True)
        for src_path in split_map[split_name]:
            if cancel_event and cancel_event.is_set():
                if cancelled_state is not None:
                    cancelled_state[0] = True
                break
            relative_path = src_path.relative_to(src_root) if retain_subdirectory_structure else src_path.name
            dst_path = dst_root / relative_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            while True:
                if dst_path.exists() and overwrite_action != "overwrite":
                    if overwrite_action == "skip":
                        mark_processed()
                        break
                    if overwrite_handler is None:
                        action = "Overwrite"
                        apply_all = False
                    else:
                        action, apply_all = overwrite_handler(src_path, dst_path)
                    if action == "Abort":
                        raise RuntimeError("Split aborted because an output file already exists.")
                    if action in ("Skip", "Skip all"):
                        if apply_all or action == "Skip all":
                            overwrite_action = "skip"
                        mark_processed()
                        break
                    if apply_all:
                        overwrite_action = "overwrite"

                try:
                    if copy_files:
                        shutil.copy2(src_path, dst_path)
                    else:
                        shutil.move(src_path, dst_path)
                    successful_counts[split_name] += 1
                    mark_processed()
                    break
                except OSError as exc:
                    if failure_action == "Continue on all":
                        mark_processed()
                        break
                    if copy_failure_handler is None:
                        raise
                    action, apply_all = copy_failure_handler(src_path, dst_path, exc)
                    if action == "Abort":
                        raise RuntimeError(f"Split aborted after a file operation failed: {exc}") from exc
                    if action in ("Skip", "Skip all"):
                        if action == "Skip all":
                            failure_action = "Continue on all"
                        mark_processed()
                        break
                    if apply_all:
                        failure_action = "Continue on all"

        if cancelled_state is not None and cancelled_state[0]:
            break

    if write_split_info_file:
        write_split_totals(train_root.parent, successful_counts)

    return successful_counts


def preview_split(total_files: int, train_pct: float, val_pct: float, test_pct: float, min_counts: dict[str, int] | None = None, max_counts: dict[str, int] | None = None):
    mins = min_counts or {}
    maxs = max_counts or {}

    counts = split_counts_by_percentage(total_files, train_pct, val_pct, test_pct)
    final = apply_optional_limits(total_files, counts.copy(), mins, maxs)
    if sum(final.values()) > total_files:
        raise ValueError("Minimum limits exceed the number of available files.")
    return final


def build_preview_label(total_files: int, counts: dict[str, int]) -> str:
    return (
        f"Source files: {total_files:,}\n"
        f"Train: {counts['train']:,}\n"
        f"Val: {counts['val']:,}\n"
        f"Test: {counts['test']:,}"
    )


def write_split_totals(output_root: Path, counts: dict[str, int]) -> None:
    total = sum(counts.values())

    def format_total(name: str) -> str:
        count = counts[name]
        percentage = (count / total * 100) if total else 0
        return f"{count:,} : {percentage:.2f}%"

    report = (
        f"Total train images: {format_total('train')}\n"
        f"Total val images:   {format_total('val')}\n"
        f"Total test images:  {format_total('test')}\n"
    )
    (output_root / "split_info.txt").write_text(report, encoding="utf-8")


def build_preview_warning(total_files: int, counts: dict[str, int]) -> str:
    unassigned = total_files - sum(counts.values())
    return f"Warning: {unassigned:,} files will remain unassigned." if unassigned > 0 else ""


def set_preview_message(preview_label: tk.Label, warning_label: tk.Label, message: str, warning: str = "") -> None:
    status_label = getattr(preview_label, "status_label", preview_label)
    preview_table = getattr(preview_label, "preview_table", None)
    status_label.config(text=message)
    if hasattr(preview_label, "status_label"):
        status_label.pack(pady=14)
    if preview_table is not None:
        preview_table.pack_forget()
    warning_label.config(text=warning)


def set_preview_counts(preview_label: tk.Frame, warning_label: tk.Label, total_files: int, counts: dict[str, int], warning: str = "") -> None:
    status_label = getattr(preview_label, "status_label")
    preview_table = getattr(preview_label, "preview_table")
    status_label.config(text="")
    status_label.pack_forget()
    preview_table.pack(anchor="center")
    values = (total_files, counts["train"], counts["val"], counts["test"])
    for value_label, value in zip(preview_table.value_labels, values):
        value_label.config(text=f"{value:,}")
    warning_label.config(text=warning)


def update_preview(
        root: tk.Tk,
        source_var: tk.StringVar,
        train_pct_var: tk.StringVar,
        val_pct_var: tk.StringVar,
        test_pct_var: tk.StringVar,
        min_train_var: tk.StringVar,
        min_val_var: tk.StringVar,
        min_test_var: tk.StringVar,
        max_train_var: tk.StringVar,
        max_val_var: tk.StringVar,
        max_test_var: tk.StringVar,
        preview_label: tk.Label,
        warning_label: tk.Label,
        preview_timer: list[int | None],
        preview_dot_state: list[int],
        animate_counting_status,
        cached_source_path: list[str | None],
        cached_file_count: list[int | None],
        extension_mode_var: tk.StringVar,
        extension_var: tk.StringVar,
        cached_extension_key: list[tuple[str, ...] | None],
        parse_all_subdirectories_var: tk.BooleanVar,
        cached_parse_all: list[bool | None],
        preview_request_id: list[int],
        preview_queue: Queue,
    ):
        preview_request_id[0] += 1
        request_id = preview_request_id[0]
        cancel_preview_timer(root, preview_timer)
        source_path = source_var.get()
        if not source_path:
            cached_source_path[0] = None
            cached_file_count[0] = None
            cached_extension_key[0] = None
            cached_parse_all[0] = None
            set_preview_message(preview_label, warning_label, "[ No source directory chosen ]")
            return

        extension_mode = extension_mode_var.get()
        extension_text = extension_var.get()
        parse_all = parse_all_subdirectories_var.get()
        percentage_values = (train_pct_var.get(), val_pct_var.get(), test_pct_var.get())
        minimum_values = (min_train_var.get(), min_val_var.get(), min_test_var.get())
        maximum_values = (max_train_var.get(), max_val_var.get(), max_test_var.get())

        extensions = None
        if extension_mode == "specified":
            if not extensions_are_valid(extension_text):
                set_preview_message(preview_label, warning_label, "Enter a valid comma-separated extension list.")
                return
            extensions = normalize_extensions(extension_text)

        try:
            percentages = parse_percentages(*percentage_values)
            mins, maxs = parse_limits(minimum_values, maximum_values)
        except ValueError as exc:
            set_preview_message(preview_label, warning_label, str(exc))
            return

        extension_key = None if extensions is None else tuple(sorted(extensions))
        cached = (
            cached_source_path[0] == source_path
            and cached_extension_key[0] == extension_key
            and cached_parse_all[0] == parse_all
            and cached_file_count[0] is not None
        )
        if not cached:
            set_preview_message(preview_label, warning_label, "Counting files.")
            preview_dot_state[0] = 1
            preview_timer[0] = root.after(
                500,
                lambda: animate_counting_status(preview_label, preview_dot_state, preview_timer),
            )

        def finish_preview_error(completed_request_id: int, message: str):
            if completed_request_id == preview_request_id[0]:
                cancel_preview_timer(root, preview_timer)
                set_preview_message(preview_label, warning_label, message)

        def worker():
            try:
                if cached:
                    total_files = cached_file_count[0]
                else:
                    total_files = count_files(source_path, extensions, parse_all)
                counts = preview_split(
                    total_files,
                    percentages[0],
                    percentages[1],
                    percentages[2],
                    mins,
                    maxs,
                )

                def finish_preview():
                    if request_id != preview_request_id[0]:
                        return
                    cancel_preview_timer(root, preview_timer)
                    if not cached:
                        cached_source_path[0] = source_path
                        cached_file_count[0] = total_files
                        cached_extension_key[0] = extension_key
                        cached_parse_all[0] = parse_all
                    if total_files == 0:
                        set_preview_message(
                            preview_label,
                            warning_label,
                            "No matching files found in the selected source directory.",
                        )
                    else:
                        set_preview_counts(
                            preview_label,
                            warning_label,
                            total_files,
                            counts,
                            build_preview_warning(total_files, counts),
                        )

                preview_queue.put(finish_preview)
            except Exception as exc:
                preview_queue.put(
                    lambda message=f"Preview failed: {exc}":
                    finish_preview_error(request_id, message)
                )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

def main():
    root = tk.Tk()
    root.title("DatasetSplit")
    root.resizable(False, False)
    config = load_app_config()

    label_font = ("TkDefaultFont", 12)
    section_font = ("TkDefaultFont", 12, "bold")
    limits_font = ("TkDefaultFont", 10)

    source_var = tk.StringVar()
    output_root_var = tk.StringVar()

    preview_dot_state = [1]
    preview_timer = [None]
    cached_source_path = [None]
    cached_file_count = [None]
    cached_extension_key = [None]
    cached_parse_all = [None]
    preview_request_id = [0]
    preview_queue = Queue()
    split_cancel_event = threading.Event()
    split_running = [False]
    split_progress_state = [0, 1]
    extension_mode_var = tk.StringVar(value=config.get("extension_mode", "all"))
    extension_var = tk.StringVar(value=config.get("extensions", ""))
    parse_all_subdirectories_var = tk.BooleanVar(value=config.get("parse_all_subdirectories", True))
    retain_subdirectories_var = tk.BooleanVar(value=config.get("retain_subdirectories", True))
    write_split_info_var = tk.BooleanVar(value=config.get("write_split_info", True))
    random_seed_var = tk.BooleanVar(value=config.get("use_seed", True))
    seed_var = tk.StringVar(value=str(config.get("seed", "42")))

    directory_frame = tk.Frame(root)
    directory_frame.pack(fill="x", padx=30)

    section_grid = tk.Frame(root)
    section_grid.pack(fill="x", padx=30)
    section_grid.columnconfigure(0, weight=1)
    section_grid.columnconfigure(1, weight=1)
    left_top_frame = tk.Frame(section_grid)
    left_top_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    right_top_frame = tk.Frame(section_grid)
    right_top_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
    left_bottom_frame = tk.Frame(section_grid)
    left_bottom_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
    right_bottom_frame = tk.Frame(section_grid)
    right_bottom_frame.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
    def choose_dir(var: tk.StringVar):
        selected = filedialog.askdirectory(parent=root)
        if selected:
            var.set(selected)
            refresh_preview()

    def animate_counting_status(periodic_label: tk.Label, dot_count: list[int], timer_id: list[int | None]):
        dots = "." * min(dot_count[0], 3)
        periodic_label.config(text=f"Counting files{dots}")
        dot_count[0] += 1
        if dot_count[0] > 3:
            dot_count[0] = 1
        timer_id[0] = root.after(500, lambda: animate_counting_status(periodic_label, dot_count, timer_id))

    def decide_overwrite(src_path: Path, dst_path: Path):
        return run_on_ui(lambda: ask_file_action(
                root,
                "File already exists",
                f"The destination file already exists:\n{dst_path}\n\nOverwrite it?",
                ("Overwrite", "Skip", "Abort"),
                "Do this for all existing files",
            )
        )

    def decide_copy_failure(src_path: Path, dst_path: Path, error: OSError):
        return run_on_ui(lambda: ask_file_action(
                root,
                "File operation failed",
                f"Could not write:\n{dst_path}\n\n{error}",
                ("Retry", "Skip", "Skip all", "Abort"),
            )
        )

    def run_on_ui(callback):
        result = {}
        finished = threading.Event()

        def execute():
            try:
                result["value"] = callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                finished.set()

        preview_queue.put(execute)
        finished.wait()
        if "error" in result:
            raise result["error"]
        return result["value"]

    def process_preview_queue():
        try:
            while True:
                preview_queue.get_nowait()()
        except Empty:
            pass
        root.after(50, process_preview_queue)

    process_preview_queue()

    def update_split_progress(completed: int, total: int):
        split_progress_state[0] = completed
        split_progress_state[1] = total

    def update_progress_display(completed: int, total: int, percent: float):
        split_progress_label.config(text=f"{completed} / {total}")
        progress_total[0] = max(total, 1)
        progress_completed[0] = completed
        draw_progress_bar(percent)

    def poll_split_progress():
        if split_running[0]:
            completed, total = split_progress_state
            percent = completed / total * 100 if total else 100
            update_progress_display(completed, total, percent)
        root.after(50, poll_split_progress)

    poll_split_progress()

    def set_controls_enabled(enabled: bool):
        state = "!disabled" if enabled else "disabled"

        def visit(widget):
            if isinstance(widget, tk.Button):
                widget.configure(state="normal" if enabled else "disabled")
            elif isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry)):
                try:
                    widget.state([state])
                except tk.TclError:
                    widget.configure(state="normal" if enabled else "disabled")
            for child in widget.winfo_children():
                visit(child)

        for child in root.winfo_children():
            visit(child)

    def split_finished(counts: dict[str, int] | None, error: str | None, cancelled: bool = False):
        completed, total = split_progress_state
        update_progress_display(completed, total, completed / total * 100 if total else 100)
        split_running[0] = False
        split_cancel_event.clear()
        set_progress_visible(False)
        set_controls_enabled(True)
        generate_button.configure(text="Generate split", command=process_user_inputs, state="normal")
        if error:
            messagebox.showerror("Split failed", error)
        elif cancelled:
            messagebox.showinfo("Split cancelled", "The split was cancelled. Counts reflect files written before cancellation.")
        else:
            messagebox.showinfo(
                "Split complete",
                f"Created split successfully.\nTrain: {counts['train']}\nValidation: {counts['val']}\nTest: {counts['test']}",
            )

    def process_user_inputs():
        source_path = source_var.get()
        if not source_path:
            messagebox.showerror("Missing source", "Please choose a source directory first.")
            return

        output_root = output_root_var.get()
        if not output_root:
            messagebox.showerror("Missing output folder", "Please choose an output directory.")
            return

        train_root = Path(output_root) / "train"
        val_root = Path(output_root) / "val"
        test_root = Path(output_root) / "test"

        try:
            train_pct, val_pct, test_pct = parse_percentages(
                train_pct_var.get(), val_pct_var.get(), test_pct_var.get()
            )
        except ValueError as exc:
            messagebox.showerror("Bad percentages", str(exc))
            return

        try:
            mins, maxs = parse_limits(
                (min_train_var.get(), min_val_var.get(), min_test_var.get()),
                (max_train_var.get(), max_val_var.get(), max_test_var.get()),
            )
        except ValueError as exc:
            messagebox.showerror("Bad file limits", str(exc))
            return

        extensions = None
        if extension_mode_var.get() == "specified":
            if not extensions_are_valid(extension_var.get()):
                messagebox.showerror("Invalid extensions", "Enter a valid comma-separated extension list.")
                return
            extensions = normalize_extensions(extension_var.get())

        retain_subdirectories = parse_all_subdirectories_var.get() and retain_subdirectories_var.get()
        parse_all = parse_all_subdirectories_var.get()
        write_split_info = write_split_info_var.get()
        try:
            seed = int(seed_var.get()) if random_seed_var.get() else random.SystemRandom().randint(0, 2**32 - 1)
        except ValueError:
            messagebox.showerror("Invalid seed", "The seed must be an integer.")
            return
        split_running[0] = True
        split_cancel_event.clear()
        set_controls_enabled(False)
        set_progress_visible(True)
        generate_button.configure(text="Cancel", command=cancel_split, state="normal")
        progress_total[0] = max(cached_file_count[0] or 1, 1)
        progress_completed[0] = 0
        split_progress_state[0] = 0
        split_progress_state[1] = progress_total[0]
        draw_progress_bar(0)
        split_progress_label.config(text="0 / 0")
        cancelled_state = [False]

        def worker():
            try:
                counts = create_dataset_split(
                    src_root=source_path,
                    train_root=train_root,
                    test_root=test_root,
                    val_root=val_root,
                    train_pct=train_pct,
                    val_pct=val_pct,
                    test_pct=test_pct,
                    min_counts=mins,
                    max_counts=maxs,
                    image_extensions=extensions,
                    retain_subdirectory_structure=(
                        retain_subdirectories
                    ),
                    parse_all_subdirectories=parse_all,
                    write_split_info_file=write_split_info,
                    copy_files=True,
                    seed=seed,
                    overwrite_handler=decide_overwrite,
                    copy_failure_handler=decide_copy_failure,
                    progress_callback=update_split_progress,
                    cancel_event=split_cancel_event,
                    cancelled_state=cancelled_state,
                )
                preview_queue.put(lambda: split_finished(counts, None, cancelled_state[0]))
            except Exception as exc:
                preview_queue.put(lambda message=str(exc): split_finished(None, message))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_split():
        if split_running[0]:
            split_cancel_event.set()
            split_progress_label.config(text="Cancelling...")

    # Source dir
    tk.Label(directory_frame, text="Source Directory", font=section_font).pack(anchor="w", pady=(14, 3))
    source_entry = ttk.Entry(directory_frame, textvariable=source_var, state="readonly")
    source_entry.pack(fill="x")
    tk.Button(directory_frame, text="Choose folder", command=lambda: choose_dir(source_var), width=18, cursor="hand2").pack(pady=(6, 0), fill="x")

    tk.Label(right_top_frame, text="Extensions", font=section_font).pack(anchor="w", pady=(14, 5))
    extension_options_frame = tk.Frame(right_top_frame)
    extension_options_frame.pack(anchor="w", fill="x")
    all_extensions_var = tk.IntVar(value=1 if extension_mode_var.get() == "all" else 0)
    specified_extensions_var = tk.IntVar(value=1 if extension_mode_var.get() == "specified" else 0)
    extension_status_var = tk.StringVar()

    extension_entry = ttk.Entry(extension_options_frame, textvariable=extension_var, state="disabled")

    def update_extension_status(*_):
        if extension_mode_var.get() != "specified":
            extension_status_var.set("")
            extension_status_label.config(fg="black")
        elif extensions_are_valid(extension_var.get()):
            extension_status_var.set("Valid extension list")
            extension_status_label.config(fg="green")
        else:
            extension_status_var.set("Invalid extension list")
            extension_status_label.config(fg="red")

    def select_all_extensions():
        all_extensions_var.set(1)
        specified_extensions_var.set(0)
        extension_mode_var.set("all")
        extension_entry.state(["disabled"])
        update_extension_status()
        refresh_preview()

    def select_specified_extensions():
        specified_extensions_var.set(1)
        all_extensions_var.set(0)
        extension_mode_var.set("specified")
        extension_entry.state(["!disabled"])
        update_extension_status()
        refresh_preview()

    ttk.Checkbutton(
        extension_options_frame,
        text="All",
        variable=all_extensions_var,
        command=select_all_extensions,
    ).pack(anchor="w")
    ttk.Checkbutton(
        extension_options_frame,
        text="Specify file extensions",
        variable=specified_extensions_var,
        command=select_specified_extensions,
    ).pack(anchor="w")
    tk.Label(
        extension_options_frame,
        text="Enter a comma-separated list of file extensions.",
        font=("TkDefaultFont", 10),
    ).pack(anchor="w", pady=(3, 2))
    extension_entry.pack(fill="x")
    extension_status_label = tk.Label(
        extension_options_frame,
        textvariable=extension_status_var,
        font=("TkDefaultFont", 9),
        height=1,
    )
    extension_status_label.pack(anchor="w", pady=(2, 0))
    extension_var.trace_add("write", update_extension_status)
    extension_entry.state(["!disabled"] if extension_mode_var.get() == "specified" else ["disabled"])
    update_extension_status()

    def remove_extension_focus(event):
        if event.widget is not extension_entry and not isinstance(event.widget, (tk.Entry, ttk.Entry)):
            root.focus_set()

    root.bind_all("<Button-1>", remove_extension_focus, add="+")

    extension_entry.bind("<FocusOut>", lambda _event: refresh_preview())

    # Split percentages row
    tk.Label(left_top_frame, text="Desired Split Percentages", font=section_font).pack(anchor="w", pady=(14, 12))
    pct_frame = tk.Frame(left_top_frame)
    pct_frame.pack(pady=(0, 14), fill="x")

    for column in range(3):
        pct_frame.columnconfigure(column, weight=1)

    tk.Label(pct_frame, text="Train %", font=label_font).grid(row=0, column=0, padx=10)
    tk.Label(pct_frame, text="Val %", font=label_font).grid(row=0, column=1, padx=10)
    tk.Label(pct_frame, text="Test %", font=label_font).grid(row=0, column=2, padx=10)

    train_pct_var = tk.StringVar(value=str(config.get("train_percentage", "80")))
    val_pct_var = tk.StringVar(value=str(config.get("val_percentage", "10")))
    test_pct_var = tk.StringVar(value=str(config.get("test_percentage", "10")))

    def update_train_percentage(*_):
        try:
            remaining = 100 - float(val_pct_var.get()) - float(test_pct_var.get())
            train_pct_var.set(f"{remaining:g}")
        except ValueError:
            train_pct_var.set("")

    train_pct_entry = ttk.Entry(pct_frame, textvariable=train_pct_var, width=10, justify="center", state="readonly")
    train_pct_entry.grid(row=1, column=0, padx=10, pady=(5, 0), ipady=2)
    ttk.Entry(pct_frame, textvariable=val_pct_var, width=10, justify="center").grid(row=1, column=1, padx=10, pady=(5, 0), ipady=2)
    ttk.Entry(pct_frame, textvariable=test_pct_var, width=10, justify="center").grid(row=1, column=2, padx=10, pady=(5, 0), ipady=2)

    def update_preview_from_percentages(*_):
        update_train_percentage()
        refresh_preview()

    for widget in [val_pct_var, test_pct_var]:
        widget.trace_add("write", update_preview_from_percentages)

    # Optional limits
    tk.Label(left_bottom_frame, text="Optional Min/Max File Counts", font=section_font).pack(anchor="w", pady=(8, 12))
    limits_frame = tk.Frame(left_bottom_frame)
    limits_frame.pack(pady=(0, 14), fill="x")

    for column in range(4):
        limits_frame.columnconfigure(column, weight=1)

    tk.Label(limits_frame, text="", font=limits_font).grid(row=0, column=0, padx=10)
    tk.Label(limits_frame, text="Train", font=limits_font).grid(row=0, column=1, padx=10)
    tk.Label(limits_frame, text="Val", font=limits_font).grid(row=0, column=2, padx=10)
    tk.Label(limits_frame, text="Test", font=limits_font).grid(row=0, column=3, padx=10)

    tk.Label(limits_frame, text="Min", font=limits_font).grid(row=1, column=0, padx=10)
    tk.Label(limits_frame, text="Max", font=limits_font).grid(row=2, column=0, padx=10)

    min_train_var = tk.StringVar(value=str(config.get("min_train", "")))
    min_val_var = tk.StringVar(value=str(config.get("min_val", "")))
    min_test_var = tk.StringVar(value=str(config.get("min_test", "")))
    max_train_var = tk.StringVar(value=str(config.get("max_train", "")))
    max_val_var = tk.StringVar(value=str(config.get("max_val", "")))
    max_test_var = tk.StringVar(value=str(config.get("max_test", "")))

    def refresh_preview():
        update_preview(
            root,
            source_var,
            train_pct_var,
            val_pct_var,
            test_pct_var,
            min_train_var,
            min_val_var,
            min_test_var,
            max_train_var,
            max_val_var,
            max_test_var,
            preview_label,
            warning_label,
            preview_timer,
            preview_dot_state,
            animate_counting_status,
            cached_source_path,
            cached_file_count,
            extension_mode_var,
            extension_var,
            cached_extension_key,
            parse_all_subdirectories_var,
            cached_parse_all,
            preview_request_id,
            preview_queue,
        )

    ttk.Entry(limits_frame, textvariable=min_train_var, width=10, justify="center").grid(row=1, column=1, padx=10, pady=(5, 2), ipady=2)
    ttk.Entry(limits_frame, textvariable=min_val_var, width=10, justify="center").grid(row=1, column=2, padx=10, pady=(5, 2), ipady=2)
    ttk.Entry(limits_frame, textvariable=min_test_var, width=10, justify="center").grid(row=1, column=3, padx=10, pady=(5, 2), ipady=2)

    ttk.Entry(limits_frame, textvariable=max_train_var, width=10, justify="center").grid(row=2, column=1, padx=10, pady=(2, 0), ipady=2)
    ttk.Entry(limits_frame, textvariable=max_val_var, width=10, justify="center").grid(row=2, column=2, padx=10, pady=(2, 0), ipady=2)
    ttk.Entry(limits_frame, textvariable=max_test_var, width=10, justify="center").grid(row=2, column=3, padx=10, pady=(2, 0), ipady=2)

    for var in [min_train_var, min_val_var, min_test_var, max_train_var, max_val_var, max_test_var]:
        var.trace_add("write", lambda *_: refresh_preview())

    tk.Label(right_bottom_frame, text="Options", font=section_font).pack(anchor="w", pady=(8, 5))
    retain_options_frame = tk.Frame(right_bottom_frame)
    retain_options_frame.pack(anchor="w", fill="x", padx=20)

    def toggle_parse_subdirectories():
        if parse_all_subdirectories_var.get():
            retain_subdirectories_check.state(["!disabled"])
        else:
            retain_subdirectories_check.state(["disabled"])
        refresh_preview()

    parse_subdirectories_check = ttk.Checkbutton(
        retain_options_frame,
        text="Parse all sub-directories",
        variable=parse_all_subdirectories_var,
        command=toggle_parse_subdirectories,
    )
    parse_subdirectories_check.pack(anchor="w")

    retain_subdirectory_row = tk.Frame(retain_options_frame)
    retain_subdirectory_row.pack(anchor="w")
    tk.Label(retain_subdirectory_row, text="∟", font=label_font).pack(side="left", padx=(0, 4))

    retain_subdirectories_check = ttk.Checkbutton(
        retain_subdirectory_row,
        text="Retain subdirectory structure",
        variable=retain_subdirectories_var,
    )
    retain_subdirectories_check.pack(side="left")

    ttk.Checkbutton(
        retain_options_frame,
        text="Write split totals to file",
        variable=write_split_info_var,
    ).pack(anchor="w", pady=(4, 0))

    seed_row = tk.Frame(retain_options_frame)
    seed_row.pack(anchor="w", pady=(4, 0))
    random_seed_check = ttk.Checkbutton(
        seed_row,
        text="Use seed",
        variable=random_seed_var,
    )
    random_seed_check.pack(side="left")
    tk.Label(seed_row, text="Seed:", font=limits_font).pack(side="left", padx=(12, 4))
    seed_entry = ttk.Entry(seed_row, textvariable=seed_var, width=10)
    seed_entry.pack(side="left")

    def update_seed_state(*_):
        seed_entry.state(["!disabled"] if random_seed_var.get() else ["disabled"])

    random_seed_var.trace_add("write", update_seed_state)
    update_seed_state()

    root.update_idletasks()
    top_height = max(left_top_frame.winfo_reqheight(), right_top_frame.winfo_reqheight())
    bottom_height = max(left_bottom_frame.winfo_reqheight(), right_bottom_frame.winfo_reqheight())
    for frame, height in (
        (left_top_frame, top_height),
        (right_top_frame, top_height),
        (left_bottom_frame, bottom_height),
        (right_bottom_frame, bottom_height),
    ):
        frame.configure(height=height)
        frame.grid_propagate(False)

    tk.Label(directory_frame, text="Output Directory", font=section_font).pack(anchor="w", pady=(20, 3))
    output_entry = ttk.Entry(directory_frame, textvariable=output_root_var, state="readonly")
    output_entry.pack(fill="x")
    tk.Button(directory_frame, text="Choose folder", command=lambda: choose_dir(output_root_var), width=18, cursor="hand2").pack(pady=(6, 20), fill="x")

    tk.Label(root, text="Split Preview", font=section_font).pack(anchor="w", padx=30, pady=(20, 3))
    preview_label = tk.Frame(root, height=90)
    preview_label.pack(fill="x", pady=(0, 0))
    preview_label.pack_propagate(False)
    preview_label.status_label = tk.Label(preview_label, font=label_font)
    preview_label.status_label.pack(pady=14)
    preview_table = tk.Frame(preview_label)
    preview_table.value_labels = []
    for row, (set_name, value) in enumerate((("Source", ""), ("Train", ""), ("Validation", ""), ("Test", ""))):
        tk.Label(preview_table, text=set_name, font=limits_font, width=10, anchor="w").grid(row=row, column=0, padx=(0, 6))
        value_label = tk.Label(preview_table, text=value, font=limits_font, width=10, anchor="e")
        value_label.grid(row=row, column=1)
        preview_table.value_labels.append(value_label)
    preview_label.preview_table = preview_table
    warning_label = tk.Label(root, anchor="w", padx=30, height=1, font=("TkDefaultFont", 9), fg="red")
    warning_label.pack(fill="x")
    set_preview_message(preview_label, warning_label, "[ No source directory chosen ]")

    split_progress_label = tk.Label(root, text="0 / 0", font=limits_font)
    split_progress_label.pack(fill="x", padx=30, pady=(2, 0))
    split_progress_frame = tk.Frame(root)
    split_progress_frame.pack(fill="x", padx=30, pady=(2, 0))
    progress_total = [1]
    progress_completed = [0]
    progress_display_active = [False]
    split_progress_canvas = tk.Canvas(
        split_progress_frame,
        height=20,
        highlightthickness=0,
        bg="#d9d9d9",
    )
    split_progress_canvas.pack(fill="x")

    def draw_progress_bar(percent: float | None = None):
        if percent is None:
            percent = progress_completed[0] / progress_total[0] * 100
        width = split_progress_canvas.winfo_width()
        height = split_progress_canvas.winfo_height()
        split_progress_canvas.delete("all")
        split_progress_canvas.create_rectangle(0, 0, width, height, fill="#d9d9d9", outline="")
        if not progress_display_active[0]:
            return
        split_progress_canvas.create_rectangle(
            0, 0, width * min(max(percent, 0), 100) / 100, height,
            fill="#4a90e2",
            outline="",
        )
        split_progress_canvas.create_text(
            width / 2,
            height / 2,
            text=f"{percent:.2f}%",
            font=limits_font,
            fill="black",
        )

    split_progress_canvas.bind("<Configure>", lambda _event: draw_progress_bar())
    draw_progress_bar(0)

    def set_progress_visible(visible: bool):
        progress_display_active[0] = visible
        if visible:
            split_progress_label.config(text="0 / 0")
            draw_progress_bar(0)
        else:
            split_progress_label.config(text="")
            split_progress_canvas.delete("all")

    set_progress_visible(False)

    generate_button = tk.Button(root, text="Generate split", command=process_user_inputs, width=18, cursor="hand2")
    generate_button.pack(pady=15, padx=30, fill="x")

    def close_app():
        split_cancel_event.set()
        save_app_config({
            "extension_mode": extension_mode_var.get(),
            "extensions": extension_var.get(),
            "train_percentage": train_pct_var.get(),
            "val_percentage": val_pct_var.get(),
            "test_percentage": test_pct_var.get(),
            "min_train": min_train_var.get(),
            "min_val": min_val_var.get(),
            "min_test": min_test_var.get(),
            "max_train": max_train_var.get(),
            "max_val": max_val_var.get(),
            "max_test": max_test_var.get(),
            "parse_all_subdirectories": parse_all_subdirectories_var.get(),
            "retain_subdirectories": retain_subdirectories_var.get(),
            "write_split_info": write_split_info_var.get(),
            "use_seed": random_seed_var.get(),
            "seed": seed_var.get(),
        })
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_app)

    root.update_idletasks()
    window_width = root.winfo_reqwidth()
    window_height = root.winfo_reqheight()
    root.geometry(f"{window_width}x{window_height}")
    root.mainloop()


if __name__ == "__main__":
    main()
