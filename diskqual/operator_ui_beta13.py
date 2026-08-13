# operator_ui_beta13.py
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from .operator_ui import OperatorDiskQualApp as BaseOperatorDiskQualApp
from .physical import drive_location
from .progress import format_duration, overall_for_drive
from .tui import _bar, _status_markup


class DriveDetailsWithLocation(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def __init__(self, drive):
        super().__init__()
        self.drive = drive

    def compose(self) -> ComposeResult:
        d = self.drive
        location = drive_location(d.get('dev'))
        location_text = location.get('label', 'unavailable')
        locate_text = 'yes' if location.get('locate_supported') else 'no'
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
            f'Location: [bold]{location_text}[/]    Locate LED: {locate_text}\n'
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


class OperatorDiskQualApp(BaseOperatorDiskQualApp):
    def on_mount(self):
        table = self.query_one('#drive-table', DataTable)
        table.cursor_type = 'row'
        table.zebra_stripes = True
        table.add_columns('Sel', 'Dev', 'Location', 'Size', 'Serial', 'Status', 'Current Test', 'Current', 'Overall', 'ETA')
        self.refresh_state()
        self.set_interval(2.0, self.refresh_state)
        if not self.demo:
            self._start_smart_observer()
            self.set_interval(15.0, self._start_smart_observer)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == 'drive-table':
            drive = self.selected_drive()
            if drive:
                self.push_screen(DriveDetailsWithLocation(drive))

    def _render_drive_rows(self, drives):
        table = self.query_one('#drive-table', DataTable)
        cursor = table.cursor_row
        table.clear()
        self.drive_keys = []
        for drive in drives:
            key = str(drive.get('id') or drive.get('serial'))
            serial = str(drive.get('serial') or key)
            self.drive_keys.append(key)
            location = drive_location(drive.get('dev'))
            drive['location'] = location.get('label', 'unavailable')
            drive['locate_supported'] = bool(location.get('locate_supported'))
            active = str(drive.get('status') or '').upper() == 'RUNNING'
            stage_progress = float(drive.get('stage_progress') or 0)
            overall = float(drive.get('overall_progress') or overall_for_drive(drive))
            workflow = str(drive.get('workflow_status') or '').upper()
            stage = str(drive.get('stage') or '').replace('-', ' ').title()
            if not stage:
                stage = 'Ready for Surface' if workflow == 'READY_FOR_SURFACE' else 'Idle'
            show_progress = active or bool(drive.get('active_job_id')) or bool(drive.get('firmware_observed'))
            table.add_row(
                '[green]✓[/]' if serial in self.selected_serials else ' ',
                Path(drive.get('dev', '?')).name,
                drive.get('location', 'unavailable'),
                f"{float(drive.get('size_bytes') or 0) / 1e12:.1f}T",
                serial,
                self._drive_status(drive),
                stage,
                f"{_bar(stage_progress, 10)} {stage_progress * 100:4.0f}%" if show_progress else '—',
                f"{_bar(overall, 10)} {overall * 100:4.0f}%" if show_progress else '—',
                format_duration(drive.get('stage_eta_seconds')) if active else '—',
                key=key,
            )
        if table.row_count:
            table.move_cursor(row=max(0, min(cursor, table.row_count - 1)))
