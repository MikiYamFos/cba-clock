# CBA Clock

CBA Clock turns collective bargaining agreements into structured timelines that unions can actually use.

CBA Clock uses OCR and Claude to extract those timing rules from contract PDFs into reviewable JSON. A user can verify the extracted rules, correct them if needed, and then generate timelines, reminders, and calendar exports tied to specific contracts or grievances.

## Problem

Contract rights often depend on deadlines. Grievances, appeals, arbitration demands, reopeners, renewals, and bargaining notices all have timing rules, and those rules can vary across contracts or contract versions. If the wrong rule is applied, or a deadline is missed, the union can lose leverage or lose the ability to act.

## Approach

CBA Clock converts contract language into structured, time-aware rules:

- Extract time-bound clauses (grievances, arbitration, expiration, reopeners)
- Normalize them into a consistent JSON schema
- Allow human review and correction
- Compute deadlines where possible
- Generate timelines and calendar outputs

AI (Claude) is used for **parsing contract language**, not making decisions.

## Core Insight

Contracts do not just contain dates — they contain **rules for generating dates**.

Example:

> “within 10 working days after occurrence”

This must be interpreted as:

- trigger: occurrence
- offset: 10
- unit: working_days

## MVP Scope

End-to-end pipeline for a single agreement:

1. Load PDF or text
2. Extract relevant clauses
3. Convert to structured JSON
4. Validate + review output
5. Compute deadlines where possible
6. Export timeline / calendar data

## Non-Goals (for MVP)

- Full legal compliance system
- Cross-contract scoring or recommendations
- Multi-user permissions
- RAG / agents

## Project Structure
```text
cba-clock/
  backend/              # FastAPI API
    app/
    tests/

  worker/               # PDF parsing, OCR, Claude extraction, validation
    app/
    tests/

  frontend/             # Review UI and timeline views
    src/

  infra/
    docker/             # Local Docker Compose setup
    terraform/
      hetzner/          # First deploy target
      aws/              # Later optional deployment target
      modules/

  docs/
    schema.md           # Contract + clause extraction schema
    evaluation-plan.md  # Test/evaluation strategy
    project-synopsis.md

  data/
    samples/            # Safe sample documents
    evaluation/         # Labeled clause test cases
    uploads/            # Ignored local uploads
    processed/          # Ignored generated output

## Infrastructure Strategy

CBA Clock is designed to be self-hostable and cloud-portable.

Local development uses Docker Compose. The first deployment target is Hetzner, where Terraform provisions the server, firewall, SSH access, and optional volume/floating IP, while Docker Compose runs the application services. AWS support is planned as a later deployment option using equivalent managed services.

Terraform provisions infrastructure. Docker Compose runs the app.

### Local Development

- Docker Compose
- Backend
- Worker
- Frontend
- Postgres
- MinIO
- Optional Redis/Postgres-backed job queue

### Hetzner Prototype Deployment

Terraform provisions:

- Hetzner VPS
- Firewall rules
- SSH key
- Optional volume
- Optional floating IP

Docker runs:

- Frontend
- Backend
- Worker
- Postgres
- MinIO or Hetzner Object Storage integration

### Later AWS Deployment

Optional AWS module may include:

- S3 for uploaded contracts and processed outputs
- RDS Postgres
- ECS/Fargate or App Runner
- Secrets Manager
- CloudWatch logs
- IAM roles and policies
- SQS for worker queue