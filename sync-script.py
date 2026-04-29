#!/usr/bin/env python3
"""
Синхронизирует .md файлы из исходных проектов в docs/
"""

import os
import shutil
import glob
from pathlib import Path

# Настройки: (исходный путь, целевая папка)
PROJECTS = {
    "/path/to/project-alpha": "docs/project-alpha",
    "/path/to/project-beta": "docs/project-beta",
    "/path/to/project-gamma/docs": "docs/project-gamma",
}

# Какие файлы копировать
PATTERNS = ["*.md", "docs/*.md", "README.md", "*.md"]


def sync():
    # Очищаем старые файлы (опционально)
    for target in PROJECTS.values():
        shutil.rmtree(target, ignore_errors=True)
        Path(target).mkdir(parents=True, exist_ok=True)

    # Копируем новые
    for src_root, dst_root in PROJECTS.items():
        for pattern in PATTERNS:
            for md_file in glob.glob(f"{src_root}/**/{pattern}", recursive=True):
                rel_path = os.path.relpath(md_file, src_root)
                dst_file = os.path.join(dst_root, rel_path)
                Path(dst_file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(md_file, dst_file)
                print(f"✅ {md_file} -> {dst_file}")


if __name__ == "__main__":
    sync()
    print("\n🔁 Готово. Запустите 'mkdocs serve' для просмотра")
