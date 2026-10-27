"""Clasa de bază pentru drivere + acces la portul serial."""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


class DriverError(Exception):
    """Eroare de comunicație cu dispozitivul (timeout, checksum, răspuns invalid)."""


class Driver:
    kind = "pack"            # "pack" sau "inverter"
    default_baudrate = 9600

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)

    def read(self):
        """Citește dispozitivul. Blocant — rulează într-un thread separat."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class _Bus:
    """Un port serial fizic. Mai multe dispozitive pot sta pe același port (ex. mai multe
    pachete Seplos pe același RS485, cu adrese diferite) — își împart portul, pe rând."""

    def __init__(self, port: str):
        self.port = port
        self.ser = None
        self.lock = threading.Lock()
        self.last_end = 0.0      # time.monotonic() la sfârșitul ultimei tranzacții


_BUSES: dict[str, _Bus] = {}
_BUSES_LOCK = threading.Lock()


def get_bus(port: str) -> _Bus:
    with _BUSES_LOCK:
        if port not in _BUSES:
            _BUSES[port] = _Bus(port)
        return _BUSES[port]


class SerialDriver(Driver):
    """Driver care vorbește pe un port serial (USB-UART, USB-RS485, USB-RS232)."""

    timeout = 0.5
    bus_gap = 0.0            # pauză minimă pe port între două tranzacții (s)

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.port = cfg["port"]
        self.baudrate = int(cfg.get("baudrate", self.default_baudrate))
        self._bus = get_bus(self.port)

    # compatibilitate: unele teste injectează direct un port fals în `_ser`
    @property
    def _ser(self):
        return self._bus.ser

    @_ser.setter
    def _ser(self, value):
        self._bus.ser = value

    def _open(self):
        bus = self._bus
        if bus.ser is None:
            import serial  # importat aici ca simulatorul să meargă și fără pyserial
            bus.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout,
                                    bytesize=8, parity="N", stopbits=1)
            time.sleep(0.05)
        else:
            for attr, val in (("baudrate", self.baudrate), ("timeout", self.timeout)):
                if hasattr(bus.ser, attr) and getattr(bus.ser, attr) != val:
                    setattr(bus.ser, attr, val)
        return bus.ser

    def close(self) -> None:
        if self._bus.ser is not None:
            try:
                self._bus.ser.close()
            except Exception:
                pass
            self._bus.ser = None

    def transact(self, request: bytes, reader) -> bytes:
        """Trimite o cerere și citește răspunsul cu funcția `reader(ser)`.
        La orice eroare închide portul, ca să fie redeschis la următoarea citire."""
        with self._bus.lock:
            wait = self.bus_gap - (time.monotonic() - self._bus.last_end)
            if wait > 0:
                time.sleep(wait)
            try:
                ser = self._open()
                ser.reset_input_buffer()
                ser.write(request)
                ser.flush()
                time.sleep(0.02)
                return reader(ser)
            except DriverError:
                raise
            except Exception as e:  # port dispărut, cablu scos etc.
                self.close()
                raise DriverError(f"port {self.port}: {e}") from e
            finally:
                self._bus.last_end = time.monotonic()
