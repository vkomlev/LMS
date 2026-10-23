"""tsk-1255: презентация (pptx) → состояния слайдов по щелчкам.

Перенос `pptx_steps.py` курса «Дипломат». Анимация в авторских презентациях —
не украшение: почти все эффекты это «появление» отдельного абзаца по щелчку,
то есть очередной пункт разбора. Показать слайд целиком — значит выдать ответ.

Из дерева `p:timing` берётся порядок щелчков; для каждого состояния собирается
копия слайда, где нераскрытые фигуры удалены, а нераскрытые абзацы очищены
(место остаётся — текст не «прыгает»). Все состояния складываются в один pptx,
его рисует LibreOffice — тот же движок, что показывает композицию автора.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from lxml import etree

from app.utils.exceptions import DomainError

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RNS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"p": P, "a": A}
_SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml")
#: Предел состояний: 30 слайдов автора дают 100–190; больше — повод остановиться.
MAX_STATES = 400

Step = list[dict[str, Any]]


def parse_steps(slide: etree._Element) -> list[Step]:
    """Щелчки слайда: что появляется на каждом (`paras=None` — фигура целиком)."""
    lst = slide.find("p:timing//p:seq[@concurrent]/p:cTn/p:childTnLst", NS)
    if lst is None:
        return []
    steps: list[Step] = []
    for click in lst.findall("p:par", NS):
        targets: Step = []
        for eff in click.iter(f"{{{P}}}cTn"):
            if eff.get("presetClass") != "entr":
                continue
            for tgt in eff.iter(f"{{{P}}}spTgt"):
                if tgt.get("spid") is None:
                    continue
                prg = tgt.find("p:txEl/p:pRg", NS)
                item = {
                    "spid": int(tgt.get("spid")),
                    "paras": (int(prg.get("st")), int(prg.get("end"))) if prg is not None else None,
                }
                if item not in targets:
                    targets.append(item)
        if targets:
            steps.append(targets)
    return steps


def revealed_at(steps: list[Step], k: int) -> tuple[set[int], dict[int, set[int]]]:
    """Что видно после k щелчков: фигуры целиком и раскрытые абзацы."""
    shapes: set[int] = set()
    paras: dict[int, set[int]] = {}
    for step in steps[:k]:
        for t in step:
            if t["paras"] is None:
                shapes.add(t["spid"])
            else:
                st, end = t["paras"]
                paras.setdefault(t["spid"], set()).update(range(st, end + 1))
    return shapes, paras


def _shape_id(sp: etree._Element) -> int | None:
    nv = sp.find(".//p:cNvPr", NS)
    return int(nv.get("id")) if nv is not None and nv.get("id") is not None else None


def build_state(slide_xml: bytes, steps: list[Step], k: int) -> bytes:
    """Копия слайда, где раскрыто ровно k щелчков; анимация убрана."""
    root = etree.fromstring(slide_xml)
    anim_shapes, anim_paras = revealed_at(steps, len(steps))
    seen_shapes, seen_paras = revealed_at(steps, k)
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        return slide_xml
    for sp in list(tree):
        if etree.QName(sp).localname not in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            continue
        sid = _shape_id(sp)
        if sid is None:
            continue
        if sid in anim_shapes and sid not in seen_shapes:
            tree.remove(sp)
            continue
        body = sp.find("p:txBody", NS) if sid in anim_paras else None
        if body is None:
            continue
        opened = seen_paras.get(sid, set())
        for idx, par in enumerate(body.findall("a:p", NS)):
            if idx in anim_paras[sid] and idx not in opened:
                for child in list(par):
                    if etree.QName(child).localname in ("r", "br", "fld"):
                        par.remove(child)
    timing = root.find("p:timing", NS)
    if timing is not None:
        root.remove(timing)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _slide_order(z: zipfile.ZipFile) -> list[str]:
    """Слайды в порядке показа — по sldIdLst презентации, а не по номеру файла."""
    pres = etree.fromstring(z.read("ppt/presentation.xml"))
    rels = etree.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sld in pres.findall("p:sldIdLst/p:sldId", NS):
        t = target.get(sld.get(f"{{{R}}}id"), "")
        name = "ppt/" + t.lstrip("/").removeprefix("ppt/")
        if _SLIDE_RE.fullmatch(name):
            out.append(name)
    return out


def build_states_pptx(data: bytes) -> tuple[bytes, list[dict[str, int]]]:
    """pptx, где каждый слайд — одно состояние, и карта «экран/шаг/всего»."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
        names = _slide_order(zin)
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise DomainError(detail="Файл не читается как презентация PowerPoint (.pptx)",
                          status_code=422) from exc
    plan: list[dict[str, int]] = []
    states: list[tuple[str, bytes]] = []
    for screen, name in enumerate(names, 1):
        xml = zin.read(name)
        steps = parse_steps(etree.fromstring(xml))
        for k in range(len(steps) + 1):
            states.append((name, build_state(xml, steps, k) if steps else xml))
            plan.append({"slide": screen, "step": k, "steps_total": len(steps)})
    if not states:
        raise DomainError(detail="В презентации нет слайдов", status_code=422)
    if len(states) > MAX_STATES:
        raise DomainError(detail=f"Слишком много шагов анимации: {len(states)} (предел {MAX_STATES})",
                          status_code=422)
    return _write_states(zin, states), plan


def _write_states(zin: zipfile.ZipFile, states: list[tuple[str, bytes]]) -> bytes:
    """Собрать новый пакет: слайды заменены состояниями, связи слайдов продублированы."""
    out = io.BytesIO()
    skip = {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            if n in skip or _SLIDE_RE.fullmatch(n) or re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", n):
                continue
            zout.writestr(n, zin.read(n))
        for i, (orig, xml) in enumerate(states, 1):
            zout.writestr(f"ppt/slides/slide{i}.xml", xml)
            rel = f"ppt/slides/_rels/{orig.rsplit('/', 1)[1]}.rels"
            if rel in zin.namelist():
                zout.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", zin.read(rel))
        pres = etree.fromstring(zin.read("ppt/presentation.xml"))
        lst = pres.find("p:sldIdLst", NS)
        for child in list(lst):
            lst.remove(child)
        rels = etree.fromstring(zin.read("ppt/_rels/presentation.xml.rels"))
        for child in list(rels):
            if child.get("Type", "").endswith("/slide"):
                rels.remove(child)
        for i in range(1, len(states) + 1):
            el = etree.SubElement(lst, f"{{{P}}}sldId")
            el.set("id", str(255 + i))
            el.set(f"{{{R}}}id", f"rIdState{i}")
            rel = etree.SubElement(rels, f"{{{RNS}}}Relationship")
            rel.set("Id", f"rIdState{i}")
            rel.set("Type", f"{R}/slide")
            rel.set("Target", f"slides/slide{i}.xml")
        for name, el in (("ppt/presentation.xml", pres), ("ppt/_rels/presentation.xml.rels", rels)):
            zout.writestr(name, etree.tostring(el, xml_declaration=True, encoding="UTF-8", standalone=True))
    return out.getvalue()
