"""
Testes de conversão de nuvens LiDAR (LAZ -> COPC, 3D Tiles, Potree) em VMs spot do Compute Engine.

Fluxo de uma rodada (detalhes em README.md e docs/TESTES.md):

    python -m lidarspot imagem    --destino gs://BUCKET/PREFIXO      # uma vez por versão da imagem
    python -m lidarspot pacote    --destino gs://BUCKET/PREFIXO      # a cada mudança no código
    python -m lidarspot plano     --destino ... --rodada t1 --amostra 3 --lotes 1
    python -m lidarspot coordena  --destino ... --rodada t1
    python -m lidarspot operacoes --destino ... --rodada t1
    python -m lidarspot relatorio --destino ... --rodada t1
"""

from __future__ import annotations

import argparse

from lidarspot import coordena as c


def main():
    p = argparse.ArgumentParser(prog="python -m lidarspot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)

    def novo(nome, fn, ajuda, rodada=True):
        sp = s.add_parser(nome, help=ajuda, description=fn.__doc__)
        sp.add_argument("--destino", required=True, help="gs://bucket/prefixo/ (ou uma pasta local, no teste T0)")
        if rodada:
            sp.add_argument("--rodada", required=True, help="nome da rodada de teste (ex.: t1-uma-vm)")
        sp.set_defaults(fn=fn)
        return sp

    novo("imagem", c.cmd_imagem, "constrói e envia a imagem Docker dos conversores", rodada=False)
    novo("pacote", c.cmd_pacote, "envia o código das VMs", rodada=False)

    pl = novo("plano", c.cmd_plano, "escolhe os LAZ, divide em lotes e grava o plano no destino")
    pl.add_argument("--origem", default="gs://amazon-3d-lidar/ENTREGAS")
    pl.add_argument("--entregas", help="ENTREGA_02,ENTREGA_08 (padrão: todas)")
    pl.add_argument("--lista", help="arquivo com uma URI (ou caminho local) de LAZ por linha, no lugar de --entregas")
    pl.add_argument("--amostra", type=int, help="N tiles espalhados pela distribuição de tamanho (padrão: todos)")
    pl.add_argument("--semente", type=int, default=1)
    pl.add_argument("--min-mib", type=float, default=1.0, help="ignora LAZ menores que isso")
    pl.add_argument("--lotes", type=int, help="número de lotes (VMs)")
    pl.add_argument("--tiles-por-lote", type=int, default=5, help="se --lotes não for dado")
    pl.add_argument("--formatos", default="copc,3dtiles,potree")
    pl.add_argument("--conversor-copc", choices=["untwine", "pdal"], default="untwine")
    pl.add_argument("--jobs", type=int, help="processos do py3dtiles (padrão: vCPUs da VM)")
    pl.add_argument(
        "--py3dtiles-args", default="", help='argumentos extras do py3dtiles, ex.: --py3dtiles-args="--srs_out 4978"'
    )
    pl.add_argument("--timeout-h", type=float, default=6)
    pl.add_argument("--maquina", default="n2d-standard-8")
    pl.add_argument("--disco-gb", type=int, help="padrão: calculado pelo maior tile")
    pl.add_argument("--sob-demanda", action="store_true", help="VMs sob demanda em vez de spot (comparação)")
    pl.add_argument("--projeto", default="mapbiomas")
    pl.add_argument("--zonas", default="us-central1-a,us-central1-b,us-central1-c,us-central1-f")

    co = novo("coordena", c.cmd_coordena, "cria, religa e apaga as VMs até a rodada terminar")
    co.add_argument("lotes", nargs="*", help="só estes lotes (padrão: todos)")
    co.add_argument("--max-vms", type=int, default=10)
    co.add_argument("--intervalo", type=int, default=120, help="segundos entre ciclos")
    co.add_argument("--segue", action="store_true", help="não para quando uma VM falha")
    co.add_argument("--uma-vez", action="store_true", help="um ciclo só")

    novo("status", c.cmd_status, "lotes, conversões e VMs da rodada")
    it = novo("interrompe", c.cmd_interrompe, "simula a preempção de uma VM spot (teste T2)")
    it.add_argument("lote")
    it.add_argument("--para", action="store_true", help="usa `instances stop` em vez do evento simulado")
    rc = novo("recria", c.cmd_recria, "apaga a VM de um lote que falhou para o coordenador criar outra")
    rc.add_argument("lote")
    novo("operacoes", c.cmd_operacoes, "salva o histórico de operações das VMs (base das horas cobradas)")
    novo("custo", c.cmd_custo, "horas ligadas e custo de tabela da rodada")
    rl = novo("relatorio", c.cmd_relatorio, "medições por formato e extrapolação para o inventário")
    rl.add_argument("--estima", action="store_true", help="estima horas e custo dos testes T1–T4 com estas medidas")
    rl.add_argument("--origem", default="gs://amazon-3d-lidar/ENTREGAS", help="inventário usado pela --estima")

    lo = novo("local", c.cmd_local, "roda um lote no Docker local (teste T0)")
    lo.add_argument("--lote", default="lote0001")
    lo.add_argument("--projeto", default="mapbiomas")
    vm = novo("vm", c.cmd_vm, "executor de um lote (roda dentro do contêiner, na VM)")
    vm.add_argument("--lote", required=True)
    vm.add_argument("--work", default="/work")

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
