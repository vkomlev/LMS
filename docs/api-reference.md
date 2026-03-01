# РџРѕР»РЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ API Quiz СЃРёСЃС‚РµРјС‹

**Р’РµСЂСЃРёСЏ:** 2.0  
**Р”Р°С‚Р° РѕР±РЅРѕРІР»РµРЅРёСЏ:** 2026-01-17  
**Р‘Р°Р·РѕРІС‹Р№ URL:** `http://localhost:8000/api/v1`  
**Swagger UI:** `http://localhost:8000/docs`

---

## РЎРѕРґРµСЂР¶Р°РЅРёРµ

1. [РћР±С‰Р°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ](#РѕР±С‰Р°СЏ-РёРЅС„РѕСЂРјР°С†РёСЏ)
2. [РђСѓС‚РµРЅС‚РёС„РёРєР°С†РёСЏ](#Р°СѓС‚РµРЅС‚РёС„РёРєР°С†РёСЏ)
3. [Р РѕР»Рё Рё СѓРїСЂР°РІР»РµРЅРёРµ РёРјРё](#СЂРѕР»Рё-Рё-СѓРїСЂР°РІР»РµРЅРёРµ-РёРјРё)
4. [Р­РЅРґРїРѕР№РЅС‚С‹ Р·Р°РґР°С‡](#СЌРЅРґРїРѕР№РЅС‚С‹-Р·Р°РґР°С‡)
5. [Р­РЅРґРїРѕР№РЅС‚С‹ РїСЂРѕРІРµСЂРєРё](#СЌРЅРґРїРѕР№РЅС‚С‹-РїСЂРѕРІРµСЂРєРё)
6. [Р­РЅРґРїРѕР№РЅС‚С‹ РїРѕРїС‹С‚РѕРє](#СЌРЅРґРїРѕР№РЅС‚С‹-РїРѕРїС‹С‚РѕРє)
7. [Р­РЅРґРїРѕР№РЅС‚С‹ СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ](#СЌРЅРґРїРѕР№РЅС‚С‹-СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ)
8. [Learning API (Learning Engine V1)](#learning-api-learning-engine-v1)
9. [Р­РЅРґРїРѕР№РЅС‚С‹ СЃС‚Р°С‚РёСЃС‚РёРєРё](#СЌРЅРґРїРѕР№РЅС‚С‹-СЃС‚Р°С‚РёСЃС‚РёРєРё)
10. [Р­РЅРґРїРѕР№РЅС‚С‹ РјР°С‚РµСЂРёР°Р»РѕРІ](#СЌРЅРґРїРѕР№РЅС‚С‹-РјР°С‚РµСЂРёР°Р»РѕРІ)
11. [Р­РЅРґРїРѕР№РЅС‚С‹ РёРјРїРѕСЂС‚Р°](#СЌРЅРґРїРѕР№РЅС‚С‹-РёРјРїРѕСЂС‚Р°)
12. [РљРѕРґС‹ РѕС€РёР±РѕРє](#РєРѕРґС‹-РѕС€РёР±РѕРє)

---

## РћР±С‰Р°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ

### Р¤РѕСЂРјР°С‚ РѕС‚РІРµС‚РѕРІ

Р’СЃРµ РѕС‚РІРµС‚С‹ РІРѕР·РІСЂР°С‰Р°СЋС‚СЃСЏ РІ С„РѕСЂРјР°С‚Рµ JSON СЃ РєРѕРґРёСЂРѕРІРєРѕР№ UTF-8.

### РџР°РіРёРЅР°С†РёСЏ

Р­РЅРґРїРѕР№РЅС‚С‹, РІРѕР·РІСЂР°С‰Р°СЋС‰РёРµ СЃРїРёСЃРєРё, РїРѕРґРґРµСЂР¶РёРІР°СЋС‚ РїР°РіРёРЅР°С†РёСЋ С‡РµСЂРµР· query-РїР°СЂР°РјРµС‚СЂС‹:
- `limit` (int, 1-1000) - РєРѕР»РёС‡РµСЃС‚РІРѕ Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ
- `offset` (int, в‰Ґ0) - СЃРјРµС‰РµРЅРёРµ РґР»СЏ РїР°РіРёРЅР°С†РёРё

### Р’РµСЂСЃРёРѕРЅРёСЂРѕРІР°РЅРёРµ

API РёСЃРїРѕР»СЊР·СѓРµС‚ РІРµСЂСЃРёРѕРЅРёСЂРѕРІР°РЅРёРµ С‡РµСЂРµР· РїСЂРµС„РёРєСЃ РїСѓС‚Рё: `/api/v1/`

---

## РђСѓС‚РµРЅС‚РёС„РёРєР°С†РёСЏ

Р’СЃРµ СЌРЅРґРїРѕР№РЅС‚С‹ С‚СЂРµР±СѓСЋС‚ API РєР»СЋС‡, РїРµСЂРµРґР°РІР°РµРјС‹Р№ С‡РµСЂРµР· query-РїР°СЂР°РјРµС‚СЂ:

```
?api_key=your-api-key
```

**РџСЂРёРјРµСЂ:**
```
GET /api/v1/tasks/1?api_key=bot-key-1
```

**РћС€РёР±РєР° Р°СѓС‚РµРЅС‚РёС„РёРєР°С†РёРё (403):**
```json
{
  "detail": "Invalid or missing API Key"
}
```

---

## Р РѕР»Рё Рё СѓРїСЂР°РІР»РµРЅРёРµ РёРјРё

РЎРїСЂР°РІРѕС‡РЅРёРє СЂРѕР»РµР№, РЅР°Р·РЅР°С‡РµРЅРёРµ СЂРѕР»РµР№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРј, С„РёР»СЊС‚СЂР°С†РёСЏ РїРѕ СЂРѕР»СЏРј Рё Р·Р°СЏРІРєРё РЅР° РґРѕСЃС‚СѓРї Рє СЂРѕР»СЏРј РѕРїРёСЃР°РЅС‹ РІ РѕС‚РґРµР»СЊРЅРѕРј РєРѕРЅС‚СЂР°РєС‚Рµ:

**[РљРѕРЅС‚СЂР°РєС‚: Р РѕР»Рё Рё СѓРїСЂР°РІР»РµРЅРёРµ РёРјРё С‡РµСЂРµР· API](roles-and-api-contract.md)**

Р’ РЅС‘Рј Р·Р°РєСЂРµРїР»РµРЅС‹ РЅРµРёР·РјРµРЅСЏРµРјС‹Рµ ID СЂРѕР»РµР№ РїРѕ РєРѕРЅС‚СЂР°РєС‚Сѓ СЃ РўР“ Р±РѕС‚РѕРј (1=admin, 2=methodist, 3=teacher, 4=student, 5=marketer, 6=customer), РїСЂР°РІРёР»Р° РґРѕР±Р°РІР»РµРЅРёСЏ РЅРѕРІС‹С… СЂРѕР»РµР№ Рё РїРµСЂРµС‡РµРЅСЊ СЌРЅРґРїРѕРёРЅС‚РѕРІ: `/roles/`, `/users/{user_id}/roles/`, С„РёР»СЊС‚СЂ `role` РІ `/users/` Рё `/users/search`, `/access_requests/` (РІ С‚.С‡. `role_id`).

---

## Р­РЅРґРїРѕР№РЅС‚С‹ Р·Р°РґР°С‡

### GET /tasks/by-course/{course_id}

РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє Р·Р°РґР°С‡ РєСѓСЂСЃР° СЃ С„РёР»СЊС‚СЂР°С†РёРµР№ Рё РїР°РіРёРЅР°С†РёРµР№.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `course_id` (path, int) - ID РєСѓСЂСЃР°
- `difficulty_id` (query, int, optional) - Р¤РёР»СЊС‚СЂ РїРѕ СѓСЂРѕРІРЅСЋ СЃР»РѕР¶РЅРѕСЃС‚Рё
- `limit` (query, int, default: 100) - РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ
- `offset` (query, int, default: 0) - РЎРјРµС‰РµРЅРёРµ

**РћС‚РІРµС‚ (200 OK):**
```json
[
  {
    "id": 1,
    "external_uid": "TASK-SC-001",
    "task_content": {
      "type": "SC",
      "stem": "Р§С‚Рѕ С‚Р°РєРѕРµ РїРµСЂРµРјРµРЅРЅР°СЏ РІ Python?",
      "options": [
        {"id": "A", "text": "РћР±Р»Р°СЃС‚СЊ РїР°РјСЏС‚Рё", "is_active": true},
        {"id": "B", "text": "Р¤СѓРЅРєС†РёСЏ", "is_active": true}
      ]
    },
    "solution_rules": {
      "max_score": 10,
      "correct_options": ["A"]
    },
    "course_id": 1,
    "difficulty_id": 3,
    "max_score": 10,
    "hints_text": [],
    "hints_video": [],
    "has_hints": false
  }
]
```

Р’ РѕС‚РІРµС‚Р°С… Р·Р°РґР°С‡ (TaskRead) РїСЂРёСЃСѓС‚СЃС‚РІСѓСЋС‚ РїРѕР»СЏ РїРѕРґСЃРєР°Р·РѕРє РёР· `task_content` (Learning Engine V1, СЌС‚Р°Рї 5): `hints_text`, `hints_video`, `has_hints`. РЎРј. [assignments-and-results-api.md](assignments-and-results-api.md), [hints-stage5.md](hints-stage5.md).

**РћС€РёР±РєРё:**
- `404` - РљСѓСЂСЃ РЅРµ РЅР°Р№РґРµРЅ

---

### GET /tasks/by-external/{external_uid}

РџРѕР»СѓС‡РёС‚СЊ Р·Р°РґР°С‡Сѓ РїРѕ РІРЅРµС€РЅРµРјСѓ РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂСѓ.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `external_uid` (path, string) - Р’РЅРµС€РЅРёР№ РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ Р·Р°РґР°С‡Рё

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "id": 1,
  "external_uid": "TASK-SC-001",
  "task_content": {...},
  "solution_rules": {...},
  "course_id": 1,
  "difficulty_id": 3,
  "max_score": 10
}
```

**РћС€РёР±РєРё:**
- `404` - Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР°

---

### POST /tasks/validate

РџСЂРµРґРІР°СЂРёС‚РµР»СЊРЅР°СЏ РІР°Р»РёРґР°С†РёСЏ Р·Р°РґР°С‡Рё РїРµСЂРµРґ РёРјРїРѕСЂС‚РѕРј.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "task_content": {
    "type": "SC",
    "stem": "Р§С‚Рѕ С‚Р°РєРѕРµ РїРµСЂРµРјРµРЅРЅР°СЏ?",
    "options": [
      {"id": "A", "text": "РћР±Р»Р°СЃС‚СЊ РїР°РјСЏС‚Рё", "is_active": true},
      {"id": "B", "text": "Р¤СѓРЅРєС†РёСЏ", "is_active": true}
    ]
  },
  "solution_rules": {
    "max_score": 10,
    "correct_options": ["A"]
  },
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "external_uid": "TASK-SC-001"
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "is_valid": true,
  "errors": []
}
```

РёР»Рё

```json
{
  "is_valid": false,
  "errors": [
    "course_code not provided",
    "Validation error: Р”Р»СЏ Р·Р°РґР°С‡ С‚РёРїР° SC РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓРєР°Р·Р°РЅ СЂРѕРІРЅРѕ РѕРґРёРЅ РїСЂР°РІРёР»СЊРЅС‹Р№ РІР°СЂРёР°РЅС‚. РЈРєР°Р·Р°РЅРѕ: 2"
  ]
}
```

---

### POST /tasks/bulk-upsert

РњР°СЃСЃРѕРІС‹Р№ upsert Р·Р°РґР°С‡ РїРѕ external_uid.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "items": [
    {
      "external_uid": "TASK-SC-001",
      "course_id": 1,
      "difficulty_id": 3,
      "task_content": {...},
      "solution_rules": {...},
      "max_score": 10
    }
  ]
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "results": [
    {"external_uid": "TASK-SC-001", "action": "created", "id": 1},
    {"external_uid": "TASK-SC-002", "action": "updated", "id": 2}
  ]
}
```

**РћС€РёР±РєРё:**
- `400` - РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё РґР°РЅРЅС‹С… Р·Р°РґР°С‡
- `422` - РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё Р·Р°РїСЂРѕСЃР°

---

### POST /tasks/find-by-external

РњР°СЃСЃРѕРІРѕРµ РїРѕР»СѓС‡РµРЅРёРµ Р·Р°РґР°С‡ РїРѕ СЃРїРёСЃРєСѓ external_uid.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "external_uids": ["TASK-SC-001", "TASK-SC-002"]
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "items": [
    {"id": 1, "external_uid": "TASK-SC-001", ...},
    {"id": 2, "external_uid": "TASK-SC-002", ...}
  ],
  "not_found": []
}
```

---

## Р­РЅРґРїРѕР№РЅС‚С‹ РїСЂРѕРІРµСЂРєРё

### POST /check/task

Stateless-РїСЂРѕРІРµСЂРєР° РѕРґРЅРѕР№ Р·Р°РґР°С‡Рё Р±РµР· СЃРѕС…СЂР°РЅРµРЅРёСЏ РІ Р‘Р”.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "task_content": {
    "type": "SC",
    "stem": "Р§С‚Рѕ С‚Р°РєРѕРµ РїРµСЂРµРјРµРЅРЅР°СЏ?",
    "options": [
      {"id": "A", "text": "РћР±Р»Р°СЃС‚СЊ РїР°РјСЏС‚Рё", "is_active": true},
      {"id": "B", "text": "Р¤СѓРЅРєС†РёСЏ", "is_active": true}
    ]
  },
  "solution_rules": {
    "max_score": 10,
    "scoring_mode": "all_or_nothing",
    "correct_options": ["A"],
    "penalties": {
      "wrong_answer": 0,
      "missing_answer": 0,
      "extra_wrong_mc": 0
    }
  },
  "answer": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  }
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "details": {
    "correct_options": ["A"],
    "user_options": ["A"],
    "matched_short_answer": null,
    "rubric_scores": null
  },
  "feedback": {
    "general": "РџСЂР°РІРёР»СЊРЅРѕ!",
    "by_option": {
      "A": "РџСЂР°РІРёР»СЊРЅРѕ! РџРµСЂРµРјРµРЅРЅР°СЏ РґРµР№СЃС‚РІРёС‚РµР»СЊРЅРѕ С…СЂР°РЅРёС‚ РґР°РЅРЅС‹Рµ РІ РїР°РјСЏС‚Рё."
    }
  }
}
```

**РћС€РёР±РєРё:**
- `400` - РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё РґР°РЅРЅС‹С… Р·Р°РґР°С‡Рё РёР»Рё РѕС‚РІРµС‚Р°
- `422` - РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё Р·Р°РїСЂРѕСЃР°

---

### POST /check/batch

РњР°СЃСЃРѕРІР°СЏ РїСЂРѕРІРµСЂРєР° РЅРµСЃРєРѕР»СЊРєРёС… Р·Р°РґР°С‡.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "items": [
    {
      "task_content": {...},
      "solution_rules": {...},
      "answer": {...}
    }
  ]
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "results": [
    {
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "details": {...},
      "feedback": {...}
    }
  ]
}
```

---

## Р­РЅРґРїРѕР№РЅС‚С‹ РїРѕРїС‹С‚РѕРє

### GET /attempts/by-user/{user_id}

РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє РїРѕРїС‹С‚РѕРє РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `user_id` (path, int) - ID РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
- `course_id` (query, int, optional) - Р¤РёР»СЊС‚СЂ РїРѕ РєСѓСЂСЃСѓ
- `limit` (query, int, default: 100) - РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№
- `offset` (query, int, default: 0) - РЎРјРµС‰РµРЅРёРµ

**РћС‚РІРµС‚ (200 OK):**
```json
[
  {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-01-17T12:00:00Z",
    "finished_at": null,
    "meta": {"task_ids": [123]},
    "time_expired": false,
    "attempts_used": null,
    "attempts_limit_effective": null,
    "last_based_status": null
  }
]
```

РџРѕР»СЏ `time_expired`, `attempts_used`, `attempts_limit_effective`, `last_based_status` (Learning Engine V1, СЌС‚Р°Рї 4) вЂ” СЃРј. [assignments-and-results-api.md](assignments-and-results-api.md), [attempts-integration-stage4.md](attempts-integration-stage4.md).

---

### POST /attempts

РЎРѕР·РґР°С‚СЊ РЅРѕРІСѓСЋ РїРѕРїС‹С‚РєСѓ РїСЂРѕС…РѕР¶РґРµРЅРёСЏ С‚РµСЃС‚Р°.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "meta": {
    "time_limit": 3600
  }
}
```

**РћС‚РІРµС‚ (201 Created):**
```json
{
  "id": 1,
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "created_at": "2026-01-17T12:00:00Z",
  "finished_at": null,
  "meta": {"time_limit": 3600}
}
```

---

### POST /attempts/{attempt_id}/answers

РћС‚РїСЂР°РІРёС‚СЊ РѕС‚РІРµС‚С‹ РїРѕ Р·Р°РґР°С‡Р°Рј РІРЅСѓС‚СЂРё РїРѕРїС‹С‚РєРё.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `attempt_id` (path, int) - ID РїРѕРїС‹С‚РєРё

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
- Р”Р»СЏ Р·Р°РґР°С‡ СЃ РєРѕРјРјРµРЅС‚Р°СЂРёРµРј (SA_COM): РІ `response` РґРѕРїСѓСЃРєР°РµС‚СЃСЏ РїРѕР»Рµ `comment` (string | null), РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ; РЅР° РїСЂРѕРІРµСЂРєСѓ/Р±Р°Р»Р»С‹ РЅРµ РІР»РёСЏРµС‚.

```json
{
  "items": [
    {
      "task_id": 1,
      "answer": {
        "type": "SC",
        "response": {
          "selected_option_ids": ["A"]
        }
      }
    },
    {
      "task_id": 2,
      "answer": {
        "type": "SA_COM",
        "response": {
          "value": "РѕСЃРЅРѕРІРЅРѕР№ РѕС‚РІРµС‚",
          "comment": "РєРѕРјРјРµРЅС‚Р°СЂРёР№ СѓС‡РµРЅРёРєР°"
        }
      }
    }
  ]
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "attempt_id": 1,
  "total_score_delta": 25,
  "total_max_score_delta": 30,
  "results": [
    {
      "task_id": 1,
      "score": 10,
      "max_score": 10,
      "is_correct": true
    },
    {
      "task_id": 2,
      "score": 15,
      "max_score": 20,
      "is_correct": false
    }
  ]
}
```

**РћС€РёР±РєРё:**
- `400` - РџРѕРїС‹С‚РєР° СѓР¶Рµ Р·Р°РІРµСЂС€РµРЅР° РёР»Рё РёСЃС‚РµРєР»Рѕ РІСЂРµРјСЏ
- `404` - РџРѕРїС‹С‚РєР° РЅРµ РЅР°Р№РґРµРЅР°
- `422` - РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё Р·Р°РїСЂРѕСЃР°

---

### POST /attempts/{attempt_id}/cancel (Learning Engine V1, СЌС‚Р°Рї 3.5)

РђРЅРЅСѓР»РёСЂРѕРІР°С‚СЊ Р°РєС‚РёРІРЅСѓСЋ РїРѕРїС‹С‚РєСѓ. РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ: РїРѕРІС‚РѕСЂРЅС‹Р№ РІС‹Р·РѕРІ РІРѕР·РІСЂР°С‰Р°РµС‚ `200` Рё `already_cancelled: true` Р±РµР· РёР·РјРµРЅРµРЅРёСЏ РґР°РЅРЅС‹С….

**РџР°СЂР°РјРµС‚СЂС‹:**
- `attempt_id` (path, int) - ID РїРѕРїС‹С‚РєРё

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ):**
```json
{
  "reason": "user_exit_to_main_menu"
}
```
РњРѕР¶РЅРѕ РѕС‚РїСЂР°РІРёС‚СЊ РїСѓСЃС‚РѕРµ С‚РµР»Рѕ `{}` РёР»Рё РЅРµ РїРµСЂРµРґР°РІР°С‚СЊ body.

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "attempt_id": 1,
  "status": "cancelled",
  "cancelled_at": "2026-02-26T12:00:00Z",
  "already_cancelled": false
}
```
- `already_cancelled: true` вЂ” РїРѕРїС‹С‚РєР° СѓР¶Рµ Р±С‹Р»Р° РѕС‚РјРµРЅРµРЅР° СЂР°РЅРµРµ (РёРґРµРјРїРѕС‚РµРЅС‚РЅС‹Р№ РѕС‚РІРµС‚).

**РћС€РёР±РєРё:**
- `404` - РџРѕРїС‹С‚РєР° РЅРµ РЅР°Р№РґРµРЅР°
- `409` - РџРѕРїС‹С‚РєР° СѓР¶Рµ Р·Р°РІРµСЂС€РµРЅР° (`finished_at` Р·Р°РґР°РЅ); РѕС‚РјРµРЅСЏС‚СЊ РјРѕР¶РЅРѕ С‚РѕР»СЊРєРѕ Р°РєС‚РёРІРЅСѓСЋ РїРѕРїС‹С‚РєСѓ

**РџРѕРІРµРґРµРЅРёРµ:**
- РћС‚РјРµРЅС‘РЅРЅР°СЏ РїРѕРїС‹С‚РєР° РЅРµ СЃС‡РёС‚Р°РµС‚СЃСЏ Р°РєС‚РёРІРЅРѕР№: РЅРµ РІРѕР·РІСЂР°С‰Р°РµС‚СЃСЏ РІ `POST /learning/tasks/{task_id}/start-or-get-attempt`.
- Р’ СЃС‚Р°С‚РёСЃС‚РёРєРµ Рё РІ В«РїРѕСЃР»РµРґРЅРµР№ РїРѕРїС‹С‚РєРµВ» РїРѕ Р·Р°РґР°С‡Рµ/РєСѓСЂСЃСѓ РѕС‚РјРµРЅС‘РЅРЅС‹Рµ РїРѕРїС‹С‚РєРё РЅРµ СѓС‡РёС‚С‹РІР°СЋС‚СЃСЏ (СѓС‡РёС‚С‹РІР°СЋС‚СЃСЏ С‚РѕР»СЊРєРѕ СЃ `finished_at` Рё Р±РµР· `cancelled_at`).

---

## Р­РЅРґРїРѕР№РЅС‚С‹ СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ

### GET /task-results/by-user/{user_id}

РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `user_id` (path, int) - ID РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
- `limit` (query, int, default: 100) - РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№
- `offset` (query, int, default: 0) - РЎРјРµС‰РµРЅРёРµ

**РћС‚РІРµС‚ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z",
    "metrics": {},
    "feedback": []
  }
]
```

---

### GET /task-results/by-task/{task_id}

РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ Р·Р°РґР°С‡Рµ.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `task_id` (path, int) - ID Р·Р°РґР°С‡Рё
- `limit` (query, int, default: 100) - РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№
- `offset` (query, int, default: 0) - РЎРјРµС‰РµРЅРёРµ

**РћС‚РІРµС‚ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z"
  }
]
```

**РћС€РёР±РєРё:**
- `404` - Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР°

---

### GET /task-results/by-attempt/{attempt_id}

РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ РїРѕРїС‹С‚РєРµ.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `attempt_id` (path, int) - ID РїРѕРїС‹С‚РєРё
- `limit` (query, int, default: 100) - РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№
- `offset` (query, int, default: 0) - РЎРјРµС‰РµРЅРёРµ

**РћС‚РІРµС‚ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z"
  }
]
```

**РћС€РёР±РєРё:**
- `404` - РџРѕРїС‹С‚РєР° РЅРµ РЅР°Р№РґРµРЅР°

---

### POST /task-results/{result_id}/manual-check

Р СѓС‡РЅР°СЏ РґРѕРѕС†РµРЅРєР° СЂРµР·СѓР»СЊС‚Р°С‚Р° Р·Р°РґР°С‡Рё.

**РџР°СЂР°РјРµС‚СЂС‹:**
- `result_id` (path, int) - ID СЂРµР·СѓР»СЊС‚Р°С‚Р°

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "score": 8,
  "checked_by": 2,
  "lock_token": "claim-token-from-claim-next",
  "is_correct": false,
  "metrics": {
    "comment": "Р§Р°СЃС‚РёС‡РЅРѕ РІРµСЂРЅРѕ"
  }
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 8,
  "max_score": 10,
  "is_correct": false,
  "checked_at": "2026-01-17T13:00:00Z",
  "checked_by": 2,
  "metrics": {
    "comment": "Р§Р°СЃС‚РёС‡РЅРѕ РІРµСЂРЅРѕ"
  }
}
```

**РћС€РёР±РєРё:**
- `400` - РќРµРІРµСЂРЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹ Р·Р°РїСЂРѕСЃР° (РЅР°РїСЂРёРјРµСЂ, score > max_score)
- `404` - Р РµР·СѓР»СЊС‚Р°С‚ РЅРµ РЅР°Р№РґРµРЅ
- `409` - Токен блокировки невалиден или просрочен (если передан `lock_token`)

---

## Learning API (Learning Engine V1)

Р­РЅРґРїРѕРёРЅС‚С‹ РјР°СЂС€СЂСѓС‚РёР·Р°С†РёРё Рё СЃРѕСЃС‚РѕСЏРЅРёР№ Learning Engine (СЌС‚Р°Рї 3). РљРѕРЅСЃРѕР»РёРґРёСЂРѕРІР°РЅРЅРѕРµ РѕРїРёСЃР°РЅРёРµ: [assignments-and-results-api.md](assignments-and-results-api.md). РџСЂРёРјРµСЂС‹ Рё smoke: [smoke-learning-api.md](smoke-learning-api.md).

| РњРµС‚РѕРґ | РџСѓС‚СЊ | РћРїРёСЃР°РЅРёРµ |
|-------|------|----------|
| GET | `/learning/next-item?student_id=` | РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі: material \| task \| none \| blocked_dependency \| blocked_limit. |
| POST | `/learning/materials/{material_id}/complete` | РћС‚РјРµС‚РёС‚СЊ РјР°С‚РµСЂРёР°Р» РїСЂРѕР№РґРµРЅРЅС‹Рј (body: `student_id`). |
| POST | `/learning/tasks/{task_id}/start-or-get-attempt` | РќР°С‡Р°С‚СЊ РёР»Рё РїРѕР»СѓС‡РёС‚СЊ С‚РµРєСѓС‰СѓСЋ РїРѕРїС‹С‚РєСѓ РїРѕ Р·Р°РґР°С‡Рµ. Р“Р°СЂР°РЅС‚РёСЏ: РІ РѕС‚РІРµС‚Рµ `GET /attempts/{id}` РїРѕР»Рµ `attempt.meta.task_ids` (int[]) СЃРѕРґРµСЂР¶РёС‚ РєР°Рє РјРёРЅРёРјСѓРј СЌС‚РѕС‚ `task_id`; РїСЂРё РїСѓСЃС‚РѕРј/Р±РёС‚РѕРј `meta` backend РІРѕСЃСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РµРіРѕ РїСЂРё РІС‹Р·РѕРІРµ. |
| GET | `/learning/tasks/{task_id}/state?student_id=` | РЎРѕСЃС‚РѕСЏРЅРёРµ Р·Р°РґР°РЅРёСЏ: OPEN \| IN_PROGRESS \| PASSED \| FAILED \| BLOCKED_LIMIT. |
| POST | `/learning/tasks/{task_id}/request-help` | Р—Р°РїСЂРѕСЃ РїРѕРјРѕС‰Рё (body: `student_id`, `message`). |
| POST | `/learning/tasks/{task_id}/hint-events` | Р¤РёРєСЃР°С†РёСЏ РѕС‚РєСЂС‹С‚РёСЏ РїРѕРґСЃРєР°Р·РєРё (СЌС‚Р°Рї 3.6). Body: `student_id`, `attempt_id`, `hint_type`, `hint_index`, `action`, `source`. РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ РІ РѕРєРЅРµ РґРµРґСѓРїР°. |
| POST | `/teacher/task-limits/override` | РџРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёРµ Р»РёРјРёС‚Р° РїРѕРїС‹С‚РѕРє (body: `student_id`, `task_id`, `max_attempts_override`, `updated_by`). |
| GET | `/teacher/help-requests?teacher_id=&status=open\|closed\|all&request_type=manual_help\|blocked_limit\|all&sort=priority\|created_at\|due_at&limit=&offset=` | РЎРїРёСЃРѕРє Р·Р°СЏРІРѕРє РЅР° РїРѕРјРѕС‰СЊ (СЌС‚Р°Рї 3.8/3.8.1/3.9). РџРѕР»СЏ: request_type, auto_created, context, priority, due_at, is_overdue. Р¤РёР»СЊС‚СЂ request_type; sort РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ priority. ACL: РЅР°Р·РЅР°С‡РµРЅРЅС‹Р№ teacher, student_teacher_links, teacher_courses РёР»Рё СЂРѕР»СЊ methodist. |
| GET | `/teacher/help-requests/{request_id}?teacher_id=` | РљР°СЂС‚РѕС‡РєР° Р·Р°СЏРІРєРё (РїРѕР»СЏ СЃРїРёСЃРєР° + message, closed_at, closed_by, resolution_comment, history, priority, due_at, is_overdue). |
| POST | `/teacher/help-requests/{request_id}/close` | Р—Р°РєСЂС‹С‚СЊ Р·Р°СЏРІРєСѓ (body: `closed_by`, `resolution_comment`, РѕРїС†. `lock_token`). РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ. РџСЂРё РЅРµРІР°Р»РёРґРЅРѕРј lock_token вЂ” 409 (СЌС‚Р°Рї 3.9). |
| POST | `/teacher/help-requests/{request_id}/reply` | РћС‚РІРµС‚РёС‚СЊ СЃС‚СѓРґРµРЅС‚Сѓ (body: `teacher_id`, `message`, `close_after_reply`, `idempotency_key`, РѕРїС†. `lock_token`). РЎРѕР·РґР°С‘С‚ СЃРѕРѕР±С‰РµРЅРёРµ РІ messages, РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ Р·Р°РєСЂС‹РІР°РµС‚ Р·Р°СЏРІРєСѓ. РџСЂРё РЅРµРІР°Р»РёРґРЅРѕРј lock_token вЂ” 409 (СЌС‚Р°Рї 3.9). |
| POST | `/teacher/help-requests/claim-next` | Р’Р·СЏС‚СЊ СЃР»РµРґСѓСЋС‰РёР№ РѕС‚РєСЂС‹С‚С‹Р№ help-request (СЌС‚Р°Рї 3.9). Body: `teacher_id`, `request_type` (manual_help\|blocked_limit\|all), `status` (open), `ttl_sec` (30..600), РѕРїС†. `idempotency_key`, `course_id`. РћС‚РІРµС‚: `item`, `lock_token`, `lock_expires_at`, `empty`. |
| POST | `/teacher/help-requests/{request_id}/release` | РћСЃРІРѕР±РѕРґРёС‚СЊ Р±Р»РѕРєРёСЂРѕРІРєСѓ Р·Р°СЏРІРєРё (СЌС‚Р°Рї 3.9). Body: `teacher_id`, `lock_token`. 409 РїСЂРё РЅРµРІРµСЂРЅРѕРј С‚РѕРєРµРЅРµ. РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ: СѓР¶Рµ СЃРІРѕР±РѕРґРЅРѕ вЂ” 200, `released: false`. |
| GET | `/teacher/workload?teacher_id=` | РЎРІРѕРґРєР° РЅР°РіСЂСѓР·РєРё РїСЂРµРїРѕРґР°РІР°С‚РµР»СЏ (СЌС‚Р°Рї 3.9): open_help_requests_total, open_blocked_limit_total, open_manual_help_total, pending_manual_reviews_total, overdue_total. |
| POST | `/teacher/reviews/claim-next` | Р’Р·СЏС‚СЊ СЃР»РµРґСѓСЋС‰РёР№ СЂРµР·СѓР»СЊС‚Р°С‚ РЅР° СЂСѓС‡РЅСѓСЋ РїСЂРѕРІРµСЂРєСѓ (СЌС‚Р°Рї 3.9). Body: `teacher_id`, `ttl_sec`, РѕРїС†. `course_id`, `user_id`, `idempotency_key`. РћС‚РІРµС‚: `item`, `lock_token`, `lock_expires_at`, `empty`. |
| POST | `/teacher/reviews/{result_id}/release` | РћСЃРІРѕР±РѕРґРёС‚СЊ Р±Р»РѕРєРёСЂРѕРІРєСѓ РїСЂРѕРІРµСЂРєРё (СЌС‚Р°Рї 3.9). Body: `teacher_id`, `lock_token`. 409 РїСЂРё РЅРµРІРµСЂРЅРѕРј С‚РѕРєРµРЅРµ. РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ. |
| | | Р’ СЃРїРёСЃРєРµ/РєР°СЂС‚РѕС‡РєРµ help-requests СЃ СЌС‚Р°РїР° 3.9: `priority`, `due_at`, `is_overdue`. Query `sort=priority\|created_at\|due_at`. Close/reply РїСЂРёРЅРёРјР°СЋС‚ РѕРїС†. `lock_token`; РїСЂРё РЅРµРІР°Р»РёРґРЅРѕРј вЂ” 409. |

РћС‚РІРµС‚ `POST /learning/tasks/{task_id}/request-help` СЃ СЌС‚Р°РїР° 3.8 РјРѕР¶РµС‚ СЃРѕРґРµСЂР¶Р°С‚СЊ РѕРїС†РёРѕРЅР°Р»СЊРЅРѕРµ РїРѕР»Рµ `request_id` (ID Р·Р°СЏРІРєРё РІ help_requests). РЎ СЌС‚Р°РїР° 3.8.1 РїСЂРё `GET /learning/next-item` РёР»Рё `GET /learning/tasks/{task_id}/state` СЃ С‚РёРїРѕРј/СЃРѕСЃС‚РѕСЏРЅРёРµРј `blocked_limit`/`BLOCKED_LIMIT` Р·Р°СЏРІРєР° РЅР° РїРѕРјРѕС‰СЊ СЃРѕР·РґР°С‘С‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё (request_type=blocked_limit, auto_created=true, context СЃ attempts_used/attempts_limit_effective). РЎРїРёСЃРѕРє Р·Р°СЏРІРѕРє РјРѕР¶РЅРѕ С„РёР»СЊС‚СЂРѕРІР°С‚СЊ РїРѕ `request_type=manual_help|blocked_limit|all`. РЎ СЌС‚Р°РїР° 3.9: claim-next/release РґР»СЏ help-requests Рё reviews, workload, РїСЂРёРѕСЂРёС‚РµС‚/SLA РІ СЃРїРёСЃРєРµ. Smoke: [smoke-learning-engine-stage3-8-help-requests.md](smoke-learning-engine-stage3-8-help-requests.md), [smoke-learning-engine-stage3-9-next-modes.md](smoke-learning-engine-stage3-9-next-modes.md).

Р’СЃРµ Р·Р°РїСЂРѕСЃС‹ С‚СЂРµР±СѓСЋС‚ `api_key` РІ query. РћС‚РІРµС‚С‹ СЃРѕРґРµСЂР¶Р°С‚ РїРѕР»СЏ, РѕРїРёСЃР°РЅРЅС‹Рµ РІ СЌС‚Р°РїРЅС‹С… РґРѕРєСѓРјРµРЅС‚Р°С… (state: `attempts_used`, `attempts_limit_effective`, `last_attempt_id` Рё С‚.Рґ.).

### POST /learning/tasks/{task_id}/hint-events (СЌС‚Р°Рї 3.6)

Р¤РёРєСЃР°С†РёСЏ РѕС‚РєСЂС‹С‚РёСЏ РїРѕРґСЃРєР°Р·РєРё (text/video) РґР»СЏ Р°РЅР°Р»РёС‚РёРєРё. РРґРµРјРїРѕС‚РµРЅС‚РЅРѕ: РїРѕРІС‚РѕСЂ РІ РѕРєРЅРµ РґРµРґСѓРїР° (5 РјРёРЅ) РІРѕР·РІСЂР°С‰Р°РµС‚ С‚РѕС‚ Р¶Рµ `event_id` Рё `deduplicated: true`.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "student_id": 2,
  "attempt_id": 47,
  "hint_type": "text",
  "hint_index": 0,
  "action": "open",
  "source": "student_execute"
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "ok": true,
  "deduplicated": false,
  "event_id": 123
}
```

**РћС€РёР±РєРё:** `404` вЂ” Р·Р°РґР°РЅРёРµ/СЃС‚СѓРґРµРЅС‚/РїРѕРїС‹С‚РєР° РЅРµ РЅР°Р№РґРµРЅС‹; `409` вЂ” РїРѕРїС‹С‚РєР° РЅРµ РїСЂРёРЅР°РґР»РµР¶РёС‚ СЃС‚СѓРґРµРЅС‚Сѓ РёР»Рё РЅРµ РІ РєРѕРЅС‚РµРєСЃС‚Рµ Р·Р°РґР°РЅРёСЏ/РєСѓСЂСЃР°.

---

## Р­РЅРґРїРѕР№РЅС‚С‹ СЃС‚Р°С‚РёСЃС‚РёРєРё

**Learning Engine V1, СЌС‚Р°Рї 6:** РѕСЃРЅРѕРІРЅРѕР№ СЃС‚Р°С‚СѓСЃ Рё РїСЂРѕРіСЂРµСЃСЃ СЃС‡РёС‚Р°СЋС‚СЃСЏ РїРѕ **РїРѕСЃР»РµРґРЅРµР№ Р·Р°РІРµСЂС€С‘РЅРЅРѕР№ РїРѕРїС‹С‚РєРµ** (last-attempt). РџРѕР»СЏ `average_score`, `total_attempts`, `total_score`, `total_max_score`, `min_score`, `max_score` РѕСЃС‚Р°СЋС‚СЃСЏ РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹РјРё (РїРѕ РІСЃРµРј РїРѕРїС‹С‚РєР°Рј). РџРѕРґСЂРѕР±РЅРµРµ: [last-attempt-statistics-stage6.md](last-attempt-statistics-stage6.md).

### GET /task-results/stats/by-task/{task_id}

РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ Р·Р°РґР°С‡Рµ. РћСЃРЅРѕРІРЅС‹Рµ РїРѕРєР°Р·Р°С‚РµР»Рё: `progress_percent`, `passed_tasks_count`, `failed_tasks_count` (РїРѕ last-attempt).

**РџР°СЂР°РјРµС‚СЂС‹:**
- `task_id` (path, int) - ID Р·Р°РґР°С‡Рё

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "task_id": 1,
  "total_attempts": 10,
  "average_score": 7.5,
  "correct_percentage": 60.0,
  "min_score": 0,
  "max_score": 10,
  "score_distribution": {},
  "progress_percent": 70.0,
  "passed_tasks_count": 7,
  "failed_tasks_count": 3,
  "last_passed_count": 7,
  "last_failed_count": 3,
  "hints_used_count": 12,
  "used_text_hints_count": 8,
  "used_video_hints_count": 4
}
```

**РџРѕР»СЏ СЌС‚Р°РїР° 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` вЂ” С‡РёСЃР»Рѕ СЃРѕР±С‹С‚РёР№ РѕС‚РєСЂС‹С‚РёСЏ РїРѕРґСЃРєР°Р·РѕРє (РїРѕ `learning_events` СЃ `event_type='hint_open'`).

**РћС€РёР±РєРё:**
- `404` - Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР°

---

### GET /task-results/stats/by-course/{course_id}

РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РєСѓСЂСЃСѓ. РћСЃРЅРѕРІРЅС‹Рµ РїРѕРєР°Р·Р°С‚РµР»Рё: `progress_percent`, `passed_tasks_count`, `failed_tasks_count` (РїРѕ last-attempt).

**РџР°СЂР°РјРµС‚СЂС‹:**
- `course_id` (path, int) - ID РєСѓСЂСЃР°

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "course_id": 1,
  "total_attempts": 50,
  "average_score": 75.5,
  "correct_percentage": 65.0,
  "tasks_count": 28,
  "progress_percent": 65.0,
  "passed_tasks_count": 120,
  "failed_tasks_count": 65,
  "hints_used_count": 45,
  "used_text_hints_count": 30,
  "used_video_hints_count": 15
}
```

**РџРѕР»СЏ СЌС‚Р°РїР° 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` вЂ” Р°РіСЂРµРіР°С‚ РїРѕ Р·Р°РґР°С‡Р°Рј РєСѓСЂСЃР°.

**РћС€РёР±РєРё:**
- `404` - РљСѓСЂСЃ РЅРµ РЅР°Р№РґРµРЅ

---

### GET /task-results/stats/by-user/{user_id}

РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ. РћСЃРЅРѕРІРЅРѕР№ РїСЂРѕРіСЂРµСЃСЃ: `progress_percent`, `passed_tasks_count`, `failed_tasks_count`, `current_score`, `current_ratio`, `last_score`, `last_max_score`, `last_ratio` (РїРѕ last-attempt).

**РџР°СЂР°РјРµС‚СЂС‹:**
- `user_id` (path, int) - ID РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "user_id": 10,
  "total_attempts": 5,
  "average_score": 8.0,
  "correct_percentage": 80.0,
  "total_score": 40,
  "total_max_score": 50,
  "completion_percentage": 80.0,
  "progress_percent": 75.0,
  "passed_tasks_count": 3,
  "failed_tasks_count": 1,
  "current_score": 24,
  "current_ratio": 0.8,
  "last_score": 24,
  "last_max_score": 30,
  "last_ratio": 0.8,
  "hints_used_count": 5,
  "used_text_hints_count": 3,
  "used_video_hints_count": 2
}
```

**РџРѕР»СЏ СЌС‚Р°РїР° 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` вЂ” С‡РёСЃР»Рѕ РѕС‚РєСЂС‹С‚РёР№ РїРѕРґСЃРєР°Р·РѕРє РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј.

**РћС€РёР±РєРё:**
- `404` - РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ

---

## Р­РЅРґРїРѕР№РЅС‚С‹ РјР°С‚РµСЂРёР°Р»РѕРІ

API СѓС‡РµР±РЅС‹С… РјР°С‚РµСЂРёР°Р»РѕРІ РєСѓСЂСЃР°: CRUD, СЃРїРёСЃРѕРє РїРѕ РєСѓСЂСЃСѓ, РёР·РјРµРЅРµРЅРёРµ РїРѕСЂСЏРґРєР°, РїРµСЂРµРјРµС‰РµРЅРёРµ, РјР°СЃСЃРѕРІРѕРµ РѕР±РЅРѕРІР»РµРЅРёРµ Р°РєС‚РёРІРЅРѕСЃС‚Рё, РєРѕРїРёСЂРѕРІР°РЅРёРµ РІ РґСЂСѓРіРѕР№ РєСѓСЂСЃ, СЃС‚Р°С‚РёСЃС‚РёРєР°, РёРјРїРѕСЂС‚ РёР· Google Sheets.

**РџРѕР»РЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ:** [API СѓС‡РµР±РЅС‹С… РјР°С‚РµСЂРёР°Р»РѕРІ](materials-api.md)

РћСЃРЅРѕРІРЅС‹Рµ СЌРЅРґРїРѕР№РЅС‚С‹:

- **GET** `/materials/search` вЂ” РїРѕРёСЃРє РјР°С‚РµСЂРёР°Р»РѕРІ РїРѕ title Рё external_uid (РїР°СЂР°РјРµС‚СЂ q РѕР±СЏР·Р°С‚РµР»РµРЅ; course_id РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ вЂ” РїРѕ РІСЃРµРј РєСѓСЂСЃР°Рј РёР»Рё РїРѕ РѕРґРЅРѕРјСѓ)
- **GET** `/courses/{course_id}/materials` вЂ” СЃРїРёСЃРѕРє РјР°С‚РµСЂРёР°Р»РѕРІ РєСѓСЂСЃР° (РїР°СЂР°РјРµС‚СЂ q вЂ” РїРѕРёСЃРє РїРѕ title/external_uid РІ СЂР°РјРєР°С… РєСѓСЂСЃР°; С„РёР»СЊС‚СЂС‹: is_active, type; СЃРѕСЂС‚РёСЂРѕРІРєР°: order_position, title, created_at; РїР°РіРёРЅР°С†РёСЏ skip/limit)
- **POST** `/materials` вЂ” СЃРѕР·РґР°РЅРёРµ РјР°С‚РµСЂРёР°Р»Р°
- **GET** `/materials/{id}` вЂ” РїРѕР»СѓС‡РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р°
- **PATCH** `/materials/{id}` вЂ” РѕР±РЅРѕРІР»РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р° (РїСЂРё РёР·РјРµРЅРµРЅРёРё content РїРµСЂРµРґР°С‘С‚СЃСЏ РїРѕР»РЅС‹Р№ РѕР±СЉРµРєС‚ content РґР»СЏ С‚РёРїР°)
- **DELETE** `/materials/{id}` вЂ” СѓРґР°Р»РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р°
- **POST** `/courses/{course_id}/materials/reorder` вЂ” РёР·РјРµРЅРёС‚СЊ РїРѕСЂСЏРґРѕРє РјР°С‚РµСЂРёР°Р»РѕРІ
- **POST** `/materials/{material_id}/move` вЂ” РїРµСЂРµРјРµСЃС‚РёС‚СЊ РјР°С‚РµСЂРёР°Р» (new_order_position РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ РїСЂРё РїРµСЂРµРЅРѕСЃРµ РІ РґСЂСѓРіРѕР№ РєСѓСЂСЃ вЂ” С‚РѕРіРґР° РІ РєРѕРЅРµС†; course_id РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ вЂ” РїСЂРё null С‚РѕР»СЊРєРѕ СЃРјРµРЅР° РїРѕР·РёС†РёРё)
- **POST** `/courses/{course_id}/materials/bulk-update` вЂ” РјР°СЃСЃРѕРІРѕРµ РѕР±РЅРѕРІР»РµРЅРёРµ is_active
- **POST** `/materials/{material_id}/copy` вЂ” РєРѕРїРёСЂРѕРІР°С‚СЊ РјР°С‚РµСЂРёР°Р» РІ РґСЂСѓРіРѕР№ РєСѓСЂСЃ
- **GET** `/courses/{course_id}/materials/stats` вЂ” СЃС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РјР°С‚РµСЂРёР°Р»Р°Рј РєСѓСЂСЃР°
- **POST** `/materials/upload` вЂ” Р·Р°РіСЂСѓР·РёС‚СЊ С„Р°Р№Р» РґР»СЏ РєРѕРЅС‚РµРЅС‚Р° РјР°С‚РµСЂРёР°Р»Р° (multipart; РІРѕР·РІСЂР°С‰Р°РµС‚ url РґР»СЏ content.sources[0].url РёР»Рё content.url)
- **GET** `/materials/files/{file_id}` вЂ” СЃРєР°С‡Р°С‚СЊ Р·Р°РіСЂСѓР¶РµРЅРЅС‹Р№ С„Р°Р№Р» РјР°С‚РµСЂРёР°Р»Р°
- **POST** `/materials/import/google-sheets` вЂ” РёРјРїРѕСЂС‚ РјР°С‚РµСЂРёР°Р»РѕРІ РёР· Google РўР°Р±Р»РёС†С‹ (РјРЅРѕРіРѕРєСѓСЂСЃРѕРІРѕР№; dry_run РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ)

РўРёРїС‹ РјР°С‚РµСЂРёР°Р»РѕРІ: `text`, `video`, `audio`, `image`, `link`, `pdf`, `office_document`, `script`, `document`. РЎС‚СЂСѓРєС‚СѓСЂР° РїРѕР»СЏ `content` Р·Р°РІРёСЃРёС‚ РѕС‚ С‚РёРїР° вЂ” СЃРј. [materials-api.md](materials-api.md).

---

## Р­РЅРґРїРѕР№РЅС‚С‹ РёРјРїРѕСЂС‚Р°

### POST /tasks/import/google-sheets

РРјРїРѕСЂС‚ Р·Р°РґР°С‡ РёР· Google Sheets.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:**
```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
  "sheet_name": "Р›РёСЃС‚1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": false,
  "column_mapping": {
    "ID": "external_uid",
    "РўРёРї": "type",
    "Р’РѕРїСЂРѕСЃ": "stem"
  }
}
```

**РћС‚РІРµС‚ (200 OK):**
```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

РёР»Рё СЃ РѕС€РёР±РєР°РјРё:

```json
{
  "imported": 8,
  "updated": 0,
  "errors": [
    {
      "row": 3,
      "message": "РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё: Р”Р»СЏ Р·Р°РґР°С‡ С‚РёРїР° SC РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓРєР°Р·Р°РЅ СЂРѕРІРЅРѕ РѕРґРёРЅ РїСЂР°РІРёР»СЊРЅС‹Р№ РІР°СЂРёР°РЅС‚"
    }
  ],
  "total_rows": 10
}
```

**РћС€РёР±РєРё:**
- `400` - РќРµРІРµСЂРЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹ Р·Р°РїСЂРѕСЃР°
- `403` - РќРµРІРµСЂРЅС‹Р№ РёР»Рё РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‰РёР№ API РєР»СЋС‡
- `404` - РљСѓСЂСЃ РёР»Рё СѓСЂРѕРІРµРЅСЊ СЃР»РѕР¶РЅРѕСЃС‚Рё РЅРµ РЅР°Р№РґРµРЅ
- `500` - РћС€РёР±РєР° РїСЂРё С‡С‚РµРЅРёРё Google Sheets

**РџРѕРґСЂРѕР±РЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ:** [РРјРїРѕСЂС‚ РёР· Google Sheets](./import-api-documentation.md)

---

### POST /materials/import/google-sheets

РРјРїРѕСЂС‚ СѓС‡РµР±РЅС‹С… РјР°С‚РµСЂРёР°Р»РѕРІ РёР· Google РўР°Р±Р»РёС†С‹. РњРЅРѕРіРѕРєСѓСЂСЃРѕРІРѕР№ РёРјРїРѕСЂС‚: РєСѓСЂСЃ РґР»СЏ РєР°Р¶РґРѕР№ СЃС‚СЂРѕРєРё Р·Р°РґР°С‘С‚СЃСЏ РїРѕР»РµРј `course_uid` РІ С‚Р°Р±Р»РёС†Рµ. Upsert РїРѕ РїР°СЂРµ (course_id, external_uid). РџРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ `dry_run`.

**РўРµР»Рѕ Р·Р°РїСЂРѕСЃР°:** `spreadsheet_url`, `sheet_name` (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ "Materials"), `dry_run`, `column_mapping` (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ).

**РџРѕРґСЂРѕР±РЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ:** [API СѓС‡РµР±РЅС‹С… РјР°С‚РµСЂРёР°Р»РѕРІ вЂ” РРјРїРѕСЂС‚ РёР· Google Sheets](materials-api.md#РёРјРїРѕСЂС‚-РёР·-google-sheets)

---

## РљРѕРґС‹ РѕС€РёР±РѕРє

### 400 Bad Request

РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё РґР°РЅРЅС‹С…:

```json
{
  "error": "domain_error",
  "detail": "РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё РґР°РЅРЅС‹С… Р·Р°РґР°С‡Рё: Р”Р»СЏ Р·Р°РґР°С‡ С‚РёРїР° SC РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓРєР°Р·Р°РЅ СЂРѕРІРЅРѕ РѕРґРёРЅ РїСЂР°РІРёР»СЊРЅС‹Р№ РІР°СЂРёР°РЅС‚. РЈРєР°Р·Р°РЅРѕ: 2"
}
```

### 403 Forbidden

РќРµРІРµСЂРЅС‹Р№ РёР»Рё РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‰РёР№ API РєР»СЋС‡:

```json
{
  "detail": "Invalid or missing API Key"
}
```

### 404 Not Found

Р РµСЃСѓСЂСЃ РЅРµ РЅР°Р№РґРµРЅ:

```json
{
  "error": "domain_error",
  "detail": "Р—Р°РґР°С‡Р° СЃ СѓРєР°Р·Р°РЅРЅС‹Рј external_uid РЅРµ РЅР°Р№РґРµРЅР°",
  "payload": {
    "external_uid": "TASK-NOT-FOUND"
  }
}
```

### 422 Unprocessable Entity

РћС€РёР±РєР° РІР°Р»РёРґР°С†РёРё Р·Р°РїСЂРѕСЃР° (РЅРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ JSON):

```json
{
  "detail": [
    {
      "loc": ["body", "task_content", "type"],
      "msg": "value is not a valid enumeration member; permitted: 'SC', 'MC', 'SA', 'SA_COM', 'TA'",
      "type": "type_error.enum"
    }
  ]
}
```

### 500 Internal Server Error

Р’РЅСѓС‚СЂРµРЅРЅСЏСЏ РѕС€РёР±РєР° СЃРµСЂРІРµСЂР°:

```json
{
  "detail": "Internal server error"
}
```

---

## Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ СЂРµСЃСѓСЂСЃС‹

- [РџСЂРёРјРµСЂС‹ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёСЏ API](./api-examples.md) - РџРѕРґСЂРѕР±РЅС‹Рµ РїСЂРёРјРµСЂС‹ Р·Р°РїСЂРѕСЃРѕРІ Рё РѕС‚РІРµС‚РѕРІ
- [API СѓРїСЂР°РІР»РµРЅРёСЏ Р·Р°РґР°РЅРёСЏРјРё Рё СЂРµР·СѓР»СЊС‚Р°С‚Р°РјРё СѓС‡РµРЅРёРєРѕРІ](./assignments-and-results-api.md) - РџРѕРґСЂРѕР±РЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ РїРѕ СЌРЅРґРїРѕР№РЅС‚Р°Рј РїРѕРїС‹С‚РѕРє, СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ Р·Р°РґР°РЅРёР№, СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРµ Рё СЃС‚Р°С‚РёСЃС‚РёРєРµ
- [Р”РѕРєСѓРјРµРЅС‚Р°С†РёСЏ РёРјРїРѕСЂС‚Р° РёР· Google Sheets](./import-api-documentation.md) - РџРѕР»РЅРѕРµ СЂСѓРєРѕРІРѕРґСЃС‚РІРѕ РїРѕ РёРјРїРѕСЂС‚Сѓ
- [РљСЂР°С‚РєР°СЏ С€РїР°СЂРіР°Р»РєР° РїРѕ РёРјРїРѕСЂС‚Сѓ](./import-quick-start.md) - Р‘С‹СЃС‚СЂС‹Р№ СЃС‚Р°СЂС‚
- [Swagger UI](http://localhost:8000/docs) - РРЅС‚РµСЂР°РєС‚РёРІРЅР°СЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ API
- [Р¤РѕСЂРјР°С‚С‹ JSONB РїРѕР»РµР№](./api-examples.md#С„РѕСЂРјР°С‚С‹-jsonb-РїРѕР»РµР№) - РћРїРёСЃР°РЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ TaskContent Рё SolutionRules

---

## РР·РјРµРЅРµРЅРёСЏ РІ РІРµСЂСЃРёРё 2.0

### РќРѕРІС‹Рµ СЌРЅРґРїРѕР№РЅС‚С‹:
- вњ… `GET /tasks/by-course/{course_id}` - Р¤РёР»СЊС‚СЂР°С†РёСЏ Р·Р°РґР°С‡ РїРѕ РєСѓСЂСЃСѓ
- вњ… `GET /attempts/by-user/{user_id}` - РџРѕР»СѓС‡РµРЅРёРµ РїРѕРїС‹С‚РѕРє РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
- вњ… `GET /task-results/by-user/{user_id}` - Р РµР·СѓР»СЊС‚Р°С‚С‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
- вњ… `GET /task-results/by-task/{task_id}` - Р РµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ Р·Р°РґР°С‡Рµ
- вњ… `GET /task-results/by-attempt/{attempt_id}` - Р РµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ РїРѕРїС‹С‚РєРµ
- вњ… `POST /task-results/{result_id}/manual-check` - Р СѓС‡РЅР°СЏ РґРѕРѕС†РµРЅРєР°
- вњ… `GET /task-results/stats/by-task/{task_id}` - РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ Р·Р°РґР°С‡Рµ
- вњ… `GET /task-results/stats/by-course/{course_id}` - РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РєСѓСЂСЃСѓ
- вњ… `GET /task-results/stats/by-user/{user_id}` - РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ
- вњ… `POST /tasks/import/google-sheets` - РРјРїРѕСЂС‚ РёР· Google Sheets

### РЈР»СѓС‡С€РµРЅРёСЏ:
- вњ… Р’Р°Р»РёРґР°С†РёСЏ JSONB РїРѕР»РµР№ (TaskContent, SolutionRules)
- вњ… РџРѕРґРґРµСЂР¶РєР° custom scoring mode
- вњ… РџСЂРёРјРµРЅРµРЅРёРµ С€С‚СЂР°С„РѕРІ (penalties)
- вњ… Р“РµРЅРµСЂР°С†РёСЏ РѕР±СЂР°С‚РЅРѕР№ СЃРІСЏР·Рё (feedback)
- вњ… Р’Р°Р»РёРґР°С†РёСЏ РїРѕРїС‹С‚РѕРє РїСЂРё РѕС‚РїСЂР°РІРєРµ РѕС‚РІРµС‚РѕРІ
- вњ… РџРѕРґРґРµСЂР¶РєР° С‚Р°Р№РјР»РёРјРёС‚РѕРІ РґР»СЏ РїРѕРїС‹С‚РѕРє
