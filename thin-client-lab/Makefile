# Simulated lab: make up && make provision && make test
SHELL := /bin/bash
COMPOSE := docker compose
PYTHON ?= python3
GUAC_VERSION := 1.5.5
HOME_ENDPOINTS := ep-home-01
export ANSIBLE_CONFIG := $(CURDIR)/ansible/ansible.cfg

-include .env
export ADMIN_PASSWORD AGENT_PASSWORD SVC_GUAC_PASSWORD GUAC_DB_PASSWORD

.PHONY: all prereqs up provision test test-fast golden-image down clean

all: up provision test

.env:
	cp .env.example .env

.lab/ssh/id_ed25519:
	mkdir -p .lab/ssh && ssh-keygen -q -t ed25519 -N '' -C cclab -f $@

.lab/vpn/gateway.pub:
	mkdir -p .lab/vpn
	$(COMPOSE) build vpn-gw
	docker run --rm -v $(CURDIR)/.lab/vpn:/keys --entrypoint /gen-keys.sh cclab/vpn-gw $(HOME_ENDPOINTS)

.lab/guac-initdb/001-schema.sql:
	mkdir -p .lab/guac-initdb
	docker run --rm guacamole/guacamole:$(GUAC_VERSION) /opt/guacamole/bin/initdb.sh --postgresql > $@.tmp
	mv $@.tmp $@
	cp config/guacamole/002-seed.sql .lab/guac-initdb/

prereqs: .env .lab/ssh/id_ed25519 .lab/vpn/gateway.pub .lab/guac-initdb/001-schema.sql
	$(PYTHON) -m pip install -q -r tests/requirements.txt

up: prereqs
	docker image inspect cclab/managed-node >/dev/null 2>&1 || docker build -t cclab/managed-node images/managed-node
	$(COMPOSE) up -d --build --wait --wait-timeout 300

# Configure session hosts and endpoints with the same playbook as the real lab.
provision:
	cd ansible && ansible-playbook site.yml $(ANSIBLE_ARGS)

test:
	$(PYTHON) -m pytest tests $(PYTEST_ARGS)

# Skips the slow tests (failover, fail2ban ban, playbook idempotency).
test-fast:
	$(PYTHON) -m pytest tests -m "not slow" $(PYTEST_ARGS)

# "Golden image deploys to a new device without manual steps":
# throw away an endpoint, recreate it from the bare image, provision, re-test.
golden-image:
	$(COMPOSE) rm -sf ep-office-01
	$(COMPOSE) up -d --wait ep-office-01
	cd ansible && ansible-playbook site.yml --limit ep-office-01 $(ANSIBLE_ARGS)
	$(PYTHON) -m pytest tests -m endpoint $(PYTEST_ARGS)

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v --remove-orphans
	rm -rf .lab
