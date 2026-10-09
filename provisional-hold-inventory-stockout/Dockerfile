FROM python:3.12.15-slim
WORKDIR /app
RUN pip install "psycopg[binary]==3.3.6"
COPY run.py .
ENTRYPOINT ["python3", "run.py"]
