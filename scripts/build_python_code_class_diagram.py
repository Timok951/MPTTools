# -*- coding: utf-8 -*-
"""
UML class diagram from *Python source* (not only Django ORM models).

django-extensions ``graph_models`` draws **Model** subclasses and DB fields.
This script runs **pyreverse** (Pylint) on app packages so you also get
views, forms, admin wrappers, API helpers, etc. — whatever is defined as
``class`` in those modules.

Note: **standalone functions** are not classes; they do not appear as boxes
on a class diagram. For those, use a call graph tool or inspect modules in the
package diagram (``packages_*.dot``).

Outputs under ``diagrams_python/`` (repo root):

  **All apps (overview)** — ``classes_TIP_python_code.*``, ``packages_*``
  (PNG may be downscaled by Graphviz when the bitmap would be huge; use PDF/SVG
  for the full graph, or per-app PNGs below.)

  **Per app** (readable high-res PNG at 300 DPI): ``classes_TIP_py_<app>.png``
  (+ matching ``.svg``, ``.pdf``, ``.dot``).

Run from repo root:

  python scripts/build_python_code_class_diagram.py

Requires: pylint (``pyreverse``), Graphviz ``dot`` on PATH.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Raster output; vector formats avoid Cairo “graph too large → shrink” for huge graphs.
PNG_DPI = "300"


def _pyreverse_exe() -> Path:
    scripts = Path(sys.executable).resolve().parent
    name = "pyreverse.exe" if sys.platform == "win32" else "pyreverse"
    exe = scripts / name
    if not exe.is_file():
        raise FileNotFoundError(
            f"pyreverse not found at {exe}. Install: pip install pylint"
        )
    return exe


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _render_dot(dot_file: Path, out_stem: Path) -> None:
    """Write ``out_stem.svg``, ``out_stem.pdf``, ``out_stem.png`` from ``dot_file``."""
    run(["dot", "-Tsvg", str(dot_file), "-o", str(out_stem.with_suffix(".svg"))], dot_file.parent)
    run(["dot", "-Tpdf", str(dot_file), "-o", str(out_stem.with_suffix(".pdf"))], dot_file.parent)
    run(
        [
            "dot",
            "-Tpng",
            str(dot_file),
            "-o",
            str(out_stem.with_suffix(".png")),
            f"-Gdpi={PNG_DPI}",
        ],
        dot_file.parent,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    tip = root / "TIP"
    out_dir = root / "diagrams_python"
    out_dir.mkdir(parents=True, exist_ok=True)

    pyreverse = _pyreverse_exe()
    apps = ["core", "assets", "operations", "audit", "inventory"]
    project_all = "TIP_python_code"

    run(
        [
            str(pyreverse),
            "-o",
            "dot",
            "-p",
            project_all,
            "-d",
            str(out_dir),
            "--ignore",
            "migrations",
            "-f",
            "ALL",
            "-m",
            "y",
            "--colorized",
            *apps,
        ],
        tip,
    )

    classes_dot = out_dir / f"classes_{project_all}.dot"
    packages_dot = out_dir / f"packages_{project_all}.dot"
    if classes_dot.is_file():
        _render_dot(classes_dot, out_dir / f"classes_{project_all}")
    if packages_dot.is_file():
        _render_dot(packages_dot, out_dir / f"packages_{project_all}")

    for app in apps:
        proj = f"TIP_py_{app}"
        run(
            [
                str(pyreverse),
                "-o",
                "dot",
                "-p",
                proj,
                "-d",
                str(out_dir),
                "--ignore",
                "migrations",
                "-f",
                "ALL",
                "-m",
                "y",
                "--colorized",
                app,
            ],
            tip,
        )
        app_classes = out_dir / f"classes_{proj}.dot"
        app_packages = out_dir / f"packages_{proj}.dot"
        if app_classes.is_file():
            _render_dot(app_classes, out_dir / f"classes_{proj}")
        if app_packages.is_file():
            _render_dot(app_packages, out_dir / f"packages_{proj}")

    print(out_dir / f"classes_{project_all}.pdf")
    print(out_dir / f"classes_{project_all}.svg")
    for app in apps:
        print(out_dir / f"classes_TIP_py_{app}.png")


if __name__ == "__main__":
    main()
