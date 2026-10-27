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
    # actualizări din GitHub Releases (amper/update.py): stable = doar versiunile finale, beta = și cele de test
    "update": {"repo": "georgexy/amper", "channel": "stable", "check_days": 7},
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


def overlay_path(cfg: dict) -> str | None:
    """Setările făcute din aplicație (ghidul de configurare, parola): un fișier scris de serviciul amper,
    lângă baza de date, aplicat peste /etc/amper/config.yaml (care rămâne neatins)."""
    if cfg.get("settings_file"):
        return cfg["settings_file"]
    if not cfg.get("database") or cfg["database"] == ":memory:":
        return None
    return str(Path(cfg["database"]).parent / "settings.yaml")


def apply_overlay(cfg: dict, over: dict) -> dict:
    """Peste config: dicționarele se îmbină, listele (ex. `devices`) se înlocuiesc."""
    return _merge(cfg, over or {})


def read_overlay(cfg: dict) -> dict:
    p = overlay_path(cfg)
    if not p:
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def save_overlay(cfg: dict, updates: dict) -> dict:
    """Adaugă/schimbă chei în fișierul de setări din aplicație (scriere atomică). Întoarce conținutul nou."""
    p = overlay_path(cfg)
    if not p:
        raise ConfigError("setările din aplicație nu se pot salva (bază de date în memorie)")
    data = _merge(read_overlay(cfg), updates)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Setări făcute din aplicația Amper (ghidul de configurare). Se aplică peste /etc/amper/config.yaml.\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    import os
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return data


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user)
    cfg = apply_overlay(cfg, read_overlay(cfg))
    validate(cfg)
    return cfg


def password_set(cfg: dict) -> bool:
    w = cfg.get("web") or {}
    return bool(w.get("password") or w.get("password_hash"))


def setup_needed(cfg: dict) -> bool:
    """Ghidul de configurare apare la prima pornire: nicio parolă și ghidul neterminat.
    O instalare existentă, cu parolă în config, nu-l vede."""
    return not cfg.get("setup_done") and not password_set(cfg)


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
