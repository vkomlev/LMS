# -*- coding: utf-8 -*-
"""tsk-761: разбор плана и перезапись остаточных ссылок на файлы источников.

Проверяется ветка, из-за которой правка чуть не потеряла данные: у задания бывает
НЕСКОЛЬКО файлов-приложений («Файл A» и «Файл B» у №26), и первая версия скрипта брала
из плана только первую пару — вторая ссылка молча осталась бы битой.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load(module_name: str):
    """Скрипты лежат в scripts/ и не образуют пакет — грузим по пути."""
    path = PROJECT_ROOT / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


rewrite = _load("tsk761_rewrite_links")
verify = _load("tsk761_verify_links")


CAS_A = "/api/v1/media/" + "a" * 64 + ".txt"
CAS_B = "/api/v1/media/" + "b" * 64 + ".txt"


def _plan_file(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"plan": items}, ensure_ascii=False), encoding="utf-8")
    return path


def test_две_ссылки_одного_задания_обе_попадают_в_план(tmp_path: Path) -> None:
    path = _plan_file(tmp_path, [
        {"task_id": 1, "bad_href": "/get_file?id=1", "cas_href": CAS_A, "match": True},
        {"task_id": 1, "bad_href": "/get_file?id=2", "cas_href": CAS_B, "match": True},
    ])
    plan = rewrite.load_plan(path)
    assert [i["bad_href"] for i in plan[1]] == ["/get_file?id=1", "/get_file?id=2"]


def test_недоказанные_пары_в_план_не_берутся(tmp_path: Path) -> None:
    path = _plan_file(tmp_path, [
        {"task_id": 1, "bad_href": "/get_file?id=1", "cas_href": CAS_A, "match": True},
        {"task_id": 2, "bad_href": "/get_file?id=9", "cas_href": CAS_B, "match": False},
        {"task_id": 3, "bad_href": "ege-txt/x.docx", "cas_href": None, "match": None},
    ])
    plan = rewrite.load_plan(path)
    assert set(plan) == {1}


def test_план_без_доказанных_пар_это_ошибка(tmp_path: Path) -> None:
    path = _plan_file(tmp_path, [
        {"task_id": 2, "bad_href": "/get_file?id=9", "cas_href": CAS_B, "match": False},
    ])
    with pytest.raises(RuntimeError):
        rewrite.load_plan(path)


def test_замена_меняет_только_href_и_не_трогает_текст_ссылки() -> None:
    stem = '<p><a href="/get_file?id=1" target="_blank">Задание 26</a></p>'
    out, n = rewrite.transform_stem(stem, "/get_file?id=1", CAS_A)
    assert n == 1
    assert out == f'<p><a href="{CAS_A}" target="_blank">Задание 26</a></p>'


def test_замена_не_задевает_соседнюю_ссылку_с_похожим_адресом() -> None:
    stem = '<a href="/get_file?id=1">A</a><a href="/get_file?id=12">B</a>'
    out, n = rewrite.transform_stem(stem, "/get_file?id=1", CAS_A)
    assert n == 1
    assert '<a href="/get_file?id=12">B</a>' in out


def test_рабочая_ссылка_на_наше_хранилище_остаётся_как_есть() -> None:
    stem = f'<a href="{CAS_A}">скачать</a><a href="/get_file?id=1">Задание 26</a>'
    out, _ = rewrite.transform_stem(stem, "/get_file?id=1", CAS_B)
    assert f'<a href="{CAS_A}">скачать</a>' in out


@pytest.mark.parametrize(
    "href, expected_prefix",
    [
        ("/get_file?id=5", "https://ege.sdamgia.ru/get_file?id=5"),
        ("/doc/inf/zadanie26/26_demo.txt", "https://ege.sdamgia.ru/doc/inf/zadanie26/26_demo.txt"),
        ("ege-txt/10-260.docx", "https://kpolyakov.spb.ru/cms/files/ege-txt/10-260.docx"),
        ("https://inf-ege.sdamgia.ru/get_file?id=7", "https://inf-ege.sdamgia.ru/get_file?id=7"),
    ],
)
def test_адрес_источника_строится_по_виду_ссылки(href: str, expected_prefix: str) -> None:
    urls = verify.candidate_urls(href)
    assert urls[0] == expected_prefix


def test_якоря_страниц_адресом_файла_не_становятся() -> None:
    assert verify.candidate_urls("#anchor") == []
    assert verify.candidate_urls("mailto:a@b.c") == []
