#!/bin/bash
# Inicialização das VMs do teste (uma VM por lote). Roda como root a cada boot, inclusive depois de uma
# interrupção do spot: prepara a máquina uma vez (Docker e a imagem dos conversores), baixa o código, roda o lote
# no contêiner (que retoma do último passo feito) e desliga a VM no fim. Se o lote falhar, grava
# <rodada>/_lotes/<lote>/_vm/_FALHA_<hora UTC> no destino e desliga também: o coordenador não religa um lote que
# falhou.
#
# Vem do vm_startup_cadeia.sh do terrahidro, com as correções das ocorrências da rodada de 21-22/10/2026:
# - a rede pode não estar pronta no boot ("Network is unreachable" no apt): o apt é tentado até 10 vezes;
# - spot interrompida no meio do apt deixa o dpkg pela metade: cada tentativa roda antes `dpkg --configure -a`;
# - o preparo só é marcado como pronto depois de conferir o Docker e a imagem carregada.
exec > >(tee -a /var/log/lidarspot.log) 2>&1   # também vai para a porta serial (google_metadata_script_runner)
set -x
MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes
md() { curl -s -H "Metadata-Flavor: Google" "$MD/$1"; }
DESTINO=$(md destino); RODADA=$(md rodada); LOTE=$(md lote); PACOTE=$(md pacote); IMAGEM=$(md imagem)
LOTE_URL="${DESTINO%/}/$RODADA/_lotes/$LOTE"
echo "=== boot $(date -u +%FT%TZ) rodada=$RODADA lote=$LOTE pacote=$PACOTE"

if [ ! -f /opt/ls/pronto ]; then
  t0=$(date +%s)
  for n in 1 2 3 4 5 6 7 8 9 10; do
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io \
      && command -v docker && break
    echo "apt falhou (tentativa $n), espera 30 s"; sleep 30
  done
  mkdir -p /opt/ls /work
  gcloud storage cp "$IMAGEM" /opt/ls/imagem.tar.gz \
    && gunzip -c /opt/ls/imagem.tar.gz | docker load \
    && rm -f /opt/ls/imagem.tar.gz
  echo "{\"preparo_s\": $(( $(date +%s) - t0 ))}" > /opt/ls/preparo.json
  docker image inspect lidar-spot:latest >/dev/null && touch /opt/ls/pronto
fi

# o código a cada boot: uma versão nova vale depois de religar (metadado pacote)
rm -rf /opt/ls/pacote && mkdir -p /opt/ls/pacote
gcloud storage cp "$PACOTE" /opt/ls/pacote.tgz && tar xzf /opt/ls/pacote.tgz -C /opt/ls/pacote

# --network host: o contêiner fala com o servidor de metadados (credenciais da conta de serviço da VM)
docker run --rm --network host -v /work:/work -v /opt/ls/pacote:/opt/pacote:ro -e PYTHONPATH=/opt/pacote \
  lidar-spot:latest python -m lidarspot vm --destino "$DESTINO" --rodada "$RODADA" --lote "$LOTE" --work /work
rc=$?
S=$(date -u +%Y%m%dT%H%M%SZ)
echo "=== fim $(date -u +%FT%TZ) rc=$rc"
gcloud storage cp --no-clobber /opt/ls/preparo.json "$LOTE_URL/_vm/preparo_$S.json"
gcloud storage cp --no-clobber /var/log/lidarspot.log "$LOTE_URL/_vm/boot_$S.log"
if [ $rc -ne 0 ]; then
  echo "rc=$rc" > /tmp/falha.txt
  gcloud storage cp --no-clobber /tmp/falha.txt "$LOTE_URL/_vm/_FALHA_$S"
fi
shutdown -h now
