FROM mcr.microsoft.com/playwright:v1.30.0-focal
WORKDIR /app/e2e
COPY requirements.txt .
RUN python3 -m ensurepip --upgrade || (apt-get update && apt-get install -y python3-pip)
RUN python3 -m pip install --no-cache-dir -r requirements.txt
COPY tests tests
ENV BASE_URL=http://app:8000
CMD ["python3", "-m", "pytest", "-v"]
