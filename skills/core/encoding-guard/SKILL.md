---
name: encoding-guard
description: Prevent and fix text encoding issues (UTF-8 corruption, mojibake, mixed encodings) in markdown, docs, review artifacts, config, and rule files. Use before and after editing text files, especially when working through PowerShell, git diff pipelines, or bulk file updates.
---

# Encoding Guard

Use this skill whenever text files may be affected by encoding drift, including `reviews/*.md` and `reviews/*.diff`.

## Standard workflow
1. Run `scripts/check_encoding.py` on target files (`docs` and `reviews`).
2. If status is clean, proceed with edits.
3. Save files as UTF-8 only.
4. Run `scripts/check_encoding.py` again after edits.
5. If mojibake is detected, do not continue with additional edits until recovered.

## Required safety rules
- Treat UTF-8 as the only target encoding for docs and rules.
- Do not use lossy recovery (`errors='ignore'`) as a final fix.
- Prefer restore-from-last-good-state over speculative conversion.
- For PowerShell pipelines, use UTF-8 output explicitly.

## PowerShell guidance
Before bulk text operations:
```powershell
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

When writing files:
```powershell
Set-Content -Encoding UTF8
Out-File -Encoding utf8
```

## Quick commands
Check one file:
```powershell
python skills/encoding-guard/scripts/check_encoding.py --path "Docs/ai-booster/onboarding-phase1.md"
```

Check a directory recursively:
```powershell
python skills/encoding-guard/scripts/check_encoding.py --path "Docs" --recursive
```

Check review artifacts recursively:
```powershell
python skills/encoding-guard/scripts/check_encoding.py --path "reviews" --recursive
```

## When to escalate
Escalate to manual recovery when:
- mixed encodings are present in one file,
- automatic heuristics give ambiguous results,
- critical docs/rules are affected across multiple files.
