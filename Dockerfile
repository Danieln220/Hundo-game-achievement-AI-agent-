FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
# Streamlit (current deliverable). For the FastAPI backend later, swap to:
#   CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
