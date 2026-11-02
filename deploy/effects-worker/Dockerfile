# effects-worker: drives a prebuilt effectcraft-cli binary. That binary is
# NOT built here (see build.sh in this directory and
# deploy/architecture.md) - effectcraft is a separate, private repo and the
# design explicitly keeps zero changes to it, including no Dockerfile living
# inside it. build.sh compiles it locally (or on whichever host runs
# `docker compose build`) and copies the release binary into
# deploy/effects-worker/bin/effectcraft-cli, which this Dockerfile just
# COPYs in - run build.sh before `docker compose build effects-worker`.
FROM python:3.11-slim-bookworm

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Only the files this service actually needs - not the whole jiuwenswarm
# tree, keeping this image independent of the much larger main app image.
COPY jiuwenswarm/__init__.py jiuwenswarm/__init__.py
COPY jiuwenswarm/services/ jiuwenswarm/services/
COPY deploy/effects-worker/bin/effectcraft-cli /usr/local/bin/effectcraft-cli
RUN chmod +x /usr/local/bin/effectcraft-cli

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1.0" \
    "uvicorn[standard]>=0.30,<1.0" \
    "python-multipart>=0.0.9"

ENV PYTHONPATH=/app
ENV EFFECTCRAFT_CLI_PATH=/usr/local/bin/effectcraft-cli

RUN chown -R app:app /app
USER app

EXPOSE 8088
CMD ["uvicorn", "jiuwenswarm.services.effects_worker.main:app", "--host", "0.0.0.0", "--port", "8088"]
