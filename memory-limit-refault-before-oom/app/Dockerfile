FROM python:3.12.16-slim-trixie
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY app.py /app/app.py
ENV OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
