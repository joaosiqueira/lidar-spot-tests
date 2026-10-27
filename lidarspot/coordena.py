"""
Lado do operador (o computador de quem roda o teste): monta a rodada, sobe a infraestrutura, lança e acompanha
as VMs, simula interrupções e calcula custo e relatório.

Só precisa de Python 3.10+ e do gcloud autenticado (e do Docker para a imagem); não usa bibliotecas do GCP.
O estado fica todo no destino (bucket), então o coordenador pode cair e ser retomado sem perder nada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from lidarspot import custo as cst
from lidarspot import estimativa as est
from lidarspot import layout
from lidarspot import plano as pl

RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / ".estado"   # fora do git: infra atual, operações, relatórios
STARTUP = RAIZ / "spot" / "startup.sh"
IMAGEM_TAG = "lidar-spot:latest"
ROTULOS = {"projeto": "amazonia3d", "etapa": "lidar-spot-teste"}


def log(msg: str):
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def sh(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"falhou: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


# ------------------------------------------------------------------------------------------- destino (gs ou pasta)
def _url(destino: str, chave: str) -> str:
    return destino.rstrip("/") + "/" + chave


def publica(destino: str, chave: str, local: Path):
    """Envia sem sobrescrever (o --no-clobber pula um objeto que já existe)."""
    if destino.startswith("gs://"):
        sh(["gcloud", "storage", "cp", "--no-clobber", str(local), _url(destino, chave)])
    else:
        alvo = Path(_url(destino, chave))
        alvo.parent.mkdir(parents=True, exist_ok=True)
        if not alvo.exists():
            shutil.copyfile(local, alvo)


def existe(destino: str, chave: str) -> bool:
    if destino.startswith("gs://"):
        return sh(["gcloud", "storage", "ls", _url(destino, chave)], check=False).returncode == 0
    return Path(_url(destino, chave)).exists()


def lista(destino: str, padrao: str) -> list[str]:
    """Chaves (relativas ao destino) que casam com o curinga `padrao`."""
    if destino.startswith("gs://"):
        r = sh(["gcloud", "storage", "ls", _url(destino, padrao)], check=False)
        base = destino.rstrip("/") + "/"
        return [u[len(base):] for u in r.stdout.split() if u.startswith(base)]
    raiz = Path(destino)   # o glob do pathlib entende `**` (qualquer profundidade) como o gcloud
    return sorted(str(p.relative_to(raiz)) for p in raiz.glob(padrao) if p.is_file())


def le_jsons(destino: str, padrao: str) -> list[dict]:
    """Todos os JSONs que casam com o curinga, numa chamada só (o `cat` concatena)."""
    if destino.startswith("gs://"):
        texto = sh(["gcloud", "storage", "cat", _url(destino, padrao)], check=False).stdout
    else:
        texto = "".join(Path(destino, k).read_text() for k in lista(destino, padrao))
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(texto):
        while i < len(texto) and texto[i].isspace():
            i += 1
        if i >= len(texto):
            break
        obj, i = dec.raw_decode(texto, i)
        out.append(obj)
    return out


def le_plano(destino: str, rodada: str) -> dict:
    if destino.startswith("gs://"):
        return json.loads(sh(["gcloud", "storage", "cat", _url(destino, layout.plano(rodada))]).stdout)
    return json.loads(Path(_url(destino, layout.plano(rodada))).read_text())


# --------------------------------------------------------------------------------------------------- inventário
def lista_laz(padroes: list[str]) -> list[tuple[str, int]]:
    """(uri, bytes) de cada LAZ/LAS que casa com os curingas gs:// (ou caminhos locais)."""
    out = []
    for p in padroes:
        if not p.startswith("gs://"):
            for f in sorted(Path().glob(p) if any(c in p for c in "*?[") else [Path(p)]):
                out.append((str(f.resolve()), f.stat().st_size))
            continue
        r = sh(["gcloud", "storage", "ls", "-l", p])
        for linha in r.stdout.splitlines():
            partes = linha.split()
            if len(partes) == 3 and partes[2].startswith("gs://") and partes[2].lower().endswith((".laz", ".las")):
                out.append((partes[2], int(partes[0])))
    return sorted(set(out))


def cmd_plano(a):
    if a.lista:
        padroes = [x.strip() for x in Path(a.lista).read_text().splitlines() if x.strip() and not x.startswith("#")]
    else:
        entregas = a.entregas.split(",") if a.entregas else ["*"]
        padroes = [f"{a.origem.rstrip('/')}/{e}/1_Nuvem_de_Pontos/*.laz" for e in entregas]
    todos = lista_laz(padroes)
    if not todos:
        sys.exit("nenhum LAZ encontrado")
    validos = [x for x in todos if x[1] >= a.min_mib * 2**20]
    escolhidos = pl.amostra(validos, a.amostra, a.semente) if a.amostra else validos
    n_lotes = a.lotes or -(-len(escolhidos) // a.tiles_por_lote)
    opcoes = {"conversor_copc": a.conversor_copc, "timeout_s": a.timeout_h * 3600}
    if a.jobs:
        opcoes["jobs"] = a.jobs
    if a.py3dtiles_args:
        opcoes["py3dtiles_args"] = a.py3dtiles_args.split()
    plano = pl.monta(a.rodada, a.destino, escolhidos, n_lotes=n_lotes, maquina=a.maquina,
                     formatos=a.formatos.split(","), opcoes=opcoes,
                     provisionamento="sob_demanda" if a.sob_demanda else "spot", projeto=a.projeto,
                     zonas=a.zonas.split(","), disco=a.disco_gb,
                     inventario={"arquivos": len(todos), "bytes": sum(b for _, b in todos), "padroes": padroes})
    if existe(a.destino, layout.plano(a.rodada)):
        sys.exit(f"a rodada {a.rodada} já tem plano no destino; use outro nome (nada é sobrescrito)")
    d = ESTADO / a.rodada
    d.mkdir(parents=True, exist_ok=True)
    (d / "plano.json").write_text(json.dumps(plano, indent=1, ensure_ascii=False))
    for nome, lote in plano["lotes"].items():
        (d / f"{nome}.json").write_text(json.dumps(lote, indent=1, ensure_ascii=False))
        publica(a.destino, layout.plano_lote(a.rodada, nome), d / f"{nome}.json")
    publica(a.destino, layout.plano(a.rodada), d / "plano.json")
    gib = plano["bytes"] / 2**30
    log(f"rodada {a.rodada}: {plano['tiles']} tiles ({gib:.1f} GiB) de {len(todos)} no inventário, "
        f"{len(plano['lotes'])} lotes, {plano['maquina']} {plano['provisionamento']}, disco {plano['disco_gb']} GB, "
        f"formatos {','.join(plano['formatos'])}")
    log(f"plano em {_url(a.destino, layout.plano(a.rodada))}")


# ------------------------------------------------------------------------------------------------ infraestrutura
def _infra_local(destino: str) -> Path:
    return ESTADO / ("infra_" + hashlib.sha1(destino.encode()).hexdigest()[:8] + ".json")


def le_infra(destino: str) -> dict:
    f = _infra_local(destino)
    return json.loads(f.read_text()) if f.exists() else {}


def grava_infra(destino: str, **kw):
    ESTADO.mkdir(exist_ok=True)
    d = {**le_infra(destino), **kw, "destino": destino}
    _infra_local(destino).write_text(json.dumps(d, indent=1))


def cmd_pacote(a):
    """Código das VMs (lidarspot/) com o hash no nome: nunca sobrescreve uma versão anterior. O startup vai junto,
    só como registro: a VM recebe o startup pelo metadado no momento em que é criada."""
    arquivos = sorted((RAIZ / "lidarspot").glob("*.py"))
    h = hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in arquivos)).hexdigest()[:10]
    ESTADO.mkdir(exist_ok=True)
    tgz = ESTADO / f"pacote_{h}.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        for p in arquivos:
            t.add(p, arcname=f"lidarspot/{p.name}")
    publica(a.destino, f"_infra/pacote_{h}.tgz", tgz)
    hs = hashlib.sha256(STARTUP.read_bytes()).hexdigest()[:10]
    publica(a.destino, f"_infra/startup_{hs}.sh", STARTUP)
    grava_infra(a.destino, pacote=_url(a.destino, f"_infra/pacote_{h}.tgz"))
    log(f"pacote {_url(a.destino, f'_infra/pacote_{h}.tgz')}")


def cmd_imagem(a):
    """Constrói a imagem (linux/amd64), salva como tar.gz e envia ao destino com o id da imagem no nome. A VM
    baixa do bucket da mesma região: a rodada do terrahidro pagou US$ 5 só de imagem baixada de outra região."""
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", IMAGEM_TAG, str(RAIZ)], check=True)
    img_id = sh(["docker", "image", "inspect", IMAGEM_TAG, "--format", "{{.Id}}"]).stdout.strip()
    h = img_id.split(":")[-1][:12]
    ESTADO.mkdir(exist_ok=True)
    tgz = ESTADO / f"imagem_{h}.tar.gz"
    if not tgz.exists():
        log("salvando a imagem (alguns minutos)")
        with open(tgz, "wb") as f:
            p1 = subprocess.Popen(["docker", "save", IMAGEM_TAG], stdout=subprocess.PIPE)
            subprocess.run(["gzip", "-1"], stdin=p1.stdout, stdout=f, check=True)
            if p1.wait() != 0:
                raise RuntimeError("docker save falhou")
    publica(a.destino, f"_infra/imagem_{h}.tar.gz", tgz)
    grava_infra(a.destino, imagem=_url(a.destino, f"_infra/imagem_{h}.tar.gz"),
                imagem_gib=round(tgz.stat().st_size / 2**30, 2))
    log(f"imagem {_url(a.destino, f'_infra/imagem_{h}.tar.gz')} ({tgz.stat().st_size / 2**30:.2f} GiB)")


# ----------------------------------------------------------------------------------------------------------- VMs
def _rotulo(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", s.lower())[:63]


def nome_vm(rodada: str, lote: str) -> str:
    n = re.sub(r"[^a-z0-9-]", "-", f"ls-{rodada}-{lote}".lower())
    return re.sub(r"-+", "-", n)[:63].rstrip("-")


def vms(plano: dict) -> dict[str, dict]:
    r = sh(["gcloud", "compute", "instances", "list", "--project", plano["projeto"],
            "--filter", f"labels.etapa={ROTULOS['etapa']} AND labels.rodada={_rotulo(plano['rodada'])}",
            "--format", "json(name,status,machineType,labels,scheduling.provisioningModel,creationTimestamp,zone)"])
    return {v["labels"]["lote"]: v for v in json.loads(r.stdout or "[]")}


def zona_de(v: dict) -> str:
    return v["zone"].rsplit("/", 1)[-1]


def cria(plano: dict, lote: str, infra: dict) -> bool:
    spot = plano["provisionamento"] == "spot"
    rotulos = ",".join(f"{k}={v}" for k, v in {**ROTULOS, "rodada": _rotulo(plano["rodada"]), "lote": lote}.items())
    md = {"destino": plano["destino"], "rodada": plano["rodada"], "lote": lote, "pacote": infra["pacote"],
          "imagem": infra["imagem"], "provisionamento": plano["provisionamento"]}
    base = ["gcloud", "compute", "instances", "create", nome_vm(plano["rodada"], lote), "--project", plano["projeto"],
            "--machine-type", plano["maquina"], "--image-family", "debian-12", "--image-project", "debian-cloud",
            "--boot-disk-size", f"{plano['disco_gb']}GB", "--boot-disk-type", "pd-balanced",
            "--scopes", "cloud-platform", "--labels", rotulos,
            "--metadata", ",".join(f"{k}={v}" for k, v in md.items()),
            "--metadata-from-file", f"startup-script={STARTUP}"]
    if spot:
        base += ["--provisioning-model", "SPOT", "--instance-termination-action", "STOP"]
    r = None
    for zona in plano["zonas"]:   # sem estoque numa zona, tenta as outras da região (lição do terrahidro, 21/10)
        r = sh(base + ["--zone", zona], check=False)
        if r.returncode == 0:
            log(f"{lote}: {plano['maquina']} {'spot' if spot else 'sob demanda'} criada em {zona}")
            return True
        if "stockout" not in r.stderr.lower() and "RESOURCE_POOL_EXHAUSTED" not in r.stderr:
            break
        log(f"{lote}: sem estoque de {plano['maquina']} em {zona}")
    log(f"{lote}: criação falhou: {r.stderr.strip()[-400:] if r else ''}")
    return False


def apaga(plano: dict, v: dict):
    sh(["gcloud", "compute", "instances", "delete", v["name"], "--project", plano["projeto"], "--zone", zona_de(v),
        "--delete-disks", "all", "--quiet"], check=False)


def _criada_utc(v: dict) -> str:
    return dt.datetime.fromisoformat(v["creationTimestamp"]).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def situacao(plano: dict) -> tuple[set[str], dict[str, str]]:
    """Lotes concluídos (_FIM) e a falha de VM mais recente de cada lote."""
    rodada, destino = plano["rodada"], plano["destino"]
    fim = {k.split("/")[2] for k in lista(destino, f"{rodada}/_lotes/*/_FIM")}
    falhas: dict[str, str] = {}
    for k in lista(destino, f"{rodada}/_lotes/*/_vm/_FALHA_*"):
        lote, quando = k.split("/")[2], k.rsplit("_FALHA_", 1)[1]
        falhas[lote] = max(falhas.get(lote, ""), quando)
    return fim, falhas


def cmd_coordena(a):
    """Laço: cria a VM de cada lote pendente (até --max-vms ao mesmo tempo), religa as spot interrompidas (o lote
    retoma do último passo), apaga as VMs dos lotes concluídos e para nas falhas de VM."""
    plano = le_plano(a.destino, a.rodada)
    infra = le_infra(a.destino)
    if not infra.get("pacote") or not infra.get("imagem"):
        sys.exit("falta subir a infraestrutura: python -m lidarspot imagem/pacote --destino ...")
    d = ESTADO / a.rodada
    d.mkdir(parents=True, exist_ok=True)
    alvo = a.lotes or sorted(plano["lotes"])
    while True:
        fim, falhas = situacao(plano)
        vs = vms(plano)
        estado = {"concluidos": [], "rodando": [], "religados": [], "falhas": [], "esperando": []}
        ativos = sum(1 for k, v in vs.items() if k in alvo and k not in fim)
        for lote in alvo:
            v = vs.get(lote)
            if lote in fim:
                estado["concluidos"].append(lote)
                if v and v["status"] in ("TERMINATED", "STOPPED"):
                    apaga(plano, v)
                    log(f"{lote}: concluído, VM apagada")
                continue
            if v is None:
                if ativos < a.max_vms and cria(plano, lote, infra):
                    ativos += 1
                    estado["rodando"].append(lote)
                else:
                    estado["esperando"].append(lote)
                continue
            if lote in falhas and falhas[lote] > _criada_utc(v):
                estado["falhas"].append(lote)
                continue
            if v["status"] in ("TERMINATED", "STOPPED"):
                r = sh(["gcloud", "compute", "instances", "start", v["name"], "--project", plano["projeto"],
                        "--zone", zona_de(v)], check=False)
                log(f"{lote}: religada" if r.returncode == 0 else f"{lote}: religar falhou {r.stderr[-200:]}")
                estado["religados"].append(lote)
            estado["rodando"].append(lote)
        estado["quando"] = dt.datetime.now().isoformat(timespec="seconds")
        (d / "coordenador.json").write_text(json.dumps(estado, indent=1))
        log(f"concluídos {len(estado['concluidos'])}/{len(alvo)}; rodando {len(estado['rodando'])}; "
            f"esperando {len(estado['esperando'])}; falhas {estado['falhas']}")
        if len(estado["concluidos"]) == len(alvo):
            log("rodada concluída")
            return
        if estado["falhas"] and not estado["rodando"] and not a.segue:
            log(f"parado por falhas de VM: {estado['falhas']} (veja <rodada>/_lotes/<lote>/_vm/ no destino)")
            return
        if a.uma_vez:
            return
        time.sleep(a.intervalo)


def cmd_status(a):
    plano = le_plano(a.destino, a.rodada)
    fim, falhas = situacao(plano)
    vs = vms(plano)
    sucessos = lista(a.destino, f"{a.rodada}/tiles/**/_SUCESSO")
    falhas_tile = lista(a.destino, f"{a.rodada}/tiles/**/_FALHA_*")
    total = plano["tiles"] * len(plano["formatos"])
    print(f"{a.rodada}: lotes {len(fim)}/{len(plano['lotes'])} concluídos; conversões {len(sucessos)}/{total} ok, "
          f"{len(falhas_tile)} falhas de tile; falhas de VM: {sorted(falhas) or 'nenhuma'}")
    for lote, v in sorted(vs.items()):
        print(f"  {lote:<10} {v['status']:<11} {v['machineType'].rsplit('/', 1)[1]:<16} "
              f"{v.get('scheduling', {}).get('provisioningModel', 'STANDARD'):<9} {zona_de(v)}")


def cmd_interrompe(a):
    """Simula a interrupção (preempção) de uma VM spot pelo evento de manutenção simulado do Compute Engine.
    Com --para, para a VM com `instances stop` (mesmo efeito no disco, sem o aviso de preempção)."""
    plano = le_plano(a.destino, a.rodada)
    v = vms(plano).get(a.lote)
    if not v or v["status"] != "RUNNING":
        sys.exit(f"{a.lote}: VM não está rodando")
    acao = "stop" if a.para else "simulate-maintenance-event"
    sh(["gcloud", "compute", "instances", acao, v["name"], "--project", plano["projeto"], "--zone", zona_de(v)])
    log(f"{a.lote}: {acao} enviado ({dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ})")


def cmd_recria(a):
    """Apaga a VM de um lote que falhou (com o disco): o coordenador cria outra, que começa o lote do zero no
    disco, mas pula o que já tem _SUCESSO no destino."""
    plano = le_plano(a.destino, a.rodada)
    v = vms(plano).get(a.lote)
    if v:
        apaga(plano, v)
        log(f"{a.lote}: VM apagada; o próximo ciclo do coordenador cria outra")


# ------------------------------------------------------------------------------------------- custo e relatório
def cmd_operacoes(a):
    """Histórico de operações das VMs da rodada (insert/start/stop/preempted/delete), base das horas cobradas.
    O Compute Engine guarda as operações por pouco tempo: rodar no fim de cada dia de teste."""
    plano = le_plano(a.destino, a.rodada)
    prefixo = nome_vm(plano["rodada"], "")
    r = sh(["gcloud", "compute", "operations", "list", "--project", plano["projeto"], "--zones",
            ",".join(plano["zonas"]), "--filter", f"targetLink~/instances/{prefixo}",
            "--format", "json(id,operationType,insertTime,targetLink,status)"])
    d = ESTADO / a.rodada
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"operacoes_{dt.datetime.now():%Y%m%dT%H%M%S}.json"
    f.write_text(r.stdout)
    log(f"{f} ({len(json.loads(r.stdout or '[]'))} operações)")


def custo_rodada(plano: dict) -> dict:
    d = ESTADO / plano["rodada"]
    ops = {}
    for f in sorted(d.glob("operacoes_*.json")):
        for o in json.loads(f.read_text() or "[]"):
            ops[o["id"]] = o
    por_vm: dict[str, list] = {}
    for o in ops.values():
        por_vm.setdefault(o["targetLink"].rsplit("/", 1)[1], []).append(o)
    lote_de = {nome_vm(plano["rodada"], k): k for k in plano["lotes"]}
    agora = dt.datetime.now(dt.timezone.utc)
    linhas = []
    for vm, lst in sorted(por_vm.items()):
        lote = lote_de.get(vm)
        if lote is None:
            continue
        h, inter, ligada = cst.horas_ligada(lst, ate=agora)
        linhas.append({"lote": lote, "vm": vm, "horas_ligada": round(h, 3), "interrupcoes": inter,
                       "ainda_ligada": ligada, "vms_criadas": sum(o["operationType"].endswith("insert") for o in lst),
                       **cst.custo_vm(h, plano["maquina"], plano["provisionamento"], plano["disco_gb"])})
    soma = lambda c: round(sum(x[c] or 0 for x in linhas), 4)  # noqa: E731
    total = {c: soma(c) for c in ("horas_ligada", "interrupcoes", "vms_criadas", "custo_vm_usd", "custo_disco_usd")}
    total["custo_total_usd"] = round(total["custo_vm_usd"] + total["custo_disco_usd"], 2)
    return {"lotes": linhas, "total": total}


def cmd_custo(a):
    plano = le_plano(a.destino, a.rodada)
    c = custo_rodada(plano)
    if not c["lotes"]:
        sys.exit("sem operações salvas: rode `python -m lidarspot operacoes` antes")
    (ESTADO / a.rodada / "custo.json").write_text(json.dumps(c, indent=1))
    if cst.preco_hora(plano["maquina"], plano["provisionamento"]) is None:
        log(f"sem preço de tabela para {plano['maquina']}: inclua a família em lidarspot/custo.py")
    print(json.dumps(c["total"], indent=1))


def cmd_relatorio(a):
    """Junta os _SUCESSO e _FALHA da rodada: tempo, RAM, disco e tamanho de saída por formato, e a extrapolação
    para o inventário inteiro. Com --estima, também as horas e o custo previstos dos testes T1–T4. Grava
    .estado/<rodada>/conversoes.csv e relatorio.md."""
    import csv
    import statistics as st

    plano = le_plano(a.destino, a.rodada)
    sucessos = le_jsons(a.destino, f"{a.rodada}/tiles/**/_SUCESSO")
    falhas = le_jsons(a.destino, f"{a.rodada}/tiles/**/_FALHA_*")
    d = ESTADO / a.rodada
    d.mkdir(parents=True, exist_ok=True)
    linhas = []
    for s in sucessos:
        m = s["medidas"]
        linhas.append({"tile": s["tile"]["id"], "formato": s["formato"], "laz_mib": round(s["laz_bytes"] / 2**20, 1),
                       "pontos_mi": round((s["pontos"] or 0) / 1e6, 2), "conversao_s": m.get("conversao_s"),
                       "envio_s": m.get("envio_s"), "ram_pico_gib": m.get("ram_pico_gib"),
                       "disco_livre_min_gib": m.get("disco_livre_min_gib"), "cpu_medio_pct": m.get("cpu_medio_pct"),
                       "saida_mib": round(s["saida_bytes"] / 2**20, 1), "objetos": s["envio"]["objetos"],
                       "maquina": s["maquina"].get("tipo_maquina") or s["maquina"].get("host")})
    if linhas:
        with open(d / "conversoes.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas[0]))
            w.writeheader()
            w.writerows(linhas)
    inv = plano.get("inventario", {})
    inv_gib = inv.get("bytes", 0) / 2**30
    out = [f"# Rodada {a.rodada}", "",
           f"{plano['tiles']} tiles ({plano['bytes'] / 2**30:.1f} GiB de LAZ) em {len(plano['lotes'])} lotes, "
           f"{plano['maquina']} {plano['provisionamento']}, disco {plano['disco_gb']} GB. "
           f"Inventário: {inv.get('arquivos', '?')} arquivos, {inv_gib / 1024:.2f} TiB.", "",
           "| Formato | OK | Falhas | Conversão (s/GiB LAZ) | Mpts/min | RAM pico máx (GiB) | Disco livre mín (GiB) "
           "| Saída / LAZ | Conversão do inventário (VM·h) |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for fmt in plano["formatos"]:
        ls = [x for x in linhas if x["formato"] == fmt]
        nf = sum(1 for f in falhas if f.get("formato") == fmt)
        if not ls:
            out.append(f"| {fmt} | 0 | {nf} | | | | | | |")
            continue
        gib = sum(x["laz_mib"] for x in ls) / 1024
        seg = sum(x["conversao_s"] or 0 for x in ls)
        pts = sum(x["pontos_mi"] for x in ls)
        ram = max((x["ram_pico_gib"] or 0) for x in ls)
        disco = min((x["disco_livre_min_gib"] or 0) for x in ls)
        razao = sum(x["saida_mib"] for x in ls) / 1024 / gib if gib else 0
        out.append(f"| {fmt} | {len(ls)} | {nf} | {seg / gib:.0f} | {pts / (seg / 60):.1f} | {ram:.1f} | "
                   f"{disco:.1f} | {razao:.2f} | {seg / gib * inv_gib / 3600:.0f} |")
    c = custo_rodada(plano)
    if c["lotes"]:
        t = c["total"]
        out += ["", f"VMs: {t['horas_ligada']:.2f} h ligadas, {t['interrupcoes']} interrupções, "
                    f"{t['vms_criadas']} criações; custo de tabela US$ {t['custo_total_usd']:.2f}."]
        if plano["bytes"] and inv_gib:
            fator = inv_gib / (plano["bytes"] / 2**30)
            out.append(f"Extrapolado pelo volume de LAZ para o inventário: ~{t['horas_ligada'] * fator:.0f} VM·h, "
                       f"~US$ {t['custo_total_usd'] * fator:.0f} (sem transferência nem armazenamento).")
    if a.estima and sucessos:
        inventario = lista_laz([f"{a.origem.rstrip('/')}/*/1_Nuvem_de_Pontos/*.laz"])
        tx = est.taxas(sucessos)
        out += est.tabela([est.estima(t, inventario, tx) for t in est.TESTES], tx)
    if falhas:
        out += ["", "## Falhas", ""] + [f"- {f['tile']['id']} {f['formato']}: {f['erro'][:300]}" for f in falhas]
    if linhas:
        med = st.median(x["conversao_s"] or 0 for x in linhas)
        out += ["", f"Mediana do tempo de conversão por tile e formato: {med:.0f} s."]
    texto = "\n".join(out) + "\n"
    (d / "relatorio.md").write_text(texto)
    print(texto)
    log(f"{d / 'relatorio.md'} e {d / 'conversoes.csv'}")


# ------------------------------------------------------------------------------------------------- local e VM
def cmd_local(a):
    """Roda um lote no contêiner local (teste T0), com o código do repositório montado por cima do da imagem.
    Lê LAZ do gs:// com as credenciais do gcloud do usuário (application default credentials)."""
    adc = Path.home() / ".config" / "gcloud"
    destino = a.destino if a.destino.startswith("gs://") else str(Path(a.destino).resolve())
    cmd = ["docker", "run", "--rm", "--platform", "linux/amd64", "-v", f"{RAIZ}:/opt/pacote:ro",
           "-v", f"{adc}:/root/.config/gcloud:ro", "-e", "PYTHONPATH=/opt/pacote",
           "-e", "GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json",
           "-e", f"GOOGLE_CLOUD_PROJECT={a.projeto}"]
    if not a.destino.startswith("gs://"):
        cmd += ["-v", f"{destino}:{destino}"]
    plano = le_plano(a.destino, a.rodada)
    for t in plano["lotes"][a.lote]["tiles"]:
        if not t["uri"].startswith("gs://"):
            cmd += ["-v", f"{t['uri']}:{t['uri']}:ro"]
    cmd += [IMAGEM_TAG, "python", "-m", "lidarspot", "vm", "--destino", destino, "--rodada", a.rodada,
            "--lote", a.lote, "--work", "/work"]
    log(" ".join(cmd))
    sys.exit(subprocess.run(cmd).returncode)


def cmd_vm(a):
    import logging

    from lidarspot.executor import roda_lote

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    sys.exit(roda_lote(a.destino, a.rodada, a.lote, Path(a.work)))
