# Conversões testadas

Os comandos são os do `backend-python` (`mapbiomas/amazonia3d/backend-python`, commit inicial `ef29be5`), para
que o teste meça o que a aplicação vai rodar.

| Formato | Conversor | Comando | Saída conferida | Origem no backend |
|---|---|---|---|---|
| `copc` | untwine (padrão) | `untwine -i <laz> -o <saida>/pointcloud.copc.laz --temp_dir <tmp>` | `pdal info --summary` lê como `readers.copc` | `app/worker/tasks/pointcloud_copc.py` |
| `copc` | PDAL (`--conversor-copc pdal`) | `pdal translate <laz> <saida>/pointcloud.copc.laz -w writers.copc` | idem | idem |
| `3dtiles` | py3dtiles | `py3dtiles-serial convert <laz> --out <saida> --overwrite --pyproj-always-xy --jobs <vCPUs> --verbose` | `tileset.json` legível | `app/worker/tasks/pointcloud_3dtiles.py` |
| `potree` | PotreeConverter 2.1.2 | `PotreeConverter <laz> -o <saida>` | `metadata.json`, `octree.bin`, `hierarchy.bin` | `app/processing/pointcloud/potree.py` |

Antes de converter, o LAZ é validado pelo header (laspy), com os mesmos status do backend. `CORRUPTED`, `EMPTY`,
`INVALID_HEADER` e `TRUNCATED` viram `_FALHA` do tile. Sem CRS (`MISSING_CRS`), a conversão segue com aviso.

Os objetos são enviados com o mesmo `Cache-Control` do backend: `public,max-age=31536000,immutable,no-transform`
para os dados e `no-cache` para o ponto de entrada (`tileset.json`, `metadata.json`).

## Escolha do untwine como padrão no COPC

Vem do backend: em nuvens sintéticas de 5, 10 e 40 milhões de pontos, o untwine usou cerca de metade da RAM e
rodou duas vezes mais rápido que o `writers.copc` do PDAL. A RAM do untwine cresce de forma sublinear; a do PDAL
cresce ~86 bytes/ponto. A troca é o disco: os temporários do untwine chegaram a ~25× o LAZ. Por isso o plano
dimensiona o disco em 30× o maior tile do lote quando o COPC usa untwine (`lidarspot/plano.py`). O teste T3 mede
os dois nos LAZ reais.

## Diferenças em relação ao backend-python

- **PotreeConverter não existe na imagem do backend.** O `pointcloud.pipeline` do backend chama o
  `PotreeConverter`, mas o Dockerfile dele não instala o binário, então o Potree falha em produção. Aqui a imagem
  traz a versão 2.1.2, a última com binário para Linux.
- **A conferência do Potree do backend é do formato 1.x** (`cloud.js` + `data/`). O PotreeConverter 2.x grava
  `metadata.json` + `octree.bin` + `hierarchy.bin`, e a conferência do backend reprovaria uma saída correta.
  Aqui os dois formatos são aceitos.
- **O 3D Tiles sai no CRS do LAZ.** O comando do backend não passa `--srs_out`, então o tileset fica nas
  coordenadas projetadas do LAZ (SIRGAS 2000 / UTM), e não em ECEF (EPSG:4978), que é o que o Cesium espera para
  posicionar no globo. O teste mantém o comando do backend. Para testar a outra opção, crie o plano com
  `--py3dtiles-args="--srs_out 4978"` (com `=`, porque o valor começa com `--`).
- **O py3dtiles lê o LAZ sem threads.** Com o py3dtiles 12.1.1, o laspy 2.7.0 e o lazrs 0.8.2, o backend
  padrão do laspy (`LazrsParallel`) trava os workers depois do fork, e a conversão nunca termina (T0, 27/10/2026).
  O executor chama o `py3dtiles-serial` (`lidarspot/py3dtiles_serial.py`), que é o mesmo `py3dtiles` com o
  backend `Lazrs`. Vale conferir se o backend-python, com as versões que ele fixa, tem o mesmo problema.
- **Validação só pelo laspy.** O backend tenta o binding Python do PDAL e cai no laspy quando ele não existe, o
  que acontece na imagem de produção. Aqui é só laspy.
- **O executor não chama o Node.** Em vez dos callbacks HTTP, o resultado de cada conversão fica no `_SUCESSO`,
  com o mesmo tipo de informação: entrada, objetos, bytes, pontos, CRS e tempos.
