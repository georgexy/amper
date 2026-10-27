"""Încărcarea și validarea configurației."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "poll_interval": 2,
    "store_interval": 10,
    "database": "/var/lib/amper/amper.db",
    "raw_retention_days": 14,
    "web": {"host": "0.0.0.0", "port": 8080, "password": ""},
    # hotspot pentru configurarea WiFi de pe telefon, pornit doar când nu e internet (amper/wifi_setup.py)
    "wifi_setup": {"enabled": True, "ssid": "Amper-Setup", "password": "", "offline_minutes": 2},
    "devices": [],
    "alerts": {
        "not_charging_minutes": 10,
        "bank_charging_amps": 5,
        "pack_idle_amps": 0.5,
        "full_soc": 97,
        "soc_divergence_percent": 15,
        "soc_divergence_minutes": 30,
        "low_soc_warning": 25,
        "low_soc_critical": 12,
        "cell_high_volts": 3.60,
        "cell_low_volts": 2.90,
        "cell_delta_volts": 0.15,
        "temp_high_c": 50,
        "temp_low_charge_c": 1,
        "comm_lost_seconds": 120,
    },
    "notify": {
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": ""},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
    },
}

KNOWN_TYPES = {"daly", "seplos", "growatt_spf", "sim_daly", "sim_seplos", "sim_growatt"}


class ConfigError(Exception):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    ids = set()
    for i, d in enumerate(cfg["devices"]):
        for key in ("id", "type"):
            if key not in d:
                raise ConfigError(f"dispozitivul #{i + 1} nu are '{key}'")
        if d["type"] not in KNOWN_TYPES:
            raise ConfigError(f"tip necunoscut '{d['type']}' la '{d['id']}' (cunoscute: {sorted(KNOWN_TYPES)})")
        if d["id"] in ids:
            raise ConfigError(f"id duplicat: {d['id']}")
        ids.add(d["id"])
        if d["type"] == "daly" and not d.get("port") and not d.get("ble"):
            d["port"] = "auto"           # Daly: se caută singur, pe cablu USB sau Bluetooth
        if not d["type"].startswith("sim_") and not d.get("port") and not d.get("ble"):
            raise ConfigError(f"dispozitivul '{d['id']}' nu are 'port'")
        d.setdefault("name", d["id"])
