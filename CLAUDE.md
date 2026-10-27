# CLAUDE.md

Orientação para o Claude Code neste repositório. Visão geral, uso e estrutura: [README.md](README.md). Roteiro
dos testes: [docs/TESTES.md](docs/TESTES.md). Método das VMs spot: [docs/METODO_SPOT.md](docs/METODO_SPOT.md).
Formatos e diferenças em relação ao backend: [docs/CONVERSOES.md](docs/CONVERSOES.md).

## Estado (27/10/2026)

**T0 aprovado na `pop-os`** (Linux x86_64, Docker nativo), com o destino numa pasta local e nada gravado no GCP:
os três formatos de um tile de 328 MiB da ENTREGA_02 com `_SUCESSO`, cerca de 35 s de conversão cada. O
relatório com a estimativa dos testes T1–T4 está em `.estado/t0/relatorio.md` (total de cerca de US$ 2 de tabela).
Os testes unitários passam (`.venv/bin/python -m pytest -q`) e o `ruff check .` está limpo.

```bash
docker build --platform linux/amd64 -t lidar-spot:latest .
python3 -m lidarspot plano --destino ./saida-t0 --rodada t0 --entregas ENTREGA_02 --amostra 1 --min-mib 20 --lotes 1
python3 -m lidarspot local --destino ./saida-t0 --rodada t0
python3 -m lidarspot relatorio --destino ./saida-t0 --rodada t0 --estima
```

O `local` monta `~/.config/gcloud` no contêiner para ler o LAZ do `gs://amazon-3d-lidar` com a ADC.

Corrigido no T0:
- **Base da imagem:** passou a `python:3.12-slim-trixie`, porque o PotreeConverter 2.1.2 exige glibc >= 2.38. O
  wrapper do PotreeConverter também define `LD_LIBRARY_PATH` (a `liblaszip.so` vem no zip). O build falha se o
  `ldd` acusar dependência faltando.
- **py3dtiles:** travava em qualquer LAZ (o `LazrsParallel` do laspy não sobrevive ao fork dos workers). O
  executor chama o `py3dtiles-serial` (`lidarspot/py3dtiles_serial.py`); veja `docs/CONVERSOES.md`.
- **`coordena.lista` com destino local:** não descia pelas pastas com `**`, e o relatório saía vazio.

**Próximo passo: T1**, que depende da confirmação do João para criar VMs.

**Destino de T1–T4: `gs://mapbiomas-lidar-spot-tests/`** (projeto `mapbiomas`, us-central1, criado em
27/10/2026). Tem os rótulos `projeto=amazonia3d` e `uso=lidar-spot-tests`, acesso uniforme e público bloqueado.
Uma regra de ciclo de vida apaga os objetos com mais de 30 dias. As VMs usam a conta de serviço padrão do Compute,
que tem `roles/storage.admin` no projeto.

## Pontos não verificados (conferir no T1)

- **RAM de pico:** é medida pela máquina inteira (`/proc/meminfo`). No T0 local inclui o resto do desktop, então
  os 10–13 GiB do relatório não valem; numa VM dedicada, valem.
- **Saída do Potree:** cerca de 4× o LAZ, com `encoding` DEFAULT (sem compressão). `--encoding BROTLI` reduziria.
- **`coordena.le_jsons`:** usa `gcloud storage cat` com curinga `**` e separa os JSONs concatenados.
- **`coordena.cmd_interrompe`:** usa `gcloud compute instances simulate-maintenance-event`, que numa VM spot deve
  disparar a preempção. Se não disparar, use `--para`.
- **Contêiner na VM:** com `--network host`, ele deve alcançar o servidor de metadados (credenciais da conta de
  serviço). O executor usa `google-cloud-storage` e não precisa do gcloud dentro da imagem.
- **Preço em `lidarspot/custo.py`:** só cobre N2D (tabela do terrahidro, 10/2026).

## Pendente com o João

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
