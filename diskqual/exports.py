# exports.py
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _xdg_documents_dir():
    user_dirs = Path.home() / '.config' / 'user-dirs.dirs'
    try:
        for line in user_dirs.read_text().splitlines():
            if not line.startswith('XDG_DOCUMENTS_DIR='):
                continue
            value = line.split('=', 1)[1].strip().strip('"').replace('$HOME', str(Path.home()))
            if value:
                return Path(value).expanduser()
    except OSError:
        pass
    return Path.home() / 'Documents'


def export_root():
    """Return the user-facing DiskQual export root.

    DISKQUAL_EXPORT_DIR overrides the complete root. Otherwise use the user's
    XDG Documents directory so reports and labels are visible in normal file
    managers without enabling hidden-file display.
    """
    configured = os.environ.get('DISKQUAL_EXPORT_DIR')
    if configured:
        return Path(configured).expanduser()
    return _xdg_documents_dir() / 'Sirgon DiskQual'


EXPORT_ROOT = export_root()
REPORTS_DIR = EXPORT_ROOT / 'Reports'
LABELS_DIR = EXPORT_ROOT / 'Labels'


def _state_path():
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state'))
    return base / 'sirgon-diskqual' / 'last-outputs.json'


def record_output(kind, path):
    """Remember the latest generated user-facing artifact for UI discovery."""
    kind = str(kind or '').strip().lower()
    if kind not in ('report', 'labels'):
        raise ValueError('Output kind must be report or labels')

    state_path = _state_path()
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}

    state[f'last_{kind}'] = str(Path(path).expanduser())
    state[f'last_{kind}_utc'] = datetime.now(timezone.utc).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


def output_locations():
    """Return persistent export locations plus the latest generated files."""
    state_path = _state_path()
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}

    return {
        'root': str(EXPORT_ROOT),
        'reports': str(REPORTS_DIR),
        'labels': str(LABELS_DIR),
        'last_report': str(state.get('last_report') or ''),
        'last_labels': str(state.get('last_labels') or ''),
    }
