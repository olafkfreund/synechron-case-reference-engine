"""Headless LibreOffice runner for untrusted files. Imports nothing from render or diagrams."""
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

SOFFICE = "soffice"

# The template comes from marketing: never let it fetch linked images/remote content (the task can
# still reach the VPC and the ECS metadata endpoint) or run macros.
LOCKDOWN = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
</oor:items>
"""
# ~235 MB per soffice; the threadpool would otherwise allow ~40 at once
SLOTS = threading.BoundedSemaphore(2)


def convert(files: dict[str, bytes], fmt: str, timeout: float, wait: float,
            limit: int | None = None) -> dict[str, bytes]:
    """Convert every input with one soffice run; returns {input name: output bytes}. An input with no
    output (or one over `limit` bytes) is left out. Raises RuntimeError("busy" | "timeout" |
    "exit N"). Input names need distinct stems: the output is <stem>.<fmt>. Own temp dir and
    profile per call: concurrent soffice runs share (and lock) one profile otherwise."""
    if not SLOTS.acquire(timeout=wait):
        raise RuntimeError("busy")
    tmp = tempfile.mkdtemp(prefix="office-")
    try:
        srcs = []
        for name, data in files.items():
            (Path(tmp) / name).write_bytes(data)
            srcs.append(str(Path(tmp) / name))
        profile = Path(tmp) / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(LOCKDOWN)
        # new session so a timeout can kill soffice's child processes too; minimal env: no DB or AWS secrets
        proc = subprocess.Popen(
            [SOFFICE, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
             "--convert-to", fmt, "--outdir", str(Path(tmp) / "out"), *srcs],
            env={"PATH": os.environ.get("PATH", ""), "HOME": tmp}, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("timeout") from None
        if code:
            raise RuntimeError(f"exit {code}")
        result = {}
        for name in files:
            out = Path(tmp) / "out" / f"{Path(name).stem}.{fmt}"
            if out.is_file():
                with out.open("rb") as f:
                    raw = f.read(None if limit is None else limit + 1)
                if limit is None or len(raw) <= limit:
                    result[name] = raw
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        SLOTS.release()
