# labels.py
import json
import os
import shutil
import subprocess
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


BASE = _default_base()
CONFIG = BASE / 'label-config.json'
LABELS = _documents_dir() / 'Labels'
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


def _workflow_result(drive):
    workflow = str(drive.get('workflow_status') or '').upper()
    if workflow == 'QUALIFIED':
        return 'QUALIFIED'
    if workflow == 'REVIEW':
        return 'REVIEW'
    if workflow in ('REJECTED', 'REJECT'):
        return 'REJECTED'
    result = str(drive.get('result') or drive.get('status') or drive.get('precheck') or 'UNKNOWN').upper()
    if result == 'COMPLETE':
        return 'PASS'
    return result


def _qualification_date(drive, date_format):
    stamp = drive.get('surface_utc') or drive.get('smart_long_utc')
    if stamp:
        try:
            return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).strftime(date_format)
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime(date_format)


def _draw_landscape_label(c, drive, width, height, config):
    from reportlab.lib.units import inch

    serial = str(drive.get('serial') or drive.get('id') or 'UNKNOWN')
    model = str(drive.get('model') or 'UNKNOWN')
    size = float(drive.get('size_bytes') or 0) / 1_000_000_000_000
    result = _workflow_result(drive)
    qualified = result in ('QUALIFIED', 'PASS')

    margin = 0.10 * inch
    c.setLineWidth(1.4)
    c.roundRect(margin, margin, width - 2 * margin, height - 2 * margin, 7, stroke=1, fill=0)
    x = 0.18 * inch
    y = height - 0.30 * inch

    c.setFont('Helvetica-Bold', 15)
    c.drawString(x, y, f'SIRGON DISKQUAL - {result}')

    y -= 0.30 * inch
    c.setFont('Helvetica-Bold', 10)
    c.drawString(x, y, model)

    y -= 0.22 * inch
    c.setFont('Helvetica', 9.5)
    c.drawString(x, y, f'Capacity: {size:.1f} TB     Serial: {serial}')

    if qualified:
        y -= 0.27 * inch
        c.setFont('Helvetica-Bold', 9)
        smart_long = str(drive.get('smart_long_result') or 'PASS').upper()
        surface = str(drive.get('surface_result') or 'PASS').upper()
        c.drawString(x, y, f'SMART Long: {smart_long}')
        y -= 0.20 * inch
        c.drawString(x, y, f'Surface Write + Verify: {"PASS" if surface in ("QUALIFIED", "PASS") else surface}')
        y -= 0.20 * inch
        c.drawString(x, y, 'Final Qualification: PASS')
        y -= 0.22 * inch
        c.setFont('Helvetica', 8.5)
        c.drawString(x, y, f'Qualified: {_qualification_date(drive, config["date_format"])}')
    else:
        y -= 0.27 * inch
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(x, y, 'Result detail:')
        c.setFont('Helvetica', 8.2)
        detail = str(drive.get('surface_detail') or drive.get('smart_long_detail') or drive.get('precheck_reason') or drive.get('message') or 'Qualification result recorded')
        for line in [detail[i:i + 55] for i in range(0, len(detail), 55)][:3]:
            y -= 0.18 * inch
            c.drawString(x + 0.12 * inch, y, line)


def generate_labels(drives, output=None, config=None):
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
    except ImportError as exc:
        raise RuntimeError('reportlab is required to generate labels') from exc

    config = {**load_label_config(), **(config or {})}
    LABELS.mkdir(parents=True, exist_ok=True)
    output = Path(output or LABELS / 'sirgon-diskqual-labels.pdf')
    output.parent.mkdir(parents=True, exist_ok=True)

    physical_width = float(config['width_in']) * inch
    physical_height = float(config['height_in']) * inch

    # Roll printers feed the long dimension through the mechanism. Keep the
    # shorter physical dimension across the print head and rotate landscape
    # artwork into the portrait feed page. This remains printer-agnostic.
    rotate = physical_width > physical_height
    page_width = min(physical_width, physical_height) if rotate else physical_width
    page_height = max(physical_width, physical_height) if rotate else physical_height
    c = canvas.Canvas(str(output), pagesize=(page_width, page_height))

    for drive in drives:
        if rotate:
            c.saveState()
            c.translate(0, page_height)
            c.rotate(-90)
            _draw_landscape_label(c, drive, physical_width, physical_height, config)
            c.restoreState()
        else:
            _draw_landscape_label(c, drive, physical_width, physical_height, config)
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
