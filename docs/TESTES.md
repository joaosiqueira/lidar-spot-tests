# Roteiro dos testes

Inventário em 10/2026: **10.389 LAZ, 4,78 TiB** em `gs://amazon-3d-lidar/ENTREGAS/*/1_Nuvem_de_Pontos/`
(ENTREGA_01 a 14 e Teste_PRF). Mediana de 428 MiB por arquivo, o maior com 1,46 GiB.

Os testes vão do mais barato ao mais caro. Cada um tem uma pergunta e um critério de aprovado. Cada rodada tem
um nome novo, porque nada no destino é sobrescrito. Nos exemplos:

```bash
D=gs://mapbiomas-lidar-spot-tests/
```

## T0: imagem e conversores, no Docker local

**Pergunta:** a imagem constrói e os três conversores funcionam num LAZ real?

```bash
docker build --platform linux/amd64 -t lidar-spot:latest .
python -m lidarspot plano --destino ./saida-t0 --rodada t0 --entregas ENTREGA_02 --amostra 1 --min-mib 20 --lotes 1
python -m lidarspot local --destino ./saida-t0 --rodada t0
python -m lidarspot relatorio --destino ./saida-t0 --rodada t0 --estima
```

Com `--estima`, o relatório inclui as horas e o custo previstos de T1–T4 com as taxas medidas (hipóteses em
`lidarspot/estimativa.py`).

O destino é uma pasta local: nada é gravado no GCP. O LAZ é lido do bucket com as credenciais do `gcloud` do
operador (`gcloud auth application-default login`). Num Mac com Apple Silicon, a imagem roda emulada (amd64) e
leva bem mais tempo. Prefira um Linux x86, ou use `--lista` com um LAZ pequeno.

**Aprovado:** os três formatos com `_SUCESSO` em `saida-t0/t0/tiles/...`. O `pointcloud.copc.laz` abre em
<https://viewer.copc.io>, e o `tileset.json` e o `metadata.json` estão na pasta de saída.

## T1: uma VM spot, ponta a ponta

**Pergunta:** a VM sobe, prepara, lê o LiDAR, converte, envia e se apaga sem intervenção?

```bash
python -m lidarspot imagem --destino $D
python -m lidarspot pacote --destino $D
python -m lidarspot plano --destino $D --rodada t1 --entregas ENTREGA_02 --amostra 3 --lotes 1
python -m lidarspot coordena --destino $D --rodada t1
python -m lidarspot operacoes --destino $D --rodada t1 && python -m lidarspot relatorio --destino $D --rodada t1
```

A amostra de 3 pega um tile pequeno, um mediano e um grande. Acompanhe a porta serial
(`gcloud compute instances get-serial-port-output ls-t1-lote0001 --zone ...`) ou o `status`.

**Aprovado:**
- 9 conversões com `_SUCESSO` e o `_FIM` do lote;
- a VM apagada pelo coordenador;
- o `preparo_*.json` em `_lotes/lote0001/_vm/` com o tempo de preparo (no TerraHidro foram 2–3 min).

## T2: interrupção do spot

**Pergunta:** a interrupção no meio de uma conversão é retomada sem perder nem duplicar nada?

```bash
python -m lidarspot plano --destino $D --rodada t2 --entregas ENTREGA_10 --amostra 4 --lotes 1
python -m lidarspot coordena --destino $D --rodada t2 &
# com a VM convertendo (veja "convertendo" na porta serial), simule a preempção:
python -m lidarspot interrompe --destino $D --rodada t2 lote0001
# repita durante um envio e durante o preparo (apt/docker load) de uma VM nova
```

`interrompe` usa o evento de manutenção simulado do Compute Engine, que numa VM spot dispara a preempção.
Com `--para`, a VM é parada com `instances stop`. O efeito no disco é o mesmo, mas sem o aviso de preempção.

**Aprovado:**
- o coordenador religa a VM e o lote termina;
- a porta serial mostra o formato interrompido rodando de novo do zero, e os anteriores pulados ("já concluído
  no destino");
- nenhum `ConflitoNoDestino` no log;
- o `custo` conta as interrupções (`interrupcoes` > 0).

## T3: escolha de máquina e de conversor

**Pergunta:** qual máquina e qual conversor COPC dão o menor custo por GiB sem estourar RAM nem disco?

Os mesmos 6 tiles (mesma semente) em rodadas separadas. Só a máquina ou o conversor muda:

```bash
for m in n2d-standard-4 n2d-standard-8 n2d-highmem-8; do
  python -m lidarspot plano --destino $D --rodada t3-$m --amostra 6 --semente 3 --lotes 1 --maquina $m
done
python -m lidarspot plano --destino $D --rodada t3-pdal --amostra 6 --semente 3 --lotes 1 \
  --formatos copc --conversor-copc pdal
python -m lidarspot coordena --destino $D --rodada t3-n2d-standard-4   # e as outras, em paralelo
```

Compare nos `relatorio.md`:
- s/GiB de conversão por formato;
- RAM de pico (o PDAL deve crescer ~86 B/ponto);
- disco livre mínimo (o untwine chega a ~25× o LAZ em temporários; se sobrar muito, reduza o fator em
  `lidarspot/plano.py`);
- custo por GiB.

O py3dtiles usa todos os vCPUs, então ele é o formato que mais deve ganhar com máquinas maiores.

**Resultado esperado:** uma máquina padrão para o T4 e o fator de disco recalibrado.

## T4: escala

**Pergunta:** muitas VMs ao mesmo tempo, com interrupções reais, mantêm o custo e o tempo previstos?

```bash
python -m lidarspot plano --destino $D --rodada t4 --amostra 100 --tiles-por-lote 5 --maquina <do T3>
python -m lidarspot coordena --destino $D --rodada t4 --max-vms 20
```

Cerca de 45 GiB em 20 VMs. Observe:
- os pedidos recusados por falta de estoque (o coordenador troca de zona);
- as interrupções reais;
- o relógio contra a soma das horas;
- o custo medido contra o custo extrapolado.

O `relatorio` extrapola as horas e o custo para o inventário inteiro (4,78 TiB) pelo volume de LAZ.

**Aprovado:** todos os lotes com `_FIM`, as falhas de tile explicadas (LAZ corrompido, por exemplo) e uma estimativa
de horas e custo para converter o inventário inteiro.

## Depois dos testes

Com o T4 aprovado, uma entrega inteira (`--entregas ENTREGA_01`, 694 arquivos, 199 GiB) é o próximo passo antes
de rodar o inventário. A conta para a aplicação inclui também:
- o armazenamento da saída (a razão saída/LAZ de cada formato está no relatório);
- a transferência, que é zero se destino, VMs e LiDAR ficarem em us-central1.
