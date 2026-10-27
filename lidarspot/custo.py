"""
Horas ligadas e custo de tabela das VMs, a partir do histórico de operações do Compute Engine.

Mesmo método do `custo` do terrahidro (`scripts/cadeia_lidar.py`): uma VM conta como ligada de cada
insert/start até o guestTerminate/preempted/stop/delete seguinte. As horas incluem boot, preparo e o trabalho
perdido nas interrupções, que é o que se paga. O faturado de fato sai do faturamento, filtrando pelos rótulos.
"""

from __future__ import annotations

import datetime as dt
import re

# Preço de tabela em us-central1 (US$/h por vCPU e por GB de RAM), o mesmo usado na rodada do terrahidro
# em 10/2026 (`scripts/plano_vm_ana.py`). Vale para as famílias listadas; outras precisam entrar aqui.
PRECOS = {
    "n2d": {"spot": {"cpu": 0.012640, "ram": 0.001693}, "sob_demanda": {"cpu": 0.027502, "ram": 0.003686}},
}
DISCO_GB_MES = 0.10    # pd-balanced
HORAS_MES = 730
RAM_POR_VCPU = {"standard": 4, "highmem": 8, "highcpu": 1}

ABRE = {"insert", "start"}
FECHA = {"guestTerminate", "preempted", "stop", "delete", "suspend"}


def recursos(maquina: str) -> tuple[str, int, int]:
    """n2d-standard-8 -> ("n2d", 8, 32)."""
    m = re.fullmatch(r"([a-z0-9]+)-(standard|highmem|highcpu)-(\d+)", maquina)
    if not m:
        raise ValueError(f"tipo de máquina não reconhecido: {maquina}")
    familia, classe, vcpu = m.group(1), m.group(2), int(m.group(3))
    return familia, vcpu, vcpu * RAM_POR_VCPU[classe]


def preco_hora(maquina: str, provisionamento: str) -> float | None:
    familia, vcpu, ram = recursos(maquina)
    p = PRECOS.get(familia, {}).get(provisionamento)
    return vcpu * p["cpu"] + ram * p["ram"] if p else None


def horas_ligada(operacoes: list[dict], ate: dt.datetime | None = None) -> tuple[float, int, bool]:
    """(horas, interrupções, ainda ligada) de uma VM. `ate` fecha um intervalo aberto (VM ainda ligada)."""
    horas, aberto, interrupcoes = 0.0, None, 0
    for o in sorted(operacoes, key=lambda o: o["insertTime"]):
        t = dt.datetime.fromisoformat(o["insertTime"].replace("Z", "+00:00"))
        tipo = o["operationType"].rsplit(".", 1)[-1]
        interrupcoes += tipo == "preempted"
        if tipo in ABRE and aberto is None:
            aberto = t
        elif tipo in FECHA and aberto is not None:
            horas += (t - aberto).total_seconds() / 3600
            aberto = None
    if aberto is not None and ate is not None:
        horas += (ate - aberto).total_seconds() / 3600
    return horas, interrupcoes, aberto is not None


def custo_vm(horas: float, maquina: str, provisionamento: str, disco_gb: int) -> dict:
    p = preco_hora(maquina, provisionamento)
    return {"custo_vm_usd": round(horas * p, 4) if p is not None else None,
            "custo_disco_usd": round(horas * disco_gb * DISCO_GB_MES / HORAS_MES, 4)}
