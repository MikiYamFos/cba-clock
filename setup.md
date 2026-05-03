# CBA Clock — Local Development Setup

## Prerequisites

Install Homebrew if you don't have it:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## 1. Install system dependencies

```bash
brew install openssl ca-certificates pyenv uv docker
```

## 2. Install Python via pyenv with OpenSSL properly linked

Pyenv compiles Python from source. Without these flags, it won't find Homebrew's
OpenSSL and will produce a Python that fails SSL verification for most HTTPS requests.

```bash
brew update && brew upgrade pyenv

LDFLAGS="-L$(brew --prefix openssl)/lib" \
CPPFLAGS="-I$(brew --prefix openssl)/include" \
CONFIGURE_OPTS="--with-openssl=$(brew --prefix openssl)" \
pyenv install 3.12.10
```

Verify SSL is working — you should see cert paths, not empty strings:
```bash
~/.pyenv/versions/3.12.10/bin/python3 -c "import ssl; print(ssl.get_default_verify_paths())"
```

Expected output includes `/opt/homebrew/etc/ca-certificates/cert.pem`. If you see
empty strings, rerun the install with `--force` added at the end.

Set the Python version for this project:
```bash
echo "3.12.10" > .python-version
```

## 3. Install project dependencies

```bash
uv sync
```

## 4. SSL note for DOL downloads

The DOL OLMS site (`olmsapps.dol.gov`) is signed by a US government certificate
authority that is not included in Python's certifi bundle. This is not a bug in
your setup — certifi simply does not ship government CA certificates.

`DOLDownloader` handles this by setting `verify=False` on all requests to this
domain. This is safe for local research use against a known government endpoint.
The decision is documented in the class with `VERIFY_SSL = False` so it is visible
to anyone reading the code. SSL verification works normally for all other domains
in this project.

## 5. Set up environment variables

Create your `.env` file at the project root:
```bash
# Postgres
POSTGRES_DB=cba_clock
POSTGRES_USER=cba_clock
POSTGRES_PASSWORD=your_password_here
POSTGRES_PORT=5432

# Airflow
AIRFLOW_DB=airflow
AIRFLOW_WWW_USER=admin
AIRFLOW_WWW_PASSWORD=your_password_here
AIRFLOW__CORE__EXECUTOR=LocalExecutor
AIRFLOW__CORE__LOAD_EXAMPLES=False

# MinIO
MINIO_ROOT_USER=cba_clock
MINIO_ROOT_PASSWORD=your_password_here
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001

# Backend
BACKEND_PORT=8000

# Elasticsearch
ELASTICSEARCH_URL=http://elasticsearch:9200

# API Keys
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
```

## 6. Start Docker services

First time only — create the Airflow database and initialize Airflow:
```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d postgres

docker compose -f infra/docker/docker-compose.yml --env-file .env exec postgres \
  psql -U cba_clock -c "CREATE DATABASE airflow;"

docker compose -f infra/docker/docker-compose.yml --env-file .env up airflow-init
```

Start all services:
```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
```

Services available at:
- Airflow UI: http://localhost:8080
- MinIO console: http://localhost:9001
- Backend API: http://localhost:8000
- Elasticsearch: http://localhost:9200

Stop all services:
```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env down
```

Data is preserved in Docker volumes and the local `data/` directory between restarts.

## 7. Run the pipeline

Trigger from the Airflow UI at http://localhost:8080, or via CLI:
```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env exec \
  airflow-scheduler airflow dags trigger cba_pipeline
```

Trigger with overwrite to reprocess everything:
```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env exec \
  airflow-scheduler airflow dags trigger cba_pipeline --conf '{"overwrite": true}'
```

## 8. Download CBA sources

### OPM (federal CBAs only)
```bash
uv run python -m worker.app.sample_sources.runner opm --limit 10
```

### DOL OLMS (4,846 CBAs available, private and public sector)

Always use filters — downloading all 4,846 at once is not recommended:
```bash
# Filter by union name (substring match, case insensitive)
uv run python -m worker.app.sample_sources.runner dol --union "AFGE"

# Filter by sector (PRIVATE or PUBLIC)
uv run python -m worker.app.sample_sources.runner dol --sector PUBLIC

# Filter by state abbreviation
uv run python -m worker.app.sample_sources.runner dol --state "NY"

# Filter by NAICS industry code prefix (e.g. 517 = telecom)
uv run python -m worker.app.sample_sources.runner dol --naics "517"

# Filter by contract expiration year
uv run python -m worker.app.sample_sources.runner dol --exp-year-min 2018

# Combine multiple filters
uv run python -m worker.app.sample_sources.runner dol \
  --union "AFGE" --sector PUBLIC --exp-year-min 2018

# Limit number of downloads
uv run python -m worker.app.sample_sources.runner dol --limit 10

# Re-fetch the source list from the DOL API (4,846 records, updates dol_cba_sources.csv)
uv run python -m worker.app.sample_sources.runner dol --refresh-source
```