"""
Plano de uma rodada de teste: quais LAZ entram, como se dividem em lotes (uma VM por lote) e com que máquina.

Funções puras, sem GCP: a listagem dos LAZ chega pronta (lista de (uri, bytes)).
"""

from __future__ import annotations

import datetime as dt
import math
import random

from lidarspot import layout

FORMATOS = ("copc", "3dtiles", "potree")
# Disco por GiB de LAZ do maior tile do lote. O untwine chega a ~25x o LAZ em temporários (medido no
# backend-python); somam-se o LAZ e as saídas. É uma das coisas que o teste mede (disco_livre_min_gib).
FATOR_DISCO_UNTWINE = 30
FATOR_DISCO = 8
DISCO_SISTEMA_GB = 20   # Debian, Docker e a imagem carregada
DISCO_MIN_GB = 50


def amostra(arquivos: list[tuple[str, int]], n: int, semente: int = 1) -> list[tuple[str, int]]:
    """`n` arquivos espalhados pela distribuição de tamanho (do menor ao maior), para o teste cobrir os
    extremos e não só a mediana. Dentro de cada faixa a escolha é aleatória, com semente fixa."""
    if n >= len(arquivos):
        return sorted(arquivos)
    ordem = sorted(arquivos, key=lambda a: (a[1], a[0]))
    rnd = random.Random(semente)
    escolhidos = []
    for i in range(n):
        ini, fim = len(ordem) * i // n, len(ordem) * (i + 1) // n
        escolhidos.append(ordem[rnd.randrange(ini, fim)])
    return escolhidos


def divide(arquivos: list[tuple[str, int]], n_lotes: int) -> list[list[tuple[str, int]]]:
    """Divide em `n_lotes` com bytes parecidos (maior primeiro, sempre no lote mais leve): o tempo de cada VM
    acompanha o volume de LAZ, e a rodada só termina quando o último lote termina."""
    n_lotes = max(1, min(n_lotes, len(arquivos)))
    lotes: list[list[tuple[str, int]]] = [[] for _ in range(n_lotes)]
    pesos = [0] * n_lotes
    for a in sorted(arquivos, key=lambda a: (-a[1], a[0])):
        i = pesos.index(min(pesos))
        lotes[i].append(a)
        pesos[i] += a[1]
    return lotes


def disco_gb(maior_bytes: int, formatos: list[str], opcoes: dict) -> int:
    fator = FATOR_DISCO_UNTWINE if "copc" in formatos and opcoes.get("conversor_copc", "untwine") == "untwine" \
        else FATOR_DISCO
    return max(DISCO_MIN_GB, math.ceil(maior_bytes / 2**30 * fator + DISCO_SISTEMA_GB))


def monta(rodada: str, destino: str, arquivos: list[tuple[str, int]], *, n_lotes: int, maquina: str,
          formatos: list[str], opcoes: dict, provisionamento: str, projeto: str, zonas: list[str],
          disco: int | None = None, inventario: dict | None = None) -> dict:
    invalidos = [f for f in formatos if f not in FORMATOS]
    if invalidos:
        raise ValueError(f"formatos desconhecidos: {invalidos} (use {FORMATOS})")
    if provisionamento not in ("spot", "sob_demanda"):
        raise ValueError("provisionamento: spot ou sob_demanda")
    lotes = {}
    for i, grupo in enumerate(divide(arquivos, n_lotes), 1):
        nome = f"lote{i:04d}"
        lotes[nome] = {"rodada": rodada, "lote": nome, "formatos": formatos, "opcoes": opcoes,
                       "bytes": sum(b for _, b in grupo),
                       "tiles": [{"id": layout.tile_id(u), "uri": u, "bytes": b} for u, b in grupo]}
    maior = max((b for _, b in arquivos), default=0)
    return {"rodada": rodada, "destino": destino, "criado": dt.datetime.now().isoformat(timespec="seconds"),
            "projeto": projeto, "zonas": zonas, "maquina": maquina, "provisionamento": provisionamento,
            "disco_gb": disco or disco_gb(maior, formatos, opcoes), "formatos": formatos, "opcoes": opcoes,
            "tiles": len(arquivos), "bytes": sum(b for _, b in arquivos), "maior_tile_bytes": maior,
            "inventario": inventario or {}, "lotes": lotes}
