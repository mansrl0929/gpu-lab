#!/usr/bin/env python3
"""Read nvidia-smi and /proc; atomically publish node_exporter textfile metrics.

No SSH, process signaling, device permission changes, or control operations.
Run on the GPU host, with permission to read process UIDs.
"""
import argparse
import csv
import io
import os
import pwd
import subprocess
import tempfile
import time
from pathlib import Path

def quote(value):
    return str(value).replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')

def smi(query):
    result = subprocess.run(['nvidia-smi',query,'--format=csv,noheader,nounits'],capture_output=True,text=True,check=True,timeout=15)
    return [[field.strip() for field in row] for row in csv.reader(io.StringIO(result.stdout)) if row]

def collect():
    devices = {row[0]: row[1] for row in smi('--query-gpu=uuid,index')}
    rows = smi('--query-compute-apps=gpu_uuid,pid,used_memory')
    lines = ['# HELP lab_gpu_process_info Active compute processes and Linux users.', '# TYPE lab_gpu_process_info gauge',
             '# HELP lab_gpu_process_memory_mib VRAM held by each compute process.', '# TYPE lab_gpu_process_memory_mib gauge']
    memory_lines = []
    for uuid, pid, used_memory in rows:
        if uuid not in devices:
            raise ValueError('Unmapped GPU UUID (MIG requires explicit mapping)')
        try:
            uid = Path('/proc',str(int(pid))).stat().st_uid
            username = pwd.getpwuid(uid).pw_name
        except FileNotFoundError:
            continue  # Process completed between nvidia-smi and proc lookup.
        except (KeyError, PermissionError):
            raise ValueError('Cannot identify process owner')
        labels = f'gpu="{quote(devices[uuid])}",user="{quote(username)}",pid="{int(pid)}"'
        lines.append(f'lab_gpu_process_info{{{labels}}} 1')
        try:  # Some drivers report [N/A] or [Not Supported] for per-process memory.
            memory_lines.append(f'lab_gpu_process_memory_mib{{{labels}}} {int(used_memory)}')
        except ValueError:
            pass
    return lines + memory_lines

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',default='/var/lib/prometheus/node-exporter/gpu_processes.prom')
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        lines = collect()
        success = 1
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print(f'GPU process collector failed: {type(exc).__name__}',flush=True)
        lines, success = [], 0
    lines += ['# TYPE lab_gpu_process_collector_success gauge',f'lab_gpu_process_collector_success {success}',
              '# TYPE lab_gpu_process_collector_timestamp_seconds gauge',f'lab_gpu_process_collector_timestamp_seconds {time.time()}']
    fd, name = tempfile.mkstemp(dir=target.parent,prefix='.gpu-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            handle.write('\n'.join(lines)+'\n')
        os.chmod(name,0o644)
        os.replace(name,target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return 0 if success else 1

if __name__ == '__main__':
    raise SystemExit(main())
