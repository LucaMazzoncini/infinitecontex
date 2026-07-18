"""Direct, bounded, non-shell validation process runner."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping

from infinitecontex.tools.validation_models import StreamResult

_SECRET = re.compile(r"(?i)(authorization:\s*bearer\s+|api[_-]?key[=:]\s*|token[=:]\s*|password[=:]\s*)([^\s]+)")
_PRIVATE = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    output_limit_exceeded: bool
    stdout: StreamResult
    stderr: StreamResult


class _Collector:
    def __init__(self, limit: int, line_limit: int) -> None:
        self.limit = limit
        self.line_limit = line_limit
        self.data = bytearray()
        self.total = 0
        self.lines = 0
        self.digest = hashlib.sha256()
        self.truncated = False

    def consume(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            self.total += len(chunk)
            self.lines += chunk.count(b"\n")
            self.digest.update(chunk)
            remaining = max(0, self.limit - len(self.data))
            self.data.extend(chunk[:remaining])
            if len(chunk) > remaining or self.lines > self.line_limit:
                self.truncated = True

    def result(self) -> StreamResult:
        text = bytes(self.data).decode("utf-8", errors="replace")
        redacted = bool(_SECRET.search(text) or _PRIVATE.search(text))
        text = _SECRET.sub(r"\1[REDACTED]", text)
        text = _PRIVATE.sub("[REDACTED PRIVATE KEY]", text)
        return StreamResult(
            byte_count=self.total,
            line_count=self.lines,
            sha256=self.digest.hexdigest(),
            preview=text,
            truncated=self.truncated,
            redacted=redacted,
        )


class BoundedProcessRunner:
    def run(
        self,
        executable: str,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
        grace_seconds: int,
        stdout_limit: int,
        stderr_limit: int,
        line_limit: int,
    ) -> ProcessOutcome:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            [executable, *arguments],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=flags,
        )
        assert process.stdout is not None and process.stderr is not None
        out, err = _Collector(stdout_limit, line_limit), _Collector(stderr_limit, line_limit)
        threads = (
            threading.Thread(target=out.consume, args=(process.stdout,), daemon=True),
            threading.Thread(target=err.consume, args=(process.stderr,), daemon=True),
        )
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate(process, grace_seconds)
        finally:
            process.stdout.close()
            process.stderr.close()
            for thread in threads:
                thread.join(timeout=grace_seconds)
        stdout, stderr = out.result(), err.result()
        return ProcessOutcome(
            exit_code=process.returncode,
            timed_out=timed_out,
            cancelled=False,
            output_limit_exceeded=stdout.truncated or stderr.truncated,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes], grace: int) -> None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                getattr(os, "killpg")(getattr(os, "getpgid")(process.pid), signal.SIGTERM)
            process.wait(timeout=grace)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name != "nt":
                    try:
                        getattr(os, "killpg")(getattr(os, "getpgid")(process.pid), getattr(signal, "SIGKILL"))
                    except OSError:
                        process.kill()
                else:
                    process.kill()
                process.wait(timeout=grace)
