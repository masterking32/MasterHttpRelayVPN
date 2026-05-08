FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (layer-cached until requirements.txt changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source — config.json and ca/ are intentionally excluded (.dockerignore)
# and mounted at runtime so secrets are never baked into the image.
COPY . .

EXPOSE 8085 1080

# --host 0.0.0.0 is required inside a container so the proxy is reachable
# from outside. The value in config.json is ignored for the host binding.
CMD ["python", "main.py", "--host", "0.0.0.0"]
