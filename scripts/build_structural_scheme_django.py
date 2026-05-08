# -*- coding: utf-8 -*-
"""
Generate a high-level structural scheme for the TIP Django project.

The diagram follows the flow:
  entrypoints -> routing -> web/api layers -> domain apps -> external services.

Outputs in repo root:
  structural_scheme_tip.dot
  structural_scheme_tip.svg
  structural_scheme_tip.pdf
  structural_scheme_tip.png

draw.io (diagrams.net): open `structural_scheme_tip.drawio` or `structural_scheme_tip.drawio.xml`
(valid XML with UTF-8 declaration; use File → Open in the diagrams.net app).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


DOT_TEMPLATE = r"""
digraph TIP_Structural_Scheme {
  graph [
    rankdir=TB,
    fontsize=11,
    fontname="Arial",
    splines=spline,
    nodesep=0.45,
    ranksep=0.60,
    bgcolor="white"
  ];
  node [
    shape=box,
    style="rounded,filled",
    fillcolor="white",
    color="black",
    fontname="Arial",
    fontsize=10
  ];
  edge [color="black", fontname="Arial", fontsize=9, arrowsize=0.7];

  external_services [label="External Services\n(PostgreSQL/SQLite, SMTP/Anymail,\nPrometheus/Grafana, Yandex Maps)"];
  manage [label="manage.py"];
  wsgi [label="TIP/wsgi.py"];
  asgi [label="TIP/asgi.py"];

  settings [label="TIP/settings.py\napps, middleware, DB, REST, email"];
  root_urls [label="TIP/urls.py"];
  inventory_urls [label="inventory/urls.py"];
  api_urls [label="TIP/api_urls.py"];
  portal_urls [label="inventory/portal_urls.py"];

  views [label="inventory/views.py\n(web pages + auth + reports)"];
  portal_views [label="inventory/portal_views.py\n(portal CRUD + procedures)"];
  api_viewsets [label="inventory/api/viewsets.py"];
  api_serializers [label="inventory/api/serializers.py"];
  api_permissions [label="inventory/api/permissions.py"];
  forms [label="inventory/forms.py\nportal_forms.py"];
  middleware [label="inventory/middleware.py"];

  core_models [label="core/models.py"];
  assets_models [label="assets/models.py"];
  operations_models [label="operations/models.py"];
  audit_models [label="audit/models.py"];
  service_modules [label="Service modules\n(backup, quality, periodic usage,\nnotifications)"];

  db [label="Database"];
  media [label="Media storage"];
  mail [label="Email transport"];
  metrics [label="Prometheus metrics"];

  { rank=same; manage; wsgi; asgi; }
  { rank=same; inventory_urls; api_urls; portal_urls; }
  { rank=same; views; api_viewsets; portal_views; }
  { rank=same; core_models; assets_models; operations_models; audit_models; }
  { rank=same; db; media; mail; metrics; }

  external_services -> settings;
  manage -> settings;
  wsgi -> settings;
  asgi -> settings;

  settings -> root_urls;
  settings -> middleware;

  root_urls -> inventory_urls;
  root_urls -> api_urls;
  inventory_urls -> portal_urls;

  inventory_urls -> views;
  inventory_urls -> forms;
  portal_urls -> portal_views;
  api_urls -> api_viewsets;
  api_viewsets -> api_serializers;
  api_viewsets -> api_permissions;

  views -> core_models;
  views -> assets_models;
  views -> operations_models;
  views -> audit_models;

  portal_views -> core_models;
  portal_views -> assets_models;
  portal_views -> operations_models;
  portal_views -> audit_models;
  api_serializers -> core_models;
  api_serializers -> assets_models;
  api_serializers -> operations_models;
  api_serializers -> audit_models;
  forms -> core_models;
  forms -> assets_models;
  forms -> operations_models;

  core_models -> db;
  assets_models -> db;
  operations_models -> db;
  audit_models -> db;
  assets_models -> media;
  service_modules -> db;
  service_modules -> mail;
  views -> mail;
  middleware -> metrics;
  root_urls -> metrics;

  settings -> service_modules [style=dashed];
}
"""


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dot_path = root / "structural_scheme_tip.dot"
    svg_path = root / "structural_scheme_tip.svg"
    pdf_path = root / "structural_scheme_tip.pdf"
    png_path = root / "structural_scheme_tip.png"

    dot_path.write_text(DOT_TEMPLATE.strip() + "\n", encoding="utf-8")

    run(["dot", "-Tsvg", str(dot_path), "-o", str(svg_path)], root)
    run(["dot", "-Tpdf", str(dot_path), "-o", str(pdf_path)], root)
    run(["dot", "-Tpng", str(dot_path), "-o", str(png_path), "-Gdpi=300"], root)

    print(dot_path)
    print(svg_path)
    print(pdf_path)
    print(png_path)


if __name__ == "__main__":
    main()
