"""
Estimativa de horas e custo dos testes T1–T4 (docs/TESTES.md), a partir das taxas medidas numa rodada já feita
(em geral o T0, no Docker local).

Funções puras, sem GCP: o inventário chega pronto (lista de (uri, bytes)). Cada teste é sorteado com a mesma
amostra e divisão em lotes que o `plano` usaria, então os GiB de cada um são os reais.

Hipóteses (a calibrar no T1):
- a conversão escala com o número de vCPUs (s/GiB medido × vCPUs medidos / vCPUs da VM);
- download do bucket a DOWNLOAD_S_POR_GIB, porque o download do T0 passa pela internet de casa e não representa
  a VM em us-central1;
- cada VM gasta PREPARO_MIN em boot e preparo (no terrahidro foram 2–3 min);
- cada interrupção custa um preparo novo e, em média, metade da conversão de um formato do maior tile;
- o conversor COPC do PDAL, sem medida, usa a taxa do untwine.
"""

from __future__ import annotations

from lidarspot import custo as cst
from lidarspot import plano as pl

DOWNLOAD_S_POR_GIB = 20
PREPARO_MIN = 3
FOLGA = 1.5
MIN_MIB = 1.0   # o padrão do `plano`

# Os testes de docs/TESTES.md, com os mesmos parâmetros do `plano` de cada um. A máquina do T4 sai do T3; até lá,
# a padrão.
TESTES = [
    {"rodada": "t1", "entregas": ["ENTREGA_02"], "amostra": 3, "lotes": 1},
    {"rodada": "t2", "entregas": ["ENTREGA_10"], "amostra": 4, "lotes": 1, "interrupcoes": 3},
    {"rodada": "t3-n2d-standard-4", "amostra": 6, "semente": 3, "lotes": 1, "maquina": "n2d-standard-4"},
    {"rodada": "t3-n2d-standard-8", "amostra": 6, "semente": 3, "lotes": 1, "maquina": "n2d-standard-8"},
    {"rodada": "t3-n2d-highmem-8", "amostra": 6, "semente": 3, "lotes": 1, "maquina": "n2d-highmem-8"},
    {"rodada": "t3-pdal", "amostra": 6, "semente": 3, "lotes": 1, "formatos": ["copc"], "conversor_copc": "pdal"},
    {"rodada": "t4", "amostra": 100, "lotes": 20},
]


def taxas(sucessos: list[dict]) -> dict:
    """Por formato: s/GiB de LAZ na conversão e no envio, e os vCPUs da máquina onde foi medido."""
    por_fmt: dict[str, dict] = {}
    for s in sucessos:
        t = por_fmt.setdefault(s["formato"], {"gib": 0.0, "conversao_s": 0.0, "envio_s": 0.0, "vcpus": 0})
        t["gib"] += s["laz_bytes"] / 2**30
        t["conversao_s"] += s["medidas"].get("conversao_s") or 0
        t["envio_s"] += s["medidas"].get("envio_s") or 0
        t["vcpus"] = max(t["vcpus"], s["maquina"].get("vcpus") or 0)
    return {f: {"conversao_s_gib": t["conversao_s"] / t["gib"], "envio_s_gib": t["envio_s"] / t["gib"],
                "vcpus": t["vcpus"]} for f, t in por_fmt.items() if t["gib"]}


def _da_entrega(uri: str, entregas: list[str] | None) -> bool:
    return entregas is None or any(f"/{e}/" in uri for e in entregas)


def estima(teste: dict, inventario: list[tuple[str, int]], tx: dict) -> dict:
    """Horas, relógio e custo de tabela de um teste. Formatos sem taxa medida ficam de fora (em `sem_medida`)."""
    maquina = teste.get("maquina", "n2d-standard-8")
    formatos = teste.get("formatos", list(pl.FORMATOS))
    opcoes = {"conversor_copc": teste.get("conversor_copc", "untwine")}
    validos = [x for x in inventario if _da_entrega(x[0], teste.get("entregas")) and x[1] >= MIN_MIB * 2**20]
    escolhidos = pl.amostra(validos, teste["amostra"], teste.get("semente", 1))
    _, vcpus, _ = cst.recursos(maquina)
    medidos = [f for f in formatos if f in tx]

    def s_por_gib(f: str) -> float:
        t = tx[f]
        return t["conversao_s_gib"] * (t["vcpus"] or vcpus) / vcpus + t["envio_s_gib"]

    horas_lotes = []
    for grupo in pl.divide(escolhidos, teste["lotes"]):
        gib = sum(b for _, b in grupo) / 2**30
        seg = PREPARO_MIN * 60 + gib * (DOWNLOAD_S_POR_GIB + sum(s_por_gib(f) for f in medidos))
        horas_lotes.append(seg / 3600)
    maior = max((b for _, b in escolhidos), default=0)
    n_int = teste.get("interrupcoes", 0)
    if n_int and medidos:
        conv_maior = max(s_por_gib(f) for f in medidos) * maior / 2**30
        horas_lotes[0] += n_int * (PREPARO_MIN * 60 + conv_maior / 2) / 3600
    horas = sum(horas_lotes)
    disco = pl.disco_gb(maior, formatos, opcoes)
    c = cst.custo_vm(horas, maquina, "spot", disco)
    usd = c["custo_vm_usd"] + c["custo_disco_usd"] if c["custo_vm_usd"] is not None else None
    return {"rodada": teste["rodada"], "maquina": maquina, "tiles": len(escolhidos),
            "gib": sum(b for _, b in escolhidos) / 2**30, "vms": len(horas_lotes), "horas": horas,
            "relogio_h": max(horas_lotes, default=0), "custo_usd": usd,
            "sem_medida": [f for f in formatos if f not in tx]}


def tabela(estimativas: list[dict], tx: dict) -> list[str]:
    """Seção do relatorio.md."""
    out = ["", "## Estimativa dos testes T1–T4", "",
           "Preço de tabela spot em us-central1, com as taxas medidas nesta rodada. Hipóteses em "
           "`lidarspot/estimativa.py`: a conversão escala com os vCPUs, download a "
           f"{DOWNLOAD_S_POR_GIB} s/GiB, {PREPARO_MIN} min de preparo por VM; o T2 conta 3 interrupções; o "
           "COPC do PDAL usa a taxa do untwine. A coluna com folga multiplica por "
           f"{FOLGA}.", "",
           "| Rodada | Máquina | Tiles | LAZ (GiB) | VMs | VM·h | Relógio (h) | US$ | US$ com folga |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    total = 0.0
    for e in estimativas:
        usd = e["custo_usd"]
        total += usd or 0
        falta = f" (sem {', '.join(e['sem_medida'])})" if e["sem_medida"] else ""
        out.append(f"| {e['rodada']}{falta} | {e['maquina']} | {e['tiles']} | {e['gib']:.1f} | {e['vms']} | "
                   f"{e['horas']:.2f} | {e['relogio_h']:.2f} | "
                   + (f"{usd:.2f} | {usd * FOLGA:.2f} |" if usd is not None else "sem preço | |"))
    out += [f"| **Total** | | | | | | | **{total:.2f}** | **{total * FOLGA:.2f}** |", "",
            "Taxas usadas (s/GiB de LAZ): " + "; ".join(
                f"{f} {t['conversao_s_gib']:.0f} de conversão em {t['vcpus']} vCPUs + {t['envio_s_gib']:.0f} de envio"
                for f, t in tx.items()) + "."]
    return out
