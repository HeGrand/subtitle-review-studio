"""Compile the Windows GUI launcher with the system .NET Framework compiler."""
import os
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def build_launcher(target):
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    icon = APP / 'desktop/studio.ico'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    '/codepage:65001', '/reference:System.Windows.Forms.dll', f'/out:{target}', f'/win32icon:{icon}',
                    str(APP / 'scripts/StudioLauncher.cs')], check=True)


if __name__ == '__main__':
    build_launcher(APP / '字幕工作室.exe')
