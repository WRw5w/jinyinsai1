#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把一份 Markdown（本项目用到的子集）转成 HTML，再用 Edge/Chrome headless 打成 PDF。

为什么不用 reportlab/weasyprint：本机 venv 的 python.exe 被 Device Guard 拦住，
而 reportlab 没装；Edge 是本机既有浏览器，headless --print-to-pdf 对中文排版
（内嵌 Microsoft YaHei Type0 字体）已实测正确。

支持的 Markdown 子集（够本项目的技术报告用，不做通用实现）：
  #..#### 标题、段落、- 无序列表、1. 有序列表、| 表格、``` 代码块、--- 分隔线，
  行内 **粗体** / `代码` / [文字](链接)。

用法:
  report_md_to_pdf.py <input.md> [--out out.pdf] [--html out.html] [--title 标题]
"""
from __future__ import annotations

import argparse
import html as html_mod
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EDGE_CANDIDATES = [
    r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    r'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    r'C:/Program Files/Google/Chrome/Application/chrome.exe',
]

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       font-size: 10.5pt; line-height: 1.75; color: #1a1a1a; }
h1 { font-size: 19pt; border-bottom: 2px solid #333; padding-bottom: 6px;
     margin: 0 0 4mm; }
h2 { font-size: 14pt; margin: 8mm 0 3mm; border-left: 4px solid #2f6fbb;
     padding-left: 7px; }
h3 { font-size: 12pt; margin: 6mm 0 2mm; color: #24487a; }
h4 { font-size: 11pt; margin: 5mm 0 2mm; }
p  { margin: 0 0 2.6mm; text-align: justify; }
ul, ol { margin: 0 0 2.6mm; padding-left: 6mm; }
li { margin-bottom: 1mm; }
table { border-collapse: collapse; width: 100%; margin: 3mm 0 4mm;
        font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 3px 6px; text-align: left;
         vertical-align: top; }
th { background: #eef3fa; font-weight: bold; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9.5pt;
       background: #f4f4f4; padding: 0 3px; border-radius: 2px; }
pre { background: #f7f7f7; border: 1px solid #ddd; border-radius: 3px;
      padding: 3mm; overflow-x: auto; }
pre code { background: none; padding: 0; font-size: 9pt; line-height: 1.5; }
hr { border: none; border-top: 1px solid #bbb; margin: 6mm 0; }
strong { color: #101820; }
a { color: #2f6fbb; text-decoration: none; }
"""


def inline(text: str) -> str:
    """行内标记。先转义，再替换 —— 顺序反了会把生成的标签再转义一遍。"""
    out = html_mod.escape(text, quote=False)
    out = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'`(.+?)`', r'<code>\1</code>', out)
    out = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', out)
    return out


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip('|').split('|')]


def is_sep(row: str) -> bool:
    return bool(re.fullmatch(r'\|[\s:\-|]+\|', row.strip()))


def convert(md: str) -> str:
    lines = md.splitlines()
    body: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        raw = line.rstrip()

        if raw.startswith('```'):
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].startswith('```'):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过收尾的 ```
            body.append('<pre><code>' +
                        html_mod.escape('\n'.join(buf), quote=False) +
                        '</code></pre>')
            continue

        if not raw.strip():
            i += 1
            continue

        m = re.match(r'^(#{1,4})\s+(.*)$', raw)
        if m:
            lvl = len(m.group(1))
            body.append(f'<h{lvl}>{inline(m.group(2))}</h{lvl}>')
            i += 1
            continue

        if re.fullmatch(r'-{3,}', raw.strip()):
            body.append('<hr>')
            i += 1
            continue

        # 表格：本行与下一行都是 | 开头，且下一行是分隔行
        if raw.strip().startswith('|') and i + 1 < len(lines) \
                and is_sep(lines[i + 1]):
            head = cells(raw)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(cells(lines[i]))
                i += 1
            th = ''.join(f'<th>{inline(c)}</th>' for c in head)
            trs = ''.join('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r)
                          + '</tr>' for r in rows)
            body.append(f'<table><thead><tr>{th}</tr></thead>'
                        f'<tbody>{trs}</tbody></table>')
            continue

        if re.match(r'^\s*[-*]\s+', raw):
            items: list[str] = []
            while i < len(lines) and re.match(r'^\s*[-*]\s+', lines[i]):
                items.append(re.sub(r'^\s*[-*]\s+', '', lines[i]))
                i += 1
            body.append('<ul>' + ''.join(f'<li>{inline(t)}</li>'
                                         for t in items) + '</ul>')
            continue

        if re.match(r'^\s*\d+\.\s+', raw):
            items = []
            while i < len(lines) and re.match(r'^\s*\d+\.\s+', lines[i]):
                items.append(re.sub(r'^\s*\d+\.\s+', '', lines[i]))
                i += 1
            body.append('<ol>' + ''.join(f'<li>{inline(t)}</li>'
                                         for t in items) + '</ol>')
            continue

        para: list[str] = []
        while i < len(lines) and lines[i].strip() \
                and not re.match(r'^(#{1,4}\s|\s*[-*]\s|\s*\d+\.\s|\||```)',
                                 lines[i]) \
                and not re.fullmatch(r'-{3,}', lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        body.append('<p>' + inline(' '.join(para)) + '</p>')

    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<title>' + 'report' + '</title><style>' + CSS +
            '</style></head><body>' + '\n'.join(body) + '</body></html>')


def find_browser() -> str:
    env = os.environ.get('AIC_REPORT_BROWSER')
    if env and Path(env).exists():
        return env
    for c in EDGE_CANDIDATES:
        if Path(c).exists():
            return c
    raise SystemExit('no Edge/Chrome found; set AIC_REPORT_BROWSER to the exe')


def to_pdf(browser: str, html_path: Path, pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    uri = html_path.resolve().as_uri()
    cmd = [browser, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
           f'--print-to-pdf={pdf_path.resolve()}', uri]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f'browser produced no PDF (rc={r.returncode})')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('markdown')
    ap.add_argument('--out', default=None, help='PDF 输出路径（默认同名 .pdf）')
    ap.add_argument('--html', default=None, help='保留中间 HTML 到该路径')
    args = ap.parse_args()

    src = Path(args.markdown)
    if not src.exists():
        raise SystemExit(f'no such markdown: {src}')
    md = src.read_text(encoding='utf-8')
    doc = convert(md)

    pdf_path = Path(args.out) if args.out else src.with_suffix('.pdf')
    if args.html:
        hp = Path(args.html)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(doc, encoding='utf-8')
    else:
        tmp = tempfile.NamedTemporaryFile('w', suffix='.html', delete=False,
                                          encoding='utf-8')
        tmp.write(doc)
        tmp.close()
        hp = Path(tmp.name)

    to_pdf(find_browser(), hp, pdf_path)
    print(f'{pdf_path}  {pdf_path.stat().st_size} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
