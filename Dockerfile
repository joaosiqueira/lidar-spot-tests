# Imagem dos conversores: PDAL + untwine (COPC), py3dtiles (3D Tiles), PotreeConverter (Potree) e o executor.
# As ferramentas são as mesmas do backend-python (Dockerfile de mapbiomas/amazonia3d/backend-python), mais o
# PotreeConverter, que o backend chama mas a imagem dele não tem.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bzip2 ca-certificates curl unzip libgomp1 libtbb12 procps util-linux \
    && rm -rf /var/lib/apt/lists/*

# PDAL não existe nos repositórios do Debian bookworm, então vem do conda-forge, isolado em /opt/pdal e exposto
# só por symlink (o /opt/pdal/bin traz python, gdalinfo e openssl próprios). Igual ao backend-python.
ENV MAMBA_ROOT_PREFIX=/opt/mamba
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
      | tar -xj -C /usr/local bin/micromamba \
    && /usr/local/bin/micromamba create -y -q -p /opt/pdal -c conda-forge pdal untwine \
    && /usr/local/bin/micromamba clean --all --yes \
    && ln -s /opt/pdal/bin/pdal /usr/local/bin/pdal \
    && ln -s /opt/pdal/bin/untwine /usr/local/bin/untwine \
    && pdal --version \
    && pdal --drivers | grep -q writers.copc \
    && command -v untwine

# PotreeConverter 2.1.2: a última versão com binário para Linux publicada pelo projeto.
ARG POTREE_VERSAO=2.1.2
RUN curl -Ls -o /tmp/potree.zip \
      https://github.com/potree/PotreeConverter/releases/download/${POTREE_VERSAO}/PotreeConverter_${POTREE_VERSAO}_x64_linux.zip \
    && unzip -q /tmp/potree.zip -d /opt/potree && rm /tmp/potree.zip \
    && bin=$(find /opt/potree -type f -name PotreeConverter | head -1) \
    && chmod +x "$bin" \
    && printf '#!/bin/sh\ncd "%s" && exec ./PotreeConverter "$@"\n' "$(dirname "$bin")" > /usr/local/bin/PotreeConverter \
    && chmod +x /usr/local/bin/PotreeConverter

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && command -v py3dtiles

# O código também vai na imagem, mas a VM monta por cima a versão do pacote (python -m lidarspot pacote).
COPY lidarspot /opt/pacote/lidarspot
ENV PYTHONPATH=/opt/pacote
WORKDIR /opt/pacote
