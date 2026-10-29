"""Comenzile Daly: repornire BMS, MOSFET încărcare/descărcare pornit/oprit, setare SOC.

Protocolul clasic Daly UART/485 (cadre A5 … de 13 octeți, același și prin Bluetooth), ca în biblioteca open-source
„dalybms”. NEVERIFICATE încă pe hardware (de testat cu utilizatorul, câte un pas odată):
  - repornire:          0x00, date 00 × 8 (unele firmware-uri repornesc fără să răspundă);
  - MOSFET descărcare:  0xD9, data[0] = 01 pornit / 00 oprit;
  - MOSFET încărcare:   0xDA, data[0] = 01 pornit / 00 oprit;
  - setare SOC:         0x21, [an−2000, lună, zi, oră, minut, secundă, SOC×10 pe 2 octeți big-endian].
Răspunsul (când vine) are același CMD, iar data[0] = 01 = acceptat. După fiecare comandă starea se citește înapoi
din BMS: rezultatul se decide după ce arată BMS-ul, nu după răspuns.

Siguranța: `precheck` (pe server) refuză oprirea unui MOSFET dacă celelalte module nu pot duce curentul de acum sau
dacă n-ar mai rămâne niciunul, și repornirea dacă alt BMS repornește deja sau dacă restul băncii nu e complet pornit.
Pornirea unui MOSFET e mereu permisă: BMS-ul își păstrează protecțiile.

Suportul se află din mers (fără listă de modele): o comandă la care BMS-ul nu răspunde și pe care nu o aplică de
2 ori la rând e marcată „nesuportată” pentru acel firmware + adresă (`Support`, în tabela meta).
"""
from __future__ import annotations

import json
import logging
import time

from . import daly_params
from .drivers.base import DriverError
from .drivers.daly import FRAME_LEN, build_request, decode_faults, parse_frames, split_faults, u16

log = logging.getLogger("amper.daly_actions")

A = dict
ACTIONS = [
    A(key="chg_on", label="Pornește încărcarea", cmd=0xDA, kind="mos", mos="chg", want=True, risk="low",
      warn="BMS-ul pornește MOSFET-ul de încărcare. Protecțiile lui rămân active: dacă o protecție e declanșată, "
           "încărcarea rămâne oprită."),
    A(key="chg_off", label="Oprește încărcarea", cmd=0xDA, kind="mos", mos="chg", want=False, risk="medium",
      warn="Modulul nu se mai încarcă până îl pornești la loc. Restul băncii preia încărcarea."),
    A(key="dsg_on", label="Pornește descărcarea", cmd=0xD9, kind="mos", mos="dsg", want=True, risk="low",
      warn="BMS-ul pornește MOSFET-ul de descărcare. Dacă modulul are altă tensiune decât banca, la pornire poate "
           "trece un curent mare de egalizare între module."),
    A(key="dsg_off", label="Oprește descărcarea", cmd=0xD9, kind="mos", mos="dsg", want=False, risk="high",
      warn="Modulul nu mai dă curent până îl pornești la loc. Consumul casei trece pe celelalte module."),
    A(key="restart", label="Repornește BMS-ul", cmd=0x00, kind="restart", risk="high",
      warn="BMS-ul se oprește câteva secunde și pornește din nou. Cât repornește, modulul poate să nu dea și să nu "
           "primească curent, iar Bluetooth-ul se reconectează."),
    A(key="set_soc", label="Setează SOC", cmd=0x21, kind="soc", risk="medium",
      warn="BMS-ul ia de la capăt numărarea energiei de la valoarea dată. O valoare greșită face ca BMS-ul să "
           "oprească încărcarea sau descărcarea prea devreme sau prea târziu."),
]
BY_KEY = {a["key"]: a for a in ACTIONS}
WHAT = {"chg": "încărcarea", "dsg": "descărcarea"}
MARGIN = 1.3                 # limitele celorlalte module trebuie să ducă curentul de acum + 30%
RESTART_WAIT = 60            # s — cât așteptăm să revină BMS-ul după repornire
FAILS_UNSUPPORTED = 2        # eșecuri la rând (fără răspuns și neaplicată) → „nesuportată”


def payload(key: str, value=None, now: float | None = None) -> bytes:
    a = BY_KEY[key]
    if a["kind"] == "mos":
        return bytes([1 if a["want"] else 0]) + bytes(7)
    if a["kind"] == "soc":
        soc = float(value)
        if not 0 <= soc <= 100:
            raise ValueError("SOC-ul trebuie între 0 și 100 %")
        t = time.localtime(now if now is not None else time.time())
        raw = int(round(soc * 10))
        return bytes([t.tm_year - 2000, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, raw >> 8, raw & 0xFF])
    return bytes(8)


def request(address: int, key: str, value=None, now: float | None = None) -> bytes:
    return build_request(address, BY_KEY[key]["cmd"], payload(key, value, now))


# ---------------- citiri ajutătoare ----------------
def firmware(drv) -> str:
    """Versiunea de firmware (0x62), ținută minte pe driver; „?” dacă BMS-ul nu răspunde la 0x62."""
    if getattr(drv, "_fw", None):
        return drv._fw
    try:
        data = b"".join(fr[1:] for fr in drv._query(0x62, 2))
        fw = "".join(chr(c) for c in data if 32 < c < 127) or data.hex()
    except DriverError:
        return "?"
    drv._fw = fw
    return fw


def support_key(drv) -> str:
    return f"{firmware(drv)}|0x{drv.address:02X}"


def mos_state(drv) -> dict:
    d = drv._query(0x93)[0]
    return {"chg": bool(d[1]), "dsg": bool(d[2])}


def reasons(drv) -> list[str]:
    """Protecțiile și avertismentele raportate acum de BMS (0x98), protecțiile întâi."""
    try:
        warn, prot = split_faults(decode_faults(drv._query(0x98)[0][:7]))
    except DriverError:
        return []
    return prot + warn


def identity(drv) -> dict:
    """Ce nu trebuie să se schimbe la o repornire: ciclurile (0x94) și capacitatea (0x50)."""
    cycles = u16(drv._query(0x94)[0], 5)
    cap = daly_params.raw_get(drv._query(0x50)[0], daly_params.BY_KEY["capacity"])
    return {"cycles": cycles, "capacity_ah": cap / 1000}


def limits(drv) -> dict:
    """Limitele de curent de nivel 1 (0x5B): {dis, chg} în A."""
    b = drv._query(0x5B)[0]
    f = daly_params.BY_KEY
    return {"dis": daly_params.raw_to_value(f["dis_a"], daly_params.raw_get(b, f["dis_a"])),
            "chg": daly_params.raw_to_value(f["chg_a"], daly_params.raw_get(b, f["chg_a"]))}


def _send(drv, key: str, value=None, now=None) -> int | None:
    """Trimite comanda; întoarce data[0] din răspuns (1 = acceptat) sau None dacă BMS-ul n-a răspuns."""
    a = BY_KEY[key]
    try:
        buf = drv._exchange(request(drv.address, key, value, now), FRAME_LEN)
    except DriverError:
        return None
    frames = parse_frames(buf, a["cmd"])
    return frames[0][0] if frames else None


# ---------------- execuția, cu verificare ----------------
def execute(drv, key: str, value=None, *, sleep=time.sleep, clock=time.time, now=None,
            restart_wait: float = RESTART_WAIT) -> dict:
    """Rulează o comandă și o verifică citind BMS-ul înapoi. Întoarce
    {ok, label, old, new, error, support_fail, critical, seconds}; `support_fail` = n-a aplicat comanda fără un motiv
    raportat de BMS (numără pentru „nesuportată”), `critical` = BMS-ul nu a revenit sau pare resetat după repornire."""
    a = BY_KEY[key]
    res = dict(ok=False, label=a["label"], old=None, new=None, error=None, support_fail=False, critical=False,
               seconds=None)
    if a["kind"] == "mos":
        before = mos_state(drv)
        res["old"] = "pornit" if before[a["mos"]] else "oprit"
        reply = _send(drv, key)
        sleep(1)
        now_state = mos_state(drv)[a["mos"]]
        res["new"] = "pornit" if now_state else "oprit"
        if now_state == a["want"]:
            res["ok"] = True
            return res
        if a["want"]:
            why = reasons(drv)
            if why:
                res["error"] = f"BMS-ul ține {WHAT[a['mos']]} oprită: {'; '.join(why[:3])}"
                return res
            res["error"] = f"BMS-ul nu a pornit {WHAT[a['mos']]}" + ("" if reply else " (nu a răspuns la comandă)")
        else:
            res["error"] = f"BMS-ul nu a oprit {WHAT[a['mos']]}" + ("" if reply else " (nu a răspuns la comandă)")
        res["support_fail"] = True                   # neaplicată fără motiv raportat de BMS
        return res

    if a["kind"] == "soc":
        target = float(value)
        payload(key, target)                         # validează 0–100 înainte de orice
        res["old"] = f"{u16(drv._query(0x90)[0], 6) / 10:.1f} %".replace(".", ",")
        reply = _send(drv, key, target, now)
        sleep(1)
        got = u16(drv._query(0x90)[0], 6) / 10
        res["new"] = f"{got:.1f} %".replace(".", ",")
        if abs(got - target) <= 0.5:
            res["ok"] = True
            return res
        res["error"] = (f"BMS-ul a refuzat SOC-ul (arată {res['new']})" if reply == 0
                        else f"BMS-ul arată {res['new']} în loc de {target:.1f} %".replace(".", ","))
        res["support_fail"] = True
        return res

    # repornire: unele firmware-uri nu răspund (repornesc imediat) — lipsa răspunsului e normală
    ident = identity(drv)
    res["old"] = f"{ident['cycles']} cicluri, {ident['capacity_ah']:g} Ah"
    t0 = clock()
    _send(drv, key)
    sleep(3)
    after = None
    while clock() - t0 <= restart_wait:
        try:
            after = identity(drv)
            break
        except DriverError:
            sleep(2)
    res["seconds"] = round(clock() - t0)
    if after is None:
        res.update(critical=True, error=f"BMS-ul nu a mai răspuns în {restart_wait:g} s după repornire")
        return res
    res["new"] = f"{after['cycles']} cicluri, {after['capacity_ah']:g} Ah"
    if after != ident:
        res.update(critical=True, error=f"După repornire BMS-ul arată altceva ({res['old']} → {res['new']}): "
                                        "poate a revenit la setările din fabrică — verificați setările")
        return res
    res["ok"] = True
    return res


# ---------------- preverificări de siguranță (pe server) ----------------
def _num(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}".replace(".", ",")


def precheck(key: str, dev_id: str, snapshot: dict, lims: dict[str, dict], restarting: set[str]) -> str | None:
    """Motivul pentru care comanda e refuzată acum, sau None. `lims` = {id: {dis, chg}} (A, nivel 1, 0x5B)."""
    a = BY_KEY[key]
    devs = snapshot["devices"]
    others = {d: v for d, v in devs.items() if d != dev_id and v["kind"] == "pack" and v.get("connected", True)
              and v.get("reading") and not v.get("stale")}
    if a["kind"] == "restart":
        if restarting - {dev_id}:
            return "Alt BMS repornește acum. Câte unul odată."
        off = [devs[d]["name"] for d, v in others.items()
               if v["reading"].get("charge_mos") is False or v["reading"].get("discharge_mos") is False]
        if off:
            return f"Celelalte module trebuie să aibă încărcarea și descărcarea pornite ({', '.join(off)} nu are)."
        return None
    if a["kind"] != "mos" or a["want"]:
        return None                                   # pornirea și SOC-ul sunt mereu permise
    field = "discharge_mos" if a["mos"] == "dsg" else "charge_mos"
    on = {d: v for d, v in others.items() if v["reading"].get(field) is not False}
    bank = snapshot.get("bank") or {}
    inv = next((v["reading"] for v in devs.values()
                if v["kind"] == "inverter" and v.get("reading") and not v.get("stale")), None) or {}
    volt = bank.get("voltage") or inv.get("battery_voltage") or 0
    if a["mos"] == "dsg":
        watts = inv.get("load_power") or 0
        amps = max(watts / volt if volt else 0, -(bank.get("current") or 0))
        if not on:
            return "Ar rămâne zero module cu descărcarea pornită: invertorul s-ar opri."
    else:
        bp = inv.get("battery_power")
        amps = max((bp / volt) if (bp and volt and bp > 0) else 0, bank.get("current") or 0)
        watts = amps * volt
        if not on and amps > 1:
            return "Banca se încarcă și ar rămâne zero module cu încărcarea pornită."
    need = amps * MARGIN
    if need < 1:
        return None
    lk = "dis" if a["mos"] == "dsg" else "chg"
    can = sum((lims.get(d) or {}).get(lk) or 0 for d in on)
    if can >= need:
        return None
    n = len(on)
    who = "un singur modul" if n == 1 else f"{n} module"
    unknown = [devs[d]["name"] for d in on if not (lims.get(d) or {}).get(lk)]
    extra = f" (limită necunoscută la {', '.join(unknown)})" if unknown else ""
    return (f"Ar rămâne {who} pentru {_num(watts / 1000)} kW: pot duce {_num(can, 0)} A, trebuie "
            f"{_num(need, 0)} A cu rezervă de 30%{extra}.")


# ---------------- SOC sugerat din tensiunea celulelor (LiFePO4 în repaus) ----------------
CURVE = [(2.50, 0), (2.90, 2), (3.00, 5), (3.10, 9), (3.20, 17), (3.25, 25), (3.27, 35), (3.29, 50), (3.30, 60),
         (3.31, 70), (3.32, 78), (3.33, 85), (3.34, 90), (3.36, 95), (3.40, 99), (3.45, 100)]


def soc_from_voltage(cells: list[float], current: float | None = None) -> dict | None:
    """SOC estimat din media celulelor, după curba LiFePO4 în repaus; `rest` = curent mic (estimare bună)."""
    if not cells:
        return None
    v = sum(cells) / len(cells)
    if v <= CURVE[0][0]:
        soc = 0.0
    elif v >= CURVE[-1][0]:
        soc = 100.0
    else:
        for (v0, s0), (v1, s1) in zip(CURVE, CURVE[1:]):
            if v0 <= v <= v1:
                soc = s0 + (s1 - s0) * (v - v0) / (v1 - v0)
                break
    return {"soc": round(soc), "cell_avg": round(v, 3), "rest": abs(current or 0) < 2}


# ---------------- „nesuportată” (ținută minte în tabela meta) ----------------
class Support:
    META = "daly_actions_support"

    def __init__(self, db):
        self.db = db

    def _load(self) -> dict:
        try:
            return json.loads(self.db.get_meta(self.META) or "{}")
        except ValueError:
            return {}

    def _save(self, data: dict) -> None:
        self.db.set_meta(self.META, json.dumps(data, ensure_ascii=False))

    def unsupported(self, skey: str) -> set[str]:
        return {k for k, v in self._load().get(skey, {}).items() if v.get("unsupported")}

    def record(self, skey: str, action: str, res: dict) -> bool:
        """Notează rezultatul; True dacă acum comanda a devenit „nesuportată”."""
        data = self._load()
        st = data.setdefault(skey, {}).setdefault(action, {"fails": 0, "unsupported": False})
        if res.get("ok"):
            st.update(fails=0, unsupported=False)
        elif res.get("support_fail"):
            st["fails"] += 1
            if st["fails"] >= FAILS_UNSUPPORTED:
                st["unsupported"] = True
        self._save(data)
        return st["unsupported"] and st["fails"] == FAILS_UNSUPPORTED

    def reset(self, skey: str, action: str | None = None) -> None:
        data = self._load()
        if action:
            data.get(skey, {}).pop(action, None)
        else:
            data.pop(skey, None)
        self._save(data)
