# labels.py
import json
import os
import shutil
import subprocess
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
CONFIG = BASE / 'label-config.json'
LABELS = BASE / 'labels'
DEFAULT = {
    'width_in': 4.0,
    'height_in': 2.125,
    'printer': '',
    'date_format': '%Y-%m-%d',
}


def load_label_config():
    if not CONFIG.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    return {**DEFAULT, **data}


def save_label_config(config):
    BASE.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({**DEFAULT, **config}, indent=2))


def available_printers():
    if not shutil.which('lpstat'):
        return []
    p = subprocess.run(['lpstat', '-a'], text=True, capture_output=True)
    return [line.split()[0] for line in p.stdout.splitlines() if line.strip()]


def _reason(drive):
    reason = drive.get('precheck_reason') or drive.get('message') or ''
    return reason.strip() or 'Qualification completed'


def generate_labels(drives, output=None, config=None):
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
    except ImportError as exc:
        raise RuntimeError('reportlab is required to generate labels') from exc

    config = {**load_label_config(), **(config or {})}
    LABELS.mkdir(parents=True, exist_ok=True)
    output = Path(output or LABELS / 'sirgon-diskqual-labels.pdf')
    width = float(config['width_in']) * inch
    height = float(config['height_in']) * inch
    c = canvas.Canvas(str(output), pagesize=(width, height))

    for drive in drives:
        serial = str(drive.get('serial') or drive.get('id') or 'UNKNOWN')
        model = str(drive.get('model') or 'UNKNOWN')
        size = float(drive.get('size_bytes') or 0) / 1_000_000_000_000
        result = str(drive.get('result') or drive.get('status') or drive.get('precheck') or 'UNKNOWN').upper()
        if result == 'COMPLETE':
            result = 'PASS'
        margin = 0.10 * inch
        c.setLineWidth(1.4)
        c.roundRect(margin, margin, width - 2 * margin, height - 2 * margin, 7, stroke=1, fill=0)
        x = 0.18 * inch
        y = height - 0.30 * inch
        c.setFont('Helvetica-Bold', 15)
        c.drawString(x, y, f'SIRGON DISKQUAL - {result}')
        y -= 0.31 * inch
        c.setFont('Helvetica-Bold', 10)
        c.drawString(x, y, f'{model} - {size:.1f} TB')
        y -= 0.25 * inch
        c.setFont('Helvetica', 9)
        c.drawString(x, y, f'Serial: {serial}')
        y -= 0.24 * inch
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(x, y, 'Result detail:')
        c.setFont('Helvetica', 8.2)
        text = _reason(drive)
        max_chars = 55
        lines = [text[i:i + max_chars] for i in range(0, len(text), max_chars)][:3]
        for line in lines:
            y -= 0.18 * inch
            c.drawString(x + 0.12 * inch, y, line)
        c.showPage()
    c.save()
    return output


def print_pdf(path, printer=''):
    if not shutil.which('lp'):
        raise RuntimeError('CUPS lp command is not installed')
    cmd = ['lp']
    printer = printer or load_label_config().get('printer', '')
    if printer:
        cmd += ['-d', printer]
    cmd.append(str(path))
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'Printing failed')
    return p.stdout.strip()
