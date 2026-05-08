# -*- coding: utf-8 -*-
"""Generate functional_scheme_tip.drawio — orthogonal tree like Imperion reference."""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape


def box(cid: str, x: float, y: float, w: float, h: float, text: str, bold: bool = False) -> str:
    fs = ";fontStyle=1" if bold else ""
    return (
        f'        <mxCell id="{cid}" value="{escape(text)}" '
        f'style="rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000{fs}" '
        f'vertex="1" parent="1">\n'
        f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>\n'
        f"        </mxCell>\n"
    )


def edge(eid: str, src: str, tgt: str, points: list[tuple[float, float]]) -> str:
    sty = (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
        "html=1;strokeColor=#000000;endArrow=classic;endFill=1;"
    )
    pts = "".join(f'            <mxPoint x="{x}" y="{y}"/>\n' for x, y in points)
    inner = f"          <Array as=\"points\">\n{pts}          </Array>\n" if pts else ""
    return (
        f'        <mxCell id="{eid}" style="{sty}" edge="1" parent="1" source="{src}" target="{tgt}">\n'
        f'          <mxGeometry relative="1" as="geometry">\n{inner}'
        f"          </mxGeometry>\n"
        f"        </mxCell>\n"
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out = root / "functional_scheme_tip.drawio"

    y_top = 20
    y_role = 46
    h_role = 42
    w_role = 172
    cx_guest, cx_user, cx_mentor, cx_admin = 270, 640, 1080, 1580

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<mxfile host="app.diagrams.net" modified="2026-05-09T12:00:00.000Z" agent="MPTTools-gen" version="22.1.0">\n',
        '  <diagram id="tip-functional-v3" name="TIP Functional">\n',
        '    <mxGraphModel dx="2400" dy="1200" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="2200" pageHeight="2100" math="0" shadow="0">\n',
        "      <root>\n",
        '        <mxCell id="0"/>\n',
        '        <mxCell id="1" parent="0"/>\n',
        f'        <mxCell id="bb" value="" style="endArrow=none;html=1;strokeColor=#000000;strokeWidth=1;" edge="1" parent="1">\n'
        f'          <mxGeometry relative="1" as="geometry">\n'
        f'            <mxPoint x="35" y="{y_top + 10}" as="sourcePoint"/>\n'
        f'            <mxPoint x="2120" y="{y_top + 10}" as="targetPoint"/>\n'
        f"          </mxGeometry>\n        </mxCell>\n",
    ]

    # Roles
    parts.append(
        box("g_r", cx_guest - w_role / 2, y_role, w_role, h_role, "Неавторизованный пользователь", True)
    )
    parts.append(box("u_r", cx_user - w_role / 2, y_role, w_role, h_role, "Пользователь", True))
    parts.append(box("m_r", cx_mentor - w_role / 2, y_role, w_role, h_role, "Ментор (старший техник)", True))
    parts.append(box("a_r", cx_admin - w_role / 2, y_role, w_role, h_role, "Администратор", True))

    # Guest: boxes on the LEFT; role on the right of column; connect from LEFT side of role
    gw, gh = 188, 30
    gx = 28
    gy = 118
    glabs = ["Авторизация", "Регистрация", "Ознакомление с лендинг-страницей"]
    for j, lab in enumerate(glabs):
        h = 36 if j == 2 else gh
        parts.append(box(f"g{j + 1}", gx, gy, gw, h, lab))
        yy = gy + h / 2
        gy += h + 10
        x_role_left = cx_guest - w_role / 2
        bus = x_role_left - 25
        parts.append(
            edge(
                f"eg{j}",
                "g_r",
                f"g{j + 1}",
                [
                    (bus, y_role + h_role / 2 - 10 + j * 6),
                    (bus, yy),
                    (gx + gw, yy),
                ],
            )
        )

    # User hub + 5 categories in row
    hub_y = y_role + h_role + 22
    parts.append(box("u_hub", cx_user - 6, hub_y - 6, 12, 12, ""))
    parts.append(edge("eu_hub", "u_r", "u_hub", [(cx_user, y_role + h_role), (cx_user, hub_y)]))

    cat_w, cat_h = 112, 34
    cat_y = hub_y + 26
    cat_x0 = cx_user - (5 * cat_w + 4 * 12) / 2
    uc_ids = ["u_c1", "u_c2", "u_c3", "u_c4", "u_c5"]
    uc_titles = ["Каталог", "Заявки", "Профиль", "Уведомления", "Выход"]
    cat_xs: list[float] = []
    for i, (cid, title) in enumerate(zip(uc_ids, uc_titles, strict=True)):
        x = cat_x0 + i * (cat_w + 12)
        cat_xs.append(x)
        parts.append(box(cid, x, cat_y, cat_w, cat_h, title, True))
        cx_cat = x + cat_w / 2
        parts.append(edge(f"eu_c{i}", "u_hub", cid, [(cx_user, cat_y + cat_h / 2), (cx_cat, cat_y + cat_h / 2)]))

    def user_children(idx: int, labels: list[str]) -> None:
        x_cat = cat_xs[idx]
        stem = x_cat + cat_w + 6
        child_x = stem + 35
        y0 = cat_y + cat_h + 16
        for k, lab in enumerate(labels):
            cy = y0 + k * 30
            nid = f"{uc_ids[idx]}_k{k}"
            parts.append(box(nid, child_x, cy, 208, 28, lab))
            parts.append(
                edge(
                    f"e_{nid}",
                    uc_ids[idx],
                    nid,
                    [
                        (stem, cat_y + cat_h),
                        (stem, cy + 14),
                        (child_x, cy + 14),
                    ],
                )
            )

    user_children(0, ["Список позиций", "Поиск и фильтрация", "Карточка оборудования", "Просмотр QR-кода"])
    user_children(1, ["Создание заявки", "Список своих заявок", "Переписка по заявке", "Вложения и фото"])
    user_children(2, ["Настройки интерфейса и языка", "Личные сообщения", "Смена пароля"])
    user_children(3, ["Просмотр уведомлений"])
    user_children(4, ["Выход из аккаунта"])

    # Mentor
    m_hub_y = y_role + h_role + 22
    parts.append(box("m_hub", cx_mentor - 6, m_hub_y - 6, 12, 12, ""))
    parts.append(edge("em_hub", "m_r", "m_hub", [(cx_mentor, y_role + h_role), (cx_mentor, m_hub_y)]))
    m_cat_y = m_hub_y + 26
    m_cat_w = 128
    m_x0 = cx_mentor - (3 * m_cat_w + 2 * 14) / 2
    mc = [("m_c1", "Склад"), ("m_c2", "Заявки"), ("m_c3", "Поиск и отчёты")]
    m_xs: list[float] = []
    for i, (cid, title) in enumerate(mc):
        x = m_x0 + i * (m_cat_w + 14)
        m_xs.append(x)
        parts.append(box(cid, x, m_cat_y, m_cat_w, cat_h, title, True))
        parts.append(
            edge(
                f"em_c{i}",
                "m_hub",
                cid,
                [(cx_mentor, m_cat_y + cat_h / 2), (x + m_cat_w / 2, m_cat_y + cat_h / 2)],
            )
        )

    def ment_children(idx: int, labels: list[str]) -> None:
        x_cat = m_xs[idx]
        stem = x_cat + m_cat_w + 6
        child_x = stem + 30
        y0 = m_cat_y + cat_h + 16
        for k, lab in enumerate(labels):
            cy = y0 + k * 30
            nid = f"{mc[idx][0]}_k{k}"
            parts.append(box(nid, child_x, cy, 236, 28, lab))
            parts.append(
                edge(
                    f"e_{nid}",
                    mc[idx][0],
                    nid,
                    [
                        (stem, m_cat_y + cat_h),
                        (stem, cy + 14),
                        (child_x, cy + 14),
                    ],
                )
            )

    ment_children(
        0,
        [
            "Создание и изменение позиции",
            "Корректировка остатков",
            "Выдача и возврат",
            "«В ремонт» / разделение",
            "Списание расходников",
            "Периодическое списание",
        ],
    )
    ment_children(1, ["Одобрение и отклонение", "Смена статуса", "Переписка по заявке"])
    ment_children(2, ["Поиск по оборудованию", "Фильтрация заявок", "Экспорт и печать"])

    # Admin — two categories; stats column right; reports between
    a_hub_y = y_role + h_role + 22
    parts.append(box("a_hub", cx_admin - 6, a_hub_y - 6, 12, 12, ""))
    parts.append(edge("ea_hub", "a_r", "a_hub", [(cx_admin, y_role + h_role), (cx_admin, a_hub_y)]))
    a_cat_y = a_hub_y + 26
    parts.append(box("a_c1", cx_admin - 255, a_cat_y, 230, cat_h, "Статистика системы", True))
    parts.append(box("a_c2", cx_admin + 35, a_cat_y, 190, cat_h, "Отчётность", True))
    parts.append(
        edge(
            "ea_c1",
            "a_hub",
            "a_c1",
            [(cx_admin, a_cat_y + cat_h / 2), (cx_admin - 140, a_cat_y + cat_h / 2)],
        )
    )
    parts.append(
        edge(
            "ea_c2",
            "a_hub",
            "a_c2",
            [(cx_admin, a_cat_y + cat_h / 2), (cx_admin + 130, a_cat_y + cat_h / 2)],
        )
    )
    ax = cx_admin + 300
    stats = [
        "Количество пользователей",
        "Распределение по ролям",
        "Позиции оборудования",
        "Заявки по статусам",
        "Активность (дашборд)",
        "Prometheus / Grafana",
        "Отчёт качества",
        "Резервное копирование БД",
    ]
    y0 = a_cat_y + cat_h + 16
    stem1 = cx_admin - 255 + 230 + 6
    for k, lab in enumerate(stats):
        cy = y0 + k * 30
        nid = f"a_s{k}"
        parts.append(box(nid, ax, cy, 268, 28, lab))
        parts.append(
            edge(
                f"ea_s{k}",
                "a_c1",
                nid,
                [
                    (stem1, a_cat_y + cat_h),
                    (stem1, cy + 14),
                    (ax, cy + 14),
                ],
            )
        )
    rx = cx_admin - 40
    stem2 = cx_admin + 35 + 190 + 6
    y0_rep = y0 + len(stats) * 30 + 28
    for k, lab in enumerate(["Экспорт CSV и архивов", "Печать отчётов", "Журнал портала (CSV)"]):
        cy = y0_rep + k * 32
        nid = f"a_rp{k}"
        parts.append(box(nid, rx, cy, 252, 28, lab))
        parts.append(
            edge(
                f"ea_rp{k}",
                "a_c2",
                nid,
                [
                    (stem2, a_cat_y + cat_h),
                    (stem2, cy + 14),
                    (rx, cy + 14),
                ],
            )
        )

    parts.append("      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n")

    out.write_text("".join(parts), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
