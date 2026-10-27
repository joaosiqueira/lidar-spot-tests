import pytest

from lidarspot import custo as cst
from lidarspot import estimativa as est

GIB = 2**30


def sucesso(formato, conversao_s, envio_s=0, gib=1, vcpus=8):
    return {"formato": formato, "laz_bytes": gib * GIB, "medidas": {"conversao_s": conversao_s, "envio_s": envio_s},
            "maquina": {"vcpus": vcpus}}


def inventario(entrega, n, gib=1):
    return [(f"gs://b/ENTREGAS/{entrega}/1_Nuvem_de_Pontos/T{i:03d}.laz", gib * GIB) for i in range(n)]


def test_taxas_por_gib():
    tx = est.taxas([sucesso("copc", 30, 2), sucesso("copc", 90, 4, gib=2), sucesso("potree", 10)])
    assert tx["copc"]["conversao_s_gib"] == pytest.approx(40)
    assert tx["copc"]["envio_s_gib"] == pytest.approx(2)
    assert tx["potree"]["vcpus"] == 8


def test_estima_uma_vm_so_da_entrega_pedida():
    tx = est.taxas([sucesso("copc", 100), sucesso("3dtiles", 200), sucesso("potree", 60)])
    inv = inventario("ENTREGA_02", 10) + inventario("ENTREGA_10", 10, gib=5)
    e = est.estima({"rodada": "t", "entregas": ["ENTREGA_02"], "amostra": 3, "lotes": 1}, inv, tx)
    assert e["tiles"] == 3 and e["gib"] == pytest.approx(3) and e["vms"] == 1
    seg = est.PREPARO_MIN * 60 + 3 * (est.DOWNLOAD_S_POR_GIB + 360)
    assert e["horas"] == pytest.approx(seg / 3600)
    assert e["custo_usd"] == pytest.approx(e["horas"] * cst.preco_hora("n2d-standard-8", "spot"), rel=0.05)


def test_estima_escala_com_vcpus_e_divide_em_lotes():
    tx = est.taxas([sucesso("copc", 100, vcpus=8)])
    inv = inventario("ENTREGA_01", 8)
    t4 = est.estima({"rodada": "t", "amostra": 8, "lotes": 4, "formatos": ["copc"],
                     "maquina": "n2d-standard-4"}, inv, tx)
    assert t4["vms"] == 4
    por_vm = est.PREPARO_MIN * 60 + 2 * (est.DOWNLOAD_S_POR_GIB + 200)
    assert t4["relogio_h"] == pytest.approx(por_vm / 3600)
    assert t4["horas"] == pytest.approx(4 * por_vm / 3600)


def test_formato_sem_medida_fica_de_fora():
    tx = est.taxas([sucesso("copc", 100)])
    e = est.estima({"rodada": "t", "amostra": 1, "lotes": 1}, inventario("ENTREGA_01", 2), tx)
    assert e["sem_medida"] == ["3dtiles", "potree"]
