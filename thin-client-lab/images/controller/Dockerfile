# Runs Ansible and the test suite from inside the lab networks, so the host only
# needs Docker (works the same on Linux, macOS and Windows/Docker Desktop).
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 python3-venv openssh-client make docker.io wireguard-tools curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY tests/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -q -r /tmp/requirements.txt
ENV PATH=/opt/venv/bin:$PATH \
    ANSIBLE_CONFIG=/lab/ansible/ansible.cfg \
    LAB_SSH_KEY=/root/.ssh/lab_key
WORKDIR /lab
CMD ["sleep", "infinity"]
