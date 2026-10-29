"""Lista dispozitivelor pe care le poți alege în ghid („Aleg eu din listă”): marca, modelul, driverul Amper și cum se
leagă. La alegerea din listă Amper încearcă doar protocolul acelui model (cu vitezele și adresele lui), deci în câteva
secunde, în loc să încerce tot. `supported=False`: apare în listă, dar driverul nu e gata încă („în curând”).

Easun se vinde în două familii cu protocoale diferite: ISolar/SMH = Voltronic/Axpert (PI30), SMG/SMG-II = SRNE
(Modbus) — de aceea apar separat; utilizatorul alege modelul, nu protocolul.
"""
from __future__ import annotations

E = dict
CATALOG = [
    # ---------------- invertoare ----------------
    E(id="growatt_spf", kind="inverter", brand="Growatt", model="SPF 3000–6000 ES / TL (off-grid)",
      driver="growatt_spf", links=["cable"], supported=True,
      hint="Cablul USB direct în portul USB al invertorului (sau un adaptor USB–RS232 în portul RS232)."),
    E(id="deye", kind="inverter", brand="Deye", model="SUN-…-SG hibrid (și Sunsynk, Sol-Ark)",
      driver="deye", links=["cable"], supported=False,
      hint="Adaptor USB–RS485 în portul „Modbus” / „RS485” al invertorului."),
    E(id="voltronic", kind="inverter", brand="Easun", model="ISolar / SMH (Voltronic, Axpert, MPP Solar)",
      driver="voltronic", links=["cable"], supported=False,
      hint="Cablul USB sau RS232 al invertorului."),
    E(id="srne", kind="inverter", brand="Easun", model="SMG / SMG-II (SRNE, PowMr, Anenji)",
      driver="srne", links=["cable"], supported=False,
      hint="Adaptor USB–RS485 în portul RS485 al invertorului."),
    # ---------------- acumulatori / BMS ----------------
    E(id="daly", kind="bms", brand="Daly", model="Smart BMS (protocolul clasic)", driver="daly",
      links=["ble", "cable"], supported=True,
      hint="Prin Bluetooth (nume „DL-…”) sau cu cablul USB–UART al BMS-ului. Seriile noi K/M/S (Modbus) nu merg încă."),
    E(id="jk", kind="bms", brand="JK", model="JK BMS / Jikong (seriile JK02)", driver="jk",
      links=["ble", "cable"], supported=True,
      hint="Prin Bluetooth (nume „JK-…”) sau cu modulul RS485/UART al BMS-ului."),
    E(id="seplos", kind="bms", brand="Seplos", model="V2 (RS485)", driver="seplos", links=["cable"],
      supported=True, address=True,
      hint="Adaptor USB–RS485 în portul RS485 al primului pachet; pachetele legate între ele au adrese diferite (DIP)."),
    E(id="pace", kind="bms", brand="Ecobat / PACE", model="PowerCube P15A și alte BMS PACE (RS485)", driver="pace",
      links=["cable"], supported=True, address=True,
      hint="Adaptor USB–RS485 în portul RS485; adresa din comutatoarele DIP."),
    E(id="jbd", kind="bms", brand="JBD", model="JBD / Xiaoxiang", driver="jbd", links=["ble"], supported=False,
      hint=""),
    E(id="ant", kind="bms", brand="ANT", model="ANT BMS", driver="ant", links=["ble"], supported=False, hint=""),
]
BY_ID = {e["id"]: e for e in CATALOG}
LINK_TEXT = {"cable": "Cablu (USB)", "ble": "Bluetooth"}
