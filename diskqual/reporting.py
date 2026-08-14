# reporting.py
import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from .projects import load_project


def _documents_dir():
    configured = os.environ.get('DISKQUAL_EXPORT_DIR')
    if configured:
        return Path(configured).expanduser()

    user_dirs = Path.home() / '.config' / 'user-dirs.dirs'
    try:
        for line in user_dirs.read_text().splitlines():
            if not line.startswith('XDG_DOCUMENTS_DIR='):
                continue
            value = line.split('=', 1)[1].strip().strip('"').replace('$HOME', str(Path.home()))
            if value:
                return Path(value).expanduser() / 'Sirgon DiskQual'
    except OSError:
        pass
    return Path.home() / 'Documents' / 'Sirgon DiskQual'


EXPORTS = _documents_dir() / 'Reports'


def _size_tb(value):
    return float(value or 0) / 1_000_000_000_000


def export_client_csv(project_id):
    project = load_project(project_id)
    folder = EXPORTS / project_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'drives.csv'
    fields = ['serial', 'model', 'size_bytes', 'result', 'precheck', 'precheck_reason', 'added_utc']
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows([{k: d.get(k, '') for k in fields} for d in project.get('drives', [])])
    return path


def export_client_pdf(project_id):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError('reportlab is required to generate PDF reports') from exc

    project = load_project(project_id)
    folder = EXPORTS / project_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'report.pdf'
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    x = 48
    y = height - 48
    c.setFont('Helvetica-Bold', 18)
    c.drawString(x, y, 'DiskQual Drive Qualification Report')
    y -= 28
    c.setFont('Helvetica-Bold', 11)
    c.drawString(x, y, f"Client: {project.get('client') or project.get('name')}")
    y -= 18
    c.setFont('Helvetica', 9)
    c.drawString(x, y, f"Report: {project.get('name')}    Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    y -= 26
    if project.get('notes'):
        c.drawString(x, y, f"Notes: {project['notes'][:100]}")
        y -= 24

    headers = ['Serial', 'Model', 'Size', 'Result']
    widths = [130, 210, 65, 70]
    c.setFont('Helvetica-Bold', 9)
    xx = x
    for header, w in zip(headers, widths):
        c.drawString(xx, y, header)
        xx += w
    y -= 14
    c.line(x, y + 4, width - 48, y + 4)
    c.setFont('Helvetica', 8.5)

    for drive in project.get('drives', []):
        if y < 70:
            c.showPage()
            y = height - 48
            c.setFont('Helvetica', 8.5)
        row = [
            str(drive.get('serial', '')),
            str(drive.get('model', '')),
            f"{_size_tb(drive.get('size_bytes')):.1f} TB",
            str(drive.get('result', '')),
        ]
        xx = x
        for value, w in zip(row, widths):
            c.drawString(xx, y, value[:32])
            xx += w
        y -= 16
    c.save()
    export_client_csv(project_id)
    return path
