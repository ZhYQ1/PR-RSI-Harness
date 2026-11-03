FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir ".[postgres,redis]"

ENV HARNESS_DB=data/harness.db
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "pr-harness --db \"${HARNESS_DB}\" serve --port \"${PORT}\""]
