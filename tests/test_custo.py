import datetime as dt
import json

import pytest

from lidarspot import coordena
from lidarspot import custo as cst


def op(tipo, hora):
    return {"operationType": f"compute.instances.{tipo}", "insertTime": f"2026-10-21T{hora}:00.000-07:00"}


def test_horas_com_preempcao_e_religada():
    ops = [op("insert", "09:00"), op("preempted", "10:30"), op("start", "10:45"), op("guestTerminate", "11:45")]
    horas, inter, ligada = cst.horas_ligada(ops)
    assert horas == pytest.approx(2.5)
    assert inter == 1 and not ligada


def test_vm_ainda_ligada_conta_ate_agora():
    ate = dt.datetime(2026, 10, 21, 18, 0, tzinfo=dt.timezone.utc)   # 11:00 em -07:00
    horas, _, ligada = cst.horas_ligada([op("insert", "10:00")], ate=ate)
    assert ligada and horas == pytest.approx(1.0)


def test_recursos_e_preco():
    assert cst.recursos("n2d-standard-8") == ("n2d", 8, 32)
    assert cst.recursos("n2d-highmem-4") == ("n2d", 4, 32)
    assert cst.preco_hora("n2d-standard-8", "spot") == pytest.approx(8 * 0.012640 + 32 * 0.001693)
    assert cst.preco_hora("c3-standard-8", "spot") is None


def test_nome_vm_valido_no_gce():
    n = coordena.nome_vm("T1_Uma.VM", "lote0001")
    assert n == "ls-t1-uma-vm-lote0001"
    assert len(coordena.nome_vm("x" * 80, "lote0001")) <= 63


def test_lista_local_desce_pelas_pastas(tmp_path):
    for fmt in ("copc", "potree"):
        (tmp_path / "r/tiles/E/T1" / fmt).mkdir(parents=True)
        (tmp_path / "r/tiles/E/T1" / fmt / "_SUCESSO").write_text(json.dumps({"formato": fmt}))
    assert coordena.lista(str(tmp_path), "r/tiles/**/_SUCESSO") == ["r/tiles/E/T1/copc/_SUCESSO",
                                                                    "r/tiles/E/T1/potree/_SUCESSO"]
    assert [j["formato"] for j in coordena.le_jsons(str(tmp_path), "r/tiles/**/_SUCESSO")] == ["copc", "potree"]
