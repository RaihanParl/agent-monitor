#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

BASE = Path('/Users/mraihanparlaungan')
HERMES_DB = BASE / '.hermes/state.db'
OPENCODE_DB = BASE / '.local/share/opencode/opencode.db'
CURSOR_DIR = BASE / '.cursor'
HOST = os.environ.get('AGENT_MIRROR_HOST', '0.0.0.0')
PORT = int(os.environ.get('AGENT_MIRROR_PORT', '8787'))
REFRESH_MS = int(os.environ.get('AGENT_MIRROR_REFRESH_MS', '2500'))
MAX_SESSIONS = int(os.environ.get('AGENT_MIRROR_MAX_SESSIONS', '8'))
MAX_MESSAGES = int(os.environ.get('AGENT_MIRROR_MAX_MESSAGES', '64'))
MAX_TERMINAL_LINES = int(os.environ.get('AGENT_MIRROR_MAX_TERMINAL_LINES', '80'))


def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def ts_to_local(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if ts > 10_000_000_000:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds')


def safe_json_loads(text, default=None):
    if text in (None, ''):
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def clip(text, limit=220):
    if text is None:
        return ''
    text = str(text).replace('\r', '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def pretty_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def clean_text(text):
    if text is None:
        return ''
    text = str(text).replace('\r', '')
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines)


def role_label(role, kind='message'):
    role = (role or kind or 'event').lower()
    mapping = {
        'assistant': 'Agent response',
        'user': 'User message',
        'tool': 'Tool call',
        'tool_result': 'Tool result',
        'reasoning': 'Agent thought',
        'terminal': 'Terminal output',
        'system': 'System event',
    }
    return mapping.get(role, role.replace('_', ' ').title())


def tool_input_summary(content):
    if isinstance(content, dict):
        if 'command' in content:
            return str(content.get('command'))
        return pretty_json(content)
    return str(content or '')


def extract_chat_text(content):
    if content is None:
        return ''
    if isinstance(content, str):
        parsed = safe_json_loads(content)
        if parsed is None:
            return clean_text(content)
        return extract_chat_text(parsed)
    if isinstance(content, list):
        parts = []
        for item in content:
            text = extract_chat_text(item)
            if text:
                parts.append(text)
        return clean_text('\n\n'.join(parts))
    if isinstance(content, dict):
        if 'text' in content and isinstance(content['text'], str):
            return clean_text(content['text'])
        if 'content' in content:
            return extract_chat_text(content['content'])
        return ''
    return ''


def extract_hermes_text(content):
    if content is None:
        return ''
    if isinstance(content, str):
        parsed = safe_json_loads(content)
        if parsed is None:
            return clean_text(content)
        return extract_hermes_text(parsed)
    if isinstance(content, list):
        parts = []
        for item in content:
            text = extract_hermes_text(item)
            if text:
                parts.append(text)
        return clean_text('\n\n'.join(parts))
    if isinstance(content, dict):
        if 'text' in content and isinstance(content['text'], str):
            return clean_text(content['text'])
        if 'content' in content:
            return extract_hermes_text(content['content'])
        if 'command' in content:
            return clean_text(str(content['command']))
        if 'output' in content:
            return clean_text(str(content['output']))
        if 'stdout' in content:
            return clean_text(str(content['stdout']))
        if 'stderr' in content:
            return clean_text(str(content['stderr']))
        if 'result' in content:
            return extract_hermes_text(content['result'])
        return clean_text(pretty_json(content))
    return clean_text(str(content))


def tail_text(path: Path, max_lines=MAX_TERMINAL_LINES):
    try:
        text = path.read_text(errors='replace')
    except Exception as exc:
        return {'path': str(path), 'error': str(exc), 'lines': []}
    lines = text.splitlines()
    return {
        'path': str(path),
        'lines': lines[-max_lines:],
        'line_count': len(lines),
        'modified_at': ts_to_local(path.stat().st_mtime),
    }


def run_cmd(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except subprocess.CalledProcessError as exc:
        return (exc.output or '').strip()
    except Exception as exc:
        return str(exc)


def get_processes():
    cmd = [
        'bash', '-lc',
        "ps aux | egrep 'hermes|opencode|Cursor' | egrep -v 'egrep|Cursor Helper \\\\(Plugin\\\\)'",
    ]
    raw = run_cmd(cmd)
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        rows.append({
            'user': parts[0],
            'pid': parts[1],
            'cpu': parts[2],
            'mem': parts[3],
            'started': parts[8],
            'time': parts[9],
            'command': parts[10],
        })
    return rows


def get_tmux_sessions():
    raw = run_cmd(['bash', '-lc', 'tmux ls 2>/dev/null || true'])
    sessions = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        name = line.split(':', 1)[0]
        sessions.append({'name': name, 'raw': line})
    return sessions


def summarize_hermes_message(message):
    content = message.get('content')
    if message.get('tool_name'):
        return 'Tool: ' + message['tool_name']
    body = extract_hermes_text(content)
    if body:
        return clip(body, 140)
    if message.get('reasoning'):
        return 'Thought: ' + clip(message['reasoning'], 120)
    if message.get('reasoning_content'):
        return 'Thought: ' + clip(message['reasoning_content'], 120)
    return message.get('role', 'message')


def fetch_hermes():
    data = {'ok': HERMES_DB.exists(), 'db_path': str(HERMES_DB), 'sessions': [], 'errors': []}
    if not HERMES_DB.exists():
        data['errors'].append('Hermes DB not found')
        return data
    try:
        conn = sqlite3.connect(HERMES_DB)
        conn.row_factory = sqlite3.Row
        sessions = conn.execute(
            """
            select id, title, source, cwd, started_at, ended_at, message_count, tool_call_count,
                   estimated_cost_usd, actual_cost_usd, model
            from sessions
            order by started_at desc
            limit ?
            """,
            (MAX_SESSIONS,),
        ).fetchall()
        for s in sessions:
            messages = conn.execute(
                """
                select id, role, content, reasoning, reasoning_content, tool_name, timestamp, finish_reason
                from messages
                where session_id=? and active=1
                order by id desc
                limit ?
                """,
                (s['id'], MAX_MESSAGES),
            ).fetchall()
            messages = list(reversed(messages))
            parsed_messages = []
            for m in messages:
                parsed_messages.append({
                    'id': m['id'],
                    'role': m['role'],
                    'tool_name': m['tool_name'],
                    'timestamp': ts_to_local(m['timestamp']),
                    'finish_reason': m['finish_reason'],
                    'content': m['content'],
                    'content_json': safe_json_loads(m['content']),
                    'reasoning': m['reasoning'],
                    'reasoning_content': m['reasoning_content'],
                })
            latest = parsed_messages[-1] if parsed_messages else None
            data['sessions'].append({
                'id': s['id'],
                'title': s['title'],
                'source': s['source'],
                'cwd': s['cwd'],
                'started_at': ts_to_local(s['started_at']),
                'ended_at': ts_to_local(s['ended_at']),
                'message_count': s['message_count'],
                'tool_call_count': s['tool_call_count'],
                'estimated_cost_usd': s['estimated_cost_usd'],
                'actual_cost_usd': s['actual_cost_usd'],
                'model': s['model'],
                'latest_preview': summarize_hermes_message(latest) if latest else '',
                'messages': parsed_messages,
            })
    except Exception as exc:
        data['errors'].append(str(exc))
    return data


def opencode_part_preview(part):
    data = part.get('data') or {}
    ptype = data.get('type', 'part')
    if ptype == 'text':
        return clip(data.get('text', ''), 140)
    if ptype == 'reasoning':
        return 'Thought: ' + clip(data.get('text', ''), 120)
    if ptype == 'tool':
        tool = data.get('tool', 'tool')
        state = (data.get('state') or {}).get('status', '')
        cmd = ((data.get('state') or {}).get('input') or {}).get('command', '')
        return clip(f'Tool {tool} {state}: {cmd}', 140)
    if ptype == 'step-start':
        return 'Step started'
    if ptype == 'step-finish':
        return 'Step finished: ' + clip(data.get('reason', ''), 80)
    return clip(json.dumps(data, ensure_ascii=False), 140)


def fetch_opencode():
    data = {'ok': OPENCODE_DB.exists(), 'db_path': str(OPENCODE_DB), 'sessions': [], 'errors': []}
    if not OPENCODE_DB.exists():
        data['errors'].append('OpenCode DB not found')
        return data
    try:
        conn = sqlite3.connect(OPENCODE_DB)
        conn.row_factory = sqlite3.Row
        sessions = conn.execute(
            """
            select id, title, directory, agent, model, time_created, time_updated, cost,
                   tokens_input, tokens_output, tokens_reasoning
            from session
            order by time_updated desc
            limit ?
            """,
            (MAX_SESSIONS,),
        ).fetchall()
        for s in sessions:
            messages = conn.execute(
                """
                select id, time_created, data
                from message
                where session_id=?
                order by time_created desc, id desc
                limit ?
                """,
                (s['id'], MAX_MESSAGES),
            ).fetchall()
            messages = list(reversed(messages))
            parsed_messages = []
            latest_preview = ''
            for m in messages:
                mdata = safe_json_loads(m['data'], {}) or {}
                parts = conn.execute(
                    """
                    select id, time_created, data
                    from part
                    where message_id=?
                    order by time_created asc, id asc
                    """,
                    (m['id'],),
                ).fetchall()
                parsed_parts = []
                for p in parts:
                    pdata = safe_json_loads(p['data'], {'raw': p['data']})
                    parsed_parts.append({
                        'id': p['id'],
                        'time_created': ts_to_local(p['time_created']),
                        'data': pdata,
                    })
                if parsed_parts:
                    latest_preview = opencode_part_preview(parsed_parts[-1])
                parsed_messages.append({
                    'id': m['id'],
                    'time_created': ts_to_local(m['time_created']),
                    'meta': mdata,
                    'parts': parsed_parts,
                })
            data['sessions'].append({
                'id': s['id'],
                'title': s['title'],
                'directory': s['directory'],
                'agent': s['agent'],
                'model': safe_json_loads(s['model'], s['model']),
                'time_created': ts_to_local(s['time_created']),
                'time_updated': ts_to_local(s['time_updated']),
                'cost': s['cost'],
                'tokens_input': s['tokens_input'],
                'tokens_output': s['tokens_output'],
                'tokens_reasoning': s['tokens_reasoning'],
                'latest_preview': latest_preview,
                'messages': parsed_messages,
            })
    except Exception as exc:
        data['errors'].append(str(exc))
    return data


def find_cursor_transcripts():
    results = []
    base = CURSOR_DIR / 'projects'
    if not base.exists():
        return results
    for path in base.glob('*/agent-transcripts/*/*.jsonl'):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        results.append((stat.st_mtime, path))
    return [p for _, p in sorted(results, key=lambda item: item[0], reverse=True)[:MAX_SESSIONS]]


def cursor_entry_preview(entry):
    if 'message' in entry:
        content = ((entry.get('message') or {}).get('content') or [])
        pieces = []
        for item in content:
            if item.get('type') == 'text' and item.get('text'):
                text = clean_cursor_visible_text(item.get('text'))
                if text:
                    pieces.append(text)
        if pieces:
            return clip(' '.join(pieces), 140)
    if entry.get('type') == 'turn_ended':
        return 'Turn ended: ' + clip(entry.get('status', ''), 60)
    return clip(json.dumps(entry, ensure_ascii=False), 140)


def is_image_value(value):
    if not isinstance(value, str) or not value:
        return False
    lower = value.lower()
    if lower.startswith(('data:image/', 'http://', 'https://')):
        return any(token in lower for token in ('image', '.png', '.jpg', '.jpeg', '.gif', '.webp'))
    return lower.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))


def media_url(value):
    if not isinstance(value, str) or not value:
        return ''
    if value.startswith(('http://', 'https://', 'data:image/')):
        return value
    path = Path(value).expanduser()
    if path.exists() and path.is_file() and is_image_value(str(path)):
        return '/media?path=' + quote(str(path))
    return ''


def extract_media_assets(content):
    found = []
    seen = set()

    def add(value, label='image'):
        url = media_url(value)
        if url and url not in seen:
            seen.add(url)
            found.append({'type': 'image', 'url': url, 'label': label})

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            kind = str(value.get('type') or '').lower()
            if 'image' in kind:
                for key in ('path', 'file_path', 'image_path', 'url', 'src', 'source', 'data'):
                    candidate = value.get(key)
                    if isinstance(candidate, str):
                        add(candidate, value.get('name') or value.get('filename') or 'image')
                    elif isinstance(candidate, dict):
                        walk(candidate)
                nested_url = value.get('image_url')
                if isinstance(nested_url, str):
                    add(nested_url, 'image')
                elif isinstance(nested_url, dict):
                    walk(nested_url)
            for key in ('image', 'image_url', 'file', 'asset', 'attachment'):
                if key in value:
                    walk(value[key])
            for key in ('text', 'content'):
                if isinstance(value.get(key), str):
                    walk(value[key])
            return
        if isinstance(value, str):
            if is_image_value(value):
                add(value, 'image')
            for match in re.findall(r'(/Users/[^\s<>"\']+\.(?:png|jpg|jpeg|gif|webp))', value, flags=re.IGNORECASE):
                add(match, Path(match).name)

    walk(content)
    return found


def parse_cursor_jsonl(path: Path):
    try:
        lines = path.read_text(errors='replace').splitlines()
    except Exception as exc:
        return {'path': str(path), 'error': str(exc), 'messages': []}
    items = []
    latest_preview = ''
    start_line = max(1, len(lines) - MAX_MESSAGES + 1)
    for line_no, raw in enumerate(lines[-MAX_MESSAGES:], start=start_line):
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {'raw': raw}
        obj['_cursor_line_no'] = line_no
        obj['_cursor_total_lines'] = len(lines)
        items.append(obj)
        latest_preview = cursor_entry_preview(obj)
    return {
        'path': str(path),
        'project': path.parts[-4] if len(path.parts) >= 4 else '',
        'session_id': path.stem,
        'modified_at': ts_to_local(path.stat().st_mtime),
        'latest_preview': latest_preview,
        'messages': items,
    }


def latest_cursor_terminals(limit=8):
    results = []
    base = CURSOR_DIR / 'projects'
    if not base.exists():
        return results
    for path in base.glob('*/terminals/*.txt'):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        results.append((stat.st_mtime, path))
    return [tail_text(p) for _, p in sorted(results, key=lambda item: item[0], reverse=True)[:limit]]


def fetch_cursor():
    ide_path = CURSOR_DIR / 'ide_state.json'
    transcripts = [parse_cursor_jsonl(p) for p in find_cursor_transcripts()]
    return {
        'ok': CURSOR_DIR.exists(),
        'cursor_dir': str(CURSOR_DIR),
        'transcripts': transcripts,
        'terminals': latest_cursor_terminals(),
        'ide_state': safe_json_loads(ide_path.read_text(errors='replace')) if ide_path.exists() else None,
    }


def make_event(ts, role, kind, title, preview, body='', raw=None, accent=None, display_ts=None, sort_key=None, media=None):
    return {
        'ts': ts,
        'display_ts': display_ts or ts,
        'sort_key': sort_key or ts or '',
        'role': role,
        'kind': kind,
        'title': title,
        'preview': clean_text(preview),
        'body': clean_text(body),
        'raw': raw,
        'media': media or [],
        'accent': accent or role,
    }


def event_sort_key(item):
    return item.get('sort_key') or item.get('ts') or ''


def normalize_hermes_sessions(hermes):
    normalized = []
    for session in hermes.get('sessions', []):
        timeline = []
        assistant_count = 0
        for msg in session.get('messages', []):
            parsed_content = msg.get('content_json') if msg.get('content_json') is not None else msg.get('content')
            text = extract_hermes_text(parsed_content)
            if msg.get('role') == 'assistant' and text:
                assistant_count += 1
            if msg.get('tool_name'):
                timeline.append(make_event(
                    msg.get('timestamp'),
                    'tool',
                    'tool',
                    f"Tool: {msg.get('tool_name')}",
                    clip(text or summarize_hermes_message(msg), 220),
                    body=text or pretty_json(parsed_content),
                    raw=pretty_json(parsed_content) if parsed_content not in (None, '') else '',
                    accent='tool',
                ))
            elif msg.get('role') == 'assistant' and text:
                timeline.append(make_event(
                    msg.get('timestamp'),
                    'assistant',
                    'message',
                    'Agent response',
                    clip(text, 220),
                    body=text,
                    raw=pretty_json(parsed_content) if parsed_content not in (None, '') else '',
                    accent='assistant',
                ))
            elif msg.get('role') == 'user' and text:
                timeline.append(make_event(
                    msg.get('timestamp'),
                    'user',
                    'message',
                    'User message',
                    clip(text, 220),
                    body=text,
                    raw=pretty_json(parsed_content) if parsed_content not in (None, '') else '',
                    accent='user',
                ))
            elif text:
                timeline.append(make_event(
                    msg.get('timestamp'),
                    msg.get('role') or 'system',
                    'message',
                    role_label(msg.get('role')), 
                    clip(text, 220),
                    body=text,
                    raw=pretty_json(parsed_content) if parsed_content not in (None, '') else '',
                    accent=msg.get('role') or 'system',
                ))
            if msg.get('reasoning'):
                timeline.append(make_event(
                    msg.get('timestamp'),
                    'reasoning',
                    'reasoning',
                    'Agent thought',
                    clip(msg['reasoning'], 220),
                    body=msg['reasoning'],
                    raw=msg['reasoning'],
                    accent='reasoning',
                ))
            if msg.get('reasoning_content'):
                timeline.append(make_event(
                    msg.get('timestamp'),
                    'reasoning',
                    'reasoning',
                    'Agent thought',
                    clip(msg['reasoning_content'], 220),
                    body=msg['reasoning_content'],
                    raw=msg['reasoning_content'],
                    accent='reasoning',
                ))
        timeline.sort(key=event_sort_key, reverse=True)
        normalized.append({
            'uid': 'hermes:' + session['id'],
            'source': 'Hermes',
            'id': session['id'],
            'title': session.get('title') or session['id'],
            'subtitle': session.get('cwd') or '',
            'updated_at': (timeline[0]['ts'] if timeline else session.get('started_at')),
            'meta': {
                'source': session.get('source'),
                'cwd': session.get('cwd'),
                'messages': session.get('message_count'),
                'tool_calls': session.get('tool_call_count'),
                'model': session.get('model'),
                'started_at': session.get('started_at'),
                'ended_at': session.get('ended_at') or 'running/unknown',
            },
            'preview': next((e['preview'] for e in timeline if e['role'] == 'assistant' and e['preview']), session.get('latest_preview', '')),
            'assistant_count': assistant_count,
            'timeline': timeline,
        })
    return normalized


def normalize_opencode_sessions(opencode):
    normalized = []
    for session in opencode.get('sessions', []):
        timeline = []
        assistant_count = 0
        for msg in session.get('messages', []):
            meta = msg.get('meta') or {}
            role = meta.get('role', 'message')
            for part in msg.get('parts', []):
                pdata = part.get('data') or {}
                ptype = pdata.get('type', 'part')
                if ptype == 'text':
                    text = clean_text(pdata.get('text', ''))
                    if text:
                        if role == 'assistant':
                            assistant_count += 1
                        timeline.append(make_event(
                            part.get('time_created'),
                            role,
                            'message',
                            'Agent response' if role == 'assistant' else role_label(role),
                            clip(text, 220),
                            body=text,
                            raw=pretty_json(pdata),
                            accent=role,
                        ))
                elif ptype == 'reasoning':
                    text = clean_text(pdata.get('text', ''))
                    if text:
                        timeline.append(make_event(
                            part.get('time_created'),
                            'reasoning',
                            'reasoning',
                            'Agent thought',
                            clip(text, 220),
                            body=text,
                            raw=pretty_json(pdata),
                            accent='reasoning',
                        ))
                elif ptype == 'tool':
                    tool = pdata.get('tool', 'tool')
                    state = pdata.get('state') or {}
                    text = tool_input_summary(state.get('input'))
                    output = state.get('output') or state.get('result') or pdata.get('result')
                    body = clean_text(text)
                    if output:
                        body = clean_text(body + '\n\nOUTPUT\n' + extract_hermes_text(output))
                    timeline.append(make_event(
                        part.get('time_created'),
                        'tool',
                        'tool',
                        f'Tool: {tool}',
                        clip(body or opencode_part_preview(part), 220),
                        body=body,
                        raw=pretty_json(pdata),
                        accent='tool',
                    ))
                else:
                    timeline.append(make_event(
                        part.get('time_created'),
                        'system',
                        ptype,
                        ptype.replace('-', ' '),
                        clip(opencode_part_preview(part), 220),
                        body=pretty_json(pdata),
                        raw=pretty_json(pdata),
                        accent='system',
                    ))
        timeline.sort(key=event_sort_key, reverse=True)
        normalized.append({
            'uid': 'opencode:' + session['id'],
            'source': 'OpenCode',
            'id': session['id'],
            'title': session.get('title') or session['id'],
            'subtitle': session.get('directory') or '',
            'updated_at': session.get('time_updated'),
            'meta': {
                'directory': session.get('directory'),
                'agent': session.get('agent'),
                'model': session.get('model'),
                'tokens_input': session.get('tokens_input'),
                'tokens_output': session.get('tokens_output'),
                'tokens_reasoning': session.get('tokens_reasoning'),
                'updated_at': session.get('time_updated'),
            },
            'preview': next((e['preview'] for e in timeline if e['role'] == 'assistant' and e['preview']), session.get('latest_preview', '')),
            'assistant_count': assistant_count,
            'timeline': timeline,
        })
    return normalized


def remove_redacted_markers(text):
    if not text:
        return ''
    lines = []
    for line in str(text).replace('\r', '').splitlines():
        if line.strip() == '[REDACTED]':
            continue
        lines.append(line)
    return clean_text('\n'.join(lines))


def clean_cursor_visible_text(text):
    text = remove_redacted_markers(text)
    if not text:
        return ''
    query_match = re.search(r'<user_query>\s*(.*?)\s*</user_query>', text, flags=re.DOTALL)
    if query_match:
        return clean_text(query_match.group(1))
    text = re.sub(r'<image_files>.*?</image_files>', '', text, flags=re.DOTALL)
    text = re.sub(r'<timestamp>.*?</timestamp>', '', text, flags=re.DOTALL)
    text = re.sub(r'^\s*\[Image\]\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'</?[^>]+>', '', text)
    return clean_text(text)


def cursor_message_text(entry):
    message = entry.get('message') or {}
    parts = message.get('content') or []
    texts = []
    for item in parts:
        if item.get('type') == 'text' and item.get('text'):
            text = clean_cursor_visible_text(item.get('text'))
            if text:
                texts.append(text)
        elif item.get('text'):
            text = clean_cursor_visible_text(item.get('text'))
            if text:
                texts.append(text)
    return clean_text('\n\n'.join(texts))


def normalize_cursor_sessions(cursor):
    normalized = []
    terminals = cursor.get('terminals', [])
    for transcript in cursor.get('transcripts', []):
        timeline = []
        assistant_count = 0
        for entry in transcript.get('messages', []):
            line_no = entry.get('_cursor_line_no') or 0
            entry_ts = entry.get('timestamp') or entry.get('created_at') or entry.get('time')
            display_ts = entry_ts or (f'Cursor message #{line_no}' if line_no else '')
            sort_key = entry_ts or f'{line_no:010d}'
            if 'message' in entry:
                role = entry.get('role', 'message')
                text = cursor_message_text(entry)
                media = extract_media_assets((entry.get('message') or {}).get('content') or [])
                if not text and not media:
                    continue
                if role == 'assistant' and text:
                    assistant_count += 1
                timeline.append(make_event(
                    entry_ts,
                    role,
                    'message',
                    'Agent response' if role == 'assistant' else role_label(role),
                    clip(text or cursor_entry_preview(entry), 220),
                    body=text,
                    raw=pretty_json(entry),
                    display_ts=display_ts,
                    sort_key=sort_key,
                    media=media,
                    accent=role,
                ))
            else:
                kind = entry.get('type', 'event')
                timeline.append(make_event(
                    entry_ts,
                    'system',
                    kind,
                    kind.replace('_', ' '),
                    clip(cursor_entry_preview(entry), 220),
                    body=pretty_json(entry),
                    raw=pretty_json(entry),
                    display_ts=display_ts,
                    sort_key=sort_key,
                    accent='system',
                ))
        body_lines = []
        for term in terminals[:3]:
            body_lines.append(term.get('path', ''))
            body_lines.extend(term.get('lines', [])[-15:])
            body_lines.append('')
        if body_lines:
            timeline.append(make_event(
                transcript.get('modified_at'),
                'terminal',
                'terminal',
                'Recent terminal snapshots',
                'Cursor terminal output',
                body='\n'.join(body_lines),
                raw='\n'.join(body_lines),
                accent='terminal',
            ))
        timeline.sort(key=event_sort_key, reverse=True)
        normalized.append({
            'uid': 'cursor:' + transcript['session_id'],
            'source': 'Cursor',
            'id': transcript['session_id'],
            'title': transcript.get('project') or transcript['session_id'],
            'subtitle': transcript.get('path') or '',
            'updated_at': transcript.get('modified_at'),
            'meta': {
                'path': transcript.get('path'),
                'project': transcript.get('project'),
                'updated_at': transcript.get('modified_at'),
            },
            'preview': next((e['preview'] for e in timeline if e['role'] == 'assistant' and e['preview']), transcript.get('latest_preview', '')),
            'assistant_count': assistant_count,
            'timeline': timeline,
        })
    return normalized


def build_state():
    hermes = fetch_hermes()
    opencode = fetch_opencode()
    cursor = fetch_cursor()
    unified_sessions = (
        normalize_hermes_sessions(hermes)
        + normalize_opencode_sessions(opencode)
        + normalize_cursor_sessions(cursor)
    )
    unified_sessions.sort(key=lambda item: item.get('updated_at') or '', reverse=True)
    return {
        'generated_at': now_iso(),
        'host': HOST,
        'port': PORT,
        'refresh_ms': REFRESH_MS,
        'processes': get_processes(),
        'tmux_sessions': get_tmux_sessions(),
        'hermes': hermes,
        'opencode': opencode,
        'cursor': cursor,
        'unified_sessions': unified_sessions,
    }


INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Agent Session Mirror</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
    :root {
      --bg: #070b12;
      --bg-2: #0b111c;
      --surface: rgba(17, 24, 39, .82);
      --surface-2: rgba(22, 30, 45, .78);
      --surface-3: rgba(31, 41, 59, .66);
      --glass: rgba(15, 23, 42, .72);
      --text: #e5edf7;
      --text-strong: #f8fafc;
      --muted: #94a3b8;
      --muted-2: #64748b;
      --line: rgba(148, 163, 184, .16);
      --line-strong: rgba(226, 232, 240, .22);
      --accent: #22d3a6;
      --accent-2: #60a5fa;
      --danger: #fb7185;
      --assistant: rgba(15, 23, 42, .94);
      --assistant-line: rgba(148, 163, 184, .18);
      --user: linear-gradient(135deg, rgba(34, 211, 166, .95), rgba(59, 130, 246, .92));
      --user-line: rgba(125, 211, 252, .32);
      --tool: rgba(30, 41, 59, .78);
      --tool-line: rgba(96, 165, 250, .2);
      --reasoning: rgba(49, 46, 129, .35);
      --reasoning-line: rgba(165, 180, 252, .22);
      --system: rgba(15, 23, 42, .62);
      --system-line: rgba(148, 163, 184, .14);
      --terminal: rgba(2, 6, 23, .78);
      --terminal-line: rgba(148, 163, 184, .16);
      --hermes: #2dd4bf;
      --opencode: #f59e0b;
      --cursor: #60a5fa;
      --shadow: 0 24px 70px rgba(0, 0, 0, .38);
      --shadow-soft: 0 16px 35px rgba(0, 0, 0, .24);
      --radius: 22px;
      --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 18% -8%, rgba(34, 211, 166, .18), transparent 30%),
        radial-gradient(circle at 78% 0%, rgba(96, 165, 250, .18), transparent 28%),
        linear-gradient(180deg, var(--bg), var(--bg-2) 42%, #070a10);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.6;
      overflow: hidden;
    }
    body::before {
      content: '';
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
      background-size: 42px 42px;
      mask-image: linear-gradient(to bottom, black, transparent 75%);
    }
    *::-webkit-scrollbar { width: 10px; height: 10px; }
    *::-webkit-scrollbar-thumb { background: rgba(148, 163, 184, .22); border: 3px solid transparent; border-radius: 999px; background-clip: padding-box; }
    *::-webkit-scrollbar-track { background: transparent; }
    a { color: inherit; }
    h1, h2, h3 { margin: 0; letter-spacing: -.035em; color: var(--text-strong); }
    h1 { font-size: 18px; font-weight: 800; }
    h2 { font-size: 16px; font-weight: 750; }
    h3 { font-size: 14px; font-weight: 700; }
    .mono { font-family: var(--mono); }
    .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .app-bar {
      height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(7, 11, 18, .74);
      backdrop-filter: blur(22px);
      position: sticky;
      top: 0;
      z-index: 30;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 260px;
    }
    .brand-mark {
      width: 38px;
      height: 38px;
      border-radius: 14px;
      background: linear-gradient(135deg, rgba(34, 211, 166, .92), rgba(96, 165, 250, .92));
      box-shadow: 0 0 34px rgba(34, 211, 166, .32);
      position: relative;
    }
    .brand-mark::after {
      content: '';
      position: absolute;
      inset: 10px;
      border: 1px solid rgba(255,255,255,.7);
      border-radius: 9px;
    }
    .brand-kicker {
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .app-actions { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .topbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; min-width: 0; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 10px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 650;
      background: rgba(15, 23, 42, .64);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
      white-space: nowrap;
    }
    .pill.live::before {
      content: '';
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 16px var(--accent);
    }
    .action-button {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 11px;
      color: var(--text);
      background: rgba(15, 23, 42, .7);
      cursor: pointer;
      font: 700 12px var(--sans);
      transition: .18s ease;
    }
    .action-button:hover, .action-button.active {
      border-color: rgba(34, 211, 166, .42);
      background: rgba(34, 211, 166, .12);
      color: #d9fff5;
    }
    .app-shell {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr) 320px;
      height: calc(100vh - 72px);
      min-height: 0;
      position: relative;
    }
    .session-rail, .chat-stage, .runtime-drawer { min-width: 0; min-height: 0; }
    .session-rail {
      border-right: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(8, 13, 22, .82), rgba(8, 13, 22, .58));
      backdrop-filter: blur(18px);
    }
    .chat-stage { background: rgba(2, 6, 23, .18); }
    .runtime-drawer {
      border-left: 1px solid var(--line);
      background: rgba(8, 13, 22, .62);
      backdrop-filter: blur(18px);
      transition: transform .22s ease, opacity .22s ease;
    }
    body.runtime-closed .runtime-drawer { transform: translateX(100%); opacity: 0; pointer-events: none; }
    body.runtime-closed .app-shell { grid-template-columns: 360px minmax(0, 1fr) 0px; }
    .panelhead {
      padding: 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(7, 11, 18, .72);
      backdrop-filter: blur(18px);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .panelhead-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .scroll { overflow: auto; height: calc(100vh - 155px); }
    .filters {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 4px;
      padding: 4px;
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(2, 6, 23, .4);
    }
    button.filter {
      border: 0;
      background: transparent;
      color: var(--muted);
      border-radius: 12px;
      padding: 8px 9px;
      cursor: pointer;
      font-family: var(--sans);
      font-size: 11px;
      font-weight: 750;
      transition: .18s ease;
    }
    button.filter:hover { color: var(--text); background: rgba(148, 163, 184, .08); }
    button.filter.active { background: rgba(34, 211, 166, .14); color: #baf7e7; box-shadow: inset 0 0 0 1px rgba(34, 211, 166, .22); }
    .session-list { padding: 12px; display: grid; gap: 10px; }
    .session-item {
      position: relative;
      padding: 13px 14px 13px 16px;
      border: 1px solid transparent;
      border-radius: 18px;
      cursor: pointer;
      background: rgba(15, 23, 42, .38);
      transition: transform .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
      overflow: hidden;
    }
    .session-item::before {
      content: '';
      position: absolute;
      left: 0;
      top: 12px;
      bottom: 12px;
      width: 3px;
      border-radius: 999px;
      background: var(--accent);
      opacity: .55;
    }
    .session-item.hermes::before { background: var(--hermes); }
    .session-item.opencode::before { background: var(--opencode); }
    .session-item.cursor::before { background: var(--cursor); }
    .session-item:hover {
      transform: translateY(-1px);
      border-color: rgba(148, 163, 184, .18);
      background: rgba(30, 41, 59, .48);
    }
    .session-item.active {
      border-color: rgba(34, 211, 166, .32);
      background: linear-gradient(180deg, rgba(22, 33, 50, .9), rgba(15, 23, 42, .78));
      box-shadow: 0 18px 34px rgba(0, 0, 0, .22), inset 0 1px 0 rgba(255,255,255,.04);
    }
    .session-meta-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .session-time { color: var(--muted-2); font-size: 11px; font-weight: 700; white-space: nowrap; }
    .session-title {
      font-weight: 760;
      font-size: 13px;
      line-height: 1.35;
      margin-top: 10px;
      color: var(--text-strong);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .session-subtitle, .session-preview, .session-stats { color: var(--muted-2); font-size: 11px; margin-top: 6px; }
    .session-subtitle { display: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .session-item.active .session-subtitle { display: block; }
    .session-preview {
      color: var(--muted);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      line-height: 1.45;
    }
    .session-stats { display: flex; gap: 8px; align-items: center; color: var(--muted-2); }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 10px;
      letter-spacing: .04em;
      text-transform: uppercase;
      border-radius: 999px;
      padding: 4px 8px;
      border: 1px solid var(--line);
      color: var(--muted);
      background: rgba(15, 23, 42, .72);
      font-weight: 800;
    }
    .badge::before { content: ''; width: 6px; height: 6px; border-radius: 99px; background: currentColor; box-shadow: 0 0 12px currentColor; }
    .badge.hermes { color: var(--hermes); border-color: rgba(45, 212, 191, .24); }
    .badge.opencode { color: var(--opencode); border-color: rgba(245, 158, 11, .24); }
    .badge.cursor { color: var(--cursor); border-color: rgba(96, 165, 250, .24); }
    .detail-wrap { height: calc(100vh - 72px); overflow: auto; padding: 22px clamp(18px, 3vw, 36px); }
    .chat-stage-inner { max-width: 940px; margin: 0 auto; }
    .detail-card, .session-hero, .chat-frame, .secondary {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow-soft);
      backdrop-filter: blur(20px);
    }
    .session-hero { padding: 18px; margin-bottom: 16px; }
    .hero-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
    .hero-title { margin-top: 10px; font-size: clamp(18px, 2vw, 24px); line-height: 1.15; }
    .hero-path { max-width: 760px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted-2); }
    .summary-row { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px; margin-top: 14px; }
    .summary-chip {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 11px 12px;
      background: rgba(2, 6, 23, .36);
      color: var(--muted);
      font-size: 11px;
      min-width: 0;
    }
    .summary-chip strong { display: block; font-size: 19px; color: var(--text-strong); line-height: 1.1; margin-bottom: 3px; }
    .meta-grid {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 9px 12px;
      font-size: 12px;
      margin-top: 12px;
      color: var(--muted);
    }
    .meta-grid div:nth-child(odd) { color: var(--muted-2); font-weight: 750; text-transform: uppercase; letter-spacing: .05em; font-size: 10px; }
    .chat-frame { padding: 18px; }
    .chat-frame-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
    .timeline { display: grid; gap: 12px; margin-top: 14px; }
    .timeline.chat-timeline { display: flex; flex-direction: column; gap: 18px; margin-top: 0; }
    .event {
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      background: var(--surface-2);
    }
    .chat-timeline .event {
      width: fit-content;
      max-width: min(760px, 84%);
      border-radius: 22px;
      overflow: visible;
      border-color: var(--assistant-line);
      box-shadow: 0 18px 40px rgba(0, 0, 0, .22);
    }
    .chat-timeline .event.assistant { align-self: flex-start; background: var(--assistant); }
    .chat-timeline .event.user {
      align-self: flex-end;
      background: var(--user);
      border-color: var(--user-line);
      color: #ecfeff;
      box-shadow: 0 20px 44px rgba(34, 211, 166, .16);
    }
    .event.tool { background: var(--tool); border-color: var(--tool-line); }
    .event.reasoning { background: var(--reasoning); border-color: var(--reasoning-line); }
    .event.system { background: var(--system); border-color: var(--system-line); }
    .event.terminal { background: var(--terminal); border-color: var(--terminal-line); }
    .event-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 12px 14px 0;
      font-size: 11px;
      color: var(--muted);
      background: transparent;
      border: 0;
    }
    .event-title { color: var(--text-strong); font-weight: 800; }
    .chat-timeline .event-head { padding: 13px 16px 0; }
    .chat-timeline .event-head .badge { display: none; }
    .chat-timeline .event.user .event-title, .chat-timeline .event.user .event-head { color: rgba(255,255,255,.86); }
    .event-time { color: var(--muted-2); font: 600 11px var(--mono); white-space: nowrap; }
    .chat-timeline .event.user .event-time { color: rgba(236, 254, 255, .74); }
    .event-preview {
      padding: 10px 16px 16px;
      word-break: break-word;
      line-height: 1.65;
      font-size: 14px;
      color: var(--text);
    }
    .chat-timeline .event.user .event-preview { color: #ecfeff; }
    .event-preview.plain { white-space: pre-wrap; }
    .event-preview.empty { color: var(--muted); font-style: italic; }
    .markdown-body { white-space: normal; }
    .markdown-body > :first-child { margin-top: 0; }
    .markdown-body > :last-child { margin-bottom: 0; }
    .markdown-body p { margin: 0 0 0.78em; }
    .markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 {
      margin: 1em 0 .45em;
      line-height: 1.25;
      font-weight: 780;
      color: var(--text-strong);
    }
    .markdown-body h1 { font-size: 1.36em; }
    .markdown-body h2 { font-size: 1.22em; }
    .markdown-body h3 { font-size: 1.08em; }
    .markdown-body h4 { font-size: 1em; }
    .markdown-body ul, .markdown-body ol { margin: 0 0 .85em 1.25em; padding: 0; }
    .markdown-body li { margin: .26em 0; }
    .markdown-body blockquote {
      margin: 0 0 .85em;
      padding: .2em 0 .2em 1em;
      border-left: 3px solid rgba(34, 211, 166, .42);
      color: var(--muted);
    }
    .markdown-body code {
      font-family: var(--mono);
      font-size: .91em;
      background: rgba(2, 6, 23, .46);
      color: #a7f3d0;
      border: 1px solid rgba(148, 163, 184, .14);
      border-radius: 7px;
      padding: .12em .38em;
    }
    .chat-timeline .event.user .markdown-body code { color: #ecfeff; background: rgba(2, 6, 23, .2); border-color: rgba(255,255,255,.18); }
    .markdown-body pre {
      margin: 0 0 .9em;
      padding: 13px 14px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(2, 6, 23, .7);
      color: #cbd5e1;
      border: 1px solid rgba(148, 163, 184, .16);
      border-radius: 14px;
      max-height: none;
      overflow: auto;
    }
    .markdown-body pre code { background: transparent; padding: 0; border: 0; border-radius: 0; font-size: inherit; color: inherit; }
    .markdown-body a { color: #67e8f9; text-decoration: none; }
    .markdown-body a:hover { text-decoration: underline; }
    .markdown-body hr { border: 0; border-top: 1px solid var(--line); margin: 1em 0; }
    .markdown-body table { border-collapse: collapse; width: 100%; margin: 0 0 .9em; font-size: 13px; overflow: hidden; border-radius: 12px; }
    .markdown-body th, .markdown-body td { border: 1px solid rgba(148, 163, 184, .16); padding: 7px 9px; text-align: left; }
    .markdown-body th { background: rgba(30, 41, 59, .78); color: var(--text-strong); }
    .media-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
      gap: 10px;
      padding: 0 16px 16px;
    }
    .event-preview + .media-grid { padding-top: 0; }
    .media-item {
      display: block;
      position: relative;
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
      background: rgba(2, 6, 23, .34);
      min-height: 96px;
      transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
    }
    .media-item:hover {
      transform: translateY(-2px);
      border-color: rgba(34, 211, 166, .36);
      box-shadow: 0 18px 35px rgba(0,0,0,.28);
    }
    .media-item img {
      display: block;
      width: 100%;
      max-height: 340px;
      object-fit: cover;
      background: #020617;
    }
    .media-caption {
      position: absolute;
      left: 8px;
      right: 8px;
      bottom: 8px;
      padding: 5px 8px;
      color: rgba(226, 232, 240, .9);
      font-size: 10px;
      border: 1px solid rgba(255,255,255,.08);
      border-radius: 999px;
      background: rgba(2, 6, 23, .64);
      backdrop-filter: blur(10px);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      opacity: 0;
      transition: opacity .18s ease;
    }
    .media-item:hover .media-caption { opacity: 1; }
    pre {
      margin: 0;
      padding: 12px 14px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(2, 6, 23, .72);
      color: #cbd5e1;
      border-top: 1px solid var(--line);
      max-height: 360px;
      overflow: auto;
      border-bottom-left-radius: 18px;
      border-bottom-right-radius: 18px;
    }
    details summary {
      cursor: pointer;
      list-style: none;
      padding: 0;
      color: #9ee7d7;
      font-size: 12px;
      font-weight: 750;
    }
    .secondary { margin-top: 16px; padding: 14px; }
    .secondary > details > summary { padding: 2px 4px; }
    .mini-block { padding: 13px 14px; border-bottom: 1px solid var(--line); }
    .mini-title { font-size: 12px; font-weight: 800; color: var(--text-strong); }
    .mini-sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .runtime-scroll { height: calc(100vh - 155px); overflow: auto; padding: 10px; }
    .runtime-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px;
      margin-bottom: 10px;
      background: rgba(15, 23, 42, .54);
    }
    .runtime-card pre { margin-top: 9px; border: 1px solid var(--line); border-radius: 12px; max-height: 160px; }
    .empty { color: var(--muted); padding: 20px; }
    @media (max-width: 1200px) {
      .app-shell { grid-template-columns: 330px minmax(0, 1fr); }
      .runtime-drawer { display: none; }
      body.runtime-closed .app-shell { grid-template-columns: 330px minmax(0, 1fr); }
      .chat-timeline .event { max-width: 92%; }
    }
    @media (max-width: 860px) {
      body { overflow: auto; }
      .app-bar { height: auto; align-items: flex-start; flex-direction: column; padding: 14px; position: relative; }
      .brand { min-width: 0; }
      .app-actions { width: 100%; justify-content: space-between; }
      .topbar { justify-content: flex-start; }
      .app-shell, body.runtime-closed .app-shell { display: block; height: auto; }
      .session-rail { border-right: 0; border-bottom: 1px solid var(--line); }
      .scroll { height: auto; max-height: 42vh; }
      .detail-wrap { height: auto; padding: 14px; }
      .panelhead { position: static; }
      .summary-row { grid-template-columns: repeat(2, minmax(0,1fr)); }
      .chat-timeline .event { max-width: 100%; }
    }
  </style>
</head>
<body class=\"runtime-open\">
<header class=\"app-bar\">
  <div class=\"brand\">
    <div class=\"brand-mark\"></div>
    <div>
      <div class=\"brand-kicker\">Local Agent Observatory</div>
      <h1>Agent Session Mirror</h1>
    </div>
  </div>
  <div class=\"app-actions\">
    <div id=\"topbar\" class=\"topbar\"></div>
    <button id=\"runtimeToggle\" class=\"action-button\" type=\"button\">Runtime</button>
  </div>
</header>
<div class=\"app-shell\">
  <section class=\"session-rail\">
    <div class=\"panelhead\">
      <div class=\"panelhead-row\"><h2>Sessions</h2><span id=\"sessionCount\" class=\"sub\"></span></div>
      <div class=\"filters\">
        <button class=\"filter active\" data-filter=\"all\">All</button>
        <button class=\"filter\" data-filter=\"Hermes\">Hermes</button>
        <button class=\"filter\" data-filter=\"OpenCode\">OpenCode</button>
        <button class=\"filter\" data-filter=\"Cursor\">Cursor</button>
      </div>
    </div>
    <div id=\"sessionList\" class=\"scroll\"></div>
  </section>
  <main class=\"chat-stage\">
    <div id=\"detail\" class=\"detail-wrap\"></div>
  </main>
  <aside class=\"runtime-drawer\">
    <div class=\"panelhead\"><div class=\"panelhead-row\"><h2>Runtime</h2><span class=\"sub\">Secondary</span></div></div>
    <div id=\"runtime\" class=\"runtime-scroll\"></div>
  </aside>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.2.4/dist/purify.min.js"></script>
<script>
const REFRESH_MS = __REFRESH_MS__;
let state = null;
let selectedId = null;
let filterSource = 'all';
let runtimeOpen = true;

function esc(v) {
  var value = (v === null || v === undefined) ? '' : String(v);
  return value.replace(/[&<>\"']/g, function(ch) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]; });
}

function stripMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function renderMarkdown(text) {
  if (!text) return '';
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return '<div class="plain">' + esc(text) + '</div>';
  }
  try {
    var html = marked.parse(String(text), { breaks: true, gfm: true });
    return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  } catch (e) {
    return '<div class="plain">' + esc(text) + '</div>';
  }
}

function renderBody(text, asMarkdown) {
  if (!text) return '<div class="event-preview empty">Empty message.</div>';
  if (asMarkdown) {
    return '<div class="event-preview markdown-body">' + renderMarkdown(text) + '</div>';
  }
  return '<div class="event-preview plain">' + esc(text) + '</div>';
}

function renderMedia(media) {
  var items = Array.isArray(media) ? media : [];
  if (!items.length) return '';
  return '<div class="media-grid compact">' + items.map(function(item) {
    if (item.type === 'image' && item.url) {
      return '<a class="media-item" href="' + esc(item.url) + '" target="_blank" rel="noreferrer">' +
        '<img src="' + esc(item.url) + '" alt="' + esc(item.label || 'image') + '" loading="lazy">' +
        '<div class="media-caption">open image</div>' +
        '</a>';
    }
    return '';
  }).join('') + '</div>';
}

function pretty(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
}

function sourceClass(source) { return String(source || '').toLowerCase(); }
function eventClass(ev) { return String((ev && ev.accent) || (ev && ev.role) || 'system').toLowerCase(); }

function shortId(value) {
  var text = String(value || '');
  if (text.length <= 18) return text;
  return text.slice(0, 8) + '...' + text.slice(-7);
}

function formatRelativeTime(value) {
  if (!value) return 'live';
  var date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace(/^Cursor message /, '#');
  var seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return seconds + 's ago';
  var minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + 'm ago';
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ago';
  var days = Math.floor(hours / 24);
  if (days < 7) return days + 'd ago';
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function sourceAccent(source) {
  var cls = sourceClass(source);
  if (cls === 'hermes') return 'Hermes';
  if (cls === 'opencode') return 'OpenCode';
  if (cls === 'cursor') return 'Cursor';
  return source || 'Agent';
}

function filteredSessions() {
  var items = (state && state.unified_sessions) ? state.unified_sessions.slice() : [];
  if (filterSource !== 'all') items = items.filter(function(item) { return item.source === filterSource; });
  items.sort(function(a, b) { return String(b.updated_at || '').localeCompare(String(a.updated_at || '')); });
  return items;
}

function renderTopbar() {
  if (!state) return;
  var html = '';
  html += '<span class="pill live">updated ' + esc(formatRelativeTime(state.generated_at)) + '</span>';
  html += '<span class="pill">sessions ' + esc((state.unified_sessions || []).length) + '</span>';
  html += '<span class="pill">processes ' + esc((state.processes || []).length) + '</span>';
  html += '<span class="pill">tmux ' + esc((state.tmux_sessions || []).length) + '</span>';
  document.getElementById('topbar').innerHTML = html;
  var count = document.getElementById('sessionCount');
  if (count) count.textContent = filteredSessions().length + ' shown';
}

function renderSessionList() {
  var items = filteredSessions();
  if ((!selectedId || !items.some(function(i) { return i.uid === selectedId; })) && items.length) selectedId = items[0].uid;
  if (!items.length) {
    document.getElementById('sessionList').innerHTML = '<div class="empty">No sessions in this filter.</div>';
    return;
  }
  var html = items.map(function(item) {
    var active = item.uid === selectedId ? ' active' : '';
    var cls = sourceClass(item.source);
    return '<div class="session-item ' + esc(cls) + active + '" data-id="' + esc(item.uid) + '">' +
      '<div class="session-meta-row"><span class="badge ' + cls + '">' + esc(sourceAccent(item.source)) + '</span><span class="session-time">' + esc(formatRelativeTime(item.updated_at)) + '</span></div>' +
      '<div class="session-title">' + esc(item.title) + '</div>' +
      '<div class="session-subtitle mono">' + esc(item.subtitle || item.id) + '</div>' +
      '<div class="session-preview">' + esc(stripMarkdown(item.preview || '')) + '</div>' +
      '<div class="session-stats"><span>' + esc(item.assistant_count || 0) + ' replies</span><span class="mono">' + esc(shortId(item.id)) + '</span></div>' +
      '</div>';
  }).join('');
  document.getElementById('sessionList').innerHTML = '<div class="session-list">' + html + '</div>';
  Array.prototype.forEach.call(document.querySelectorAll('.session-item'), function(el) {
    el.onclick = function() { selectedId = el.getAttribute('data-id'); render(); };
  });
}

function renderDetail() {
  var items = filteredSessions();
  var item = items.find(function(i) { return i.uid === selectedId; });
  if (!item) {
    document.getElementById('detail').innerHTML = '<div class="detail-card">No session selected.</div>';
    return;
  }
  var metaPairs = '';
  var meta = item.meta || {};
  Object.keys(meta).forEach(function(key) {
    metaPairs += '<div>' + esc(key) + '</div><div>' + (typeof meta[key] === 'object' ? '<pre>' + esc(pretty(meta[key])) + '</pre>' : esc(meta[key])) + '</div>';
  });

  var timeline = (item.timeline || []).slice().sort(function(a, b) { return String(b.sort_key || b.ts || '').localeCompare(String(a.sort_key || a.ts || '')); });
  var chatMessages = timeline.filter(function(ev) {
    return ev.role === 'user' || ev.role === 'assistant';
  });
  var secondaryEvents = timeline.filter(function(ev) {
    return ev.role !== 'user' && ev.role !== 'assistant';
  });
  var responses = chatMessages.filter(function(ev) { return ev.role === 'assistant'; }).length;
  var userMessages = chatMessages.filter(function(ev) { return ev.role === 'user'; }).length;
  var thoughts = secondaryEvents.filter(function(ev) { return ev.kind === 'reasoning'; }).length;
  var tools = secondaryEvents.filter(function(ev) { return ev.kind === 'tool'; }).length;

  var chatHtml = chatMessages.map(function(ev) {
    var body = renderBody(ev.body, true);
    var media = renderMedia(ev.media);
    return '<article class="event ' + esc(eventClass(ev)) + '">' +
      '<div class="event-head"><span class="event-title">' + esc(ev.role === 'assistant' ? 'Agent' : 'You') + '</span><span class="event-time">' + esc(ev.display_ts || ev.ts || '') + '</span></div>' +
      body + media +
      '</article>';
  }).join('');

  var secondaryHtml = secondaryEvents.map(function(ev) {
    var body = renderBody(ev.body, ev.kind === 'reasoning');
    var media = renderMedia(ev.media);
    var raw = ev.raw ? '<details><summary>open raw detail</summary><pre>' + esc(ev.raw) + '</pre></details>' : '';
    return '<article class="event ' + esc(eventClass(ev)) + '">' +
      '<div class="event-head"><span><span class="badge ' + sourceClass(item.source) + '">' + esc(item.source) + '</span><span class="event-title">' + esc(ev.title || ev.kind || 'event') + '</span></span><span class="event-time">' + esc(ev.display_ts || ev.ts || '') + '</span></div>' +
      body + media + raw +
      '</article>';
  }).join('');

  var html = '';
  html += '<div class="chat-stage-inner">';
  html += '<section class="session-hero">';
  html += '<div class="hero-top"><div><span class="badge ' + sourceClass(item.source) + '">' + esc(sourceAccent(item.source)) + '</span><h2 class="hero-title">' + esc(item.title) + '</h2><div class="sub mono hero-path">' + esc(item.subtitle || item.id) + '</div></div><div class="pill live">' + esc(formatRelativeTime(item.updated_at)) + '</div></div>';
  html += '<div class="summary-row">';
  html += '<div class="summary-chip"><strong>' + esc(userMessages) + '</strong>your messages</div>';
  html += '<div class="summary-chip"><strong>' + esc(responses) + '</strong>agent replies</div>';
  html += '<div class="summary-chip"><strong>' + esc(thoughts) + '</strong>hidden thoughts</div>';
  html += '<div class="summary-chip"><strong>' + esc(tools) + '</strong>hidden tool events</div>';
  html += '</div>';
  html += '<details style="margin-top:14px"><summary>show session metadata</summary><div class="meta-grid">' + metaPairs + '</div></details>';
  html += '</section>';
  html += '<section class="chat-frame"><div class="chat-frame-head"><div><h3>Conversation</h3><div class="sub chat-note">Newest messages are at the top. Tool output stays secondary.</div></div><span class="pill">' + esc(chatMessages.length) + ' messages</span></div><div class="timeline chat-timeline">' + (chatHtml || '<div class="empty">No user/agent messages extracted for this session.</div>') + '</div></section>';
  html += '<section class="secondary"><details><summary>show secondary activity (' + esc(secondaryEvents.length) + ')</summary><div class="timeline">' + (secondaryHtml || '<div class="empty">No secondary activity.</div>') + '</div></details></section>';
  html += '</div>';
  document.getElementById('detail').innerHTML = html;
}

function renderRuntime() {
  if (!state) return;
  var html = '<div class="mini-block"><div class="mini-title">Processes</div><div class="mini-sub">Running agent-related commands</div></div>';
  html += (state.processes || []).map(function(p) {
    return '<details class="runtime-card"><summary><span class="mini-title mono">pid ' + esc(p.pid) + '</span><span class="mini-sub">cpu ' + esc(p.cpu) + ' / mem ' + esc(p.mem) + '</span></summary><div class="mini-sub">' + esc(p.started) + ' / ' + esc(p.time) + '</div><pre>' + esc(p.command) + '</pre></details>';
  }).join('');
  html += '<div class="mini-block"><div class="mini-title">tmux</div><div class="mini-sub">' + esc((state.tmux_sessions || []).length) + ' sessions</div></div>';
  html += (state.tmux_sessions || []).map(function(t) { return '<div class="runtime-card"><div class="mini-title">' + esc(t.name || 'tmux') + '</div><pre>' + esc(t.raw) + '</pre></div>'; }).join('');
  document.getElementById('runtime').innerHTML = html;
}

function render() {
  document.body.classList.toggle('runtime-closed', !runtimeOpen);
  document.body.classList.toggle('runtime-open', runtimeOpen);
  renderTopbar();
  renderSessionList();
  renderDetail();
  renderRuntime();
  var runtimeToggle = document.getElementById('runtimeToggle');
  if (runtimeToggle) {
    runtimeToggle.classList.toggle('active', runtimeOpen);
    runtimeToggle.onclick = function() {
      runtimeOpen = !runtimeOpen;
      render();
    };
  }
  Array.prototype.forEach.call(document.querySelectorAll('button.filter'), function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-filter') === filterSource);
    btn.onclick = function() { filterSource = btn.getAttribute('data-filter'); render(); };
  });
}

async function refresh() {
  try {
    var res = await fetch('/api/state');
    state = await res.json();
    render();
  } catch (err) {
    document.getElementById('detail').innerHTML = '<div class="detail-card">Failed to load state: <pre>' + esc(String(err)) + '</pre></div>';
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
""".replace('__REFRESH_MS__', str(REFRESH_MS))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type='text/html; charset=utf-8'):
        payload = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(payload)

    def _send_media(self, parsed):
        raw_path = parse_qs(parsed.query).get('path', [''])[0]
        if not raw_path:
            return self._send(400, 'missing path', 'text/plain; charset=utf-8')
        try:
            path = Path(unquote(raw_path)).expanduser().resolve()
            base = BASE.resolve()
            if base not in path.parents and path != base:
                return self._send(403, 'path not allowed', 'text/plain; charset=utf-8')
            if not path.exists() or not path.is_file() or not is_image_value(str(path)):
                return self._send(404, 'image not found', 'text/plain; charset=utf-8')
            content_type = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
            return self._send(200, path.read_bytes(), content_type)
        except Exception as exc:
            return self._send(500, str(exc), 'text/plain; charset=utf-8')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/':
            return self._send(200, INDEX_HTML)
        if path == '/media':
            return self._send_media(parsed)
        if path == '/healthz':
            return self._send(200, json.dumps({'ok': True, 'time': now_iso()}), 'application/json; charset=utf-8')
        if path == '/api/state':
            return self._send(200, json.dumps(build_state(), ensure_ascii=False), 'application/json; charset=utf-8')
        if path == '/api/hermes':
            return self._send(200, json.dumps(fetch_hermes(), ensure_ascii=False), 'application/json; charset=utf-8')
        if path == '/api/opencode':
            return self._send(200, json.dumps(fetch_opencode(), ensure_ascii=False), 'application/json; charset=utf-8')
        if path == '/api/cursor':
            return self._send(200, json.dumps(fetch_cursor(), ensure_ascii=False), 'application/json; charset=utf-8')
        return self._send(404, 'not found', 'text/plain; charset=utf-8')

    def log_message(self, fmt, *args):
        print(f'[{now_iso()}] ' + (fmt % args))


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Agent Session Mirror listening on http://{HOST}:{PORT}')
    server.serve_forever()
