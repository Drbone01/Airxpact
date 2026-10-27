FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "-m", "agent"]
