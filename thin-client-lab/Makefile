# Runs inside the controller container (started by ./lab.sh or .\lab.ps1).
# Use those scripts from your computer; they call these targets.
SHELL := /bin/bash
PYTHON ?= python3
GUAC_VERSION := 1.5.5
HOME_ENDPOINTS := ep-home-01
export ANSIBLE_CONFIG := $(CURDIR)/ansible/ansible.cfg

-include .env
export ADMIN_PASSWORD AGENT_PASSWORD SVC_GUAC_PASSWORD GUAC_DB_PASSWORD CHROME_INSTALL

.PHONY: prereqs ssh-key provision test test-fast golden-provision

.lab/ssh/id_ed25519:
	mkdir -p .lab/ssh && ssh-keygen -q -t ed25519 -N '' -C cclab -f $@

.lab/vpn/gateway.pub:
	mkdir -p .lab/vpn && cd .lab/vpn && umask 077 && for n in gateway $(HOME_ENDPOINTS); do \
	  [ -f $$n.key ] || wg genkey > $$n.key; wg pubkey < $$n.key > $$n.pub; done

.lab/guac-initdb/001-schema.sql:
	mkdir -p .lab/guac-initdb
	docker run --rm guacamole/guacamole:$(GUAC_VERSION) /opt/guacamole/bin/initdb.sh --postgresql > $@.tmp
	mv $@.tmp $@
	cp config/guacamole/002-seed.sql .lab/guac-initdb/

.lab/broker-token:
	mkdir -p .lab && umask 077 && python3 -c "import secrets; print(secrets.token_urlsafe(32))" > $@

prereqs: .lab/ssh/id_ed25519 .lab/vpn/gateway.pub .lab/guac-initdb/001-schema.sql .lab/broker-token

# ssh refuses keys with the loose permissions bind mounts get on Windows/macOS.
ssh-key: .lab/ssh/id_ed25519
	@if [ -n "$$LAB_SSH_KEY" ]; then mkdir -p $$(dirname $$LAB_SSH_KEY) && install -m 600 $< $$LAB_SSH_KEY; fi

# Configure session hosts and endpoints with the same playbook as the real lab.
provision: ssh-key
	cd ansible && ansible-playbook site.yml $(ANSIBLE_ARGS)

test: ssh-key
	$(PYTHON) -m pytest tests $(PYTEST_ARGS)

# Skips the slow tests (failover, fail2ban ban, playbook idempotency).
test-fast: ssh-key
	$(PYTHON) -m pytest tests -m "not slow" $(PYTEST_ARGS)

# Second half of "golden image to a new device": provision the recreated endpoint, re-test.
golden-provision: ssh-key
	cd ansible && ansible-playbook site.yml --limit ep-office-01 $(ANSIBLE_ARGS)
	$(PYTHON) -m pytest tests -m endpoint $(PYTEST_ARGS)
