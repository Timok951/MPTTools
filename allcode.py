import os
from docx import Document
from docx.shared import Pt

PROJECT_DIR = "./"
OUTPUT_FILE = "full_code_report.docx"

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode",
    "venv", ".venv", "env", "migrations", "node_modules"
}

INCLUDE_EXTENSIONS = (".py", ".html", ".css", ".js", ".ts", ".json")


def get_file_info(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            content = "".join(lines)

        line_count = len(lines)
        size_kb = os.path.getsize(filepath) / 1024

        # описание из docstring
        description = "—"
        for line in lines[:10]:
            line = line.strip()
            if line.startswith('"""') or line.startswith("'''"):
                description = line.strip('"\' ')
                break

        return line_count, round(size_kb, 2), description, content

    except:
        return 0, 0, "Ошибка чтения", ""


files_data = []

# 🔍 Сбор файлов
for root, dirs, files in os.walk(PROJECT_DIR):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

    for file in files:
        if file.endswith(INCLUDE_EXTENSIONS):
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, PROJECT_DIR)

            lines, size, desc, content = get_file_info(full_path)

            files_data.append({
                "name": rel_path,
                "lines": lines,
                "size": size,
                "desc": desc,
                "content": content
            })

files_data.sort(key=lambda x: x["name"])

# 📄 Создание документа
doc = Document()

doc.add_heading("Таблица модулей", level=1)

# --- Таблица ---
table = doc.add_table(rows=1, cols=5)
table.style = 'Table Grid'

hdr_cells = table.rows[0].cells
hdr_cells[0].text = "No"
hdr_cells[1].text = "Наименование модуля"
hdr_cells[2].text = "Функциональное назначение"
hdr_cells[3].text = "Количество строк"
hdr_cells[4].text = "Размер (КБ)"

for i, file in enumerate(files_data, start=1):
    row_cells = table.add_row().cells
    row_cells[0].text = str(i)
    row_cells[1].text = file["name"]
    row_cells[2].text = file["desc"]
    row_cells[3].text = str(file["lines"])
    row_cells[4].text = str(file["size"])

# --- Код ---
doc.add_page_break()
doc.add_heading("Полный листинг кода", level=1)

for file in files_data:
    doc.add_heading(file["name"], level=2)

    paragraph = doc.add_paragraph()
    run = paragraph.add_run(file["content"])
    run.font.name = "Courier New"
    run.font.size = Pt(9)

# 💾 Сохранение
doc.save(OUTPUT_FILE)

print("Готово:", OUTPUT_FILE)