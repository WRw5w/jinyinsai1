"""Render a WorkBuddy session .jsonl into a readable Markdown transcript.

The raw .jsonl is the archival copy (it can be handed back to the host).  This
script produces the human-readable companion: one section per turn, with the
user's words, the assistant's prose, and a one-line summary of every tool call
and its result (full tool output is elided -- the raw file has it).

    python -X utf8 tools/render_conversation.py <session.jsonl> --out <out.md>

It is deliberately tolerant: unknown record types are skipped, malformed lines
are counted and reported rather than aborting, and very long payloads are
truncated so the Markdown stays readable.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CN = timezone(timedelta(hours=8))
MAX_TOOL_ARGS = 600
MAX_TOOL_RESULT = 900
MAX_ASSISTANT = 6000

# Host-injected boilerplate: workspace identity files, connector status, memory
# reminders.  It is context, not conversation, and it dominates the byte count of
# the early turns -- strip it so the transcript reads as an actual dialogue.
INJECTED = re.compile(
    r'<(system-reminder|identity_context|product_identity|project_context|'
    r'memory|user_info|connector-status|conversation_history_summary|'
    r'available_deferred_tools|personal_files_safety|response_language)'
    r'[\s\S]*?</\1>',
    re.IGNORECASE,
)


def strip_injected(text):
    """Remove host boilerplate, keeping any real prompt text around it."""
    cleaned = INJECTED.sub('', text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def ts(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, CN).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:                                                    # noqa: BLE001
        return ''


def clip(text, limit):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f'\n\n…（此处省略 {len(text) - limit} 字符，全文见原始 .jsonl）'


def blocks(record):
    """Normalise `content` into a list of (kind, payload) blocks."""
    content = record.get('content')
    if isinstance(content, str):
        return [('text', content)]
    if not isinstance(content, list):
        return []
    out = []
    for item in content:
        if not isinstance(item, dict):
            out.append(('text', str(item)))
            continue
        kind = item.get('type')
        if kind in ('input_text', 'output_text', 'text'):
            out.append(('text', item.get('text', '')))
        elif kind == 'tool_use':
            out.append(('tool_use', item))
        elif kind == 'tool_result':
            out.append(('tool_result', item))
        else:
            out.append(('other', item))
    return out


def render(src: Path, out: Path):
    lines = []
    stats = Counter()
    pending_tools = {}          # tool_use id -> name
    user_turn = 0

    lines.append('# 对话记录：棒材优化复赛（`jinyinsai1` / 鱼不吃猫）')
    lines.append('')
    lines.append(f'- 会话 ID：`{src.stem}`')
    lines.append(f'- 原始归档：`{src.name}`（完整记录，含全部工具输入输出）')
    lines.append(f'- 生成时间：{datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('- 说明：工具调用的**参数与结果已摘要**；完整内容请查原始 `.jsonl`。')
    lines.append('')
    lines.append('---')
    lines.append('')

    for raw in src.open(encoding='utf-8'):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            stats['bad-line'] += 1
            continue
        rtype = record.get('type')
        stats[rtype] += 1

        if rtype == 'message':
            role = record.get('role')
            text_parts, tool_parts = [], []
            for kind, payload in blocks(record):
                if kind == 'text':
                    if payload.strip():
                        text_parts.append(payload.strip())
                elif kind == 'tool_use':
                    name = payload.get('name', '?')
                    pending_tools[payload.get('id')] = name
                    args = payload.get('input')
                    tool_parts.append(
                        f'**[工具] `{name}`**\n\n```json\n'
                        f'{clip(json.dumps(args, ensure_ascii=False, indent=1), MAX_TOOL_ARGS)}\n```')
                elif kind == 'tool_result':
                    body = payload.get('content')
                    if isinstance(body, list):
                        body = '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b)
                                         for b in body)
                    tool_parts.append(f'**[工具结果]**\n\n```\n'
                                      f'{clip(body, MAX_TOOL_RESULT)}\n```')

            if role == 'user' and text_parts:
                body = strip_injected('\n\n'.join(text_parts))
                if not body:
                    stats['user-turn-boilerplate-only'] += 1
                    continue
                user_turn += 1
                lines.append(f'## 第 {user_turn} 轮 — 用户')
                lines.append('')
                lines.append(f'*{ts(record.get("timestamp"))}*')
                lines.append('')
                lines.append(clip(body, MAX_ASSISTANT))
                lines.append('')
            elif role == 'assistant':
                text_parts = [strip_injected(t) for t in text_parts]
                text_parts = [t for t in text_parts if t]
                if text_parts:
                    lines.append('### 助手')
                    lines.append('')
                    lines.append(clip('\n\n'.join(text_parts), MAX_ASSISTANT))
                    lines.append('')
                if tool_parts:
                    lines.append('<details><summary>工具调用 '
                                 f'({len(tool_parts)})</summary>')
                    lines.append('')
                    lines.extend(tool_parts)
                    lines.append('')
                    lines.append('</details>')
                    lines.append('')

        elif rtype == 'reasoning':
            pass                                    # internal, not part of the record
        elif rtype == 'ai-title':
            title = record.get('title') or record.get('content')
            if title:
                lines.append(f'> **会话标题**：{title}')
                lines.append('')
        elif rtype in ('function_call', 'function_call_result'):
            stats[f'{rtype}-folded'] += 1           # already mirrored inside messages

    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'wrote {out} ({out.stat().st_size} bytes, '
          f'{len(lines)} lines, {user_turn} user turns)')
    print('record types:', dict(stats))
    return user_turn


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    render(Path(args.source), Path(args.out))


if __name__ == '__main__':
    main()
