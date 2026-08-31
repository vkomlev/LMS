/**
 * tsk-745: мобильный аудит трёх онбордингов.
 *
 * Открывает под живым профилем ученика все материалы и по одному заданию каждого
 * типа на ширине телефона и МЕРЯЕТ переполнение, а не смотрит на глаз: у каждого
 * элемента внутри контента сравнивается его правый край с правым краем колонки.
 * Так ловится ровно тот класс, что нашёлся у таблицы баллов — часть содержимого
 * есть в DOM, но за экран не влезает, а прокрутки нет.
 *
 * Профиль копируется во временный каталог (как в live-browse.mjs): Chromium
 * держит файловый замок, параллельные прогоны иначе мешают друг другу.
 *
 * Запуск из D:\Work\SPW:
 *   node <путь>/mobile_audit.mjs 390
 */
import pw from "file:///D:/Work/SPW/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright/index.js";
const { chromium } = pw;
import { cpSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";

const WIDTH = Number(process.argv[2] || 390);
const HEIGHT = 844;
const BASE = "https://learn.victor-komlev.ru";
const PROFILE = join(homedir(), ".claude-live-profile");
const OUT = join(process.cwd(), ".qa-artifacts", `mobile-${WIDTH}-${Date.now()}`);

/** Курс -> материалы (id) и задания для проверки. Взято из прода. */
const PAGES = [
  ["список курсов", "/courses"],
  ["общий: курс", "/courses/id-1467"],
  ["общий: м0 зачем", "/courses/id-1467/material/3878"],
  ["общий: кто вас учит", "/courses/id-1467/material/3885"],
  ["общий: кабинет", "/courses/id-1467/material/3879"],
  ["общий: задания", "/courses/id-1467/material/3880"],
  ["общий: помощь", "/courses/id-1467/material/3881"],
  ["общий: занятия", "/courses/id-1467/material/3882"],
  ["общий: дома", "/courses/id-1467/material/3883"],
  ["общий: оплата", "/courses/id-1467/material/3884"],
  ["общий: задание SC", "/courses/id-1467/task/id-10168"],
  ["общий: задание SA", "/courses/id-1467/task/id-10170"],
  ["общий: задание MC", "/courses/id-1467/task/id-10171"],
  ["ЕГЭ: курс", "/courses/id-1474"],
  ["ЕГЭ: м0 зачем", "/courses/id-1474/material/3886"],
  ["ЕГЭ: экзамен", "/courses/id-1474/material/3887"],
  ["ЕГЭ: баллы", "/courses/id-1474/material/3888"],
  ["ЕГЭ: инструменты", "/courses/id-1474/material/3889"],
  ["ЕГЭ: шаблоны", "/courses/id-1474/material/3890"],
  ["ЕГЭ: этапы", "/courses/id-1474/material/3891"],
  ["ЕГЭ: отработка", "/courses/id-1474/material/3892"],
  ["ОГЭ: курс", "/courses/id-1481"],
  ["ОГЭ: м0 зачем", "/courses/id-1481/material/3893"],
  ["ОГЭ: экзамен", "/courses/id-1481/material/3894"],
  ["ОГЭ: оценки", "/courses/id-1481/material/3895"],
  ["ОГЭ: задания", "/courses/id-1481/material/3896"],
  ["ОГЭ: практика", "/courses/id-1481/material/3897"],
  ["ОГЭ: подготовка", "/courses/id-1481/material/3898"],
  ["ОГЭ: как учиться", "/courses/id-1481/material/3899"],
];

const work = join(tmpdir(), `mobile-audit-${Date.now()}`);
cpSync(PROFILE, work, {
  recursive: true,
  filter: (src) => !/[\\/](Cache|Code Cache|GPUCache|ShaderCache)([\\/]|$)/i.test(src),
});
mkdirSync(OUT, { recursive: true });

const ctx = await chromium.launchPersistentContext(work, {
  headless: true,
  viewport: { width: WIDTH, height: HEIGHT },
  locale: "ru-RU",
  args: ["--disable-blink-features=AutomationControlled"],
});
const page = await ctx.newPage();
const report = [];

for (const [name, url] of PAGES) {
  try {
    await page.goto(BASE + url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(2500);

    const found = await page.evaluate(() => {
      const docW = document.documentElement.clientWidth;
      // Горизонтальная прокрутка всей страницы — сама по себе дефект на телефоне.
      const pageScroll = document.documentElement.scrollWidth - docW;
      const bad = [];
      const seen = new Set();
      for (const el of document.querySelectorAll("main *")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const over = Math.round(r.right - docW);
        // 2 px — запас на скругления и субпиксели.
        if (over > 2) {
          const tag = el.tagName.toLowerCase();
          // Интересует самый ВНЕШНИЙ вылезающий элемент, а не каждый его потомок.
          if (el.parentElement && seen.has(el.parentElement)) continue;
          seen.add(el);
          bad.push({
            tag,
            over,
            text: (el.textContent || "").trim().slice(0, 60),
          });
        }
      }
      // Отдельно: таблица шире своей колонки — та самая находка с баллами.
      const tables = [...document.querySelectorAll("main table")].map((t) => ({
        cols: t.querySelector("tr") ? t.querySelector("tr").children.length : 0,
        scrollW: t.scrollWidth,
        clientW: t.clientWidth,
        parentW: t.parentElement ? t.parentElement.clientWidth : 0,
      }));
      return { pageScroll, bad: bad.slice(0, 8), tables, title: document.title };
    });

    const shot = join(OUT, name.replace(/[^a-zA-Zа-яА-Я0-9]+/g, "-") + ".png");
    await page.screenshot({ path: shot, fullPage: true });

    const tableProblems = found.tables.filter((t) => t.scrollW > t.clientW + 2);
    report.push({ name, url, ...found, tableProblems });

    const flag =
      found.pageScroll > 2 || found.bad.length || tableProblems.length ? "ПРОБЛЕМА" : "ок";
    console.log(
      `${flag.padEnd(9)} ${name.padEnd(24)} прокрутка ${found.pageScroll}px, ` +
        `вылезает ${found.bad.length}, таблиц-шире ${tableProblems.length}` +
        (found.tables.length ? ` (таблиц ${found.tables.length}, колонок ${found.tables.map((t) => t.cols).join("/")})` : "")
    );
    for (const b of found.bad) console.log(`             <${b.tag}> +${b.over}px  ${b.text}`);
  } catch (e) {
    console.log(`ОШИБКА    ${name}: ${e.message.split("\n")[0]}`);
    report.push({ name, url, error: e.message.split("\n")[0] });
  }
}

writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 2), "utf-8");
console.log(`\nСкриншоты и отчёт: ${OUT}`);
await ctx.close();
