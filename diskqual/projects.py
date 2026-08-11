# projects.py
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def _default_base():
    configured = os.environ.get('DISKQUAL_HOME')
    if configured:
        configured_path = Path(configured).expanduser()
        if os.access(configured_path, os.W_OK):
            return configured_path
    production = Path('/opt/diskqual')
    if os.access(production, os.W_OK):
        return production
    xdg = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return xdg / 'sirgon-diskqual'


BASE = _default_base()
PROJECTS = BASE / 'client-reports'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(name):
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', name.strip()).strip('-').lower()
    return value or 'report'


def ensure_projects_dir():
    PROJECTS.mkdir(parents=True, exist_ok=True)


def create_project(name, client='', notes=''):
    ensure_projects_dir()
    base = _slug(name)
    folder = PROJECTS / base
    index = 2
    while folder.exists():
        folder = PROJECTS / f'{base}-{index}'
        index += 1
    folder.mkdir(parents=True)
    data = {
        'id': folder.name,
        'name': name.strip(),
        'client': client.strip() or name.strip(),
        'notes': notes.strip(),
        'created_utc': _now(),
        'updated_utc': _now(),
        'drives': [],
    }
    save_project(data)
    return data


def project_path(project_id):
    return PROJECTS / project_id / 'manifest.json'


def save_project(data):
    ensure_projects_dir()
    folder = PROJECTS / data['id']
    folder.mkdir(parents=True, exist_ok=True)
    data['updated_utc'] = _now()
    tmp = folder / 'manifest.json.tmp'
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(folder / 'manifest.json')


def load_project(project_id):
    return json.loads(project_path(project_id).read_text())


def list_projects():
    ensure_projects_dir()
    rows = []
    for manifest in sorted(PROJECTS.glob('*/manifest.json')):
        try:
            rows.append(json.loads(manifest.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda x: x.get('updated_utc', ''), reverse=True)


def add_drive(project_id, drive):
    data = load_project(project_id)
    serial = str(drive.get('serial') or drive.get('id') or '').strip()
    if not serial:
        raise ValueError('Drive has no serial number')
    snapshot = {
        'serial': serial,
        'model': drive.get('model', ''),
        'size_bytes': int(drive.get('size_bytes') or 0),
        'result': drive.get('result') or drive.get('status') or drive.get('precheck') or '',
        'precheck': drive.get('precheck', ''),
        'precheck_reason': drive.get('precheck_reason', ''),
        'added_utc': _now(),
    }
    existing = [d for d in data['drives'] if d.get('serial') != serial]
    existing.append(snapshot)
    data['drives'] = existing
    save_project(data)
    return data


def remove_drive(project_id, serial):
    data = load_project(project_id)
    data['drives'] = [d for d in data.get('drives', []) if d.get('serial') != serial]
    save_project(data)
    return data
