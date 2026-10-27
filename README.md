# lidar-spot-tests

Testes de conversão das nuvens de pontos LiDAR do Amazônia 3D (LAZ em `gs://amazon-3d-lidar/ENTREGAS/`) em
**VMs spot** do Compute Engine. O repositório junta duas coisas que já existem:

- **as conversões do `backend-python`** (`mapbiomas/amazonia3d/backend-python`): LAZ para **COPC**
  (untwine ou PDAL), **3D Tiles** (py3dtiles) e **Potree** (PotreeConverter), com os mesmos comandos e a mesma
  validação do LAZ;
- **o método de processamento em VMs spot do TerraHidro** (`terrahidro-project`), que processou todo o LiDAR
  em 110 VMs (22,4 h, 176 VM·h, US$ 50,59 em 10/2026): uma VM por unidade de trabalho, estado todo no
  bucket, retomada depois de interrupção do spot, coordenador que religa e apaga as VMs, custo medido pelo
  histórico de operações.

O objetivo é o devops testar o uso de VMs spot na conversão (preparo, interrupções, custo, máquina certa) antes
de levar o processo para a aplicação.

## O que tem aqui

```
lidarspot/            código (Python 3.10+)
  __main__.py         CLI: python -m lidarspot <comando>
  plano.py            escolha dos LAZ (amostra por tamanho) e divisão em lotes (uma VM por lote)
  coordena.py         lado do operador: imagem, pacote, plano, coordena, status, interrompe, custo, relatório
  executor.py         lado da VM: baixa, valida, converte, confere, envia e marca cada tile/formato
  formatos.py         comandos e conferência de saída de COPC, 3D Tiles e Potree
  validacao.py        validação do LAS/LAZ pelo header (laspy)
  bucket.py           destino gs:// ou pasta local, nunca sobrescreve
  recursos.py         RAM de pico, disco livre mínimo e CPU médio durante cada conversão
  custo.py            horas ligadas pelo histórico de operações × preço de tabela
spot/startup.sh       script de inicialização das VMs
Dockerfile            imagem dos conversores (PDAL + untwine, py3dtiles, PotreeConverter)
tests/                testes unitários (sem GCP)
docs/TESTES.md        roteiro dos testes T0–T4 com critérios
docs/METODO_SPOT.md   o método das VMs spot e de onde vem cada decisão
docs/CONVERSOES.md    os formatos, os comandos e o que difere do backend-python
```

## Pré-requisitos

| Item | Valor |
|---|---|
| Projeto GCP | `mapbiomas` (o mesmo da rodada do TerraHidro; cota de 3.000 vCPU N2D e 5.000 spot em 10/2026) |
| Região | `us-central1`, a mesma do `gs://amazon-3d-lidar`: leitura sem transferência entre regiões |
| Bucket de destino | **a definir pelo devops**, em us-central1. A rodada do TerraHidro pagou ~30% do custo em transferência por gravar em us-east1 |
| Conta de serviço das VMs | a padrão do Compute, escopo `cloud-platform`: leitura em `gs://amazon-3d-lidar`, criação e leitura de objetos no destino. Não precisa de permissão para apagar |
| Na máquina do operador | Python 3.10+, `gcloud` autenticado, Docker (para construir a imagem) |

Regra herdada do TerraHidro: **nada no bucket é apagado, movido ou sobrescrito**. Todo envio é `--no-clobber`
(ou `if_generation_match=0`), cada rodada tem um nome novo, e os pacotes e a imagem levam o hash no nome.

## Uso rápido

```bash
D=gs://mapbiomas-lidar-spot-tests/          # destino (bucket em us-central1)

python -m lidarspot imagem --destino $D      # constrói e envia a imagem (uma vez por versão)
python -m lidarspot pacote --destino $D      # envia o código das VMs (a cada mudança)

# rodada t1: 3 tiles (pequeno, mediano e grande) da ENTREGA_02 numa VM spot, os 3 formatos
python -m lidarspot plano --destino $D --rodada t1 --entregas ENTREGA_02 --amostra 3 --lotes 1
python -m lidarspot coordena --destino $D --rodada t1        # cria, religa e apaga as VMs até o fim
python -m lidarspot status --destino $D --rodada t1          # em outro terminal

python -m lidarspot operacoes --destino $D --rodada t1       # histórico de operações (horas cobradas)
python -m lidarspot relatorio --destino $D --rodada t1       # .estado/t1/relatorio.md e conversoes.csv
```

O roteiro completo, com o teste local (T0), a simulação de preempção (T2), a escolha de máquina (T3) e o teste
de escala (T4), está em [docs/TESTES.md](docs/TESTES.md).

## Onde fica cada coisa no destino

```
_infra/imagem_<id>.tar.gz, pacote_<hash>.tgz, startup_<hash>.sh
<rodada>/_plano/plano.json e <lote>.json
<rodada>/tiles/<entrega>/<tile>/<formato>/...         saída (pointcloud.copc.laz, tileset.json, metadata.json)
<rodada>/tiles/<entrega>/<tile>/<formato>/_SUCESSO    medições: tempo, RAM, disco, CPU, tamanhos, máquina
<rodada>/tiles/<entrega>/<tile>/<formato>/_FALHA_<hora>
<rodada>/_logs/<entrega>/<tile>/<formato>_<hora>.log  saída do conversor
<rodada>/_lotes/<lote>/_FIM                           lote concluído
<rodada>/_lotes/<lote>/_vm/                           log de cada boot e _FALHA_<hora> da VM
```

Arquivos locais do operador (plano, operações, custo, relatório) ficam em `.estado/`, fora do git.

## Desenvolvimento

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Os testes unitários cobrem a divisão em lotes, a retomada (passos com marcador `.ok`, formatos com `_SUCESSO`
no destino), a regra de não sobrescrever, a conferência das saídas e o cálculo de horas e custo. Os conversores
de verdade só rodam na imagem (teste T0).
