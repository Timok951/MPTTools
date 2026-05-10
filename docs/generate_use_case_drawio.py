#!/usr/bin/env python3
"""Генерация многостраничной диаграммы прецедентов Draw.io из структуры PlantUML (docs/use_case_mpt_tools.puml)."""

from __future__ import annotations

from pathlib import Path

import html
import uuid


def nid() -> str:
    return uuid.uuid4().hex[:12]


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def actor_xml(cid: str, label: str, x: float, y: float) -> str:
    return f"""        <mxCell id="{cid}" value="{esc(label)}" style="shape=umlActor;verticalLabelPosition=bottom;html=1;verticalAlign=top;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="{x}" y="{y}" width="36" height="70" as="geometry" />
        </mxCell>"""


def uc_xml(cid: str, label: str, x: float, y: float, w: float = 200, h: float = 44, dashed: bool = False) -> str:
    d = ";dashed=1;fontStyle=2" if dashed else ""
    return f"""        <mxCell id="{cid}" value="{esc(label)}" style="ellipse;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;fontSize=10{d}" vertex="1" parent="1">
          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />
        </mxCell>"""


def edge_xml(eid: str, src: str, tgt: str, extend: bool = False) -> str:
    if extend:
        st = 'endArrow=open;dashed=1;html=1;dashPattern=1 4;strokeColor=#000000;exitX=1;exitY=0.5;entryX=0;entryY=0.5;'
    else:
        st = 'endArrow=none;html=1;strokeColor=#000000;'
    return f"""        <mxCell id="{eid}" style="{st}" edge="1" parent="1" source="{src}" target="{tgt}">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>"""


def gen_arrow(eid: str, src: str, tgt: str) -> str:
    return f"""        <mxCell id="{eid}" style="endArrow=block;endFill=0;html=1;endSize=10;strokeColor=#000000;" edge="1" parent="1" source="{src}" target="{tgt}">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>"""


def note_xml(cid: str, text: str, x: float, y: float, w: float, h: float) -> str:
    return f"""        <mxCell id="{cid}" value="{esc(text)}" style="shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;fontSize=10;align=left;spacingLeft=6;" vertex="1" parent="1">
          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />
        </mxCell>"""


def diagram_block(name: str, content: str) -> str:
    did = nid()
    return f"""  <diagram id="{did}" name="{esc(name)}">
    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="1180" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
{content}
      </root>
    </mxGraphModel>
  </diagram>
"""


def page_roles() -> str:
    g, u, t, l1, s, a, sa = "p0_g", "p0_u", "p0_t", "p0_l1", "p0_s", "p0_a", "p0_sa"
    ext = "p0_api"
    cells = [
        f"""        <mxCell id="p0_title" value="&lt;b&gt;Роли и обобщение&lt;/b&gt;&lt;br&gt;&lt;span style=&quot;font-size:10px&quot;&gt;MPT Tools (TIP)&lt;/span&gt;" style="text;html=1;strokeColor=none;fillColor=none;align=left;fontColor=#000000;" vertex="1" parent="1">
          <mxGeometry x="40" y="20" width="280" height="44" as="geometry" />
        </mxCell>""",
        actor_xml(g, "Гость", 60, 120),
        actor_xml(u, "Пользователь\n(аутентиф.)", 60, 240),
        actor_xml(t, "Техник", 60, 380),
        actor_xml(l1, "Поддержка\n1-й линии", 60, 500),
        actor_xml(s, "Старший техник", 60, 620),
        actor_xml(a, "Администратор", 60, 740),
        actor_xml(sa, "Системный\nадминистратор", 60, 820),
        actor_xml(ext, "Внешняя система\n(REST)", 320, 440),
    ]
    edges = [
        gen_arrow("p0_e1", u, g),
        gen_arrow("p0_e2", t, u),
        gen_arrow("p0_e3", l1, u),
        gen_arrow("p0_e4", a, u),
        gen_arrow("p0_e5", s, t),
        gen_arrow("p0_e6", sa, a),
        gen_arrow("p0_e7", sa, s),
    ]
    cells.append(
        note_xml(
            "p0_n",
            "Полномочия по прецедентам: TIP/inventory/authz.py.\n"
            "Дальнейшие страницы — по пакетам PlantUML (меньше пересечений линий).",
            320,
            120,
            380,
            100,
        )
    )
    return "\n".join(cells + edges)


def simplify_page_auth_profile() -> str:
    """Without botched swim parenting — flat layout."""
    boundary = "p1_bd"
    lines = [
        f"""        <mxCell id="{boundary}" value="&lt;b&gt;01 — Вход и профиль&lt;/b&gt; (пакеты PlantUML)" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="780" height="560" as="geometry" />
        </mxCell>""",
        actor_xml("p1_guest", "Гость", 52, 100),
        actor_xml("p1_user", "Пользователь", 52, 280),
        actor_xml("p1_admin", "Администратор", 52, 460),
    ]
    pairs = [
        ("Зарегистрироваться", False),
        ("Авторизоваться", False),
        ("Запросить восстановление пароля", False),
        ("Подтвердить сброс пароля по коду", True),
        ("Выйти из системы", False),
        ("Изменить персональные настройки", False),
        ("Сменить язык (i18n)", True),
        ("Просмотреть центр уведомлений", False),
        ("Обмен личными сообщениями", False),
        ("Модерировать переписку", True),
        ("Раздел «О проекте»", False),
        ("Просмотреть описание REST API", False),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 210, 88, 250
    for i, (lab, dashed) in enumerate(pairs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 54, w=cw - 18, h=48, dashed=dashed))
    e = []
    e.append(edge_xml(nid(), "p1_guest", uc_ids["Зарегистрироваться"]))
    e.append(edge_xml(nid(), "p1_guest", uc_ids["Авторизоваться"]))
    e.append(edge_xml(nid(), "p1_guest", uc_ids["Запросить восстановление пароля"]))
    for lab in [
        "Выйти из системы",
        "Изменить персональные настройки",
        "Сменить язык (i18n)",
        "Просмотреть центр уведомлений",
        "Обмен личными сообщениями",
        "Раздел «О проекте»",
        "Просмотреть описание REST API",
        "Подтвердить сброс пароля по коду",
    ]:
        e.append(edge_xml(nid(), "p1_user", uc_ids[lab]))
    e.append(edge_xml(nid(), "p1_admin", uc_ids["Модерировать переписку"]))
    e.append(edge_xml(nid(), uc_ids["Подтвердить сброс пароля по коду"], uc_ids["Запросить восстановление пароля"], extend=True))
    e.append(edge_xml(nid(), uc_ids["Сменить язык (i18n)"], uc_ids["Изменить персональные настройки"], extend=True))
    e.append(edge_xml(nid(), uc_ids["Модерировать переписку"], uc_ids["Обмен личными сообщениями"], extend=True))
    lines.append(note_xml("p1_n", "Источник: docs/use_case_mpt_tools.puml", 520, 480, 260, 52))
    return "\n".join(lines + e)


def page_equipment() -> str:
    bd = "p2_bd"
    labs = [
        ("Публичная карточка оборудования", False),
        ("Каталог оборудования", False),
        ("Фильтры и сортировка каталога", True),
        ("Карточка оборудования (детально)", False),
        ("Экспорт каталога в CSV", False),
        ("Печать каталога / PDF списка", False),
        ("QR-код единицы", False),
        ("Выделить количество в ремонт", False),
        ("Изменить статусы на складе", False),
        ("Реестр рабочих мест", False),
        ("Реестр кабинетов", False),
        ("Глобальный поиск", False),
        ("Показать / скрыть удалённые", False),
        ("Переход к пополнению (заявка)", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;02 — Оборудование и справочники&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="820" height="520" as="geometry" />
        </mxCell>""",
        actor_xml("p2_guest", "Гость", 52, 120),
        actor_xml("p2_tech", "Техник", 52, 260),
        actor_xml("p2_senior", "Старший техник", 52, 420),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 200, 88, 280
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 52, w=cw - 20, h=46, dashed=dashed))
    e = []
    e.append(edge_xml(nid(), "p2_guest", uc_ids["Публичная карточка оборудования"]))
    for lab in [
        "Каталог оборудования",
        "Фильтры и сортировка каталога",
        "Карточка оборудования (детально)",
        "Экспорт каталога в CSV",
        "Печать каталога / PDF списка",
        "QR-код единицы",
        "Реестр рабочих мест",
        "Реестр кабинетов",
        "Глобальный поиск",
        "Показать / скрыть удалённые",
        "Переход к пополнению (заявка)",
    ]:
        e.append(edge_xml(nid(), "p2_tech", uc_ids[lab]))
    for lab in ["Выделить количество в ремонт", "Изменить статусы на складе"]:
        e.append(edge_xml(nid(), "p2_senior", uc_ids[lab]))
    e.append(edge_xml(nid(), uc_ids["Фильтры и сортировка каталога"], uc_ids["Каталог оборудования"], extend=True))
    lines.append(
        note_xml(
            "p2_n",
            "Поддержка 1-й линии: те же прецеденты, что у техника (каталог, справочники), см. authz.",
            520,
            460,
            300,
            72,
        )
    )
    return "\n".join(lines + e)


def page_requests() -> str:
    bd = "p3_bd"
    labs = [
        ("Создать заявку", False),
        ("Журнал заявок", False),
        ("Карточка заявки и переписка", False),
        ("Изменить статус заявки", False),
        ("Зафиксировать состояние оборудования", False),
        ("Экспорт заявок в CSV", False),
        ("Печать журнала заявок", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;03 — Заявки&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="760" height="400" as="geometry" />
        </mxCell>""",
        actor_xml("p3_tech", "Техник", 52, 120),
        actor_xml("p3_l1", "Поддержка 1-й линии", 52, 260),
        actor_xml("p3_admin", "Администратор", 52, 400),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 220, 90, 260
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 54, w=cw - 18, h=48, dashed=dashed))
    e = []
    for lab in ["Создать заявку", "Журнал заявок", "Карточка заявки и переписка", "Экспорт заявок в CSV", "Печать журнала заявок"]:
        e.append(edge_xml(nid(), "p3_tech", uc_ids[lab]))
    for lab in ["Изменить статус заявки", "Зафиксировать состояние оборудования"]:
        e.append(edge_xml(nid(), "p3_l1", uc_ids[lab]))
    e.append(edge_xml(nid(), "p3_l1", uc_ids["Создать заявку"]))
    e.append(edge_xml(nid(), "p3_l1", uc_ids["Журнал заявок"]))
    e.append(edge_xml(nid(), "p3_l1", uc_ids["Карточка заявки и переписка"]))
    e.append(edge_xml(nid(), "p3_l1", uc_ids["Экспорт заявок в CSV"]))
    e.append(edge_xml(nid(), "p3_l1", uc_ids["Печать журнала заявок"]))
    e.append(edge_xml(nid(), "p3_admin", uc_ids["Журнал заявок"]))
    lines.append(note_xml("p3_n", "Старший техник: обработка заявок + функции техника (не все линии продублированы).", 480, 300, 290, 72))
    return "\n".join(lines + e)


def page_checkout() -> str:
    bd = "p4_bd"
    labs = [
        ("Журнал выдач", False),
        ("Оформить выдачу", False),
        ("Оформить возврат", False),
        ("Зарегистрировать расход материала", False),
        ("Журнал расхода", False),
        ("Экспорт журнала расхода (CSV)", False),
        ("Печать журнала расхода", False),
        ("Корректировка остатков", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;04 — Выдачи и расходные материалы&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="720" height="420" as="geometry" />
        </mxCell>""",
        actor_xml("p4_senior", "Старший техник", 52, 160),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 200, 88, 260
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 54, w=cw - 18, h=48, dashed=dashed))
    e = [edge_xml(nid(), "p4_senior", uc_ids[lab]) for lab, _ in labs]
    return "\n".join(lines + e)


def page_analytics() -> str:
    bd = "p5_bd"
    labs = [
        ("Дашборд аналитики", False),
        ("Экспорт аналитики (ZIP CSV)", True),
        ("Экспорт графиков (PDF)", True),
        ("Печать страницы аналитики", True),
        ("Отчёты по номенклатуре", False),
        ("Экспорт отчёта по типу", False),
        ("Печать отчётов", False),
        ("Хронология изменений (аудит)", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;05 — Аналитика и отчёты&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="820" height="440" as="geometry" />
        </mxCell>""",
        actor_xml("p5_l1", "Поддержка 1-й линии", 52, 120),
        actor_xml("p5_senior", "Старший техник", 52, 260),
        actor_xml("p5_admin", "Администратор", 52, 400),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 210, 88, 290
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 54, w=cw - 18, h=48, dashed=dashed))
    e = []
    for lab in ["Дашборд аналитики", "Экспорт аналитики (ZIP CSV)", "Экспорт графиков (PDF)", "Печать страницы аналитики"]:
        e.append(edge_xml(nid(), "p5_l1", uc_ids[lab]))
    for lab in ["Отчёты по номенклатуре", "Экспорт отчёта по типу", "Печать отчётов"]:
        e.append(edge_xml(nid(), "p5_senior", uc_ids[lab]))
    for lab in [
        "Дашборд аналитики",
        "Экспорт аналитики (ZIP CSV)",
        "Экспорт графиков (PDF)",
        "Печать страницы аналитики",
        "Хронология изменений (аудит)",
    ]:
        e.append(edge_xml(nid(), "p5_admin", uc_ids[lab]))
    for ext_lab in ["Экспорт аналитики (ZIP CSV)", "Экспорт графиков (PDF)", "Печать страницы аналитики"]:
        e.append(edge_xml(nid(), uc_ids[ext_lab], uc_ids["Дашборд аналитики"], extend=True))
    lines.append(note_xml("p5_n", "Системный администратор включает эти полномочия + см. стр. API.", 520, 360, 300, 56))
    return "\n".join(lines + e)


def page_admin_data() -> str:
    bd = "p6_bd"
    labs = [
        ("Портал: списки сущностей", False),
        ("Портал: CRUD / восстановление", True),
        ("Журнал действий портала", False),
        ("Запуск процедур портала", False),
        ("Назначение групп и ролей", False),
        ("Отчёт качества данных", False),
        ("Инструменты данных (обзор БД)", False),
        ("Резервная копия JSON", False),
        ("Импорт из JSON", False),
        ("Дамп PostgreSQL", False),
        ("Восстановление из дампа PG", False),
        ("Выгрузка SQLite", True),
        ("Экспорт журнала портала CSV", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;06 — Администрирование и данные&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="840" height="560" as="geometry" />
        </mxCell>""",
        actor_xml("p6_admin", "Администратор", 52, 180),
    ]
    uc_ids: dict[str, str] = {}
    bx, by, cw = 200, 80, 290
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        row, col = divmod(i, 2)
        lines.append(uc_xml(cid, lab, bx + col * cw, by + row * 52, w=cw - 18, h=46, dashed=dashed))
    e = [edge_xml(nid(), "p6_admin", uc_ids[lab]) for lab, _ in labs]
    e.append(edge_xml(nid(), uc_ids["Портал: CRUD / восстановление"], uc_ids["Портал: списки сущностей"], extend=True))
    return "\n".join(lines + e)


def page_api() -> str:
    bd = "p7_bd"
    labs = [
        ("Управление API-токеном", False),
        ("Доступ через REST API", False),
        ("OpenAPI-схема", False),
        ("Метрики Prometheus", True),
        ("Панель Django admin", False),
    ]
    lines = [
        f"""        <mxCell id="{bd}" value="&lt;b&gt;07 — API и интеграции&lt;/b&gt;" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;dashed=1;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;" vertex="1" parent="1">
          <mxGeometry x="30" y="40" width="760" height="360" as="geometry" />
        </mxCell>""",
        actor_xml("p7_admin", "Администратор", 52, 140),
        actor_xml("p7_sys", "Системный администратор", 52, 260),
        actor_xml("p7_ext", "Внешняя система", 52, 420),
    ]
    uc_ids: dict[str, str] = {}
    bx, by = 260, 100
    for i, (lab, dashed) in enumerate(labs):
        cid = nid()
        uc_ids[lab] = cid
        lines.append(uc_xml(cid, lab, bx + (i % 2) * 280, by + (i // 2) * 56, w=258, h=48, dashed=dashed))
    e = [
        edge_xml(nid(), "p7_admin", uc_ids["Управление API-токеном"]),
        edge_xml(nid(), "p7_sys", uc_ids["Доступ через REST API"]),
        edge_xml(nid(), "p7_sys", uc_ids["OpenAPI-схема"]),
        edge_xml(nid(), "p7_sys", uc_ids["Метрики Prometheus"]),
        edge_xml(nid(), "p7_sys", uc_ids["Панель Django admin"]),
        edge_xml(nid(), "p7_sys", uc_ids["Управление API-токеном"]),
        edge_xml(nid(), "p7_ext", uc_ids["Доступ через REST API"]),
        edge_xml(nid(), "p7_ext", uc_ids["OpenAPI-схема"]),
        edge_xml(nid(), "p7_ext", uc_ids["Метрики Prometheus"]),
    ]
    lines.append(note_xml("p7_n", "Prometheus для внешнего клиента — опционально (как в PlantUML <<optional>>).", 480, 260, 280, 56))
    return "\n".join(lines + e)


def main() -> None:
    out_path = Path(__file__).resolve().parent / "diagram_use_case_mpt_tools.drawio"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mxfile host="app.diagrams.net" modified="2026-05-09T00:00:00.000Z" agent="MPTTools-generator" version="22.1.0">',
        diagram_block("00 Роли и обобщение", page_roles()),
        diagram_block("01 Вход и профиль", simplify_page_auth_profile()),
        diagram_block("02 Оборудование", page_equipment()),
        diagram_block("03 Заявки", page_requests()),
        diagram_block("04 Выдачи и расход", page_checkout()),
        diagram_block("05 Аналитика и отчёты", page_analytics()),
        diagram_block("06 Портал и данные", page_admin_data()),
        diagram_block("07 API", page_api()),
        "</mxfile>",
    ]
    text = "\n".join(parts)
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
