"""Informații despre Pi pentru Setări → Info: sistem, alimentare, rețea, porturi și dispozitive conectate.

Toate citirile sunt fără drepturi speciale (rulează ca utilizatorul amper); ce lipsește apare ca None.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time

THROTTLE_BITS = {0: "Subtensiune acum", 1: "Frecvență limitată acum", 2: "Procesor încetinit acum",
                 3: "Limită de temperatură acum", 16: "A avut subtensiune de la pornire",
                 17: "A avut frecvența limitată", 18: "A fost încetinit", 19: "A atins limita de temperatură"}


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _run(*cmd, timeout: float = 5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_times() -> tuple[int, int]:
    f = [int(x) for x in (_read("/proc/stat") or "cpu 0 0 0 0").split("\n")[0].split()[1:]]
    idle = f[3] + (f[4] if len(f) > 4 else 0)
    return sum(f), idle


def cpu_percent(interval: float = 0.4) -> float | None:
    t1, i1 = _cpu_times()
    time.sleep(interval)
    t2, i2 = _cpu_times()
    return round(100 * (1 - (i2 - i1) / (t2 - t1)), 1) if t2 > t1 else None


def memory() -> dict:
    mi = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        mi[k] = int(v.split()[0]) if v.split() else 0
    total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
    return dict(total_mb=round(total / 1024), used_mb=round((total - avail) / 1024),
                percent=round(100 * (total - avail) / total, 1) if total else None)


def throttled() -> dict:
    out = _run("vcgencmd", "get_throttled")          # „throttled=0x50000”
    if "=" not in out:
        return dict(value=None, flags=[])
    v = int(out.split("=")[1], 16)
    return dict(value=hex(v), flags=[t for b, t in THROTTLE_BITS.items() if v & (1 << b)])


def split_terse(line: str) -> list[str]:
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def network() -> dict:
    devs = [split_terse(x) for x in _run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines()]
    ifaces = {}
    for line in _run("ip", "-4", "-br", "addr").splitlines():
        p = line.split()
        if len(p) >= 3:
            ifaces[p[0]] = p[2].split("/")[0]
    wifi = None
    for f in (split_terse(x) for x in _run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,CHAN,FREQ,SECURITY",
                                            "device", "wifi").splitlines()):
        if len(f) >= 6 and f[0] == "yes":
            wifi = dict(ssid=f[1], signal=int(f[2] or 0), channel=f[3], freq=f[4], security=f[5])
            break
    ts = {}
    try:
        d = json.loads(_run("tailscale", "status", "--json"))
        ts = dict(state=d.get("BackendState"), ip=(d.get("Self") or {}).get("TailscaleIPs", [None])[0],
                  name=(d.get("Self") or {}).get("DNSName", "").rstrip("."),
                  # fără nodurile Tailscale pentru accesul public (funnel-ingress-node): nu sunt aparatele tale
                  peers=[dict(name=p.get("HostName"), online=p.get("Online"), os=p.get("OS"))
                         for p in (d.get("Peer") or {}).values()
                         if not str(p.get("HostName") or "").startswith("funnel-ingress") and not p.get("ShareeNode")])
    except (ValueError, TypeError):
        pass
    return dict(devices=[dict(device=d[0], type=d[1], state=d[2], connection=d[3]) for d in devs
                         if len(d) >= 4 and d[1] in ("wifi", "ethernet")],
                ip=ifaces, wifi=wifi, tailscale=ts)


def ports(engine) -> list[dict]:
    """Porturile seriale USB și ce dispozitiv Amper folosește fiecare."""
    used: dict[str, list[str]] = {}
    for k, st in engine.devices.items():
        port = getattr(getattr(st.driver, "inner", None) or st.driver, "port", None)
        if port:
            used.setdefault(os.path.realpath(port) if os.path.exists(port) else port, []).append(
                st.cfg.get("name", k))
    out = []
    for p in sorted(glob.glob("/dev/serial/by-id/*")):
        real = os.path.realpath(p)
        out.append(dict(port=os.path.basename(p), dev=os.path.basename(real), used_by=used.get(real, [])))
    return out


def bluetooth(engine) -> list[dict]:
    """Legăturile Bluetooth ale modulelor (Daly)."""
    out = []
    for k, st in engine.devices.items():
        link = getattr(getattr(st.driver, "inner", None) or st.driver, "link", None)
        if link is not None:
            out.append(dict(device=st.cfg.get("name", k), target=link.target,
                            connected=bool(link.client and link.client.is_connected),
                            ok=st.last_ok is not None and time.time() - st.last_ok < 30))
    return out


def gather(engine) -> dict:
    du = shutil.disk_usage("/")
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    up = float((_read("/proc/uptime") or "0").split()[0])
    return dict(
        model=(_read("/proc/device-tree/model") or "").rstrip("\x00"),
        hostname=_run("hostname"),
        uptime_s=round(up), load=(_read("/proc/loadavg") or "").split()[:3],
        cpu_percent=cpu_percent(), cpu_temp=round(int(temp) / 1000, 1) if temp else None,
        memory=memory(), disk=dict(total_gb=round(du.total / 2**30, 1), used_gb=round(du.used / 2**30, 1),
                                   percent=round(100 * du.used / du.total, 1)),
        power=throttled(), network=network(), ports=ports(engine), bluetooth=bluetooth(engine),
        time=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
