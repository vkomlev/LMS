# tsk-996 фаза D: якорь задания банка на материал прозрачного хозяина

**Контекст:** пилот ОГЭ 20.10 — задания урока (6308) живут в курсе-банке 1948, подвешенном к уроку 1111 прозрачной связью (tsk-1198); якорь на материал урока 2358 давал 422 и не учитывался порядком. ADR-0008 «Дополнение 2026-10-20».

**Что изменено:**
- миграция `tsk996_bank_anchor` (revises `tsk1384_help_stuck_reason`): страж допускает материал прозрачного хозяина; триггер `trg_course_parents_anchor_on_unlink` обнуляет якоря при снятии/удалении/переносе прозрачной связи; `materials_anchor_on_move` по новому правилу;
- `node_sequence.anchor_of`: в списке хозяина действует якорь на материал хозяина; движок (курсор, список, `has_anchors`), программа курса, grace, превью, ручной прогресс — через него;
- `TasksService.anchor_course_ids` / `validate_anchor` / `anchor_material_options`, новый `GET /tasks/{id}/anchor-materials`, bulk-upsert резолвит `anchor_external_uid` у хозяина;
- тесты `tests/test_tsk996_bank_anchor.py` (23).

Ревью: `reviews/2026-10-20-tsk996-bank-anchor-review.md` — ПРИНЯТО. Полный дифф: `reviews/2026-10-20-tsk996-bank-anchor.diff`.
