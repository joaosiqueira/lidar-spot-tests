# Método: conversão em VMs spot

O método vem da rodada de todo o LiDAR em cadeia do TerraHidro (`terrahidro-project`,
`docs/PLANO_CADEIA_LIDAR.md`, seções 10 a 12): 110 partes, 122 VMs criadas, 30 interrupções do spot, 22,4 h de
relógio, 176 VM·h cobradas, US$ 50,59. Aqui a unidade de trabalho é um **lote de tiles LAZ** em vez de uma parte
da bacia, e os lotes não dependem uns dos outros (no TerraHidro, cada parte esperava as de montante).

## Peças

| Peça | Onde | O que faz |
|---|---|---|
| Plano | `python -m lidarspot plano` | escolhe os LAZ, divide em lotes de bytes parecidos e grava `<rodada>/_plano/` no destino |
| Imagem | `Dockerfile`, `python -m lidarspot imagem` | conversores numa imagem salva como tar.gz no bucket da mesma região |
| Pacote | `python -m lidarspot pacote` | o código (`lidarspot/`) num tgz com hash, separado da imagem: mudar o código não exige reconstruir a imagem |
| Startup | `spot/startup.sh` (metadado da VM) | prepara a VM uma vez, baixa o pacote, roda o lote no contêiner, envia o log e desliga |
| Executor | `python -m lidarspot vm` (na VM) | processa o lote, retomável |
| Coordenador | `python -m lidarspot coordena` (no computador do operador) | cria as VMs, religa as interrompidas, apaga as concluídas, para nas falhas |

## Estado no bucket

O coordenador não guarda estado próprio: tudo o que ele decide vem do destino e da lista de VMs (filtrada pelos
rótulos `etapa=lidar-spot-teste` e `rodada=<rodada>`). O coordenador pode cair, ser interrompido ou trocar de
máquina sem perder a rodada.

| Marcador | Significa |
|---|---|
| `tiles/.../<formato>/_SUCESSO` | o formato daquele tile terminou e foi enviado; não roda de novo |
| `tiles/.../<formato>/_FALHA_<hora>` | a conversão falhou (dado do teste); não roda de novo no mesmo lote |
| `_lotes/<lote>/_FIM` | o lote terminou (com ou sem falhas de tile); o coordenador apaga a VM |
| `_lotes/<lote>/_vm/_FALHA_<hora>` | a VM falhou (infraestrutura); o coordenador não religa. `recria` apaga a VM para tentar de novo |

## Retomada depois de uma interrupção

As VMs spot são criadas com `--instance-termination-action STOP`: na interrupção, a VM para e o disco fica.
O coordenador vê a VM `TERMINATED` sem `_FIM` e a religa; o startup roda de novo e o executor retoma.

- **Por formato, no bucket:** o que tem `_SUCESSO` é pulado.
- **Por passo, no disco:** download, validação e conversão só contam como feitos com o marcador `.ok` gravado
  depois da saída. No TerraHidro, uma interrupção no meio de um passo deixou a saída pela metade com o nome final
  e a retomada pulou o passo (parte 4633499, 21/10/2026); outra deixou o `.part.tif` de um `gdalwarp` que
  impediu o passo de rodar de novo (4633733). Aqui, o passo sem `.ok` apaga tudo o que gerou e roda do zero, e o
  download grava em `.part` e só renomeia no fim.
- **No envio:** um objeto que já está no destino com o mesmo tamanho e crc32c conta como enviado (a interrupção
  pode cair no meio do upload). Conteúdo diferente é erro (`ConflitoNoDestino`): nunca sobrescreve.

## Lições do TerraHidro já incorporadas

| Ocorrência (10/2026) | Correção aqui |
|---|---|
| VM sem rede no boot ("Network is unreachable" no apt) ficou sem Docker, mas marcou o preparo como pronto | apt tentado até 10 vezes; o preparo só é marcado com o Docker e a imagem conferidos |
| Spot interrompida no meio do `apt-get install` deixou o dpkg pela metade e as 10 tentativas falharam | cada tentativa roda `dpkg --configure -a` antes |
| `n2d-highmem-16` sem estoque em us-central1-a (`stockout`) | o coordenador tenta as outras zonas da região (b, c, f) e usa a zona de cada VM para religar e apagar |
| Bucket de destino e imagem em us-east1, VMs em us-central1: US$ 15 dos US$ 50 foram transferência | destino, imagem e VMs na mesma região (pré-requisito do README) |
| Uma VM highmem-8 spot teve 6 interrupções (14,7 h ligada para 6,9 h de trabalho) | `--sob-demanda` no plano, para comparar e para lotes longos |
| Horas cobradas 26% acima das de processamento (interrupções, boot, preparo) | `operacoes` + `custo` medem as horas ligadas pelo histórico de operações, não pelo tempo de processamento |

## Custo

`python -m lidarspot operacoes` salva o histórico de operações das VMs da rodada (`insert`, `start`,
`preempted`, `guestTerminate`, `delete`). O Compute Engine guarda esse histórico por pouco tempo, então rode no fim
de cada dia de teste. `custo` soma as horas em que cada VM ficou ligada e multiplica pelo preço de tabela
(`lidarspot/custo.py`, N2D em us-central1). O valor faturado de fato sai do faturamento do projeto, filtrando
pelos rótulos `projeto=amazonia3d`, `etapa=lidar-spot-teste` e `rodada=<rodada>`. Ele costuma aparecer em 24–48 h
e pode diferir pelo preço spot do momento e pelos descontos do contrato.

## O que fica para a aplicação

O teste usa `gcloud` e um laço de coordenação simples para medir. Na aplicação, o coordenador pode virar um job
(Cloud Run job, Cloud Scheduler ou o próprio backend Node), e as VMs podem vir de um instance template ou de um
MIG. O que importa manter é o contrato: estado no bucket, retomada por marcador, envio sem sobrescrever, uma VM
por lote e a VM se desligando no fim.
