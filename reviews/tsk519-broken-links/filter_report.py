"""Фильтр отчёта аудита: оставить только ссылки на ФАЙЛЫ (картинки, вложения)."""
import json
import re

rows = json.load(open("/tmp/broken_links_report.json", encoding="utf-8"))
EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|bmp|pdf|mp4|webm|mp3|zip|rar|7z|docx|xlsx|pptx|csv|txt|py)$",
    re.I,
)
files = [r for r in rows if r["url"].startswith("LOCALFILE:") or EXT.search(r["url"].split("?")[0])]

print("всего записей не-200:", len(rows), "| из них файловых:", len(files))
print("\n--- ФАЙЛОВЫЕ ССЫЛКИ, не отдающие 200 ---")
for r in sorted(files, key=lambda x: -x["active"]):
    print("[{}] active={}/{} {}".format(r["code"], r["active"], r["refs"], r["url"][:130]))
    if r["active"]:
        print("     владельцы:", r["owners_active"][:6])

codes = {}
for r in rows:
    codes[r["code"]] = codes.get(r["code"], 0) + 1
print("\nраспределение кодов (все не-200):", dict(sorted(codes.items())))

hosts = {}
for r in files:
    host = r["url"].split("/")[2] if r["url"].startswith("http") else "LOCAL"
    hosts[host] = hosts.get(host, 0) + 1
print("файловые по хостам:", hosts)
