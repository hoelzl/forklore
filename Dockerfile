# forklore: all-in-one image (web build + python server)
#
#   docker build -t forklore .
#   docker run -p 8000:8000 forklore
#   # open http://localhost:8000

FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY core /srv/core
COPY server /srv/server
COPY strategies /srv/strategies
COPY --from=web /web/dist /srv/dist

ENV FORKLORE_DIST=/srv/dist
EXPOSE 8000
# mount a volume at /srv/data to persist the event log across restarts:
#   docker run -v forklore-data:/srv/data -p 8000:8000 forklore
CMD ["sh", "-c", "uvicorn server.__main__:app --host 0.0.0.0 --port ${PORT:-8000}"]
