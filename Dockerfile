

FROM python:3.13-slim

ARG DELI_REF=patch
ARG FASTP_VERSION=0.23.4

LABEL org.opencontainers.image.title="DELIVER"
LABEL org.opencontainers.image.description="DEL pipeline — GCP Cloud Batch image"

# ── System packages ───────────────────────────────────────────
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        git \
        wget \
        ca-certificates \
        bash \
        gzip \
        curl \
        gnupg \
        procps \                   
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Google Cloud CLI (gsutil)
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | \
        tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | \
        gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update -qq && \
    apt-get install -y --no-install-recommends google-cloud-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/local/bin/python3 /usr/bin/python3

# ── fastp ─────────────────────────────────────────────────────
RUN wget -q "http://opengene.org/fastp/fastp.${FASTP_VERSION}" \
        -O /usr/local/bin/fastp && \
    chmod +x /usr/local/bin/fastp

# ── uv + Python dependencies ──────────────────────────────────
RUN pip install --no-cache-dir uv

# Install DELi from GitHub
RUN git clone --branch "${DELI_REF}" --depth 1 \
        https://github.com/Popov-Lab-UNC/DELi.git /opt/deli && \
    uv pip install --system --no-cache /opt/deli && \
    rm -rf /opt/deli/.git

RUN uv pip install --system --no-cache pyarrow polars pyyaml click


COPY data/deli_data/building_blocks/ /opt/deli_data/buildingblocks/building_blocks/
COPY data/deli_data/libraries/ /opt/deli_data/buildingblocks/libraries/

RUN echo "=== checking copied deli data ===" && \
    find /opt/deli_data/buildingblocks/ -type f && \
    echo "=== done ==="


RUN rm -rf /root/.deli && \
    deli config init --overwrite && \
    sed -i 's|deli_data_dir = $|deli_data_dir = /opt/deli_data/buildingblocks|' /root/.deli && \
    echo "=== /root/.deli ===" && \
    cat /root/.deli

# ── DELIVER postprocess scripts ───────────────────────────────
ENV DELIVER_SRC_DIR=/opt/deliver/src
RUN mkdir -p ${DELIVER_SRC_DIR}/deliver/postprocess

COPY src/deliver/__init__.py ${DELIVER_SRC_DIR}/deliver/__init__.py
COPY src/deliver/postprocess/deduplicate.py ${DELIVER_SRC_DIR}/deliver/postprocess/deduplicate.py
COPY src/deliver/postprocess/enrichment.py ${DELIVER_SRC_DIR}/deliver/postprocess/enrichment.py

# ── Entrypoint ────────────────────────────────────────────────
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["bash"]

# ── Build-time smoke tests ────────────────────────────────────
RUN python - <<'EOF'
import yaml, polars, click, pyarrow
print("pyyaml  :", yaml.__version__)
print("polars  :", polars.__version__)
print("click   :", click.__version__)
print("pyarrow :", pyarrow.__version__)
EOF

RUN fastp --version 2>&1 | head -1
RUN deli --version

# ── Runtime settings ──────────────────────────────────────────
USER root
WORKDIR /work


# docker build --no-cache  --platform linux/amd64 -t deliver-test .

# docker run --rm --platform linux/amd64 deliver-test ls -la /opt/deli_data/buildingblocks/libraries/
