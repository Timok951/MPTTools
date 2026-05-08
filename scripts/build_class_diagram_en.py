# -*- coding: utf-8 -*-
"""
Full Django domain class diagram (models + inheritance + relations) in English.

Uses model and field *code names* and Django field types (CharField, ForeignKey, …),
not verbose_name (so labels stay English even when verbose_name is localized).

Outputs:
  class_diagram_tip_en.dot — Graphviz source
  class_diagram_tip_en.svg — vector (zoom without blur)
  class_diagram_tip_en.pdf — vector (good for printing)
  class_diagram_tip_en.png — raster, 300 DPI

Run from repo root:
  python scripts/build_class_diagram_en.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from diagram_i18n import read_dot_text


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manage_py = root / "TIP" / "manage.py"
    dot_path = root / "class_diagram_tip_en.dot"
    svg_path = root / "class_diagram_tip_en.svg"
    pdf_path = root / "class_diagram_tip_en.pdf"
    png_path = root / "class_diagram_tip_en.png"

    run(
        [
            sys.executable,
            str(manage_py),
            "graph_models",
            "core",
            "assets",
            "operations",
            "audit",
            "--group-models",
            "--inheritance",
            "--rankdir",
            "TB",
            "--display-field-choices",
            "--color-code-deletions",
            "--dot",
            "-o",
            str(dot_path),
        ],
        root,
    )

    # graph_models may write legacy encodings; normalize to UTF-8 for tooling
    dot_path.write_text(read_dot_text(dot_path), encoding="utf-8")

    run(["dot", "-Tsvg", str(dot_path), "-o", str(svg_path)], root)
    run(["dot", "-Tpdf", str(dot_path), "-o", str(pdf_path)], root)
    run(
        [
            "dot",
            "-Tpng",
            str(dot_path),
            "-o",
            str(png_path),
            "-Gdpi=300",
        ],
        root,
    )
    print(dot_path)
    print(svg_path)
    print(pdf_path)
    print(png_path)


if __name__ == "__main__":
    main()
