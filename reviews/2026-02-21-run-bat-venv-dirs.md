# run.bat: поддержка .venv и venv без пересоздания

**Дата:** 2026-02-21

## Контекст

Обновлён `run.bat`: при наличии виртуальной среды в `.venv` или в `venv` используется существующая среда; новая создаётся только если нет ни одной из двух.

## Изменения

- Проверка наличия `.venv\Scripts\activate.bat` и `venv\Scripts\activate.bat`.
- Переменная `ACTIVATE` — путь к найденному скрипту активации.
- Создание venv только при `if not defined ACTIVATE`.
- Вызов `call "%ACTIVATE%"` вместо жёстко заданного `venv\Scripts\activate.bat`.

Начало diff:

```diff
diff --git a/run.bat b/run.bat
--- a/run.bat
+++ b/run.bat
@@ -5,7 +5,11 @@ setlocal
 set "ROOT=%~dp0"
 cd /d "%ROOT%"
 
-if not exist "venv\Scripts\activate.bat" (
+set "ACTIVATE="
+if exist ".venv\Scripts\activate.bat" set "ACTIVATE=.venv\Scripts\activate.bat"
+if exist "venv\Scripts\activate.bat"     set "ACTIVATE=venv\Scripts\activate.bat"
+
+if not defined ACTIVATE (
     ...
+    set "ACTIVATE=venv\Scripts\activate.bat"
 )
 ...
-call venv\Scripts\activate.bat
+call "%ACTIVATE%"
```

Полный diff: [2026-02-21-run-bat-venv-dirs.diff](2026-02-21-run-bat-venv-dirs.diff)
