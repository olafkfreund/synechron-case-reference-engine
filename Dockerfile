FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-core libreoffice-writer libreoffice-impress libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
# Aptos, the brand font, from Microsoft's download: installed, never committed (its EULA grants no distribution) (#282)
RUN python -c "import hashlib, io, sys, urllib.request, zipfile; \
url = 'https://download.microsoft.com/download/8/6/0/860a94fa-7feb-44ef-ac79-c072d9113d69/Microsoft%20Aptos%20Fonts.zip'; \
data = urllib.request.urlopen(url, timeout=120).read(); \
sha = '6528fd120e719a9f985e94214eca6887d1653b88456916a792a630b02e95b025'; \
hashlib.sha256(data).hexdigest() == sha or sys.exit('Aptos download does not match its pinned SHA-256'); \
names = ['Aptos', 'Aptos-Light', 'Aptos-SemiBold', 'Aptos-Bold', 'Aptos-Italic', 'Aptos-Bold-Italic', 'Aptos-Mono']; \
zipfile.ZipFile(io.BytesIO(data)).extractall('/usr/share/fonts/truetype/aptos', [n + '.ttf' for n in names])" \
 && fc-cache -f
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 LITELLM_LOG=WARNING DOCLING_ARTIFACTS_PATH=/opt/docling-models HF_HUB_OFFLINE=1
# pinned lock with hashes (see README: Dependency lock); CPU-only torch from its own index: Fargate has no GPU
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock --extra-index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install --no-deps -e . && pip check
# Docling models at build time: web has no internet egress at runtime
RUN HF_HUB_OFFLINE=0 python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'))"
# RDS CA bundle: the app connects with sslmode=verify-full in AWS
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem', '/opt/rds-ca.pem')"
RUN useradd --create-home app
COPY --chown=app:app . .
USER app
