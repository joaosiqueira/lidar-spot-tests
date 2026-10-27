# Imagem dos conversores: PDAL + untwine (COPC), py3dtiles (3D Tiles), PotreeConverter (Potree) e o executor.
# As ferramentas são as mesmas do backend-python (Dockerfile de mapbiomas/amazonia3d/backend-python), mais o
# PotreeConverter, que o backend chama mas a imagem dele não tem.
# trixie (glibc 2.41): o binário do PotreeConverter 2.1.2 exige glibc >= 2.38 e libstdc++ do GCC 13, que o
# bookworm (glibc 2.36) não tem.
FROM python:3.12-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    bzip2 ca-certificates curl unzip libgomp1 libtbb12 procps util-linux \
    && rm -rf /var/lib/apt/lists/*

# PDAL não existe nos repositórios do Debian, então vem do conda-forge, isolado em /opt/pdal e exposto
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
    && dir=$(dirname "$bin") \
    && ! LD_LIBRARY_PATH="$dir" ldd "$bin" 2>&1 | grep "not found" \
    && printf '#!/bin/sh\ncd "%s" && LD_LIBRARY_PATH="%s" exec ./PotreeConverter "$@"\n' "$dir" "$dir" \
       > /usr/local/bin/PotreeConverter \
    && chmod +x /usr/local/bin/PotreeConverter

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && command -v py3dtiles

# O executor chama o py3dtiles por este wrapper, que lê o LAZ sem threads (o LazrsParallel trava os workers
# depois do fork; veja lidarspot/py3dtiles_serial.py).
RUN printf '#!/bin/sh\nexec python -m lidarspot.py3dtiles_serial "$@"\n' > /usr/local/bin/py3dtiles-serial \
    && chmod +x /usr/local/bin/py3dtiles-serial

# O código também vai na imagem, mas a VM monta por cima a versão do pacote (python -m lidarspot pacote).
COPY lidarspot /opt/pacote/lidarspot
ENV PYTHONPATH=/opt/pacote
WORKDIR /opt/pacote
