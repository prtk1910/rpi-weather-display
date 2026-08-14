import re
import subprocess
from pathlib import Path


ROOT=Path(__file__).parents[1]

def test_installer_shell_syntax(): subprocess.run(["bash","-n",ROOT/"scripts/install.sh"],check=True)

def test_xrandr_primary_mode_parser():
    line="Unknown19-1 connected primary 480x320+0+0 (normal left inverted right x axis y axis)"
    program='/ connected / {for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+x[0-9]+\\+/) {print $i; exit}}'
    result=subprocess.run(["awk",program],input=line,text=True,capture_output=True,check=True)
    assert result.stdout.strip()=="480x320+0+0"

def test_systemd_template_substitutes_all_placeholders():
    text=(ROOT/"systemd/weather-display.service.in").read_text()
    for key,value in {"@SERVICE_USER@":"pi","@USER_HOME@":"/home/pi","@INSTALL_DIR@":"/opt/weather-display"}.items(): text=text.replace(key,value)
    assert not re.search(r"@[A-Z_]+@",text)
    assert "User=pi" in text and "ExecStart=/opt/weather-display/.venv/bin/weather-display" in text

def test_real_pin_is_not_in_tracked_source():
    allowed={ROOT/"tests/test_repository.py"}
    for path in ROOT.rglob("*"):
        if path.is_file() and path not in allowed and not {".git", ".venv", "__pycache__"}.intersection(path.parts):
            try: content=path.read_text()
            except UnicodeDecodeError: continue
            assert "".join(("19","10")) not in content, path
