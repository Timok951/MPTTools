# -*- coding: utf-8 -*-
"""
Диаграмма классов предметной области (модели Django + наследование).

Сборка:
  python scripts/build_class_diagram_ru.py

Результат:
  class_diagram_tip_ru.dot / .svg / .pdf / .png (PNG 300 DPI)

UML по «сырым» Python-классам (pyreverse), при необходимости:
  pip install pylint
  pyreverse -o png -p TIP_domain -d . TIP/core/models.py TIP/assets/models.py TIP/operations/models.py TIP/audit/models.py
"""
from pathlib import Path
import subprocess

from diagram_i18n import apply_russian_types_to_dot, read_dot_text


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manage_py = root / "TIP" / "manage.py"
    dot_path = root / "class_diagram_tip_ru.dot"
    png_path = root / "class_diagram_tip_ru.png"

    run(
        [
            str(root / ".venv" / "Scripts" / "python"),
            str(manage_py),
            "graph_models",
            "core",
            "assets",
            "operations",
            "audit",
            "--group-models",
            "--verbose-names",
            "--inheritance",
            "--rankdir",
            "TB",
            "--hide-edge-labels",
            "--dot",
            "-o",
            str(dot_path),
        ],
        root,
    )

    text = apply_russian_types_to_dot(read_dot_text(dot_path))
    dot_path.write_text(text, encoding="utf-8")
    svg_path = dot_path.with_suffix(".svg")
    pdf_path = dot_path.with_suffix(".pdf")
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
