#!/usr/bin/env python3
"""
SpecStory Wrapper - Records timestamps for Claude Code sessions.

This wrapper intercepts specstory commands and logs timestamps for each
User/Agent message exchange, then merges them into the markdown history files.
"""

import os
import sys
import subprocess
import time
import glob
from pathlib import Path
import shutil
import signal
import json
import re
from typing import List, Optional, Dict, Any

# Dynamically find specstory-real, handling homebrew upgrades
def is_wrapper_script(path: Optional[str]) -> bool:
    """Check if a given path is the wrapper script (bash or Python)."""
    if not path or not os.path.exists(path):
        return False
    
    path_abs = os.path.abspath(path)
    wrapper_bash = os.path.abspath(os.path.expanduser("~/bin/specstory"))
    wrapper_py = os.path.abspath(os.path.expanduser("~/.specstory_wrapper/specstory_wrapper.py"))
    
    if path_abs == wrapper_bash or path_abs == wrapper_py:
        return True
    
    # Check file content (open in binary mode to handle binary files safely)
    try:
        with open(path, 'rb') as f:
            content_bytes = f.read(500)
            # Try to decode as UTF-8, fallback to byte search for binary files
            try:
                content = content_bytes.decode('utf-8')
                if 'specstory_wrapper.py' in content:
                    return True
            except UnicodeDecodeError:
                pass  # Binary file, will check bytes below
            # Check for the string in bytes (handles binary files)
            if b'specstory_wrapper.py' in content_bytes:
                return True
    except (OSError, IOError, PermissionError):
        # If file can't be read, assume it's not the wrapper
        pass
    
    return False

def find_real_specstory() -> Optional[str]:
    """Find the real specstory binary, handling homebrew version changes.

    Search order:
    1. SPECSTORY_ORIGINAL/SPECSTORY_REAL/ORIGINAL_SPECSTORY environment variable
    2. brew --prefix based location (specstory-real or specstory)
    3. System PATH (excluding our wrapper directory)

    Returns:
        Absolute path to the real specstory binary, or None if not found.
    """
    # Get the wrapper's own path to exclude it from PATH searches
    wrapper_path = os.path.expanduser("~/bin/specstory")
    wrapper_dir = os.path.dirname(os.path.abspath(wrapper_path))
    
    # Respect environment override first (set by the installer wrapper when renaming isn't possible)
    env_path = os.environ.get("SPECSTORY_ORIGINAL") or os.environ.get("SPECSTORY_REAL") or os.environ.get("ORIGINAL_SPECSTORY")
    if env_path:
        # Expand user home directory if present
        env_path = os.path.expanduser(env_path)
        
        # If it's an absolute path and exists, validate it's not the wrapper
        if os.path.isabs(env_path):
            if os.path.exists(env_path):
                if not is_wrapper_script(env_path):
                    return os.path.abspath(env_path)
            # If absolute path doesn't exist, continue to fallback methods
        else:
            # Relative path: try to resolve it in PATH (excluding wrapper directory)
            path_env = os.environ.get("PATH", "")
            # Remove wrapper directory from PATH temporarily
            path_parts = path_env.split(os.pathsep)
            filtered_path = os.pathsep.join([p for p in path_parts if p != wrapper_dir and p != os.path.expanduser("~/bin")])
            old_path = os.environ.get("PATH")
            try:
                os.environ["PATH"] = filtered_path
                resolved = shutil.which(env_path)
                if resolved:
                    resolved_abs = os.path.abspath(resolved)
                    if not is_wrapper_script(resolved_abs):
                        return resolved_abs
            finally:
                if old_path:
                    os.environ["PATH"] = old_path
                else:
                    os.environ.pop("PATH", None)

    try:
        prefix = subprocess.check_output(
            ["brew", "--prefix"],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        real_path = os.path.join(prefix, "bin", "specstory-real")
        # If specstory-real exists and is valid, use it
        if os.path.exists(real_path):
            # Check if it's a broken symlink
            if os.path.islink(real_path):
                try:
                    os.stat(real_path)  # Will raise if symlink is broken
                    return real_path
                except OSError:
                    # Broken symlink, fix it
                    pass
            else:
                return real_path

        # specstory-real doesn't exist or is broken, find the current version
        specstory_path = subprocess.check_output(
            ["brew", "--prefix", "specstory"],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()

        real_bin = os.path.join(specstory_path, "bin", "specstory")

        # Create/update the specstory-real symlink
        if os.path.exists(real_path) or os.path.islink(real_path):
            os.remove(real_path)
        os.symlink(real_bin, real_path)

        return real_path
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass

    # Fallback: try to find specstory in PATH, excluding our wrapper directory
    path_env = os.environ.get("PATH", "")
    path_parts = path_env.split(os.pathsep)
    filtered_path = os.pathsep.join([
        p for p in path_parts
        if p != wrapper_dir and p != os.path.expanduser("~/bin")
    ])

    old_path = os.environ.get("PATH")
    try:
        os.environ["PATH"] = filtered_path
        resolved = shutil.which("specstory")
        if resolved and not is_wrapper_script(resolved):
            return os.path.abspath(resolved)
    finally:
        if old_path is not None:
            os.environ["PATH"] = old_path
        else:
            os.environ.pop("PATH", None)

    # Not found
    return None

REAL = find_real_specstory()
HIST_DIR = ".specstory/history"
TS_DIR = ".specstory/timestamps"

POLL_INTERVAL = 0.1

def read_lines_text(path: str) -> List[str]:
    """Read a text file into normalized lines (LF) with UTF-8 decoding."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.split('\n')

def find_conversation_header_indices(lines: List[str]) -> List[int]:
    """Find indices of User/Agent header blocks."""
    indices: List[int] = []
    for i, line in enumerate(lines):
        if line.startswith('_**') and line.endswith('**_') and ('User' in line or 'Agent' in line):
            indices.append(i)
    return indices

def first_meaningful_line_after(lines: List[str], start_idx: int) -> str:
    """Return first non-empty, non-separator line following a header. Fallback to header line."""
    for j in range(start_idx + 1, len(lines)):
        content = lines[j].strip()
        if not content or content == '---':
            continue
        return content
    return lines[start_idx].strip() if start_idx < len(lines) else ''


def get_timestamp_file_for_md(md_path: str) -> str:
    """Given a markdown path (absolute or relative), return the absolute timestamps file path alongside it.
    
    Works cross-platform (Linux, macOS, Windows) by normalizing paths properly.
    """
    # Normalize and resolve to absolute path (handles relative paths, .., ., symlinks, etc.)
    # This works even if the file doesn't exist yet (handles cases where file is being created)
    md_abs = os.path.abspath(os.path.normpath(md_path))
    
    # Derive directories: md_path is in .specstory/history/, so:
    # md_abs = /path/to/project/.specstory/history/file.md
    # history_dir = /path/to/project/.specstory/history
    # specstory_dir = /path/to/project/.specstory
    history_dir = os.path.dirname(md_abs)  # .../.specstory/history
    specstory_dir = os.path.dirname(history_dir)  # .../.specstory
    
    # Build timestamps directory path using os.path.join for cross-platform compatibility
    ts_dir_for_file = os.path.join(specstory_dir, "timestamps")
    
    # Create directory if it doesn't exist (works on all platforms)
    os.makedirs(ts_dir_for_file, exist_ok=True)
    
    # Get base filename without extension (cross-platform)
    base = Path(md_abs).stem
    
    # Return absolute path to timestamp file
    ts_file_path = os.path.join(ts_dir_for_file, f"{base}.timestamps")
    return os.path.normpath(ts_file_path)

def get_most_recent_md_file() -> Optional[str]:
    """Get the most recently modified .md file in the current project's history directory.
    
    Works cross-platform by normalizing paths properly.
    """
    # Use os.path.join for cross-platform path construction
    hist_pattern = os.path.join(HIST_DIR, "*.md")
    md_files = glob.glob(hist_pattern)
    if not md_files:
        return None
    # Normalize paths and get the most recent one
    md_files_abs = [os.path.abspath(os.path.normpath(f)) for f in md_files]
    latest = max(md_files_abs, key=os.path.getmtime)
    return os.path.normpath(latest)

def start_watcher(before_file: Optional[str], before_mtime: Optional[float]) -> None:
    """Start the background watcher that logs timestamp|first-line for new User/Agent entries."""
    # Pre-create pidfile so the parent can reliably wait for it to populate
    Path(f"/tmp/specstory_watcher_{os.getpid()}").touch()

    pid = os.fork()
    if pid != 0:
        # Parent process
        return

    # Child process - detach from parent
    os.setsid()

    # Determine which history file to watch by detecting any md whose mtime increases
    # since startup, or any new md that appears. This supports both new sessions and resumes.
    def list_md_files():
        # Use os.path.join for cross-platform path construction
        hist_pattern = os.path.join(HIST_DIR, "*.md")
        files = glob.glob(hist_pattern)
        return [os.path.abspath(os.path.normpath(p)) for p in files]

    # Snapshot initial mtimes
    initial_mtimes = {}
    for p in list_md_files():
        initial_mtimes[p] = os.path.getmtime(p)

    new_file = None
    start_wait = time.time()
    while new_file is None:
        candidates = []
        for p in list_md_files():
            mt = os.path.getmtime(p)
            if p not in initial_mtimes:
                candidates.append((p, mt))
            elif mt > (initial_mtimes.get(p, 0.0) + 1e-6):
                candidates.append((p, mt))
        if candidates:
            # Choose the most recently modified candidate
            candidates.sort(key=lambda t: t[1], reverse=True)
            new_file = candidates[0][0]
            break

        time.sleep(POLL_INTERVAL)

    # Create timestamp file for the new md file (next to the md's .specstory)
    new_file = os.path.abspath(new_file)
    logfile = get_timestamp_file_for_md(new_file)
    Path(logfile).touch()

    # Write PID metadata so parent can stop this watcher
    pidfile = f"/tmp/specstory_watcher_{os.getppid()}"
    with open(pidfile, 'w') as f:
        f.write(json.dumps({"pid": os.getpid(), "target": new_file}))

    # Inline watcher loop: ensure timestamps exist for new history files and
    # append new entries for all markdown files in this history directory
    while True:
        # Detect and initialize new markdown files by creating their timestamps files
        md_files = list_md_files()
        for md_path in md_files:
            ts_path = get_timestamp_file_for_md(md_path)
            if not os.path.exists(ts_path):
                Path(ts_path).touch()

        # For each md, compute headers with meaningful content and append new timestamps
        ts_now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        for md_path in md_files:
            ts_path = get_timestamp_file_for_md(md_path)

            # Read existing entries
            existing = []
            if os.path.exists(ts_path):
                with open(ts_path, 'r', encoding='utf-8') as f:
                    existing = [ln.strip() for ln in f if ln.strip()]

            # Compute snippets for headers-with-content
            lines = read_lines_text(md_path)
            hdrs = find_conversation_header_indices(lines)
            snippets = []
            for idx in hdrs:
                header_txt = lines[idx].strip() if idx < len(lines) else ''
                snippet = first_meaningful_line_after(lines, idx)
                if snippet and snippet != header_txt and snippet != '---':
                    snippets.append(snippet)

            # Append only newly appeared entries
            if len(existing) < len(snippets):
                with open(ts_path, 'a') as out:
                    for i in range(len(existing), len(snippets)):
                        out.write(f"{ts_now}|{snippets[i]}\n")

        time.sleep(POLL_INTERVAL)


def extract_base_role(header_line: str) -> str:
    """Extract the base role from a header, removing timestamp if present.
    
    Examples:
        '_**User**_' -> 'User'
        '_**User (2024-01-01T12:00:00Z)**_' -> 'User'
        '_**Agent**_' -> 'Agent'
    """
    # Remove _** and **_
    content = header_line[3:-3]
    # Check if there's a timestamp in parentheses
    if '(' in content and ')' in content:
        # Extract everything before the opening parenthesis
        role = content.split('(')[0].strip()
        return role
    return content.strip()

def header_has_timestamp(header_line: str) -> bool:
    """Check if a header line already contains a timestamp."""
    content = header_line[3:-3] if header_line.startswith('_**') and header_line.endswith('**_') else header_line
    # Check for timestamp pattern: (YYYY-MM-DDTHH:MM:SSZ)
    return bool(re.search(r'\(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\)', content))

def merge_timestamps(target_md: Optional[str] = None) -> None:
    """Insert timestamps into the markdown headers from the corresponding timestamps file.

    Args:
        target_md: Path to the markdown file to process (absolute or relative). 
                   If None, uses the most recent file.
    """
    if target_md:
        # Normalize path for cross-platform compatibility
        latest_md = os.path.abspath(os.path.normpath(target_md))
    else:
        latest_md = get_most_recent_md_file()
    if not latest_md:
        return

    # Ensure path is normalized (handles edge cases)
    latest_md = os.path.normpath(latest_md)
    timestamp_file = get_timestamp_file_for_md(latest_md)

    # Ensure timestamp file exists; do not early-return on empty as we may need to backfill
    os.makedirs(os.path.dirname(timestamp_file), exist_ok=True)
    if not os.path.exists(timestamp_file):
        Path(timestamp_file).touch()

    # Consider interactive only if there is at least one User block with content
    has_user_content = False
    in_user_block = False
    with open(latest_md, 'r', encoding='utf-8') as md_in:
        for raw_line in md_in:
            line = raw_line.rstrip('\n')
            if line.startswith('_**') and line.endswith('**_'):
                in_user_block = line.startswith('_**User')
                continue
            if in_user_block:
                if line.strip() and line.strip() != '---':
                    has_user_content = True
                    break

    # If no user content, don't merge timestamps; clear any noise
    if not has_user_content:
        # Clear any noise written by the watcher during startup/shutdown
        with open(timestamp_file, 'w'):
            pass
        return

    # Read markdown file and build header-to-snippet mapping
    md_lines = read_lines_text(latest_md)
    header_idxs = find_conversation_header_indices(md_lines)
    
    # Build mapping of header indices to their snippets (only headers with content)
    header_snippet_map = {}
    for h_idx in header_idxs:
        header_txt = md_lines[h_idx].strip() if h_idx < len(md_lines) else ''
        snippet = first_meaningful_line_after(md_lines, h_idx)
        if snippet and snippet != header_txt and snippet != '---':
            header_snippet_map[h_idx] = snippet

    # Read existing timestamps and build snippet-to-timestamp mapping
    with open(timestamp_file, 'r', encoding='utf-8') as f:
        existing = [ln.strip() for ln in f if ln.strip()]

    # Build mapping from snippet to timestamp
    timestamp_map = {}
    for ts_line in existing:
        parts = ts_line.split('|', 1)
        if len(parts) == 2:
            ts, snippet = parts[0].strip(), parts[1].strip()
            timestamp_map[snippet] = ts

    # Backfill missing timestamps for headers with content
    headers_with_content = [(h_idx, snippet) for h_idx, snippet in header_snippet_map.items()]
    missing_timestamps = []
    for h_idx, snippet in headers_with_content:
        if snippet not in timestamp_map:
            missing_timestamps.append((h_idx, snippet))
    
    if missing_timestamps:
        # Append missing timestamps with the corresponding snippets
        now_ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        with open(timestamp_file, 'a', encoding='utf-8') as f:
            for _, snippet in missing_timestamps:
                f.write(f"{now_ts}|{snippet}\n")
                timestamp_map[snippet] = now_ts

    # Process the markdown file, matching timestamps to headers by snippet
    temp_file = f"{latest_md}.tmp"

    with open(temp_file, 'w', encoding='utf-8') as md_out:
        for line_idx, line in enumerate(md_lines):
            # Check if this line is a User or Agent header
            if line.startswith('_**') and ('User' in line or 'Agent' in line) and line.endswith('**_'):
                # Check if header already has a timestamp
                if header_has_timestamp(line):
                    # Header already has timestamp, preserve it
                    md_out.write(line + '\n')
                elif line_idx in header_snippet_map:
                    # Header has content, try to find matching timestamp
                    snippet = header_snippet_map[line_idx]
                    if snippet in timestamp_map:
                        # Extract base role and add timestamp
                        base_role = extract_base_role(line)
                        ts_display = timestamp_map[snippet]
                        md_out.write(f"_**{base_role} ({ts_display})**_\n")
                    else:
                        # No timestamp found, write header as-is
                        md_out.write(line + '\n')
                else:
                    # Header without content, write as-is
                    md_out.write(line + '\n')
            else:
                md_out.write(line + '\n')

    # Replace original with updated file
    shutil.move(temp_file, latest_md)

def merge_all_timestamps() -> None:
    """Merge timestamps into all markdown files in the history directory.
    
    Works cross-platform by normalizing paths properly.
    """
    # Use os.path.join for cross-platform path construction
    hist_pattern = os.path.join(HIST_DIR, "*.md")
    md_files = glob.glob(hist_pattern)
    for md_path in md_files:
        # Normalize path before processing
        md_path_normalized = os.path.abspath(os.path.normpath(md_path))
        merge_timestamps(md_path_normalized)


def _try_kill_process(pid: int, sig: signal.Signals) -> None:
    """Try to kill process by process group, fallback to direct kill."""
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # Process group doesn't exist or no permission, try direct kill
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            # Process already gone
            pass

def stop_watcher() -> None:
    """Stop the background watcher started by this process and remove its pidfile."""
    pidfile = f"/tmp/specstory_watcher_{os.getpid()}"
    
    # Wait briefly for the pidfile to be populated if watcher is still starting
    deadline = time.time() + 2.0
    pid_text = None
    while time.time() < deadline:
        if os.path.exists(pidfile):
            with open(pidfile, 'r', encoding='utf-8') as f:
                pid_text = f.read().strip()
            if pid_text:
                break
        time.sleep(0.1)

    if not pid_text:
        # Nothing to stop; clean up any empty pidfile
        if os.path.exists(pidfile):
            os.remove(pidfile)
        return

    # Parse PID from JSON or plain text
    try:
        meta = json.loads(pid_text)
        pid = int(meta.get("pid"))
    except (json.JSONDecodeError, ValueError, KeyError):
        pid = int(pid_text)

    # Attempt graceful stop
    _try_kill_process(pid, signal.SIGTERM)

    # Check if process still exists after brief wait
    time.sleep(0.2)
    try:
        os.kill(pid, 0)  # Check if process exists
    except ProcessLookupError:
        # Process doesn't exist, clean up and return
        if os.path.exists(pidfile):
            os.remove(pidfile)
        return
    
    # Process still exists, force kill it
    _try_kill_process(pid, signal.SIGKILL)

    # Clean up pidfile
    if os.path.exists(pidfile):
        os.remove(pidfile)


def print_specstory_banner() -> None:
    """Print a banner indicating specstory recording is active."""
    # ANSI color codes
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    print(f"{CYAN}╭{'─' * 50}╮{RESET}")
    print(f"{CYAN}│{RESET} {GREEN}{BOLD}📝 SpecStory Recording Active{RESET}{'':>20}{CYAN}│{RESET}")
    print(f"{CYAN}│{RESET}    Session will be logged to .specstory/{'':>9}{CYAN}│{RESET}")
    print(f"{CYAN}╰{'─' * 50}╯{RESET}")
    print()


def main():
    """Entry point: start watcher, run real tool, then merge timestamps."""
    os.makedirs(TS_DIR, exist_ok=True)

    # Show indicator that specstory is active (only for 'run' commands)
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        print_specstory_banner()

    # Do not kill other watchers at startup to avoid stopping active sessions

    # Get timestamp of most recent existing file (if any)
    before_file = get_most_recent_md_file()
    before_mtime = None
    if before_file:
        before_mtime = os.path.getmtime(before_file)
    # Start watcher in background
    start_watcher(before_file, before_mtime)

    # Validate REAL path before running
    if not REAL:
        print("Error: Could not locate the real SpecStory binary.", file=sys.stderr)
        print("Please ensure specstory is installed (brew install specstoryai/tap/specstory)", file=sys.stderr)
        stop_watcher()
        sys.exit(1)
    if not os.path.exists(REAL):
        print(f"Error: SpecStory binary not found at: {REAL}", file=sys.stderr)
        stop_watcher()
        sys.exit(1)
    if not os.access(REAL, os.X_OK):
        print(f"Error: SpecStory binary is not executable: {REAL}", file=sys.stderr)
        stop_watcher()
        sys.exit(1)

    # Run SpecStory in foreground
    result = subprocess.run([REAL] + sys.argv[1:])
    status = result.returncode

    # Give watcher time to finish writing
    time.sleep(POLL_INTERVAL * 5)

    # Capture target md before stopping watcher
    pidfile = f"/tmp/specstory_watcher_{os.getpid()}"
    target_md = None
    if os.path.exists(pidfile):
        try:
            with open(pidfile, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if content:
                meta = json.loads(content)
                target_md = meta.get("target")
        except (json.JSONDecodeError, OSError, KeyError):
            # Pidfile may be corrupted or partially written
            pass

    # Stop the watcher before modifying the markdown so it doesn't record the merge
    stop_watcher()
    time.sleep(POLL_INTERVAL * 2)

    # Merge timestamps into all markdown files (multiple files can change per session)
    merge_all_timestamps()

    sys.exit(status)

if __name__ == "__main__":
    main()
