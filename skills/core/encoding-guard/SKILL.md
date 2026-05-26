---
name: encoding-guard
description: Prevent and fix text encoding issues (UTF-8 corruption, mojibake, mixed encodings) in markdown, docs, review artifacts, config, and rule files. Use before and after editing text files, especially when working through PowerShell, git diff pipelines, or bulk file updates.
---

# Encoding Guard

Use this skill whenever text files may be affected by encoding drift, including `reviews/*.md` and `reviews/*.diff`.

## Standard workflow
1. Set explicit UTF-8 in the shell before reading or writing non-ASCII text.
2. Run `scripts/check_encoding.py` on target files (`docs`, `reviews`, skills, rules, prompts).
3. Classify each finding as `clean`, `BOM`, `mojibake`, or `mixed`.
4. If status is clean/BOM, proceed with edits and save as UTF-8 without BOM unless a runtime requires BOM.
5. Run `scripts/check_encoding.py` again after edits.
6. If mojibake is detected, stop additional edits until recovered.

## Required safety rules
- Treat UTF-8 as the only target encoding for docs and rules.
- Do not use lossy recovery (`errors='ignore'`) as a final fix.
- Prefer restore-from-last-good-state over speculative conversion.
- For PowerShell pipelines, use UTF-8 output explicitly.
- Do not trust terminal rendering for Cyrillic; verify bytes or hashes when parity matters.
- For CLI Python on Windows, set `PYTHONUTF8=1` when scripts read/write non-ASCII files.

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

## Recovery preference
Use the last known good source first:

1. `git log --all --oneline -- <file>`
2. `git show <sha>:<file>` to inspect or restore clean bytes
3. Re-apply intended edits on the clean base

If there is no git history, create a backup before any conversion and verify restored text with another source or human-readable sample.
