# labels.py
import json
import os
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
CONFIG_VERSION = 2
DEFAULT = {
    'config_version': CONFIG_VERSION,
    # Use the conventional stock dimensions printed on label packaging.
    # DYMO 30323, for example, is sold as 2-1/8 x 4 inches.
    'width_in': 2.125,
    'height_in': 4.0,
    # The named dimension that advances through a roll printer.
    'feed_orientation': 'height',
    'printer': '',
    'date_format': '%Y-%m-%d',
}


def _close(a, b, tolerance=0.01):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def _migrate_legacy_config(data):
    """Normalize label settings written by pre-v2 beta builds.

    Earlier betas changed the meaning/order of width, height, and feed while
    the label UI was being developed. The common DYMO 30323 stock could
    therefore remain persisted as 4 x 2.125 with a feed value that produces a
    landscape PDF page. That legacy state survives application upgrades.

    Only the known 2-1/8 x 4 stock is normalized automatically. Other custom
    sizes are preserved exactly so DiskQual remains printer/stock agnostic.
    """
    migrated = dict(data)
    version = int(migrated.get('config_version') or 0)
    if version >= CONFIG_VERSION:
        return migrated

    width = migrated.get('width_in', DEFAULT['width_in'])
    height = migrated.get('height_in', DEFAULT['height_in'])

    if (_close(width, 4.0) and _close(height, 2.125)) or (
        _close(width, 2.125) and _close(height, 4.0)
    ):
        # Conventional package notation is 2-1/8 x 4. The 4-inch dimension
        # advances through the roll, yielding a 2.125 x 4-inch PDF media page.
        migrated['width_in'] = 2.125
        migrated['height_in'] = 4.0
        migrated['feed_orientation'] = 'height'

    migrated['config_version'] = CONFIG_VERSION
    return migrated


def load_label_config():
    if not CONFIG.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    data = _migrate_legacy_config(data)
    return {**DEFAULT, **data}


def save_label_config(config):
    BASE.mkdir(parents=True, exist_ok=True)
    normalized = {**DEFAULT, **config, 'config_version': CONFIG_VERSION}
    CONFIG.write_text(json.dumps(normalized, indent=2))


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
    if workflow == 'READY_FOR_SURFACE':
        return 'READY FOR SURFACE'
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


def _stage_result(drive, result_key, stage_name, qualified):
    value = drive.get(result_key)
    if value:
        return str(value).upper()
    if stage_name in set(drive.get('completed_stages') or []):
        return 'PASS'
    if qualified:
        # A drive cannot reach final QUALIFIED/PASS state without completing
        # the required qualification pipeline. This also keeps labels useful
        # for older state files that predate per-stage result fields.
        return 'PASS'
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
    """Return PDF media and artwork geometry for one-label-per-page roll output.

    Width and height are the conventional dimensions shown on the package.
    feed_orientation identifies which named dimension advances through the
    printer. The PDF page is always cross-feed x feed. The artwork is drawn in
    the readable label orientation and rotated onto that media page.
    """
    physical_width = float(config['width_in']) * inch
    physical_height = float(config['height_in']) * inch
    feed = str(config.get('feed_orientation') or 'height').lower()
    if feed not in ('width', 'height'):
        raise ValueError('feed_orientation must be width or height')

    if feed == 'height':
        page_width = physical_width
        page_height = physical_height
        artwork_width = physical_height
        artwork_height = physical_width
    else:
        page_width = physical_height
        page_height = physical_width
        artwork_width = physical_width
        artwork_height = physical_height

    return page_width, page_height, artwork_width, artwork_height


def label_geometry_inches(config=None):
    """Return user-visible physical/PDF geometry in inches."""
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
        # Roll printers advance along the PDF page height. Draw the readable
        # label horizontally, then rotate it onto the physical media page.
        c.saveState()
        c.translate(0, page_height)
        c.rotate(-90)
        _draw_label(c, drive, artwork_width, artwork_height, config)
        c.restoreState()
        c.showPage()

    c.save()
    record_output('labels', output)
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
