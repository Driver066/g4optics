# Public Geant4 11.4.2 multi-architecture index (linux/amd64 and linux/arm64).
FROM carlomt/geant4@sha256:7e660c618ad80506c66e9f509a285e7d9e76dadd75b66f28cbe378ccf91ae3f5
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-reproduce.txt /tmp/dimple-requirements.txt
RUN python3 -m venv /opt/dimple-venv \
    && /opt/dimple-venv/bin/python -m pip install --no-cache-dir -r /tmp/dimple-requirements.txt \
    && /opt/dimple-venv/bin/python -m pip freeze > /opt/dimple-python-freeze.txt \
    && test "$(/opt/geant4/bin/geant4-config --version)" = 11.4.2
COPY docker/entrypoint.sh /opt/dimple-entrypoint.sh
RUN chmod 755 /opt/dimple-entrypoint.sh
ENV PATH="/opt/dimple-venv/bin:/opt/geant4/bin:${PATH}" \
    GEANT4_DATA_DIR=/g4data G4RUN_MANAGER_TYPE=Serial \
    PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/dimple-mpl-cache
WORKDIR /work
ENTRYPOINT ["/opt/dimple-entrypoint.sh"]
CMD ["bash"]
