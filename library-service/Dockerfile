FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=5004
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5004
# Render asigna el puerto en la variable PORT; gunicorn es el servidor de produccion.
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120 --access-logfile - app:app
