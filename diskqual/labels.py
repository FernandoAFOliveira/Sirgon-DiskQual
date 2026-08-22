# labels.py
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .exports import LABELS_DIR, record_output


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
LABELS = LABELS_DIR
CONFIG_VERSION = 3
DEFAULT = {
    'config_version': CONFIG_VERSION,
    'width_in': 2.125,
    'height_in': 4.0,
    'feed_orientation': 'height',
    'printer': '',
    'date_format': '%Y-%m-%d',
    'x_offset_in': 0.0,
    'y_offset_in': 0.0,
    'cups_media': '',
}


def _close(a, b, tolerance=0.01):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def _migrate_legacy_config(data):
    migrated = dict(data)
    version = int(migrated.get('config_version') or 0)
    if version >= CONFIG_VERSION:
        return migrated
    width = migrated.get('width_in', DEFAULT['width_in'])
    height = migrated.get('height_in', DEFAULT['height_in'])
    if version < 2 and ((_close(width, 4.0) and _close(height, 2.125)) or (_close(width, 2.125) and _close(height, 4.0))):
        migrated['width_in'] = 2.125
        migrated['height_in'] = 4.0
        migrated['feed_orientation'] = 'height'
    migrated.setdefault('x_offset_in', 0.0)
    migrated.setdefault('y_offset_in', 0.0)
    migrated.setdefault('cups_media', '')
    migrated['config_version'] = CONFIG_VERSION
    return migrated


def load_label_config():
    if not CONFIG.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    return {**DEFAULT, **_migrate_legacy_config(data)}


def save_label_config(config):
    BASE.mkdir(parents=True, exist_ok=True)
    normalized = {**DEFAULT, **config, 'config_version': CONFIG_VERSION}
    CONFIG.write_text(json.dumps(normalized, indent=2))


def available_printers():
    if not shutil.which('lpstat'):
        return []
    p = subprocess.run(['lpstat', '-a'], text=True, capture_output=True)
    return [line.split()[0] for line in p.stdout.splitlines() if line.strip()]


def printer_options(printer):
    if not printer or not shutil.which('lpoptions'):
        return ''
    p = subprocess.run(['lpoptions', '-p', printer, '-l'], text=True, capture_output=True)
    return p.stdout if p.returncode == 0 else ''


def _workflow_result(drive):
    workflow = str(drive.get('workflow_status') or '').upper()
    if workflow == 'QUALIFIED': return 'QUALIFIED'
    if workflow == 'REVIEW': return 'REVIEW'
    if workflow in ('REJECTED', 'REJECT'): return 'REJECTED'
    if workflow == 'READY_FOR_SURFACE': return 'READY FOR SURFACE'
    result = str(drive.get('result') or drive.get('status') or drive.get('precheck') or 'UNKNOWN').upper()
    return 'PASS' if result == 'COMPLETE' else result


def _qualification_date(drive, date_format):
    stamp = drive.get('surface_utc') or drive.get('smart_long_utc')
    if stamp:
        try:
            return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).strftime(date_format)
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime(date_format)


def _stage_result(drive, result_key, stage_name, qualified):
    value = drive.get(result_key)
    if value: return str(value).upper()
    if stage_name in set(drive.get('completed_stages') or []): return 'PASS'
    if qualified: return 'PASS'
    return 'UNKNOWN'


def _draw_label(c, drive, width, height, config):
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
        y -= 0.25 * inch
        c.setFont('Helvetica-Bold', 8.8)
        smart_short = _stage_result(drive, 'smart_short_result', 'smart-short', qualified)
        smart_long = _stage_result(drive, 'smart_long_result', 'smart-long', qualified)
        surface = _stage_result(drive, 'surface_result', 'surface-verify', qualified)
        c.drawString(x, y, f'SMART Short: {smart_short}')
        y -= 0.18 * inch
        c.drawString(x, y, f'SMART Long: {smart_long}')
        y -= 0.18 * inch
        c.drawString(x, y, f'Surface Write + Verify: {"PASS" if surface in ("QUALIFIED", "PASS") else surface}')
        y -= 0.18 * inch
        c.drawString(x, y, 'Final Qualification: PASS')
        y -= 0.20 * inch
        c.setFont('Helvetica', 8.2)
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


def _roll_geometry(config, inch):
    """Return CUPS-page and human-readable artwork dimensions.

    The PDF page follows the roll: cross-feed x feed. Artwork follows the label
    as the operator reads it. When height is the feed dimension, readable
    artwork is rotated onto the portrait roll page.
    """
    width = float(config['width_in']) * inch
    height = float(config['height_in']) * inch
    feed = str(config.get('feed_orientation') or 'height').lower()
    if feed == 'height':
        return width, height, height, width
    if feed == 'width':
        return height, width, width, height
    raise ValueError('feed_orientation must be width or height')


def label_geometry_inches(config=None):
    config = {**load_label_config(), **(config or {})}
    width = float(config['width_in'])
    height = float(config['height_in'])
    feed = str(config.get('feed_orientation') or 'height').lower()
    if feed == 'height':
        page_width, page_height = width, height
    elif feed == 'width':
        page_width, page_height = height, width
    else:
        raise ValueError('feed_orientation must be width or height')
    return {
        'stock_width': width,
        'stock_height': height,
        'feed_orientation': feed,
        'page_width': page_width,
        'page_height': page_height,
    }


def _apply_artwork_transform(c, page_width, page_height, config, inch):
    """Place readable artwork on roll media at true physical scale.

    X/Y offsets are defined in the final readable-label view, not in the raw
    portrait CUPS page axes. Positive X moves artwork right; positive Y moves
    artwork up when looking at the finished label.
    """
    xoff = float(config.get('x_offset_in') or 0.0) * inch
    yoff = float(config.get('y_offset_in') or 0.0) * inch
    feed = str(config.get('feed_orientation') or 'height').lower()
    if feed == 'height':
        c.translate(yoff, page_height + xoff)
        c.rotate(-90)
    elif feed == 'width':
        c.translate(page_width - yoff, xoff)
        c.rotate(90)
    else:
        raise ValueError('feed_orientation must be width or height')


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
    page_width, page_height, artwork_width, artwork_height = _roll_geometry(config, inch)
    c = canvas.Canvas(str(output), pagesize=(page_width, page_height))
    for drive in drives:
        c.saveState()
        _apply_artwork_transform(c, page_width, page_height, config, inch)
        _draw_label(c, drive, artwork_width, artwork_height, config)
        c.restoreState()
        c.showPage()
    c.save()
    record_output('labels', output)
    return output


def generate_calibration_label(output=None, config=None):
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    config = {**load_label_config(), **(config or {})}
    LABELS.mkdir(parents=True, exist_ok=True)
    output = Path(output or LABELS / 'sirgon-diskqual-calibration.pdf')
    page_width, page_height, artwork_width, artwork_height = _roll_geometry(config, inch)
    c = canvas.Canvas(str(output), pagesize=(page_width, page_height))
    c.saveState()
    _apply_artwork_transform(c, page_width, page_height, config, inch)
    m = 0.10 * inch
    c.setLineWidth(1)
    c.rect(m, m, artwork_width - 2*m, artwork_height - 2*m)
    c.line(artwork_width/2, m, artwork_width/2, artwork_height-m)
    c.line(m, artwork_height/2, artwork_width-m, artwork_height/2)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(m + 0.06*inch, artwork_height - m - 0.18*inch, 'SIRGON DISKQUAL - PRINT CALIBRATION')
    c.setFont('Helvetica', 8)
    c.drawString(m + 0.06*inch, m + 0.08*inch, 'Border = 0.10 in from physical label edge; measure any shift/clipping.')
    for i in range(1, int(artwork_width / (0.1*inch))):
        x = i * 0.1 * inch
        tick = 0.05*inch if i % 5 else 0.09*inch
        c.line(x, m, x, m+tick)
    for i in range(1, int(artwork_height / (0.1*inch))):
        y = i * 0.1 * inch
        tick = 0.05*inch if i % 5 else 0.09*inch
        c.line(m, y, m+tick, y)
    c.restoreState()
    c.showPage()
    c.save()
    record_output('labels', output)
    return output


def _custom_media_name(config):
    geom = label_geometry_inches(config)
    return f'Custom.{geom["page_width"]:.3f}x{geom["page_height"]:.3f}in'


def print_pdf(path, printer='', config=None):
    if not shutil.which('lp'):
        raise RuntimeError('CUPS lp command is not installed')
    config = {**load_label_config(), **(config or {})}
    printer = printer or config.get('printer', '')
    cmd = ['lp']
    if printer:
        cmd += ['-d', printer]
    media = str(config.get('cups_media') or '').strip() or _custom_media_name(config)
    cmd += ['-o', f'media={media}', '-o', 'scaling=100', str(path)]
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode:
        detail = p.stderr.strip() or p.stdout.strip() or 'Printing failed'
        raise RuntimeError(f'{detail}\nCUPS command: {" ".join(cmd)}')
    return p.stdout.strip()


def generate_and_print(drives, printer='', config=None, output=None):
    config = {**load_label_config(), **(config or {})}
    path = generate_labels(drives, output=output, config=config)
    job = print_pdf(path, printer=printer, config=config)
    return path, job


def print_calibration(printer='', config=None):
    config = {**load_label_config(), **(config or {})}
    path = generate_calibration_label(config=config)
    job = print_pdf(path, printer=printer, config=config)
    return path, job
