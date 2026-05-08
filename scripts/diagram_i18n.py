# -*- coding: utf-8 -*-
"""Перевод типов Django-полей в .dot для graph_models / диаграмм."""

TYPE_MAP = {
    "BigAutoField": "Ключ (автоинкремент)",
    "AutoField": "Ключ (автоинкремент)",
    "CharField": "Строка",
    "TextField": "Текст",
    "IntegerField": "Целое",
    "PositiveIntegerField": "Положительное целое",
    "PositiveSmallIntegerField": "Малое положительное целое",
    "BooleanField": "Логический",
    "DateField": "Дата",
    "DateTimeField": "Дата и время",
    "EmailField": "Email",
    "ImageField": "Изображение",
    "JSONField": "Структура JSON",
    "DecimalField": "Десятичное число",
    "ForeignKey": "Ссылка",
    "OneToOneField": "Связь 1:1",
}

TEXT_REPLACEMENTS = {
    " ID ": " Ключ ",
    ">ID<": ">Ключ<",
    " Id ": " Ключ ",
    "id": "ключ",
    "PositiveSmallЦелое": "Малое положительное целое",
    "PositiveЦелое": "Положительное целое",
    ">JSON</FONT>": ">Структура JSON</FONT>",
}


def apply_russian_types_to_dot(text: str) -> str:
    for en, ru in sorted(TYPE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        text = text.replace(en, ru)
    for src, dst in TEXT_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text


def read_dot_text(path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")
