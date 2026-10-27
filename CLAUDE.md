# CLAUDE.md

Orientação para o Claude Code neste repositório. Visão geral, uso e estrutura: [README.md](README.md). Roteiro
dos testes: [docs/TESTES.md](docs/TESTES.md). Método das VMs spot: [docs/METODO_SPOT.md](docs/METODO_SPOT.md).
Formatos e diferenças em relação ao backend: [docs/CONVERSOES.md](docs/CONVERSOES.md).

## Estado (27/10/2026)

O repositório foi escrito no Mac do João (sessão anterior) e ainda **nunca rodou com os conversores de verdade**.
Os 25 testes unitários passam (`.venv/bin/python -m pytest -q`) e o `ruff check .` está limpo. Eles não usam
GCP nem os conversores: o fluxo do lote é testado com um conversor falso e o destino numa pasta local.

A imagem Docker não chegou a ser construída: o disco do Mac encheu durante o build. **Próximo passo: construir a
imagem e rodar o T0 na `pop-os`** (Linux x86_64, Docker nativo), com o destino numa pasta local, sem gravar nada
no GCP:

```bash
docker build --platform linux/amd64 -t lidar-spot:latest .
python3 -m lidarspot plano --destino ./saida-t0 --rodada t0 --entregas ENTREGA_02 --amostra 1 --min-mib 20 --lotes 1
python3 -m lidarspot local --destino ./saida-t0 --rodada t0
python3 -m lidarspot relatorio --destino ./saida-t0 --rodada t0
```

O `local` monta `~/.config/gcloud` no contêiner para ler o LAZ do `gs://amazon-3d-lidar` com a ADC. Se não
houver `application_default_credentials.json`, o João roda `gcloud auth application-default login`.

## Pontos não verificados (conferir no T0/T1)

- **Dockerfile:** o build nunca terminou. Pontos a conferir:
  - o PDAL/untwine via micromamba é a mesma receita do backend-python, então deve funcionar;
  - o py3dtiles >= 9 precisa instalar no Python 3.12;
  - o layout do zip do PotreeConverter 2.1.2 e as bibliotecas de que ele depende (`libtbb12` e `libgomp1` foram
    instalados por palpite; rode `ldd`).
- **Wrapper do PotreeConverter** (`/usr/local/bin/PotreeConverter`): faz `cd` na pasta do binário antes de
  executar (o 2.x procura arquivos ao lado do binário). Por isso os caminhos passados a ele precisam ser
  absolutos, como o executor já faz. Se o binário não precisar do `cd`, troque por um symlink.
- **Flags dos conversores:**
  - untwine (`-i`, `-o`, `--temp_dir`) e py3dtiles (`--pyproj-always-xy`, `--jobs`) foram copiados do
    backend-python;
  - `PotreeConverter <laz> -o <dir>` é a sintaxe do 2.x.
- **`formatos._confere_potree`:** as chaves `version` e `points` do `metadata.json` do 2.x são suposições; o
  valor só entra no relatório.
- **`coordena.le_jsons`:** usa `gcloud storage cat` com curinga `**` e separa os JSONs concatenados.
- **`coordena.cmd_interrompe`:** usa `gcloud compute instances simulate-maintenance-event`, que numa VM spot deve
  disparar a preempção. Se não disparar, use `--para`.
- **Contêiner na VM:** com `--network host`, ele deve alcançar o servidor de metadados (credenciais da conta de
  serviço). O executor usa `google-cloud-storage` e não precisa do gcloud dentro da imagem.
- **Preço em `lidarspot/custo.py`:** só cobre N2D (tabela do terrahidro, 10/2026).

## Pendente com o João

- O bucket de destino em us-central1 para T1–T4 ainda não foi definido. Os comandos exigem `--destino`.
- O repositório está no GitHub (`joaosiqueira/lidar-spot-tests`, privado). Depois vai para o devops.

## Regras

- **Nunca apagar, mover ou sobrescrever nada em bucket.**
  - `gs://amazon-3d-lidar` é só leitura.
  - Todo envio é sem sobrescrever (`--no-clobber` / `if_generation_match=0`).
  - Cada rodada tem um nome novo.
- **Criar VMs custa dinheiro e mexe no projeto `mapbiomas`.** Confirme com o João antes de rodar `coordena` (T1
  em diante).
- **Contexto de origem:**
  - as conversões vêm de `mapbiomas/amazonia3d/backend-python` (GitLab);
  - o método spot vem de `scripts/cadeia_lidar.py` e `scripts/vm_startup_cadeia.sh` do `terrahidro-project`
    (`docs/PLANO_CADEIA_LIDAR.md`, seções 10–12).
- Código, comentários e docs em português, no estilo do terrahidro.
