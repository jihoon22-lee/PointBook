FROM mcr.microsoft.com/playwright/python:v1.30.0-jammy
WORKDIR /app/e2e
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt
COPY tests tests
ENV BASE_URL=http://app:8000
CMD ["python3", "-m", "pytest", "-v"]
