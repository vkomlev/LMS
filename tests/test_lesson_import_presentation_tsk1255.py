"""tsk-1255, поставка 2: презентация → состояния по щелчкам → кадры → блок разбора.

Главное, что ловят тесты: шаги анимации не теряются (иначе ученик видит
ответ сразу), кадры доступны ученику штатной проверкой прав (адрес кадра
целиком в содержимом материала), а фоновая задача не раскрывает чужой курс и
не висит «в работе» вечно после рестарта.
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest
from lxml import etree

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.services.lesson_import import jobs, slides
from app.services.lesson_import.pptx_steps import build_state, build_states_pptx, parse_steps
from app.utils.exceptions import DomainError

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

_SLIDE = f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>
 <p:sp><p:nvSpPr><p:cNvPr id="2" name="t"/></p:nvSpPr><p:txBody>
  <a:p><a:r><a:t>Вопрос</a:t></a:r></a:p>
  <a:p><a:r><a:t>Шаг 1</a:t></a:r></a:p>
  <a:p><a:r><a:t>Шаг 2</a:t></a:r></a:p></p:txBody></p:sp>
 <p:sp><p:nvSpPr><p:cNvPr id="3" name="pic"/></p:nvSpPr></p:sp>
</p:spTree></p:cSld>
<p:timing><p:tnLst><p:par><p:cTn><p:childTnLst><p:seq concurrent="1"><p:cTn><p:childTnLst>
 <p:par><p:cTn><p:childTnLst><p:par><p:cTn presetClass="entr"><p:stCondLst/><p:childTnLst><p:set><p:cBhvr><p:tgtEl>
  <p:spTgt spid="2"><p:txEl><p:pRg st="1" end="1"/></p:txEl></p:spTgt></p:tgtEl></p:cBhvr></p:set></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>
 <p:par><p:cTn><p:childTnLst><p:par><p:cTn presetClass="entr"><p:childTnLst><p:set><p:cBhvr><p:tgtEl>
  <p:spTgt spid="2"><p:txEl><p:pRg st="2" end="2"/></p:txEl></p:spTgt></p:tgtEl></p:cBhvr></p:set>
  <p:set><p:cBhvr><p:tgtEl><p:spTgt spid="3"/></p:tgtEl></p:cBhvr></p:set></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>
</p:childTnLst></p:cTn></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing></p:sld>""".encode()


def _texts(xml: bytes) -> list[str]:
    root = etree.fromstring(xml)
    return ["".join(p.itertext()) for p in root.iter(f"{{{A}}}p")]


def test_steps_reveal_paragraphs_and_shapes_by_click():
    steps = parse_steps(etree.fromstring(_SLIDE))
    assert len(steps) == 2
    s0, s1, s2 = (build_state(_SLIDE, steps, k) for k in range(3))
    assert _texts(s0) == ["Вопрос", "", ""]  # место абзацев осталось, текста нет
    assert b'id="3"' not in s0 and b"p:timing" not in s0
    assert _texts(s1) == ["Вопрос", "Шаг 1", ""]
    assert _texts(s2) == ["Вопрос", "Шаг 1", "Шаг 2"] and b'id="3"' in s2


def test_not_pptx_is_422():
    with pytest.raises(DomainError) as exc:
        build_states_pptx(b"not a zip")
    assert exc.value.status_code == 422


def test_slideshow_html_carries_full_file_url_for_acl():
    html = slides.slideshow_html(
        [{"slide": 1, "step": 0, "steps_total": 1}, {"slide": 1, "step": 1, "steps_total": 1}],
        ["aa.webp", "bb.webp"],
    )
    assert 'data-slideshow-base="/"' in html
    # Проверка прав ищет именно эту подстроку в content материала.
    assert "/api/v1/materials/files/aa.webp" in html and "/api/v1/materials/files/bb.webp" in html


_DIPLOMAT = Path("D:/Work/CreateCourses/courses/himiya-diplomat")


@pytest.mark.skipif(not _DIPLOMAT.exists(), reason="нет исходников курса «Дипломат»")
def test_real_lesson_states_match_manual_script():
    src = _DIPLOMAT / "materials" / "urok-06" / "Презентация к уроку 6.pptx"
    data, plan = build_states_pptx(src.read_bytes())
    ref = json.loads((_DIPLOMAT / "build" / "urok-06" / "plan.json").read_text("utf-8"))
    assert plan == ref
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slides_in = [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
    assert len(slides_in) == len(plan) == 91


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="нет pdftoppm")
def test_pdf_presentation_renders_frames(monkeypatch):
    """PDF-презентация: страницы → WebP → блок; хранилище подменено."""
    from PIL import Image

    buf = io.BytesIO()
    pages = [Image.new("RGB", (400, 300), c) for c in ("white", "gray")]
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    stored: list[bytes] = []

    async def fake_store(data: bytes, ext: str, content_type: str) -> str:
        stored.append(data)
        return f"f{len(stored)}.{ext}"

    monkeypatch.setattr(slides.material_files_storage, "store_bytes", fake_store)
    # conftest ставит на Windows селекторный цикл — подпроцессов в нём нет.
    factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
    res = asyncio.run(slides.render_presentation(buf.getvalue(), "prez.pdf"), loop_factory=factory)
    assert res["states"] == 2 and res["screens"] == 2
    assert all(d[:4] == b"RIFF" and d[8:12] == b"WEBP" for d in stored)
    assert "1/0/0/api/v1/materials/files/f1.webp" in res["html"]


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, k):
        return self.data.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.data:
            return None
        self.data[k] = v
        return True

    async def delete(self, k):
        self.data.pop(k, None)


def test_job_hidden_from_other_course_and_stale_fails(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(jobs, "_redis", lambda: fake)
    fake.data[jobs.KEY.format("a" * 32)] = json.dumps(
        {"course_id": 5, "status": "running", "updated": time.time() - jobs.STALE_S - 1}
    )
    assert asyncio.run(jobs.get_job("a" * 32, 6)) is None
    state = asyncio.run(jobs.get_job("a" * 32, 5))
    assert state["status"] == "failed" and "прервался" in state["error"]


def test_job_failure_is_recorded_and_lock_released(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(jobs, "_redis", lambda: fake)

    async def boom(data, filename):
        raise DomainError(detail="LibreOffice не отдал PDF", status_code=500)

    monkeypatch.setattr(jobs, "render_presentation", boom)

    async def scenario():
        job_id = await jobs.start_job(5, 1, b"x", "p.pptx")
        await asyncio.gather(*jobs._tasks)
        return job_id

    job_id = asyncio.run(scenario())
    state = asyncio.run(jobs.get_job(job_id, 5))
    assert state["status"] == "failed" and state["error"] == "LibreOffice не отдал PDF"
    assert jobs.LOCK not in fake.data
