# -*- coding: utf-8 -*-
"""tsk-537: guard в unwrap_broken_rel_links_tsk261.py не должен снова стирать
рабочие внутренние deep-link (/courses/..., /api/...), если скрипт когда-нибудь
запустят повторно на новой партии материалов (root cause tsk-537 — этот скрипт
уже один раз стёр <a href> у 10 материалов ОГЭ-информатики 2026-07-17).
"""
import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "unwrap_broken_rel_links_tsk261.py"

spec = importlib.util.spec_from_file_location("unwrap_broken_rel_links_tsk261", SCRIPT_PATH)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
unwrap = _mod.unwrap


def test_internal_courses_link_survives_unwrap():
    html = '<li><a href="/courses/wp%3Ainf-7-g1-t5" target="_blank" rel="noopener">Тема</a> — текст.</li>'
    assert unwrap(html) == html


def test_internal_api_link_survives_unwrap():
    html = '<p>Файл: <a href="/api/v1/media/abc123">скачать</a>.</p>'
    assert unwrap(html) == html


def test_broken_wp_relative_link_still_unwrapped():
    html = '<li><a href="/sozdanie-chat-botov-navigator-kursa/foo/">Словарь бот-мейкера</a> — текст.</li>'
    assert unwrap(html) == "<li>Словарь бот-мейкера — текст.</li>"


def test_mixed_content_only_broken_link_stripped():
    html = (
        '<p>См. <a href="/courses/wp%3Ainf-10-g4-t2">Алгебра логики</a> и '
        '<a href="/old-wp-navigator/foo/">старую тему</a>.</p>'
    )
    expected = (
        '<p>См. <a href="/courses/wp%3Ainf-10-g4-t2">Алгебра логики</a> и '
        "старую тему.</p>"
    )
    assert unwrap(html) == expected


def test_absolute_and_anchor_links_untouched():
    html = '<p><a href="https://example.com/foo">внешняя</a> и <a href="#anchor">якорь</a>.</p>'
    assert unwrap(html) == html
