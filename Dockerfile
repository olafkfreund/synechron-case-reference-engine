FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-core libreoffice-writer libreoffice-impress libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 LITELLM_LOG=WARNING DOCLING_ARTIFACTS_PATH=/opt/docling-models HF_HUB_OFFLINE=1
# pinned lock with hashes (see README: Dependency lock); CPU-only torch from its own index: Fargate has no GPU
COPY pyproject.toml requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock --extra-index-url https://download.pytorch.org/whl/cpu
RUN mkdir app && touch app/__init__.py && pip install --no-deps -e . && pip check
# Docling models at build time: web has no internet egress at runtime
RUN HF_HUB_OFFLINE=0 python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'))"
# RDS CA bundle: the app connects with sslmode=verify-full in AWS
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem', '/opt/rds-ca.pem')"
RUN useradd --create-home app
COPY --chown=app:app . .
USER app
