# keep_awake.py
"""Temporary Linux idle/sleep inhibition while the DiskQual UI is running."""

import re
import shutil
import subprocess


class KeepAwake:
    """Hold best-effort desktop and system inhibitors for the UI lifetime.

    The implementation deliberately does not change persistent power settings.
    Each inhibitor is released when the UI exits. Unsupported mechanisms are
    silently skipped so DiskQual remains portable across Linux desktops.
    """

    def __init__(self):
        self._systemd_process = None
        self._screensaver_cookie = None
        self._power_cookie = None

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
        if not shutil.which('systemd-inhibit'):
            return
        try:
            self._systemd_process = subprocess.Popen(
                [
                    'systemd-inhibit',
                    '--what=sleep:idle',
                    '--who=Sirgon DiskQual',
                    '--why=Drive qualification UI is active',
                    '--mode=block',
                    'sleep', 'infinity',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._systemd_process = None

    def _start_desktop_inhibitors(self):
        try:
            output = self._gdbus_call(
                'org.freedesktop.ScreenSaver',
                '/org/freedesktop/ScreenSaver',
                'org.freedesktop.ScreenSaver.Inhibit',
                'Sirgon DiskQual',
                'Drive qualification UI is active',
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
                'Drive qualification UI is active',
            )
            self._power_cookie = self._cookie(output)
        except (OSError, subprocess.SubprocessError):
            self._power_cookie = None

    def start(self):
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
