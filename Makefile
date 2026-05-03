.PHONY: setup up down es

setup:
	ln -sf $(shell pwd)/.env infra/docker/.env

up:
	docker compose -f infra/docker/docker-compose.yml up -d

down:
	docker compose -f infra/docker/docker-compose.yml down

es:
	docker compose -f infra/docker/docker-compose.yml up -d elasticsearch

dev:
	docker compose -f infra/docker/docker-compose.yml up -d elasticsearch postgres

airflow:
	docker compose -f infra/docker/docker-compose.yml up -d elasticsearch postgres airflow-init airflow-scheduler airflow-apiserver