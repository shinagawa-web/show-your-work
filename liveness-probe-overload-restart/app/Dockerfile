ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG GUNICORN_VERSION
ARG GUNICORN_SHA256
RUN pip install --no-cache-dir "gunicorn @ https://github.com/benoitc/gunicorn/archive/refs/tags/${GUNICORN_VERSION}.tar.gz#sha256=${GUNICORN_SHA256}"
WORKDIR /app
COPY app.py /app/app.py
