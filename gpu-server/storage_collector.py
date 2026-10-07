#!/usr/bin/env python3
"""Measure each user's folder on the data disks and publish node_exporter metrics.

Reads sizes only. Creates nothing, deletes nothing, changes no permissions.
Paths come from /etc/gpu-lab/storage.conf (one mount point per line) or argv.
"""
import argparse
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CONFIG = Path('/etc/gpu-lab/storage.conf')
SKIP = {'lost+found', '.Trash-1000', 'snap'}


def quote(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def mounts(arguments):
    if arguments:
        return [Path(p) for p in arguments]
    if CONFIG.exists():
        return [Path(line.strip()) for line in CONFIG.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.startswith('#')]
    return []


def folder_bytes(path):
    """du is far faster than walking the tree in Python on a multi-terabyte disk."""
    result = subprocess.run(['du', '-sb', '--one-file-system', str(path)],
                            capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return int(result.stdout.split('\t', 1)[0])


SYSTEM_LABEL = '(system)'


def login_accounts():
    return {entry.pw_name for entry in pwd.getpwall()
            if 1000 <= entry.pw_uid < 65534 and not entry.pw_shell.endswith(('nologin', 'false'))}


NETWORK_FILESYSTEMS = {'cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', 'fuse.sshfs'}


def is_network(path):
    """du over a mounted share is far too slow, so those disks report capacity only."""
    try:
        mounts = Path('/proc/mounts').read_text(encoding='utf-8').splitlines()
    except OSError:
        return False
    best = ''
    kind = ''
    for line in mounts:
        parts = line.split()
        if len(parts) >= 3 and str(path).startswith(parts[1]) and len(parts[1]) >= len(best):
            best, kind = parts[1], parts[2]
    return kind in NETWORK_FILESYSTEMS


def collect(paths):
    lines = ['# HELP lab_storage_total_bytes Capacity of a data disk.', '# TYPE lab_storage_total_bytes gauge',
             '# HELP lab_storage_used_bytes Space in use on a data disk.', '# TYPE lab_storage_used_bytes gauge',
             '# HELP lab_storage_free_bytes Space left on a data disk.', '# TYPE lab_storage_free_bytes gauge',
             '# HELP lab_storage_user_bytes Space used by one user folder.', '# TYPE lab_storage_user_bytes gauge']
    for mount in paths:
        if not mount.is_dir():
            continue
        usage = shutil.disk_usage(mount)
        label = f'mount="{quote(mount)}"'
        lines += [f'lab_storage_total_bytes{{{label}}} {usage.total}',
                  f'lab_storage_used_bytes{{{label}}} {usage.used}',
                  f'lab_storage_free_bytes{{{label}}} {usage.free}']
        if is_network(mount):
            continue
        people = login_accounts()
        other = 0
        for entry in sorted(os.scandir(mount), key=lambda e: e.name):
            if not entry.is_dir(follow_symlinks=False) or entry.name in SKIP:
                continue
            size = folder_bytes(entry.path)
            if size is None:
                continue
            if entry.name in people:
                lines.append(f'lab_storage_user_bytes{{{label},user="{quote(entry.name)}"}} {size}')
            else:
                # 윈도우 잔여 폴더나 시스템 디렉터리는 한 줄로 묶습니다.
                other += size
        if other:
            lines.append(f'lab_storage_user_bytes{{{label},user="{SYSTEM_LABEL}"}} {other}')
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mounts', nargs='*')
    parser.add_argument('--output', default='/var/lib/prometheus/node-exporter/gpu_lab_storage.prom')
    arguments = parser.parse_args()
    target = Path(arguments.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = mounts(arguments.mounts)
    try:
        lines = collect(paths)
        success = 1 if paths else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Storage collector failed: {type(exc).__name__}', flush=True)
        lines, success = [], 0
    lines += ['# TYPE lab_storage_collector_success gauge', f'lab_storage_collector_success {success}',
              '# TYPE lab_storage_collector_timestamp_seconds gauge',
              f'lab_storage_collector_timestamp_seconds {time.time()}']
    fd, name = tempfile.mkstemp(dir=target.parent, prefix='.storage-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines)+'\n')
        os.chmod(name, 0o644)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
