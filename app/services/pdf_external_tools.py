from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_pdf_file(pdf_path: Path) -> Path:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found:\n{pdf_path}")

    backup_path = pdf_path.with_name(f"{pdf_path.name}.backup-{timestamp_for_filename()}.pdf")
    shutil.copy2(pdf_path, backup_path)
    return backup_path


def resolve_qpdf_executable(qpdf_bin_dir: str) -> Path | None:
    configured_dir = Path(qpdf_bin_dir.strip()) if qpdf_bin_dir and qpdf_bin_dir.strip() else None
    if configured_dir is not None:
        configured_exe = configured_dir / "qpdf.exe"
        if configured_exe.exists():
            return configured_exe

    discovered = shutil.which("qpdf")
    if discovered:
        return Path(discovered)
    return None


def run_qpdf_check(pdf_path: Path, qpdf_bin_dir: str, fail_on_error: bool = False) -> Path:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found:\n{pdf_path}")

    qpdf_exe = resolve_qpdf_executable(qpdf_bin_dir)
    if qpdf_exe is None:
        raise RuntimeError(
            "qpdf.exe was not found.\n\n"
            "Set the qpdf bin directory in File > Settings, or add qpdf to PATH."
        )

    report_path = pdf_path.with_name(f"{pdf_path.name}.qpdf-{timestamp_for_filename()}.txt")
    command = [str(qpdf_exe), "--check", str(pdf_path)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    report_lines = [
        f"PDF: {pdf_path}",
        f"Command: {' '.join(command)}",
        f"Exit code: {completed.returncode}",
        "",
        "STDOUT:",
        stdout.rstrip(),
        "",
        "STDERR:",
        stderr.rstrip(),
        "",
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    if fail_on_error and completed.returncode != 0:
        raise RuntimeError(
            "qpdf check failed.\n\n"
            f"Report:\n{report_path}\n\n"
            f"Exit code: {completed.returncode}"
        )
    return report_path


def rewrite_pdf_with_qpdf(pdf_path: Path, qpdf_bin_dir: str) -> Path:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found:\n{pdf_path}")

    qpdf_exe = resolve_qpdf_executable(qpdf_bin_dir)
    if qpdf_exe is None:
        raise RuntimeError(
            "qpdf.exe was not found.\n\n"
            "Set the qpdf bin directory in File > Settings, or add qpdf to PATH."
        )

    timestamp = timestamp_for_filename()
    rewritten_path = pdf_path.with_name(f"{pdf_path.name}.qpdf-rewrite-{timestamp}.pdf")
    report_path = pdf_path.with_name(f"{pdf_path.name}.qpdf-rewrite-{timestamp}.txt")
    command = [str(qpdf_exe), str(pdf_path), str(rewritten_path)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    report_lines = [
        f"PDF: {pdf_path}",
        f"Output: {rewritten_path}",
        f"Command: {' '.join(command)}",
        f"Exit code: {completed.returncode}",
        "",
        "STDOUT:",
        stdout.rstrip(),
        "",
        "STDERR:",
        stderr.rstrip(),
        "",
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    if completed.returncode != 0:
        raise RuntimeError(
            "qpdf rewrite failed.\n\n"
            f"Report:\n{report_path}\n\n"
            f"Exit code: {completed.returncode}"
        )
    if not rewritten_path.exists() or rewritten_path.stat().st_size <= 0:
        raise RuntimeError(f"qpdf rewrite did not create a valid output PDF:\n{rewritten_path}")
    return rewritten_path
