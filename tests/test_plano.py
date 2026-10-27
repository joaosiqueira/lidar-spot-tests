import pytest

from lidarspot import layout
from lidarspot import plano as pl

GIB = 2**30


def arquivos(n):
    return [(f"gs://b/ENTREGAS/ENTREGA_01/1_Nuvem_de_Pontos/NP_{i:04d}.laz", (i + 1) * 2**20) for i in range(n)]


def test_amostra_cobre_do_menor_ao_maior():
    todos = arquivos(100)
    escolhidos = pl.amostra(todos, 10, semente=7)
    tamanhos = sorted(b for _, b in escolhidos)
    assert len(escolhidos) == 10
    assert tamanhos[0] <= 10 * 2**20       # um da faixa dos 10% menores
    assert tamanhos[-1] >= 91 * 2**20      # um da faixa dos 10% maiores
    assert pl.amostra(todos, 10, semente=7) == escolhidos


def test_amostra_maior_que_o_total_devolve_todos():
    assert len(pl.amostra(arquivos(3), 10)) == 3


def test_divide_equilibra_bytes():
    lotes = pl.divide(arquivos(20), 4)
    pesos = [sum(b for _, b in lote) for lote in lotes]
    assert sum(len(lote) for lote in lotes) == 20
    assert max(pesos) - min(pesos) <= 20 * 2**20   # no máximo o maior arquivo de diferença


def test_divide_nunca_cria_lote_vazio():
    assert len(pl.divide(arquivos(2), 5)) == 2


def test_disco_pelo_maior_tile_e_conversor():
    assert pl.disco_gb(int(1.5 * GIB), ["copc"], {"conversor_copc": "untwine"}) == 65
    assert pl.disco_gb(int(1.5 * GIB), ["copc"], {"conversor_copc": "pdal"}) == pl.DISCO_MIN_GB
    assert pl.disco_gb(0, ["3dtiles"], {}) == pl.DISCO_MIN_GB


def test_monta_plano():
    p = pl.monta("t1", "gs://x/y/", arquivos(6), n_lotes=2, maquina="n2d-standard-8", formatos=["copc", "3dtiles"],
                 opcoes={}, provisionamento="spot", projeto="p", zonas=["us-central1-a"])
    assert sorted(p["lotes"]) == ["lote0001", "lote0002"]
    tiles = [t for lote in p["lotes"].values() for t in lote["tiles"]]
    assert len(tiles) == 6
    assert tiles[0]["id"].startswith("ENTREGA_01/NP_")


def test_monta_recusa_formato_desconhecido():
    with pytest.raises(ValueError):
        pl.monta("t", "d", arquivos(1), n_lotes=1, maquina="m", formatos=["ept"], opcoes={},
                 provisionamento="spot", projeto="p", zonas=[])


@pytest.mark.parametrize("uri,esperado", [
    ("gs://amazon-3d-lidar/ENTREGAS/ENTREGA_02/1_Nuvem_de_Pontos/NP_1438107.laz", "ENTREGA_02/NP_1438107"),
    ("gs://amazon-3d-lidar/ENTREGAS/Teste_PRF_13.10.2026/1_Nuvem_de_Pontos/A.laz", "Teste_PRF_13.10.2026/A"),
    ("/dados/teste/pequeno.laz", "teste/pequeno"),
])
def test_tile_id(uri, esperado):
    assert layout.tile_id(uri) == esperado
