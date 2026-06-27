"""
BEES Annotation Engine Wrapper
────────────────────────────────────────────────────────────────────
This module is the sole interface to the variant annotation engine
bundled with BEES. It abstracts the underlying implementation and
exposes a clean Python API used by the pipeline.

Do not call the engine binary directly — use annotate_vcf() below.
"""
import os
import subprocess
import sys

# ─── Internal paths ────────────────────────────────────────────────────────────
# The annotation engine binary lives in the bin/ directory alongside this
# wrapper. Its name and implementation are internal BEES components.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE   = os.path.join(_THIS_DIR, ".bees-core.jar")
_JAVA     = "java"


def _require_engine():
    """Raises RuntimeError if the engine binary is missing."""
    if not os.path.isfile(_ENGINE):
        raise RuntimeError(
            "BEES annotation engine not found. "
            "Ensure the distribution is complete and re-run setup_env.sh."
        )


def annotate_vcf(input_vcf: str, output_vcf: str, db_ser: str) -> None:
    """
    Runs transcript-level variant annotation on a VCF file.

    Parameters
    ----------
    input_vcf  : Path to the input VCF (may be gzipped).
    output_vcf : Destination path for the annotated VCF.
    db_ser     : Path to the serialized transcript annotation database
                 (bees_ensembl_hg38.ser or bees_refseq_hg38.ser).

    Raises
    ------
    RuntimeError    : Engine binary missing.
    subprocess.CalledProcessError : Annotation returned non-zero exit code.
    """
    _require_engine()

    cmd = [
        _JAVA, "-jar", _ENGINE,
        "annotate-vcf",
        "-i", input_vcf,
        "-o", output_vcf,
        "-d", db_ser,
        "--report-no-progress",
    ]

    # stderr is captured and discarded — engine banners/debug messages are
    # internal implementation details and must not surface to callers.
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd,
            output=None,
            stderr="Annotation engine returned a non-zero exit code. "
                   "Check that the input VCF and annotation database are valid."
        )


def version() -> str:
    """
    Returns the annotation engine version string in the form 'BEES Annotator vX.Y'.
    The underlying engine version is intentionally abstracted.
    """
    _require_engine()
    # We know the bundled engine version — hard-code to avoid exposing internals
    return "BEES Annotator v2.0"
