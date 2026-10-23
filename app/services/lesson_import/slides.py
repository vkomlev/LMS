"""tsk-1255: презентация → кадры WebP в хранилище → разметка блока «разбор по шагам».

Рисует LibreOffice (pptx → PDF), страницы режет `pdftoppm`, кадры сжимает
Pillow. Кадры уходят в то же хранилище материалов (имя — sha содержимого),
что у загрузок методиста, поэтому доступ к ним проверяется штатно: по
вхождению адреса `/api/v1/materials/files/<имя>` в содержимое материала.

Конвертация тяжёлая (сотни МБ памяти, минуты) и делит сервер с боевыми
службами: пониженный приоритет, таймаут, свой профиль LibreOffice на запуск.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import signal
import tempfile
from pathlib import Path

from PIL import Image

from app.services import material_files_storage
from app.services.lesson_import.pptx_steps import build_states_pptx
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

FILES_PREFIX = "api/v1/materials/files/"
SOFFICE_TIMEOUT_S = 900
WIDTH, QUALITY, DPI = 1400, 82, 120
CAPTION = (
    "Разбор идёт по шагам — так же, как на занятии: следующий пункт открывается, "
    "когда вы нажимаете «Дальше». Полный текст урока — в конспекте ниже."
)


async def _run(cmd: list[str], cwd: Path, timeout: int) -> None:
    """Запустить внешнюю программу с пониженным приоритетом; сбой — DomainError с хвостом вывода."""
    env = {**os.environ, "HOME": str(cwd)}
    prefix = ["nice", "-n", "15"] if shutil.which("nice") else []
    proc = await asyncio.create_subprocess_exec(
        *prefix, *cmd, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        # Своя группа процессов: soffice — скрипт, рисует дочерний soffice.bin.
        # Убить только обёртку — оставить сироту с сотнями МБ на боевом сервере.
        start_new_session=os.name == "posix",
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        await proc.wait()
        raise DomainError(detail=f"{cmd[0]}: не уложился в {timeout} с", status_code=500) from exc
    if proc.returncode != 0:
        tail = out.decode("utf-8", "replace")[-400:]
        raise DomainError(detail=f"{cmd[0]}: код {proc.returncode}: {tail}", status_code=500)


async def _to_pdf(src: Path, work: Path) -> Path:
    """pptx → PDF через LibreOffice с отдельным профилем (параллельные запуски не делят его)."""
    if shutil.which("soffice") is None:
        raise DomainError(detail="На сервере нет LibreOffice (soffice)", status_code=503)
    await _run(
        ["soffice", f"-env:UserInstallation=file://{work}/profile", "--headless", "--norestore",
         "--convert-to", "pdf", "--outdir", str(work), str(src)],
        work, SOFFICE_TIMEOUT_S,
    )
    pdf = work / (src.stem + ".pdf")
    if not pdf.exists():
        raise DomainError(detail="LibreOffice не отдал PDF", status_code=500)
    return pdf


def _webp(png: Path) -> bytes:
    """Страница → WebP шириной WIDTH."""
    img = Image.open(png).convert("RGB")
    if img.width != WIDTH:
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=QUALITY, method=6)
    return buf.getvalue()


async def render_presentation(data: bytes, filename: str) -> dict:
    """Файл презентации (pptx или pdf) → {"html", "states", "screens"}."""
    is_pdf = filename.lower().endswith(".pdf")
    with tempfile.TemporaryDirectory(prefix="lesson-import-") as tmp:
        work = Path(tmp)
        if is_pdf:
            pdf = work / "in.pdf"
            pdf.write_bytes(data)
            plan = None
        else:
            states_pptx, plan = await asyncio.to_thread(build_states_pptx, data)
            src = work / "states.pptx"
            src.write_bytes(states_pptx)
            pdf = await _to_pdf(src, work)
        await _run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), str(work / "s")], work, 1800)
        pngs = sorted(work.glob("s-*.png"))
        if not pngs:
            raise DomainError(detail="Из PDF не получилось ни одной страницы", status_code=500)
        if plan is None:
            plan = [{"slide": i, "step": 0, "steps_total": 0} for i in range(1, len(pngs) + 1)]
        if len(plan) != len(pngs):
            raise DomainError(detail=f"Состояний {len(plan)}, страниц PDF {len(pngs)}", status_code=500)
        files = []
        for png in pngs:
            frame = await asyncio.to_thread(_webp, png)
            files.append(await material_files_storage.store_bytes(frame, "webp", "image/webp"))
    return {"html": slideshow_html(plan, files), "states": len(plan), "screens": plan[-1]["slide"]}


def slideshow_html(plan: list[dict], files: list[str]) -> str:
    """Разметка блока SlideShowBlock: каталог «/» и путь кадра от корня сайта.

    Полный путь кадра в самой записи — не ради плеера, а ради доступа: файл
    отдаётся ученику, только если адрес `/api/v1/materials/files/<имя>`
    встречается в содержимом материала его курса.
    """
    states = ";".join(
        f'{p["slide"]}/{p["step"]}/{p["steps_total"]}/{FILES_PREFIX}{f}' for p, f in zip(plan, files)
    )
    return (
        f'<div data-slideshow-base="/" data-slideshow-states="{states}" '
        f'data-slideshow-caption="{CAPTION}"></div>'
    )
