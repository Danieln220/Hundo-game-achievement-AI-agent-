# Hundo API — FastAPI backend image (Render deployment, Step 15).
# The agent/data layer are unchanged; this just packages api.main:app.
#
# Local Streamlit dev UI is unaffected (run it outside Docker with
#   streamlit run app.py
# ). If you ever want a Streamlit container instead, swap the CMD to:
#   CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
FROM python:3.11-slim

WORKDIR /app

# Install deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form so ${PORT} expands at runtime.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
