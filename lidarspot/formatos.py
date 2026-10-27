"""
Conversões de LAS/LAZ testadas, com os mesmos comandos do backend-python
(`app/worker/tasks/pointcloud_copc.py`, `pointcloud_3dtiles.py` e `app/processing/pointcloud/potree.py`).

Cada formato sabe montar o comando e conferir a saída. A conferência lança `SaidaInvalida` quando o conversor
terminou com código 0 mas não gerou o que devia.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

COPC_ARQUIVO = "pointcloud.copc.laz"
CONVERSORES_COPC = ("untwine", "pdal")

# Cache igual ao do backend: a versão publicada nunca muda, só o ponto de entrada é revalidado.
CACHE_IMUTAVEL = "public,max-age=31536000,immutable,no-transform"
CACHE_ENTRADA = "no-cache"
ENTRADAS = {"tileset.json", "metadata.json", "cloud.js"}


class SaidaInvalida(RuntimeError):
    pass


@dataclass
class Saida:
    raiz: Path            # pasta enviada ao bucket
    entrada: str          # caminho relativo do ponto de entrada (tileset.json, metadata.json, pointcloud.copc.laz)
    info: dict


def comando(formato: str, origem: Path, saida: Path, tmp: Path, opcoes: dict) -> list[str]:
    if formato == "copc":
        conversor = opcoes.get("conversor_copc", "untwine")
        if conversor not in CONVERSORES_COPC:
            raise ValueError(f"conversor COPC desconhecido: {conversor} (use {CONVERSORES_COPC})")
        destino = saida / COPC_ARQUIVO
        if conversor == "untwine":
            return ["untwine", "-i", str(origem), "-o", str(destino), "--temp_dir", str(tmp)]
        return ["pdal", "translate", str(origem), str(destino), "-w", "writers.copc"]
    if formato == "3dtiles":
        # py3dtiles-serial: o py3dtiles com o LAZ lido sem threads (lidarspot/py3dtiles_serial.py)
        cmd = ["py3dtiles-serial", "convert", str(origem), "--out", str(saida), "--overwrite", "--pyproj-always-xy",
               "--jobs", str(opcoes.get("jobs") or os.cpu_count() or 2), "--verbose"]
        return cmd + [str(a) for a in opcoes.get("py3dtiles_args", [])]
    if formato == "potree":
        return ["PotreeConverter", str(origem), "-o", str(saida)] + [str(a) for a in opcoes.get("potree_args", [])]
    raise ValueError(f"formato desconhecido: {formato}")


def executavel(formato: str, opcoes: dict) -> str:
    return comando(formato, Path("x"), Path("y"), Path("z"), opcoes)[0]


def disponivel(formato: str, opcoes: dict) -> bool:
    return shutil.which(executavel(formato, opcoes)) is not None


def confere(formato: str, saida: Path) -> Saida:
    if formato == "copc":
        return _confere_copc(saida)
    if formato == "3dtiles":
        return _confere_3dtiles(saida)
    if formato == "potree":
        return _confere_potree(saida)
    raise ValueError(f"formato desconhecido: {formato}")


def _confere_copc(saida: Path) -> Saida:
    arq = saida / COPC_ARQUIVO
    if not arq.exists() or arq.stat().st_size == 0:
        raise SaidaInvalida(f"{COPC_ARQUIVO} não foi gerado")
    info: dict = {"bytes": arq.stat().st_size}
    if shutil.which("pdal"):
        r = subprocess.run(["pdal", "info", "--summary", str(arq)], capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            d = json.loads(r.stdout)
            resumo = d.get("summary", {})
            info.update(eh_copc=d.get("reader") == "readers.copc", pontos=resumo.get("num_points"),
                        limites=resumo.get("bounds"))
            if not info["eh_copc"]:
                raise SaidaInvalida(f"a saída não é COPC (leitor {d.get('reader')})")
    return Saida(saida, COPC_ARQUIVO, info)


def _confere_3dtiles(saida: Path) -> Saida:
    direto = saida / "tileset.json"
    achados = [direto] if direto.exists() else sorted(saida.rglob("tileset.json"))
    if not achados:
        raise SaidaInvalida("py3dtiles não gerou tileset.json")
    tileset = achados[0]
    json.loads(tileset.read_text())  # tileset ilegível é saída inválida
    raiz = tileset.parent             # o backend publica a partir da pasta do tileset
    return Saida(raiz, "tileset.json", {"tiles": sum(1 for p in raiz.rglob("*.pnts"))})


def _confere_potree(saida: Path) -> Saida:
    # PotreeConverter 2.x grava metadata.json + octree.bin + hierarchy.bin; o 1.x, cloud.js + data/.
    # O backend só conferia o 1.x, e a imagem dele não tem o PotreeConverter.
    if (saida / "metadata.json").exists():
        faltam = [n for n in ("octree.bin", "hierarchy.bin") if not (saida / n).exists()]
        if faltam:
            raise SaidaInvalida(f"Potree 2.x incompleto, faltam {faltam}")
        meta = json.loads((saida / "metadata.json").read_text())
        return Saida(saida, "metadata.json", {"versao": meta.get("version"), "pontos": meta.get("points")})
    if (saida / "cloud.js").exists() and (saida / "data").is_dir():
        return Saida(saida, "cloud.js", {"versao": "1.x"})
    raise SaidaInvalida("PotreeConverter não gerou metadata.json nem cloud.js")


def content_type(p: Path) -> str:
    sufixo = p.suffix.lower()
    if sufixo == ".json":
        return "application/json"
    if sufixo == ".glb":
        return "model/gltf-binary"
    return "application/octet-stream"


def cache_control(p: Path) -> str:
    return CACHE_ENTRADA if p.name in ENTRADAS else CACHE_IMUTAVEL
