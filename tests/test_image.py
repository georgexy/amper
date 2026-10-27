"""Imaginea SD: config-ul generic (fără dispozitive, topicul generat la prima pornire)."""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_image_config_has_no_devices(tmp_path):
    sh = open(os.path.join(ROOT, "deploy", "install.sh")).read()
    snippet = re.search(r"<<'PY'\n(.*?)\nPY\n", sh, re.S).group(1)       # cel rulat de install.sh --image
    cfg = tmp_path / "config.yaml"
    cfg.write_text(open(os.path.join(ROOT, "config.example.yaml")).read())
    subprocess.run([sys.executable, "-", str(cfg)], input=snippet, text=True, check=True)
    c = yaml.safe_load(cfg.read_text())
    assert c["devices"] == [] and "REPLACE-TOPIC" in cfg.read_text()
    assert c["alerts"] and c["notify"] and c["wifi_setup"]["password"] == "amper-setup"


def test_firstboot_unit_runs_before_services():
    unit = open(os.path.join(ROOT, "deploy", "amper-firstboot.service")).read()
    assert "ConditionPathExists=/etc/amper/.firstboot" in unit
    assert all(s in unit for s in ("amper.service", "amper-wifi.service", "amper-update.service"))
    assert os.access(os.path.join(ROOT, "deploy", "firstboot.sh"), os.X_OK)
