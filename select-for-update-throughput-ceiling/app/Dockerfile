FROM python:3.12.15-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENTRYPOINT ["python3", "server.py"]
