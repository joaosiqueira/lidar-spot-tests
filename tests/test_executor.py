"""Retomada depois de interrupção e regra de nunca sobrescrever, com o destino numa pasta local."""

import json
from pathlib import Path

import pytest

from lidarspot import bucket, executor, formatos


def test_passo_pula_o_que_tem_ok(tmp_path):
    saida = tmp_path / "saida.txt"
    chamadas = []
    executor.passo(tmp_path / "x.ok", [saida], lambda: (chamadas.append(1), saida.write_text("a")))
    executor.passo(tmp_path / "x.ok", [saida], lambda: chamadas.append(2))
    assert chamadas == [1]


def test_passo_sem_ok_apaga_saida_pela_metade(tmp_path):
    """Interrupção no meio do passo: a saída existe com o nome final, mas sem .ok. Tem que rodar de novo do zero."""
    saida = tmp_path / "saida"
    saida.mkdir()
    (saida / "parcial.bin").write_text("lixo")
    vistos = []
    executor.passo(tmp_path / "x.ok", [saida], lambda: vistos.append(saida.exists()))
    assert vistos == [False]
    assert (tmp_path / "x.ok").exists()


def test_destino_local_envio_idempotente_e_sem_sobrescrever(tmp_path):
    dst = bucket.DestinoLocal(str(tmp_path / "dst"))
    f = tmp_path / "a.bin"
    f.write_bytes(b"123")
    assert dst.envia(f, "r/a.bin").ja_existia is False
    assert dst.envia(f, "r/a.bin").ja_existia is True        # retomada: mesmo conteúdo conta como enviado
    f.write_bytes(b"456")
    with pytest.raises(bucket.ConflitoNoDestino):
        dst.envia(f, "r/a.bin")
    assert (tmp_path / "dst" / "r" / "a.bin").read_bytes() == b"123"


def test_baixa_nao_deixa_parcial_com_nome_final(tmp_path):
    origem = tmp_path / "o.laz"
    origem.write_bytes(b"x" * 10)
    local = tmp_path / "w" / "o.laz"
    assert bucket.baixa(str(origem), local) == 10
    assert not (tmp_path / "w" / "o.laz.part").exists()


def _confere_potree(tmp_path, nomes):
    d = tmp_path / "potree"
    d.mkdir()
    for n in nomes:
        (d / n).write_text(json.dumps({"version": "2.0", "points": 5}) if n.endswith(".json") else "x")
    return formatos.confere("potree", d)


def test_potree_2x(tmp_path):
    s = _confere_potree(tmp_path, ["metadata.json", "octree.bin", "hierarchy.bin"])
    assert s.entrada == "metadata.json" and s.info["pontos"] == 5


def test_potree_2x_incompleto(tmp_path):
    with pytest.raises(formatos.SaidaInvalida):
        _confere_potree(tmp_path, ["metadata.json", "octree.bin"])


def test_3dtiles_publica_a_partir_da_pasta_do_tileset(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "tileset.json").write_text("{}")
    s = formatos.confere("3dtiles", tmp_path)
    assert s.raiz == tmp_path / "sub"


def test_comando_copc():
    o, s, t = Path("/i/a.laz"), Path("/o"), Path("/t")
    assert formatos.comando("copc", o, s, t, {})[0] == "untwine"
    assert formatos.comando("copc", o, s, t, {"conversor_copc": "pdal"})[:2] == ["pdal", "translate"]
    with pytest.raises(ValueError):
        formatos.comando("copc", o, s, t, {"conversor_copc": "lastools"})


class FalsoConversor:
    """Troca a conversão por uma cópia, para testar o fluxo do lote sem os conversores."""

    def __init__(self, monkeypatch, falha_em=None):
        self.chamadas = []
        monkeypatch.setattr(formatos, "disponivel", lambda f, o: True)
        monkeypatch.setattr(executor, "valida", lambda p: _Val())

        def roda(cmd, arq_log, timeout_s):
            self.chamadas.append(cmd)
            arq_log.write_text("ok")
            if falha_em and falha_em in cmd[0]:
                return {"rc": 1, "dur_s": 0.1, "cauda": ["erro simulado"]}
            saida = Path(cmd[cmd.index("-o") + 1]) if "-o" in cmd else Path(cmd[cmd.index("--out") + 1])
            if cmd[0] == "untwine":
                saida.write_bytes(b"copc")
            elif cmd[0] == "py3dtiles-serial":
                (saida / "tileset.json").write_text("{}")
                (saida / "r.pnts").write_bytes(b"p")
            else:
                for n in ("metadata.json", "octree.bin", "hierarchy.bin"):
                    (saida / n).write_text("{}")
            return {"rc": 0, "dur_s": 0.1, "cauda": []}

        monkeypatch.setattr(executor, "roda_comando", roda)
        monkeypatch.setattr(formatos, "_confere_copc",
                            lambda s: formatos.Saida(s, formatos.COPC_ARQUIVO, {"bytes": 4}))
        monkeypatch.setattr(executor, "maquina", lambda: {"host": "teste"})


class _Val:
    def dict(self):
        return {"status": "VALID", "pontos": 10, "bytes": 8}


def _lote(tmp_path, n=2):
    dst = tmp_path / "dst"
    tiles = []
    for i in range(n):
        f = tmp_path / "fonte" / f"t{i}.laz"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"lazlazla")
        tiles.append({"id": f"fonte/t{i}", "uri": str(f), "bytes": 8})
    d = bucket.DestinoLocal(str(dst))
    d.grava_texto("r/_plano/lote0001.json", json.dumps(
        {"formatos": ["copc", "3dtiles", "potree"], "opcoes": {}, "tiles": tiles}))
    return str(dst), d


def test_lote_completo_e_rerun_nao_repete(tmp_path, monkeypatch):
    conv = FalsoConversor(monkeypatch)
    dst, d = _lote(tmp_path)
    assert executor.roda_lote(dst, "r", "lote0001", tmp_path / "work") == 0
    assert len(conv.chamadas) == 6
    assert d.existe("r/tiles/fonte/t0/copc/pointcloud.copc.laz")
    assert d.existe("r/tiles/fonte/t1/3dtiles/tileset.json")
    assert d.existe("r/tiles/fonte/t1/potree/_SUCESSO")
    fim = json.loads(d.le_texto("r/_lotes/lote0001/_FIM"))
    assert fim["conversoes_ok"] == 6 and fim["falhas"] == []
    executor.roda_lote(dst, "r", "lote0001", tmp_path / "work")
    assert len(conv.chamadas) == 6


def test_retomada_pula_formatos_com_sucesso(tmp_path, monkeypatch):
    """Simula a VM interrompida depois do COPC do t0: no reboot, só o resto roda."""
    conv = FalsoConversor(monkeypatch)
    dst, d = _lote(tmp_path, n=1)
    d.grava_texto("r/tiles/fonte/t0/copc/_SUCESSO", "{}")
    executor.roda_lote(dst, "r", "lote0001", tmp_path / "work")
    assert [c[0] for c in conv.chamadas] == ["py3dtiles-serial", "PotreeConverter"]


def test_falha_de_conversao_nao_derruba_o_lote(tmp_path, monkeypatch):
    FalsoConversor(monkeypatch, falha_em="py3dtiles")
    dst, d = _lote(tmp_path, n=1)
    assert executor.roda_lote(dst, "r", "lote0001", tmp_path / "work") == 0
    assert d.lista("r/tiles/fonte/t0/3dtiles/_FALHA_")
    assert d.existe("r/tiles/fonte/t0/potree/_SUCESSO")
    assert json.loads(d.le_texto("r/_lotes/lote0001/_FIM"))["falhas"] == ["fonte/t0 3dtiles"]
