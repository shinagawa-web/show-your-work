FROM python:3.12.15-slim
WORKDIR /app
RUN pip install "psycopg[binary]==3.3.6" psutil==7.2.2
COPY run.py .
ENTRYPOINT ["python3", "run.py"]
