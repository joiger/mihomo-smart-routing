#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aegis Security Audit & Git Diff Leak Scanner
Scans git diff (staged and unstaged) and tracked files to ensure no sensitive personal domains,
private tokens, or credentials are accidentally tracked.
"""

import sys
import os
import re
import subprocess

# Patterns that MUST NEVER be committed to the repository
FORBIDDEN_DOMAIN = "desiderius" + ".ru"
FORBIDDEN_PATTERNS = [
    (re.compile(rf"(?:[a-zA-Z0-9_-]+\.)?{re.escape(FORBIDDEN_DOMAIN)}", re.IGNORECASE), f"Personal domain leak (*.{FORBIDDEN_DOMAIN})"),
    (re.compile(r"ghp_[a-zA-Z0-9]{30,}", re.IGNORECASE), "GitHub Personal Access Token (classic)"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{30,}", re.IGNORECASE), "GitHub Fine-Grained Personal Access Token"),
    (re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA|PRIVATE)? ?KEY-----"), "Private cryptographic key"),
    (re.compile(r"(?:vless|trojan|hysteria2|hy2)://[a-zA-Z0-9_.-]{6,}@", re.IGNORECASE), "Live proxy credential with userinfo"),
]

# Patterns allowed in documentation as examples/placeholders
ALLOWED_SNIPPETS = [
    f"*.{FORBIDDEN_DOMAIN}",
    "ghp_`",
    "ghp_YOUR_TOKEN",
    "ghp_...",
    "example.com",
    "your_token_here",
]

def scan_text(text, filename=""):
    violations = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        # Ignore diff metadata lines
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        # For diffs, only check added lines
        if text.startswith("diff --git") and not line.startswith("+"):
            continue

        stripped_line = line.lstrip("+").strip()

        # Remove allowed placeholder snippets from test string before scanning
        test_line = stripped_line
        for allowed in ALLOWED_SNIPPETS:
            test_line = test_line.replace(allowed, "")

        for pattern, desc in FORBIDDEN_PATTERNS:
            match = pattern.search(test_line)
            if match:
                matched_val = match.group(0)
                # Redact matched sensitive token
                if len(matched_val) > 8:
                    redacted = matched_val[:3] + "..." + matched_val[-3:]
                else:
                    redacted = "***"
                violations.append((filename, idx, desc, redacted))
    return violations

def get_git_diff():
    diffs = []
    # 1. Staged diff
    p_staged = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, errors="replace")
    if p_staged.returncode == 0 and p_staged.stdout.strip():
        diffs.append(("staged changes", p_staged.stdout))
        
    # 2. Unstaged diff vs HEAD
    p_head = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, errors="replace")
    if p_head.returncode == 0 and p_head.stdout.strip():
        diffs.append(("working tree vs HEAD", p_head.stdout))

    return diffs

def scan_tracked_files():
    p = subprocess.run(["git", "ls-files"], capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        return []

    files = [f.strip() for f in p.stdout.splitlines() if f.strip()]
    violations = []
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            violations.extend(scan_text(content, filename=f))
        except Exception:
            pass
    return violations

def main():
    print("[*] Running Aegis Security Audit (Leak Scanner)...")
    violations = []

    # Check git diffs
    diffs = get_git_diff()
    for source_name, diff_content in diffs:
        violations.extend(scan_text(diff_content, filename=source_name))

    # Also scan all currently tracked files
    file_violations = scan_tracked_files()
    violations.extend(file_violations)

    if violations:
        print("[!] SECURITY ALERT: Sensitive data leaks detected!")
        for fname, lno, desc, redacted in violations:
            print(f"    - {fname}:{lno} -> {desc} (value: {redacted})")
        print("[!] Commit / build aborted to protect confidentiality.")
        return 1

    print("[OK] Zero leaks detected. All personal domains, tokens, and credentials are safe.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
