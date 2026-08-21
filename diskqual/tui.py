# tui.py
import argparse
import os
from pathlib import Path

from .labels import (
    generate_labels,
    generate_and_print,
    load_label_config,
    print_calibration,
    save_label_config,
)
from .progress import format_duration, load_state, overall_for_drive, weighted_batch_progress
from .projects import create_project, list_projects, load_project, save_project
from .reporting import export_client_pdf

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal
    from textual.screen import ModalScreen, Screen
    from textual.widgets import Button, DataTable, Footer, Header, Input, Static
except ImportError as exc:
    raise SystemExit('Textual is required. Install with: python3 -m pip install textual') from exc

DEFAULT_STATE = Path(os.environ.get('DISKQUAL_STATE', '/opt/diskqual/state.json'))


def _bar(progress, width=18):
    progress = max(0.0, min(1.0, float(progress or 0.0)))
    filled = int(round(progress * width))
    return '█' * filled + '░' * (width - filled)


def _report_result(drive):
    return str(
        drive.get('workflow_status')
        or drive.get('result')
        or drive.get('status')
        or drive.get('precheck')
        or ''
    )


def _status_markup(drive):
    value = str(drive.get('result') or drive.get('status') or drive.get('precheck') or 'WAITING').upper()
    if value in ('PASS', 'COMPLETE'):
        return f'[bold green]{value}[/]'
    if value in ('REVIEW', 'WARNING'):
        return f'[bold yellow]{value}[/]'
    if value in ('REJECT', 'REJECTED', 'BAD', 'FAILED'):
        return f'[bold red]{value}[/]'
    if value == 'RUNNING':
        return '[bold cyan]RUNNING[/]'
    return f'[dim]{value}[/]'


def _demo_state():
    sizes = [6, 6, 4, 4, 4, 2, 4, 4, 4, 2, 8, 12]
    state = {'version': 3, 'batch_id': 'demo_mixed_drive_batch', 'status': 'RUNNING', 'drives': {}}
    for i, tb in enumerate(sizes, start=1):
        serial = f'DEMO{i:02d}SERIAL'
        stage = 'surface-test' if i % 3 else 'smart-long'
        progress = min(0.97, 0.08 + i * 0.057)
        precheck = 'REVIEW' if i in (6, 7) else 'PASS'
        state['drives'][serial] = {
            'id': serial,
            'dev': f'/dev/sd{chr(97+i)}',
            'serial': serial,
            'model': 'Demo Enterprise HDD',
            'size_bytes': tb * 1_000_000_000_000,
            'precheck': precheck,
            'precheck_reason': '130 reallocated sectors' if precheck == 'REVIEW' else 'Baseline SMART acceptable',
            'status': 'RUNNING',
            'stage': stage,
            'stage_progress': progress,
            'overall_progress': min(0.95, 0.05 + progress * 0.76),
            'stage_elapsed_seconds': 1200 + i * 90,
            'stage_eta_seconds': 3600 + (12-i) * 1400,
            'throughput_mib_s': 125 + i * 6 if stage == 'surface-test' else None,
            'message': 'Writing pattern 0x00' if stage == 'surface-test' else 'Self-test routine in progress...',
            'completed_stages': ['baseline-smart', 'smart-short'],
            'result': None,
        }
    return state


class DriveDetails(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def __init__(self, drive):
        super().__init__()
        self.drive = drive

    def compose(self) -> ComposeResult:
        d = self.drive
        pipeline = ['baseline-smart', 'smart-short', 'smart-long', 'surface-write', 'surface-verify', 'final-smart', 'classify']
        completed = set(d.get('completed_stages', []))
        current = d.get('stage', '')
        lines = []
        for stage in pipeline:
            if stage in completed:
                marker = '[green]✓[/]'
            elif stage == current or (stage.startswith('surface-') and current == 'surface-test'):
                marker = '[cyan]▶[/]'
            else:
                marker = '[dim]○[/]'
            lines.append(f'{marker} {stage.replace("-", " ").title()}')
        current_progress = float(d.get('stage_progress') or 0)
        overall = float(d.get('overall_progress') or overall_for_drive(d))
        body = (
            f'[bold]{d.get("model", "UNKNOWN")}[/]\n'
            f'Serial: [bold]{d.get("serial", "UNKNOWN")}[/]    Device: {d.get("dev", "?")}\n'
            f'Capacity: {float(d.get("size_bytes") or 0)/1e12:.1f} TB    Precheck: {_status_markup({"status": d.get("precheck")})}\n'
            f'Reason: {d.get("precheck_reason", "")}\n\n'
            '[bold]Qualification Pipeline[/]\n' + '\n'.join(lines) + '\n\n'
            f'Current  {_bar(current_progress, 28)}  {current_progress*100:5.1f}%\n'
            f'Overall  {_bar(overall, 28)}  {overall*100:5.1f}%\n'
            f'Elapsed: {format_duration(d.get("stage_elapsed_seconds"))}    ETA: {format_duration(d.get("stage_eta_seconds"))}\n'
            f'Throughput: {d.get("throughput_mib_s") or "—"} MiB/s\n\n'
            '[bold]ESC or BACKSPACE — Return to Drive List[/]'
        )
        with Container(id='dialog'):
            yield Static('[bold cyan]SIRGON DISKQUAL — DRIVE DETAILS[/]', classes='dialog-title')
            yield Static(body)

    def action_dismiss(self):
        self.dismiss()


class HelpScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def compose(self) -> ComposeResult:
        text = (
            '[bold cyan]SIRGON DISKQUAL — HELP[/]\n\n'
            '↑ / ↓        Select a drive\n'
            'ENTER        Open drive details\n'
            'R            Client reports\n'
            'L            Labels\n'
            'H            Help\n'
            'Q / Ctrl+C   Exit Sirgon DiskQual display — active tests continue\n\n'
            'Ctrl+Alt+F1  Switch to Linux OS — tests continue\n'
            'Ctrl+Alt+F2  Return to Sirgon DiskQual screen\n\n'
            '[bold]ESC or BACKSPACE — Return[/]'
        )
        with Container(id='dialog'):
            yield Static(text)

    def action_dismiss(self):
        self.dismiss()


class NewReportDialog(ModalScreen):
    def compose(self) -> ComposeResult:
        with Container(id='dialog'):
            yield Static('[bold cyan]SIRGON DISKQUAL — CREATE CLIENT REPORT[/]', classes='dialog-title')
            yield Input(placeholder='Report name (for example: Client A)', id='report-name')
            yield Input(placeholder='Client / customer name', id='client-name')
            yield Input(placeholder='Optional notes', id='report-notes')
            with Horizontal():
                yield Button('Create', variant='success', id='create-report')
                yield Button('Cancel', id='cancel-report')

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'cancel-report':
            self.dismiss(None)
            return
        name = self.query_one('#report-name', Input).value.strip()
        if not name:
            self.query_one('#report-name', Input).focus()
            return
        self.dismiss(create_project(name, self.query_one('#client-name', Input).value, self.query_one('#report-notes', Input).value))


class ReportDriveScreen(Screen):
    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('backspace', 'app.pop_screen', 'Back'),
        Binding('s', 'save_and_back', 'Save Selection'),
        Binding('g', 'generate_report', 'Generate Report'),
        Binding('a', 'select_all', 'Select All'),
        Binding('x', 'clear_all', 'Clear All'),
    ]

    def __init__(self, project_id):
        super().__init__()
        self.project_id = project_id
        self.project = load_project(project_id)
        self.selected = {str(d.get('serial')) for d in self.project.get('drives', [])}
        self.drive_keys = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f"[bold cyan]SIRGON DISKQUAL — REPORT: {self.project.get('name','')}[/]    Client: {self.project.get('client','')}\n"
            'Choose every drive that belongs in this client report. G saves the selection and generates the PDF/CSV report.',
            id='section-title',
        )
        yield DataTable(id='report-drives')
        yield Static('SPACE Toggle   A Select All   X Clear All   S Save Selection   G Generate Report   ESC Return', id='hint')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#report-drives', DataTable)
        table.cursor_type = 'row'
        table.add_columns('Include', 'Device', 'Size', 'Serial', 'Result')
        for d in self.app.drives():
            key = str(d.get('id') or d.get('serial'))
            self.drive_keys.append(key)
            serial = str(d.get('serial') or key)
            table.add_row(
                '☑' if serial in self.selected else '☐',
                Path(d.get('dev','?')).name,
                f"{float(d.get('size_bytes') or 0)/1e12:.1f} TB",
                serial,
                _report_result(d),
                key=key,
            )

    def on_key(self, event):
        if event.key == 'space':
            table = self.query_one('#report-drives', DataTable)
            drive = self.app.drive_by_key(str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value))
            if not drive:
                return
            serial = str(drive.get('serial') or drive.get('id'))
            if serial in self.selected:
                self.selected.remove(serial)
            else:
                self.selected.add(serial)
            table.update_cell_at((table.cursor_row, 0), '☑' if serial in self.selected else '☐')
            event.stop()

    def action_select_all(self):
        self.selected = {str(d.get('serial') or d.get('id')) for d in self.app.drives()}
        self._redraw()

    def action_clear_all(self):
        self.selected.clear()
        self._redraw()

    def _redraw(self):
        table = self.query_one('#report-drives', DataTable)
        for i, d in enumerate(self.app.drives()):
            serial = str(d.get('serial') or d.get('id'))
            table.update_cell_at((i, 0), '☑' if serial in self.selected else '☐')

    def _save_selection(self):
        snapshots = []
        for d in self.app.drives():
            serial = str(d.get('serial') or d.get('id') or '')
            if serial not in self.selected:
                continue
            snapshots.append({
                'serial': serial,
                'model': d.get('model', ''),
                'size_bytes': int(d.get('size_bytes') or 0),
                'result': _report_result(d),
                'workflow_status': d.get('workflow_status', ''),
                'precheck': d.get('precheck', ''),
                'precheck_reason': d.get('precheck_reason', ''),
            })
        self.project['drives'] = snapshots
        save_project(self.project)

    def action_save_and_back(self):
        self._save_selection()
        self.notify(f'Saved {len(self.selected)} drive(s) to report')
        self.app.pop_screen()

    def action_generate_report(self):
        self._save_selection()
        path = export_client_pdf(self.project_id)
        self.notify(f'Generated report: {path}')


class ReportScreen(Screen):
    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('n', 'new_report', 'New Report'),
        Binding('enter', 'open_report', 'Manage Drives'),
        Binding('g', 'generate_report', 'Generate Report'),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('[bold cyan]SIRGON DISKQUAL — CLIENT REPORT BUILDER[/]  — ENTER manages drives; G generates PDF/CSV for the selected report.', id='section-title')
        yield DataTable(id='projects')
        yield Static('ENTER Manage Drives   G Generate Report   N New Report   ESC Return', id='hint')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#projects', DataTable)
        table.cursor_type = 'row'
        table.add_columns('Report', 'Client', 'Drives', 'Updated')
        self.refresh_projects()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.action_open_report()

    def refresh_projects(self):
        table = self.query_one('#projects', DataTable)
        table.clear()
        for p in list_projects():
            table.add_row(p.get('name',''), p.get('client',''), str(len(p.get('drives',[]))), p.get('updated_utc','')[:16].replace('T',' '), key=p['id'])

    def action_new_report(self):
        self.app.push_screen(NewReportDialog(), self._created)

    def _created(self, project):
        if project:
            self.refresh_projects()
            self.notify(f"Created report: {project['name']}")

    def _selected_project_id(self):
        table = self.query_one('#projects', DataTable)
        if table.row_count == 0:
            self.notify('Create a client report first', severity='warning')
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def action_open_report(self):
        project_id = self._selected_project_id()
        if project_id:
            self.app.push_screen(ReportDriveScreen(project_id), self._returned)

    def action_generate_report(self):
        project_id = self._selected_project_id()
        if not project_id:
            return
        project = load_project(project_id)
        if not project.get('drives'):
            self.notify('This report has no drives. Press ENTER to choose drives first.', severity='warning')
            return
        path = export_client_pdf(project_id)
        self.notify(f'Generated report: {path}')

    def _returned(self, _result=None):
        self.refresh_projects()


class LabelScreen(Screen):
    BINDINGS = [
        Binding('escape', 'app.pop_screen', 'Back'),
        Binding('g', 'generate', 'Generate PDF'),
        Binding('p', 'print_labels', 'Print Labels'),
        Binding('c', 'calibrate', 'Print Calibration'),
    ]

    def compose(self) -> ComposeResult:
        cfg = load_label_config()
        feed = str(cfg.get('feed_orientation') or 'width').lower()
        yield Header(show_clock=True)
        yield Static('[bold cyan]SIRGON DISKQUAL — LABELS[/] — Configure physical label size, feed orientation, and printer profile.', id='section-title')
        with Horizontal(id='label-settings'):
            yield Input(value=str(cfg['width_in']), id='label-width', placeholder='Width inches')
            yield Input(value=str(cfg['height_in']), id='label-height', placeholder='Height inches')
            yield Input(value=feed, id='label-feed', placeholder='Feed: width or height')
            yield Input(value=str(cfg.get('printer','')), id='label-printer', placeholder='CUPS printer name')
        with Horizontal(id='label-settings-2'):
            yield Input(value=str(cfg.get('cups_media','')), id='label-media', placeholder='CUPS media (optional)')
            yield Input(value=str(cfg.get('x_offset_in', 0.0)), id='label-x-offset', placeholder='X offset inches')
            yield Input(value=str(cfg.get('y_offset_in', 0.0)), id='label-y-offset', placeholder='Y offset inches')
        yield Static(
            'Width × Height is the physical label size. Feed tells DiskQual which dimension travels through a roll printer.\n'
            'P prints selected labels directly through CUPS at 100% scale. C prints one calibration label. G only generates the PDF.\n'
            'Leave CUPS media blank to request an exact custom media size; use X/Y offsets only after measuring the calibration label.',
            id='hint',
        )
        yield DataTable(id='label-drives')
        yield Static('G Generate PDF   P Print Labels   C Print Calibration   A Select All   Q Qualified/Review   F Failed/Rejected   ESC Return', id='hint2')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#label-drives', DataTable)
        table.cursor_type = 'row'
        table.add_columns('Print', 'Device', 'Size', 'Serial', 'Result')
        for d in self.app.drives():
            result = str(d.get('workflow_status') or d.get('result') or d.get('status') or d.get('precheck') or '')
            table.add_row('☐', Path(d.get('dev','?')).name, f"{float(d.get('size_bytes') or 0)/1e12:.1f} TB", d.get('serial',''), result, key=d.get('id') or d.get('serial'))
        self.selected = set()

    def on_key(self, event):
        if event.key == 'space':
            table = self.query_one('#label-drives', DataTable)
            key = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.add(key)
            table.update_cell_at((table.cursor_row, 0), '☑' if key in self.selected else '☐')
            event.stop()
        elif event.key == 'a':
            self.selected = {str(d.get('id') or d.get('serial')) for d in self.app.drives()}
            self._redraw_checks()
        elif event.key == 'q':
            self.selected = {
                str(d.get('id') or d.get('serial'))
                for d in self.app.drives()
                if str(d.get('workflow_status') or d.get('result') or d.get('status') or '').upper() in ('QUALIFIED', 'REVIEW', 'PASS')
            }
            self._redraw_checks()
        elif event.key == 'f':
            self.selected = {
                str(d.get('id') or d.get('serial'))
                for d in self.app.drives()
                if str(d.get('workflow_status') or d.get('result') or d.get('status') or '').upper() in ('FAILED', 'BAD', 'REJECT', 'REJECTED')
            }
            self._redraw_checks()

    def _redraw_checks(self):
        table = self.query_one('#label-drives', DataTable)
        for i, d in enumerate(self.app.drives()):
            key = str(d.get('id') or d.get('serial'))
            table.update_cell_at((i, 0), '☑' if key in self.selected else '☐')

    def _read_config(self):
        config = load_label_config()
        try:
            config['width_in'] = float(self.query_one('#label-width', Input).value)
            config['height_in'] = float(self.query_one('#label-height', Input).value)
            config['x_offset_in'] = float(self.query_one('#label-x-offset', Input).value or 0)
            config['y_offset_in'] = float(self.query_one('#label-y-offset', Input).value or 0)
        except ValueError:
            self.notify('Label size and calibration offsets must be numbers', severity='error')
            return None
        feed = self.query_one('#label-feed', Input).value.strip().lower()
        if feed not in ('width', 'height'):
            self.notify('Feed orientation must be width or height', severity='error')
            return None
        config['feed_orientation'] = feed
        config['printer'] = self.query_one('#label-printer', Input).value.strip()
        config['cups_media'] = self.query_one('#label-media', Input).value.strip()
        save_label_config(config)
        return config

    def _chosen_drives(self):
        return [d for d in self.app.drives() if str(d.get('id') or d.get('serial')) in self.selected]

    def action_generate(self):
        config = self._read_config()
        if not config:
            return
        chosen = self._chosen_drives()
        if not chosen:
            self.notify('Select at least one drive', severity='warning')
            return
        path = generate_labels(chosen, config=config)
        self.notify(f'Generated {path}')

    def action_print_labels(self):
        config = self._read_config()
        if not config:
            return
        if not config.get('printer'):
            self.notify('Enter a CUPS printer name before printing', severity='warning')
            return
        chosen = self._chosen_drives()
        if not chosen:
            self.notify('Select at least one drive', severity='warning')
            return
        try:
            path, job = generate_and_print(chosen, printer=config['printer'], config=config)
        except RuntimeError as exc:
            self.notify(str(exc), severity='error', timeout=8)
            return
        self.notify(f'Print submitted: {job or config["printer"]} — {path}', timeout=8)

    def action_calibrate(self):
        config = self._read_config()
        if not config:
            return
        if not config.get('printer'):
            self.notify('Enter a CUPS printer name before calibration', severity='warning')
            return
        try:
            path, job = print_calibration(printer=config['printer'], config=config)
        except RuntimeError as exc:
            self.notify(str(exc), severity='error', timeout=8)
            return
        self.notify(f'Calibration submitted: {job or config["printer"]} — {path}', timeout=8)


class DiskQualApp(App):
    TITLE = 'Sirgon DiskQual'
    SUB_TITLE = 'Disk Qualification Station'

    CSS = """
    Screen { background: #081018; color: #d9e2ec; }
    Header { background: #102a43; color: white; }
    Footer { background: #102a43; color: #d9e2ec; }
    #title { height: 3; padding: 0 2; background: #0b1f33; color: #7dd3fc; content-align: left middle; text-style: bold; }
    #summary { height: 5; margin: 0 1; padding: 0 2; border: round #1d4ed8; }
    #drive-table { height: 1fr; margin: 0 1; border: round #334e68; }
    DataTable > .datatable--header { background: #102a43; color: #f0f4f8; text-style: bold; }
    DataTable > .datatable--cursor { background: #164e63; color: white; }
    #hint, #hint2 { height: 3; padding: 0 2; color: #9fb3c8; }
    #section-title { height: 4; padding: 0 2; content-align: left middle; background: #0b1f33; }
    #dialog { width: 78%; height: auto; max-height: 90%; padding: 1 2; border: double #38bdf8; background: #0b1725; align: center middle; }
    .dialog-title { height: 2; text-align: center; }
    ModalScreen { align: center middle; background: rgba(0,0,0,0.65); }
    #label-settings, #label-settings-2 { height: 3; margin: 0 1; }
    #label-settings Input, #label-settings-2 Input { width: 1fr; margin-right: 1; }
    #label-drives, #projects, #report-drives { height: 1fr; margin: 0 1; border: round #334e68; }
    """

    BINDINGS = [
        Binding('h', 'help', 'Help'),
        Binding('r', 'reports', 'Client Reports'),
        Binding('l', 'labels', 'Labels'),
        Binding('ctrl+r', 'refresh_now', 'Refresh'),
        Binding('q', 'quit_display', 'Exit Display'),
        Binding('ctrl+c', 'quit_display', 'Exit Display', show=False),
    ]

    def __init__(self, state_path=DEFAULT_STATE, demo=False):
        super().__init__()
        self.state_path = Path(state_path)
        self.demo = demo
        self.state = _demo_state() if demo else None
        self.drive_keys = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('SIRGON DISKQUAL  •  Disk Qualification Station', id='title')
        yield Static(id='summary')
        yield DataTable(id='drive-table')
        yield Static('↑/↓ Select Drive   ENTER Drive Details   R Client Reports   L Labels   H Help   Q Exit Display', id='hint')
        yield Static('Ctrl+Alt+F1  Switch to Linux OS — Tests Continue', id='hint2')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#drive-table', DataTable)
        table.cursor_type = 'row'
        table.zebra_stripes = True
        table.add_columns('Dev', 'Size', 'Serial', 'Status', 'Current Test', 'Current', 'Overall', 'ETA')
        self.refresh_state()
        self.set_interval(2.0, self.refresh_state)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == 'drive-table':
            drive = self.selected_drive()
            if drive:
                self.push_screen(DriveDetails(drive))

    def drives(self):
        return list((self.state or {}).get('drives', {}).values())

    def drive_by_key(self, key):
        return (self.state or {}).get('drives', {}).get(str(key))

    def selected_drive(self):
        table = self.query_one('#drive-table', DataTable)
        if not self.drive_keys or table.row_count == 0:
            return None
        idx = max(0, min(table.cursor_row, len(self.drive_keys)-1))
        return self.drive_by_key(self.drive_keys[idx])

    def refresh_state(self):
        if not self.demo:
            state = load_state(self.state_path)
            if state:
                self.state = state
        if not self.state:
            self.query_one('#summary', Static).update('[yellow]No Sirgon DiskQual state found.[/] Set DISKQUAL_STATE or use --demo.')
            return
        drives = self.drives()
        batch = weighted_batch_progress(self.state)
        counts = {'complete':0, 'running':0, 'review':0, 'failed':0, 'rejected':0}
        for d in drives:
            status = str(d.get('result') or d.get('status') or '').upper()
            if status in ('PASS','COMPLETE'):
                counts['complete'] += 1
            elif status == 'RUNNING':
                counts['running'] += 1
            elif status == 'REVIEW':
                counts['review'] += 1
            elif status in ('FAILED','BAD'):
                counts['failed'] += 1
            elif status in ('REJECT','REJECTED'):
                counts['rejected'] += 1
        self.query_one('#summary', Static).update(
            f"[bold]Batch:[/] {self.state.get('batch_id','unknown')}    [bold cyan]{self.state.get('status','UNKNOWN')}[/]\n"
            f"TOTAL  [cyan]{_bar(batch, 42)}[/]  [bold]{batch*100:5.1f}%[/]\n"
            f"{counts['running']} testing   [green]{counts['complete']} complete[/]   [yellow]{counts['review']} review[/]   [red]{counts['failed']} failed   {counts['rejected']} rejected[/]"
        )
        table = self.query_one('#drive-table', DataTable)
        cursor = table.cursor_row
        table.clear()
        self.drive_keys = []
        for d in drives:
            key = str(d.get('id') or d.get('serial'))
            self.drive_keys.append(key)
            stage_progress = float(d.get('stage_progress') or 0)
            overall = float(d.get('overall_progress') or overall_for_drive(d))
            stage = str(d.get('stage') or 'waiting').replace('-', ' ').title()
            table.add_row(
                Path(d.get('dev','?')).name,
                f"{float(d.get('size_bytes') or 0)/1e12:.1f}T",
                str(d.get('serial','')),
                _status_markup(d),
                stage,
                f"{_bar(stage_progress, 10)} {stage_progress*100:4.0f}%",
                f"{_bar(overall, 10)} {overall*100:4.0f}%",
                format_duration(d.get('stage_eta_seconds')),
                key=key,
            )
        if table.row_count:
            table.move_cursor(row=max(0, min(cursor, table.row_count-1)))

    def action_help(self):
        self.push_screen(HelpScreen())

    def action_reports(self):
        self.push_screen(ReportScreen())

    def action_labels(self):
        self.push_screen(LabelScreen())

    def action_refresh_now(self):
        self.refresh_state()

    def action_quit_display(self):
        self.exit()


def main():
    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()
    DiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
