FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-core libreoffice-writer libreoffice-impress libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 DOCLING_ARTIFACTS_PATH=/opt/docling-models HF_HUB_OFFLINE=1
# CPU-only torch, own layer and index: Fargate has no GPU
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml .
RUN mkdir app && touch app/__init__.py && pip install -e ".[dev]"
# Docling models at build time: web has no internet egress at runtime
RUN HF_HUB_OFFLINE=0 python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'))"
RUN useradd --create-home app
COPY --chown=app:app . .
USER app
