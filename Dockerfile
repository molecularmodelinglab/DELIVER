

FROM python:3.13-slim

ARG DELI_REF=patch
ARG FASTP_VERSION=0.23.4
ARG FASTQC_VERSION=0.12.1

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
        unzip \
        default-jre-headless \
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

# ── FastQC ────────────────────────────────────────────────────
RUN wget -q "https://www.bioinformatics.babraham.ac.uk/projects/fastqc/fastqc_v${FASTQC_VERSION}.zip" \
        -O /tmp/fastqc.zip && \
    unzip -q /tmp/fastqc.zip -d /opt && \
    chmod +x /opt/FastQC/fastqc && \
    ln -s /opt/FastQC/fastqc /usr/local/bin/fastqc && \
    rm /tmp/fastqc.zip

# ── orad (Illumina ORA / DRAGEN decompression) ────────────────
# Only needed when input FASTQs are .ora. Illumina distributes the "ORA
# Decompression Software" tarball (login-gated), so supply its URL at build:
#   docker build --build-arg ORAD_URL=https://.../orad.2.x.x.linux.tar.gz ...
# The tarball layout is orad_2_x_x/{orad,oradata}; adjust --strip-components if
# your build differs. When ORAD_URL is empty the image builds without ORA support.
ARG ORAD_URL=""
RUN if [ -n "$ORAD_URL" ]; then \
        wget -q "$ORAD_URL" -O /tmp/orad.tar.gz && \
        mkdir -p /opt/orad && \
        tar -xzf /tmp/orad.tar.gz -C /opt/orad --strip-components=1 && \
        ln -sf /opt/orad/orad /usr/local/bin/orad && \
        rm /tmp/orad.tar.gz && \
        orad --version ; \
    else \
        echo "ORAD_URL not set — .ora decompression unavailable in this image"; \
    fi

# orad finds its reference (refbin) by searching ./, $HOME/oradata/, ./oradata/,
# then $ORA_REF_PATH/. The tarball ships the default human reference at
# /opt/orad/oradata/refbin, so point ORA_REF_PATH at that dir for reference-based
# .ora decompression. The pipeline only overrides this when params.ora_reference
# is set (else it leaves this image default in place).
ENV ORA_REF_PATH=/opt/orad/oradata

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

# ── DELIVER postprocess package ───────────────────────────────
ENV DELIVER_SRC_DIR=/opt/deliver/src

COPY pyproject.toml /opt/deliver/pyproject.toml
COPY src/ /opt/deliver/src/

RUN uv pip install --system --no-cache /opt/deliver

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
RUN fastqc --version
RUN deli --version
# Verify orad can locate its reference (only when orad was installed).
# Non-fatal: prints the resolved refbin path (or a warning) without failing the
# build, since the exact flag name can vary across orad versions.
RUN if command -v orad >/dev/null 2>&1; then \
        orad --check-ora-reference-path || echo "WARN: orad reference check failed — verify ORA_REF_PATH"; \
    fi

# ── Runtime settings ──────────────────────────────────────────
USER root
WORKDIR /work


# docker build --no-cache  --platform linux/amd64 -t deliver-test .

# docker run --rm --platform linux/amd64 deliver-test ls -la /opt/deli_data/buildingblocks/libraries/
