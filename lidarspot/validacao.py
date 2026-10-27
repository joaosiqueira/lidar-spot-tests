"""
Validação do LAS/LAZ antes da conversão, pelo header (laspy).

Mesmo critério do backend-python (`app/processing/pointcloud/validator.py`, caminho laspy, que é o que roda na
imagem de produção): os status fatais abortam a conversão do tile; sem CRS a conversão segue com aviso.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

FATAIS = {"CORRUPTED", "EMPTY", "INVALID_HEADER", "TRUNCATED", "MISSING_FILE"}


@dataclass
class Validacao:
    status: str = "VALID"
    pontos: int = 0
    bytes: int = 0
    versao_las: str = ""
    formato_ponto: int | None = None
    tem_crs: bool = False
    crs_wkt: str | None = None
    dimensoes: list[str] = field(default_factory=list)
    limites: dict | None = None
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def fatal(self) -> bool:
        return self.status in FATAIS

    def dict(self) -> dict:
        return asdict(self)


def valida(caminho: Path) -> Validacao:
    v = Validacao()
    if not caminho.exists():
        v.status = "MISSING_FILE"
        v.erros.append("arquivo não encontrado")
        return v
    v.bytes = caminho.stat().st_size
    if v.bytes == 0:
        v.status = "EMPTY"
        v.erros.append("arquivo vazio")
        return v
    try:
        import laspy

        with laspy.open(caminho) as leitor:
            h = leitor.header
            v.pontos = int(h.point_count)
            v.versao_las = str(h.version)
            v.formato_ponto = int(h.point_format.id)
            v.dimensoes = list(h.point_format.dimension_names)
            v.limites = {"minX": float(h.mins[0]), "minY": float(h.mins[1]), "minZ": float(h.mins[2]),
                         "maxX": float(h.maxs[0]), "maxY": float(h.maxs[1]), "maxZ": float(h.maxs[2])}
            crs = h.parse_crs()
            if crs:
                v.tem_crs, v.crs_wkt = True, crs.to_wkt()
        if v.pontos == 0:
            v.status = "EMPTY"
            v.erros.append("zero pontos")
        elif not v.tem_crs:
            v.status = "MISSING_CRS"
            v.avisos.append("sem CRS: a saída não fica georreferenciada")
    except Exception as exc:  # noqa: BLE001 — qualquer falha de leitura vira um status
        erro = str(exc).lower()
        if "header" in erro or "signature" in erro or "magic" in erro:
            v.status = "INVALID_HEADER"
        elif "truncat" in erro or "eof" in erro:
            v.status = "TRUNCATED"
        else:
            v.status = "CORRUPTED"
        v.erros.append(str(exc))
    return v
