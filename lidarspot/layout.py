"""
Onde cada coisa fica no destino (chaves relativas à raiz do destino).

    _infra/imagem_<hash>.tar.gz             imagem Docker com os conversores
    _infra/pacote_<hash>.tgz                código (este pacote), separado da imagem
    _infra/startup_<hash>.sh                script de inicialização das VMs
    <rodada>/_plano/plano.json              a rodada inteira (máquina, formatos, lotes)
    <rodada>/_plano/<lote>.json             o que cada VM lê no boot
    <rodada>/tiles/<entrega>/<nome>/<formato>/...          saída publicada
    <rodada>/tiles/<entrega>/<nome>/<formato>/_SUCESSO     medições da conversão (JSON)
    <rodada>/tiles/<entrega>/<nome>/<formato>/_FALHA_<hora>
    <rodada>/_logs/<entrega>/<nome>/<formato>_<hora>.log   saída do conversor
    <rodada>/_lotes/<lote>/_FIM                            lote concluído (com falhas de tile, se houver)
    <rodada>/_lotes/<lote>/_vm/                            log do boot e _FALHA_<hora> da VM
"""

from __future__ import annotations


def plano(rodada: str) -> str:
    return f"{rodada}/_plano/plano.json"


def plano_lote(rodada: str, lote: str) -> str:
    return f"{rodada}/_plano/{lote}.json"


def saida(rodada: str, tile_id: str, formato: str) -> str:
    return f"{rodada}/tiles/{tile_id}/{formato}/"


def log_conversor(rodada: str, tile_id: str, formato: str, quando: str) -> str:
    return f"{rodada}/_logs/{tile_id}/{formato}_{quando}.log"


def lote(rodada: str, nome: str) -> str:
    return f"{rodada}/_lotes/{nome}/"


def tile_id(uri: str) -> str:
    """gs://amazon-3d-lidar/ENTREGAS/ENTREGA_02/1_Nuvem_de_Pontos/NP_1438107.laz -> ENTREGA_02/NP_1438107.

    A entrega é a pasta logo abaixo de ENTREGAS/; fora desse padrão, a pasta do arquivo."""
    partes = uri.rstrip("/").split("/")
    nome = partes[-1].rsplit(".", 1)[0]
    entrega = partes[partes.index("ENTREGAS") + 1] if "ENTREGAS" in partes[:-2] else partes[-2]
    return f"{entrega}/{nome}"
