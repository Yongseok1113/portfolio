# adp-ma controller/worker 공용 이미지
# 빌드: minikube -p portfolio image build -t adp-ma:0.1.0 adp-ma/
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# worker: adp-ma worker --prefix <run-id>/<seq>
ENTRYPOINT ["adp-ma"]
