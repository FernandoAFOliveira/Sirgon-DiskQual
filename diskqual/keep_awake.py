# keep_awake.py
"""Temporary Linux display and system-sleep inhibition for DiskQual."""

import re
import shutil
import subprocess
import time


class KeepAwake:
    """Hold best-effort temporary power-management inhibitors.

    ``display=True`` requests desktop screensaver/display inhibition. This is
    appropriate for the interactive UI and is released when the UI exits.

    ``system=True`` requests a logind sleep inhibitor. This is intended for
    active qualification workers so closing the UI cannot allow the host to
    suspend while a drive test is still running.

    No persistent power settings are changed. Unsupported mechanisms are
    skipped so DiskQual remains portable across Linux desktops and servers.
    """

    def __init__(self, *, display=True, system=True, reason='Drive qualification is active'):
        self.display = bool(display)
        self.system = bool(system)
        self.reason = str(reason)
        self._systemd_process = None
        self._screensaver_cookie = None
        self._power_cookie = None

    @classmethod
    def display_only(cls):
        return cls(display=True, system=False, reason='DiskQual operator display is active')

    @classmethod
    def testing_only(cls):
        return cls(display=False, system=True, reason='DiskQual drive testing is active')

    @staticmethod
    def _gdbus_call(destination, object_path, method, *args):
        if not shutil.which('gdbus'):
            return ''
        process = subprocess.run(
            [
                'gdbus', 'call', '--session',
                '--dest', destination,
                '--object-path', object_path,
                '--method', method,
                *args,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return process.stdout.strip() if process.returncode == 0 else ''

    @staticmethod
    def _cookie(output):
        match = re.search(r'uint32\s+(\d+)', output)
        if not match:
            match = re.search(r'\((\d+),?\)', output)
        return int(match.group(1)) if match else None

    def _start_systemd_inhibitor(self):
        if not self.system or not shutil.which('systemd-inhibit'):
            return
        try:
            process = subprocess.Popen(
                [
                    'systemd-inhibit',
                    '--what=sleep',
                    '--who=Sirgon DiskQual',
                    f'--why={self.reason}',
                    '--mode=block',
                    'tail', '-f', '/dev/null',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give systemd-inhibit enough time to contact logind/polkit. An
            # unprivileged or otherwise unauthorized session exits quickly with
            # "Failed to inhibit: Access denied"; do not report that as active.
            time.sleep(0.20)
            if process.poll() is None:
                self._systemd_process = process
            else:
                self._systemd_process = None
        except OSError:
            self._systemd_process = None

    def _start_desktop_inhibitors(self):
        if not self.display:
            return
        try:
            output = self._gdbus_call(
                'org.freedesktop.ScreenSaver',
                '/org/freedesktop/ScreenSaver',
                'org.freedesktop.ScreenSaver.Inhibit',
                'Sirgon DiskQual',
                self.reason,
            )
            self._screensaver_cookie = self._cookie(output)
        except (OSError, subprocess.SubprocessError):
            self._screensaver_cookie = None

        try:
            output = self._gdbus_call(
                'org.freedesktop.PowerManagement',
                '/org/freedesktop/PowerManagement/Inhibit',
                'org.freedesktop.PowerManagement.Inhibit.Inhibit',
                'Sirgon DiskQual',
                self.reason,
            )
            self._power_cookie = self._cookie(output)
        except (OSError, subprocess.SubprocessError):
            self._power_cookie = None

    @property
    def system_active(self):
        return self._systemd_process is not None and self._systemd_process.poll() is None

    def start(self):
        self._systemd_process = None
        self._start_systemd_inhibitor()
        self._start_desktop_inhibitors()
        return self

    def stop(self):
        if self._screensaver_cookie is not None:
            try:
                self._gdbus_call(
                    'org.freedesktop.ScreenSaver',
                    '/org/freedesktop/ScreenSaver',
                    'org.freedesktop.ScreenSaver.UnInhibit',
                    str(self._screensaver_cookie),
                )
            except (OSError, subprocess.SubprocessError):
                pass
            self._screensaver_cookie = None

        if self._power_cookie is not None:
            try:
                self._gdbus_call(
                    'org.freedesktop.PowerManagement',
                    '/org/freedesktop/PowerManagement/Inhibit',
                    'org.freedesktop.PowerManagement.Inhibit.UnInhibit',
                    str(self._power_cookie),
                )
            except (OSError, subprocess.SubprocessError):
                pass
            self._power_cookie = None

        if self._systemd_process is not None:
            try:
                self._systemd_process.terminate()
                self._systemd_process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._systemd_process.kill()
                except OSError:
                    pass
            self._systemd_process = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False
