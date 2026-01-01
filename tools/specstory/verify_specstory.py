#!/usr/bin/env python3
"""
SpecStory Verifier - Verifies that specstory files contain:
1. Both Claude and Cursor sessions
2. Timestamps for each User/Agent header in all .md files

Usage:
    python verify_specstory.py [project_root]
    
    If project_root is not provided, uses current working directory.

Cross-platform compatibility:
    This script works on Linux, macOS, and Windows by using os.path.join()
    and os.path.normpath() for all path operations, and handles different
    line endings (CRLF, LF, CR) in markdown files.
"""

import os
import sys
import glob
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict

# Use os.path.join for cross-platform path construction
HIST_DIR = os.path.join(".specstory", "history")
TS_DIR = os.path.join(".specstory", "timestamps")

# Patterns to identify session sources
CURSOR_PATTERN = re.compile(r'<!--\s*cursor\s+Session', re.IGNORECASE)
CLAUDE_PATTERN = re.compile(r'<!--\s*claude\s+Session', re.IGNORECASE)
# Fallback: check if file was created by CLI wrapper (Claude) vs extension (Cursor)
# CLI wrapper files might have different characteristics

# Timestamp pattern in headers: 
# - (YYYY-MM-DD HH:MMZ) or (YYYY-MM-DD HH:MM:SSZ)
# - (YYYY-MM-DDTHH:MM:SSZ)
# - May include model name: (YYYY-MM-DD HH:MMZ • model-name)
TIMESTAMP_PATTERN = re.compile(r'\(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?(?:\s+•\s+[^)]+)?\)')

# Header pattern: _**User**_ or _**Agent**_ or _**User (timestamp)**_ etc.
HEADER_PATTERN = re.compile(r'_\*\*((?:User|Agent|Assistant).*?)\*\*_')


def read_lines_text(path: str) -> List[str]:
    """Read a text file into normalized lines (LF) with UTF-8 decoding."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.split('\n')
    except (OSError, IOError, UnicodeDecodeError) as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return []


def identify_session_source(md_path: str, lines: List[str]) -> Optional[str]:
    """Identify if a markdown file is from Claude Code CLI or Cursor extension.
    
    Returns:
        'claude' if from Claude Code CLI
        'cursor' if from Cursor extension
        None if cannot determine
    """
    # Method 1: Check first few lines for explicit session comment
    for i, line in enumerate(lines[:10]):
        if CURSOR_PATTERN.search(line):
            return 'cursor'
        if CLAUDE_PATTERN.search(line):
            return 'claude'
    
    # Method 2: Check if corresponding timestamp file exists and has content
    # Claude CLI wrapper creates and uses timestamp files for merging
    timestamp_file = get_timestamp_file_for_md(md_path)
    has_timestamp_file = os.path.exists(timestamp_file) and os.path.getsize(timestamp_file) > 0
    
    # Method 3: Check if headers have merged timestamps
    # CLI wrapper merges timestamps into headers, extension might not
    headers_with_timestamps = 0
    headers_without_timestamps = 0
    
    for line in lines:
        match = HEADER_PATTERN.search(line)
        if match:
            if TIMESTAMP_PATTERN.search(line):
                headers_with_timestamps += 1
            else:
                headers_without_timestamps += 1
    
    # If file has timestamp file AND merged timestamps, likely from Claude CLI wrapper
    if has_timestamp_file and headers_with_timestamps > 0:
        # Additional check: if most/all headers have timestamps, it's likely CLI wrapper
        total_headers = headers_with_timestamps + headers_without_timestamps
        if total_headers > 0 and headers_with_timestamps / total_headers > 0.8:
            return 'claude'
    
    # If no timestamp file but has headers (some with timestamps), might be Cursor extension
    # Cursor extension might add timestamps differently or not merge them
    if not has_timestamp_file and headers_with_timestamps > 0:
        return 'cursor'
    
    # Method 4: Check filename patterns (heuristic)
    filename = os.path.basename(md_path).lower()
    if 'cursor' in filename:
        return 'cursor'
    if 'claude' in filename:
        return 'claude'
    
    # If we have headers but can't determine source, default based on timestamp file presence
    if has_timestamp_file:
        return 'claude'  # Timestamp files are created by CLI wrapper
    elif headers_with_timestamps > 0 or headers_without_timestamps > 0:
        return 'cursor'  # Extension might create files without timestamp files
    
    return None


def get_timestamp_file_for_md(md_path: str) -> str:
    """Given a markdown path, return the absolute timestamps file path alongside it.
    
    Works cross-platform (Linux, macOS, Windows) by normalizing paths properly.
    """
    # Normalize and resolve to absolute path (handles relative paths, .., ., symlinks, etc.)
    md_abs = os.path.abspath(os.path.normpath(md_path))
    history_dir = os.path.dirname(md_abs)
    specstory_dir = os.path.dirname(history_dir)
    ts_dir_for_file = os.path.join(specstory_dir, "timestamps")
    base = Path(md_abs).stem
    ts_file_path = os.path.join(ts_dir_for_file, f"{base}.timestamps")
    return os.path.normpath(ts_file_path)


def find_conversation_headers(lines: List[str]) -> List[Tuple[int, str, bool]]:
    """Find all User/Agent/Assistant headers and check if they have timestamps.
    
    Returns:
        List of (line_index, header_text, has_timestamp) tuples
    """
    headers = []
    for i, line in enumerate(lines):
        match = HEADER_PATTERN.search(line)
        if match:
            header_text = match.group(1)
            has_timestamp = bool(TIMESTAMP_PATTERN.search(line))
            headers.append((i, header_text, has_timestamp))
    return headers


def verify_file(md_path: str) -> Tuple[Optional[str], List[Tuple[int, str]], bool]:
    """Verify a single markdown file.
    
    Returns:
        (session_source, missing_timestamps, is_valid)
        where missing_timestamps is list of (line_num, header_text) for headers without timestamps
    """
    lines = read_lines_text(md_path)
    if not lines:
        return None, [], False
    
    session_source = identify_session_source(md_path, lines)
    headers = find_conversation_headers(lines)
    
    missing_timestamps = []
    for line_idx, header_text, has_timestamp in headers:
        if not has_timestamp:
            missing_timestamps.append((line_idx + 1, header_text))  # +1 for 1-based line numbers
    
    is_valid = len(missing_timestamps) == 0
    return session_source, missing_timestamps, is_valid


def find_specstory_directories(root_dir: str) -> List[str]:
    """Find all .specstory directories in the project tree."""
    specstory_dirs = []
    for root, dirs, files in os.walk(root_dir):
        # Check for .specstory BEFORE filtering (since it starts with '.')
        if '.specstory' in dirs:
            specstory_path = os.path.join(root, '.specstory')
            if os.path.isdir(specstory_path):
                specstory_dirs.append(specstory_path)
        
        # Skip hidden directories (including .specstory to avoid recursing) and common ignore patterns
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', 'venv', '__pycache__']]
    
    return specstory_dirs


def verify_project(project_root: Optional[str] = None) -> Dict:
    """Verify all specstory files in a project.
    
    Returns:
        Dictionary with verification results
    """
    if project_root is None:
        project_root = os.getcwd()
    
    project_root = os.path.abspath(os.path.normpath(project_root))
    
    # Find all .specstory directories
    specstory_dirs = find_specstory_directories(project_root)
    
    if not specstory_dirs:
        return {
            'error': f'No .specstory directories found in {project_root}',
            'has_claude': False,
            'has_cursor': False,
            'files': {},
            'valid': False
        }
    
    # Collect all markdown files from all .specstory/history directories
    all_md_files = []
    for specstory_dir in specstory_dirs:
        hist_dir = os.path.join(specstory_dir, 'history')
        if os.path.isdir(hist_dir):
            # Use os.path.join for cross-platform glob pattern
            md_pattern = os.path.join(hist_dir, '*.md')
            md_files = glob.glob(md_pattern)
            # Normalize all paths for cross-platform compatibility
            all_md_files.extend([os.path.normpath(f) for f in md_files])
    
    if not all_md_files:
        return {
            'error': f'No markdown files found in .specstory/history directories',
            'has_claude': False,
            'has_cursor': False,
            'files': {},
            'valid': False
        }
    
    # Verify each file
    files_info = {}
    session_sources: Set[str] = set()
    all_valid = True
    
    for md_path in all_md_files:
        # Normalize path before computing relative path (cross-platform)
        md_path_normalized = os.path.normpath(md_path)
        md_rel = os.path.relpath(md_path_normalized, project_root)
        session_source, missing_timestamps, is_valid = verify_file(md_path_normalized)
        
        files_info[md_rel] = {
            'source': session_source,
            'missing_timestamps': missing_timestamps,
            'valid': is_valid,
            'path': md_path
        }
        
        if session_source:
            session_sources.add(session_source)
        
        if not is_valid:
            all_valid = False
    
    has_claude = 'claude' in session_sources
    has_cursor = 'cursor' in session_sources
    
    return {
        'has_claude': has_claude,
        'has_cursor': has_cursor,
        'has_both': has_claude and has_cursor,
        'files': files_info,
        'valid': all_valid and has_claude and has_cursor,
        'total_files': len(all_md_files)
    }


def print_results(results: Dict) -> None:
    """Print verification results in a human-readable format."""
    if 'error' in results:
        print(f"Error: {results['error']}", file=sys.stderr)
        return
    
    print("=" * 70)
    print("SpecStory Verification Results")
    print("=" * 70)
    print()
    
    # Session type check
    print("Session Types:")
    print(f"  Claude Code CLI sessions: {results['has_claude']}")
    print(f"  Cursor extension sessions: {results['has_cursor']}")
    # print(f"  Both session types present: {results['has_both']}")
    print()
    
    # File-by-file results
    print(f"Files Checked: {results['total_files']}")
    
    valid_count = sum(1 for f in results['files'].values() if f['valid'])
    print(f"Files with all timestamps: {valid_count}/{results['total_files']}")
    
    # Only show failed files
    failed_files = [(md_rel, file_info) for md_rel, file_info in sorted(results['files'].items()) if not file_info['valid']]
    
    if failed_files:
        print()
        print(f"Failed Files ({len(failed_files)}):")
        print()
        
        for md_rel, file_info in failed_files:
            source_name = file_info['source'] or 'unknown'
            print(f"{md_rel} ({source_name})")
            
            missing = file_info['missing_timestamps']
            if missing:
                print(f"  Missing timestamps on {len(missing)} header(s):")
                for line_num, header_text in missing[:5]:  # Show first 5
                    header_preview = header_text[:50] + ('...' if len(header_text) > 50 else '')
                    print(f"    Line {line_num}: {header_preview}")
                if len(missing) > 5:
                    print(f"    ... and {len(missing) - 5} more")
            print()
    
    # Overall status
    print("=" * 70)
    if results['valid']:
        print("VERIFICATION PASSED")
        print("  - Both Claude and Cursor sessions found")
        print("  - All headers have timestamps")
    else:
        print("VERIFICATION FAILED")
        if not results['has_claude']:
            print("  - Missing Claude Code CLI sessions")
        if not results['has_cursor']:
            print("  - Missing Cursor extension sessions")
        if not results['has_both']:
            print("  - Missing one or both session types")
        
        if failed_files:
            print(f"  - {len(failed_files)} file(s) have headers without timestamps")
    print("=" * 70)


def main():
    """Main entry point."""
    project_root = sys.argv[1] if len(sys.argv) > 1 else None
    
    results = verify_project(project_root)
    print_results(results)
    
    # Exit with error code if verification failed
    sys.exit(0 if results.get('valid', False) else 1)


if __name__ == "__main__":
    main()

