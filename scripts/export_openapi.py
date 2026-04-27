"""Выгружает свежую OpenAPI-схему в docs/openapi.json."""

import json
import os
import sys

# Корень проекта — родитель папки scripts/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), encoding="utf-8-sig")

from app.api.main import app  # noqa: E402  (импорт после path-патча)

OUTPUT_PATH = os.path.join(PROJECT_ROOT, "docs", "openapi.json")

schema = app.openapi()
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(schema, f, ensure_ascii=False, indent=2)

print(f"OpenAPI schema saved: {OUTPUT_PATH}")
print(f"Endpoints: {len(schema.get('paths', {}))}")
