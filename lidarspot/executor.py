"""
Executor de um lote: roda na VM (dentro do contêiner) ou localmente.

Para cada tile do lote e cada formato pedido: baixa o LAZ, valida, converte, confere a saída, envia ao destino
e grava `_SUCESSO` com as medições. Tudo retomável depois de uma interrupção do spot:

- no bucket, um formato com `_SUCESSO` (ou `_FALHA_*`) não roda de novo;
- no disco, cada passo só conta como feito com o marcador `.ok` gravado depois da saída. Uma interrupção no meio
  de um passo deixa a saída pela metade com o nome final (lição do terrahidro, 21/10/2026), então o passo sem
  `.ok` apaga o que sobrou e roda do zero;
- o envio é idempotente: um objeto que já está no destino com o mesmo conteúdo conta como enviado.

Falha de conversão de um tile é dado do teste: fica em `_FALHA_<hora>` e o lote segue. Só erro de
infraestrutura (bucket, disco cheio, bug) derruba o lote; aí o startup grava `_vm/_FALHA_<hora>` e desliga.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

from lidarspot import bucket, formatos, layout
from lidarspot.recursos import Vigia, livre_gib, maquina
from lidarspot.validacao import FATAIS, valida

log = logging.getLogger("lidarspot")


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def passo(marcador: Path, alvos: list[Path], fn):
    """Roda `fn` se o passo não terminou. Terminado = marcador `.ok` gravado depois da saída."""
    if marcador.exists():
        return False
    for a in alvos:
        if a.is_dir():
            shutil.rmtree(a)
        elif a.exists():
            a.unlink()
    fn()
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.touch()
    return True


def roda_comando(cmd: list[str], arq_log: Path, timeout_s: int) -> dict:
    """Roda o conversor gravando a saída em `arq_log` e guardando as últimas linhas."""
    t0 = time.time()
    cauda: deque[str] = deque(maxlen=60)
    with open(arq_log, "w") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for linha in proc.stdout:
            f.write(linha)
            cauda.append(linha.rstrip())
            if time.time() - t0 > timeout_s:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, timeout_s)
        rc = proc.wait()
    return {"rc": rc, "dur_s": round(time.time() - t0, 1), "cauda": list(cauda)}


def envia_pasta(dst, raiz: Path, prefixo: str) -> dict:
    n, total, ja = 0, 0, 0
    for p in sorted(raiz.rglob("*")):
        if not p.is_file():
            continue
        r = dst.envia(p, prefixo + p.relative_to(raiz).as_posix(),
                      content_type=formatos.content_type(p), cache_control=formatos.cache_control(p))
        n, total, ja = n + 1, total + r.bytes, ja + r.ja_existia
    return {"objetos": n, "bytes": total, "ja_existiam": ja}


def tamanho_pasta(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def processa_formato(dst, rodada: str, tile: dict, formato: str, opcoes: dict, pasta: Path, laz: Path,
                     validacao: dict, info_maquina: dict) -> str:
    pref = layout.saida(rodada, tile["id"], formato)
    if dst.existe(pref + "_SUCESSO"):
        log.info("%s %s: já concluído no destino", tile["id"], formato)
        return "ok"
    if dst.lista(pref + "_FALHA_"):
        log.info("%s %s: falhou antes, não repete", tile["id"], formato)
        return "falha"

    base = pasta / formato
    saida, tmp = base / "saida", base / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    medidas: dict = {}

    def converte():
        saida.mkdir(parents=True)
        tmp.mkdir(parents=True)
        cmd = formatos.comando(formato, laz, saida, tmp, opcoes)
        vigia = Vigia(pasta)
        vigia.start()
        try:
            r = roda_comando(cmd, base / "conversor.log", int(opcoes.get("timeout_s", 6 * 3600)))
        finally:
            medidas.update(vigia.para())
        medidas.update(comando=cmd, conversao_s=r["dur_s"], rc=r["rc"])
        (base / "medidas.json").write_text(json.dumps(medidas))
        if r["rc"] != 0:
            raise formatos.SaidaInvalida(f"{cmd[0]} saiu com código {r['rc']}: " + " | ".join(r["cauda"][-8:]))

    t0 = time.time()
    try:
        if not formatos.disponivel(formato, opcoes):
            raise formatos.SaidaInvalida(f"{formatos.executavel(formato, opcoes)} não está no PATH")
        log.info("%s %s: convertendo", tile["id"], formato)
        passo(base / "converte.ok", [saida, tmp, base / "medidas.json"], converte)
        if not medidas:
            medidas.update(json.loads((base / "medidas.json").read_text()))
        conf = formatos.confere(formato, saida)
    except (formatos.SaidaInvalida, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            medidas["erro"] = f"timeout de {exc.timeout} s"
        falha = {"tile": tile, "formato": formato, "erro": medidas.get("erro") or str(exc), "medidas": medidas,
                 "validacao": validacao, "maquina": info_maquina, "quando": agora()}
        dst.grava_texto(pref + f"_FALHA_{agora()}", json.dumps(falha, indent=1, ensure_ascii=False))
        _envia_log(dst, rodada, tile, formato, base)
        log.warning("%s %s: FALHA %s", tile["id"], formato, falha["erro"])
        return "falha"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    t_envio = time.time()
    envio = envia_pasta(dst, conf.raiz, pref)
    medidas["envio_s"] = round(time.time() - t_envio, 1)
    _envia_log(dst, rodada, tile, formato, base)
    sucesso = {"tile": tile, "formato": formato, "opcoes": opcoes, "entrada": conf.entrada,
               "url_entrada": dst.url(pref + conf.entrada), "conferencia": conf.info, "medidas": medidas,
               "envio": envio, "saida_bytes": tamanho_pasta(conf.raiz), "pontos": validacao.get("pontos"),
               "laz_bytes": validacao.get("bytes"), "maquina": info_maquina, "quando": agora(),
               "total_s": round(time.time() - t0, 1)}
    dst.grava_texto(pref + "_SUCESSO", json.dumps(sucesso, indent=1, ensure_ascii=False))
    shutil.rmtree(saida, ignore_errors=True)  # libera o disco para o próximo formato
    log.info("%s %s: ok (%.0f s de conversão, %d objetos)", tile["id"], formato, medidas.get("conversao_s", 0),
             envio["objetos"])
    return "ok"


def _envia_log(dst, rodada: str, tile: dict, formato: str, base: Path):
    arq = base / "conversor.log"
    if arq.exists():
        dst.envia(arq, layout.log_conversor(rodada, tile["id"], formato, agora()), content_type="text/plain")


def processa_tile(dst, rodada: str, tile: dict, plano_lote: dict, work: Path, info_maquina: dict) -> dict:
    formatos_pedidos = plano_lote["formatos"]
    pendentes = [f for f in formatos_pedidos if not dst.existe(layout.saida(rodada, tile["id"], f) + "_SUCESSO")]
    if not pendentes:
        return {f: "ok" for f in formatos_pedidos}
    pasta = work / tile["id"].replace("/", "__")
    laz = pasta / "entrada" / Path(tile["uri"]).name
    t0 = time.time()
    if passo(pasta / "download.ok", [laz], lambda: bucket.baixa(tile["uri"], laz)):
        log.info("%s: baixado %.0f MiB em %.0f s", tile["id"], laz.stat().st_size / 2**20, time.time() - t0)
    download_s = round(time.time() - t0, 1)

    arq_val = pasta / "validacao.json"
    passo(pasta / "validacao.ok", [arq_val], lambda: arq_val.write_text(json.dumps(valida(laz).dict())))
    v = json.loads(arq_val.read_text())
    v["download_s"] = download_s
    estado = {}
    if v["status"] in FATAIS:
        for f in pendentes:
            dst.grava_texto(layout.saida(rodada, tile["id"], f) + f"_FALHA_{agora()}",
                            json.dumps({"tile": tile, "formato": f, "erro": f"VALIDACAO: {v['status']}",
                                        "validacao": v, "quando": agora()}, indent=1, ensure_ascii=False))
            estado[f] = "falha"
    else:
        for f in formatos_pedidos:
            estado[f] = processa_formato(dst, rodada, tile, f, plano_lote["opcoes"], pasta, laz, v, info_maquina)
    shutil.rmtree(pasta, ignore_errors=True)
    return estado


def roda_lote(destino: str, rodada: str, lote: str, work: Path) -> int:
    dst = bucket.abre(destino)
    if dst.existe(layout.lote(rodada, lote) + "_FIM"):
        log.info("lote %s já concluído no destino", lote)
        return 0
    texto = dst.le_texto(layout.plano_lote(rodada, lote))
    if texto is None:
        raise RuntimeError(f"plano do lote não encontrado: {dst.url(layout.plano_lote(rodada, lote))}")
    plano_lote = json.loads(texto)
    work = work / rodada / lote
    work.mkdir(parents=True, exist_ok=True)
    info = maquina()
    ini, disco_ini = agora(), livre_gib(work)
    t0 = time.time()
    estados = {}
    for i, tile in enumerate(plano_lote["tiles"], 1):
        log.info("lote %s: tile %d/%d %s", lote, i, len(plano_lote["tiles"]), tile["id"])
        estados[tile["id"]] = processa_tile(dst, rodada, tile, plano_lote, work, info)
    ok = sum(e == "ok" for est in estados.values() for e in est.values())
    falhas = [f"{t} {f}" for t, est in estados.items() for f, e in est.items() if e != "ok"]
    fim = {"rodada": rodada, "lote": lote, "inicio_utc": ini, "fim_utc": agora(),
           "horas_neste_boot": round((time.time() - t0) / 3600, 3), "conversoes_ok": ok, "falhas": falhas,
           "disco_livre_inicio_gib": round(disco_ini, 1), "maquina": info}
    dst.grava_texto(layout.lote(rodada, lote) + "_FIM", json.dumps(fim, indent=1, ensure_ascii=False))
    log.info("lote %s concluído: %d conversões ok, %d falhas", lote, ok, len(falhas))
    shutil.rmtree(work, ignore_errors=True)
    return 0
