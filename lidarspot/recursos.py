"""
Medição de recursos durante um passo: pico de RAM usada, menor espaço livre em disco e CPU médio.

Lê /proc da máquina inteira, como o `Vigia` do terrahidro (`scripts/cadeia_lidar.py`). Na VM do teste só roda
a conversão, então o número é o da conversão; num computador pessoal ele inclui o resto do sistema.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

GIB = 2**30


def _ram_usada_gib() -> float:
    info = dict(linha.split(":", 1) for linha in open("/proc/meminfo"))
    return (int(info["MemTotal"].split()[0]) - int(info["MemAvailable"].split()[0])) / 2**20


def _cpu() -> tuple[int, int]:
    campos = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    ocioso = campos[3] + campos[4]
    return sum(campos) - ocioso, sum(campos)


def livre_gib(pasta: Path) -> float:
    return shutil.disk_usage(pasta).free / GIB


class Vigia(threading.Thread):
    def __init__(self, pasta: Path, intervalo: float = 2.0):
        super().__init__(daemon=True)
        self.pasta, self.intervalo = pasta, intervalo
        self.ram_pico_gib, self.disco_min_gib = 0.0, livre_gib(pasta)
        self._fim = threading.Event()
        self._cpu0 = _cpu() if os.path.exists("/proc/stat") else None
        self.cpu_medio_pct: float | None = None

    def run(self):
        if not os.path.exists("/proc/meminfo"):
            return  # fora do Linux (macOS) não mede RAM nem CPU
        while not self._fim.is_set():
            self.ram_pico_gib = max(self.ram_pico_gib, _ram_usada_gib())
            self.disco_min_gib = min(self.disco_min_gib, livre_gib(self.pasta))
            self._fim.wait(self.intervalo)

    def para(self) -> dict:
        self._fim.set()
        self.join()
        if self._cpu0:
            ocupado1, total1 = _cpu()
            dt = total1 - self._cpu0[1]
            self.cpu_medio_pct = round(100 * (ocupado1 - self._cpu0[0]) / dt, 1) if dt else None
        return {"ram_pico_gib": round(self.ram_pico_gib, 2), "disco_livre_min_gib": round(self.disco_min_gib, 2),
                "cpu_medio_pct": self.cpu_medio_pct}


def metadado(chave: str) -> str:
    """Metadado da VM no GCE (vazio fora do GCE)."""
    try:
        r = subprocess.run(["curl", "-s", "-m", "2", "-H", "Metadata-Flavor: Google",
                            f"http://metadata.google.internal/computeMetadata/v1/instance/{chave}"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def maquina() -> dict:
    ram_total = ""
    if os.path.exists("/proc/meminfo"):
        ram_total = next((x.split(":")[1].strip() for x in open("/proc/meminfo") if x.startswith("MemTotal")), "")
    modelo = ""
    if shutil.which("lscpu"):
        saida = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        modelo = next((x.split(":", 1)[1].strip() for x in saida.splitlines() if x.startswith("Model name")), "")
    return {"host": socket.gethostname(), "vcpus": os.cpu_count(), "ram_total": ram_total, "cpu_modelo": modelo,
            "tipo_maquina": metadado("machine-type").rsplit("/", 1)[-1],
            "zona": metadado("zone").rsplit("/", 1)[-1],
            "provisionamento": metadado("attributes/provisionamento")}
