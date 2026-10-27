"""
Acesso às fontes e ao destino: gs:// pelo google-cloud-storage, ou uma pasta local (testes sem GCP).

Regra herdada do terrahidro: nada no destino é apagado, movido ou sobrescrito. Todo envio pede
if_generation_match=0. Se o objeto já existe e é idêntico (mesmo tamanho e crc32c), conta como enviado: é a
retomada depois de uma interrupção do spot no meio do upload. Se for diferente, é erro.
"""

from __future__ import annotations

import base64
import filecmp
import shutil
from dataclasses import dataclass
from pathlib import Path


class ConflitoNoDestino(RuntimeError):
    """O objeto já existe no destino com conteúdo diferente: nunca sobrescreve."""


@dataclass
class Enviado:
    chave: str
    bytes: int
    ja_existia: bool


def separa_gs(uri: str) -> tuple[str, str]:
    """gs://bucket/a/b -> ("bucket", "a/b")."""
    if not uri.startswith("gs://"):
        raise ValueError(f"não é gs://: {uri}")
    bucket, _, caminho = uri[5:].partition("/")
    return bucket, caminho


def _cliente():
    from google.cloud import storage  # type: ignore

    return storage.Client()


def _crc32c_local(path: Path) -> str:
    import google_crc32c  # type: ignore

    c = google_crc32c.Checksum()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(8 << 20), b""):
            c.update(bloco)
    return base64.b64encode(c.digest()).decode()


class DestinoGcs:
    def __init__(self, uri: str):
        self.uri = uri.rstrip("/") + "/"
        nome, self.prefixo = separa_gs(self.uri)
        self._bucket = _cliente().bucket(nome)

    def url(self, chave: str) -> str:
        return self.uri + chave

    def existe(self, chave: str) -> bool:
        return self._bucket.blob(self.prefixo + chave).exists()

    def le_texto(self, chave: str) -> str | None:
        blob = self._bucket.blob(self.prefixo + chave)
        return blob.download_as_text() if blob.exists() else None

    def lista(self, prefixo: str) -> list[str]:
        base = self.prefixo + prefixo
        return [b.name[len(self.prefixo):] for b in self._bucket.list_blobs(prefix=base)]

    def envia(self, local: Path, chave: str, content_type: str | None = None,
              cache_control: str | None = None) -> Enviado:
        from google.api_core.exceptions import PreconditionFailed  # type: ignore

        blob = self._bucket.blob(self.prefixo + chave)
        if cache_control:
            blob.cache_control = cache_control
        tamanho = local.stat().st_size
        try:
            blob.upload_from_filename(str(local), content_type=content_type, if_generation_match=0)
            return Enviado(chave, tamanho, False)
        except PreconditionFailed:
            blob.reload()
            if blob.size == tamanho and blob.crc32c == _crc32c_local(local):
                return Enviado(chave, tamanho, True)
            raise ConflitoNoDestino(f"{self.url(chave)} já existe com outro conteúdo") from None

    def grava_texto(self, chave: str, texto: str, content_type: str = "application/json") -> Enviado:
        from google.api_core.exceptions import PreconditionFailed  # type: ignore

        blob = self._bucket.blob(self.prefixo + chave)
        try:
            blob.upload_from_string(texto, content_type=content_type, if_generation_match=0)
            return Enviado(chave, len(texto.encode()), False)
        except PreconditionFailed:
            if blob.download_as_text() == texto:
                return Enviado(chave, len(texto.encode()), True)
            raise ConflitoNoDestino(f"{self.url(chave)} já existe com outro conteúdo") from None


class DestinoLocal:
    """Uma pasta no lugar do bucket, com as mesmas regras (para o teste T0, sem gravar no GCP)."""

    def __init__(self, raiz: str):
        self.raiz = Path(raiz).expanduser().resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.uri = str(self.raiz) + "/"

    def url(self, chave: str) -> str:
        return str(self.raiz / chave)

    def existe(self, chave: str) -> bool:
        return (self.raiz / chave).exists()

    def le_texto(self, chave: str) -> str | None:
        p = self.raiz / chave
        return p.read_text() if p.exists() else None

    def lista(self, prefixo: str) -> list[str]:
        return sorted(str(p.relative_to(self.raiz)) for p in self.raiz.rglob("*")
                      if p.is_file() and str(p.relative_to(self.raiz)).startswith(prefixo))

    def envia(self, local: Path, chave: str, content_type: str | None = None,
              cache_control: str | None = None) -> Enviado:
        alvo = self.raiz / chave
        if alvo.exists():
            if filecmp.cmp(local, alvo, shallow=False):
                return Enviado(chave, alvo.stat().st_size, True)
            raise ConflitoNoDestino(f"{alvo} já existe com outro conteúdo")
        alvo.parent.mkdir(parents=True, exist_ok=True)
        tmp = alvo.with_name(alvo.name + ".envio")
        shutil.copyfile(local, tmp)
        tmp.rename(alvo)
        return Enviado(chave, alvo.stat().st_size, False)

    def grava_texto(self, chave: str, texto: str, content_type: str = "application/json") -> Enviado:
        alvo = self.raiz / chave
        if alvo.exists():
            if alvo.read_text() == texto:
                return Enviado(chave, len(texto.encode()), True)
            raise ConflitoNoDestino(f"{alvo} já existe com outro conteúdo")
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(texto)
        return Enviado(chave, len(texto.encode()), False)


def abre(uri: str) -> DestinoGcs | DestinoLocal:
    return DestinoGcs(uri) if uri.startswith("gs://") else DestinoLocal(uri)


def baixa(origem: str, local: Path) -> int:
    """Baixa um LAZ (gs:// ou caminho local) para `local`. Grava em .part e renomeia no fim, então um download
    interrompido nunca fica com o nome final."""
    local.parent.mkdir(parents=True, exist_ok=True)
    parcial = local.with_name(local.name + ".part")
    parcial.unlink(missing_ok=True)
    if origem.startswith("gs://"):
        nome, caminho = separa_gs(origem)
        _cliente().bucket(nome).blob(caminho).download_to_filename(str(parcial))
    else:
        shutil.copyfile(origem, parcial)
    parcial.rename(local)
    return local.stat().st_size
