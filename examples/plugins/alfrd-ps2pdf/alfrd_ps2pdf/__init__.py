"""Reference Ghostscript converter, run by ALFRD's bounded worker process."""
from pathlib import Path
import subprocess

from alfrd.extensions import Converter, Plugin


def ps_to_pdf(src: Path, dest: Path, *, timeout: float) -> None:
    command = ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite"]
    if src.suffix.lower() == ".eps":
        command.append("-dEPSCrop")
    # The host supplies absolute paths; filenames cannot be mistaken for options.
    command += [f"-sOutputFile={dest}", "-f", str(src)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"Ghostscript exited with status {result.returncode}: {detail}")


plugin = Plugin(
    id="ps2pdf", version="0.1.0", title="PostScript to PDF",
    description="Convert PostScript and EPS files to PDF using Ghostscript.",
    requires_bin=["gs"],
    converters=[Converter(src=[".ps", ".eps"], to="pdf", run=ps_to_pdf)],
)
